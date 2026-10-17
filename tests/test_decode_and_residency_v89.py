"""V8.9 provider-neutral decode-bounding and Ollama residency regression tests."""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from maestro_gateway.ollama_gateway_client import (
    GatewayOllamaClient, MISSION_FOREGROUND, MISSION_STREAM_POLICY,
    _stream_policy_complete, current_stream_metrics, reset_stream_metrics,
)
from maestro_gateway.response_effort import (
    bounded_response_tokens, compact_chat_instruction, stream_completion_policy,
)

PROMPT = ('Explique en trois étapes pratiques comment vérifier qu’un service web '
          'fonctionne entièrement sur mon ordinateur, sans cloud ni téléchargement. '
          'Cite une limite.')


class V89DecodeBudgetTests(unittest.TestCase):
    def test_three_step_shape_keeps_caller_budget_and_relies_on_early_stop(self):
        self.assertEqual(bounded_response_tokens('normal', PROMPT, 320), 320)

    def test_general_normal_budget_is_not_globally_reduced(self):
        self.assertEqual(bounded_response_tokens('normal', 'Explique ce sujet.', 320), 320)
        self.assertEqual(bounded_response_tokens('normal', 'Explique ce sujet.', 900), 512)

    def test_structured_instruction_requests_four_short_lines(self):
        text = compact_chat_instruction(prompt=PROMPT, language_hint='French', task='normal')
        self.assertIn('at most 12 words', text)
        self.assertIn("beginning 'Limite:'", text)
        self.assertIn('No preamble or conclusion', text)
        self.assertIn('CURRENT USER REQUEST', text)
        self.assertLess(len(text), 760)

    def test_completion_floor_no_longer_forces_150_characters(self):
        policy = stream_completion_policy('normal', PROMPT, workspace_active=False, max_tokens=320)
        self.assertEqual(policy['mode'], 'three_steps')
        self.assertEqual(policy['min_chars'], 64)
        complete = ('1. Vérifier le port local.\n2. Tester localhost.\n3. Couper Internet et retester.\n'
                    'Limite : certaines dépendances peuvent rester cachées.')
        self.assertTrue(_stream_policy_complete(complete, policy))


class V89ResidencyTests(unittest.TestCase):
    def _capture(self, foreground: bool, env: dict[str, str] | None = None):
        class CaptureClient(GatewayOllamaClient):
            def _bounded_generate_request(self, path, payload):
                self.captured = payload
                return {'done': True, 'response': 'ok'}
        client = CaptureClient('http://127.0.0.1:11434')
        token = MISSION_FOREGROUND.set(foreground)
        try:
            with patch.dict(os.environ, env or {}, clear=False):
                if env is None:
                    os.environ.pop('MAESTRO_OLLAMA_KEEP_ALIVE', None)
                    os.environ.pop('MAESTRO_OLLAMA_PROBE_KEEP_ALIVE', None)
                client._request('/api/generate', {'model': 'neutral', 'prompt': 'hello',
                                                  'options': {'num_predict': 64}})
        finally:
            MISSION_FOREGROUND.reset(token)
        return client.captured

    def test_foreground_model_defaults_to_long_residency(self):
        self.assertEqual(self._capture(True)['keep_alive'], '60m')

    def test_background_probe_is_not_left_resident(self):
        self.assertEqual(self._capture(False)['keep_alive'], '0s')

    def test_foreground_environment_override_remains_supported(self):
        self.assertEqual(self._capture(True, {'MAESTRO_OLLAMA_KEEP_ALIVE': '20m'})['keep_alive'], '20m')


class V89TelemetryTests(unittest.TestCase):
    def test_early_stream_records_decode_after_first_and_characters(self):
        events = [
            {'model': 'neutral', 'response': '1. Vérifier le port.\n'},
            {'model': 'neutral', 'response': '2. Tester localhost.\n'},
            {'model': 'neutral', 'response': ('3. Couper Internet et retester.\n'
                                               'Limite : certaines dépendances restent cachées.')},
        ]
        class FakeReply:
            status = 200
            def __init__(self): self.rows=[(json.dumps(e)+'\n').encode() for e in events]
            def readline(self, _limit): return self.rows.pop(0) if self.rows else b''
            def read(self, _limit=-1): return b''
        reply=FakeReply()
        class FakeConnection:
            sock=None
            def __init__(self,*args,**kwargs): pass
            def request(self,*args,**kwargs): pass
            def getresponse(self): return reply
            def close(self): pass
        policy_token = MISSION_STREAM_POLICY.set({'mode':'three_steps','min_chars':64,'requires_limit':True})
        fg_token = MISSION_FOREGROUND.set(True)
        reset_stream_metrics()
        try:
            with patch('maestro_gateway.ollama_gateway_client.http.client.HTTPConnection', FakeConnection):
                result=GatewayOllamaClient('http://127.0.0.1:11434',timeout=5)._bounded_generate_request(
                    '/api/generate', {'model':'neutral','prompt':'x','options':{'num_predict':160,'num_ctx':1024}})
        finally:
            MISSION_FOREGROUND.reset(fg_token)
            MISSION_STREAM_POLICY.reset(policy_token)
        m=current_stream_metrics()
        self.assertEqual(result['done_reason'],'maestro_sufficient')
        self.assertEqual(m['generated_characters'], len(result['response']))
        self.assertEqual(m['stream_fragments'], 3)
        self.assertIsInstance(m['decode_after_first_ms'], float)


if __name__ == '__main__': unittest.main()
