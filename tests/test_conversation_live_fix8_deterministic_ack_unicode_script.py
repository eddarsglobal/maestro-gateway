from __future__ import annotations
import unittest

from maestro_gateway.mission import _deterministic_core_preflight
from maestro_gateway.response_constraints import evaluate_shape


class Fix8DeterministicAckUnicodeScriptTests(unittest.TestCase):
    def test_orion_exact_ack_survives_code_classification_into_core_lane(self):
        prompt = (
            "Retiens ce code fictif pour cette conversation : ORION-47.\n"
            "Réponds uniquement OK."
        )
        task, fast, contract, subtask, candidate = _deterministic_core_preflight(
            prompt, workspace_active=False, ui_language="fr", model=None
        )
        self.assertTrue(fast)
        self.assertIsNotNone(contract)
        self.assertEqual(subtask, "exact_echo")
        self.assertEqual(candidate, "OK")

    def test_exact_ack_does_not_override_explicit_model_choice(self):
        prompt = "Retiens le code ORION-47.\nRéponds uniquement OK."
        _, fast, contract, subtask, candidate = _deterministic_core_preflight(
            prompt, workspace_active=False, ui_language="fr", model="ollama::manual"
        )
        self.assertFalse(fast)
        self.assertIsNone(candidate)

    def test_exact_ack_does_not_bypass_workspace_boundary(self):
        prompt = "Retiens le code ORION-47.\nRéponds uniquement OK."
        _, fast, contract, subtask, candidate = _deterministic_core_preflight(
            prompt, workspace_active=True, ui_language="fr", model=None
        )
        self.assertFalse(fast)
        self.assertIsNone(contract)
        self.assertIsNone(subtask)
        self.assertIsNone(candidate)

    def test_contextual_code_recall_is_not_turned_into_exact_echo(self):
        prompt = "Quel était le code fictif ?\nRéponds uniquement par le code."
        _, fast, contract, subtask, candidate = _deterministic_core_preflight(
            prompt, workspace_active=False, ui_language="fr", model=None
        )
        self.assertFalse(fast)
        self.assertIsNone(contract)
        self.assertIsNone(subtask)
        self.assertIsNone(candidate)

    def test_arabic_only_rejects_thai_letters_not_just_latin(self):
        prompt = "أجب بالعربية الفصحى الطبيعية في جملتين فقط. لا تستخدم كلمات إنجليزية."
        answer = "MAESTRO هو مساعد محلي على هذا الحاسوب. يساعدك على تحويلข้อมات وتنظيم المهام."
        got = evaluate_shape(prompt, answer)
        self.assertEqual(got.status, "FAIL_SHAPE")
        self.assertEqual(got.reason, "arabic_only_foreign_script")

    def test_arabic_only_allows_arabic_letters_and_maestro_name(self):
        prompt = "أجب بالعربية الفصحى الطبيعية في جملتين فقط. لا تستخدم كلمات إنجليزية."
        answer = "MAESTRO هو مساعد ذكاء اصطناعي محلي على هذا الحاسوب. يساعدك في تنظيم المهام والإجابة عن الأسئلة."
        got = evaluate_shape(prompt, answer)
        self.assertNotEqual(got.reason, "arabic_only_foreign_script")
        self.assertEqual(got.status, "PASS_SHAPE")


if __name__ == "__main__":
    unittest.main()
