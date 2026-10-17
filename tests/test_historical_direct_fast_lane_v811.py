"""V8.11: restore MAESTRO's historical governed warm-DIRECT latency lane."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from maestro_gateway.ollama_gateway_client import (
    GatewayOllamaClient,
    MISSION_FOREGROUND,
    MISSION_STREAM_POLICY,
)
from maestro_gateway.response_effort import (
    direct_fast_instruction,
    direct_fast_lane_eligible,
    stream_completion_policy,
)


class CaptureClient(GatewayOllamaClient):
    def _bounded_generate_request(self, path, payload):
        self.captured = payload
        return {"done": True, "response": "12"}


class HistoricalDirectFastLaneV811Tests(unittest.TestCase):
    def test_exact_short_french_request_is_eligible(self):
        self.assertTrue(direct_fast_lane_eligible(
            "Calcule 7 + 5. Réponds uniquement par le nombre.", "quick",
            workspace_active=False,
        ))

    def test_exact_short_english_request_is_eligible(self):
        self.assertTrue(direct_fast_lane_eligible(
            "Return exactly MAESTRO-DIRECT-OK and nothing else.", "quick",
            workspace_active=False,
        ))

    def test_context_sensitive_request_is_not_eligible(self):
        self.assertFalse(direct_fast_lane_eligible(
            "Return only what you said in your previous answer.", "quick",
            workspace_active=False,
        ))

    def test_workspace_never_uses_direct_fast_lane(self):
        self.assertFalse(direct_fast_lane_eligible(
            "Return only the filename.", "quick", workspace_active=True,
        ))

    def test_complex_three_step_request_never_uses_direct_fast_lane(self):
        self.assertFalse(direct_fast_lane_eligible(
            "Explain in three steps and return only the answer.", "normal",
            workspace_active=False,
        ))

    def test_direct_prompt_is_minimal_and_keeps_current_request_marker(self):
        prompt = "Calcule 7 + 5. Réponds uniquement par le nombre."
        text = direct_fast_instruction(prompt=prompt, language_hint="French")
        self.assertIn("CURRENT USER REQUEST", text)
        self.assertIn(prompt, text)
        self.assertNotIn("CONTEXT:", text)
        self.assertLess(len(text), 240)

    def test_direct_policy_never_changes_provider_generation_budget(self):
        prompt = "Calcule 7 + 5. Réponds uniquement par le nombre."
        policy = stream_completion_policy(
            "quick", prompt, workspace_active=False, max_tokens=320,
            direct_fast_lane=True,
        )
        client = CaptureClient("http://127.0.0.1:11434")
        fg = MISSION_FOREGROUND.set(True)
        sp = MISSION_STREAM_POLICY.set(policy)
        try:
            with patch.dict(os.environ, {}, clear=False):
                client._request("/api/generate", {
                    "model": "neutral",
                    "prompt": direct_fast_instruction(prompt=prompt, language_hint="French"),
                    "options": {"num_predict": 320},
                })
        finally:
            MISSION_STREAM_POLICY.reset(sp)
            MISSION_FOREGROUND.reset(fg)
        self.assertEqual(client.captured["options"]["num_predict"], 320)
        self.assertEqual(client.captured["options"]["num_ctx"], 512)
        self.assertEqual(client.captured["keep_alive"], "12h")

    def test_general_path_remains_unchanged(self):
        prompt = "Explique ce concept avec suffisamment de contexte."
        self.assertFalse(direct_fast_lane_eligible(prompt, "normal", workspace_active=False))
        policy = stream_completion_policy(
            "normal", prompt, workspace_active=False, max_tokens=320,
            direct_fast_lane=False,
        )
        self.assertEqual(policy["mode"], "disabled")


if __name__ == "__main__":
    unittest.main()
