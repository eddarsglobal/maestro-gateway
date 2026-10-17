"""V8.11.5: read-only provenance view of real-mission operational evidence.

This never grants eligibility, probes a model, or infers semantic competence.
It deliberately highlights that historical operational samples are NOT currently
bound to immutable model-version fingerprints (FIXED5.8B observability only).
"""
from __future__ import annotations
from typing import Any
from .model_qualification import qualification, verified_version

LANGUAGES = ('en', 'fr', 'de', 'ar', 'pt', 'es', 'hr', 'it')
TASKS = ('quick', 'normal', 'code', 'complex')


def inspect_operational_evidence(descriptors: Any) -> dict[str, Any]:
    """Bounded, in-memory-only snapshot for /models; never a routing decision."""
    rows: list[dict[str, Any]] = []
    for descriptor in descriptors:
        if len(rows) >= 128:
            break
        if getattr(descriptor, 'local', None) is not True:
            continue
        rid = getattr(descriptor, 'resource_id', None)
        if not isinstance(rid, str) or not rid or len(rid) > 256:
            continue
        caps = {str(getattr(c, 'value', c)).upper()
                for c in (getattr(descriptor, 'capabilities', ()) or ())}
        if not caps.intersection({'DIRECT', 'GENERAL_REASONING'}):
            continue
        status = str(getattr(getattr(descriptor, 'status', 'UNKNOWN'), 'value',
                             getattr(descriptor, 'status', 'UNKNOWN')))
        observations: list[dict[str, Any]] = []
        for lang in LANGUAGES:
            for task in TASKS:
                q = qualification(rid, task, lang)
                if q.attempts <= 0:
                    continue
                observations.append({
                    'task_class': task, 'language': lang,
                    'attempts': q.attempts, 'accepted_by_guard': q.accepted,
                    'rejected_by_guard': q.rejected, 'timed_out': q.timed_out,
                    'p95_accepted_ms': q.p95_ms,
                    'operational_threshold_met': bool(q.qualified),
                    'factual_truth_verified': False,
                })
        rows.append({
            'resource_id': rid, 'registry_status_at_snapshot': status,
            'catalogue_sha256_verified': verified_version(rid) is not None,
            'catalogue_digest_not_execution_attested': True,
            'observations': observations,
            'observed_task_language_pairs': len(observations),
            'unobserved_task_language_pairs': len(LANGUAGES) * len(TASKS) - len(observations),
        })
    return {
        'schema': 'maestro-operational-evidence-observability-v1',
        'provenance': 'authenticated_real_mission_attempts_and_private_history',
        'version_binding': 'CATALOGUE_SHA256_SCOPED_NOT_EXECUTION_ATTESTED',
        'model_digest_change_invalidation': True,
        'trust': 'response_guard_operational_only_not_semantic_truth',
        'synthetic_idle_probes_used_for_operational_threshold': False,
        'eligible_for_quality_fallback': None,
        'eligibility_note': 'Requires fresh runtime permissions, current primary, same-task/language threshold, remaining deadline and Core governance.',
        'hot_path': 'read_only_in_memory_no_inference',
        'languages': list(LANGUAGES), 'task_classes': list(TASKS),
        'models': rows,
    }
