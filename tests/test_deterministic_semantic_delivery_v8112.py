"""V8.11.2: deterministic DIRECT profile + proven-truth projection."""
from __future__ import annotations

import hashlib
import unittest

from maestro_gateway.response_effort import local_execution_profile
from maestro_gateway.model_qualification import update_verified_versions
from maestro_gateway.semantic_competence import (
    observe_semantic_attempt,
    reset_for_testing,
    semantic_qualification,
)
from maestro_gateway.semantic_verifier import (
    deterministic_truth_projection,
    verify_deterministic_response,
)


class DeterministicExecutionProfileTests(unittest.TestCase):
    def test_provable_direct_profile_is_provider_neutral_deterministic(self):
        p = local_execution_profile("quick", deterministic_direct=True)
        self.assertIsNotNone(p)
        self.assertEqual(p.temperature, 0.0)
        self.assertEqual(p.seed, 8112)
        self.assertFalse(p.thinking)
        self.assertTrue(p.deterministic)
        self.assertEqual(p.profile_ref, "gateway-v8.11.2-deterministic-direct")

    def test_normal_quick_profile_stays_nondeterministic(self):
        p = local_execution_profile("quick")
        self.assertIsNotNone(p)
        self.assertIsNone(p.temperature)
        self.assertIsNone(p.seed)
        self.assertFalse(p.deterministic)

    def test_manual_override_still_owns_its_execution_profile(self):
        self.assertIsNone(local_execution_profile(
            "quick", manual_override=True, deterministic_direct=True
        ))


class DeterministicTruthProjectionTests(unittest.TestCase):
    def test_proven_wrong_answer_projects_only_locally_proven_truth(self):
        v = verify_deterministic_response(
            "Return exactly OUI if 4 is even, otherwise return exactly NON.",
            "NON",
        )
        self.assertIsNotNone(v)
        self.assertFalse(v.accepted)
        self.assertEqual(deterministic_truth_projection(v), "OUI")

    def test_correct_model_answer_is_not_rewritten(self):
        v = verify_deterministic_response(
            "Compute 7 + 5. Return only the integer.", "12"
        )
        self.assertTrue(v.accepted)
        self.assertIsNone(deterministic_truth_projection(v))


class SemanticDisqualificationTests(unittest.TestCase):
    def setUp(self):
        reset_for_testing()
        self._test_digest = hashlib.sha256(b"fixed58f-v8112-test:runtime::model").hexdigest()
        update_verified_versions({"runtime::model": self._test_digest})

    def tearDown(self):
        reset_for_testing()
        update_verified_versions({})

    def test_unversioned_semantic_attempt_never_qualifies(self):
        rid = "runtime::model"
        self.assertFalse(observe_semantic_attempt(
            rid, "classification", "en", "pass", 180.0))
        self.assertEqual(semantic_qualification(
            rid, "classification", "en").attempts, 0)

    def test_one_pass_one_fail_is_not_overreacted_to(self):
        rid = "runtime::model"
        observe_semantic_attempt(rid, "classification", "en", "pass", 210.0, model_fingerprint=self._test_digest)
        observe_semantic_attempt(rid, "classification", "en", "fail", 220.0, model_fingerprint=self._test_digest)
        self.assertFalse(semantic_qualification(rid, "classification", "en").disqualified)

    def test_lucky_pass_cannot_mask_two_later_proven_failures(self):
        rid = "runtime::model"
        observe_semantic_attempt(rid, "classification", "en", "pass", 210.0, model_fingerprint=self._test_digest)
        observe_semantic_attempt(rid, "classification", "en", "fail", 220.0, model_fingerprint=self._test_digest)
        observe_semantic_attempt(rid, "classification", "en", "fail", 230.0, model_fingerprint=self._test_digest)
        q = semantic_qualification(rid, "classification", "en")
        self.assertEqual((q.attempts, q.passed, q.failed), (3, 1, 2))
        self.assertTrue(q.disqualified)


if __name__ == "__main__":
    unittest.main()
