from __future__ import annotations

import sys
import tempfile
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from maestro.providers.ollama.client import OllamaHTTPError
from maestro_gateway.autonomous_qualification import AutonomousQualificationService, reset_for_testing
from maestro_gateway.ollama_gateway_client import GatewayOllamaClient


@dataclass(frozen=True)
class Descriptor:
    resource_id: str = 'ollama::unit-test'
    provider_id: str = 'ollama'
    model_id: str = 'unit-test'
    local: bool = True
    capabilities: tuple[str, ...] = ('DIRECT',)
    status: str = 'AVAILABLE'
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            object.__setattr__(self, 'metadata', {'size': 1_500_000_000})


class _FakeStop:
    def __init__(self):
        self.calls = 0

    def wait(self, _seconds: float) -> bool:
        self.calls += 1
        return self.calls >= 3


class _BrokenReply:
    status = 200

    def readline(self, _limit: int):
        raise AttributeError("simulated chunk parser close race")


class _BrokenConnection:
    def __init__(self, *_args, **_kwargs):
        self.sock = None

    def request(self, *_args, **_kwargs):
        return None

    def getresponse(self):
        return _BrokenReply()

    def close(self):
        return None


class AutonomousQualificationResilienceV852Tests(unittest.TestCase):
    def setUp(self):
        reset_for_testing()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()
        reset_for_testing()

    def _service(self, runner):
        svc = AutonomousQualificationService(
            candidate_supplier=lambda: [(Descriptor(), 'v1')],
            runner=runner,
            state_path=Path(self.tmp.name) / 'profiles.json',
            idle_seconds=1.0,
            probe_timeout=2.0,
            enabled=True,
        )
        svc._last_user_activity = time.monotonic() - 10.0
        return svc

    def test_unexpected_probe_exception_is_recorded_not_raised(self):
        def broken(*_args, **_kwargs):
            raise AttributeError('provider stream parser failed')

        svc = self._service(broken)
        with patch.object(sys, 'platform', 'linux'):
            self.assertTrue(svc.run_one_if_idle())
        status = svc.status()
        self.assertEqual(status['last_result'], 'error')
        self.assertEqual(status['attempts_this_session'], 1)
        self.assertEqual(status['last_probe']['failure_category'], 'unexpected_probe_error')
        self.assertEqual(status['last_probe']['error_type'], 'AttributeError')

    def test_known_transport_error_remains_controlled_error(self):
        def broken(*_args, **_kwargs):
            raise RuntimeError('transport failed')

        svc = self._service(broken)
        with patch.object(sys, 'platform', 'linux'):
            self.assertTrue(svc.run_one_if_idle())
        status = svc.status()
        self.assertEqual(status['last_result'], 'error')
        self.assertEqual(status['last_probe']['failure_category'], 'execution_or_transport_error')
        self.assertEqual(status['last_probe']['error_type'], 'RuntimeError')

    def test_scheduler_guard_keeps_loop_alive_after_unexpected_error(self):
        svc = self._service(lambda *_args: ('ok', None))
        svc._stop = _FakeStop()
        svc.run_one_if_idle = lambda: (_ for _ in ()).throw(AttributeError('scheduler bug'))
        svc._loop()
        status = svc.status()
        self.assertEqual(status['last_result'], 'scheduler_error')
        self.assertEqual(status['last_probe']['failure_category'], 'qualification_scheduler_error')
        self.assertEqual(status['last_probe']['error_type'], 'AttributeError')

    @patch('maestro_gateway.ollama_gateway_client.http.client.HTTPConnection', _BrokenConnection)
    def test_chunk_parser_attribute_error_becomes_ollama_transport_error(self):
        client = GatewayOllamaClient('http://127.0.0.1:11434', timeout=3.0)
        with self.assertRaises(OllamaHTTPError) as ctx:
            client._bounded_generate_request('/api/generate', {'model': 'unit-test', 'prompt': 'x'})
        self.assertIn('streaming transport failure', str(ctx.exception))
        self.assertIn('AttributeError', str(ctx.exception))

    def test_background_probe_failure_does_not_leave_running_flag_set(self):
        svc = self._service(lambda *_args: (_ for _ in ()).throw(AttributeError('boom')))
        with patch.object(sys, 'platform', 'linux'):
            self.assertTrue(svc.run_one_if_idle())
        self.assertFalse(svc.status()['running'])


if __name__ == '__main__':
    unittest.main()
