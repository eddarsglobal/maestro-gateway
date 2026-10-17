"""FIXED2 — fail-closed extraction binding; no model/cloud required."""
from __future__ import annotations
import unittest

from maestro_gateway.semantic_verifier import (
    deterministic_expectation,
    deterministic_truth_projection,
    verify_deterministic_response,
)


class AtomicExtractionSafetyV8115Fixed2(unittest.TestCase):
    def _expect(self, prompt: str, answer: str | None):
        result = deterministic_expectation(prompt)
        self.assertEqual(result.expected if result else None, answer, prompt)
        if answer is not None:
            self.assertEqual(result.subtask, 'extraction', prompt)

    def test_requested_extractions(self):
        cases = [
            ("Dans alpha|beta|gamma, retourne uniquement le deuxième élément.", 'beta'),
            ("Extract the second item from alpha|beta|gamma. Return only it.", 'beta'),
            ("From alpha|beta|gamma, return only the second item.", 'beta'),
            ("Dans alpha|beta|gamma, retourne uniquement le troisième élément.", 'gamma'),
            ("From alpha|beta|gamma, return only the first item.", 'alpha'),
            ("Dans alpha|beta|gamma, pas le premier élément ; retourne le deuxième élément.", 'beta'),
            ("Dans alpha|beta|gamma, retourne le deuxième élément, pas le premier.", 'beta'),
            ("Voici un exemple x|y|z. Dans alpha|beta|gamma, retourne le deuxième élément.", 'beta'),
            ("Dans alpha|beta|gamma, retourne le 2e élément.", 'beta'),
            ("Dans alpha|beta|gamma, retourne le 2ème élément.", 'beta'),
            ("Dans alpha|beta|gamma, retourne le 3e élément.", 'gamma'),
            ("From alpha|beta|gamma, return the second item, not the first item.", 'beta'),
            ("Not the first item; from alpha|beta|gamma, return the second item.", 'beta'),
            ("In alpha|beta|gamma, gib das zweite Element.", 'beta'),
            ("De alpha|beta|gamma, devuelve el segundo elemento.", 'beta'),
            ("Da alpha|beta|gamma, estrai il secondo elemento.", 'beta'),
            ("De alpha|beta|gamma, devolve o segundo elemento.", 'beta'),
            ("Iz alpha|beta|gamma, vrati drugi element.", 'beta'),
            ("من alpha|beta|gamma، استخرج العنصر الثاني.", 'beta'),
            ("From alpha|beta|gamma, pick the third element.", 'gamma'),
            ("From alpha|beta|gamma, do not return the first item; return the second item.", 'beta'),
            ("From alpha|beta|gamma, return the second item instead of the first item.", 'beta'),
            ("From alpha|beta|gamma, return the third item, not the first item.", 'gamma'),
        ]
        for prompt, expected in cases:
            with self.subTest(prompt=prompt):
                self._expect(prompt, expected)

    def test_refuse_unsafe_or_underspecified_extractions(self):
        cases = [
            "Voici alpha|beta|gamma. Résume cette liste.",
            "First element from alpha|beta|gamma.",
            "Dans alpha|beta|gamma, le deuxième élément est différent du premier.",
            "From alpha|beta|gamma, return the first or second item.",
            "From alpha|beta|gamma, return the second item or third item.",
            "From alpha|beta|gamma, return the first item and the second item.",
            "From alpha|beta|gamma, return the second item and then explain why.",
            "Dans alpha|beta|gamma, retourne le deuxième élément et explique pourquoi.",
            "From alpha|beta|gamma, do not return the first item.",
            "Dans alpha|beta|gamma, ne retourne pas le premier élément.",
            "From alpha|beta|gamma, do not return the second item; return the second item.",
            "Compare alpha|beta|gamma with x|y|z then return the second item.",
            "From alpha|beta|gamma and from x|y|z, return the second item.",
            "x|y|z alpha|beta|gamma, return the second item.",
            "From alpha|beta|gamma, return the fifth item.",
            "From alpha|beta|gamma, return the fourth item.",
            "From alpha|beta|gamma, return the third item and second item.",
            "From alpha|beta|gamma, return the second element and summarize the list.",
            "From a|b, return the third item.",
        ]
        for prompt in cases:
            with self.subTest(prompt=prompt):
                self._expect(prompt, None)

    def test_verification_and_projection_only_of_proven_target(self):
        prompt = "Dans alpha|beta|gamma, retourne uniquement le deuxième élément."
        bad = verify_deterministic_response(prompt, 'alpha')
        self.assertIsNotNone(bad)
        self.assertFalse(bad.accepted)
        self.assertEqual(deterministic_truth_projection(bad), 'beta')
        good = verify_deterministic_response(prompt, 'beta')
        self.assertTrue(good.accepted)
        self.assertIsNone(deterministic_truth_projection(good))
        unsupported = verify_deterministic_response(
            "From alpha|beta|gamma, return the first or second item.", 'alpha'
        )
        self.assertIsNone(unsupported)
        self.assertIsNone(deterministic_truth_projection(unsupported))

    def test_other_fast_lane_contracts_do_not_regress(self):
        e = deterministic_expectation('Calcule 17 + 25. Réponds uniquement par le nombre.')
        self.assertEqual(e.expected, '42')
        e = deterministic_expectation('Réponds uniquement OUI si 29 est un nombre premier, sinon NON.')
        self.assertEqual(e.expected, 'OUI')


if __name__ == '__main__':
    unittest.main()
