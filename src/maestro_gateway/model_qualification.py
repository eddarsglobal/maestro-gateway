"""MAESTRO: immutable-digest-scoped operational qualification (NOT semantic proof).

Only verified current local-catalogue SHA-256 digests unlock historical and
live response-guard observations. Missing/legacy versions fail closed; synthetic
microprobes are never promoted. Cache reads are atomic and never perform I/O.
A catalogue digest is not execution-time provider attestation: provider-side
silent model swaps between catalogue refreshes remain a documented limitation.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import statistics
import threading
from typing import Any

@dataclass(frozen=True)
class Qualification:
    attempts: int = 0
    accepted: int = 0
    rejected: int = 0
    timed_out: int = 0
    p95_ms: float | None = None
    median_ms: float | None = None

    @property
    def qualified(self) -> bool:
        # Response Guard acceptance is operational, never factual certification.
        return self.accepted >= 2 and self.attempts >= 2 and self.accepted / self.attempts >= .75

_LOCK = threading.Lock()
# Indexed by (resource ID, verified SHA256 digest, task class, language).
_SAMPLES: dict[tuple[str, str, str, str], tuple[tuple[str, float], ...]] = {}
_SNAPSHOT: dict[tuple[str, str, str, str], Qualification] = {}
_EMPTY_QUALIFICATION = Qualification()
_MAX_PER_KEY = 64
# Immutable compound published snapshot: catalogue identities + qualifications
# + samples. All hot readers take ONE pointer, preventing torn version switches.
_STATE: tuple[dict[str, str], dict[tuple[str, str, str, str], Qualification],
              dict[tuple[str, str, str, str], tuple[tuple[str, float], ...]]] = ({}, {}, {})
_DIGEST = re.compile(r'(?:sha256:)?([0-9a-f]{64})\Z')


def _digest(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _DIGEST.fullmatch(value)
    return match.group(1) if match else None


def update_verified_versions(fingerprints: dict[str, str]) -> int:
    """Publish only catalogue-provided cryptographic model digests, fail closed.

    Call while holding the Core registry mutation lock after a live catalogue
    refresh. An absent digest revokes eligibility immediately, never aliases IDs.
    """
    global _STATE
    if not isinstance(fingerprints, dict):
        raise TypeError('Fingerprints must be a dict')
    clean = {rid: digest for rid, raw in fingerprints.items()
             if isinstance(rid, str) and 1 <= len(rid) <= 256
             if (digest := _digest(raw)) is not None}
    with _LOCK:
        _STATE = (clean, _STATE[1], _STATE[2])
    return len(clean)


def verified_version(resource_id: str) -> str | None:
    return _STATE[0].get(resource_id)


def _valid_sample(resource_id: Any, task: Any, language: Any,
                  outcome: Any, wall_ms: Any, fingerprint: Any = None):
    digest = _digest(fingerprint)
    if not isinstance(resource_id, str) or not 1 <= len(resource_id) <= 256 or digest is None:
        return None
    if task not in {'quick', 'normal', 'code', 'complex'}:
        return None
    if language not in {'fr', 'en', 'hr', 'de', 'it', 'es', 'pt', 'ar'}:
        return None
    if outcome not in {'quality_accepted', 'quality_rejected', 'timeout', 'transport_failure'}:
        return None
    if not isinstance(wall_ms, (int, float)) or isinstance(wall_ms, bool) or not math.isfinite(wall_ms) or not 0 < wall_ms <= 120_000:
        return None
    return (resource_id, digest, task, language), (outcome, float(wall_ms))


def _summarize(rows: tuple[tuple[str, float], ...]) -> Qualification:
    successful = sorted(ms for kind, ms in rows if kind == 'quality_accepted')
    return Qualification(
        attempts=len(rows), accepted=len(successful),
        rejected=sum(kind == 'quality_rejected' for kind, _ in rows),
        timed_out=sum(kind == 'timeout' for kind, _ in rows),
        median_ms=statistics.median(successful) if successful else None,
        p95_ms=successful[max(0, math.ceil(len(successful) * .95) - 1)] if successful else None,
    )


def observe_attempt(resource_id: str, task: str, language: str,
                    outcome: str, wall_ms: float, *, model_fingerprint: str | None = None) -> bool:
    """Count only a tagged attempt matching the currently verified catalogue.

    Under a refresh race the old attempt is retained only as private mission
    history, not promoted into current-model competence.
    """
    global _STATE, _SAMPLES, _SNAPSHOT
    normalized = _valid_sample(resource_id, task, language, outcome, wall_ms, model_fingerprint)
    if normalized is None:
        return False
    key, sample = normalized
    with _LOCK:
        versions, quals, history = _STATE
        if versions.get(resource_id) != key[1]:
            return False
        rows = (history.get(key, ()) + (sample,))[-_MAX_PER_KEY:]
        _SAMPLES = {**history, key: rows}
        _SNAPSHOT = {**quals, key: _summarize(rows)}
        _STATE = (versions, _SNAPSHOT, _SAMPLES)
    return True


def qualification(resource_id: str, task: str, language: str) -> Qualification:
    versions, snapshot, _samples = _STATE
    digest = versions.get(resource_id)
    return snapshot.get((resource_id, digest, task, language), _EMPTY_QUALIFICATION) if digest else _EMPTY_QUALIFICATION


def task_qualification(resource_id: str, task: str) -> Qualification:
    versions, _snapshot, samples = _STATE
    digest = versions.get(resource_id)
    if digest is None:
        return _EMPTY_QUALIFICATION
    rows: tuple[tuple[str, float], ...] = ()
    for (rid, fp, observed_task, _language), evidence in samples.items():
        if rid == resource_id and fp == digest and observed_task == task:
            rows += evidence
    return _summarize(rows[-_MAX_PER_KEY:]) if rows else _EMPTY_QUALIFICATION


def warm_qualifications(root: Path, limit: int = 100) -> int:
    """Read only version-bound private metadata; legacy unversioned is ignored.

    Loading before catalogue discovery is safe. Samples remain in memory by
    version and become readable only when that digest is verified as current.
    """
    global _STATE, _SAMPLES, _SNAPSHOT
    loaded: dict[tuple[str, str, str, str], tuple[tuple[str, float], ...]] = {}
    try:
        files = sorted(root.glob('CHAT-*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    except (OSError, ValueError):
        return 0
    for path in reversed(files):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            for message in (record.get('messages') or []):
                if not isinstance(message, dict) or message.get('role') not in {'assistant', 'system'}:
                    continue
                metrics = ((message.get('metadata') or {}).get('mission') or {}).get('metrics') or {}
                if not isinstance(metrics, dict):
                    continue
                for attempt in metrics.get('model_attempt_metrics') or []:
                    if not isinstance(attempt, dict):
                        continue
                    if (str(metrics.get('quality_reason') or '').strip().casefold() == 'generation_token_limit'
                            and attempt.get('outcome') == 'quality_rejected'):
                        continue
                    validated = _valid_sample(attempt.get('resource_id'),
                        metrics.get('qualification_task', metrics.get('routing_class')),
                        metrics.get('routing_language'), attempt.get('outcome'),
                        attempt.get('wall_ms'), attempt.get('model_fingerprint'))
                    if validated is not None:
                        key, sample = validated
                        loaded[key] = (loaded.get(key, ()) + (sample,))[-_MAX_PER_KEY:]
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    with _LOCK:
        versions, _snapshot, existing = _STATE
        for key, rows in existing.items():
            loaded[key] = (loaded.get(key, ()) + rows)[-_MAX_PER_KEY:]
        _SAMPLES = loaded
        _SNAPSHOT = {key: _summarize(rows) for key, rows in loaded.items()}
        _STATE = (versions, _SNAPSHOT, _SAMPLES)
    return sum(len(rows) for rows in loaded.values())


def reset_for_testing() -> None:
    global _STATE, _SAMPLES, _SNAPSHOT
    with _LOCK:
        _SAMPLES = {}
        _SNAPSHOT = {}
        _STATE = ({}, {}, {})
