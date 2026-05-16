"""
ChatGPT Plus full-RE flow.

Uses:
  - cookies + bearer for auth
  - Python: proof_token + PoW solver
  - Node subprocess: turnstile.dx → turnstile_token
  - 4-step server dance: /api/auth/session → /prepare → /finalize → /f/conversation

Image generation: send `image=True`. DALL-E tool runs async, so we poll
GET /backend-api/conversation/{id} until a multimodal_text part with an
asset_pointer appears, then download via /backend-api/files/{id}/download.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import struct
import time
from pathlib import Path
from uuid import uuid4

from curl_cffi import requests as cffi
from dotenv import load_dotenv

from proof import build_proof_token
from pow import solve_pow
from solver_client import NodeSolver
from sse_parser import parse_sse, decode_stream, iter_sse_events

_BASE = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
load_dotenv(_BASE / ".env")
logger = logging.getLogger(__name__)

IMPERSONATE = os.getenv("GPT_IMPERSONATE", "chrome131")


def _image_dims(path: str) -> tuple[int | None, int | None, str]:
    """Best-effort (width, height, mime) from PNG/JPEG/GIF/WEBP headers. No PIL needed."""
    try:
        with open(path, "rb") as f:
            head = f.read(32)
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            w, h = struct.unpack(">II", head[16:24])
            return w, h, "image/png"
        if head[:3] == b"\xff\xd8\xff":
            with open(path, "rb") as f:
                f.read(2)
                while True:
                    b = f.read(1)
                    while b and b != b"\xff":
                        b = f.read(1)
                    marker = f.read(1)
                    if not marker:
                        break
                    if marker[0] in (0xC0, 0xC1, 0xC2, 0xC3):
                        f.read(3)
                        h, w = struct.unpack(">HH", f.read(4))
                        return w, h, "image/jpeg"
                    size_b = f.read(2)
                    if len(size_b) < 2:
                        break
                    size = struct.unpack(">H", size_b)[0]
                    f.read(size - 2)
            return None, None, "image/jpeg"
        if head[:6] in (b"GIF87a", b"GIF89a"):
            w, h = struct.unpack("<HH", head[6:10])
            return w, h, "image/gif"
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return None, None, "image/webp"
    except Exception:
        pass
    return None, None, "application/octet-stream"


async def _upload_reference_image(
    client, bearer: str, cookies: str, ua: str,
    device_id: str, client_build: str, client_version: str,
    oai_is: str, session_id: str, path: str | Path,
) -> dict:
    """Upload a reference image via the 3-step ChatGPT file API.

    Returns dict with file_id, size, width, height, mime_type, asset_pointer.
    """
    path = str(path)
    if not os.path.isfile(path):
        raise PlusError(f"ref_image not found: {path}")
    file_name = os.path.basename(path)
    file_size = os.path.getsize(path)
    width, height, mime = _image_dims(path)
    logger.info("[upload] %s  size=%d  %sx%s  %s", file_name, file_size, width, height, mime)

    h = _base_headers(cookies, ua, device_id, client_build, client_version, oai_is, session_id)
    h["Authorization"] = f"Bearer {bearer}"
    h["x-openai-target-path"] = "/backend-api/files"
    h["x-openai-target-route"] = "/backend-api/files"
    body = {
        "file_name": file_name,
        "file_size": file_size,
        "use_case": "multimodal",
        "reset_rate_limits": False,
        "timezone_offset_min": -420,
    }
    r = await client.post(
        "https://chatgpt.com/backend-api/files",
        headers=h, data=json.dumps(body), timeout=30,
    )
    if r.status_code != 200:
        raise PlusError(f"upload create HTTP {r.status_code}: {r.text[:300]}")
    info = r.json()
    file_id = info.get("file_id")
    upload_url = info.get("upload_url")
    if not file_id or not upload_url:
        raise PlusError(f"upload create: missing fields in {info}")
    logger.info("[upload] step 1/3 OK  file_id=%s", file_id)

    with open(path, "rb") as f:
        blob_data = f.read()
    r2 = await client.put(
        upload_url, data=blob_data,
        headers={"Content-Type": mime, "x-ms-blob-type": "BlockBlob", "User-Agent": ua},
        timeout=120,
    )
    if r2.status_code not in (200, 201):
        raise PlusError(f"blob PUT HTTP {r2.status_code}: {r2.text[:200]}")
    logger.info("[upload] step 2/3 OK  blob PUT")

    h2 = _base_headers(cookies, ua, device_id, client_build, client_version, oai_is, session_id)
    h2["Authorization"] = f"Bearer {bearer}"
    h2["x-openai-target-path"] = f"/backend-api/files/{file_id}/uploaded"
    h2["x-openai-target-route"] = "/backend-api/files/[fileId]/uploaded"
    r3 = await client.post(
        f"https://chatgpt.com/backend-api/files/{file_id}/uploaded",
        headers=h2, data=json.dumps({}), timeout=30,
    )
    if r3.status_code != 200:
        raise PlusError(f"upload notify HTTP {r3.status_code}: {r3.text[:200]}")
    logger.info("[upload] step 3/3 OK  asset ready")

    return {
        "file_id":       file_id,
        "size":          file_size,
        "width":         width,
        "height":        height,
        "mime_type":     mime,
        "asset_pointer": f"file-service://{file_id}",
    }


class PlusError(Exception):
    pass


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip().strip('"').strip("'")


def _base_headers(cookies: str, ua: str, device_id: str, client_build: str,
                  client_version: str, oai_is: str, session_id: str) -> dict:
    return {
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        "Content-Type": "application/json",
        "Cookie": cookies, "User-Agent": ua,
        "Origin": "https://chatgpt.com", "Referer": "https://chatgpt.com/",
        "OAI-Device-Id": device_id, "OAI-Language": "en-US",
        "oai-client-build-number": client_build, "oai-client-version": client_version,
        "oai-session-id": session_id, "x-oai-is": oai_is,
        "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
        "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty", "sec-fetch-mode": "cors", "sec-fetch-site": "same-origin",
    }


async def _fetch_bootstrap(client, cookies: str, ua: str) -> dict:
    """Fetch chatgpt.com HTML and parse <script id='client-bootstrap'> JSON."""
    r = await client.get("https://chatgpt.com/",
                         headers={"Cookie": cookies, "User-Agent": ua, "Accept": "text/html"})
    if r.status_code != 200:
        raise PlusError(f"GET / HTTP {r.status_code}")
    m = re.search(r'<script[^>]*id="client-bootstrap"[^>]*>(.*?)</script>', r.text, re.DOTALL)
    if not m:
        raise PlusError("client-bootstrap script not found in HTML")
    return json.loads(m.group(1))


class GPTPlus:
    BASE = "https://chatgpt.com"
    SESSION = f"{BASE}/api/auth/session"
    PREPARE = f"{BASE}/backend-api/sentinel/chat-requirements/prepare"
    FINALIZE = f"{BASE}/backend-api/sentinel/chat-requirements/finalize"
    CHAT = f"{BASE}/backend-api/f/conversation"

    def __init__(self):
        self.solver = NodeSolver()
        self._bootstrap_cache: dict | None = None
        self._bootstrap_age: float = 0
        self._last_conv_id: str | None = None

    async def close(self):
        await self.solver.stop()

    async def _bootstrap(self, client, cookies: str, ua: str) -> dict:
        # Cache bootstrap for 5 min to avoid hitting / on every chat
        if self._bootstrap_cache and time.time() - self._bootstrap_age < 300:
            return self._bootstrap_cache
        self._bootstrap_cache = await _fetch_bootstrap(client, cookies, ua)
        self._bootstrap_age = time.time()
        return self._bootstrap_cache

    async def chat(self, message: str, image: bool = False, model: str | None = None,
                   ref_images: list[str | Path] | str | Path | None = None) -> dict:
        cookies = _env("GPT_COOKIES")
        ua = _env("GPT_USER_AGENT")
        device_id = _env("GPT_DEVICE_ID") or str(uuid4())
        client_build = _env("GPT_CLIENT_BUILD")
        client_version = _env("GPT_CLIENT_VERSION")
        oai_is = _env("GPT_OAI_IS")
        chosen_model = model or _env("GPT_MODEL", "gpt-5-5-thinking")
        if not cookies or not ua:
            raise PlusError("GPT_COOKIES / GPT_USER_AGENT missing in .env")

        async with cffi.AsyncSession(impersonate=IMPERSONATE, timeout=180) as client:
            # 1. accessToken
            r = await client.get(self.SESSION,
                                 headers={"Cookie": cookies, "User-Agent": ua, "Accept": "application/json"})
            if r.status_code != 200:
                raise PlusError(f"/api/auth/session HTTP {r.status_code}")
            access_token = r.json().get("accessToken")
            if not access_token:
                raise PlusError("no accessToken (cookies expired?)")
            logger.info("[plus] auth OK")

            session_id = str(uuid4())

            # 1b. upload reference images (if any) — reuses this session + bearer
            if ref_images is None:
                _refs: list = []
            elif isinstance(ref_images, (str, Path)):
                _refs = [ref_images]
            else:
                _refs = list(ref_images)
            upload_metas: list[dict] = []
            for _rp in _refs:
                _m = await _upload_reference_image(
                    client, access_token, cookies, ua,
                    device_id, client_build, client_version, oai_is, session_id, _rp,
                )
                upload_metas.append(_m)
                logger.info("[plus] ref image ready: %s", _m["asset_pointer"])

            # 2. bootstrap (cached)
            bootstrap = await self._bootstrap(client, cookies, ua)
            logger.info("[plus] bootstrap OK (%d keys)", len(bootstrap))

            # 3. /prepare
            proof_token = build_proof_token(ua, client_version)
            h = _base_headers(cookies, ua, device_id, client_build, client_version, oai_is, session_id)
            h["Authorization"] = f"Bearer {access_token}"
            h["x-openai-target-path"] = "/backend-api/sentinel/chat-requirements/prepare"
            h["x-openai-target-route"] = "/backend-api/sentinel/chat-requirements/prepare"
            r = await client.post(self.PREPARE, headers=h, data=json.dumps({"p": proof_token}))
            if r.status_code != 200:
                raise PlusError(f"/prepare HTTP {r.status_code}: {r.text[:300]}")
            prep = r.json()
            logger.info("[plus] /prepare OK persona=%s", prep.get("persona"))

            # 4. Solve challenges in parallel
            pow_seed = prep["proofofwork"]["seed"]
            pow_diff = prep["proofofwork"]["difficulty"]
            dx = prep["turnstile"]["dx"]

            pow_task = asyncio.to_thread(solve_pow, pow_seed, pow_diff, ua)
            ts_task = self.solver.solve_turnstile(proof_token, dx, bootstrap, ua)
            pow_answer, ts_answer = await asyncio.gather(pow_task, ts_task)
            logger.info("[plus] PoW solved (%d chars), turnstile solved (%d chars)",
                        len(pow_answer), len(ts_answer))

            # 5. /finalize
            h["x-openai-target-path"] = "/backend-api/sentinel/chat-requirements/finalize"
            h["x-openai-target-route"] = "/backend-api/sentinel/chat-requirements/finalize"
            r = await client.post(self.FINALIZE, headers=h, data=json.dumps({
                "prepare_token": prep["prepare_token"],
                "proofofwork": pow_answer,
                "turnstile": ts_answer,
            }))
            if r.status_code != 200:
                raise PlusError(f"/finalize HTTP {r.status_code}: {r.text[:500]}")
            fin = r.json()
            final_token = fin.get("token")
            if not final_token:
                raise PlusError(f"/finalize: no token. body: {r.text[:300]}")
            logger.info("[plus] /finalize OK")

            # 6. /f/conversation
            h2 = _base_headers(cookies, ua, device_id, client_build, client_version, oai_is, session_id)
            h2["Accept"] = "text/event-stream"
            h2["Authorization"] = f"Bearer {access_token}"
            h2["openai-sentinel-chat-requirements-token"] = final_token
            h2["openai-sentinel-proof-token"] = pow_answer
            h2["openai-sentinel-turnstile-token"] = ts_answer
            h2["x-oai-turn-trace-id"] = str(uuid4())
            h2["x-openai-target-path"] = "/backend-api/f/conversation"
            h2["x-openai-target-route"] = "/backend-api/f/conversation"

            system_hints = ["picture_v2"] if image else []
            if upload_metas:
                _parts: list = [
                    {
                        "asset_pointer": m["asset_pointer"],
                        "size_bytes":    m["size"],
                        "width":         m["width"],
                        "height":        m["height"],
                        "mimeType":      m["mime_type"],
                    }
                    for m in upload_metas
                ]
                _parts.append(message)
                _content = {"content_type": "multimodal_text", "parts": _parts}
            else:
                _content = {"content_type": "text", "parts": [message]}
            body = {
                "action": "next",
                "messages": [{
                    "id": str(uuid4()), "author": {"role": "user"},
                    "create_time": time.time(),
                    "content": _content,
                    "metadata": {
                        "developer_mode_connector_ids": [],
                        "selected_github_repos": [], "selected_all_github_repos": False,
                        "system_hints": system_hints,
                        "serialization_metadata": {"custom_symbol_offsets": []},
                    },
                }],
                "parent_message_id": "client-created-root",
                "model": chosen_model,
                "client_prepare_state": "success",
                "timezone_offset_min": -420, "timezone": "Asia/Bangkok",
                "conversation_mode": {"kind": "primary_assistant"},
                "enable_message_followups": True,
                "system_hints": system_hints,
                "supports_buffering": True, "supported_encodings": ["v1"],
                "client_contextual_info": {
                    "is_dark_mode": True, "time_since_loaded": 7,
                    "page_height": 945, "page_width": 1106, "pixel_ratio": 1,
                    "screen_height": 1080, "screen_width": 1920,
                    "app_name": "chatgpt.com",
                },
                "paragen_cot_summary_display_override": "allow",
                "force_parallel_switch": "auto", "thinking_effort": "standard",
            }
            r = await client.post(self.CHAT, headers=h2, data=json.dumps(body))

            logger.info("[plus] /conversation HTTP %d, %d bytes", r.status_code, len(r.text))
            if r.status_code != 200:
                raise PlusError(f"/conversation HTTP {r.status_code}: {r.text[:500]}")

            result = parse_sse(r.text)

            if image and not result.get("image_assets"):
                # DALL-E runs async — poll the mapping endpoint for the asset
                assets = await _poll_for_image(
                    client, cookies, ua, access_token,
                    device_id, client_build, client_version, oai_is,
                    conversation_id=result.get("conversation_id"),
                    max_attempts=60, interval=3,
                )
                result["image_assets"] = assets

        return result

    async def generate_image(self, prompt: str, save_to: str | Path | None = None,
                             model: str | None = None,
                             ref_images: list[str | Path] | str | Path | None = None) -> dict:
        """High-level wrapper: send picture_v2 prompt, poll for asset, download.

        Returns: {message, image_assets, local_path, image_url}
        Pass ref_images (single path or list) to do image-to-image.
        """
        result = await self.chat(prompt, image=True, model=model, ref_images=ref_images)
        assets = result.get("image_assets") or []
        if not assets:
            raise PlusError(f"image gen produced no asset within timeout. text={result.get('message','')[:200]}")

        asset_pointer = assets[0]
        asset_id = asset_pointer.rsplit("/", 1)[-1]

        cookies = _env("GPT_COOKIES")
        ua = _env("GPT_USER_AGENT")

        async with cffi.AsyncSession(impersonate=IMPERSONATE, timeout=120) as client:
            r = await client.get(self.SESSION,
                                 headers={"Cookie": cookies, "User-Agent": ua, "Accept": "application/json"})
            access_token = r.json()["accessToken"]
            dh = {"Authorization": f"Bearer {access_token}", "Cookie": cookies, "User-Agent": ua,
                  "Accept": "application/json"}
            dr = await client.get(f"{self.BASE}/backend-api/files/{asset_id}/download", headers=dh)
            if dr.status_code != 200:
                raise PlusError(f"file download meta HTTP {dr.status_code}: {dr.text[:200]}")
            cdn = dr.json().get("download_url") or dr.json().get("url")
            if not cdn:
                raise PlusError("no CDN URL in download meta")

            img = await client.get(cdn, headers={"User-Agent": ua, "Referer": "https://chatgpt.com/"})
            if img.status_code != 200 or len(img.content) < 1024:
                raise PlusError(f"image fetch HTTP {img.status_code}, {len(img.content)} bytes")

        out_path = Path(save_to) if save_to else Path("downloads") / f"gpt_{asset_id}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(img.content)
        result["local_path"] = str(out_path.resolve())
        result["image_url"] = cdn
        result["asset_id"] = asset_id
        return result


    async def chat_stream(self, message: str, model: str | None = None):
        """Async generator: yields text delta tokens from ChatGPT's live SSE stream."""
        cookies = _env("GPT_COOKIES")
        ua = _env("GPT_USER_AGENT")
        device_id = _env("GPT_DEVICE_ID") or str(uuid4())
        client_build = _env("GPT_CLIENT_BUILD")
        client_version = _env("GPT_CLIENT_VERSION")
        oai_is = _env("GPT_OAI_IS")
        chosen_model = model or _env("GPT_MODEL", "gpt-5-5-thinking")
        if not cookies or not ua:
            raise PlusError("GPT_COOKIES / GPT_USER_AGENT missing in .env")

        async with cffi.AsyncSession(impersonate=IMPERSONATE, timeout=180) as client:
            # 1. accessToken
            r = await client.get(self.SESSION,
                                 headers={"Cookie": cookies, "User-Agent": ua, "Accept": "application/json"})
            if r.status_code != 200:
                raise PlusError(f"/api/auth/session HTTP {r.status_code}")
            access_token = r.json().get("accessToken")
            if not access_token:
                raise PlusError("no accessToken (cookies expired?)")
            logger.info("[stream] auth OK")

            session_id = str(uuid4())

            # 2. bootstrap
            bootstrap = await self._bootstrap(client, cookies, ua)

            # 3. /prepare
            proof_token = build_proof_token(ua, client_version)
            h = _base_headers(cookies, ua, device_id, client_build, client_version, oai_is, session_id)
            h["Authorization"] = f"Bearer {access_token}"
            h["x-openai-target-path"] = "/backend-api/sentinel/chat-requirements/prepare"
            h["x-openai-target-route"] = "/backend-api/sentinel/chat-requirements/prepare"
            r = await client.post(self.PREPARE, headers=h, data=json.dumps({"p": proof_token}))
            if r.status_code != 200:
                raise PlusError(f"/prepare HTTP {r.status_code}: {r.text[:300]}")
            prep = r.json()
            logger.info("[stream] /prepare OK")

            # 4. Solve PoW + turnstile in parallel
            pow_seed = prep["proofofwork"]["seed"]
            pow_diff = prep["proofofwork"]["difficulty"]
            dx = prep["turnstile"]["dx"]
            pow_task = asyncio.to_thread(solve_pow, pow_seed, pow_diff, ua)
            ts_task = self.solver.solve_turnstile(proof_token, dx, bootstrap, ua)
            pow_answer, ts_answer = await asyncio.gather(pow_task, ts_task)
            logger.info("[stream] challenges solved")

            # 5. /finalize
            h["x-openai-target-path"] = "/backend-api/sentinel/chat-requirements/finalize"
            h["x-openai-target-route"] = "/backend-api/sentinel/chat-requirements/finalize"
            r = await client.post(self.FINALIZE, headers=h, data=json.dumps({
                "prepare_token": prep["prepare_token"],
                "proofofwork": pow_answer,
                "turnstile": ts_answer,
            }))
            if r.status_code != 200:
                raise PlusError(f"/finalize HTTP {r.status_code}: {r.text[:500]}")
            fin = r.json()
            final_token = fin.get("token")
            if not final_token:
                raise PlusError(f"/finalize: no token")
            logger.info("[stream] /finalize OK")

            # 6. POST /f/conversation with stream=True
            h2 = _base_headers(cookies, ua, device_id, client_build, client_version, oai_is, session_id)
            h2["Accept"] = "text/event-stream"
            h2["Authorization"] = f"Bearer {access_token}"
            h2["openai-sentinel-chat-requirements-token"] = final_token
            h2["openai-sentinel-proof-token"] = pow_answer
            h2["openai-sentinel-turnstile-token"] = ts_answer
            h2["x-oai-turn-trace-id"] = str(uuid4())
            h2["x-openai-target-path"] = "/backend-api/f/conversation"
            h2["x-openai-target-route"] = "/backend-api/f/conversation"

            body = {
                "action": "next",
                "messages": [{
                    "id": str(uuid4()), "author": {"role": "user"},
                    "create_time": time.time(),
                    "content": {"content_type": "text", "parts": [message]},
                    "metadata": {
                        "developer_mode_connector_ids": [],
                        "selected_github_repos": [], "selected_all_github_repos": False,
                        "system_hints": [],
                        "serialization_metadata": {"custom_symbol_offsets": []},
                    },
                }],
                "parent_message_id": "client-created-root",
                "model": chosen_model,
                "client_prepare_state": "success",
                "timezone_offset_min": -420, "timezone": "Asia/Bangkok",
                "conversation_mode": {"kind": "primary_assistant"},
                "enable_message_followups": True,
                "system_hints": [],
                "supports_buffering": True, "supported_encodings": ["v1"],
                "client_contextual_info": {
                    "is_dark_mode": True, "time_since_loaded": 7,
                    "page_height": 945, "page_width": 1106, "pixel_ratio": 1,
                    "screen_height": 1080, "screen_width": 1920,
                    "app_name": "chatgpt.com",
                },
                "paragen_cot_summary_display_override": "allow",
                "force_parallel_switch": "auto", "thinking_effort": "standard",
            }

            r = await client.post(self.CHAT, headers=h2, data=json.dumps(body), stream=True)
            logger.info("[stream] /conversation HTTP %d (streaming)", r.status_code)
            if r.status_code != 200:
                text = await r.atext()
                raise PlusError(f"/conversation HTTP {r.status_code}: {text[:500]}")

            raw_body = b""
            async for chunk in r.aiter_content():
                raw_body += chunk

            # decode with carry-over rule; yield incremental deltas
            last_path = ""
            last_op = "append"
            parts0 = ""
            for ev in iter_sse_events(raw_body.decode("utf-8", errors="replace")):
                if not isinstance(ev, dict):
                    continue
                if ev.get("conversation_id"):
                    self._last_conv_id = ev["conversation_id"]
                if "v" not in ev and "o" not in ev and "p" not in ev:
                    continue
                if "p" in ev:
                    last_path = ev["p"]
                if "o" in ev:
                    last_op = ev["o"]
                path = last_path
                op = last_op or "append"
                value = ev.get("v")

                # batched patch — scan for parts/0 appends
                if op == "patch" and isinstance(value, list):
                    for sub in value:
                        if not isinstance(sub, dict):
                            continue
                        sv, so, sp = sub.get("v"), sub.get("o", "append"), sub.get("p", "")
                        if isinstance(sv, str) and "/parts/0" in sp and so == "append":
                            parts0 += sv
                            yield sv
                    continue

                if not isinstance(value, str):
                    continue
                if "/parts/0" not in path:
                    continue
                if op == "append":
                    parts0 += value
                    yield value
                elif op in ("add", "replace"):
                    delta = value[len(parts0):]
                    parts0 = value
                    if delta:
                        yield delta


async def _poll_for_image(client, cookies, ua, access_token,
                          device_id, client_build, client_version, oai_is,
                          conversation_id: str | None,
                          max_attempts: int = 60, interval: float = 3) -> list[str]:
    """Poll /backend-api/conversation/{id} until a multimodal_text asset appears."""
    if not conversation_id:
        return []
    h = _base_headers(cookies, ua, device_id, client_build, client_version, oai_is, str(uuid4()))
    h["Authorization"] = f"Bearer {access_token}"
    h["x-openai-target-path"] = f"/backend-api/conversation/{conversation_id}"
    h["x-openai-target-route"] = "/backend-api/conversation/[id]"

    for attempt in range(max_attempts):
        await asyncio.sleep(interval)
        r = await client.get(f"https://chatgpt.com/backend-api/conversation/{conversation_id}", headers=h)
        if r.status_code != 200:
            logger.warning("[plus.poll] GET HTTP %d", r.status_code)
            continue
        data = r.json()
        assets = []
        for node in (data.get("mapping") or {}).values():
            if not isinstance(node, dict):
                continue
            msg = node.get("message")
            if not msg or not isinstance(msg, dict):
                continue
            if (msg.get("author") or {}).get("role") not in ("assistant", "tool"):
                continue
            content = msg.get("content") or {}
            if content.get("content_type") == "multimodal_text":
                for part in content.get("parts") or []:
                    if isinstance(part, dict) and part.get("asset_pointer"):
                        assets.append(part["asset_pointer"])
        logger.info("[plus.poll] attempt %d assets=%d", attempt, len(assets))
        if assets:
            return assets
    return []


def _parse_sse(text: str) -> dict:
    """Parse v1-encoding SSE stream from /f/conversation."""
    conv_id = None
    image_assets = []
    snapshot_text = ""   # from final assistant message snapshot (most reliable)
    delta_text = ""      # reconstructed from append/patch ops (fallback)

    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        p = line[5:].strip()
        if not p or p == "[DONE]":
            continue
        try:
            ch = json.loads(p)
        except json.JSONDecodeError:
            continue
        if not isinstance(ch, dict):
            continue
        conv_id = ch.get("conversation_id") or conv_id

        v = ch.get("v")

        # Assistant message snapshot — prefer the last non-empty one
        if isinstance(v, dict):
            msg = v.get("message")
            if isinstance(msg, dict) and (msg.get("author") or {}).get("role") in ("assistant", "tool"):
                content = msg.get("content") or {}
                ct = content.get("content_type")
                if ct == "text":
                    joined = "".join(x for x in (content.get("parts") or []) if isinstance(x, str))
                    if joined:
                        snapshot_text = joined
                elif ct == "multimodal_text":
                    for p2 in content.get("parts") or []:
                        if isinstance(p2, str) and p2:
                            snapshot_text = p2
                        elif isinstance(p2, dict):
                            ap = p2.get("asset_pointer", "")
                            if ap:
                                image_assets.append(ap)

        # Delta ops (append = incremental, patch = replace entire value)
        elif isinstance(v, str):
            op = ch.get("o", "")
            path = ch.get("p", "")
            if "/parts/0" in path:
                if op == "append":
                    delta_text += v
                elif op == "patch":
                    delta_text = v

        # Batched ops
        elif isinstance(v, list):
            for entry in v:
                if not isinstance(entry, dict):
                    continue
                sv = entry.get("v")
                sp = entry.get("p", "")
                so = entry.get("o", "")
                if isinstance(sv, str) and "/parts/0" in sp:
                    if so == "append":
                        delta_text += sv
                    elif so == "patch":
                        delta_text = sv

    full = snapshot_text or delta_text
    return {"message": full.strip(), "conversation_id": conv_id, "image_assets": image_assets}
