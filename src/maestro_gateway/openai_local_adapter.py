"""Provider-neutral local OpenAI-compatible inference edge.

Only HTTP loopback URLs that were explicitly probed are registered. This
adapter never creates capabilities or authorizes a mission; the Core does that.
"""
from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from urllib.parse import urlsplit

from maestro.sovereign import IntelligenceRequest, IntelligenceResponse
from .mission_control import MissionCancelledError
from .ollama_gateway_client import MISSION_CANCEL, MISSION_DEADLINE
from .provider_inventory import _loopback_url

class LocalInferenceError(RuntimeError):
    pass

class OpenAICompatibleLocalAdapter:
    io_only = True  # Audited: no Core state accessed while the HTTP call runs.

    def __init__(self, provider_id: str, model_id: str, base_url: str):
        validated = _loopback_url(base_url)
        if validated is None:
            raise ValueError("Local inference adapter requires a validated loopback URL")
        self.provider_id = provider_id
        self.model_id = model_id
        self.resource_id = f"{provider_id}::{model_id}"
        self.base_url = validated

    def execute(self, request: IntelligenceRequest) -> IntelligenceResponse:
        cancellation = MISSION_CANCEL.get()
        if cancellation is not None and cancellation.is_set():
            raise MissionCancelledError("Mission cancelled before local inference")
        until = MISSION_DEADLINE.get()
        # Provider-neutral adapters share the governed mission deadline.
        # This remains non-streaming; transport-level event streaming is
        # separately implemented for Ollama in V5.
        timeout = min(150.0, max(0.0, until - time.monotonic())) if until is not None else 150.0
        if timeout <= 0:
            raise TimeoutError("Mission inference deadline exhausted")
        parsed = urlsplit(self.base_url)
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
        ended = threading.Event()
        expired = threading.Event()
        started = time.monotonic()
        live_socket: list[socket.socket] = []

        def abort() -> None:
            expired.set()
            try:
                sock = live_socket[0] if live_socket else conn.sock
                if sock:
                    sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()

        def watchdog() -> None:
            until_local = started + timeout
            while not ended.is_set():
                if cancellation is not None and cancellation.is_set():
                    abort()
                    return
                remaining = until_local - time.monotonic()
                if remaining <= 0:
                    abort()
                    return
                ended.wait(min(remaining, 0.03))

        watcher = threading.Thread(target=watchdog, daemon=True)
        watcher.start()
        try:
            payload = json.dumps({
                "model": self.model_id,
                "messages": [{"role": "user", "content": request.instruction}],
                "max_tokens": min(4096, max(1, request.max_tokens or 512)),
                "stream": False,
            }).encode("utf-8")
            conn.request("POST", (parsed.path.rstrip("/") + "/v1/chat/completions"),
                         body=payload, headers={"Content-Type": "application/json"})
            if conn.sock:
                live_socket.append(conn.sock)
            response = conn.getresponse()
            body = response.read(4 * 1024 * 1024 + 1)
            if cancellation is not None and cancellation.is_set():
                raise MissionCancelledError("Local inference cancelled")
            if expired.is_set() or time.monotonic() - started >= timeout:
                raise TimeoutError("Local inference exceeded wall clock budget")
            if response.status != 200 or len(body) > 4 * 1024 * 1024:
                raise LocalInferenceError(f"Local inference HTTP {response.status} or oversized response")
            data = json.loads(body)
            choices = data.get("choices") or []
            message = choices[0].get("message", {}) if choices else {}
            output = message.get("content")
            if isinstance(output, list):
                output = "".join(str(part.get("text", "")) for part in output if isinstance(part, dict))
            if not isinstance(output, str):
                raise LocalInferenceError("Local endpoint did not provide text content")
            usage = data.get("usage") or {}
            return IntelligenceResponse(
                resource_id=self.resource_id, provider_id=self.provider_id,
                model_id=self.model_id, output=output, success=True,
                metadata={"total_ms": (time.monotonic() - started) * 1000,
                          "generated_tokens": usage.get("completion_tokens"),
                          "prompt_tokens": usage.get("prompt_tokens"),
                          "finish_reason": choices[0].get("finish_reason") if choices else None},
            )
        except (TimeoutError, socket.timeout) as exc:
            raise TimeoutError("Local inference exceeded wall clock budget") from exc
        except (ConnectionError, OSError, http.client.HTTPException) as exc:
            if cancellation is not None and cancellation.is_set():
                raise MissionCancelledError("Local inference cancelled") from exc
            if expired.is_set() or time.monotonic() - started >= timeout:
                raise TimeoutError("Local inference exceeded wall clock budget") from exc
            raise LocalInferenceError(f"Local inference transport failure: {type(exc).__name__}") from exc
        except (ValueError, IndexError, KeyError, TypeError) as exc:
            raise LocalInferenceError("Malformed local inference response") from exc
        finally:
            ended.set()
            conn.close()
            watcher.join(timeout=0.1)
