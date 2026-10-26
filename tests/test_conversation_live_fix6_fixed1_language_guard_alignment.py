from __future__ import annotations

import unittest

from maestro_gateway.response_guard import (
    asks_about_previous_language,
    evaluate_response,
)


class Fix6Fixed1LanguageGuardAlignmentTests(unittest.TestCase):
    def test_german_previous_language_answer_accepts_localized_language_name(self):
        prompt = (
            "Antworte jetzt nur auf Deutsch: Welche Sprache habe ich "
            "unmittelbar vor dieser Nachricht verlangt?"
        )
        self.assertTrue(asks_about_previous_language(prompt))
        got = evaluate_response(
            prompt,
            "Unmittelbar davor hast du Arabisch verlangt.",
        )
        self.assertTrue(got.accepted, got.reason)

    def test_arabic_previous_language_answer_accepts_localized_language_name(self):
        prompt = "أجب الآن بالعربية فقط: ما اللغة التي طلبتُ منك استخدامها مباشرة قبل هذه الرسالة؟"
        self.assertTrue(asks_about_previous_language(prompt))
        got = evaluate_response(
            prompt,
            "كانت اللغة المطلوبة مباشرةً قبل هذه الرسالة هي الألمانية.",
        )
        self.assertTrue(got.accepted, got.reason)

    def test_german_previous_language_answer_without_language_name_is_rejected(self):
        prompt = "Welche Sprache habe ich unmittelbar vor dieser Nachricht verlangt?"
        got = evaluate_response(prompt, "Das weiß ich nicht sicher.")
        self.assertFalse(got.accepted)
        self.assertEqual(got.reason, "language_question_not_answered")

    def test_arabic_previous_language_answer_without_language_name_is_rejected(self):
        prompt = "ما اللغة التي طلبتُ منك استخدامها مباشرة قبل هذه الرسالة؟"
        got = evaluate_response(prompt, "لا أعرف ذلك على وجه اليقين.")
        self.assertFalse(got.accepted)
        self.assertEqual(got.reason, "language_question_not_answered")


if __name__ == "__main__":
    unittest.main()
