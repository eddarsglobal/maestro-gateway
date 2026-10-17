"""Version-bound verified semantic evidence from governed DIRECT missions.

A locally checked answer is subtask evidence, not general conversational skill.
Operational catalogue fingerprints gate eligibility; they are NOT attestation of
executed provider weights. Legacy unversioned attempts are excluded on reload.
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

from .model_qualification import verified_version

_ALLOWED_SUBTASKS = frozenset({"exact_echo", "arithmetic", "classification", "extraction", "sorting"})
_ALLOWED_LANGUAGES = frozenset({"fr", "en", "hr", "de", "it", "es", "pt", "ar"})
_DIGEST = re.compile(r"(?:sha256:)?([0-9a-f]{64})\Z")
_LOCK = threading.Lock()
_MAX_PER_KEY = 32
# The model digest prevents success/failure from a replaced model from
# affecting a model that later reuses its resource ID.
_SAMPLES: dict[tuple[str, str, str, str], tuple[tuple[str, float], ...]] = {}
_SNAPSHOT: dict[tuple[str, str, str, str], "SemanticQualification"] = {}


@dataclass(frozen=True, slots=True)
class SemanticQualification:
    attempts: int = 0
    passed: int = 0
    failed: int = 0
    p95_ms: float | None = None
    median_ms: float | None = None

    @property
    def qualified(self) -> bool:
        return self.passed >= 2 and self.attempts >= 2 and self.passed / self.attempts >= .80

    @property
    def disqualified(self) -> bool:
        # A deterministic verifier has stronger evidence than shape checks.
        return ((self.failed >= 1 and self.passed == 0)
                or (self.attempts >= 3 and self.failed >= 2
                    and self.passed / self.attempts < .50))


_EMPTY = SemanticQualification()


def _valid(resource_id: Any, subtask: Any, language: Any,
           outcome: Any, wall_ms: Any, fingerprint: Any = None):
    digest = (_DIGEST.fullmatch(fingerprint).group(1)
              if isinstance(fingerprint, str) and _DIGEST.fullmatch(fingerprint) else None)
    if not isinstance(resource_id, str) or not 1 <= len(resource_id) <= 256 or digest is None:
        return None
    if subtask not in _ALLOWED_SUBTASKS or language not in _ALLOWED_LANGUAGES:
        return None
    if outcome not in {"pass", "fail"}:
        return None
    if (not isinstance(wall_ms, (int, float)) or isinstance(wall_ms, bool)
            or not math.isfinite(wall_ms) or not 0 < wall_ms <= 120_000):
        return None
    return (resource_id, digest, str(subtask), str(language)), (str(outcome), float(wall_ms))


def _summarize(rows: tuple[tuple[str, float], ...]) -> SemanticQualification:
    good = sorted(ms for outcome, ms in rows if outcome == "pass")
    return SemanticQualification(
        attempts=len(rows), passed=len(good),
        failed=sum(outcome == "fail" for outcome, _ in rows),
        median_ms=statistics.median(good) if good else None,
        p95_ms=good[max(0, math.ceil(len(good) * .95) - 1)] if good else None,
    )


def observe_semantic_attempt(resource_id: str, subtask: str, language: str,
                             outcome: str, wall_ms: float, *,
                             model_fingerprint: str | None = None) -> bool:
    """Observe only a real, version-tagged, locally verified semantic verdict.

    A version refresh during the call may retain historical rows but cannot
    qualify the new current digest through these stale rows.
    """
    normalized = _valid(resource_id, subtask, language, outcome, wall_ms,
                        model_fingerprint)
    if normalized is None:
        return False
    key, sample = normalized
    global _SNAPSHOT
    with _LOCK:
        if verified_version(resource_id) != key[1]:
            return False
        rows = (_SAMPLES.get(key, ()) + (sample,))[-_MAX_PER_KEY:]
        _SAMPLES[key] = rows
        _SNAPSHOT = {**_SNAPSHOT, key: _summarize(rows)}
    return True


def semantic_qualification(resource_id: str, subtask: str | None,
                           language: str) -> SemanticQualification:
    if subtask not in _ALLOWED_SUBTASKS or language not in _ALLOWED_LANGUAGES:
        return _EMPTY
    version = verified_version(resource_id)
    if version is None:
        return _EMPTY
    snapshot = _SNAPSHOT
    q = snapshot.get((resource_id, version, str(subtask), language), _EMPTY)
    # A concurrent catalogue refresh must not expose a verdict from a replaced
    # version that was current at the beginning of this read.
    return q if verified_version(resource_id) == version else _EMPTY


def warm_semantic_qualifications(root: Path, limit: int = 100) -> int:
    """Load only SHA256-scoped, deterministic-verifier metadata on boot.

    Old records without model_fingerprint never grant semantic qualification.
    This path performs no provider calls and stores no chat contents.
    """
    loaded: dict[tuple[str, str, str, str], tuple[tuple[str, float], ...]] = {}
    try:
        files = sorted(root.glob("CHAT-*.json"), key=lambda p: p.stat().st_mtime,
                       reverse=True)[:limit]
    except (OSError, ValueError):
        return 0
    for path in reversed(files):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            for message in record.get("messages") or []:
                if not isinstance(message, dict) or message.get("role") not in {"assistant", "system"}:
                    continue
                metrics = ((message.get("metadata") or {}).get("mission") or {}).get("metrics") or {}
                if not isinstance(metrics, dict):
                    continue
                language = metrics.get("routing_language")
                for attempt in metrics.get("model_attempt_metrics") or []:
                    if not isinstance(attempt, dict):
                        continue
                    normalized = _valid(
                        attempt.get("resource_id"), attempt.get("semantic_subtask"),
                        language, attempt.get("semantic_outcome"),
                        attempt.get("wall_ms"), attempt.get("model_fingerprint"),
                    )
                    if normalized is not None:
                        key, sample = normalized
                        loaded[key] = (loaded.get(key, ()) + (sample,))[-_MAX_PER_KEY:]
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    global _SAMPLES, _SNAPSHOT
    with _LOCK:
        for key, rows in _SAMPLES.items():
            loaded[key] = (loaded.get(key, ()) + rows)[-_MAX_PER_KEY:]
        _SAMPLES = loaded
        _SNAPSHOT = {key: _summarize(rows) for key, rows in loaded.items()}
    return sum(len(rows) for rows in loaded.values())


def reset_for_testing() -> None:
    global _SAMPLES, _SNAPSHOT
    with _LOCK:
        _SAMPLES = {}
        _SNAPSHOT = {}
