"""V8.7 universal local inference acceleration without model-name preferences."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from maestro_gateway.autonomous_qualification import AutonomousQualificationService
from maestro_gateway.ollama_gateway_client import (
    GatewayOllamaClient,
    MISSION_STREAM_POLICY,
    _adaptive_num_ctx,
    _stream_policy_complete,
    current_stream_metrics,
    reset_stream_metrics,
)
from maestro_gateway.response_effort import (
    bounded_response_tokens,
    compact_chat_instruction,
    compact_recent_context,
    stream_completion_policy,
)


class V87PromptAccelerationTests(unittest.TestCase):
    def test_historical_safe_output_budgets_are_preserved(self):
        self.assertEqual(bounded_response_tokens('quick', 'Bonjour', 320), 128)
        self.assertEqual(bounded_response_tokens('normal', 'Explique en trois étapes.', 320), 320)
        self.assertEqual(bounded_response_tokens('normal', 'Réponds en une phrase', 512), 112)
        self.assertEqual(bounded_response_tokens('complex', 'Analyse profondément ceci', 1024), 1024)

    def test_normal_context_is_bounded(self):
        messages = []
        for i in range(20):
            messages.append({'role': 'user' if i % 2 == 0 else 'assistant',
                             'content': ('x' * 900) + str(i)})
        normal = compact_recent_context(messages, 'normal')
        complex_ctx = compact_recent_context(messages, 'complex')
        self.assertLessEqual(len(normal), 1601)
        self.assertLessEqual(len(complex_ctx), 5201)
        self.assertGreater(len(complex_ctx), len(normal))
        self.assertNotIn('0', normal)  # only recent bounded turns survive

    def test_ordinary_instruction_omits_capability_catalogue(self):
        prompt = ('Explique en trois étapes pratiques comment vérifier qu’un service web '
                  'fonctionne entièrement sur mon ordinateur, sans cloud ni téléchargement. '
                  'Cite une limite.')
        text = compact_chat_instruction(prompt=prompt, language_hint='French', task='normal')
        self.assertIn('CURRENT USER REQUEST', text)
        self.assertIn(prompt, text)
        self.assertNotIn('VERIFIED MAESTRO CAPABILITIES', text)
        self.assertNotIn('filesystem.write', text)
        self.assertLess(len(text), 900)

    def test_stream_policy_is_conservative(self):
        prompt = ('Explique en trois étapes pratiques comment vérifier qu’un service web '
                  'fonctionne entièrement sur mon ordinateur. Cite une limite.')
        policy = stream_completion_policy('normal', prompt, workspace_active=False, max_tokens=320)
        self.assertEqual(policy['mode'], 'three_steps')
        self.assertTrue(policy['requires_limit'])
        self.assertEqual(stream_completion_policy('complex', prompt, workspace_active=False,
                                                  max_tokens=1024)['mode'], 'disabled')
        self.assertEqual(stream_completion_policy('normal', prompt, workspace_active=True,
                                                  max_tokens=320)['mode'], 'disabled')
        self.assertEqual(stream_completion_policy('quick', 'Quel est le port local ?', workspace_active=False,
                                                  max_tokens=128)['mode'], 'disabled')
        self.assertEqual(stream_completion_policy('quick', 'Bonjour !', workspace_active=False,
                                                  max_tokens=128)['mode'], 'quick')

    def test_three_step_early_completion_requires_complete_shape(self):
        policy = {'mode': 'three_steps', 'min_chars': 80, 'requires_limit': True}
        incomplete = '1. Vérifier le port.\n2. Tester la page.\n3. Examiner les journaux.'
        complete = (incomplete + '\nLimite : cela ne prouve pas qu’aucun composant externe '
                    'n’est contacté.')
        self.assertFalse(_stream_policy_complete(incomplete, policy))
        self.assertTrue(_stream_policy_complete(complete, policy))


class V87OllamaTransportTests(unittest.TestCase):
    def test_adaptive_context_uses_small_safe_window_and_declines_huge_prompt(self):
        short = _adaptive_num_ctx({'prompt': 'bonjour ' * 40,
                                   'options': {'num_predict': 320}})
        self.assertEqual(short, 1024)
        huge = _adaptive_num_ctx({'prompt': 'x' * 30000,
                                  'options': {'num_predict': 2048}})
        self.assertIsNone(huge)

    def test_generate_request_adds_context_without_overriding_explicit_context(self):
        class CaptureClient(GatewayOllamaClient):
            def _bounded_generate_request(self, path, payload):
                self.captured = payload
                return {'done': True, 'response': 'ok'}

        client = CaptureClient('http://127.0.0.1:11434')
        client._request('/api/generate', {'model': 'neutral', 'prompt': 'hello',
                                          'options': {'num_predict': 128}})
        self.assertEqual(client.captured['options']['num_ctx'], 1024)
        self.assertEqual(client.captured['options']['num_predict'], 128)

        client._request('/api/generate', {'model': 'neutral', 'prompt': 'hello',
                                          'options': {'num_predict': 128, 'num_ctx': 4096}})
        self.assertEqual(client.captured['options']['num_ctx'], 4096)

    def test_stream_can_stop_after_verified_bounded_shape_and_records_ttft(self):
        events = [
            {'model': 'neutral', 'response': '1. Vérifier le port local et la boucle réseau.\n'},
            {'model': 'neutral', 'response': '2. Tester la page depuis le navigateur local.\n'},
            {'model': 'neutral', 'response': ('3. Couper le réseau externe puis retester.\n'
                                               'Limite : cela ne prouve pas chaque dépendance.')},
            # This event must not be consumed once the bounded answer is complete.
            {'model': 'neutral', 'response': 'Texte superflu.', 'done': True, 'done_reason': 'stop'},
        ]

        class FakeReply:
            status = 200
            def __init__(self):
                self.rows = [(json.dumps(e) + '\n').encode() for e in events]
                self.reads = 0
            def readline(self, _limit):
                self.reads += 1
                return self.rows.pop(0) if self.rows else b''
            def read(self, _limit=-1):
                return b''

        reply = FakeReply()

        class FakeConnection:
            sock = None
            def __init__(self, *args, **kwargs):
                pass
            def request(self, *args, **kwargs):
                return None
            def getresponse(self):
                return reply
            def close(self):
                return None

        token = MISSION_STREAM_POLICY.set(
            {'mode': 'three_steps', 'min_chars': 100, 'requires_limit': True})
        reset_stream_metrics()
        try:
            with patch('maestro_gateway.ollama_gateway_client.http.client.HTTPConnection',
                       FakeConnection):
                result = GatewayOllamaClient('http://127.0.0.1:11434', timeout=5)._bounded_generate_request(
                    '/api/generate', {'model': 'neutral', 'prompt': 'x',
                                      'options': {'num_predict': 320, 'num_ctx': 2048}})
        finally:
            MISSION_STREAM_POLICY.reset(token)
        metrics = current_stream_metrics()
        self.assertEqual(result['done_reason'], 'maestro_sufficient')
        self.assertNotIn('Texte superflu', result['response'])
        self.assertEqual(reply.reads, 3)
        self.assertTrue(metrics['early_completion'])
        self.assertIsInstance(metrics['first_token_ms'], float)
        self.assertEqual(metrics['adaptive_num_ctx'], 2048)


class V87ForegroundPriorityTests(unittest.TestCase):
    def test_foreground_activity_cancels_and_drains_running_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            service = AutonomousQualificationService(
                candidate_supplier=lambda: [],
                runner=lambda *args: ('', None),
                state_path=Path(temp) / 'q.json',
                idle_seconds=90,
                enabled=True,
            )
            with service._lock:
                service._running = True
            def simulated_probe_exit():
                self.assertTrue(service._cancel.wait(0.3))
                time.sleep(0.03)
                with service._lock:
                    service._running = False
            worker = threading.Thread(target=simulated_probe_exit)
            worker.start()
            started = time.monotonic()
            service.user_activity(True)
            elapsed = time.monotonic() - started
            worker.join(timeout=1)
            self.assertFalse(worker.is_alive())
            self.assertLess(elapsed, 0.45)
            with service._lock:
                self.assertFalse(service._running)
                self.assertEqual(service._active_users, 1)
            service.user_activity(False)


if __name__ == '__main__':
    unittest.main()
