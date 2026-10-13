"""Local Ollama transport preferences, without altering the sovereign Core.

Ollama receives keep_alive only for inference calls. This is a cache hint,
not an execution or mission authorization. Set MAESTRO_OLLAMA_KEEP_ALIVE=0s
if resident model memory must be minimized.
"""
from __future__ import annotations

import os
import re
import json
import socket
import threading
import http.client
import time
from contextvars import ContextVar

from .mission_control import MissionCancelledError
from urllib.parse import urlsplit
from typing import Any

from maestro.providers.ollama.client import OllamaClient, OllamaHTTPError

# Scoped to one governed mission. Never grant model authority.
MISSION_DEADLINE: ContextVar[float | None] = ContextVar("maestro_mission_deadline", default=None)
MISSION_CANCEL: ContextVar[threading.Event | None] = ContextVar("maestro_mission_cancel", default=None)


class GatewayOllamaClient(OllamaClient):
    # Prevent the bundled desktop from waiting 120 seconds per resource call.
    # This is an IO-bound timeout, not a claim about model quality or speed.
    DEFAULT_TIMEOUT_SECONDS = 150.0
    FIRST_PROGRESS_SECONDS = 120.0
    STREAM_IDLE_SECONDS = 45.0

    def __init__(self, base_url: str = "http://127.0.0.1:11434", timeout: float | None = None):
        if timeout is None:
            raw = os.getenv("MAESTRO_OLLAMA_REQUEST_TIMEOUT", "150")
            try:
                timeout = float(raw)
            except (TypeError, ValueError):
                timeout = self.DEFAULT_TIMEOUT_SECONDS
        # Bound local request duration, including a misconfigured environment.
        if not (3.0 <= timeout <= 240.0):
            timeout = self.DEFAULT_TIMEOUT_SECONDS
        super().__init__(base_url=base_url, timeout=timeout)

    def _request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if path == "/api/generate" and payload is not None:
            keep_alive = os.getenv("MAESTRO_OLLAMA_KEEP_ALIVE", "10m").strip()
            match = re.fullmatch(r"(0|[1-9][0-9]{0,3})([smh])", keep_alive)
            if (not match or int(match.group(1)) * {"s": 1, "m": 60, "h": 3600}[match.group(2)] > 3600):
                keep_alive = "10m"
            payload = {**payload, "keep_alive": keep_alive}
        # This adapter consumes Ollama's native NDJSON stream internally and
        # returns the same complete dictionary expected by the immutable Core.
        # It uses separate absolute, first-progress and progress-idle limits.
        # The UI does NOT yet consume individual tokens.
        if path == "/api/generate":
            return self._bounded_generate_request(path, payload)
        return super()._request(path, payload)

    def _bounded_generate_request(self, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        cancellation = MISSION_CANCEL.get()
        if cancellation is not None and cancellation.is_set():
            raise MissionCancelledError("Mission cancelled before model call")
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise OllamaHTTPError("Invalid Ollama endpoint URL")
        connector = (http.client.HTTPSConnection if parsed.scheme == "https"
                     else http.client.HTTPConnection)
        deadline = MISSION_DEADLINE.get()
        remaining = deadline - time.monotonic() if deadline is not None else self.timeout
        if remaining <= 0:
            raise TimeoutError("Governed mission wall-clock budget exhausted")
        effective_timeout = min(self.timeout, remaining)
        conn = connector(parsed.hostname, parsed.port, timeout=effective_timeout)
        expired = threading.Event()
        closed = threading.Event()
        live_socket: list[socket.socket] = []
        started = time.monotonic()
        progress = [started]
        received_chunk = [False]
        # Cold-start models may take much longer to load than to emit a token.
        # After data starts arriving, an idle stream should fail sooner.
        first_progress_limit = min(self.FIRST_PROGRESS_SECONDS, effective_timeout)
        progress_idle_limit = min(self.STREAM_IDLE_SECONDS, effective_timeout)
        # Closing the transport wakes a blocked read even when the server
        # periodically sends data, unlike urllib's inactivity timeout.
        def abort() -> None:
            expired.set()
            try:
                # http.client can clear conn.sock once it hands us an HTTP
                # response. Preserve the live socket before getresponse().
                sock = live_socket[0] if live_socket else conn.sock
                if sock is not None:
                    sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()

        def watchdog() -> None:
            absolute_end = started + effective_timeout
            while not closed.is_set():
                if cancellation is not None and cancellation.is_set():
                    abort()
                    return
                idle_cap = progress_idle_limit if received_chunk[0] else first_progress_limit
                remaining = min(absolute_end - time.monotonic(),
                                progress[0] + idle_cap - time.monotonic())
                if remaining <= 0:
                    abort()
                    return
                closed.wait(min(remaining, 0.05))
        watcher = threading.Thread(target=watchdog, daemon=True)
        watcher.start()
        try:
            endpoint = parsed.path.rstrip("/") + path
            # The sovereign Core's external contract stays non-streaming; only
            # this transport requests incremental events for progress tracking.
            stream_payload = {**(payload or {}), "stream": True}
            body = json.dumps(stream_payload).encode("utf-8")
            conn.request("POST", endpoint, body=body,
                         headers={"Content-Type": "application/json"})
            if conn.sock is not None:
                live_socket.append(conn.sock)
            reply = conn.getresponse()
            if reply.status >= 400:
                error_body = reply.read(4096)
                raise OllamaHTTPError(
                    f"Ollama HTTP {reply.status}: "
                    + error_body.decode("utf-8", errors="replace")[:500]
                )
            fragments: list[str] = []
            final: dict[str, Any] | None = None
            total_bytes = 0
            # readline() blocks on partial lines; socket shutdown by watchdog
            # unblocks it. Never accept unfinished output as a complete answer.
            while True:
                line = reply.readline(1_048_577)
                if cancellation is not None and cancellation.is_set():
                    raise MissionCancelledError("Ollama generation cancelled")
                if expired.is_set():
                    raise TimeoutError("Ollama generation exceeded wall-clock deadline")
                if not line:
                    break
                total_bytes += len(line)
                if len(line) > 1_048_576 or total_bytes > 8_388_608:
                    raise OllamaHTTPError("Ollama streaming response exceeds size limit")
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except (ValueError, TypeError) as exc:
                    raise OllamaHTTPError("Malformed Ollama stream event") from exc
                if not isinstance(event, dict):
                    raise OllamaHTTPError("Malformed Ollama stream event")
                if event.get("error"):
                    raise OllamaHTTPError("Ollama generation error: " + str(event["error"])[:500])
                progress[0] = time.monotonic()
                received_chunk[0] = True
                part = event.get("response")
                if isinstance(part, str) and part:
                    fragments.append(part)
                if event.get("done") is True:
                    final = event
                    break
            if cancellation is not None and cancellation.is_set():
                raise MissionCancelledError("Ollama generation cancelled")
            if expired.is_set() or time.monotonic() >= started + effective_timeout:
                raise TimeoutError("Ollama generation exceeded wall-clock deadline")
            if final is None:
                raise OllamaHTTPError("Ollama stream ended without completion marker")
            return {**final, "response": "".join(fragments)}
        except TimeoutError as exc:
            if cancellation is not None and cancellation.is_set():
                raise MissionCancelledError("Ollama generation cancelled") from exc
            # The socket's own deadline may fire a moment BEFORE our watchdog
            # timer marks `expired`. Both indicate a timed-out model request.
            # Preserve that exception type so bounded AUTO recovery can run.
            raise TimeoutError("Ollama generation exceeded wall-clock deadline") from exc
        except (OSError, http.client.HTTPException) as exc:
            if cancellation is not None and cancellation.is_set():
                raise MissionCancelledError("Ollama generation cancelled") from exc
            if cancellation is not None and cancellation.is_set():
                raise MissionCancelledError("Ollama generation cancelled")
            if expired.is_set():
                raise TimeoutError("Ollama generation exceeded wall-clock deadline") from exc
            raise OllamaHTTPError(f"Ollama transport failure: {exc}") from exc
        finally:
            closed.set()
            conn.close()
            watcher.join(timeout=0.2)
