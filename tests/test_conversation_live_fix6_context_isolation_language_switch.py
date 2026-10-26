from __future__ import annotations

import unittest

from maestro_gateway.adaptive_router import explicit_requested_language
from maestro_gateway.conversation_language_context import (
    resolve_previous_language_answer, is_previous_language_question,
)
from maestro_gateway.response_constraints import evaluate_shape
from maestro_gateway.response_effort import (
    compact_chat_instruction,
    conversation_context_required,
    isolate_unrelated_context_for_explicit_turn,
)


class Fix6ContextIsolationTests(unittest.TestCase):
    def test_self_contained_german_two_sentence_turn_isolates_unrelated_history(self):
        prompt = (
            "Antworte auf Deutsch in genau zwei natürlichen und grammatikalisch korrekten Sätzen.\n"
            "Was ist MAESTRO und wozu dient es?\n"
            "MAESTRO ist mein lokaler KI-Assistent auf diesem Computer.\n"
            "Verwende durchgehend die Anrede „du“."
        )
        self.assertTrue(isolate_unrelated_context_for_explicit_turn(prompt))
        text = compact_chat_instruction(
            prompt=prompt,
            language_hint="German",
            task="normal",
            recent_context="USER: ORION-47\nMAESTRO: ORION-47",
        )
        self.assertNotIn("ORION-47", text)
        self.assertIn("Du bist MAESTRO", text)

    def test_self_contained_arabic_two_sentence_turn_isolates_unrelated_history(self):
        prompt = (
            "أجب بالعربية الفصحى الطبيعية في جملتين فقط.\n"
            "ما هو MAESTRO وما وظيفته؟\n"
            "MAESTRO هو مساعد الذكاء الاصطناعي المحلي الخاص بي على هذا الحاسوب.\n"
            "لا تستخدم كلمات إنجليزية."
        )
        self.assertTrue(isolate_unrelated_context_for_explicit_turn(prompt))
        text = compact_chat_instruction(
            prompt=prompt,
            language_hint="Arabic",
            task="normal",
            recent_context="USER: ORION-47\nMAESTRO: ORION-47",
        )
        self.assertNotIn("ORION-47", text)
        self.assertIn("أنت MAESTRO", text)

    def test_context_dependent_recall_is_not_isolated(self):
        prompt = "Welches war mein vorheriger Code? Antworte nur mit dem Code."
        self.assertTrue(conversation_context_required(prompt))
        self.assertFalse(isolate_unrelated_context_for_explicit_turn(prompt))

    def test_german_immediately_before_phrase_is_context_dependency(self):
        prompt = "Antworte jetzt nur auf Deutsch: Welche Sprache habe ich unmittelbar vor dieser Nachricht verlangt?"
        self.assertTrue(conversation_context_required(prompt))

    def test_arabic_immediately_before_phrase_is_context_dependency(self):
        prompt = "أجب الآن بالعربية فقط: ما اللغة التي طلبتُ منك استخدامها مباشرة قبل هذه الرسالة؟"
        self.assertTrue(conversation_context_required(prompt))


class Fix6LanguageScriptContractTests(unittest.TestCase):
    def test_arabic_only_rejects_obvious_english_words(self):
        req = "أجب بالعربية الفصحى في جملتين فقط. لا تستخدم كلمات إنجليزية."
        out = "أنا MAESTRO، AI Assistant محلي على هذا الحاسوب. أساعدك في تنظيم المهام."
        got = evaluate_shape(req, out)
        self.assertEqual(got.status, "FAIL_SHAPE")
        self.assertEqual(got.reason, "arabic_only_foreign_script")

    def test_arabic_only_allows_maestro_brand(self):
        req = "أجب بالعربية الفصحى في جملتين فقط. لا تستخدم كلمات إنجليزية."
        out = "أنا MAESTRO، مساعد ذكاء اصطناعي محلي على هذا الحاسوب. أساعدك في تنظيم المهام."
        self.assertEqual(evaluate_shape(req, out).status, "PASS_SHAPE")

    def test_german_only_rejects_arabic_script(self):
        req = "Antworte nur auf Deutsch in genau zwei Sätzen."
        out = "MAESTRO ist lokal. العربية ist ebenfalls hier."
        got = evaluate_shape(req, out)
        self.assertEqual(got.status, "FAIL_SHAPE")
        self.assertEqual(got.reason, "german_only_foreign_script")


class Fix6PreviousLanguageResolverTests(unittest.TestCase):
    def test_german_question_about_previous_requested_language_uses_prior_user_turn(self):
        msgs = [
            {"role":"user","content":"أجب بالعربية الفصحى الطبيعية في جملتين فقط."},
            {"role":"assistant","content":"هذه إجابة عربية سليمة."},
        ]
        prompt = "Antworte jetzt nur auf Deutsch: Welche Sprache habe ich unmittelbar vor dieser Nachricht verlangt?"
        self.assertTrue(is_previous_language_question(prompt))
        got = resolve_previous_language_answer(prompt, msgs, "de", explicit_requested_language)
        self.assertEqual(got, "Unmittelbar davor hast du Arabisch verlangt.")

    def test_arabic_question_about_previous_requested_language_uses_prior_user_turn(self):
        msgs = [
            {"role":"user","content":"Antworte jetzt nur auf Deutsch: Welche Sprache habe ich unmittelbar vor dieser Nachricht verlangt?"},
            {"role":"assistant","content":"Unmittelbar davor hast du Arabisch verlangt."},
        ]
        prompt = "أجب الآن بالعربية فقط: ما اللغة التي طلبتُ منك استخدامها مباشرة قبل هذه الرسالة؟"
        self.assertTrue(is_previous_language_question(prompt))
        got = resolve_previous_language_answer(prompt, msgs, "ar", explicit_requested_language)
        self.assertEqual(got, "كانت اللغة المطلوبة مباشرةً قبل هذه الرسالة هي الألمانية.")


if __name__ == "__main__":
    unittest.main()
