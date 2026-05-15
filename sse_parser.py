"""
Full decoder for ChatGPT's /backend-api/f/conversation SSE stream (v1 buffered encoding).

WHY THIS EXISTS
---------------
The chatgpt.com web app does NOT send plain JSON snapshots. It sends a *stateful*
JSON-pointer patch stream. A naive "json.loads(line) then read .v" parser drops
most of the tokens, because the majority of delta events carry ONLY a "v" field —
the path ("p") and the op ("o") are inherited from the previous event.

STREAM SHAPE
------------
    event: delta_encoding
    data: "v1"

    data: {"p":"","o":"add","v":{"message":{...},"conversation_id":"..."}}   # root snapshot
    data: {"p":"/message/content/parts/0","o":"append","v":"Hel"}            # first delta (explicit)
    data: {"v":"lo"}                                                         # inherits p + o
    data: {"v":" world"}                                                     # inherits p + o
    data: {"p":"/message/status","o":"replace","v":"finished_successfully"}
    data: {"o":"patch","v":[ {"p":"...","o":"...","v":...}, ... ]}            # batched ops
    data: {"type":"message_stream_complete","conversation_id":"..."}
    data: [DONE]

CARRY-OVER RULE (mirrors the chatgpt.com client decoder)
--------------------------------------------------------
    if "p" in event:  last_path = event["p"]      else: use last_path
    if "o" in event:  last_op   = event["o"]      else: use last_op
    initial defaults: last_path = "" , last_op = "append"

A turn can contain MULTIPLE messages (e.g. a "thoughts" message, then the final
"text" message). Each new message arrives as a fresh root op at p="" — we snapshot
the previous message before swapping roots.

USAGE
-----
    from sse_parser import decode_stream, parse_sse

    full = decode_stream(resp.text)        # rich result, see DecodedStream below
    print(full.message)                    # final assistant answer
    print(full.reasoning)                  # thinking / chain-of-thought text
    print(full.image_assets)               # ['file-service://file-...', ...]

    legacy = parse_sse(resp.text)           # drop-in replacement for the old _parse_sse
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any


# --------------------------------------------------------------------------- #
# JSON Pointer apply
# --------------------------------------------------------------------------- #
def _unescape(token: str) -> str:
    # RFC 6901: ~1 -> "/", ~0 -> "~"
    return token.replace("~1", "/").replace("~0", "~")


def _split_pointer(path: str) -> list[str]:
    if path in ("", "/"):
        return []
    return [_unescape(t) for t in path.lstrip("/").split("/")]


def _resolve_parent(root: Any, tokens: list[str]) -> tuple[Any, str]:
    """Walk to the container holding the last token, auto-creating dict/list nodes."""
    cur = root
    for i, tok in enumerate(tokens[:-1]):
        nxt = tokens[i + 1]
        if isinstance(cur, list):
            idx = int(tok)
            while idx >= len(cur):
                cur.append({} if not nxt.lstrip("-").isdigit() else [])
            cur = cur[idx]
        else:
            if tok not in cur or cur[tok] is None:
                cur[tok] = [] if nxt.lstrip("-").isdigit() else {}
            cur = cur[tok]
    return cur, tokens[-1]


def _apply(root: Any, op: str, path: str, value: Any) -> Any:
    """Apply one JSON-pointer op. Returns (possibly new) root."""
    tokens = _split_pointer(path)

    # whole-document op
    if not tokens:
        if op in ("add", "replace"):
            return value
        return root

    parent, key = _resolve_parent(root, tokens)

    if isinstance(parent, list):
        idx = len(parent) if key == "-" else int(key)
        if op == "add":
            if idx >= len(parent):
                parent.append(value)
            else:
                parent.insert(idx, value)
        elif op == "append":
            if idx < len(parent) and isinstance(parent[idx], str) and isinstance(value, str):
                parent[idx] += value
            elif idx < len(parent) and isinstance(parent[idx], list):
                parent[idx].extend(value if isinstance(value, list) else [value])
            elif idx < len(parent):
                parent[idx] = value
            else:
                parent.append(value)
        elif op == "replace":
            if idx < len(parent):
                parent[idx] = value
            else:
                parent.append(value)
        elif op == "remove":
            if 0 <= idx < len(parent):
                del parent[idx]
    elif isinstance(parent, dict):
        if op in ("add", "replace"):
            parent[key] = value
        elif op == "append":
            existing = parent.get(key)
            if isinstance(existing, str) and isinstance(value, str):
                parent[key] = existing + value
            elif isinstance(existing, list):
                existing.extend(value if isinstance(value, list) else [value])
            else:
                parent[key] = value
        elif op == "remove":
            parent.pop(key, None)

    return root


# --------------------------------------------------------------------------- #
# Result container
# --------------------------------------------------------------------------- #
@dataclass
class DecodedStream:
    conversation_id: str | None = None
    message_id: str | None = None
    model: str | None = None
    finish: str | None = None              # message.status of the final assistant msg
    end_turn: bool | None = None
    stream_complete: bool = False
    message: str = ""                      # final assistant answer text
    reasoning: str = ""                    # concatenated thinking / chain-of-thought
    image_assets: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)   # code / dalle / search invocations
    citations: list[dict] = field(default_factory=list)
    raw_messages: list[dict] = field(default_factory=list)  # every fully-decoded message object
    error: Any = None

    def as_dict(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "model": self.model,
            "finish": self.finish,
            "end_turn": self.end_turn,
            "stream_complete": self.stream_complete,
            "message": self.message,
            "reasoning": self.reasoning,
            "image_assets": self.image_assets,
            "tool_calls": self.tool_calls,
            "citations": self.citations,
            "error": self.error,
        }


# --------------------------------------------------------------------------- #
# Iterate raw SSE lines into JSON events
# --------------------------------------------------------------------------- #
def iter_sse_events(text: str):
    """Yield parsed JSON payloads from raw SSE text. Handles `data:` continuation,
    skips `event:` / comments / `[DONE]` / the `"v1"` encoding marker."""
    data_buf: list[str] = []

    def flush():
        if not data_buf:
            return None
        payload = "\n".join(data_buf)
        data_buf.clear()
        payload = payload.strip()
        if not payload or payload == "[DONE]":
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None

    for raw in text.splitlines():
        if raw == "":                       # blank line = event boundary
            ev = flush()
            if ev is not None:
                yield ev
            continue
        if raw.startswith(":"):             # SSE comment / keep-alive
            continue
        if raw.startswith("event:"):
            continue
        if raw.startswith("data:"):
            data_buf.append(raw[5:].lstrip())
            continue
        # tolerate streams that don't put a blank line between events
        data_buf.append(raw)

    ev = flush()
    if ev is not None:
        yield ev


# --------------------------------------------------------------------------- #
# Main decoder
# --------------------------------------------------------------------------- #
_CONTROL_TYPES = {
    "title_generation",
    "moderation_response",
    "conversation_detail_metadata",
    "message_stream_complete",
}


def decode_stream(text: str) -> DecodedStream:
    """Decode a full /f/conversation SSE response into a DecodedStream."""
    out = DecodedStream()
    root: dict = {}
    messages: list[dict] = []          # completed message objects (in arrival order)
    last_path = ""
    last_op = "append"

    def flush_root():
        msg = root.get("message")
        if isinstance(msg, dict) and msg:
            messages.append(copy.deepcopy(msg))

    for ev in iter_sse_events(text):
        # encoding marker: data: "v1"
        if isinstance(ev, str):
            continue
        if not isinstance(ev, dict):
            continue

        if ev.get("conversation_id"):
            out.conversation_id = ev["conversation_id"]
        if ev.get("error"):
            out.error = ev["error"]

        etype = ev.get("type")
        if etype == "message_stream_complete":
            out.stream_complete = True
            continue
        if etype in _CONTROL_TYPES:
            continue

        # Plain (non-buffered) snapshot fallback: {"message": {...}, "conversation_id": ...}
        if "message" in ev and "v" not in ev and "o" not in ev and "p" not in ev:
            if isinstance(root.get("message"), dict):
                flush_root()
            root = {"message": ev["message"]}
            continue

        # Not a patch event (no v/o/p) — nothing to apply
        if "v" not in ev and "o" not in ev and "p" not in ev:
            continue

        # ---- carry-over resolution ----
        if "p" in ev:
            last_path = ev["p"]
        if "o" in ev:
            last_op = ev["o"]
        path = last_path
        op = last_op or "append"
        value = ev.get("v")

        # ---- batched patch ----
        if op == "patch":
            sub_ops = value if isinstance(value, list) else []
            for sub in sub_ops:
                if not isinstance(sub, dict):
                    continue
                sp = sub.get("p", "")
                so = sub.get("o", "append")
                sv = sub.get("v")
                if sp in ("", "/") and so in ("add", "replace"):
                    if isinstance(root.get("message"), dict):
                        flush_root()
                    root = sv if isinstance(sv, dict) else {}
                else:
                    root = _apply(root, so, sp, sv)
            continue

        # ---- new message boundary: root-level add/replace ----
        if path in ("", "/") and op in ("add", "replace"):
            if isinstance(root.get("message"), dict):
                flush_root()
            root = value if isinstance(value, dict) else {}
            continue

        # ---- ordinary op ----
        root = _apply(root, op, path, value)

    # capture the final in-flight message
    flush_root()

    # ---- extract a friendly result from the decoded message objects ----
    text_chunks: list[str] = []
    for m in messages:
        out.raw_messages.append(m)
        author = m.get("author") or {}
        role = author.get("role")
        content = m.get("content") or {}
        ct = content.get("content_type")
        meta = m.get("metadata") or {}

        if role == "assistant":
            out.model = meta.get("model_slug") or meta.get("default_model_slug") or out.model
            out.message_id = m.get("id") or out.message_id
            if m.get("status"):
                out.finish = m["status"]
            if m.get("end_turn") is not None:
                out.end_turn = m["end_turn"]

        # citations / web search results
        for c in meta.get("citations") or []:
            out.citations.append(c)
        if meta.get("content_references"):
            out.citations.extend(meta["content_references"])

        if ct == "text":
            joined = "".join(p for p in (content.get("parts") or []) if isinstance(p, str))
            if role == "assistant" and joined.strip():
                text_chunks.append(joined)

        elif ct == "multimodal_text":
            for p in content.get("parts") or []:
                if isinstance(p, str):
                    if p.strip():
                        text_chunks.append(p)
                elif isinstance(p, dict):
                    ap = p.get("asset_pointer")
                    if ap:
                        out.image_assets.append(ap)

        elif ct in ("thoughts", "reasoning_recap"):
            for p in content.get("parts") or []:
                if isinstance(p, str):
                    if p.strip():
                        out.reasoning += p + "\n"
                elif isinstance(p, dict):
                    seg = p.get("content") or p.get("summary") or p.get("text") or ""
                    if seg:
                        out.reasoning += seg + "\n"
            if isinstance(content.get("content"), str) and content["content"].strip():
                out.reasoning += content["content"] + "\n"

        elif ct == "code":
            out.tool_calls.append({
                "recipient": m.get("recipient"),
                "language": content.get("language"),
                "text": content.get("text", ""),
            })

        elif ct in ("execution_output", "tether_browsing_display", "tether_quote"):
            out.tool_calls.append({
                "recipient": m.get("recipient"),
                "content_type": ct,
                "text": content.get("text", "") or content.get("result", ""),
            })

    out.message = (text_chunks[-1].strip() if text_chunks else "")
    out.reasoning = out.reasoning.strip()
    return out


# --------------------------------------------------------------------------- #
# Backwards-compatible shim — drop-in replacement for gpt_plus._parse_sse
# --------------------------------------------------------------------------- #
def parse_sse(text: str) -> dict:
    """Same return shape as the old _parse_sse: {message, conversation_id, image_assets}."""
    d = decode_stream(text)
    return {
        "message": d.message,
        "conversation_id": d.conversation_id,
        "image_assets": d.image_assets,
        # extras (harmless if unused by old callers):
        "reasoning": d.reasoning,
        "model": d.model,
        "finish": d.finish,
        "stream_complete": d.stream_complete,
        "tool_calls": d.tool_calls,
        "citations": d.citations,
    }


if __name__ == "__main__":
    import sys
    src = sys.stdin.read() if len(sys.argv) < 2 else open(sys.argv[1], encoding="utf-8").read()
    res = decode_stream(src)
    print(json.dumps(res.as_dict(), ensure_ascii=False, indent=2))
