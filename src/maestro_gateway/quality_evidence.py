"""FIXED5.8G2 — bounded, provider-neutral observation of real Gateway model attempts.

Evidence is written ONLY in the existing mission model_attempt_metrics metadata.
This is not a qualification or execution-weight attestation. The original
response and prompt are never copied into this record. No background inference.
"""
from __future__ import annotations

import re
from typing import Any
from .response_guard import explicit_format_evidence

_SHA = re.compile(r'(?:sha256:)?([0-9a-f]{64})\Z')
_SCHEMA = 'maestro-fixed58g2-quality-evidence-1'


def _digest(value: Any) -> str | None:
    m = _SHA.fullmatch(value) if isinstance(value, str) else None
    return m.group(1) if m else None


def capture_attempt_quality(prompt: str, output: str, finish_reason: Any,
                            model_fingerprint: Any) -> dict[str, Any]:
    """Capture only bounded observations; do not promote a model.

    Caller executes this in the real model-attempt path, before persistence.
    Completion is based on the provider's self-reported reason, not an
    independent verification of inference or weights.
    """
    finish = finish_reason if isinstance(finish_reason, str) else None
    evidence = explicit_format_evidence(prompt, output, finish)
    if (not isinstance(evidence, dict) or
        evidence.get('format_conformance') not in {'pass', 'fail', 'unverified'} or
        evidence.get('completion') not in {'complete_reported', 'truncated', 'unverified'} or
        evidence.get('format_contract') not in {None, 'two_paragraphs', 'digits_only'}):
        raise RuntimeError('Unexpected G1 evidence schema; refusing to claim a verdict')
    version = _digest(model_fingerprint)
    return {
        'schema': _SCHEMA,
        'catalogue_sha256': version,
        'binding': ('catalogue_snapshot_not_execution_attested' if version else 'unverified'),
        'format_contract': evidence['format_contract'],
        'format_conformance': evidence['format_conformance'],
        'completion': evidence['completion'],
        'completion_provenance': 'provider_reported_or_unknown',
        'semantic_verdict_at_capture': 'not_assessed',
        'general_semantic_truth': 'unverified',
        'qualifies_model': False,
    }


def capture_transport_failure(model_fingerprint: Any) -> dict[str, Any]:
    """Provider call raised: no verified completed response was returned."""
    version = _digest(model_fingerprint)
    return {
        'schema': _SCHEMA,
        'catalogue_sha256': version,
        'binding': ('catalogue_snapshot_not_execution_attested' if version else 'unverified'),
        'format_contract': None,
        'format_conformance': 'unverified',
        'completion': 'provider_error_no_complete_response',
        'completion_provenance': 'gateway_transport_exception',
        'semantic_verdict_at_capture': 'not_assessed',
        'general_semantic_truth': 'unverified',
        'qualifies_model': False,
    }


def inspect_persisted_attempt(attempt: Any, *, expected_catalogue_digest: Any = None) -> dict[str, Any]:
    """Read-only reconciliation of *recorded* dimensions. No qualification grant.

    Deterministic semantic result is separate metadata written by the existing
    semantic verifier. A format failure does not invent a semantic failure.
    Legacy/malformed evidence stays unverified. Digest mismatches invalidate
    the version binding without erasing the historical observation.
    """
    unknown = {
        'schema': _SCHEMA,
        'version_consistent': False,
        'format': 'unverified',
        'completion': 'unverified',
        'deterministic_semantic': 'unverified',
        'general_semantic_truth': 'unverified',
        'qualifies_model': False,
    }
    if not isinstance(attempt, dict):
        return unknown
    row = attempt.get('quality_evidence')
    if not isinstance(row, dict) or row.get('schema') != _SCHEMA:
        return unknown
    version = _digest(attempt.get('model_fingerprint'))
    observed = _digest(row.get('catalogue_sha256'))
    current = _digest(expected_catalogue_digest)
    consistent = bool(version and observed and version == observed and
                      current is not None and current == version)
    fmt = row.get('format_conformance')
    completion = row.get('completion')
    semantic = attempt.get('semantic_outcome')
    allowed_contracts = {None, 'two_paragraphs', 'digits_only'}
    if (fmt not in {'pass', 'fail', 'unverified'} or
        completion not in {'complete_reported', 'truncated', 'unverified', 'provider_error_no_complete_response'} or
        row.get('format_contract') not in allowed_contracts or
        row.get('qualifies_model') is not False):
        return unknown
    if row.get('binding') != ('catalogue_snapshot_not_execution_attested' if version else 'unverified'):
        return unknown
    return {
        'schema': _SCHEMA,
        'version_consistent': consistent,
        'format': fmt,
        'completion': completion,
        'deterministic_semantic': semantic if semantic in {'pass', 'fail'} else 'unverified',
        'general_semantic_truth': 'unverified',
        'qualifies_model': False,
    }
