"""Local cancellation registry: crash-aware statuses, not global transaction atomicity.

A2.4.1 makes request state publication durable where the filesystem supports
file and directory fsync. Recovery marks unfinished records *interrupted*, never
pretends to undo Core TX or external side effects. One Gateway process owns this
registry; cross-process locking / full transaction coordination are not provided.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

_ID = re.compile(r"^[a-f0-9]{32}$")
_TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
_STATES = _TERMINAL | frozenset({"queued", "running", "cancel_requested"})


class MissionCancelledError(RuntimeError):
    """One authenticated mission was cancelled by user request."""


class MissionConflictError(RuntimeError):
    """Request ID has already been used or a transition is invalid."""


class MissionJournalError(RuntimeError):
    """Persistent mission state cannot be trusted; fail closed."""


class MissionControl:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        if self.directory.is_symlink():
            raise MissionJournalError("Symlinked mission journal directory refused")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        self._lock = threading.RLock()
        self._active: dict[str, threading.Event] = {}
        # An abrupt process exit cannot prove rollback or Core TX completion.
        # Inspect all records before editing any: fail closed on corrupt state.
        interrupted = []
        for file in sorted(self.directory.glob("*.json")):
            if not _ID.fullmatch(file.stem) or file.is_symlink():
                raise MissionJournalError("Unexpected mission journal entry")
            try:
                record = self._read(file.stem)
            except (OSError, ValueError, TypeError) as exc:
                raise MissionJournalError("Corrupt mission journal; manual recovery required") from exc
            if record["state"] not in _TERMINAL:
                record["previous_state_before_restart"] = record["state"]
                record["state"] = "interrupted"
                record["recovered_at"] = time.time()
                record["outcome_ambiguous"] = True
                interrupted.append(record)
        # A crash between temporary-file creation and rename may leave no
        # authoritative .json yet. Retire that request ID rather than replay it.
        # Never trust or execute the contents of a partial temporary record.
        reserved = {record["request_id"] for record in interrupted}
        for temp in sorted(self.directory.glob("*.tmp")):
            matched = re.fullmatch(r"([a-f0-9]{32})\.[a-f0-9]{32}\.tmp", temp.name)
            if matched is None:
                continue
            if temp.is_symlink():
                raise MissionJournalError("Symlinked orphan mission journal refused")
            ident = matched.group(1)
            if ident not in reserved and not self._path(ident).exists():
                interrupted.append({
                    "request_id": ident,
                    "state": "interrupted",
                    "recovered_at": time.time(),
                    "previous_state_before_restart": "unconfirmed_temporary_write",
                    "outcome_ambiguous": True,
                })
                reserved.add(ident)
        for record in interrupted:
            self._store(record)

    @staticmethod
    def validate(request_id: str) -> str:
        if not isinstance(request_id, str) or not _ID.fullmatch(request_id):
            raise ValueError("request_id must be a lowercase UUID4 hex string")
        return request_id

    def _path(self, request_id: str) -> Path:
        return self.directory / (self.validate(request_id) + ".json")

    def _read(self, request_id: str) -> dict:
        path = self._path(request_id)
        if path.is_symlink():
            raise MissionJournalError("Symlinked mission record refused")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise KeyError(request_id) from exc
        except (OSError, ValueError, UnicodeError) as exc:
            raise MissionJournalError("Unreadable mission record") from exc
        if (not isinstance(data, dict) or data.get("request_id") != request_id
                or data.get("state") not in _STATES):
            raise MissionJournalError("Corrupt mission record fields")
        return data

    def _store(self, record: dict) -> None:
        request_id = self.validate(record["request_id"])
        if record.get("state") not in _STATES:
            raise MissionJournalError("Invalid mission record state")
        path = self._path(request_id)
        if path.is_symlink():
            raise MissionJournalError("Symlinked mission record refused")
        tmp = self.directory / (request_id + "." + uuid.uuid4().hex + ".tmp")
        fd = None
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = None
                json.dump(record, handle, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            # Sync the rename itself, not just the temporary file contents.
            dir_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if fd is not None:
                os.close(fd)
            tmp.unlink(missing_ok=True)

    def register(self, request_id: str) -> threading.Event:
        request_id = self.validate(request_id)
        with self._lock:
            if self._path(request_id).exists() or request_id in self._active:
                raise MissionConflictError("request_id already recorded; cannot replay")
            event = threading.Event()
            # Never publish a runnable in-memory mission before its durable ID.
            self._store({"request_id": request_id, "state": "queued", "created_at": time.time()})
            self._active[request_id] = event
            return event

    def start(self, request_id: str) -> None:
        with self._lock:
            event = self._active[request_id]
            if event.is_set():
                raise MissionCancelledError("Mission cancelled while queued")
            record = self.status(request_id)
            if record["state"] != "queued":
                raise MissionConflictError("Mission cannot be restarted")
            record["state"] = "running"
            self._store(record)

    def cancel(self, request_id: str) -> dict:
        with self._lock:
            record = self.status(request_id)
            event = self._active.get(request_id)
            if event is not None and record["state"] not in _TERMINAL:
                # Abort the ongoing operation *before* potentially slow I/O.
                event.set()
                record["state"] = "cancel_requested"
                self._store(record)
            return record

    def finish(self, request_id: str, *, success: bool, cancelled: bool = False,
               durable_committed: bool | None = None) -> dict:
        with self._lock:
            record = self.status(request_id)
            if record["state"] in _TERMINAL:
                return record  # Terminal state is immutable / no resurrection.
            event = self._active.get(request_id)
            if event is None:
                raise MissionConflictError("No active mission to finish")
            requested = event.is_set() or cancelled
            if requested and success and durable_committed is True:
                # Commit already observed: cancellation was too late to undo TX.
                record["state"] = "completed"
                record["late_cancel_requested"] = True
                record["durable_commit_observed"] = True
            elif requested and success:
                # Ambiguous outcome: cannot claim commit or rollback.
                record["state"] = "interrupted"
                record["outcome_ambiguous"] = True
            elif requested:
                record["state"] = "cancelled"
            elif success and durable_committed is False:
                record["state"] = "interrupted"
                record["outcome_ambiguous"] = True
            else:
                record["state"] = "completed" if success else "failed"
                if success and durable_committed is True:
                    record["durable_commit_observed"] = True
            record["finished_at"] = time.time()
            self._store(record)
            # If durable journal publication fails, keep the active token.
            self._active.pop(request_id, None)
            return record

    def status(self, request_id: str) -> dict:
        with self._lock:
            return self._read(request_id)
