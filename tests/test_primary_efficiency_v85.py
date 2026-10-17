"""V8.5: bounded sovereign CPU efficiency, without model-name preferences."""
from __future__ import annotations
import hashlib

import time
import unittest
from dataclasses import dataclass

from maestro_gateway import autonomous_qualification, model_qualification
from maestro_gateway.adaptive_router import choose_adaptive_resource
from maestro_gateway.primary_intelligence import PrimaryIntelligenceManager


@dataclass(frozen=True)
class D:
    resource_id: str
    local: bool = True
    capabilities: tuple[str, ...] = ('DIRECT',)
    size: int = 1_500_000_000
    status: str = 'AVAILABLE'

    @property
    def metadata(self):
        return {'size': self.size}


class R:
    def __init__(self, *items):
        self.items = items

    def all(self):
        return self.items


# FIXED5.8C: synthetic immutable digests ONLY for isolated test descriptors.
_TEST_RESOURCE_IDS = ('cloud::fast', 'local::active', 'local::chat', 'local::embed', 'local::unavailable', 'provider::middle', 'provider::tiny', 'providerX::big', 'providerY::medium', 'runtime::candidate', 'runtime::fast', 'runtime::slow', 'runtime::small', 'runtime::strong', 'runtime::unmeasured', 'runtime::weak', 'runtimeA::general', 'runtimeB::code')
def _test_model_fingerprint(rid: str) -> str:
    return hashlib.sha256(('maestro/fixed58c/testing-only\0' + rid).encode('utf-8')).hexdigest()
def _activate_test_versions() -> None:
    model_qualification.update_verified_versions({
        rid: _test_model_fingerprint(rid) for rid in _TEST_RESOURCE_IDS
    })


def learn(rid: str, count: int, ms: float, task='normal', lang='fr'):
    for _ in range(count):
        assert model_qualification.observe_attempt(rid, task, lang, 'quality_accepted', ms, model_fingerprint=_test_model_fingerprint(rid))


class PrimaryEfficiencyV85Tests(unittest.TestCase):
    def setUp(self):
        model_qualification.reset_for_testing()
        _activate_test_versions()
        autonomous_qualification.reset_for_testing()

    def tearDown(self):
        model_qualification.reset_for_testing()
        autonomous_qualification.reset_for_testing()

    def test_coder_can_compete_in_ordinary_conversation(self):
        general = D('runtimeA::general', size=6_000_000_000)
        coder = D('runtimeB::code', capabilities=('DIRECT', 'CODE'), size=1_400_000_000)
        p = PrimaryIntelligenceManager()
        self.assertEqual(p.select(R(general, coder), 'Bonjour, explique le principe.',
                                  ui_language='fr').decision.resource_id, coder.resource_id)

    def test_cold_start_independent_of_registry_order_and_brand(self):
        a = D('providerX::big', size=5_000_000_000)
        b = D('providerY::medium', size=1_450_000_000)
        for models in ((a, b), (b, a)):
            d = choose_adaptive_resource(R(*models), 'Explique le sujet.', ui_language='fr')
            self.assertEqual(d.resource_id, b.resource_id)
            self.assertEqual(d.qualification_status, 'provisional')

    def test_avoid_tiny_unproven_model_for_normal_task(self):
        tiny = D('provider::tiny', size=1_000_000_000)
        middle = D('provider::middle', size=1_500_000_000)
        p = PrimaryIntelligenceManager()
        self.assertEqual(p.select(R(tiny, middle), 'Explique le sujet.',
                                  ui_language='fr').decision.resource_id, middle.resource_id)

    def test_qualified_fast_model_beats_unmeasured_cold_candidate(self):
        fast = D('runtime::fast', size=3_000_000_000)
        small = D('runtime::small', size=1_400_000_000)
        learn(fast.resource_id, 6, 1300)
        self.assertEqual(choose_adaptive_resource(R(fast, small), 'Explique le sujet.',
                                                  ui_language='fr').resource_id, fast.resource_id)

    def test_provisional_trial_after_proven_extreme_slowness(self):
        slow = D('runtime::slow', size=6_000_000_000)
        fast_unknown = D('runtime::unmeasured', size=1_300_000_000)
        p = PrimaryIntelligenceManager()
        self.assertEqual(p.select(R(slow), 'Explique le sujet.', ui_language='fr').decision.resource_id,
                         slow.resource_id)
        learn(slow.resource_id, 2, 70_000)
        # No immediate oscillation; wait for the switch window.
        self.assertEqual(p.select(R(slow, fast_unknown), 'Explique le sujet.',
                                  ui_language='fr').decision.resource_id, slow.resource_id)
        p._primary_selected_at = time.monotonic() - 121
        next_one = p.select(R(slow, fast_unknown), 'Explique le sujet.', ui_language='fr')
        self.assertEqual(next_one.decision.resource_id, fast_unknown.resource_id)
        self.assertEqual(next_one.reason, 'bounded_provisional_speed_trial')
        self.assertEqual(next_one.decision.qualification_status, 'provisional')
        self.assertEqual(next_one.epoch, 2)
        self.assertFalse(p.snapshot()['inference_ready'])
        self.assertFalse(p.snapshot()['cloud_enabled'])
        # A trial cannot be repeated indefinitely even if the original returns.
        p._primary = slow.resource_id
        p._primary_selected_at = time.monotonic() - 121
        p._last_measured_switch_at = time.monotonic() - 121
        self.assertEqual(p.select(R(slow, fast_unknown), 'Explique le sujet.',
                                  ui_language='fr').decision.resource_id, slow.resource_id)

    def test_never_provisional_trial_on_complex_task(self):
        slow = D('runtime::slow', size=6_000_000_000)
        unknown = D('runtime::candidate', size=2_200_000_000)
        p = PrimaryIntelligenceManager()
        p.select(R(slow), 'Analyse en détail le système.', ui_language='fr')
        learn(slow.resource_id, 3, 110_000, task='complex')
        p._primary_selected_at = time.monotonic() - 121
        self.assertEqual(p.select(R(slow, unknown), 'Analyse en détail le système.',
                                  ui_language='fr').decision.resource_id, slow.resource_id)

    def test_no_remote_or_embedding_resources(self):
        embed = D('local::embed', capabilities=('EMBEDDING',), size=200_000_000)
        cloud = D('cloud::fast', local=False, size=100_000_000)
        local = D('local::chat', size=2_000_000_000)
        p = PrimaryIntelligenceManager()
        self.assertEqual(p.select(R(embed, cloud, local), 'Salut',
                                  ui_language='fr').decision.resource_id, local.resource_id)
        with self.assertRaises(RuntimeError):
            PrimaryIntelligenceManager().select(R(embed, cloud), 'Salut', ui_language='fr')

    def test_preserve_measured_slow_but_superior_complex_competence(self):
        strong = D('runtime::strong', size=6_000_000_000)
        weak = D('runtime::weak', size=2_500_000_000)
        learn(strong.resource_id, 6, 50_000, task='complex')
        self.assertEqual(choose_adaptive_resource(R(weak, strong),
                         'Analyse en détail le système.', ui_language='fr').resource_id, strong.resource_id)

    def test_unavailable_and_degraded_filter(self):
        a = D('local::unavailable', size=1_500_000_000, status='OFFLINE')
        b = D('local::active', size=2_000_000_000)
        self.assertEqual(PrimaryIntelligenceManager().select(R(a, b), 'Salut',
                         ui_language='fr').decision.resource_id, b.resource_id)


if __name__ == '__main__':
    unittest.main()
