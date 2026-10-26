"""Experimental P0.4.3b2g: cooperative shared file lease for HTTP foreground.

DOES NOT prove all inference routes participate, or Ollama-side termination.
Real background and external exclusive admission are NOT implemented.
The installer (future milestone) must safely provision a 0700 lock directory.
Fail closed if directory or lock file is unsafe or absent.
"""
from __future__ import annotations
import threading
import os
from pathlib import Path
from .native_admission import NativeAdmissionBarrier, ForegroundAdmissionBusy
from .cooperative_admission import CooperativeAdmission, AdmissionBlocked


class CrossProcessForegroundAdmission(NativeAdmissionBarrier):
    """Production Gateway candidate only; preserve the NativeAdmissionBarrier API.

    An OS-level shared lock is held from successful admission until the mission
    exits its finally block.  Real idle remains prohibited unconditionally.
    """
    def __init__(self, *, lock_root: Path, adapter_cancellation_verified: bool = False):
        if adapter_cancellation_verified is not False:
            raise ValueError("provider-side quiescence is not certified")
        super().__init__(adapter_cancellation_verified=False)
        # A symlink anywhere in the lock path may redirect all Gateway workers.
        # Unlike test tmp aliases, production uses a canonical HOME-owned path.
        lock_path = Path(os.path.abspath(os.fspath(lock_root)))
        if any(component.is_symlink() for component in (lock_path, *lock_path.parents)):
            raise AdmissionBlocked("symlink lock directory ancestor forbidden")
        self._cooperative = CooperativeAdmission(lock_path)
        self._cross_guard = threading.Lock()
        self._cross_by_token: dict[str, object] = {}
        # Check current root/lock rights before any request is accepted.
        # This does not create the directory and never falls back to local-only.
        with self._cooperative.foreground(wait=0):
            pass

    def begin_foreground(self, *, wait_seconds: float = 2.0) -> str:
        if isinstance(wait_seconds, bool) or not isinstance(wait_seconds, (int, float)) or not 0 <= wait_seconds <= 10:
            raise ValueError("Invalid bounded foreground wait")
        # The cooperative contract bounds its wait at <=3 seconds.
        # Preserve the native caller's 2-second default and cap any higher value.
        cm = self._cooperative.foreground(wait=min(3., float(wait_seconds)))
        try:
            cm.__enter__()
        except (AdmissionBlocked, OSError) as exc:
            raise ForegroundAdmissionBusy("cross-process foreground admission unavailable") from exc
        try:
            token = super().begin_foreground(wait_seconds=wait_seconds)
            with self._cross_guard:
                if token in self._cross_by_token:
                    raise RuntimeError("duplicate native token")
                self._cross_by_token[token] = cm
            return token
        except BaseException:
            cm.__exit__(None, None, None)
            raise

    def end_foreground(self, token: str) -> None:
        # Always release the OS lease even if the native guard rejects a token.
        # But never let a duplicate/unknown release unlock someone else's lease.
        with self._cross_guard:
            cm = self._cross_by_token.get(token)
            if cm is None:
                raise RuntimeError("unknown or stale cross-process token")
            super().end_foreground(token)
            del self._cross_by_token[token]
        cm.__exit__(None, None, None)

    def status(self) -> dict:
        state = super().status()
        state.update({
            "scope": "one_gateway_plus_cooperative_http_foreground",
            "cooperative_http_foreground_lease": True,
            "cross_process_exclusivity_verified": False,
            "all_provider_paths_coordinated": False,
            "provider_quiescence_verified": False,
            "autonomous_idle_inference_authorized": False,
        })
        return state
