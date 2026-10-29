"""Klien sidecar OpenUI (D6).

Sidecar adalah proses bun long-lived (``backend/openui-sidecar``) yang memakai
``@openuidev/lang-core`` asli untuk parse, merge, dan system prompt. Protokol:
satu JSON per baris lewat stdin/stdout (lihat ``src/server.ts``).

I/O blocking dijalankan di thread (``asyncio.to_thread``) agar tidak bergantung
pada dukungan subprocess event loop (Windows + uvicorn). Satu lock menjaga
urutan permintaan/jawaban. Bila proses mati atau timeout, proses dibuang dan
dijalankan ulang pada permintaan berikutnya.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import subprocess
import threading
from pathlib import Path
from typing import Any

from studio.api.errors import StudioError

log = logging.getLogger(__name__)

SIDECAR_DIR = Path(__file__).resolve().parents[3] / "openui-sidecar"


class SidecarError(StudioError):
    """Sidecar tidak tersedia, timeout, atau menjawab error."""

    def __init__(self, message: str) -> None:
        super().__init__("OPENUI_SIDECAR_ERROR", message, {}, http_status=503)


class OpenUISidecar:
    """Proses sidecar dengan pemanggilan sinkron (thread-safe) dan async."""

    def __init__(
        self,
        *,
        bun: str = "bun",
        timeout_s: float = 10.0,
        sidecar_dir: Path = SIDECAR_DIR,
    ) -> None:
        self._bun = bun
        self._timeout_s = timeout_s
        self._dir = sidecar_dir
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._next_id = 0
        self._prompt_cache: dict[str, str] = {}

    # -- lifecycle ---------------------------------------------------------

    def _spawn(self) -> subprocess.Popen[str]:
        env = {**os.environ, "OPENUI_TELEMETRY_DISABLED": "1", "DO_NOT_TRACK": "1"}
        try:
            proc = subprocess.Popen(
                [self._bun, "run", "src/server.ts"],
                cwd=self._dir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
                bufsize=1,
            )
        except OSError as exc:
            raise SidecarError(f"Sidecar OpenUI gagal dijalankan ({self._bun}): {exc}") from exc
        lines: queue.Queue[str | None] = queue.Queue()

        def pump(stream: Any, sink: queue.Queue[str | None] | None) -> None:
            for line in stream:
                if sink is not None:
                    sink.put(line)
                else:
                    log.debug("openui-sidecar: %s", line.rstrip())
            if sink is not None:
                sink.put(None)

        threading.Thread(target=pump, args=(proc.stdout, lines), daemon=True).start()
        threading.Thread(target=pump, args=(proc.stderr, None), daemon=True).start()
        self._lines = lines
        return proc

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001 — pembersihan best-effort
            pass

    def close(self) -> None:
        with self._lock:
            self._kill()

    async def aclose(self) -> None:
        await asyncio.to_thread(self.close)

    # -- RPC ---------------------------------------------------------------

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._proc = self._spawn()
            self._next_id += 1
            req_id = self._next_id
            payload = json.dumps({"id": req_id, "method": method, "params": params or {}})
            try:
                assert self._proc.stdin is not None
                self._proc.stdin.write(payload + "\n")
                self._proc.stdin.flush()
                while True:
                    line = self._lines.get(timeout=self._timeout_s)
                    if line is None:
                        raise SidecarError("Sidecar OpenUI berhenti tanpa jawaban.")
                    msg = json.loads(line)
                    if msg.get("id") == req_id:
                        break
            except queue.Empty:
                self._kill()
                raise SidecarError(f"Sidecar OpenUI timeout ({self._timeout_s}s) pada '{method}'.") from None
            except (OSError, ValueError) as exc:
                self._kill()
                raise SidecarError(f"Sidecar OpenUI gagal: {exc}") from exc
            except SidecarError:
                self._kill()
                raise
        if "error" in msg:
            raise SidecarError(f"Sidecar OpenUI menolak '{method}': {msg['error']}")
        return msg["result"]

    async def acall(self, method: str, params: dict[str, Any] | None = None) -> Any:
        return await asyncio.to_thread(self.call, method, params)

    # -- API bertipe ringan --------------------------------------------------

    def analyse(self, program: str) -> dict[str, Any]:
        return self.call("analyse", {"program": program})

    def apply(self, program: str, patch: str) -> dict[str, Any]:
        return self.call("apply", {"program": program, "patch": patch})

    def prompt(
        self,
        *,
        preamble: str | None = None,
        additional_rules: list[str] | None = None,
        tool_examples: list[str] | None = None,
    ) -> str:
        key = json.dumps([preamble, additional_rules, tool_examples])
        if key not in self._prompt_cache:
            params = {
                "preamble": preamble,
                "additionalRules": additional_rules,
                "toolExamples": tool_examples,
            }
            self._prompt_cache[key] = self.call(
                "prompt", {k: v for k, v in params.items() if v is not None}
            )["prompt"]
        return self._prompt_cache[key]
