"""V8.11.5 FIXED1 — order-independent deterministic pipe extraction."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from maestro_gateway.capabilities import CapabilityRegistry
from maestro_gateway.conversations import ConversationStore
from maestro_gateway.core import MaestroCoreBridge
from maestro_gateway.mission import GovernedDirectMissionRunner
from maestro_gateway.semantic_verifier import deterministic_expectation
from maestro_gateway.workspace import WorkspaceRegistry


class AtomicExtractionOrderV8115Fixed1Tests(unittest.TestCase):
    def test_exact_user_french_sequence_before_instruction(self):
        e = deterministic_expectation(
            "Dans alpha|beta|gamma, retourne uniquement le deuxième élément."
        )
        self.assertIsNotNone(e)
        self.assertEqual((e.subtask, e.expected), ("extraction", "beta"))

    def test_existing_english_instruction_before_sequence_still_works(self):
        e = deterministic_expectation(
            "Extract the second item from alpha|beta|gamma. Return only it."
        )
        self.assertEqual((e.subtask, e.expected), ("extraction", "beta"))

    def test_sequence_before_instruction_english(self):
        e = deterministic_expectation(
            "From alpha|beta|gamma, return only the second item."
        )
        self.assertEqual((e.subtask, e.expected), ("extraction", "beta"))

    def test_first_and_third_are_indexed_not_hardcoded_to_second(self):
        first = deterministic_expectation(
            "From alpha|beta|gamma, return only the first item."
        )
        third = deterministic_expectation(
            "Dans alpha|beta|gamma, retourne uniquement le troisième élément."
        )
        self.assertEqual(first.expected, "alpha")
        self.assertEqual(third.expected, "gamma")

    def test_no_ordinal_means_no_fake_extraction_truth(self):
        self.assertIsNone(deterministic_expectation(
            "Voici alpha|beta|gamma. Résume cette liste."
        ))

    def test_zero_model_full_mission_returns_beta_for_exact_user_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            core = MaestroCoreBridge(root / "runtime")
            core.ensure_runtime_resources = lambda **kwargs: 0
            runner = GovernedDirectMissionRunner(
                core,
                ConversationStore(root / "conversations"),
                WorkspaceRegistry(root / "workspaces.json"),
                CapabilityRegistry(),
            )
            result = runner.run(
                prompt=(
                    "Dans alpha|beta|gamma, retourne uniquement le deuxième élément."
                ),
                ui_language="fr",
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["output"], "beta")
            self.assertIsNone(result["resource_id"])
            self.assertEqual(result["metrics"]["resource_calls"], 0)
            self.assertEqual(
                result["metrics"]["response_origin"],
                "local_deterministic_core",
            )


if __name__ == "__main__":
    unittest.main()
