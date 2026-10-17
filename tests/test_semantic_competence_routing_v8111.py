"""V8.11.1: deterministic semantic guard + subtask competence routing."""
from __future__ import annotations
import hashlib

import unittest
from dataclasses import dataclass

from maestro_gateway import model_qualification
from maestro_gateway.adaptive_router import choose_quality_fallback
from maestro_gateway.primary_intelligence import PrimaryIntelligenceManager
from maestro_gateway.semantic_competence import (
    observe_semantic_attempt,
    reset_for_testing as reset_semantic,
    semantic_qualification,
)
from maestro_gateway.semantic_verifier import (
    deterministic_expectation,
    verify_deterministic_response,
)


@dataclass(frozen=True)
class D:
    resource_id: str
    local: bool = True
    capabilities: tuple[str, ...] = ("DIRECT",)
    status: str = "AVAILABLE"
    size: int = 1_500_000_000

    @property
    def metadata(self):
        return {"size": self.size}


class R:
    def __init__(self, *items):
        self.items = items

    def all(self):
        return self.items


# FIXED5.8C: synthetic immutable digests ONLY for isolated test descriptors.
_TEST_RESOURCE_IDS = ('runtime-a::A', 'runtime-b::B')
def _test_model_fingerprint(rid: str) -> str:
    return hashlib.sha256(('maestro/fixed58c/testing-only\0' + rid).encode('utf-8')).hexdigest()
def _activate_test_versions() -> None:
    model_qualification.update_verified_versions({
        rid: _test_model_fingerprint(rid) for rid in _TEST_RESOURCE_IDS
    })


def operational(rid: str, *, task="quick", lang="fr", count=3, ms=500.0):
    for _ in range(count):
        assert model_qualification.observe_attempt(rid, task, lang, "quality_accepted", ms, model_fingerprint=_test_model_fingerprint(rid))


class SemanticVerifierV8111Tests(unittest.TestCase):
    def test_even_classification_is_locally_provable(self):
        e = deterministic_expectation(
            "Return exactly OUI if 4 is even, otherwise return exactly NON."
        )
        self.assertIsNotNone(e)
        self.assertEqual(e.subtask, "classification")
        self.assertEqual(e.expected, "OUI")

    def test_prime_classification_is_locally_provable(self):
        e = deterministic_expectation(
            "Return exactly OUI if 29 is prime, otherwise return exactly NON."
        )
        self.assertEqual(e.expected, "OUI")

    def test_arithmetic_is_locally_provable(self):
        e = deterministic_expectation("Compute 7 + 5. Return only the integer.")
        self.assertEqual((e.subtask, e.expected), ("arithmetic", "12"))

    def test_sorting_is_locally_provable(self):
        e = deterministic_expectation(
            "Sort these integers ascending: 9, 1, 7, 3. Return only the result."
        )
        self.assertEqual((e.subtask, e.expected), ("sorting", "1,3,7,9"))

    def test_extraction_is_locally_provable(self):
        e = deterministic_expectation(
            "Extract the second item from alpha|beta|gamma. Return only it."
        )
        self.assertEqual((e.subtask, e.expected), ("extraction", "beta"))

    def test_exact_echo_is_locally_provable(self):
        e = deterministic_expectation(
            "Return exactly MAESTRO-DIRECT-OK and nothing else."
        )
        self.assertEqual((e.subtask, e.expected), ("exact_echo", "MAESTRO-DIRECT-OK"))

    def test_wrong_deterministic_answer_is_rejected(self):
        v = verify_deterministic_response(
            "Return exactly OUI if 4 is even, otherwise return exactly NON.", "NON"
        )
        self.assertFalse(v.accepted)
        self.assertEqual(v.subtask, "classification")

    def test_harmless_terminal_punctuation_does_not_change_semantic_truth(self):
        v = verify_deterministic_response(
            "Return exactly OUI if 4 is even, otherwise return exactly NON.", "OUI."
        )
        self.assertTrue(v.accepted)

    def test_unsupported_open_question_has_no_fake_local_truth(self):
        self.assertIsNone(deterministic_expectation(
            "Explique pourquoi cette architecture est intéressante."
        ))


class SemanticRoutingV8111Tests(unittest.TestCase):
    def setUp(self):
        model_qualification.reset_for_testing()
        _activate_test_versions()
        reset_semantic()
        self.a = D("runtime-a::A", size=1_400_000_000)
        self.b = D("runtime-b::B", size=1_600_000_000)

    def tearDown(self):
        model_qualification.reset_for_testing()
        reset_semantic()

    def test_old_semantic_history_with_no_fingerprint_cannot_qualify(self):
        import json
        import tempfile
        from pathlib import Path
        from maestro_gateway.semantic_competence import warm_semantic_qualifications
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'CHAT-legacy.json'
            path.write_text(json.dumps({'messages':[{'role':'system','metadata':{
                'mission':{'metrics':{'routing_language':'en','model_attempt_metrics':[
                    {'resource_id':self.a.resource_id,'semantic_subtask':'classification',
                     'semantic_outcome':'pass','wall_ms':100.0},
                    {'resource_id':self.a.resource_id,'semantic_subtask':'classification',
                     'semantic_outcome':'pass','wall_ms':110.0}]}}}}]}))
            self.assertEqual(warm_semantic_qualifications(Path(td)),0)
            self.assertEqual(semantic_qualification(
                self.a.resource_id,'classification','en').attempts,0)

    def test_one_proven_wrong_exact_answer_disqualifies_only_that_subtask(self):
        self.assertTrue(observe_semantic_attempt(
            self.a.resource_id, "classification", "en", "fail", 220.0,
            model_fingerprint=_test_model_fingerprint(self.a.resource_id)
        ))
        q = semantic_qualification(self.a.resource_id, "classification", "en")
        self.assertTrue(q.disqualified)
        self.assertFalse(semantic_qualification(
            self.a.resource_id, "arithmetic", "en").disqualified)

    def test_two_verified_correct_answers_qualify_subtask(self):
        for ms in (230.0, 240.0):
            self.assertTrue(observe_semantic_attempt(
                self.b.resource_id, "classification", "en", "pass", ms,
                model_fingerprint=_test_model_fingerprint(self.b.resource_id)
            ))
        self.assertTrue(semantic_qualification(
            self.b.resource_id, "classification", "en").qualified)

    def test_language_change_alone_does_not_cold_switch_proven_primary(self):
        p = PrimaryIntelligenceManager()
        first = p.select(R(self.a, self.b), "Hello", ui_language="en")
        active = first.decision.resource_id
        other = self.b.resource_id if active == self.a.resource_id else self.a.resource_id
        operational(active, task="quick", lang="en", count=1, ms=300)
        operational(other, task="quick", lang="fr", count=3, ms=200)
        second = p.select(R(self.a, self.b), "Salut", ui_language="fr")
        self.assertEqual(second.decision.resource_id, active)
        self.assertEqual(second.reason, "primary_reused")

    def test_proven_semantic_candidate_can_replace_unknown_primary(self):
        p = PrimaryIntelligenceManager()
        first = p.select(R(self.a), "Salut", ui_language="fr")
        self.assertEqual(first.decision.resource_id, self.a.resource_id)
        operational(self.b.resource_id, lang="fr", count=3, ms=350)
        for _ in range(2):
            observe_semantic_attempt(
                self.b.resource_id, "classification", "fr", "pass", 350.0,
                model_fingerprint=_test_model_fingerprint(self.b.resource_id)
            )
        selected = p.select(
            R(self.a, self.b),
            "Réponds exactement OUI si 4 est pair, sinon réponds exactement NON.",
            ui_language="fr", semantic_subtask="classification",
        )
        self.assertEqual(selected.decision.resource_id, self.b.resource_id)
        self.assertEqual(selected.reason, "semantic_competence_evidence")

    def test_known_wrong_primary_gets_one_bounded_semantic_recovery_trial(self):
        p = PrimaryIntelligenceManager()
        p.select(R(self.a), "Hello", ui_language="en")
        observe_semantic_attempt(
            self.a.resource_id, "classification", "en", "fail", 210.0,
            model_fingerprint=_test_model_fingerprint(self.a.resource_id)
        )
        selected = p.select(
            R(self.a, self.b),
            "Return exactly OUI if 4 is even, otherwise return exactly NON.",
            ui_language="en", semantic_subtask="classification",
        )
        self.assertEqual(selected.decision.resource_id, self.b.resource_id)
        self.assertEqual(selected.reason, "semantic_recovery_trial")

    def test_semantic_failover_does_not_globally_quarantine_model(self):
        p = PrimaryIntelligenceManager()
        first = p.select(R(self.a), "Hello", ui_language="en")
        self.assertTrue(p.failover(
            failed_id=self.a.resource_id,
            replacement_id=self.b.resource_id,
            expected_epoch=first.epoch,
            registry=R(self.a, self.b),
            reason="semantic_rejected",
        ))
        self.assertEqual(p.snapshot()["cooling_down_models"], 0)

    def test_quality_fallback_excludes_known_bad_semantic_candidate(self):
        operational(self.a.resource_id, lang="en", count=3, ms=300)
        operational(self.b.resource_id, lang="en", count=3, ms=400)
        observe_semantic_attempt(
            self.b.resource_id, "classification", "en", "fail", 400.0,
            model_fingerprint=_test_model_fingerprint(self.b.resource_id)
        )
        # A is current so B is the only alternative, but B is deterministically
        # known bad for this subtask and must not be retried.
        self.assertIsNone(choose_quality_fallback(
            R(self.a, self.b), self.a.resource_id,
            task_class="quick", language="en",
            remaining_seconds=30.0, semantic_subtask="classification",
        ))


if __name__ == "__main__":
    unittest.main()
