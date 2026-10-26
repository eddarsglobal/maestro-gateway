"""G1-P0-A2.4.2.2b2: real HTTP socket -> MissionControl -> runner journal.

Uses the production HTTP handler / real durable stores, with a bounded fake Core
body: these tests do not certify actual Core/Workspace/Git effect atomicity.
"""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from maestro_gateway.conversations import ConversationStore
from maestro_gateway.mission import GovernedDirectMissionRunner
from maestro_gateway.mission_control import MissionControl
from maestro_gateway.request_journal import MissionRequestJournal
from maestro_gateway.server import GatewayHandler
from maestro_gateway.native_admission import NativeAdmissionBarrier


class FakeQualification:
    def user_activity(self, active):
        pass


class CoreBodyHarness(GovernedDirectMissionRunner):
    def __init__(self, store):
        self._conversations = store
        self.calls = 0
        self._lock = threading.Lock()
        self.engaged = threading.Event()
        self.release = threading.Event()
        self.should_block = False
        self.should_raise = False
        self.skip_receipt = False

    def _run_untracked(self, **kwargs):
        with self._lock:
            self.calls += 1
            task_id = f'TASK-HTTP-{self.calls}'
        self.engaged.set()
        if self.should_block:
            if not self.release.wait(3):
                raise RuntimeError('simulated timeout')
        if self.should_raise:
            raise RuntimeError('simulated post-effect failure')
        cid = kwargs['conversation_id'] or self._conversations.create()['id']
        response = {'ok': True, 'task_id': task_id,
                    'conversation_id': cid, 'output': 'verified',
                    'tx_committed': True}
        if not self.skip_receipt:
            self._conversations.append_messages(cid, mission_id=task_id,
                messages=[{'role': 'user', 'content': kwargs['prompt']},
                          {'role': 'assistant', 'content': 'verified',
                           'metadata': {'mission': response}}])
        return response


class HTTPBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.store = ConversationStore(root / 'conversations')
        self.control = MissionControl(root / 'mission-controls')
        self.runner = CoreBodyHarness(self.store)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), GatewayHandler)
        self.server.session_token = 'one-session'
        self.server.config = SimpleNamespace(allowed_origins=())
        self.server.mission_runner = self.runner
        self.server.mission_control = self.control
        self.server.autonomous_qualification = FakeQualification()
        # Explicit native foreground admission is part of the real server API.
        # Do not bypass it when exercising request idempotency and crash safety.
        self.server.model_admission = NativeAdmissionBarrier(adapter_cancellation_verified=False)
        self.t = threading.Thread(target=self.server.serve_forever,
                                  kwargs={'poll_interval': 0.01}, daemon=True)
        self.t.start()
        self.addCleanup(self.shutdown)
        self.request_id = uuid.uuid4().hex

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.t.join(timeout=2)

    def post(self, payload, path='/mission', *, token='one-session'):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=6)
        try:
            hdr = {'Content-Type': 'application/json'}
            if token is not None:
                hdr['X-Maestro-Session'] = token
            connection.request('POST', path, body=json.dumps(payload).encode(), headers=hdr)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_missing_admission_fails_closed_before_receipt(self):
        barrier = self.server.model_admission
        del self.server.model_admission
        try:
            status, body = self.post({'prompt': 'hello', 'request_id': self.request_id})
            self.assertEqual(status, 503, body)
            self.assertEqual(body['error'], 'native_model_admission_unavailable')
            self.assertEqual(self.runner.calls, 0)
            with self.assertRaises(KeyError):
                self.control.status(self.request_id)
        finally:
            self.server.model_admission = barrier

    def test_shut_down_admission_denies_core_execution(self):
        self.server.model_admission.shutdown()
        status, body = self.post({'prompt': 'hello'})
        self.assertEqual(status, 503, body)
        self.assertEqual(body['error'], 'native_model_admission_busy')
        self.assertEqual(self.runner.calls, 0)

    def test_http_direct_passes_same_id_and_commits_both_ledgers(self):
        status, result = self.post({'prompt': 'hello', 'request_id': self.request_id})
        self.assertEqual(status, 200, result)
        self.assertEqual(self.runner.calls, 1)
        self.assertEqual(self.control.status(self.request_id)['state'], 'completed')
        self.assertEqual(MissionRequestJournal(self.store.root).inspect(
            self.request_id, self._fingerprint('hello'))['state'], 'COMPLETED')

    def _fingerprint(self, prompt, **overrides):
        from maestro_gateway.request_journal import fingerprint
        v = dict(mode='M0_DIRECT', prompt=prompt, model=None, max_tokens=512,
                 conversation_id=None, workspace_id=None, ui_language=None)
        v.update(overrides)
        return fingerprint(v)

    def test_identical_http_retry_returns_original_no_core_reexecution(self):
        payload = {'prompt': 'hello', 'request_id': self.request_id}
        first = self.post(payload)
        again = self.post(payload)
        self.assertEqual(first, again)
        self.assertEqual(self.runner.calls, 1)
        self.assertEqual(len(self.store.get(first[1]['conversation_id'])['messages']), 2)

    def test_same_id_different_prompt_conflicts(self):
        self.assertEqual(self.post({'prompt': 'hello', 'request_id': self.request_id})[0], 200)
        status, error = self.post({'prompt': 'different', 'request_id': self.request_id})
        self.assertEqual(status, 409)
        self.assertEqual(error['error'], 'mission_reconciliation_required')
        self.assertEqual(self.runner.calls, 1)

    def test_same_id_different_token_budget_conflicts(self):
        self.post({'prompt': 'hello', 'request_id': self.request_id})
        status, _ = self.post({'prompt': 'hello', 'request_id': self.request_id, 'max_tokens': 128})
        self.assertEqual(status, 409)
        self.assertEqual(self.runner.calls, 1)

    def test_missing_request_receipt_after_completed_control_no_replay(self):
        self.control.register(self.request_id)
        self.control.start(self.request_id)
        self.control.finish(self.request_id, success=True, durable_committed=True)
        status, error = self.post({'prompt': 'hello', 'request_id': self.request_id})
        self.assertEqual(status, 409)
        self.assertEqual(error['error'], 'mission_reconciliation_required')
        self.assertEqual(self.runner.calls, 0)
        self.assertFalse(list((self.store.root / '.mission_requests').glob('*.json')) if
                         (self.store.root / '.mission_requests').exists() else False)

    def test_crash_scenario_completed_request_ledger_but_control_interrupted(self):
        self.control.register(self.request_id)
        self.control.start(self.request_id)
        self.runner.run(prompt='hello', request_id=self.request_id)
        # Simulated Gateway restart detects running record -> interrupted.
        self.server.mission_control = MissionControl(self.control.directory)
        status, error = self.post({'prompt': 'hello', 'request_id': self.request_id})
        self.assertEqual(status, 409)
        self.assertEqual(error['error'], 'duplicate_mission_request')
        self.assertEqual(self.runner.calls, 1)

    def test_concurrent_duplicate_http_request_no_double_core(self):
        self.runner.should_block = True
        first = []
        thread = threading.Thread(target=lambda: first.append(self.post(
            {'prompt': 'hello', 'request_id': self.request_id})))
        thread.start()
        self.assertTrue(self.runner.engaged.wait(3))
        status, error = self.post({'prompt': 'hello', 'request_id': self.request_id})
        self.assertEqual(status, 409, error)
        self.runner.release.set()
        thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(first[0][0], 200)
        self.assertEqual(self.runner.calls, 1)

    def test_post_effect_exception_stays_uncertain_on_retry(self):
        self.runner.should_raise = True
        self.assertEqual(self.post({'prompt': 'hello', 'request_id': self.request_id})[0], 500)
        self.runner.should_raise = False
        status, _ = self.post({'prompt': 'hello', 'request_id': self.request_id})
        self.assertEqual(status, 409)
        self.assertEqual(self.runner.calls, 1)

    def test_unverified_conversation_blocks_replay(self):
        self.runner.skip_receipt = True
        self.assertEqual(self.post({'prompt': 'hello', 'request_id': self.request_id})[0], 200)
        status, data = self.post({'prompt': 'hello', 'request_id': self.request_id})
        self.assertEqual(status, 409, data)
        self.assertEqual(self.runner.calls, 1)

    def test_abrupt_process_death_after_http_journal_claim(self):
        """Real child HTTP process exits inside Core body after durable ingress claim."""
        script = r'''import json,os,sys
from pathlib import Path
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from maestro_gateway.server import GatewayHandler
from maestro_gateway.native_admission import NativeAdmissionBarrier
from maestro_gateway.conversations import ConversationStore
from maestro_gateway.mission_control import MissionControl
from maestro_gateway.mission import GovernedDirectMissionRunner
class DeadCore(GovernedDirectMissionRunner):
    def __init__(self,store): self._conversations=store
    def _run_untracked(self,**kwargs): os._exit(83)
class Qual:
    def user_activity(self,active): pass
root=Path(sys.argv[1])
s=ThreadingHTTPServer(('127.0.0.1',0),GatewayHandler)
s.session_token='one-session'
s.config=SimpleNamespace(allowed_origins=())
s.mission_control=MissionControl(root/'mission-controls')
s.mission_runner=DeadCore(ConversationStore(root/'conversations'))
s.autonomous_qualification=Qual()
s.model_admission=NativeAdmissionBarrier(adapter_cancellation_verified=False)
print(s.server_address[1],flush=True)
s.serve_forever(poll_interval=0.01)
'''
        root = Path(self.tmp.name) / 'child-crash'
        root.mkdir()
        env = dict(os.environ)
        proc = subprocess.Popen([sys.executable, '-u', '-c', script, str(root)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        try:
            import select
            ready, _, _ = select.select([proc.stdout], [], [], 30)
            self.assertTrue(ready, 'child HTTP listener did not start')
            port = int(proc.stdout.readline().strip())
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
            try:
                with self.assertRaises((http.client.RemoteDisconnected, ConnectionResetError,
                                        BrokenPipeError, OSError)):
                    conn.request('POST', '/mission', body=json.dumps({
                        'request_id': self.request_id, 'prompt': 'crash'}).encode(),
                        headers={'Content-Type': 'application/json',
                                 'X-Maestro-Session': 'one-session'})
                    conn.getresponse()
            finally:
                conn.close()
            self.assertEqual(proc.wait(timeout=4), 83)
            recovered = MissionControl(root/'mission-controls')
            self.assertEqual(recovered.status(self.request_id)['state'], 'interrupted')
            from maestro_gateway.request_journal import fingerprint
            digest = fingerprint(dict(mode='M0_DIRECT', prompt='crash', model=None,
                                      max_tokens=512, conversation_id=None,
                                      workspace_id=None, ui_language=None))
            self.assertEqual(MissionRequestJournal(root/'conversations').inspect(
                self.request_id, digest)['state'], 'STARTED_UNCERTAIN')
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=4)

    def test_deleted_conversation_after_completion_blocks_http_replay(self):
        payload = {'prompt': 'hello', 'request_id': self.request_id}
        _, first = self.post(payload)
        self.store.delete(first['conversation_id'])
        status, error = self.post(payload)
        self.assertEqual(status, 409, error)
        self.assertEqual(self.runner.calls, 1)

    def test_corrupt_request_receipt_blocks_http_replay(self):
        payload = {'prompt': 'hello', 'request_id': self.request_id}
        self.post(payload)
        target = next((self.store.root / '.mission_requests').glob('*.json'))
        target.write_text('{broken', encoding='utf-8')
        status, error = self.post(payload)
        self.assertEqual(status, 409, error)
        self.assertEqual(self.runner.calls, 1)

    def test_symlinked_completed_receipt_blocks_http_replay(self):
        payload = {'prompt': 'hello', 'request_id': self.request_id}
        self.post(payload)
        target = next((self.store.root / '.mission_requests').glob('*.json'))
        other = Path(self.tmp.name) / 'outside-receipt'
        other.write_text('preserved')
        target.unlink()
        target.symlink_to(other)
        status, error = self.post(payload)
        self.assertEqual(status, 409, error)
        self.assertEqual(self.runner.calls, 1)
        self.assertEqual(other.read_text(), 'preserved')

    def test_deleted_request_lock_blocks_completed_replay(self):
        payload = {'prompt': 'hello', 'request_id': self.request_id}
        self.post(payload)
        target = next(x for x in (self.store.root / '.mission_requests').glob('*.lock') if x.name != '.initialization.lock')
        # Keep the initialization lock, remove only per-request tombstone.
        target.unlink()
        status, error = self.post(payload)
        self.assertEqual(status, 409, error)
        self.assertEqual(self.runner.calls, 1)

    def test_missing_id_keeps_legacy_untracked_behavior(self):
        self.assertEqual(self.post({'prompt': 'hello'})[0], 200)
        self.assertEqual(self.post({'prompt': 'hello'})[0], 200)
        self.assertEqual(self.runner.calls, 2)

    def test_invalid_id_rejected_no_core(self):
        self.assertEqual(self.post({'prompt': 'hello', 'request_id': 'bad/id'})[0], 400)
        self.assertEqual(self.runner.calls, 0)

    def test_unauthorized_request_rejected_before_registration(self):
        status, _ = self.post({'prompt': 'hello', 'request_id': self.request_id}, token=None)
        self.assertEqual(status, 401)
        self.assertEqual(self.runner.calls, 0)
        with self.assertRaises(KeyError):
            self.control.status(self.request_id)

    def test_non_direct_id_keeps_previous_mission_control_behavior_without_request_receipt(self):
        status, _ = self.post({'prompt': 'hello', 'mode': 'M0_RESEARCH_FETCH',
                               'request_id': self.request_id})
        self.assertEqual(status, 200)
        self.assertEqual(self.runner.calls, 1)
        self.assertFalse((self.store.root / '.mission_requests').exists())
        self.assertEqual(self.post({'prompt': 'hello', 'mode': 'M0_RESEARCH_FETCH',
                                    'request_id': self.request_id})[0], 409)


if __name__ == '__main__':
    unittest.main()
