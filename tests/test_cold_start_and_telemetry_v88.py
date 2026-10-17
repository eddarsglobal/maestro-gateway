"""V8.8 cold-start efficiency and live telemetry regression tests."""
from __future__ import annotations
import hashlib

import importlib.util
from dataclasses import dataclass
from pathlib import Path
import unittest

from maestro_gateway import model_qualification
from maestro_gateway.adaptive_router import choose_adaptive_resource
from maestro_gateway.ollama_gateway_client import _adaptive_num_ctx, _stream_policy_complete
from maestro_gateway.response_effort import compact_chat_instruction


@dataclass(frozen=True)
class D:
    resource_id: str
    size: int
    local: bool = True
    status: str = 'AVAILABLE'
    capabilities: tuple[str, ...] = ('DIRECT', 'GENERAL_REASONING')
    @property
    def metadata(self): return {'size': self.size}

class R:
    def __init__(self, *items): self.items = items
    def all(self): return self.items


# FIXED5.8C: synthetic immutable digests ONLY for isolated test descriptors.
_TEST_RESOURCE_IDS = ('local::heavy', 'local::light', 'local::one')
def _test_model_fingerprint(rid: str) -> str:
    return hashlib.sha256(('maestro/fixed58c/testing-only\0' + rid).encode('utf-8')).hexdigest()
def _activate_test_versions() -> None:
    model_qualification.update_verified_versions({
        rid: _test_model_fingerprint(rid) for rid in _TEST_RESOURCE_IDS
    })


def learn(rid, outcome, ms, task='normal', lang='fr'):
    assert model_qualification.observe_attempt(rid, task, lang, outcome, ms, model_fingerprint=_test_model_fingerprint(rid))


class V88ColdStartTests(unittest.TestCase):
    def setUp(self):
        model_qualification.reset_for_testing()
        _activate_test_versions()
    def tearDown(self): model_qualification.reset_for_testing()

    def test_cold_start_prefers_minimum_sufficient_candidate_over_slow_heavy_history(self):
        heavy = D('local::heavy', 5_500_000_000)
        light = D('local::light', 1_700_000_000)
        learn(heavy.resource_id, 'quality_accepted', 15000)
        learn(heavy.resource_id, 'quality_accepted', 15000)
        chosen = choose_adaptive_resource(R(heavy, light), 'Explique le sujet.', ui_language='fr', cold_start=True)
        self.assertEqual(chosen.resource_id, light.resource_id)

    def test_cold_start_keeps_heavy_model_when_real_latency_is_exceptionally_good(self):
        heavy = D('local::heavy', 5_500_000_000)
        light = D('local::light', 1_700_000_000)
        learn(heavy.resource_id, 'quality_accepted', 2500)
        learn(heavy.resource_id, 'quality_accepted', 2500)
        chosen = choose_adaptive_resource(R(heavy, light), 'Explique le sujet.', ui_language='fr', cold_start=True)
        self.assertEqual(chosen.resource_id, heavy.resource_id)

    def test_non_cold_routing_semantics_unchanged(self):
        heavy = D('local::heavy', 5_500_000_000)
        light = D('local::light', 1_700_000_000)
        learn(heavy.resource_id, 'quality_accepted', 15000)
        learn(heavy.resource_id, 'quality_accepted', 15000)
        chosen = choose_adaptive_resource(R(heavy, light), 'Explique le sujet.', ui_language='fr')
        self.assertEqual(chosen.resource_id, heavy.resource_id)


class V88UniversalInferenceTests(unittest.TestCase):
    def test_short_request_can_use_1024_context(self):
        self.assertEqual(_adaptive_num_ctx({'prompt': 'bonjour ' * 20, 'options': {'num_predict': 320}}), 1024)

    def test_structured_request_instruction_eliminates_preamble(self):
        prompt = 'Explique en trois étapes pratiques comment tester ce service. Cite une limite.'
        text = compact_chat_instruction(prompt=prompt, language_hint='French', task='normal')
        self.assertIn('exactly three numbered items', text)
        self.assertIn('No preamble or conclusion', text)

    def test_markdown_numbered_answer_can_end_stream_early(self):
        policy = {'mode': 'three_steps', 'min_chars': 60, 'requires_limit': True}
        text = ('**1.** Vérifier le port local.\n**2.** Tester depuis localhost.\n'
                '**3.** Couper le réseau externe et retester.\nLimite : ce test ne couvre pas tout.')
        self.assertTrue(_stream_policy_complete(text, policy))


class V88ValidatorTelemetryTests(unittest.TestCase):
    def test_live_validator_preserves_transport_metrics(self):
        path = Path(__file__).parents[1] / 'tools' / 'v83_live_validation.py'
        spec = importlib.util.spec_from_file_location('v83_live_validation_v88', path)
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
        result = {'ok': True, 'resource_id': 'local::one', 'output': 'Réponse.',
                  'semantic_truth_verified': False,
                  'metrics': {'response_origin': 'local_model', 'routing_language': 'fr',
                              'first_token_ms': 321.0, 'transport_wall_ms': 1250.0,
                              'adaptive_num_ctx': 1024, 'early_completion': True,
                              'prompt_tokens': 88, 'generated_tokens': 42,
                              'tokens_per_second': 33.6, 'load_ms': 12.0}}
        row = mod.assess_result(result, {'local::one'}, 'fr', 1.25)
        self.assertEqual(row['first_token_ms'], 321.0)
        self.assertEqual(row['adaptive_num_ctx'], 1024)
        self.assertTrue(row['early_completion'])


if __name__ == '__main__': unittest.main()
