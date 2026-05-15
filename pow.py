"""
ChatGPT proof-of-work solver. Port of `_generateAnswer` from bundle.

The bundle does:
  config = [core+screen, parseTime, 4294705152, counter, userAgent, lang, ...]
  for nonce in 0..N:
    config[3] = nonce
    base = btoa(JSON.stringify(config))
    hash = sha3_512(seed + base)
    if hash.hex()[:diffLen] <= difficulty:
      return "gAAAAAB" + base
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from base64 import b64encode
from datetime import datetime, timezone

SIMULATED_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)


def _utc_parse_time() -> str:
    """JS: new Date(Date.now() - 8*3600*1000).toUTCString().replace('GMT', 'GMT+0100 (Central European Time)')."""
    from datetime import timedelta
    d = datetime.now(timezone.utc) - timedelta(hours=8)
    s = d.strftime("%a, %d %b %Y %H:%M:%S GMT")
    return s.replace("GMT", "GMT+0100 (Central European Time)")


def solve_pow(seed: str, difficulty: str, user_agent: str | None = None,
              max_iters: int = 200_000) -> str:
    cores = [8, 12, 16, 24]
    screens = [3000, 4000, 6000]
    core = random.choice(cores)
    screen = random.choice(screens)
    ua = user_agent or SIMULATED_UA

    config = [core + screen, _utc_parse_time(), 4294705152, 0, ua]
    diff_len = len(difficulty) // 2

    for i in range(max_iters):
        config[3] = i
        payload = json.dumps(config, separators=(",", ":"), ensure_ascii=False)
        base = b64encode(payload.encode()).decode()
        h = hashlib.sha3_512((seed + base).encode()).hexdigest()
        if h[:diff_len] <= difficulty:
            return "gAAAAAB" + base

    # Fallback — server may reject but at least matches format
    fallback = b64encode(f'"{seed}"'.encode()).decode()
    return "gAAAAABwQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D" + fallback
