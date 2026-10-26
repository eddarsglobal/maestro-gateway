"""Proof-driven regression: the 226-mission Mac evidence is never a training success claim."""
import json
import pathlib
import unittest

from maestro_gateway.mission import _deterministic_core_preflight
from maestro_gateway.semantic_verifier import deterministic_expectation, strict_multilingual_arithmetic
from maestro_gateway.response_constraints import explicit_exact_literal

ROOT = pathlib.Path(__file__).resolve().parents[1]
EVIDENCE = json.loads((ROOT / 'tests/fixtures/siq_root_p02_prompts.json').read_text(encoding='utf-8'))


class SovereignAtomicGovernance(unittest.TestCase):
    def test_all_30_arithmetic_cases_on_mac_now_preflight_via_core(self):
        self.assertEqual(len(EVIDENCE['arithmetic']), 30)
        for case in EVIDENCE['arithmetic']:
            with self.subTest(case=case['id']):
                task, fast, contract, subtask, candidate = _deterministic_core_preflight(
                    case['prompt'], workspace_active=False,
                    ui_language=case['language'], model=None
                )
                self.assertTrue(fast)
                self.assertEqual(subtask, 'arithmetic')
                self.assertEqual(candidate, str(case['expected']))
                self.assertEqual(contract.expected, str(case['expected']))

    def test_core_no_model_required_for_mathematical_truth(self):
        case = EVIDENCE['arithmetic'][0]
        out = _deterministic_core_preflight(case['prompt'], workspace_active=False, ui_language='en', model=None)
        self.assertEqual(out[-1], '433')

    def test_manual_model_override_remains_authoritative(self):
        for case in EVIDENCE['arithmetic']:
            with self.subTest(case=case['id']):
                out = _deterministic_core_preflight(case['prompt'], workspace_active=False,
                    ui_language=case['language'], model='ollama::manually-selected')
                self.assertIsNone(out[-1])

    def test_workspace_not_bypassed(self):
        for case in EVIDENCE['arithmetic']:
            out = _deterministic_core_preflight(case['prompt'], workspace_active=True,
                ui_language=case['language'], model=None)
            self.assertIsNone(out[-1])

    def test_croatian_false_core_echo_regressions(self):
        """P0.2 invariant survives P0.3: format nouns never become echoed answers.

        P0.2 could not yet prove order or sets and correctly abstained. P0.3
        may now execute a genuine, unique symbolic proof; this is *not* the
        old false echo of the words kodom/slovom/riječju.
        """
        prompts = EVIDENCE['croatian_false_literals']
        self.assertEqual(len(prompts), 3)
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertIsNone(explicit_exact_literal(prompt))
        # Multi-turn recall has no local proof: never fabricate prior context.
        self.assertIsNone(deterministic_expectation(prompts[0]))
        out = _deterministic_core_preflight(prompts[0], workspace_active=False,
            ui_language='hr', model=None)
        self.assertIsNone(out[-1])

    def test_croatian_symbolic_proofs_supersede_old_abstention(self):
        """New behavior must prove the task, not weaken the old protection."""
        for prompt, kind, value in zip(
            EVIDENCE['croatian_false_literals'][1:],
            ('symbolic_order', 'symbolic_intersection'),
            ('E', 'PEAR'),
        ):
            with self.subTest(kind=kind):
                contract = deterministic_expectation(prompt)
                self.assertIsNotNone(contract)
                self.assertEqual((contract.subtask, contract.expected), (kind, value))
                self.assertNotIn(value, ('kodom', 'slovom', 'riječju'))
                task, fast, resolved, subtask, output = _deterministic_core_preflight(
                    prompt, workspace_active=False, ui_language='hr', model=None)
                self.assertTrue(fast)
                self.assertEqual((subtask, output), (kind, value))
                self.assertEqual(resolved, contract)
                # No bypass of explicit model selection or Workspace rights.
                for kwargs in ({'model': 'ollama::user-selected', 'workspace_active': False},
                               {'model': None, 'workspace_active': True}):
                    denied = _deterministic_core_preflight(
                        prompt, ui_language='hr', **kwargs)
                    self.assertIsNone(denied[-1])

    def test_clear_explicit_ack_still_core(self):
        prompt = 'Retiens ce code fictif : ORION-47.\nRéponds uniquement OK.'
        out = _deterministic_core_preflight(prompt, workspace_active=False,
            ui_language='fr', model=None)
        self.assertEqual(out[-1], 'OK')

    def test_mixed_or_unproven_math_abstains(self):
        for prompt in EVIDENCE['unproven']:
            with self.subTest(prompt=prompt):
                self.assertIsNone(strict_multilingual_arithmetic(prompt))

    def test_arabic_and_chinese_no_model_verification(self):
        for code in ('ar', 'zh', 'ja', 'hi'):
            matching=[x for x in EVIDENCE['arithmetic'] if x['language']==code]
            self.assertTrue(matching)
            for c in matching:
                out = _deterministic_core_preflight(c['prompt'],workspace_active=False,ui_language=code,model=None)
                self.assertEqual(out[-1],str(c['expected']))

    def test_no_model_vendor_preference_in_proof(self):
        source=(ROOT/'src/maestro_gateway/semantic_verifier.py').read_text(encoding='utf8')
        self.assertNotIn('llama3.2:',source)
        self.assertNotIn('qwen2.5-coder:',source)

    def test_evidence_not_used_to_claim_grammar_or_fluency(self):
        source=(ROOT/'src/maestro_gateway/semantic_verifier.py').read_text(encoding='utf8')
        self.assertIn('not a multilingual grammar',source)

if __name__=='__main__': unittest.main()
