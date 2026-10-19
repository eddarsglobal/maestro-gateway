"""G1-P0-A2.4.2.2b1: fail-closed request journal for opt-in DIRECT missions.

The gateway's HTTP ingress does NOT yet supply stable request IDs; therefore
this module is not an HTTP idempotency solution and is not a Core effect ledger.
Durability assumes a local fsync-capable filesystem; network filesystems and
separate Gateway hosts are not certified. All uncertain outcomes block replay.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid
from datetime import datetime, timezone
from typing import Any

SCHEMA = 'MAESTRO_G1_A2422B1_REQUEST_V1'
_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{3,127}$')
MAX_BYTES = 2_000_000


class RequestReplayBlocked(RuntimeError):
    """Recorded operation is ambiguous, conflicting, or concurrently active."""


def canonical(data: Any) -> bytes:
    try:
        out = json.dumps(data, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('Request journal only accepts finite JSON values') from exc
    if len(out) > MAX_BYTES:
        raise ValueError('Request record exceeds maximum size')
    return out


def fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(payload)).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _verify_root(root: Path) -> None:
    if root.is_symlink():
        raise RuntimeError('Symlinked request journal directory refused')
    if not root.is_dir():
        raise RuntimeError('Request journal is not a directory')
    mode = stat.S_IMODE(root.stat().st_mode)
    if mode & 0o077:
        raise RuntimeError('Request journal directory permits group/other access')


def _no_follow_read(path: Path) -> bytes:
    if path.is_symlink():
        raise RuntimeError('Symlinked request receipt refused')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (stat.S_IMODE(info.st_mode) & 0o077):
            raise RuntimeError('Insecure request receipt permissions/type')
        with os.fdopen(fd, 'rb', closefd=False) as handle:
            data = handle.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise RuntimeError('Oversized request receipt')
        return data
    finally:
        os.close(fd)


def _atomic_write(path: Path, record: dict[str, Any]) -> None:
    root = path.parent
    _verify_root(root)
    if path.is_symlink():
        raise RuntimeError('Symlinked request receipt refused')
    data = canonical(record) + b'\n'
    tmp = root / ('.pending-' + uuid.uuid4().hex)
    try:
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        dirfd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        tmp.unlink(missing_ok=True)


def _make_record(request_hash: str, request_fingerprint: str, state: str,
                 *, result: dict[str, Any] | None = None,
                 detail: str | None = None) -> dict[str, Any]:
    doc = {'schema': SCHEMA, 'request_hash': request_hash,
           'request_fingerprint': request_fingerprint, 'state': state,
           'updated_at': _now()}
    if result is not None:
        doc['result'] = result
    if detail is not None:
        doc['detail'] = detail
    doc['checksum'] = fingerprint(doc)
    return doc


def _check_record(doc: Any, request_hash: str, request_fingerprint: str) -> dict[str, Any]:
    if not isinstance(doc, dict) or doc.get('schema') != SCHEMA or doc.get('request_hash') != request_hash:
        raise RequestReplayBlocked('Invalid persisted request receipt')
    checksum = doc.get('checksum')
    bare = {k: v for k, v in doc.items() if k != 'checksum'}
    if not isinstance(checksum, str) or not re.fullmatch('[0-9a-f]{64}', checksum) or fingerprint(bare) != checksum:
        raise RequestReplayBlocked('Corrupted request receipt checksum')
    if doc.get('request_fingerprint') != request_fingerprint:
        raise RequestReplayBlocked('Same request ID has different request payload')
    if doc.get('state') not in ('STARTED_UNCERTAIN', 'COMPLETED', 'RECONCILIATION_REQUIRED'):
        raise RequestReplayBlocked('Unrecognized persisted request state')
    if doc['state'] == 'COMPLETED':
        result = doc.get('result')
        if not isinstance(result, dict) or not isinstance(result.get('task_id'), str) or not isinstance(result.get('conversation_id'), str):
            raise RequestReplayBlocked('Incomplete completed-request receipt')
    return doc


class RequestLease:
    def __init__(self, root: Path, name: str, request_hash: str, request_fingerprint: str):
        self.root = root
        self.path = root / (name + '.json')
        self.lock_path = root / (name + '.lock')
        self.request_hash = request_hash
        self.request_fingerprint = request_fingerprint
        self.fd: int | None = None
        self.existing: dict[str, Any] | None = None
        self._closed = False

    def __enter__(self) -> 'RequestLease':
        _verify_root(self.root)
        if self.lock_path.is_symlink():
            raise RequestReplayBlocked('Symlinked request lock refused')
        # A lock file is a durable tombstone: missing receipt + prior lock
        # means uncertain history, never a fresh, safe-to-execute request.
        flags = os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
        try:
            self.fd = os.open(self.lock_path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            lock_created = True
        except FileExistsError:
            self.fd = os.open(self.lock_path, flags)
            lock_created = False
        try:
            st = os.fstat(self.fd)
            if not stat.S_ISREG(st.st_mode) or (stat.S_IMODE(st.st_mode) & 0o077):
                raise RequestReplayBlocked('Insecure request lock')
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RequestReplayBlocked('Identical request already active') from exc
            try:
                doc = json.loads(_no_follow_read(self.path))
            except FileNotFoundError:
                if not lock_created:
                    raise RequestReplayBlocked('Receipt missing for existing request lock; no replay')
                _atomic_write(self.path, _make_record(self.request_hash, self.request_fingerprint,
                                                     'STARTED_UNCERTAIN'))
            except (ValueError, UnicodeError) as exc:
                raise RequestReplayBlocked('Malformed request receipt') from exc
            else:
                self.existing = _check_record(doc, self.request_hash, self.request_fingerprint)
            return self
        except BaseException:
            self.close()
            raise

    def _transition(self, state: str, *, result: dict[str, Any] | None = None,
                    detail: str | None = None) -> None:
        if self._closed or self.fd is None or self.existing is not None:
            raise RuntimeError('Cannot mutate a closed or existing request lease')
        old = _check_record(json.loads(_no_follow_read(self.path)),
                            self.request_hash, self.request_fingerprint)
        if old['state'] != 'STARTED_UNCERTAIN':
            raise RequestReplayBlocked('Request state changed unexpectedly')
        _atomic_write(self.path, _make_record(self.request_hash, self.request_fingerprint,
                                             state, result=result, detail=detail))

    def complete(self, result: dict[str, Any]) -> None:
        if not isinstance(result, dict):
            raise ValueError('Completed result must be a JSON object')
        self._transition('COMPLETED', result=result)

    def uncertain(self, detail: str) -> None:
        if not re.fullmatch('[A-Za-z0-9_.:-]{1,90}', detail or ''):
            raise ValueError('Invalid diagnostic code')
        self._transition('RECONCILIATION_REQUIRED', detail=detail)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None

    def __exit__(self, *_args: object) -> None:
        self.close()


class MissionRequestJournal:
    def __init__(self, conversation_root: Path):
        chat_root = Path(conversation_root).expanduser()
        if chat_root.is_symlink() or not chat_root.is_dir():
            raise RuntimeError('Unsafe conversation root for request journal')
        self.root = chat_root / '.mission_requests'
        if self.root.is_symlink():
            raise RuntimeError('Symlinked request journal refused')
        if not self.root.exists():
            self.root.mkdir(mode=0o700)
            # Parent directory fsync makes the new journal directory durable.
            fd = os.open(chat_root, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        _verify_root(self.root)

    @staticmethod
    def _request_hash(request_id: str) -> str:
        if not isinstance(request_id, str) or not _ID.fullmatch(request_id):
            raise ValueError('Request ID must be 4..128 safe characters')
        return hashlib.sha256(request_id.encode('utf-8')).hexdigest()

    def open(self, request_id: str, request_fingerprint: str) -> RequestLease:
        name = self._request_hash(request_id)
        if not isinstance(request_fingerprint, str) or not re.fullmatch('[a-f0-9]{64}', request_fingerprint):
            raise ValueError('Invalid request fingerprint')
        return RequestLease(self.root, name, name, request_fingerprint)

    def inspect(self, request_id: str, request_fingerprint: str) -> dict[str, Any]:
        # Read-only diagnostic; not permission to execute or replay anything.
        name = self._request_hash(request_id)
        _verify_root(self.root)
        try:
            doc = json.loads(_no_follow_read(self.root / (name + '.json')))
        except FileNotFoundError:
            return {'state': 'ABSENT'}
        except (ValueError, UnicodeError) as exc:
            raise RequestReplayBlocked('Malformed request receipt') from exc
        state = _check_record(doc, name, request_fingerprint)
        return {'state': state['state'], 'updated_at': state['updated_at'],
                'task_id': state.get('result', {}).get('task_id'),
                'conversation_id': state.get('result', {}).get('conversation_id')}
