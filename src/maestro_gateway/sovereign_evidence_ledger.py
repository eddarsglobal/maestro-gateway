"""P0.4.1 durable, conservative and version-scoped local model evidence.

This is NOT a linguistic evaluator, not a model benchmark, and not a model loader.
Only already-governed primary mission receipts may supply operational evidence.
Data never includes prompts, answers, conversation IDs, or credentials.
The ledger never promotes a model based on HTTP 200 or model self-judgment.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile
from typing import Any, Iterator

SCHEMA = 'maestro-sovereign-qualification-1'
STATUSES = frozenset({'TECHNICAL_DELIVERED', 'CONTRACT_REJECTED',
                      'LINGUISTIC_DEFECT', 'RUNTIME_FAILURE'})
LANGUAGE = re.compile(r'^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,3}$')
TOKEN = re.compile(r'^[A-Za-z0-9_.:+@/\-]{1,160}$')
VERSION = re.compile(r'^[a-fA-F0-9]{16,128}$')
SUBTASK = re.compile(r'^[A-Za-z0-9_\-]{1,70}$')
MAX_PROFILES = 4096
MAX_IDS = 64
MAX_FILE = 2_000_000


class EvidenceUnavailable(RuntimeError):
    """No data is trusted if storage integrity/permissions cannot be established."""


def _bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True).encode('utf-8')


def _check_path(path: Path, *, must_exist: bool = False) -> None:
    if path.is_symlink():
        raise EvidenceUnavailable('symlink forbidden in qualification store')
    if must_exist and not path.is_file():
        raise EvidenceUnavailable('expected regular file missing')
    if path.exists():
        st = path.stat()
        if (st.st_uid != os.getuid() or st.st_mode & 0o077
                or not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode))
                or (not stat.S_ISDIR(st.st_mode) and st.st_nlink != 1)):
            raise EvidenceUnavailable('qualification store permissions or ownership unsafe')


# macOS publishes root-owned /var and /tmp filesystem aliases. Python's
# tempfile often returns /var/folders/... even though /var is a symlink to
# /private/var. These are the *only* symlink ancestors we tolerate: arbitrary
# user-controlled links, including links beneath those aliases, remain denied.
_DARWIN_SYSTEM_ALIASES = {'/var': '/private/var', '/tmp': '/private/tmp'}


def _trusted_darwin_system_alias(ancestor: Path) -> bool:
    if sys.platform != 'darwin':
        return False
    expected = _DARWIN_SYSTEM_ALIASES.get(str(ancestor))
    if expected is None:
        return False
    try:
        link = ancestor.lstat()
        private = Path('/private').lstat()
        target = Path(expected).lstat()
        return (stat.S_ISLNK(link.st_mode) and link.st_uid == 0
                and stat.S_ISDIR(private.st_mode) and private.st_uid == 0
                and stat.S_ISDIR(target.st_mode) and target.st_uid == 0
                and ancestor.resolve(strict=True) == Path(expected))
    except (OSError, RuntimeError):
        return False


def _safe_directory(directory: Path) -> None:
    # Deny *every* symlink ancestor except verified, root-owned macOS
    # filesystem aliases. Never trust a symlink based on its name alone.
    path = directory.expanduser().absolute()
    ancestors = [path, *path.parents]
    for ancestor in ancestors:
        if ancestor.is_symlink() and not _trusted_darwin_system_alias(ancestor):
            raise EvidenceUnavailable('symlink ancestor forbidden')
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    _check_path(path)
    if not path.is_dir():
        raise EvidenceUnavailable('qualification store is not directory')
    os.chmod(path, 0o700)


def _atomic_write(path: Path, data: bytes) -> None:
    _check_path(path)
    fd, name = tempfile.mkstemp(prefix='.p04-', dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as fp:
            fp.write(data)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(name, path)
        dfd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class SovereignEvidenceLedger:
    """Small owner-only snapshot with lock, authenticated checksum, and backup.

    HMAC catches accidental modification and changes by less-trusted local
    processes without the key. It is NOT protection from a malicious owner or
    a process with full access to the user's home directory and key file.
    """
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser() if root is not None else Path.home()/'.maestro'/'runtime'/'qualification-p04'

    @contextlib.contextmanager
    def _locked(self) -> Iterator[bytes]:
        _safe_directory(self.root)
        lockfile = self.root/'ledger.lock'
        _check_path(lockfile)
        fd = os.open(lockfile, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            _check_path(lockfile, must_exist=True)
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                keyfile = self.root/'ledger.key'
                if not keyfile.exists():
                    _atomic_write(keyfile, secrets.token_bytes(32))
                _check_path(keyfile, must_exist=True)
                key = keyfile.read_bytes()
                if len(key) != 32:
                    raise EvidenceUnavailable('invalid ledger key')
                yield key
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    @staticmethod
    def _mac(key: bytes, payload: dict) -> str:
        return hmac.new(key, _bytes(payload), hashlib.sha256).hexdigest()

    def _unpack(self, path: Path, key: bytes) -> dict:
        _check_path(path, must_exist=True)
        if path.stat().st_size > MAX_FILE:
            raise EvidenceUnavailable('qualification data exceeds cap')
        try:
            pack = json.loads(path.read_text('utf-8'))
        except (UnicodeError, ValueError, OSError) as exc:
            raise EvidenceUnavailable('qualification data unreadable') from exc
        if not isinstance(pack, dict) or set(pack) != {'payload', 'hmac'}:
            raise EvidenceUnavailable('qualification wrapper invalid')
        body = pack['payload']
        if not isinstance(body, dict) or body.get('schema') != SCHEMA or not isinstance(body.get('profiles'), dict):
            raise EvidenceUnavailable('qualification schema invalid')
        if not isinstance(pack['hmac'], str) or not hmac.compare_digest(self._mac(key, body), pack['hmac']):
            raise EvidenceUnavailable('qualification checksum invalid')
        if len(body['profiles']) > MAX_PROFILES or body.get('sequence', -1) < 0:
            raise EvidenceUnavailable('qualification bounds invalid')
        return body

    @staticmethod
    def _initial() -> dict:
        return {'schema': SCHEMA, 'sequence': 0, 'profiles': {}}

    def _load(self, key: bytes) -> tuple[dict, str]:
        state = self.root/'ledger.json'
        backup = self.root/'ledger.previous.json'
        if not state.exists():
            if backup.exists():
                return self._unpack(backup, key), 'recovered_from_backup'
            return self._initial(), 'new'
        try:
            return self._unpack(state, key), 'healthy'
        except EvidenceUnavailable:
            if backup.exists():
                return self._unpack(backup, key), 'recovered_from_backup'
            raise

    def _save(self, body: dict, key: bytes, *, recovery: bool) -> None:
        state = self.root/'ledger.json'
        backup = self.root/'ledger.previous.json'
        envelope = _bytes({'payload': body, 'hmac': self._mac(key, body)})
        if len(envelope) > MAX_FILE:
            raise EvidenceUnavailable('qualification maximum size exceeded')
        if state.exists() and recovery:
            # Preserve corrupt primary for human/Guardian forensics, never
            # silently discard unknown evidence. Only one recovered copy per
            # unique random suffix, not a fixed filename vulnerable to links.
            _check_path(state)
            corrupt = self.root/('ledger.corrupt.' + secrets.token_hex(8))
            os.replace(state, corrupt)
        elif state.exists():
            # Last verified snapshot provides a safe recovery point.
            self._unpack(state, key)
            _atomic_write(backup, state.read_bytes())
        _atomic_write(state, envelope)

    @staticmethod
    def _profile_key(resource: str, digest: str, language: str, task: str) -> str:
        if not (isinstance(resource, str) and TOKEN.fullmatch(resource)
                and isinstance(digest, str) and VERSION.fullmatch(digest)
                and isinstance(language, str) and LANGUAGE.fullmatch(language)
                and isinstance(task, str) and SUBTASK.fullmatch(task)):
            raise ValueError('unattributable evidence: resource/digest/language/task')
        # Opaque key; payload must never contain prompt or answer text.
        return hashlib.sha256(_bytes([resource, digest.lower(), language.lower(), task])).hexdigest()

    def record(self, resource: str, digest: str, language: str, task: str,
               status: str, *, receipt_id: str) -> bool:
        """Persist one attributed outcome, never a self-asserted objective win.

        Invalid/unversioned observations are refused, not downgraded into
        versionless claims. Retries of the same mission are idempotent.
        """
        if status not in STATUSES:
            raise ValueError('unsupported qualification status; no automatic OBJECTIVE_VERIFIED')
        scope = self._profile_key(resource, digest, language, task)
        if not isinstance(receipt_id, str) or not (1 <= len(receipt_id) <= 160):
            raise ValueError('invalid opaque mission receipt')
        with self._locked() as key:
            body, origin = self._load(key)
            profiles = body['profiles']
            row = profiles.get(scope)
            if row is None:
                if len(profiles) >= MAX_PROFILES:
                    raise EvidenceUnavailable('qualification profile capacity exhausted')
                row = {'resource': resource, 'digest': digest.lower(),
                       'language': language.lower(), 'task': task,
                       'counts': {kind: 0 for kind in sorted(STATUSES)},
                       'recent_receipts': [], 'defect_streak': 0,
                       'qualified': False, 'linguistic_certified': False}
            # Confidential token digest also protects against accidental ID disclosure.
            receipt = hmac.new(key, receipt_id.encode('utf-8'), hashlib.sha256).hexdigest()
            if receipt in row['recent_receipts']:
                return False
            row['recent_receipts'] = (row['recent_receipts'] + [receipt])[-MAX_IDS:]
            row['counts'][status] += 1
            if status == 'LINGUISTIC_DEFECT':
                row['defect_streak'] += 1
            elif status in {'TECHNICAL_DELIVERED', 'CONTRACT_REJECTED', 'RUNTIME_FAILURE'}:
                # Technical success/failure is neither grammatical recovery nor fluency evidence.
                pass
            body['profiles'][scope] = row
            body['sequence'] += 1
            self._save(body, key, recovery=origin == 'recovered_from_backup')
        return True

    def snapshot(self, resource: str | None = None, digest: str | None = None) -> dict:
        """Only exact digest scoped evidence; never aggregate an old model version."""
        with self._locked() as key:
            body, origin = self._load(key)
            valid = isinstance(digest, str) and VERSION.fullmatch(digest) is not None
            items = [dict(row) for row in body['profiles'].values()
                     if valid and row['digest'] == digest.lower()
                     and isinstance(resource, str) and row['resource'] == resource]
            for row in items:
                row.pop('recent_receipts', None)
            return {'schema': SCHEMA, 'storage': origin, 'sequence': body['sequence'],
                    'version_bound': bool(valid), 'profiles': items,
                    'objective_certified': False, 'fluency_certified': False}

    def overview(self) -> dict:
        """Read-only local governance summary. No raw chat or receipt hashes."""
        with self._locked() as key:
            body, origin = self._load(key)
            rows = []
            for row in body['profiles'].values():
                rows.append({k: row[k] for k in ('resource', 'digest', 'language',
                    'task', 'counts', 'defect_streak', 'qualified', 'linguistic_certified')})
            return {'schema': SCHEMA, 'storage': origin, 'sequence': body['sequence'],
                    'profiles': sorted(rows, key=lambda r:(r['resource'],r['language'],r['task'])),
                    'objective_certified': False, 'fluency_certified': False}

    def should_trial_alternative(self, resource: str, digest: str | None, language: str) -> bool:
        # Negative-only recovery signal. No route is promoted for a positive tally.
        if not isinstance(digest, str) or VERSION.fullmatch(digest) is None:
            return False
        summary = self.snapshot(resource, digest)
        return any(r['language'] == language and r['defect_streak'] >= 2
                   for r in summary['profiles'])
