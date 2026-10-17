"""V8.10.1 regression: acceleration must never silently truncate provider output."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from maestro_gateway.ollama_gateway_client import GatewayOllamaClient, MISSION_STREAM_POLICY
from maestro_gateway.response_effort import stream_completion_policy, quality_retry_allowed

PROMPT = ('Explique en trois étapes pratiques comment vérifier qu’un service web '
          'fonctionne entièrement sur mon ordinateur, sans cloud ni téléchargement. '
          'Cite une limite.')

class CaptureClient(GatewayOllamaClient):
    def _bounded_generate_request(self, path, payload):
        self.captured = payload
        return {'done': True, 'response': 'ok'}

class ProviderBudgetSafetyTests(unittest.TestCase):
    def capture(self, requested=320):
        client = CaptureClient('http://127.0.0.1:11434')
        policy = stream_completion_policy('normal', PROMPT, workspace_active=False, max_tokens=requested)
        token = MISSION_STREAM_POLICY.set(policy)
        try:
            with patch.dict('os.environ', {}, clear=False):
                client._request('/api/generate', {
                    'model': 'neutral', 'prompt': PROMPT,
                    'options': {'num_predict': requested},
                })
        finally:
            MISSION_STREAM_POLICY.reset(token)
        return client.captured

    def test_bounded_shape_does_not_silently_lower_num_predict(self):
        payload = self.capture(320)
        self.assertEqual(payload['options']['num_predict'], 320)

    def test_small_explicit_budget_is_preserved_exactly(self):
        payload = self.capture(80)
        self.assertEqual(payload['options']['num_predict'], 80)

    def test_generation_token_limit_remains_fail_closed(self):
        self.assertFalse(quality_retry_allowed('generation_token_limit'))

if __name__ == '__main__':
    unittest.main()
