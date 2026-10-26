from __future__ import annotations

import unittest
from types import SimpleNamespace

from maestro_gateway.adaptive_router import choose_quality_fallback
from maestro_gateway.model_qualification import (
    observe_attempt,
    reset_for_testing,
    update_verified_versions,
)
from maestro_gateway.quality_retry_contract import contract_retry_hint
from maestro_gateway.response_constraints import (evaluate_shape, explicit_two_sentences, explicit_exact_literal)
from maestro_gateway.response_guard import evaluate_response


class _Registry:
    def __init__(self, *rows):
        self._rows = rows
    def all(self):
        return self._rows


def _desc(rid: str, size: int, *, local: bool = True):
    return SimpleNamespace(
        resource_id=rid,
        local=local,
        status="AVAILABLE",
        capabilities=("DIRECT", "GENERAL_REASONING"),
        metadata={"size": size},
    )


class TwoSentenceContractTests(unittest.TestCase):
    def test_real_german_prompt_with_adjectives_is_detected(self):
        prompt = (
            "Antworte auf Deutsch. Schreibe zwei natürliche und grammatikalisch "
            "korrekte deutsche Sätze darüber, was MAESTRO ist."
        )
        self.assertTrue(explicit_two_sentences(prompt))
        result = evaluate_shape(prompt, "MAESTRO ist ein virtueller Assistent.")
        self.assertEqual(result.status, "FAIL_SHAPE")
        self.assertEqual(result.reason, "expected_two_sentences_got_1")

    def test_real_german_two_sentences_pass_shape_only(self):
        prompt = (
            "Antworte auf Deutsch. Schreibe zwei natürliche und grammatikalisch "
            "korrekte deutsche Sätze darüber, was MAESTRO ist."
        )
        result = evaluate_shape(
            prompt,
            "MAESTRO ist ein lokaler Assistent. Er unterstützt den Benutzer bei Aufgaben.",
        )
        self.assertEqual(result.status, "PASS_SHAPE")
        self.assertFalse(result.factual_truth_verified)

    def test_real_arabic_write_two_sentences_is_detected(self):
        prompt = (
            "أجب باللغة العربية. اكتب جملتين عربيتين طبيعيتين وصحيحتين نحويًا "
            "تشرحان ما هو MAESTRO."
        )
        self.assertTrue(explicit_two_sentences(prompt))
        result = evaluate_shape(
            prompt,
            "MAESTRO هو مساعد معلوماتي رقمي يمكنه مساعدتك في معرفة المعلومات والمساعدة في العديد من الأمور.",
        )
        self.assertEqual(result.status, "FAIL_SHAPE")
        self.assertEqual(result.reason, "expected_two_sentences_got_1")

    def test_arabic_two_sentences_pass_shape_only(self):
        prompt = "أجب باللغة العربية. اكتب جملتين تشرحان ما هو MAESTRO."
        answer = "MAESTRO مساعد رقمي محلي. يساعد المستخدم في تنفيذ المهام المصرح بها."
        self.assertEqual(evaluate_shape(prompt, answer).status, "PASS_SHAPE")

    def test_quality_guard_rejects_real_german_one_sentence(self):
        prompt = (
            "Antworte auf Deutsch. Schreibe zwei natürliche und grammatikalisch "
            "korrekte deutsche Sätze darüber, was MAESTRO ist."
        )
        result = evaluate_response(
            prompt,
            "MAESTRO ist ein virtueller Assistent, der mithilfe von Texte und Kenntnissen bestimmt und informiert.",
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "explicit_shape_expected_two_sentences_got_1")

    def test_quality_guard_rejects_real_arabic_one_sentence(self):
        prompt = (
            "أجب باللغة العربية. اكتب جملتين عربيتين طبيعيتين وصحيحتين نحويًا "
            "تشرحان ما هو MAESTRO."
        )
        result = evaluate_response(
            prompt,
            "MAESTRO هو مساعد معلوماتي رقمي يمكنه مساعدتك في معرفة المعلومات والمساعدة في العديد من الأمور.",
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "explicit_shape_expected_two_sentences_got_1")

    def test_german_retry_hint_is_localized(self):
        hint = contract_retry_hint("explicit_shape_expected_two_sentences_got_1", "de")
        self.assertIn("GENAU ZWEI", hint)
        self.assertIn("deutsche", hint)

    def test_arabic_retry_hint_is_localized(self):
        hint = contract_retry_hint("explicit_shape_expected_two_sentences_got_1", "ar")
        self.assertIn("جملتين", hint)
        self.assertIn("عربيتين", hint)


class ExactLiteralVsConditionalTests(unittest.TestCase):
    def test_conditional_classifier_is_not_misread_as_exact_literal(self):
        prompt = "Return exactly OUI if 4 is even, otherwise return exactly NON."
        self.assertIsNone(explicit_exact_literal(prompt))
        result = evaluate_shape(prompt, "OUI")
        self.assertEqual(result.status, "ABSTAIN")

    def test_true_exact_literal_remains_case_sensitive(self):
        prompt = "Antworte ausschließlich mit: SPRACHE-DE-OK"
        self.assertEqual(explicit_exact_literal(prompt), "SPRACHE-DE-OK")
        self.assertEqual(evaluate_shape(prompt, "SPRACHE-DE-OK").status, "PASS_SHAPE")
        mismatch = evaluate_shape(prompt, "SprACHE-DE-OK")
        self.assertEqual(mismatch.status, "FAIL_SHAPE")
        self.assertEqual(mismatch.reason, "exact_literal_case_sensitive_mismatch")


class ProvisionalLanguageFallbackTests(unittest.TestCase):
    def setUp(self):
        reset_for_testing()

    def tearDown(self):
        reset_for_testing()

    def test_no_provisional_trial_without_explicit_permission(self):
        reg = _Registry(_desc("current", 1_000_000_000), _desc("next", 3_000_000_000))
        self.assertIsNone(choose_quality_fallback(
            reg, "current", task_class="normal", language="de",
            remaining_seconds=60.0,
        ))

    def test_language_trial_is_local_only_and_bounded_to_next_capacity_step(self):
        reg = _Registry(
            _desc("current", 1_000_000_000),
            _desc("next", 3_000_000_000),
            _desc("huge", 8_000_000_000),
            _desc("cloud", 2_000_000_000, local=False),
        )
        chosen = choose_quality_fallback(
            reg, "current", task_class="normal", language="de",
            remaining_seconds=60.0, allow_provisional_language_trial=True,
        )
        self.assertEqual(chosen, "next")

    def test_known_same_language_total_failure_is_not_retried(self):
        fp = "a" * 64
        update_verified_versions({"bad": fp})
        observe_attempt("bad", "normal", "ar", "quality_rejected", 50.0,
                        model_fingerprint=fp)
        reg = _Registry(
            _desc("current", 1_000_000_000),
            _desc("bad", 2_000_000_000),
            _desc("fresh", 3_000_000_000),
        )
        chosen = choose_quality_fallback(
            reg, "current", task_class="normal", language="ar",
            remaining_seconds=60.0, allow_provisional_language_trial=True,
        )
        self.assertEqual(chosen, "fresh")

    def test_qualified_same_language_alternative_wins_before_trial(self):
        fp = "b" * 64
        update_verified_versions({"proven": fp})
        observe_attempt("proven", "normal", "de", "quality_accepted", 50.0,
                        model_fingerprint=fp)
        observe_attempt("proven", "normal", "de", "quality_accepted", 55.0,
                        model_fingerprint=fp)
        reg = _Registry(
            _desc("current", 1_000_000_000),
            _desc("trial", 2_000_000_000),
            _desc("proven", 8_000_000_000),
        )
        chosen = choose_quality_fallback(
            reg, "current", task_class="normal", language="de",
            remaining_seconds=60.0, allow_provisional_language_trial=True,
        )
        self.assertEqual(chosen, "proven")

    def test_no_trial_for_code_task(self):
        reg = _Registry(_desc("current", 1_000_000_000), _desc("next", 3_000_000_000))
        self.assertIsNone(choose_quality_fallback(
            reg, "current", task_class="code", language="de",
            remaining_seconds=60.0, allow_provisional_language_trial=True,
        ))

    def test_no_trial_for_unsupported_language(self):
        reg = _Registry(_desc("current", 1_000_000_000), _desc("next", 3_000_000_000))
        self.assertIsNone(choose_quality_fallback(
            reg, "current", task_class="normal", language="nl",
            remaining_seconds=60.0, allow_provisional_language_trial=True,
        ))


if __name__ == "__main__":
    unittest.main()
