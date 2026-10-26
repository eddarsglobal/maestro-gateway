"""G1-P0-A2.4.2.2c1 — bounded, fail-closed DIRECT effect observations.

Local same-host durable audit trail, NOT a transaction coordinator. Only enabled
while a stable request ID owns its existing MissionRequestJournal lease.
Effects may have happened even when a process dies before its receipt is updated.
All state changes are single-file fsync+rename records; no automatic replay.
"""
from __future__ import annotations

from contextvars import ContextVar
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import re
from typing import Any, Iterator

from .request_journal import (_atomic_write, _no_follow_read, _verify_root,
                              fingerprint, RequestReplayBlocked, MissionRequestJournal)

SCHEMA = 'MAESTRO_G1_A2422C1_EFFECTS_V1'
_SHA = re.compile(r'^[0-9a-f]{64}$')
_ID = re.compile(r'^[A-Za-z0-9_.:-]{1,128}$')
MAX_EFFECTS = 24
_CURRENT: ContextVar['EffectJournal | None'] = ContextVar('maestro_g1_effect_journal', default=None)


class EffectReceiptBlocked(RequestReplayBlocked):
    """Missing, ambiguous, mismatched or altered effect journal."""


def current_effect_journal() -> 'EffectJournal | None':
    return _CURRENT.get()


@contextmanager
def effect_scope(journal: 'EffectJournal') -> Iterator[None]:
    marker = _CURRENT.set(journal)
    try:
        yield
    finally:
        _CURRENT.reset(marker)


def _check(doc: Any, request_hash: str, signature: str) -> dict[str, Any]:
    if not isinstance(doc, dict) or doc.get('schema') != SCHEMA:
        raise EffectReceiptBlocked('Wrong effect journal schema')
    if doc.get('request_hash') != request_hash or doc.get('request_fingerprint') != signature:
        raise EffectReceiptBlocked('Effect journal request identity mismatch')
    checksum = doc.get('checksum')
    if not isinstance(checksum, str) or not _SHA.fullmatch(checksum):
        raise EffectReceiptBlocked('Missing effect journal checksum')
    if fingerprint({k: v for k, v in doc.items() if k != 'checksum'}) != checksum:
        raise EffectReceiptBlocked('Tampered effect journal')
    if doc.get('state') not in ('ACTIVE', 'RETURNED'):
        raise EffectReceiptBlocked('Unknown effect journal state')
    if not isinstance(doc.get('effects'), list) or len(doc['effects']) > MAX_EFFECTS:
        raise EffectReceiptBlocked('Invalid effect list')
    if not isinstance(doc.get('task_id'), (str, type(None))):
        raise EffectReceiptBlocked('Invalid task identity')
    for i, row in enumerate(doc['effects']):
        if not isinstance(row, dict) or row.get('ordinal') != i+1:
            raise EffectReceiptBlocked('Invalid effect sequence')
        if row.get('domain') not in ('CORE', 'WORKSPACE') or not isinstance(row.get('action'), str):
            raise EffectReceiptBlocked('Invalid effect operation')
        if row.get('state') not in ('PREPARED', 'ENGAGED', 'OBSERVED'):
            raise EffectReceiptBlocked('Invalid effect state')
        if not isinstance(row.get('input_sha256'), str) or not _SHA.fullmatch(row['input_sha256']):
            raise EffectReceiptBlocked('Invalid effect fingerprint')
        if row['state'] == 'OBSERVED' and (not isinstance(row.get('outcome_sha256'), str)
                                            or not _SHA.fullmatch(row['outcome_sha256'])):
            raise EffectReceiptBlocked('Observed effect missing output hash')
    if doc['state'] == 'RETURNED':
        if not all(row['state'] == 'OBSERVED' for row in doc['effects']):
            raise EffectReceiptBlocked('Returned journal contains unresolved effects')
        if not isinstance(doc.get('task_id'), str) or not _ID.fullmatch(doc['task_id']):
            raise EffectReceiptBlocked('Returned journal missing task identity')
    return doc


class EffectJournal:
    """Single-writer: caller MUST hold its matching MissionRequestJournal lease.

    RequestLease serializes concurrent duplicate requests. The effect journal is
    NOT independently safe for arbitrarily concurrent writers using one request.
    """
    def __init__(self, conversation_root: Path, request_id: str, signature: str):
        self.request_hash = MissionRequestJournal._request_hash(request_id)
        if not isinstance(signature, str) or not _SHA.fullmatch(signature):
            raise ValueError('Invalid effect request fingerprint')
        self.signature = signature
        root = Path(conversation_root)
        if root.is_symlink() or not root.is_dir():
            raise EffectReceiptBlocked('Invalid effect journal parent')
        self.root = root / '.mission_effects'
        if self.root.is_symlink():
            raise EffectReceiptBlocked('Symlinked effect directory')
        self.root.mkdir(mode=0o700, exist_ok=True)
        _verify_root(self.root)
        self.path = self.root / (self.request_hash + '.json')

    def _read(self) -> dict[str, Any]:
        try:
            doc = json.loads(_no_follow_read(self.path))
        except (OSError, ValueError, UnicodeError) as exc:
            raise EffectReceiptBlocked('Missing or unreadable effect journal') from exc
        return _check(doc, self.request_hash, self.signature)

    def _write(self, doc: dict[str, Any]) -> str:
        raw = {k: v for k, v in doc.items() if k != 'checksum'}
        doc = {**raw, 'checksum': fingerprint(raw)}
        _check(doc, self.request_hash, self.signature)
        _atomic_write(self.path, doc)
        return doc['checksum']

    def start(self) -> None:
        # NO RESET: even a partial/tombstoned request cannot be initialized twice.
        if self.path.exists() or self.path.is_symlink():
            raise EffectReceiptBlocked('Existing effect journal cannot be reinitialized')
        self._write(dict(schema=SCHEMA, request_hash=self.request_hash,
                         request_fingerprint=self.signature, task_id=None,
                         state='ACTIVE', effects=[]))

    def bind_task(self, task_id: str) -> None:
        if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
            raise EffectReceiptBlocked('Invalid Core task identity')
        doc = self._read()
        if doc['state'] != 'ACTIVE' or (doc['task_id'] is not None and doc['task_id'] != task_id):
            raise EffectReceiptBlocked('Conflicting Core task identity')
        doc['task_id'] = task_id
        self._write(doc)

    def prepare(self, domain: str, action: str, arguments: dict[str, Any]) -> int:
        if domain not in ('CORE', 'WORKSPACE') or not isinstance(action, str) or not _ID.fullmatch(action):
            raise ValueError('Invalid effect identity')
        doc = self._read()
        if doc['state'] != 'ACTIVE' or not doc['task_id']:
            raise EffectReceiptBlocked('Effect cannot start before Core task identity')
        if len(doc['effects']) >= MAX_EFFECTS:
            raise EffectReceiptBlocked('Too many effects in one mission')
        number = len(doc['effects']) + 1
        doc['effects'].append(dict(ordinal=number, domain=domain, action=action,
                                   input_sha256=fingerprint(arguments), state='PREPARED'))
        self._write(doc)
        return number

    def engage(self, ordinal: int) -> None:
        doc = self._read()
        row = self._pending(doc, ordinal, 'PREPARED')
        row['state'] = 'ENGAGED'
        self._write(doc)  # durable BEFORE invoking any external operation

    def observe(self, ordinal: int, outcome: dict[str, Any]) -> None:
        doc = self._read()
        row = self._pending(doc, ordinal, 'ENGAGED')
        row['state'] = 'OBSERVED'
        row['outcome_sha256'] = fingerprint(outcome)
        self._write(doc)

    @staticmethod
    def _pending(doc: dict[str, Any], ordinal: int, expected: str) -> dict[str, Any]:
        if doc['state'] != 'ACTIVE' or not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 1 or ordinal > len(doc['effects']):
            raise EffectReceiptBlocked('Invalid effect transition')
        row = doc['effects'][ordinal-1]
        if row['state'] != expected:
            raise EffectReceiptBlocked('Effect transition out of order')
        return row

    def returned(self, task_id: str) -> str:
        """Mark successful runner return, not an independently certified side effect."""
        doc = self._read()
        if doc['state'] != 'ACTIVE' or not isinstance(task_id, str):
            raise EffectReceiptBlocked('Invalid completed effect journal')
        if doc['task_id'] is None:
            doc['task_id'] = task_id  # for bounded fake-Core integration tests
        if doc['task_id'] != task_id:
            raise EffectReceiptBlocked('Runner returned different task identity')
        if any(row['state'] != 'OBSERVED' for row in doc['effects']):
            raise EffectReceiptBlocked('Outstanding effects require manual reconciliation')
        doc['state'] = 'RETURNED'
        return self._write(doc)

    def verify_returned(self, checksum: str, task_id: str) -> None:
        if not isinstance(checksum, str) or not _SHA.fullmatch(checksum):
            raise EffectReceiptBlocked('Missing completed effect proof')
        doc = self._read()
        if doc['state'] != 'RETURNED' or doc['task_id'] != task_id or doc['checksum'] != checksum:
            raise EffectReceiptBlocked('Effect journal not correlated with completed request')

    def inspect(self) -> dict[str, Any]:
        doc = self._read()
        return dict(state=doc['state'], task_id=doc['task_id'],
                    effects=[dict(row) for row in doc['effects']], checksum=doc['checksum'])
