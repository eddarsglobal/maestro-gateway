"""User-authorized local language benchmark evidence for provisional routing.

This module never runs a model, performs network I/O, downloads anything, or
claims fluency.  A separate importer records objective benchmark outcomes bound
to the exact local model digest.  The hot routing path consumes only the
in-memory snapshot loaded during Gateway startup.

Evidence is intentionally stronger than background synthetic micro-probes but
still lower-trust than successful real user missions: it may choose a
*provisional* candidate for an explicitly requested language, while the normal
MAESTRO response guard and mission acceptance path remain authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Iterable

_SCHEMA = 1
_LOCK = RLock()
_EVIDENCE: dict[str, dict[str, Any]] = {}
_SOURCE: dict[str, Any] = {}
_SUPPORTED = frozenset({'de', 'ar', 'fr', 'en', 'hr', 'it', 'es', 'pt'})


@dataclass(frozen=True)
class LanguageBenchmark:
    resource_id: str
    language: str
    passes: int
    total: int
    natural_pass: bool
    elapsed_s: float
    source_report_sha256: str

    @property
    def complete(self) -> bool:
        return self.total > 0 and self.passes == self.total and self.natural_pass


def _clean_row(resource_id: str, raw: Any) -> dict[str, Any] | None:
    if not isinstance(resource_id, str) or not (1 <= len(resource_id) <= 256):
        return None
    if not isinstance(raw, dict):
        return None
    digest = raw.get('digest')
    if not isinstance(digest, str) or len(digest) < 16 or len(digest) > 256:
        return None
    languages = raw.get('languages')
    if not isinstance(languages, dict):
        return None
    clean_languages: dict[str, dict[str, Any]] = {}
    for language, item in languages.items():
        if language not in _SUPPORTED or not isinstance(item, dict):
            continue
        passes, total = item.get('passes'), item.get('total')
        natural_pass = item.get('natural_pass')
        elapsed_s = item.get('elapsed_s', 0.0)
        if (not isinstance(passes, int) or isinstance(passes, bool)
                or not isinstance(total, int) or isinstance(total, bool)
                or total <= 0 or passes < 0 or passes > total
                or not isinstance(natural_pass, bool)
                or not isinstance(elapsed_s, (int, float)) or isinstance(elapsed_s, bool)
                or elapsed_s < 0 or elapsed_s > 3600):
            continue
        clean_languages[language] = {
            'passes': passes,
            'total': total,
            'natural_pass': natural_pass,
            'elapsed_s': float(elapsed_s),
        }
    if not clean_languages:
        return None
    return {'digest': digest, 'languages': clean_languages}


def load_language_benchmark_evidence(path: Path) -> int:
    """Load a bounded, fail-closed evidence snapshot from local disk.

    Invalid or absent evidence clears the snapshot and returns zero.  Raw model
    outputs/prompts are neither required nor retained in memory.
    """
    global _EVIDENCE, _SOURCE
    imported: dict[str, dict[str, Any]] = {}
    source: dict[str, Any] = {}
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError('missing_or_unsafe')
        doc = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(doc, dict) or doc.get('schema') != _SCHEMA:
            raise ValueError('schema')
        report_sha = doc.get('source_report_sha256')
        if not isinstance(report_sha, str) or len(report_sha) != 64:
            raise ValueError('source_report_sha256')
        models = doc.get('models')
        if not isinstance(models, dict) or len(models) > 128:
            raise ValueError('models')
        for rid, raw in models.items():
            row = _clean_row(rid, raw)
            if row is not None:
                imported[rid] = row
        source = {'source_report_sha256': report_sha, 'path': str(path)}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        imported = {}
        source = {}
    with _LOCK:
        _EVIDENCE = imported
        _SOURCE = source
    return len(imported)


def benchmark(resource_id: str, verified_digest: str | None,
              language: str) -> LanguageBenchmark | None:
    if language not in _SUPPORTED or not isinstance(verified_digest, str):
        return None
    with _LOCK:
        row = _EVIDENCE.get(resource_id)
        source_sha = _SOURCE.get('source_report_sha256', '')
        if not isinstance(row, dict) or row.get('digest') != verified_digest:
            return None
        item = (row.get('languages') or {}).get(language)
        if not isinstance(item, dict):
            return None
        return LanguageBenchmark(
            resource_id=resource_id,
            language=language,
            passes=int(item['passes']),
            total=int(item['total']),
            natural_pass=bool(item['natural_pass']),
            elapsed_s=float(item['elapsed_s']),
            source_report_sha256=str(source_sha),
        )


def preferred_language_resource(resource_ids: Iterable[str], language: str,
                                verified_version: Callable[[str], str | None]) -> str | None:
    """Return the best *provisional* complete benchmark candidate.

    No provider/model identity is encoded here.  Exact digest binding revokes
    stale evidence automatically.  Complete objective coverage + a natural
    identity probe is required; elapsed time only breaks ties among equally
    complete candidates.
    """
    candidates: list[tuple[float, str]] = []
    for rid in resource_ids:
        evidence = benchmark(rid, verified_version(rid), language)
        if evidence is None or not evidence.complete:
            continue
        candidates.append((evidence.elapsed_s, rid))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]))[1]


def evidence_status() -> dict[str, Any]:
    with _LOCK:
        return {
            'models': len(_EVIDENCE),
            'source_report_sha256': _SOURCE.get('source_report_sha256'),
            'semantic_fluency_certified': False,
            'purpose': 'provisional explicit-language routing only',
        }


def reset_for_testing() -> None:
    global _EVIDENCE, _SOURCE
    with _LOCK:
        _EVIDENCE = {}
        _SOURCE = {}
