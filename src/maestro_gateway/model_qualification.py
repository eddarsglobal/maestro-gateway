"""Provider-neutral, evidence-driven routing qualifications.

Only authenticated Gateway mission outcomes are evidence. Model names, sizes,
provider brands and unmeasured descriptor quality scores are NEVER proxies for
language competence or observed response time.

Warm from local (private) mission metadata on boot. The conversation hot path
reads a copy-on-write in-memory snapshot: no disk, HTTP, benchmarks or model
inference during the routing decision. This is routing evidence, NOT proof of
semantic truth; human benchmarks are still required for production quality.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
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
        # Gateway response-guard accepted != human-confirmed semantic accuracy.
        # This only qualifies a model for a bounded operational trial.
        return self.accepted >= 2 and self.attempts >= 2 and self.accepted / self.attempts >= .75


_LOCK = threading.Lock()
# Indexed by (resource ID, task class, language code).
_SAMPLES: dict[tuple[str, str, str], tuple[tuple[str, float], ...]] = {}
_SNAPSHOT: dict[tuple[str, str, str], Qualification] = {}
_EMPTY_QUALIFICATION = Qualification()  # immutable; safe to share on empty lookups
_MAX_PER_KEY = 64


def _valid_sample(resource_id: Any, task: Any, language: Any,
                  outcome: Any, wall_ms: Any) -> tuple[tuple[str, str, str], tuple[str, float]] | None:
    if not (isinstance(resource_id, str) and resource_id and len(resource_id) <= 256):
        return None
    if task not in {'quick', 'normal', 'code', 'complex'}:
        return None
    if language not in {'fr', 'en', 'hr', 'de', 'it', 'es', 'pt', 'ar'}:
        return None
    if outcome not in {'quality_accepted', 'quality_rejected', 'timeout', 'transport_failure'}:
        return None
    if (not isinstance(wall_ms, (int, float)) or isinstance(wall_ms, bool)
            or not math.isfinite(wall_ms) or not 0 < wall_ms <= 120_000):
        return None
    return (resource_id, task, language), (outcome, float(wall_ms))


def _summarize(rows: tuple[tuple[str, float], ...]) -> Qualification:
    successful = sorted(ms for kind, ms in rows if kind == 'quality_accepted')
    return Qualification(
        attempts=len(rows), accepted=len(successful),
        rejected=sum(kind == 'quality_rejected' for kind, _ in rows),
        timed_out=sum(kind == 'timeout' for kind, _ in rows),
        median_ms=statistics.median(successful) if successful else None,
        p95_ms=successful[max(0, math.ceil(len(successful) * .95) - 1)] if successful else None,
    )


def _append(key: tuple[str, str, str], sample: tuple[str, float]) -> None:
    global _SNAPSHOT
    rows = (_SAMPLES.get(key, ()) + (sample,))[-_MAX_PER_KEY:]
    _SAMPLES[key] = rows
    # Copy on write. Readers on the routing hot path don't acquire a lock.
    _SNAPSHOT = {**_SNAPSHOT, key: _summarize(rows)}


def observe_attempt(resource_id: str, task: str, language: str,
                    outcome: str, wall_ms: float) -> bool:
    normalized = _valid_sample(resource_id, task, language, outcome, wall_ms)
    if normalized is None:
        return False
    with _LOCK:
        _append(*normalized)
    return True


def qualification(resource_id: str, task: str, language: str) -> Qualification:
    return _SNAPSHOT.get((resource_id, task, language), _EMPTY_QUALIFICATION)


def warm_qualifications(root: Path, limit: int = 100) -> int:
    """Load ONLY outcome metadata. Historic entries lacking provenance are ignored.

    Files are read on a background boot worker; never from the hot route.
    """
    global _SAMPLES, _SNAPSHOT
    loaded: dict[tuple[str, str, str], tuple[tuple[str, float], ...]] = {}
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
                for attempt in metrics.get('model_attempt_metrics') or []:
                    if not isinstance(attempt, dict):
                        continue
                    validated = _valid_sample(
                        attempt.get('resource_id'), metrics.get('qualification_task', metrics.get('routing_class')),
                        metrics.get('routing_language'), attempt.get('outcome'),
                        attempt.get('wall_ms'))
                    if validated is not None:
                        key, sample = validated
                        loaded[key] = (loaded.get(key, ()) + (sample,))[-_MAX_PER_KEY:]
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    with _LOCK:
        # Preserve observations made during background load, without
        # reimporting duplicates from historical files indefinitely.
        for key, rows in _SAMPLES.items():
            existing = loaded.get(key, ())
            loaded[key] = (existing + rows)[-_MAX_PER_KEY:]
        _SAMPLES = loaded
        _SNAPSHOT = {key: _summarize(rows) for key, rows in loaded.items()}
    return sum(len(rows) for rows in loaded.values())


def reset_for_testing() -> None:
    global _SAMPLES, _SNAPSHOT
    with _LOCK:
        _SAMPLES = {}
        _SNAPSHOT = {}
