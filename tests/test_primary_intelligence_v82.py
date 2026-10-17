"""V8.2 sovereign primary selection from actual operational mission evidence."""
from __future__ import annotations
import hashlib

import time
import unittest
from dataclasses import dataclass

from maestro_gateway import autonomous_qualification, model_qualification
from maestro_gateway.primary_intelligence import PrimaryIntelligenceManager


@dataclass(frozen=True)
class Descriptor:
    resource_id: str
    local: bool = True
    capabilities: tuple[str, ...] = ('DIRECT',)
    status: str = 'AVAILABLE'


class Registry:
    def __init__(self, *descriptors):
        self.items = list(descriptors)

    def all(self):
        return tuple(self.items)


# FIXED5.8C: synthetic immutable digests ONLY for isolated test descriptors.
_TEST_RESOURCE_IDS = ('local-one::A', 'local-three::C', 'local-two::B')
def _test_model_fingerprint(rid: str) -> str:
    return hashlib.sha256(('maestro/fixed58c/testing-only\0' + rid).encode('utf-8')).hexdigest()
def _activate_test_versions() -> None:
    model_qualification.update_verified_versions({
        rid: _test_model_fingerprint(rid) for rid in _TEST_RESOURCE_IDS
    })


def learn(rid, *, task='quick', language='en', accepted=8, failures=0, ms=5000):
    for _ in range(accepted):
        assert model_qualification.observe_attempt(rid, task, language,
                                                   'quality_accepted', ms, model_fingerprint=_test_model_fingerprint(rid))
    for _ in range(failures):
        assert model_qualification.observe_attempt(rid, task, language,
                                                   'quality_rejected', ms, model_fingerprint=_test_model_fingerprint(rid))


class V82MeasuredSwitchTests(unittest.TestCase):
    def setUp(self):
        model_qualification.reset_for_testing()
        _activate_test_versions()
        autonomous_qualification.reset_for_testing()
        self.a = Descriptor('local-one::A')
        self.b = Descriptor('local-two::B')
        self.c = Descriptor('local-three::C')
        self.manager = PrimaryIntelligenceManager()
        self.assertEqual(self.manager.select(Registry(self.a), 'Hello',
                                             ui_language='en').decision.resource_id,
                         self.a.resource_id)

    def tearDown(self):
        model_qualification.reset_for_testing()
        autonomous_qualification.reset_for_testing()

    def choose(self, *candidates, language='en'):
        return self.manager.select(Registry(*candidates), 'Hello', ui_language=language)

    def test_switch_after_sufficient_real_latency_evidence(self):
        learn(self.a.resource_id, ms=20_000)
        learn(self.b.resource_id, ms=3_000)
        selected = self.choose(self.a, self.b)
        self.assertEqual(selected.decision.resource_id, self.b.resource_id)
        self.assertEqual(selected.reason, 'measured_operational_improvement')
        self.assertEqual(selected.epoch, 2)

    def test_switch_after_sufficient_real_reliability_evidence(self):
        learn(self.a.resource_id, accepted=6, failures=2, ms=6000)
        learn(self.b.resource_id, accepted=8, ms=6000)
        self.assertEqual(self.choose(self.a, self.b).decision.resource_id,
                         self.b.resource_id)

    def test_not_switch_on_sparse_fast_alternative(self):
        learn(self.a.resource_id, ms=25_000)
        learn(self.b.resource_id, accepted=2, ms=1000)
        self.assertEqual(self.choose(self.a, self.b).decision.resource_id,
                         self.a.resource_id)

    def test_not_switch_for_marginal_speed_gain(self):
        learn(self.a.resource_id, ms=5000)
        learn(self.b.resource_id, ms=4100)
        self.assertEqual(self.choose(self.a, self.b).decision.resource_id,
                         self.a.resource_id)

    def test_no_synthetic_microprobe_promotion(self):
        for _ in range(3):
            autonomous_qualification.record_probe(self.a.resource_id, 'v1', 'en',
                                                   'arithmetic', 'fail', 2000)
            autonomous_qualification.record_probe(self.b.resource_id, 'v1', 'en',
                                                   'arithmetic', 'pass', 100)
        self.assertEqual(self.choose(self.a, self.b).decision.resource_id,
                         self.a.resource_id)

    def test_no_cloud_even_with_superior_measured_evidence(self):
        learn(self.a.resource_id, ms=20_000)
        learn(self.b.resource_id, ms=1000)
        remote = Descriptor(self.b.resource_id, local=False)
        self.assertEqual(self.choose(self.a, remote).decision.resource_id,
                         self.a.resource_id)

    def test_evidence_is_task_language_specific(self):
        learn(self.a.resource_id, ms=20_000)
        learn(self.b.resource_id, language='fr', ms=1000)
        self.assertEqual(self.choose(self.a, self.b).decision.resource_id,
                         self.a.resource_id)

    def test_prevent_switch_oscillation_in_cooldown(self):
        learn(self.a.resource_id, ms=20_000)
        learn(self.b.resource_id, ms=7000)
        learn(self.c.resource_id, ms=1000)
        self.assertEqual(self.choose(self.a, self.b).decision.resource_id,
                         self.b.resource_id)
        # Current primary B has a faster qualified C, but hysteresis protects
        # in-flight conversational continuity from eager back-to-back switches.
        self.assertEqual(self.choose(self.a, self.b, self.c).decision.resource_id,
                         self.b.resource_id)
        self.manager._last_measured_switch_at = time.monotonic() - 121
        self.assertEqual(self.choose(self.a, self.b, self.c).decision.resource_id,
                         self.c.resource_id)

    def test_unqualified_primary_yields_to_real_qualified_candidate(self):
        learn(self.b.resource_id, ms=7500)
        selected = self.choose(self.a, self.b)
        self.assertEqual(selected.decision.resource_id, self.b.resource_id)
        self.assertEqual(selected.reason, 'task_language_evidence')

    def test_zero_models_remains_safe(self):
        with self.assertRaises(RuntimeError):
            self.choose()
        self.assertIsNone(self.manager.snapshot()['primary_resource_id'])
        self.assertFalse(self.manager.snapshot()['cloud_enabled'])

    def test_quarantine_and_atomic_epoch_guard_still_hold(self):
        learn(self.a.resource_id, ms=20_000)
        learn(self.b.resource_id, ms=1000)
        first = self.choose(self.a, self.b)
        self.assertEqual(first.decision.resource_id, self.b.resource_id)
        self.assertFalse(self.manager.failover(failed_id=self.a.resource_id,
                                                replacement_id=None,
                                                expected_epoch=first.epoch - 1,
                                                registry=Registry(self.a, self.b),
                                                reason='timeout'))
        self.assertEqual(self.manager.snapshot()['primary_resource_id'], self.b.resource_id)


if __name__ == '__main__':
    unittest.main()
