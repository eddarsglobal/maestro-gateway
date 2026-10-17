"""V8.6 regression coverage for poisoned evidence and slow-primary escape."""
from __future__ import annotations
import hashlib

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from maestro_gateway import model_qualification
from maestro_gateway.adaptive_router import choose_adaptive_resource
from maestro_gateway.primary_intelligence import PrimaryIntelligenceManager


@dataclass(frozen=True)
class D:
    resource_id: str
    size: int
    local: bool = True
    status: str = 'AVAILABLE'
    capabilities: tuple[str, ...] = ('DIRECT', 'GENERAL_REASONING')

    @property
    def metadata(self):
        return {'size': self.size}


class R:
    def __init__(self, *items): self.items = items
    def all(self): return self.items


# FIXED5.8C: synthetic immutable digests ONLY for isolated test descriptors.
_TEST_RESOURCE_IDS = ('local::candidate', 'local::heavy', 'local::light', 'local::medium', 'local::slow', 'local::small', 'local::smaller', 'local::tiny', 'local::very-slow')
def _test_model_fingerprint(rid: str) -> str:
    return hashlib.sha256(('maestro/fixed58c/testing-only\0' + rid).encode('utf-8')).hexdigest()
def _activate_test_versions() -> None:
    model_qualification.update_verified_versions({
        rid: _test_model_fingerprint(rid) for rid in _TEST_RESOURCE_IDS
    })


def learn(rid: str, outcome: str, ms: float, *, task='normal', lang='fr'):
    assert model_qualification.observe_attempt(rid, task, lang, outcome, ms, model_fingerprint=_test_model_fingerprint(rid))


class LatencyEvidenceRepairV86Tests(unittest.TestCase):
    def setUp(self):
        model_qualification.reset_for_testing()
        _activate_test_versions()
    def tearDown(self): model_qualification.reset_for_testing()

    def test_historic_generation_limit_rejection_is_not_reloaded_as_bad_model(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            payload = {
                'messages': [{
                    'role': 'system',
                    'metadata': {'mission': {'metrics': {
                        'routing_class': 'normal', 'routing_language': 'fr',
                        'quality_reason': 'generation_token_limit',
                        'model_attempt_metrics': [{
                            'resource_id': 'local::small',
                            'model_fingerprint': _test_model_fingerprint('local::small'),
                            'outcome': 'quality_rejected', 'wall_ms': 1800.0,
                        }],
                    }}},
                }],
            }
            (root/'CHAT-1.json').write_text(json.dumps(payload))
            self.assertEqual(model_qualification.warm_qualifications(root), 0)
            self.assertEqual(model_qualification.qualification('local::small','normal','fr').attempts, 0)

    def test_real_quality_rejection_still_reloads(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            payload = {
                'messages': [{
                    'role': 'system',
                    'metadata': {'mission': {'metrics': {
                        'routing_class': 'normal', 'routing_language': 'fr',
                        'quality_reason': 'echo',
                        'model_attempt_metrics': [{
                            'resource_id': 'local::small',
                            'model_fingerprint': _test_model_fingerprint('local::small'),
                            'outcome': 'quality_rejected', 'wall_ms': 1800.0,
                        }],
                    }}},
                }],
            }
            (root/'CHAT-1.json').write_text(json.dumps(payload))
            self.assertEqual(model_qualification.warm_qualifications(root), 1)
            self.assertEqual(model_qualification.qualification('local::small','normal','fr').rejected, 1)

    def test_unversioned_legacy_rejection_is_ignored_for_qualification(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            payload = {'messages': [{'role': 'system', 'metadata': {'mission': {'metrics': {
                'routing_class': 'normal', 'routing_language': 'fr',
                'model_attempt_metrics': [{'resource_id': 'local::small',
                    'outcome': 'quality_rejected', 'wall_ms': 1800.0}],
            }}}}]}
            (root / 'CHAT-legacy.json').write_text(json.dumps(payload))
            self.assertEqual(model_qualification.warm_qualifications(root), 0)
            self.assertEqual(model_qualification.qualification('local::small', 'normal', 'fr').attempts, 0)

    def test_one_sparse_rejection_does_not_permanently_hide_much_lighter_candidate(self):
        light = D('local::light', 1_200_000_000)
        heavy = D('local::heavy', 5_500_000_000)
        learn(light.resource_id, 'quality_rejected', 2500)
        chosen = choose_adaptive_resource(R(light, heavy), 'Explique le sujet.', ui_language='fr')
        self.assertEqual(chosen.resource_id, light.resource_id)

    def test_repeated_real_rejections_still_make_alternative_preferable(self):
        light = D('local::light', 1_200_000_000)
        heavy = D('local::heavy', 2_000_000_000)
        for _ in range(4): learn(light.resource_id, 'quality_rejected', 2500)
        chosen = choose_adaptive_resource(R(light, heavy), 'Explique le sujet.', ui_language='fr')
        self.assertEqual(chosen.resource_id, heavy.resource_id)

    def test_known_extremely_slow_primary_loses_cold_selection_to_clean_smaller_model(self):
        slow = D('local::slow', 6_000_000_000)
        smaller = D('local::smaller', 1_300_000_000)
        for _ in range(2): learn(slow.resource_id, 'quality_accepted', 70_000)
        chosen = choose_adaptive_resource(R(slow, smaller), 'Explique le sujet.', ui_language='fr')
        self.assertEqual(chosen.resource_id, smaller.resource_id)

    def test_one_accepted_extremely_slow_turn_allows_next_turn_bounded_trial(self):
        slow = D('local::slow', 6_000_000_000)
        candidate = D('local::candidate', 1_300_000_000)
        p = PrimaryIntelligenceManager()
        first = p.select(R(slow), 'Explique le sujet.', ui_language='fr')
        self.assertEqual(first.decision.resource_id, slow.resource_id)
        learn(slow.resource_id, 'quality_accepted', 70_000)
        second = p.select(R(slow, candidate), 'Explique encore.', ui_language='fr')
        self.assertEqual(second.decision.resource_id, candidate.resource_id)
        self.assertEqual(second.reason, 'immediate_slow_primary_trial')

    def test_immediate_trial_never_uses_tiny_candidate_below_normal_floor(self):
        slow = D('local::slow', 6_000_000_000)
        tiny = D('local::tiny', 500_000_000)
        p = PrimaryIntelligenceManager()
        p.select(R(slow), 'Explique le sujet.', ui_language='fr')
        learn(slow.resource_id, 'quality_accepted', 70_000)
        second = p.select(R(slow, tiny), 'Explique encore.', ui_language='fr')
        self.assertEqual(second.decision.resource_id, slow.resource_id)
        self.assertEqual(second.reason, 'primary_reused')

    def test_extreme_complex_latency_opens_cold_competition_without_discarding_normal_complex_evidence(self):
        very_slow = D('local::very-slow', 6_000_000_000)
        medium = D('local::medium', 2_500_000_000)
        for _ in range(2):
            learn(very_slow.resource_id, 'quality_accepted', 70_000, task='complex')
        chosen = choose_adaptive_resource(
            R(very_slow, medium), 'Analyse en détail le système.', ui_language='fr')
        self.assertEqual(chosen.resource_id, medium.resource_id)


if __name__ == '__main__':
    unittest.main()
