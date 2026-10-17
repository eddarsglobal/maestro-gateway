"""V8.5.1 regression: live 2026-10-14 budget-exhaustion incident, no model I/O."""
import unittest

from maestro_gateway.response_effort import (
    bounded_response_tokens,
    quality_attempt_outcome,
    quality_retry_allowed,
)
from maestro_gateway.response_guard import evaluate_response


class GenerationBudgetV851Tests(unittest.TestCase):
    def test_normal_default_no_longer_truncated_to_256(self):
        self.assertEqual(bounded_response_tokens('normal',
                         'Explique en trois étapes et donne une limite.', 512), 512)

    def test_user_smaller_explicit_budget_not_enlarged(self):
        self.assertEqual(bounded_response_tokens('normal', 'Explique brièvement', 128), 128)
        self.assertEqual(bounded_response_tokens('normal', 'Explique brièvement', 256), 256)

    def test_complex_and_code_preserved(self):
        for task in ('complex', 'code'):
            self.assertEqual(bounded_response_tokens(task, 'Write a detailed algorithm', 1024), 1024)

    def test_one_sentence_remains_bounded(self):
        self.assertEqual(bounded_response_tokens('normal', 'Réponds en une phrase', 512), 112)

    def test_limit_ends_are_not_model_quality_failures(self):
        for reason in ('length', 'max_tokens', 'max_output_tokens', 'token_limit'):
            outcome = evaluate_response('Explique localement.',
                                        'La première étape fonctionne.',
                                        completion_reason=reason)
            self.assertFalse(outcome.accepted)
            self.assertEqual(outcome.reason, 'generation_token_limit')
            self.assertFalse(quality_retry_allowed(outcome.reason))
            self.assertEqual(quality_attempt_outcome(False, outcome.reason),
                             'generation_budget_exhausted')

    def test_other_rejections_can_still_retry_once(self):
        self.assertTrue(quality_retry_allowed('empty_output'))
        self.assertTrue(quality_retry_allowed('identity_policy_leak'))
        self.assertEqual(quality_attempt_outcome(False, 'empty_output'), 'quality_rejected')
        self.assertEqual(quality_attempt_outcome(True, 'accepted'), 'quality_accepted')

    def test_case_normalization_does_not_hide_exhaustion(self):
        self.assertFalse(quality_retry_allowed(' Generation_Token_Limit '))

if __name__ == '__main__':
    unittest.main()
