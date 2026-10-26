from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from maestro_gateway.adaptive_router import (
    RouteDecision,
    classify_request,
    explicit_requested_language,
    response_language_hint,
)
from maestro_gateway.primary_intelligence import PrimaryIntelligenceManager
from maestro_gateway.response_constraints import evaluate_shape, explicit_exact_literal
from maestro_gateway.response_effort import (
    conversation_context_required,
    direct_fast_lane_eligible,
)


class ConversationContextRoutingTests(unittest.TestCase):
    def test_orion_recall_requires_context(self):
        prompt = "Quel était le code fictif ?\nRéponds uniquement par le code."
        self.assertTrue(conversation_context_required(prompt))
        self.assertEqual(classify_request(prompt, ui_language="fr"), "normal")
        self.assertFalse(direct_fast_lane_eligible(prompt, "normal"))

    def test_exact_format_does_not_erase_context_dependency(self):
        prompt = "Return only what you said in your previous answer."
        self.assertTrue(conversation_context_required(prompt))
        self.assertFalse(direct_fast_lane_eligible(prompt, "quick"))

    def test_german_recall_is_normal(self):
        prompt = "Welches war mein vorheriger Code? Antworte nur mit dem Code."
        self.assertTrue(conversation_context_required(prompt))
        self.assertEqual(classify_request(prompt, ui_language="de"), "normal")

    def test_croatian_recall_is_normal(self):
        prompt = "Koji je bio moj prethodni kod? Odgovori samo kodom."
        self.assertTrue(conversation_context_required(prompt))
        self.assertEqual(classify_request(prompt, ui_language="hr"), "normal")

    def test_arabic_recall_is_normal(self):
        prompt = "ماذا قلت عن الرمز السابق؟ أجب فقط بالرمز."
        self.assertTrue(conversation_context_required(prompt))
        self.assertEqual(classify_request(prompt, ui_language="ar"), "normal")

    def test_arithmetic_fast_lane_remains_available(self):
        prompt = "Calcule 17 + 25. Réponds uniquement par le nombre."
        task = classify_request(prompt, ui_language="fr")
        self.assertEqual(task, "quick")
        self.assertTrue(direct_fast_lane_eligible(prompt, task))

    def test_real_programming_request_still_routes_code(self):
        self.assertEqual(
            classify_request("Écris du code Python pour lire un fichier.", ui_language="fr"),
            "code",
        )


class ExplicitLanguageRoutingTests(unittest.TestCase):
    CASES = (
        ("Réponds uniquement en français.", "French", "fr"),
        ("Antworte ab jetzt auf Deutsch.", "German", "de"),
        ("Od sada odgovaraj na hrvatskom.", "Croatian", "hr"),
        ("Da ora rispondi in italiano.", "Italian", "it"),
        ("A partir de ahora responde en español.", "Spanish", "es"),
        ("A partir de agora responde em português.", "Portuguese", "pt"),
        ("من الآن فصاعدًا أجب باللغة العربية.", "Arabic", "ar"),
        ("Answer from now on in English.", "English", "en"),
    )

    def test_explicit_language_requests_are_detected_and_normal(self):
        for prompt, language, _ in self.CASES:
            with self.subTest(language=language):
                self.assertEqual(explicit_requested_language(prompt), language)
                self.assertEqual(response_language_hint(prompt, "fr"), language)
                self.assertEqual(classify_request(prompt, ui_language="fr"), "normal")
                self.assertFalse(direct_fast_lane_eligible(prompt, "normal"))

    def test_explicit_language_overrides_ui_hint(self):
        self.assertEqual(
            response_language_hint("Antworte ab jetzt auf Deutsch.", "fr"), "German"
        )

    def test_non_language_prompt_keeps_existing_hint_behavior(self):
        self.assertEqual(response_language_hint("Bonjour, comment ça va ?", "en"), "French")

    def test_proven_same_language_alternative_can_replace_cross_language_primary(self):
        old = SimpleNamespace(resource_id="old", local=True, status="AVAILABLE", capabilities=("DIRECT",))
        german = SimpleNamespace(resource_id="german", local=True, status="AVAILABLE", capabilities=("DIRECT",))
        registry = SimpleNamespace(all=lambda: [old, german])
        q_old = SimpleNamespace(qualified=False, attempts=0, accepted=0, p95_ms=None)
        q_german = SimpleNamespace(qualified=True, attempts=4, accepted=4, p95_ms=900.0)
        cross = SimpleNamespace(accepted=5)
        manager = PrimaryIntelligenceManager()
        manager._primary = "old"
        decision = RouteDecision(
            resource_id="german", task_class="normal", prompt_budget=512,
            evidence_count=4, qualification_status="operationally_qualified", language="de",
        )
        def q(rid, task, language):
            self.assertEqual(task, "normal")
            self.assertEqual(language, "de")
            return q_german if rid == "german" else q_old
        with patch("maestro_gateway.primary_intelligence.qualification", side_effect=q), \
             patch("maestro_gateway.primary_intelligence.task_qualification", return_value=cross), \
             patch("maestro_gateway.primary_intelligence.choose_adaptive_resource", return_value=decision):
            selected = manager.select(registry, "Antworte ab jetzt auf Deutsch.", ui_language="fr")
        self.assertEqual(selected.decision.resource_id, "german")
        self.assertEqual(selected.reason, "task_language_evidence")


class ExactLiteralContractTests(unittest.TestCase):
    def test_german_exact_literal_is_case_sensitive(self):
        prompt = "Wechsle jetzt wieder auf Deutsch.\nAntworte ausschließlich mit: SPRACHE-DE-OK"
        self.assertEqual(explicit_exact_literal(prompt), "SPRACHE-DE-OK")
        self.assertEqual(evaluate_shape(prompt, "SPRACHE-DE-OK").status, "PASS_SHAPE")
        result = evaluate_shape(prompt, "SprACHE-DE-OK")
        self.assertEqual(result.status, "FAIL_SHAPE")
        self.assertEqual(result.reason, "exact_literal_case_sensitive_mismatch")

    def test_english_exact_literal(self):
        prompt = "Respond only with: HELLO-OK"
        self.assertEqual(explicit_exact_literal(prompt), "HELLO-OK")
        self.assertEqual(evaluate_shape(prompt, "HELLO-OK").status, "PASS_SHAPE")

    def test_french_exact_literal(self):
        prompt = "Réponds uniquement avec: FR-OK"
        self.assertEqual(explicit_exact_literal(prompt), "FR-OK")
        self.assertEqual(evaluate_shape(prompt, "FR-OK").status, "PASS_SHAPE")

    def test_arabic_exact_literal(self):
        prompt = "أجب فقط بـ: AR-OK"
        self.assertEqual(explicit_exact_literal(prompt), "AR-OK")
        self.assertEqual(evaluate_shape(prompt, "AR-OK").status, "PASS_SHAPE")


if __name__ == "__main__":
    unittest.main()
