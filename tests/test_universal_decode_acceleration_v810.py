"""V8.10 universal bounded-decode and primary-residency acceleration tests."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from maestro_gateway.ollama_gateway_client import (
    GatewayOllamaClient, MISSION_FOREGROUND, MISSION_STREAM_POLICY,
    _FOREGROUND_RESIDENCY_UNTIL, _RESIDENCY_LOCK, _stream_policy_complete,
)
from maestro_gateway.response_effort import compact_chat_instruction, stream_completion_policy

PROMPT = ('Explique en trois étapes pratiques comment vérifier qu’un service web '
          'fonctionne entièrement sur mon ordinateur, sans cloud ni téléchargement. '
          'Cite une limite.')


class CaptureClient(GatewayOllamaClient):
    def _bounded_generate_request(self, path, payload):
        self.captured = payload
        return {'done': True, 'response': 'ok'}


class V810BoundedDecodeTests(unittest.TestCase):
    def setUp(self):
        with _RESIDENCY_LOCK:
            _FOREGROUND_RESIDENCY_UNTIL.clear()

    def tearDown(self):
        with _RESIDENCY_LOCK:
            _FOREGROUND_RESIDENCY_UNTIL.clear()

    def _capture(self, *, policy, foreground=True, options=None, model='neutral'):
        client = CaptureClient('http://127.0.0.1:11434')
        fg = MISSION_FOREGROUND.set(foreground)
        sp = MISSION_STREAM_POLICY.set(policy)
        try:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop('MAESTRO_OLLAMA_KEEP_ALIVE', None)
                os.environ.pop('MAESTRO_OLLAMA_PROBE_KEEP_ALIVE', None)
                client._request('/api/generate', {
                    'model': model, 'prompt': compact_chat_instruction(
                        prompt=PROMPT, language_hint='French', task='normal'),
                    'options': dict(options or {'num_predict': 320}),
                })
        finally:
            MISSION_STREAM_POLICY.reset(sp)
            MISSION_FOREGROUND.reset(fg)
        return client.captured

    def test_bounded_three_steps_preserves_caller_decode_budget(self):
        policy = stream_completion_policy('normal', PROMPT, workspace_active=False, max_tokens=320)
        payload = self._capture(policy=policy)
        self.assertEqual(payload['options']['num_predict'], 320)

    def test_bounded_shape_can_use_768_context_without_truncation(self):
        policy = stream_completion_policy('normal', PROMPT, workspace_active=False, max_tokens=320)
        payload = self._capture(policy=policy)
        self.assertEqual(payload['options']['num_ctx'], 768)

    def test_general_generation_keeps_original_budget_and_1024_floor(self):
        payload = self._capture(policy={'mode': 'disabled'}, options={'num_predict': 320})
        self.assertEqual(payload['options']['num_predict'], 320)
        self.assertEqual(payload['options']['num_ctx'], 1024)

    def test_explicit_context_is_never_overridden(self):
        policy = stream_completion_policy('normal', PROMPT, workspace_active=False, max_tokens=320)
        payload = self._capture(policy=policy, options={'num_predict': 320, 'num_ctx': 4096})
        self.assertEqual(payload['options']['num_ctx'], 4096)
        self.assertEqual(payload['options']['num_predict'], 320)

    def test_compact_structured_prompt_preserves_contract_and_is_smaller(self):
        text = compact_chat_instruction(prompt=PROMPT, language_hint='French', task='normal')
        self.assertIn('CURRENT USER REQUEST', text)
        self.assertIn('exactly three numbered items', text)
        self.assertIn("beginning 'Limite:'", text)
        self.assertLess(len(text), 480)

    def test_structured_completion_does_not_require_final_punctuation(self):
        policy = {'mode': 'three_steps', 'min_chars': 64, 'requires_limit': True}
        text = ('1. Vérifier le port local\n2. Tester depuis localhost\n'
                '3. Couper Internet puis retester\nLimite : dépendances externes parfois invisibles')
        self.assertTrue(_stream_policy_complete(text, policy))

    def test_incomplete_limit_clause_does_not_stop(self):
        policy = {'mode': 'three_steps', 'min_chars': 64, 'requires_limit': True}
        text = ('1. Vérifier le port local\n2. Tester depuis localhost\n'
                '3. Couper Internet puis retester\nLimite : dépendances')
        self.assertFalse(_stream_policy_complete(text, policy))

    def test_probe_cannot_evict_recent_foreground_model(self):
        policy = stream_completion_policy('normal', PROMPT, workspace_active=False, max_tokens=320)
        foreground = self._capture(policy=policy, foreground=True, model='same-model')
        self.assertEqual(foreground['keep_alive'], '60m')
        background = self._capture(policy={'mode': 'disabled'}, foreground=False,
                                   options={'num_predict': 32}, model='same-model')
        self.assertEqual(background['keep_alive'], '60m')

    def test_probe_of_other_model_still_unloads(self):
        policy = stream_completion_policy('normal', PROMPT, workspace_active=False, max_tokens=320)
        self._capture(policy=policy, foreground=True, model='primary-model')
        background = self._capture(policy={'mode': 'disabled'}, foreground=False,
                                   options={'num_predict': 32}, model='probe-model')
        self.assertEqual(background['keep_alive'], '0s')


if __name__ == '__main__':
    unittest.main()
