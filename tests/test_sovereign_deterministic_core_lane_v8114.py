"""V8.11.4 — sovereign deterministic Core lane.

Locally provable tiny DIRECT requests must not cold-switch or invoke an LLM.
Unsupported/open-ended requests remain on the ordinary governed model path.
"""
from __future__ import annotations

import unittest
from dataclasses import dataclass

from maestro_gateway.mission import (
    _deterministic_core_allowed,
    _deterministic_core_candidate,
    _response_origin_from_attempts,
    _routing_semantic_subtask,
    _should_note_model_success,
)
from maestro_gateway.semantic_verifier import deterministic_expectation


@dataclass(frozen=True)
class DummySelection:
    epoch: int = 1


class SovereignDeterministicCoreLaneTests(unittest.TestCase):
    def test_classification_truth_is_directly_executable(self):
        contract = deterministic_expectation(
            "Return exactly OUI if 4 is even, otherwise return exactly NON."
        )
        self.assertEqual(_deterministic_core_candidate(contract), "OUI")

    def test_arithmetic_truth_is_directly_executable(self):
        contract = deterministic_expectation("Compute 7 + 5. Return only the integer.")
        self.assertEqual(_deterministic_core_candidate(contract), "12")

    def test_exact_echo_truth_is_directly_executable(self):
        contract = deterministic_expectation(
            "Return exactly MAESTRO-DIRECT-OK and nothing else."
        )
        self.assertEqual(_deterministic_core_candidate(contract), "MAESTRO-DIRECT-OK")


    def test_explicit_model_override_disables_core_shortcut(self):
        contract = deterministic_expectation("Compute 7 + 5. Return only the integer.")
        self.assertTrue(_deterministic_core_allowed(contract, None))
        self.assertTrue(_deterministic_core_allowed(contract, ""))
        self.assertFalse(_deterministic_core_allowed(contract, "ollama::chosen"))

    def test_open_ended_prompt_has_no_core_shortcut(self):
        contract = deterministic_expectation(
            "Explain the tradeoffs of this architecture in depth."
        )
        self.assertIsNone(contract)
        self.assertIsNone(_deterministic_core_candidate(contract))

    def test_locally_executable_truth_suppresses_semantic_model_switch(self):
        contract = deterministic_expectation(
            "Return exactly OUI if 4 is even, otherwise return exactly NON."
        )
        self.assertIsNone(_routing_semantic_subtask(contract, "classification"))

    def test_unsupported_truth_keeps_semantic_routing_evidence(self):
        self.assertEqual(
            _routing_semantic_subtask(None, "classification"),
            "classification",
        )

    def test_core_answer_is_not_mislabeled_as_model_output(self):
        self.assertEqual(
            _response_origin_from_attempts([], deterministic_core=True),
            "local_deterministic_core",
        )
        self.assertEqual(_response_origin_from_attempts([]), "local_model")

    def test_core_answer_cannot_fabricate_model_success_evidence(self):
        selection = DummySelection()
        self.assertFalse(_should_note_model_success(
            ok=True, primary_selection=selection, resource_calls=0
        ))
        self.assertTrue(_should_note_model_success(
            ok=True, primary_selection=selection, resource_calls=1
        ))
        self.assertFalse(_should_note_model_success(
            ok=False, primary_selection=selection, resource_calls=1
        ))


if __name__ == "__main__":
    unittest.main()
