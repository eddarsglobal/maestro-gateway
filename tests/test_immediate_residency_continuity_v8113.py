"""V8.11.3: immediate DIRECT residency continuity + evidence telemetry."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from maestro.providers.ollama.client import OllamaClient
from maestro_gateway.mission import (
    _response_origin_from_attempts,
    _semantic_recovery_attempt_count,
)
from maestro_gateway.ollama_gateway_client import (
    GatewayOllamaClient,
    MISSION_FOREGROUND,
    MISSION_STREAM_POLICY,
    _FOREGROUND_RESIDENCY_UNTIL,
    _RESIDENCY_LOCK,
    _provider_model_names,
    current_stream_metrics,
    reset_stream_metrics,
)
from maestro_gateway.primary_intelligence import PrimarySelection
from maestro_gateway.adaptive_router import RouteDecision


class CaptureClient(GatewayOllamaClient):
    def _bounded_generate_request(self, path, payload):
        self.captured = payload
        return {"done": True, "response": "OK"}


class ImmediateResidencyTests(unittest.TestCase):
    def setUp(self):
        with _RESIDENCY_LOCK:
            _FOREGROUND_RESIDENCY_UNTIL.clear()
        reset_stream_metrics()

    def tearDown(self):
        with _RESIDENCY_LOCK:
            _FOREGROUND_RESIDENCY_UNTIL.clear()
        reset_stream_metrics()

    def _capture(self, *, foreground, mode, model="neutral", resident=False):
        client = CaptureClient("http://127.0.0.1:11434")
        fg = MISSION_FOREGROUND.set(foreground)
        sp = MISSION_STREAM_POLICY.set({"mode": mode, "min_chars": 1})
        try:
            with patch.dict(os.environ, {}, clear=False):
                for key in (
                    "MAESTRO_OLLAMA_KEEP_ALIVE",
                    "MAESTRO_OLLAMA_PROBE_KEEP_ALIVE",
                    "MAESTRO_OLLAMA_RESIDENT_PRESERVE_KEEP_ALIVE",
                ):
                    os.environ.pop(key, None)
                with patch.object(client, "_provider_model_resident", return_value=resident):
                    client._request("/api/generate", {
                        "model": model,
                        "prompt": "x",
                        "options": {"num_predict": 32},
                    })
        finally:
            MISSION_STREAM_POLICY.reset(sp)
            MISSION_FOREGROUND.reset(fg)
        return client.captured, current_stream_metrics()

    def test_direct_fast_foreground_gets_12h_lease(self):
        payload, metrics = self._capture(foreground=True, mode="direct_fast")
        self.assertEqual(payload["keep_alive"], "12h")
        self.assertEqual(metrics["residency_policy"], "extended_direct_fast")

    def test_general_foreground_stays_60m(self):
        payload, metrics = self._capture(foreground=True, mode="disabled")
        self.assertEqual(payload["keep_alive"], "60m")
        self.assertEqual(metrics["residency_policy"], "foreground_standard")

    def test_cross_process_provider_resident_probe_cannot_evict(self):
        payload, metrics = self._capture(
            foreground=False, mode="disabled", model="still-hot", resident=True
        )
        self.assertEqual(payload["keep_alive"], "12h")
        self.assertTrue(metrics["provider_resident_before_request"])
        self.assertTrue(metrics["cross_process_residency_preserved"])
        self.assertEqual(metrics["residency_policy"], "probe_preserve_resident")

    def test_nonresident_probe_remains_ephemeral(self):
        payload, metrics = self._capture(
            foreground=False, mode="disabled", model="cold", resident=False
        )
        self.assertEqual(payload["keep_alive"], "0s")
        self.assertFalse(metrics["provider_resident_before_request"])
        self.assertFalse(metrics["cross_process_residency_preserved"])
        self.assertEqual(metrics["residency_policy"], "probe_ephemeral")

    def test_in_process_standard_residency_is_not_shortened(self):
        first, _ = self._capture(foreground=True, mode="disabled", model="same")
        self.assertEqual(first["keep_alive"], "60m")
        second, _ = self._capture(
            foreground=False, mode="disabled", model="same", resident=False
        )
        self.assertEqual(second["keep_alive"], "60m")

    def test_provider_ps_name_normalization_accepts_name_and_model(self):
        self.assertEqual(
            _provider_model_names({"models": [
                {"name": "a:1"}, {"model": "b:2"}, {"name": "a:1"}, {}
            ]}),
            {"a:1", "b:2"},
        )

    def test_provider_residency_check_is_read_only_ps(self):
        client = GatewayOllamaClient("http://127.0.0.1:11434")
        with patch.object(OllamaClient, "_request", return_value={
            "models": [{"name": "hot:1"}]
        }) as req:
            self.assertTrue(client._provider_model_resident("hot:1"))
            req.assert_called_once_with("/api/ps")


class EvidenceTelemetryTests(unittest.TestCase):
    def _selection(self, reason: str) -> PrimarySelection:
        return PrimarySelection(
            RouteDecision(
                resource_id="runtime::model", task_class="quick",
                prompt_budget=32, evidence_count=0, policy="test",
                qualification_status="provisional", language="en",
            ),
            epoch=1,
            reason=reason,
        )

    def test_semantic_recovery_trial_counts_as_one_attempt(self):
        self.assertEqual(
            _semantic_recovery_attempt_count(self._selection("semantic_recovery_trial")),
            1,
        )

    def test_non_recovery_selection_counts_zero(self):
        self.assertEqual(
            _semantic_recovery_attempt_count(self._selection("primary_reused")),
            0,
        )

    def test_projection_origin_is_not_mislabeled_as_model(self):
        self.assertEqual(
            _response_origin_from_attempts([{"semantic_projection_applied": True}]),
            "local_deterministic_verifier",
        )
        self.assertEqual(_response_origin_from_attempts([{}]), "local_model")


if __name__ == "__main__":
    unittest.main()
