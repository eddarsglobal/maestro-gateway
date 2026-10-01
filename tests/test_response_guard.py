import unittest

from maestro_gateway.response_guard import (
    evaluate_response,
)


class ResponseGuardTests(unittest.TestCase):
    def test_exact_echo_rejected(self):
        prompt = (
            "Peux-tu ouvrir mes fichiers "
            "et les corriger directement ?"
        )

        result = evaluate_response(
            prompt,
            prompt,
        )

        self.assertFalse(
            result.accepted
        )
        self.assertEqual(
            result.reason,
            "exact_prompt_echo",
        )

    def test_substantive_answer_accepted(self):
        result = evaluate_response(
            "Peux-tu ouvrir mes fichiers ?",
            (
                "Oui, après autorisation d'un "
                "workspace local je peux lire "
                "les fichiers autorisés."
            ),
        )

        self.assertTrue(
            result.accepted
        )

    def test_explicit_repeat_allowed(self):
        prompt = (
            "Répète exactement : bonjour MAESTRO"
        )

        result = evaluate_response(
            prompt,
            prompt,
        )

        self.assertTrue(
            result.accepted
        )


if __name__ == "__main__":
    unittest.main()
