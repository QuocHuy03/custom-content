"""Python client for the Node solver subprocess.

Spawns `node solver/solver.js` as a long-lived daemon, sends JSON requests
over stdin, reads JSON responses from stdout. One subprocess handles many
requests for fast turn-around.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SOLVER_JS = Path(__file__).parent / "solver" / "solver.js"


class SolverError(Exception):
    pass


class NodeSolver:
    """Long-lived Node subprocess wrapper."""

    def __init__(self, trace: bool = False):
        self.proc: asyncio.subprocess.Process | None = None
        self.trace = trace
        self._lock = asyncio.Lock()

    async def start(self):
        if self.proc and self.proc.returncode is None:
            return
        env = os.environ.copy()
        if self.trace:
            env["SOLVER_TRACE"] = "1"
        self.proc = await asyncio.create_subprocess_exec(
            "node", str(SOLVER_JS),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE if not self.trace else sys.stderr,
            env=env,
        )

    async def stop(self):
        if self.proc and self.proc.returncode is None:
            self.proc.stdin.close()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.proc.kill()
        self.proc = None

    async def call(self, action: str, **kwargs) -> dict:
        """Send one JSON request, await one JSON line back."""
        async with self._lock:
            await self.start()
            req = {"action": action, **kwargs}
            line = json.dumps(req, ensure_ascii=False) + "\n"
            self.proc.stdin.write(line.encode())
            await self.proc.stdin.drain()
            reply = await self.proc.stdout.readline()
            if not reply:
                stderr = await self.proc.stderr.read() if not self.trace else b""
                raise SolverError(f"solver closed unexpectedly: {stderr.decode(errors='replace')[:500]}")
            try:
                data = json.loads(reply)
            except json.JSONDecodeError as e:
                raise SolverError(f"bad reply: {e} | reply={reply!r}")
            if "error" in data:
                raise SolverError(f"solver error: {data['error']}")
            return data

    async def solve_turnstile(self, proof: str, dx: str, bootstrap: dict, user_agent: str) -> str:
        r = await self.call(
            "turnstile",
            proof=proof, dx=dx, bootstrap=bootstrap, userAgent=user_agent,
        )
        return r["token"]
