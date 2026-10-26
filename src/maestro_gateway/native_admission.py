"""P0.4.3b2c native one-Gateway admission barrier: no background inference permitted yet.

The barrier prevents scheduling races WITHIN ONE Gateway process. This is
not a provider quiescence attestation or a cross-process lease. Adapter
termination verification is not present, so production instances construct
this gate with `adapter_cancellation_verified=False` and no setting may
promote that value via environment variables.
"""
from __future__ import annotations
import threading
import time
import uuid


class ForegroundAdmissionBusy(RuntimeError):
    """A local background reservation failed to drain within the bounded wait."""


class NativeAdmissionBarrier:
    def __init__(self, *, adapter_cancellation_verified: bool = False):
        if not isinstance(adapter_cancellation_verified, bool):
            raise ValueError("Invalid adapter proof flag")
        self._adapter_verified = adapter_cancellation_verified
        self._cv = threading.Condition(threading.RLock())
        self._foreground_waiters = 0
        self._foreground_tokens: set[str] = set()
        self._idle_token: str | None = None
        self._idle_cancel: threading.Event | None = None
        self._shutdown = False

    @property
    def idle_authorized(self) -> bool:
        # Only a verified adapter MAY enable idle; production currently passes False.
        return self._adapter_verified

    def begin_idle(self, cancel: threading.Event) -> str | None:
        if not isinstance(cancel, threading.Event):
            raise ValueError("A cancellable idle runner is required")
        with self._cv:
            if (self._shutdown or not self._adapter_verified or self._idle_token is not None
                    or self._foreground_waiters or self._foreground_tokens):
                return None
            token = uuid.uuid4().hex
            self._idle_token, self._idle_cancel = token, cancel
            return token

    def end_idle(self, token: str) -> None:
        with self._cv:
            if not isinstance(token, str) or token != self._idle_token:
                raise RuntimeError("Unknown or stale idle lease")
            self._idle_token = None
            self._idle_cancel = None
            self._cv.notify_all()

    def begin_foreground(self, *, wait_seconds: float = 2.0) -> str:
        if isinstance(wait_seconds, bool) or not isinstance(wait_seconds, (int, float)) or not 0 <= wait_seconds <= 10:
            raise ValueError("Invalid bounded foreground wait")
        until = time.monotonic() + wait_seconds
        with self._cv:
            if self._shutdown:
                raise ForegroundAdmissionBusy("Gateway admission is shutting down")
            self._foreground_waiters += 1
            try:
                if self._idle_cancel is not None:
                    self._idle_cancel.set()
                while self._idle_token is not None:
                    delay = until - time.monotonic()
                    if delay <= 0:
                        raise ForegroundAdmissionBusy("Idle worker did not acknowledge quiescence")
                    self._cv.wait(min(delay, 0.05))
                    if self._idle_cancel is not None:
                        self._idle_cancel.set()
                if self._shutdown:
                    raise ForegroundAdmissionBusy("Gateway admission is shutting down")
                token = uuid.uuid4().hex
                self._foreground_tokens.add(token)
                return token
            finally:
                self._foreground_waiters -= 1
                self._cv.notify_all()

    def end_foreground(self, token: str) -> None:
        with self._cv:
            if token not in self._foreground_tokens:
                raise RuntimeError("Unknown or stale foreground lease")
            self._foreground_tokens.remove(token)
            self._cv.notify_all()

    def shutdown(self) -> None:
        with self._cv:
            self._shutdown = True
            if self._idle_cancel is not None:
                self._idle_cancel.set()
            self._cv.notify_all()

    def status(self) -> dict:
        with self._cv:
            return {
                'scope': 'single_gateway_process',
                'provider_quiescence_verified': False,
                'cross_process_exclusivity_verified': False,
                'autonomous_idle_inference_authorized': False,
                'foreground_active': len(self._foreground_tokens),
                'foreground_waiters': self._foreground_waiters,
                'idle_lease_active': self._idle_token is not None,
                'shutdown': self._shutdown,
            }
