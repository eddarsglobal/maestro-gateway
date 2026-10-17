"""V8.3 local-only live validation tests; all mocks, no external requests."""
from __future__ import annotations

import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

_SCRIPT = Path(__file__).resolve().parents[1] / 'tools' / 'v83_live_validation.py'
_spec = importlib.util.spec_from_file_location('v83_live_validation', _SCRIPT)
v83 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v83)


def inventory(models=None, primary=None):
    return {'models': ['ollama::safe:1'] if models is None else models,
            'primary': {'cloud_enabled': False, 'mode': 'single_local_primary',
                        'primary_resource_id': primary}}


def mission(language='en', rid='ollama::safe:1', *, response='Three real steps and one limit.', ok=True):
    return {'ok': ok, 'resource_id': rid, 'output': response,
            'semantic_truth_verified': False,
            'metrics': {'response_origin': 'local_model', 'routing_language': language,
                        'wall_ms': 234.0, 'primary_selection_epoch': 2}}


class MockGateway:
    def __init__(self, empty=False):
        self.calls = []
        self.empty = empty
        self.missing_language = False

    def request(self, path, *, body=None, session=None):
        self.calls.append((path, body, session))
        if path == '/models':
            return inventory(models=[] if self.empty else None)
        if path == '/session':
            return {'session_token': 'A' * 32}
        if path == '/mission':
            return mission(body['ui_language'])
        raise AssertionError('Unrecognized path')


class V83LiveValidationTests(unittest.TestCase):
    def test_url_requires_literal_loopback(self):
        self.assertEqual(v83.validate_url('http://127.0.0.1:8787'), 'http://127.0.0.1:8787')
        self.assertEqual(v83.validate_url('http://[::1]:8787'), 'http://[::1]:8787')
        for url in ('https://127.0.0.1:8787', 'http://localhost:8787',
                    'http://192.168.1.7:8787', 'http://8.8.8.8:8787',
                    'http://127.0.0.1:8787/mission',
                    'http://person:pass@127.0.0.1:8787',
                    'http://127.0.0.1:8787?a=1', 'http://127.0.0.1',
                    'http://evil.example', 'file:///etc/passwd'):
            with self.subTest(url=url), self.assertRaises(v83.ProbeError):
                v83.validate_url(url)

    def test_language_set_and_duplicate_rejection(self):
        self.assertEqual(v83.parse_languages('en,fr'), ('en', 'fr'))
        self.assertEqual(len(v83.PROMPTS), 8)
        for bad in ('en,en', 'xx', '', 'en,', 'en,cloud'):
            with self.subTest(bad=bad), self.assertRaises(v83.ProbeError):
                v83.parse_languages(bad)

    def test_inspection_enforces_sovereignty_and_valid_descriptors(self):
        self.assertEqual(v83.inspect_inventory(inventory())[0], {'ollama::safe:1'})
        for bad in ({'models': [], 'primary': {'cloud_enabled': True, 'mode': 'single_local_primary'}},
                    {'models': [], 'primary': {'cloud_enabled': False, 'mode': 'other'}},
                    {'models': [''], 'primary': inventory()['primary']},
                    {'models': 'bad', 'primary': inventory()['primary']}):
            with self.subTest(bad=bad), self.assertRaises(v83.ProbeError):
                v83.inspect_inventory(bad)

    def test_valid_real_envelope_operational_not_semantically_certified(self):
        row = v83.assess_result(mission(), {'ollama::safe:1'}, 'en', 1.25)
        self.assertTrue(row['operational_pass'])
        self.assertFalse(row['semantic_truth_verified'])
        self.assertFalse(row['human_reviewed'])
        self.assertIsNone(row['human_semantic_acceptance'])
        self.assertNotIn('output_for_human_review', row)

    def test_invalid_envelopes_never_pass(self):
        cases = [mission(rid='cloud::model'), mission(response=''),
                 mission(ok=False), mission(language='fr')]
        semantic = mission()
        semantic['semantic_truth_verified'] = True
        cases.append(semantic)
        nonlocal_output = mission()
        nonlocal_output['metrics']['response_origin'] = 'cloud_model'
        cases.append(nonlocal_output)
        for case in cases:
            with self.subTest(case=case):
                row = v83.assess_result(case, {'ollama::safe:1'}, 'en', .75)
                self.assertFalse(row['operational_pass'])

    def test_output_saved_only_with_consent(self):
        result = v83.assess_result(mission(), {'ollama::safe:1'}, 'en', 1.1, keep_output=True)
        self.assertEqual(result['output_for_human_review'], 'Three real steps and one limit.')
        self.assertFalse(result['output_truncated_in_report'])

    def test_report_has_private_permissions_and_no_secret(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'private'
            path = v83.write_private_report(directory, {'results': [{'ok': True}]})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual(json.loads(path.read_text())['results'][0]['ok'], True)

    def test_report_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / 'dest'
            destination.mkdir()
            symlink = Path(temp) / 'symlink'
            symlink.symlink_to(destination)
            with self.assertRaises(v83.ProbeError):
                v83.write_private_report(symlink, {'test': 1})

    def test_live_runs_only_explicit_local_missions(self):
        with tempfile.TemporaryDirectory() as temp:
            client = MockGateway()
            client.url = 'http://127.0.0.1:8787'
            report, path = v83.run_live(client, ('en', 'fr'), report_dir=Path(temp))
            self.assertTrue(report['operational_pass'])
            self.assertTrue(path.exists())
            self.assertEqual([req[0] for req in client.calls],
                             ['/models', '/session', '/mission', '/mission', '/models'])
            for _, body, session in client.calls[2:4]:
                self.assertNotIn('model', body)
                self.assertNotIn('workspace_id', body)
                self.assertEqual(session, 'A' * 32)
            self.assertNotIn('A' * 32, path.read_text())
            self.assertNotIn('output_for_human_review', path.read_text())
            self.assertFalse(report['semantic_language_quality_certified'])

    def test_zero_model_does_not_start_session_or_mission(self):
        client = MockGateway(empty=True)
        with self.assertRaisesRegex(v83.ProbeError, 'No local model'):
            v83.run_live(client, ('en',))
        self.assertEqual([row[0] for row in client.calls], ['/models'])

    def test_reject_invalid_session_before_missions(self):
        class BadSession(MockGateway):
            def request(self, path, **kwargs):
                if path == '/session':
                    self.calls.append((path, None, None))
                    return {'session_token': 'x'}
                return super().request(path, **kwargs)
        client = BadSession()
        with self.assertRaisesRegex(v83.ProbeError, 'valid session token'):
            v83.run_live(client, ('en',))
        self.assertEqual([row[0] for row in client.calls], ['/models', '/session'])

    def test_failed_mission_is_reported_not_falsely_passed(self):
        class FailedClient(MockGateway):
            def request(self, path, **kwargs):
                if path == '/mission':
                    self.calls.append((path, kwargs.get('body'), kwargs.get('session')))
                    return mission('en', ok=False, response='')
                return super().request(path, **kwargs)
        with tempfile.TemporaryDirectory() as temp:
            client = FailedClient()
            client.url = 'http://127.0.0.1:8787'
            report, _ = v83.run_live(client, ('en',), report_dir=Path(temp))
            self.assertFalse(report['operational_pass'])
            self.assertFalse(report['results'][0]['operational_pass'])

    def test_single_language_failure_does_not_abort_remaining_languages(self):
        class MixedClient(MockGateway):
            def request(self, path, **kwargs):
                if path == '/mission' and kwargs.get('body', {}).get('ui_language') == 'fr':
                    self.calls.append((path, kwargs['body'], kwargs.get('session')))
                    raise v83.ProbeError('HTTP 422')
                return super().request(path, **kwargs)
        with tempfile.TemporaryDirectory() as temp:
            client = MixedClient()
            client.url = 'http://127.0.0.1:8787'
            report, _ = v83.run_live(client, ('fr','en'), report_dir=Path(temp))
            self.assertEqual([r['operational_pass'] for r in report['results']], [False, True])

    def test_inspect_only_calls_models(self):
        client = MockGateway()
        with patch.object(v83, 'LocalGatewayClient', return_value=client):
            self.assertEqual(v83.main(['--inspect']), 0)
        self.assertEqual([r[0] for r in client.calls], ['/models'])

    def test_actual_loopback_http_request_without_proxy(self):
        class Server(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return
            def do_GET(self):
                if self.path == '/models':
                    obj = inventory()
                    data = json.dumps(obj).encode()
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self.send_error(404)
            def do_POST(self):
                data = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                if self.path == '/session':
                    obj, status = {'session_token': 'A' * 32}, 200
                elif self.path == '/mission':
                    self.server.last_session = self.headers.get('X-Maestro-Session')
                    self.server.last_body = json.loads(data)
                    obj, status = mission(self.server.last_body['ui_language']), 200
                else:
                    obj, status = {'error':'missing'}, 404
                content = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Server)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = v83.LocalGatewayClient(f'http://127.0.0.1:{server.server_port}', timeout=5)
            self.assertEqual(v83.inspect_inventory(client.request('/models'))[0], {'ollama::safe:1'})
            token = client.request('/session', body={})['session_token']
            obj = client.request('/mission', body={'prompt':'hi','ui_language':'en'}, session=token)
            self.assertTrue(obj['ok'])
            self.assertEqual(server.last_session, token)
            self.assertNotIn('model', server.last_body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_http_422_failed_mission_is_parsed(self):
        class Server(BaseHTTPRequestHandler):
            def log_message(self, *args): return
            def do_POST(self):
                data = json.dumps(mission(ok=False, response='')).encode()
                self.send_response(422)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Server)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = v83.LocalGatewayClient(f'http://127.0.0.1:{server.server_port}', timeout=5)
            self.assertFalse(client.request('/mission', body={'prompt':'x','ui_language':'en'})['ok'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_live_timeout_and_budget_limits(self):
        for n in (0, -1, 241):
            with self.assertRaises(v83.ProbeError):
                v83.LocalGatewayClient('http://127.0.0.1:8787', timeout=n)
        with self.assertRaises(v83.ProbeError):
            v83.run_live(MockGateway(), ('en',), max_tokens=9000)


if __name__ == '__main__':
    unittest.main()
