from __future__ import annotations
import unittest

from maestro_gateway.semantic_verifier import deterministic_expectation
from maestro_gateway.response_constraints import explicit_exact_literal, evaluate_shape
from maestro_gateway.response_guard import evaluate_response
from maestro_gateway.quality_retry_contract import contract_retry_hint
from maestro_gateway.response_effort import compact_chat_instruction


class Fix7ExactAckArabicAnswerFocusTests(unittest.TestCase):
    def test_french_bare_token_exact_ack_is_deterministic(self):
        prompt = "Retiens ce code fictif pour cette conversation : ORION-47.\nRéponds uniquement OK."
        got = deterministic_expectation(prompt)
        self.assertIsNotNone(got)
        self.assertEqual(got.subtask, "exact_echo")
        self.assertEqual(got.expected, "OK")

    def test_french_bare_token_shape_is_case_sensitive(self):
        prompt = "Réponds uniquement OK."
        self.assertEqual(explicit_exact_literal(prompt), "OK")
        self.assertEqual(evaluate_shape(prompt, "OK").status, "PASS_SHAPE")
        self.assertEqual(evaluate_shape(prompt, "OK.").status, "FAIL_SHAPE")

    def test_contextual_code_instruction_is_not_misread_as_literal(self):
        prompt = "Quel était le code fictif ?\nRéponds uniquement par le code."
        self.assertIsNone(explicit_exact_literal(prompt))
        self.assertIsNone(deterministic_expectation(prompt))

    def test_arabic_user_instruction_echo_is_rejected(self):
        prompt = (
            "أجب بالعربية الفصحى الطبيعية في جملتين فقط.\n"
            "ما هو MAESTRO وما وظيفته؟\n"
            "لا تستخدم كلمات إنجليزية."
        )
        output = (
            "MAESTRO هو مساعد ذكاء اصطناعي محلي على هذا الحاسوب. "
            "لا تستخدم كلمات إنجليزية."
        )
        got = evaluate_response(prompt, output)
        self.assertFalse(got.accepted)
        self.assertEqual(got.reason, "user_instruction_echo")

    def test_arabic_semantic_answer_without_instruction_echo_is_not_rejected_for_echo(self):
        prompt = (
            "أجب بالعربية الفصحى الطبيعية في جملتين فقط.\n"
            "ما هو MAESTRO وما وظيفته؟\n"
            "لا تستخدم كلمات إنجليزية."
        )
        output = (
            "MAESTRO مساعد ذكاء اصطناعي محلي يعمل على هذا الحاسوب. "
            "يساعد المستخدم في الإجابة عن الأسئلة وتنظيم المهام اليومية."
        )
        got = evaluate_response(prompt, output)
        self.assertNotEqual(got.reason, "user_instruction_echo")
        self.assertTrue(got.accepted)

    def test_arabic_retry_hint_is_internal_and_not_copyable_user_sentence(self):
        hint = contract_retry_hint("explicit_shape_arabic_only_foreign_script", "ar")
        self.assertIn("قاعدة داخلية", hint)
        self.assertIn("لا تذكرها", hint)
        self.assertNotIn("لا تستخدم كلمات إنجليزية", hint)

    def test_two_sentence_scaffold_forbids_instruction_restatement(self):
        prompt = "Antworte auf Deutsch in genau zwei Sätzen. Was ist MAESTRO?"
        text = compact_chat_instruction(prompt=prompt, language_hint="German", task="normal")
        self.assertIn("wiederhole keine Nutzeranweisung", text)


if __name__ == "__main__":
    unittest.main()
