"""Local, authenticated mission cancellation registry (G1 partial implementation).

Cancel requests interrupt cooperative Ollama I/O. The global governed Core
mission lock is deliberately preserved until safe runtime isolation is proven.
No claims of parallel mission execution or complete cancellation are made.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

_ID = re.compile(r"^[a-f0-9]{32}$")
_TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


class MissionCancelledError(RuntimeError):
    """One authenticated mission was cancelled by user request."""


class MissionConflictError(RuntimeError):
    """Request ID has already been used."""


class MissionControl:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        self._lock = threading.RLock()
        self._active: dict[str, threading.Event] = {}
        # Any running state left by a killed Gateway must not appear active.
        for file in self.directory.glob("*.json"):
            try:
                record = json.loads(file.read_text(encoding="utf-8"))
                if record.get("state") in {"queued", "running", "cancel_requested"}:
                    record["state"] = "interrupted"
                    self._store(record)
            except (OSError, ValueError, TypeError):
                continue

    @staticmethod
    def validate(request_id: str) -> str:
        if not isinstance(request_id, str) or not _ID.fullmatch(request_id):
            raise ValueError("request_id must be a lowercase UUID4 hex string")
        return request_id

    def _path(self, request_id: str) -> Path:
        return self.directory / (self.validate(request_id) + ".json")

    def _store(self, record: dict) -> None:
        import uuid
        path = self._path(record["request_id"])
        tmp = self.directory / (record["request_id"] + "." + uuid.uuid4().hex + ".tmp")
        try:
            tmp.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
            tmp.chmod(0o600)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)

    def register(self, request_id: str) -> threading.Event:
        request_id = self.validate(request_id)
        with self._lock:
            if self._path(request_id).exists() or request_id in self._active:
                raise MissionConflictError("request_id already recorded; cannot replay")
            event = threading.Event()
            self._active[request_id] = event
            self._store({"request_id": request_id, "state": "queued", "created_at": time.time()})
            return event

    def start(self, request_id: str) -> None:
        with self._lock:
            event = self._active[request_id]
            if event.is_set():
                raise MissionCancelledError("Mission cancelled while queued")
            record = self.status(request_id)
            record["state"] = "running"
            self._store(record)

    def cancel(self, request_id: str) -> dict:
        with self._lock:
            record = self.status(request_id)
            event = self._active.get(request_id)
            if event is not None and record["state"] not in _TERMINAL:
                event.set()
                record["state"] = "cancel_requested"
                self._store(record)
            return record

    def finish(self, request_id: str, *, success: bool, cancelled: bool = False) -> dict:
        with self._lock:
            record = self.status(request_id)
            event = self._active.pop(request_id, None)
            if event is not None and event.is_set() and success:
                # A successful Core TX may have committed before cancellation arrived.
                # Never rewrite a committed mission's true outcome as cancelled.
                record["late_cancel_requested"] = True
                cancelled = False
            elif event is not None and event.is_set():
                cancelled = True
            record["state"] = "cancelled" if cancelled else ("completed" if success else "failed")
            record["finished_at"] = time.time()
            self._store(record)
            return record

    def status(self, request_id: str) -> dict:
        path = self._path(request_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise KeyError(request_id) from exc
        return data
