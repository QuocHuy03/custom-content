"""
Sentinel proof token generator.

The token sent to /backend-api/sentinel/chat-requirements/prepare as {"p": "<token>"}
has the format:

    "gAAAAAC" + base64(JSON.stringify(fingerprint_array))

Decoding the real cURL captured from chatgpt.com shows the fingerprint array layout:

    [
      0,                         # flag (0 = standard)
      "<Date string from new Date()>",
      4294967296,                # 2^32 — constant
      <small counter, 1..N>,
      "<navigator.userAgent>",
      null,                      # plugin/screen related, often null
      "<oai-client-version>",
      "<navigator.language>",
      "<navigator.languages joined>",
      <Math.random()>,
      "hardwareConcurrency<−><n>",   # NOTE: uses U+2212 minus
      "<random react container key>",
      "<global function name>",
      <performance.now()>,
      "<random UUID — storage bucket / page>",
      "<comma-separated URL search param names>",
      <hardwareConcurrency int>,
      <Date.now() with fractional ms>,
      0,0,0,0,0,0,0              # event counters, all 0 at first call
    ]
"""

from __future__ import annotations

import json
import random
import time
from base64 import b64encode
from uuid import uuid4

TOKEN_PREFIX = "gAAAAAC"
JS_CLIENT_BUCKET_UUID = "a67561ed-f3e2-455f-901a-e2c8a3247003"  # stable per UA, used by client JS


def _date_string(epoch_ms: float) -> str:
    """Match JavaScript's `new Date().toString()` for GMT+0700 (Asia/Bangkok).

    Example: 'Wed May 13 2026 16:03:02 GMT+0700 (Giờ Đông Dương)'
    """
    t = time.gmtime(epoch_ms / 1000 + 7 * 3600)  # convert to Bangkok wall clock
    days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    day = days[(t.tm_wday + 1) % 7]
    return (
        f"{day} {months[t.tm_mon - 1]} {t.tm_mday:02d} {t.tm_year} "
        f"{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d} GMT+0700 (Giờ Đông Dương)"
    )


def _react_container_key() -> str:
    """Mimic React's internal container key __reactContainer$<11 chars base36>."""
    suffix = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=11))
    return f"__reactContainer${suffix}"


def build_proof_token(
    user_agent: str,
    client_version: str,
    language: str = "en-US",
    languages_joined: str = "en-US,en,vi",
    hardware_concurrency: int = 12,
    url_search_param_names: str = (
        "utm_source,utm_medium,utm_campaign,c_id,c_agid,c_crid,c_kwid,c_ims,"
        "c_pms,c_nw,c_dvc,gad_source,gad_campaignid,gbraid,gclid"
    ),
    counter: int = 1,
) -> str:
    """Build the `p` value for /sentinel/chat-requirements/prepare."""
    now_ms = time.time() * 1000
    perf_now = round(random.uniform(40000, 80000), 1)

    fingerprint = [
        3000,                                # const — verified from real cURL
        _date_string(now_ms),
        4294967296,                          # 2^32, const
        counter,
        user_agent,
        None,
        client_version,
        language,
        languages_joined,
        0.6000000238418579,                  # Math.fround(0.6), const
        f"hardwareConcurrency−{hardware_concurrency}",  # U+2212 MINUS SIGN
        _react_container_key(),
        "structuredClone",
        perf_now,
        JS_CLIENT_BUCKET_UUID,
        url_search_param_names,
        hardware_concurrency,
        round(now_ms, 1),
        0, 0, 0, 0, 0, 0, 0,
    ]

    payload = json.dumps(fingerprint, ensure_ascii=False, separators=(",", ":"))
    return TOKEN_PREFIX + b64encode(payload.encode()).decode()
