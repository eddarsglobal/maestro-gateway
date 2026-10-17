"""V8.11.5 — model churn and atomic independence certification.

The sovereign deterministic Core lane must remain operational and bit-stable
across arbitrary model inventories, including ZERO MODEL. Generative requests
still require an eligible inference resource and fail closed otherwise.
"""
from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from maestro.sovereign.contracts import (
    IntelligenceCapability,
    ResourceDescriptor,
    ResourceStatus,
)
from maestro_gateway.capabilities import CapabilityRegistry
from maestro_gateway.conversations import ConversationStore
from maestro_gateway.core import MaestroCoreBridge
from maestro_gateway.mission import (
    GovernedDirectMissionRunner,
    _deterministic_core_preflight,
)
from maestro_gateway.primary_intelligence import PrimaryIntelligenceManager
from maestro_gateway.workspace import WorkspaceRegistry


class TrapAdapter:
    """A generic future adapter that must never execute on the Core lane."""

    def __init__(self, resource_id: str, provider_id: str, model_id: str):
        self.resource_id = resource_id
        self.provider_id = provider_id
        self.model_id = model_id
        self.calls = 0

    def execute(self, request):  # pragma: no cover - failure path only
        self.calls += 1
        raise AssertionError("deterministic Core lane invoked a model adapter")


def descriptor(provider: str, model: str) -> ResourceDescriptor:
    return ResourceDescriptor(
        resource_id=f"{provider}::{model}",
        provider_id=provider,
        model_id=model,
        capabilities=frozenset({
            IntelligenceCapability.DIRECT,
            IntelligenceCapability.GENERAL_REASONING,
        }),
        status=ResourceStatus.AVAILABLE,
        local=True,
        metadata={"test": "v8.11.5-model-churn"},
    )


def runner_bundle(root: Path):
    core = MaestroCoreBridge(root / "runtime")
    # Certification controls the registry directly; no ambient host discovery.
    core.ensure_runtime_resources = lambda **kwargs: len(
        core.system.sovereign_intelligence.registry.all()
    )
    runner = GovernedDirectMissionRunner(
        core,
        ConversationStore(root / "conversations"),
        WorkspaceRegistry(root / "workspaces.json"),
        CapabilityRegistry(),
    )
    return core, runner


class ModelChurnAtomicIndependenceV8115Tests(unittest.TestCase):
    def test_preflight_proves_atomic_answer_without_model_inventory(self):
        task, fast, contract, subtask, candidate = _deterministic_core_preflight(
            "Compute 7 + 5. Return only the integer.",
            workspace_active=False,
            ui_language="en",
            model=None,
        )
        self.assertEqual(task, "quick")
        self.assertTrue(fast)
        self.assertIsNotNone(contract)
        self.assertEqual(subtask, "arithmetic")
        self.assertEqual(candidate, "12")

    def test_explicit_model_override_still_disables_atomic_shortcut(self):
        *_, candidate = _deterministic_core_preflight(
            "Compute 7 + 5. Return only the integer.",
            workspace_active=False,
            ui_language="en",
            model="future-provider::user-selected-model",
        )
        self.assertIsNone(candidate)

    def test_zero_model_full_mission_executes_arithmetic(self):
        with tempfile.TemporaryDirectory() as td:
            core, runner = runner_bundle(Path(td))
            self.assertEqual(list(core.system.sovereign_intelligence.registry.all()), [])
            result = runner.run(
                prompt="Compute 7 + 5. Return only the integer.", ui_language="en"
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["output"], "12")
            self.assertIsNone(result["resource_id"])
            self.assertEqual(result["metrics"]["resource_calls"], 0)
            self.assertFalse(result["metrics"]["model_inventory_required"])
            self.assertEqual(result["metrics"]["model_inventory_size_at_route"], 0)
            self.assertTrue(result["metrics"]["model_independent_execution"])
            self.assertEqual(
                result["metrics"]["primary_selection_reason"],
                "deterministic_core_no_model_required",
            )

    def test_zero_model_full_mission_executes_classification(self):
        with tempfile.TemporaryDirectory() as td:
            _, runner = runner_bundle(Path(td))
            result = runner.run(
                prompt=(
                    "Return exactly OUI if 4 is even, otherwise return exactly NON."
                ),
                ui_language="en",
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["output"], "OUI")
            self.assertTrue(result["semantic_truth_verified"])
            self.assertEqual(result["metrics"]["response_origin"], "local_deterministic_core")

    def test_zero_model_open_ended_request_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            _, runner = runner_bundle(Path(td))
            with self.assertRaisesRegex(RuntimeError, "Aucun modèle"):
                runner.run(
                    prompt="Explain quantum gravity in depth.", ui_language="en"
                )

    def test_atomic_output_survives_A_to_zero_to_Z_inventory_churn(self):
        with tempfile.TemporaryDirectory() as td:
            core, runner = runner_bundle(Path(td))
            registry = core.system.sovereign_intelligence.registry
            a = descriptor("future-alpha", "model-A")
            aa = TrapAdapter(a.resource_id, a.provider_id, a.model_id)
            registry.register(a, aa)
            first = runner.run(
                prompt="Compute 7 + 5. Return only the integer.", ui_language="en"
            )
            registry.unregister(a.resource_id)
            second = runner.run(
                prompt="Compute 7 + 5. Return only the integer.", ui_language="en"
            )
            z = descriptor("future-zeta", "model-Z-2099")
            zz = TrapAdapter(z.resource_id, z.provider_id, z.model_id)
            registry.register(z, zz)
            third = runner.run(
                prompt="Compute 7 + 5. Return only the integer.", ui_language="en"
            )
            self.assertEqual(
                (first["output"], second["output"], third["output"]),
                ("12", "12", "12"),
            )
            self.assertEqual((aa.calls, zz.calls), (0, 0))
            self.assertEqual(
                [x["metrics"]["resource_calls"] for x in (first, second, third)],
                [0, 0, 0],
            )
            self.assertEqual(
                [x["resource_id"] for x in (first, second, third)],
                [None, None, None],
            )

    def test_atomic_lane_does_not_select_or_mutate_primary(self):
        with tempfile.TemporaryDirectory() as td:
            core, runner = runner_bundle(Path(td))
            before = core.primary_intelligence.snapshot()
            result = runner.run(
                prompt="Compute 7 + 5. Return only the integer.", ui_language="en"
            )
            after = core.primary_intelligence.snapshot()
            self.assertIsNone(before["primary_resource_id"])
            self.assertIsNone(after["primary_resource_id"])
            self.assertIsNone(result["metrics"]["selected_primary_resource_id"])
            self.assertFalse(after["inference_ready"])

    def test_removed_primary_is_invalidated_and_replacement_is_selectable(self):
        with tempfile.TemporaryDirectory() as td:
            core, _ = runner_bundle(Path(td))
            registry = core.system.sovereign_intelligence.registry
            a = descriptor("future-alpha", "model-A")
            aa = TrapAdapter(a.resource_id, a.provider_id, a.model_id)
            registry.register(a, aa)
            selected = core.primary_intelligence.select(
                registry, "Hello", ui_language="en"
            )
            self.assertEqual(selected.decision.resource_id, a.resource_id)
            registry.unregister(a.resource_id)
            core.primary_intelligence.reconcile(registry)
            self.assertIsNone(core.primary_intelligence.snapshot()["primary_resource_id"])
            self.assertEqual(core.primary_intelligence.snapshot()["reason"], "resource_disappeared")
            z = descriptor("future-zeta", "model-Z-2099")
            zz = TrapAdapter(z.resource_id, z.provider_id, z.model_id)
            registry.register(z, zz)
            replacement = core.primary_intelligence.select(
                registry, "Hello", ui_language="en"
            )
            self.assertEqual(replacement.decision.resource_id, z.resource_id)

    def test_runtime_refresh_reconciles_added_removed_and_disappeared_models(self):
        inv_a = [{
            "id": "future-local", "name": "Future Local",
            "url": "http://127.0.0.1:19999", "status": "online",
            "models": ["model-A"], "inference_supported": True,
            "selection_enabled": True, "installed": True,
            "reason": "test", "model_fingerprints": {},
        }]
        inv_z = [{
            "id": "future-local", "name": "Future Local",
            "url": "http://127.0.0.1:19999", "status": "online",
            "models": ["model-Z-2099"], "inference_supported": True,
            "selection_enabled": True, "installed": True,
            "reason": "test", "model_fingerprints": {},
        }]
        with tempfile.TemporaryDirectory() as td:
            core = MaestroCoreBridge(Path(td) / "runtime")
            with patch(
                "maestro_gateway.core.discover_runtime_inventory",
                side_effect=[inv_a, inv_z, []],
            ):
                self.assertEqual(core.ensure_runtime_resources(force=True), 1)
                registry = core.system.sovereign_intelligence.registry
                self.assertEqual(
                    [d.resource_id for d in registry.all()], ["future-local::model-A"]
                )
                selected = core.primary_intelligence.select(
                    registry, "Hello", ui_language="en"
                )
                self.assertEqual(selected.decision.resource_id, "future-local::model-A")
                self.assertEqual(core.ensure_runtime_resources(force=True), 1)
                self.assertEqual(
                    [d.resource_id for d in registry.all()],
                    ["future-local::model-Z-2099"],
                )
                self.assertIsNone(
                    core.primary_intelligence.snapshot()["primary_resource_id"]
                )
                self.assertEqual(core.ensure_runtime_resources(force=True), 0)
                self.assertEqual(list(registry.all()), [])

    def test_generic_registry_can_register_and_unregister_unknown_future_resource(self):
        with tempfile.TemporaryDirectory() as td:
            core, _ = runner_bundle(Path(td))
            registry = core.system.sovereign_intelligence.registry
            z = descriptor("provider-never-seen-before", "model-never-seen-before")
            adapter = TrapAdapter(z.resource_id, z.provider_id, z.model_id)
            registry.register(z, adapter)
            self.assertEqual(registry.adapter(z.resource_id), adapter)
            registry.unregister(z.resource_id)
            self.assertEqual(list(registry.all()), [])

    def test_atomic_preflight_source_contains_no_model_or_provider_identity(self):
        source = inspect.getsource(_deterministic_core_preflight).casefold()
        for forbidden in (
            "qwen", "gemma", "llama", "ollama", "lm studio", "mlx",
            "openai", "anthropic", "model-a", "model-z",
        ):
            self.assertNotIn(forbidden, source)

    def test_primary_manager_zero_model_state_remains_sovereign(self):
        manager = PrimaryIntelligenceManager()

        class EmptyRegistry:
            def all(self):
                return ()

        manager.reconcile(EmptyRegistry())
        snapshot = manager.snapshot()
        self.assertIsNone(snapshot["primary_resource_id"])
        self.assertFalse(snapshot["primary_selected"])
        self.assertFalse(snapshot["cloud_enabled"])
        with self.assertRaises(RuntimeError):
            manager.select(EmptyRegistry(), "open ended", ui_language="en")


if __name__ == "__main__":
    unittest.main()
