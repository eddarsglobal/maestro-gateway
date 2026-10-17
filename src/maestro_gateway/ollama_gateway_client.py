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
MISSION_STREAM_POLICY: ContextVar[dict[str, Any] | None] = ContextVar("maestro_stream_policy", default=None)
MISSION_STREAM_METRICS: ContextVar[dict[str, Any] | None] = ContextVar("maestro_stream_metrics", default=None)
MISSION_FOREGROUND: ContextVar[bool] = ContextVar("maestro_foreground_inference", default=False)

# V8.11.3 residency policy: DIRECT-fast user turns may keep the already-authorized
# local primary hot across ordinary work/sleep gaps. This never loads a model at
# zero-model boot; it only extends provider cache residency after real inference.
DIRECT_FAST_KEEP_ALIVE_DEFAULT = "12h"
FOREGROUND_KEEP_ALIVE_DEFAULT = "60m"
PROVIDER_RESIDENT_PRESERVE_DEFAULT = "12h"
MAX_KEEP_ALIVE_SECONDS = 24 * 3600

# Provider-local residency bookkeeping. This never selects or authorizes a model;
# it only prevents a background synthetic probe from immediately unloading a
# model that a foreground user mission deliberately kept warm.
_RESIDENCY_LOCK = threading.Lock()
_FOREGROUND_RESIDENCY_UNTIL: dict[str, float] = {}


def _remember_foreground_residency(model: str, seconds: float) -> None:
    if not model or seconds <= 0:
        return
    with _RESIDENCY_LOCK:
        _FOREGROUND_RESIDENCY_UNTIL[model] = max(
            _FOREGROUND_RESIDENCY_UNTIL.get(model, 0.0), time.monotonic() + seconds
        )


def _foreground_residency_remaining(model: str) -> float:
    now = time.monotonic()
    with _RESIDENCY_LOCK:
        expired = [key for key, until in _FOREGROUND_RESIDENCY_UNTIL.items() if until <= now]
        for key in expired:
            _FOREGROUND_RESIDENCY_UNTIL.pop(key, None)
        return max(0.0, _FOREGROUND_RESIDENCY_UNTIL.get(model, 0.0) - now)


def _provider_model_names(payload: Any) -> set[str]:
    """Normalize Ollama /api/ps without treating provider state as authority."""
    if not isinstance(payload, dict):
        return set()
    names: set[str] = set()
    for row in payload.get("models") or ():
        if not isinstance(row, dict):
            continue
        for key in ("name", "model"):
            value = row.get(key)
            if isinstance(value, str) and value.strip():
                names.add(value.strip())
    return names


def _duration_seconds(value: str) -> int:
    match = re.fullmatch(r"(0|[1-9][0-9]{0,3})([smh])", value)
    if not match:
        return 0
    return int(match.group(1)) * {"s": 1, "m": 60, "h": 3600}[match.group(2)]


def reset_stream_metrics() -> None:
    MISSION_STREAM_METRICS.set(None)


def current_stream_metrics() -> dict[str, Any]:
    value = MISSION_STREAM_METRICS.get()
    return dict(value) if isinstance(value, dict) else {}


def _adaptive_num_ctx(payload: dict[str, Any]) -> int | None:
    """Choose a conservative smaller KV context for local generation.

    Large default context windows cost RAM and CPU even when a short chat turn
    needs only a fraction of them.  Never choose a context smaller than the
    estimated prompt + output budget, and decline to override genuinely large
    requests.
    """
    prompt = str(payload.get("prompt") or "")
    options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
    raw_predict = options.get("num_predict", 256)
    try:
        predict = max(1, min(2048, int(raw_predict)))
    except (TypeError, ValueError):
        predict = 256
    # Conservative multilingual estimate: roughly <=2 chars/token in the
    # worst ordinary case, plus margin for templates/special tokens.
    estimated_prompt = max(64, (len(prompt) + 1) // 2)
    required = estimated_prompt + predict + 192
    if required > 8192:
        return None
    # Preserve the conservative general-purpose V8.8 floor. Intrinsically
    # bounded foreground shapes may lower it to 512 inside _request only after
    # their provider-side output ceiling is known.
    target = 1024
    while target < required:
        target *= 2
    return min(target, 8192)


def _terminal_text(value: str) -> bool:
    return bool(re.search(r"[.!?。！？…][\s\"\'»”’)*_`~]*$", value.strip()))


def _stream_policy_complete(text: str, policy: dict[str, Any] | None) -> bool:
    if not isinstance(policy, dict):
        return False
    mode = str(policy.get("mode") or "disabled")
    try:
        min_chars = int(policy.get("min_chars") or 0)
    except (TypeError, ValueError):
        min_chars = 0
    stripped = text.strip()
    if mode == "disabled" or len(stripped) < min_chars:
        return False
    if mode == "three_steps":
        # Structured bounded answers do not need a final full stop to be known
        # complete. Require all requested sections and substantive final content
        # instead; this avoids decoding a needless conclusion. Accept common
        # model numbering variants without weakening the semantic shape.
        positions: list[int] = []
        for n in (1, 2, 3):
            match = re.search(
                rf"(?:^|\n|\s)(?:[#>*_-]+\s*)?(?:\*\*)?{n}\s*(?:[.):\-–—](?:\*\*)?|(?:\*\*)[.):\-–—])\s+",
                stripped, re.I
            )
            if match is None:
                return False
            positions.append(match.start())
        if not (positions[0] < positions[1] < positions[2]):
            return False
        if policy.get("requires_limit"):
            limit = re.search(
                r"(?:^|\n)\s*(?:[#>*_-]+\s*)?(?:\*\*)?"
                r"(?:limite|limitation|limit|grenze|l[ií]mite|ograni[cč]en\w*|قيد)"
                r"\s*:?\s*(?:\*\*)?\s*(.+)$", stripped, re.I | re.S
            )
            if limit is None or limit.start() <= positions[2]:
                return False
            final_words = re.findall(r"\w+", limit.group(1), re.UNICODE)
            if len(final_words) < 3:
                return False
            # Either punctuation or four lexical words is sufficient evidence
            # that the bounded limitation clause itself has been expressed.
            if not _terminal_text(stripped) and len(final_words) < 4:
                return False
            # FIXED5.4 FIXED1: a 4-word fragment is not a safe stream boundary.
            # Legacy manually specified policy without flag stays compatible.
            if policy.get('require_terminal_boundary') and not _terminal_text(stripped):
                return False
        elif not _terminal_text(stripped):
            return False
        return True
    if not _terminal_text(stripped):
        return False
    if mode == "one_sentence":
        return True
    if mode == "quick":
        # Require some lexical substance, not a model emitting only punctuation.
        return len(re.findall(r"\w+", stripped, re.UNICODE)) >= 7
    return False


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

    def _provider_model_resident(self, model: str) -> bool:
        """Read provider residency only; never load/start/select a model."""
        if not model:
            return False
        try:
            state = super()._request("/api/ps")
        except (OllamaHTTPError, OSError, ValueError, TypeError):
            return False
        return model in _provider_model_names(state)

    def _request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        residency_meta: dict[str, Any] = {}
        if path == "/api/generate" and payload is not None:
            foreground = bool(MISSION_FOREGROUND.get())
            model = str(payload.get("model") or "")
            policy = MISSION_STREAM_POLICY.get()
            mode = str(policy.get("mode") or "disabled") if isinstance(policy, dict) else "disabled"
            env_name = "MAESTRO_OLLAMA_KEEP_ALIVE" if foreground else "MAESTRO_OLLAMA_PROBE_KEEP_ALIVE"
            default_keep_alive = (
                DIRECT_FAST_KEEP_ALIVE_DEFAULT
                if foreground and mode == "direct_fast"
                else FOREGROUND_KEEP_ALIVE_DEFAULT if foreground else "0s"
            )
            keep_alive = os.getenv(env_name, default_keep_alive).strip()
            seconds = _duration_seconds(keep_alive)
            if seconds > MAX_KEEP_ALIVE_SECONDS or (seconds == 0 and keep_alive != "0s"):
                keep_alive = default_keep_alive
                seconds = _duration_seconds(keep_alive)

            provider_resident = False
            cross_process_preserved = False
            if foreground:
                _remember_foreground_residency(model, seconds)
            elif keep_alive == "0s":
                in_process_resident = _foreground_residency_remaining(model) > 0
                if not in_process_resident:
                    provider_resident = self._provider_model_resident(model)
                if in_process_resident or provider_resident:
                    # V8.11.3: a fresh Gateway process must not evict an Ollama
                    # model that survived the process restart. Provider /api/ps
                    # is read-only residency evidence, not model authority.
                    if in_process_resident:
                        remaining = _foreground_residency_remaining(model)
                        if remaining <= 3600:
                            preserve_default = "60m"
                        else:
                            preserve_default = f"{max(1, min(24, int((remaining + 3599) // 3600)))}h"
                    else:
                        preserve_default = os.getenv(
                            "MAESTRO_OLLAMA_RESIDENT_PRESERVE_KEEP_ALIVE",
                            PROVIDER_RESIDENT_PRESERVE_DEFAULT,
                        ).strip()
                    preserve_seconds = _duration_seconds(preserve_default)
                    if not (0 < preserve_seconds <= MAX_KEEP_ALIVE_SECONDS):
                        preserve_default = PROVIDER_RESIDENT_PRESERVE_DEFAULT
                        preserve_seconds = _duration_seconds(preserve_default)
                    keep_alive = preserve_default
                    seconds = preserve_seconds
                    _remember_foreground_residency(model, seconds)
                    cross_process_preserved = provider_resident and not in_process_resident

            residency_meta = {
                "residency_policy": (
                    "extended_direct_fast" if foreground and mode == "direct_fast"
                    else "foreground_standard" if foreground
                    else "probe_preserve_resident" if keep_alive != "0s"
                    else "probe_ephemeral"
                ),
                "provider_resident_before_request": provider_resident,
                "cross_process_residency_preserved": cross_process_preserved,
            }

            options = dict(payload.get("options") or {}) if isinstance(payload.get("options"), dict) else {}
            # V8.10.1 safety rule: never silently lower the caller's generation
            # budget at the provider edge. A provider-side shape cap can end with
            # done_reason=length even when the mission itself authorized a larger
            # budget, which the Response Quality Guard correctly rejects as
            # generation_token_limit. Speed comes from compact prompts, smaller
            # context allocation and stream completion -- not hidden truncation.
            #
            # Keep the requested num_predict unchanged for every mission class.
            # Provider-neutral mission policy remains the sole owner of output
            # budget semantics.
            if "num_ctx" not in options:
                context_window = _adaptive_num_ctx({**payload, "options": options})
                if isinstance(policy, dict) and str(policy.get("mode") or "disabled") in {"three_steps", "one_sentence", "quick", "direct_fast"}:
                    # Bounded foreground prompts can use a smaller KV window
                    # without lowering num_predict. DIRECT-fast may use 512 only
                    # when the complete caller budget still fits conservatively.
                    prompt_text = str(payload.get("prompt") or "")
                    try:
                        bounded_predict = max(1, int(options.get("num_predict", 256)))
                    except (TypeError, ValueError):
                        bounded_predict = 256
                    bounded_required = max(64, (len(prompt_text) + 1) // 2) + bounded_predict + 96
                    mode = str(policy.get("mode") or "disabled")
                    if mode == "direct_fast" and bounded_required <= 512:
                        context_window = 512
                    elif bounded_required <= 768:
                        context_window = 768
                if context_window is not None:
                    options["num_ctx"] = context_window
            payload = {**payload, "keep_alive": keep_alive, "options": options}
        # This adapter consumes Ollama's native NDJSON stream internally and
        # returns the same complete dictionary expected by the immutable Core.
        # It uses separate absolute, first-progress and progress-idle limits.
        # The UI does NOT yet consume individual tokens.
        if path == "/api/generate":
            result = self._bounded_generate_request(path, payload)
            if residency_meta:
                metrics = current_stream_metrics()
                metrics.update(residency_meta)
                MISSION_STREAM_METRICS.set(metrics)
            return result
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
            first_token_ms: float | None = None
            early_stopped = False
            stream_policy = MISSION_STREAM_POLICY.get()
            # readline() blocks on partial lines; socket shutdown by watchdog
            # unblocks it. Never accept unfinished output as a complete answer.
            while True:
                try:
                    line = reply.readline(1_048_577)
                except (OSError, http.client.HTTPException, ValueError, AttributeError) as exc:
                    # Python's chunked-response parser can surface ValueError or
                    # AttributeError when a local provider closes a malformed or
                    # incomplete chunked stream. Treat this as a provider
                    # transport failure, not as an uncaught daemon-killing bug.
                    if cancellation is not None and cancellation.is_set():
                        raise MissionCancelledError('Ollama generation cancelled') from exc
                    if expired.is_set():
                        raise TimeoutError('Ollama generation exceeded wall-clock deadline') from exc
                    raise OllamaHTTPError(
                        'Ollama streaming transport failure: ' + type(exc).__name__
                    ) from exc
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
                    if first_token_ms is None:
                        first_token_ms = round((time.monotonic() - started) * 1000, 3)
                    fragments.append(part)
                    if _stream_policy_complete("".join(fragments), stream_policy):
                        # Disconnecting a local Ollama stream cancels surplus
                        # decoding.  The mission-level quality guard still
                        # validates the captured answer before delivery.
                        early_stopped = True
                        final = {
                            "model": event.get("model") or (payload or {}).get("model"),
                            "done": True,
                            "done_reason": "maestro_sufficient",
                        }
                        break
                if event.get("done") is True:
                    final = event
                    break
            if cancellation is not None and cancellation.is_set():
                raise MissionCancelledError("Ollama generation cancelled")
            if expired.is_set() or time.monotonic() >= started + effective_timeout:
                raise TimeoutError("Ollama generation exceeded wall-clock deadline")
            if final is None:
                raise OllamaHTTPError("Ollama stream ended without completion marker")
            wall_ms = round((time.monotonic() - started) * 1000, 3)
            text = "".join(fragments)
            decode_after_first_ms = (
                round(max(0.0, wall_ms - first_token_ms), 3)
                if isinstance(first_token_ms, (int, float)) else None
            )
            active_seconds = (decode_after_first_ms / 1000.0
                              if isinstance(decode_after_first_ms, (int, float))
                              and decode_after_first_ms > 0 else None)
            MISSION_STREAM_METRICS.set({
                "first_token_ms": first_token_ms,
                "transport_wall_ms": wall_ms,
                "decode_after_first_ms": decode_after_first_ms,
                "generated_characters": len(text),
                "stream_fragments": len(fragments),
                "characters_per_second_after_first": (
                    round(len(text) / active_seconds, 3) if active_seconds else None
                ),
                "early_completion": early_stopped,
                "adaptive_num_ctx": (payload or {}).get("options", {}).get("num_ctx")
                    if isinstance((payload or {}).get("options"), dict) else None,
                "adaptive_num_predict": (payload or {}).get("options", {}).get("num_predict")
                    if isinstance((payload or {}).get("options"), dict) else None,
                "residency_keep_alive": (payload or {}).get("keep_alive"),
            })
            return {**final, "response": text}
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
