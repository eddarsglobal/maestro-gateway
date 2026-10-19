"""A2.4.2.2b1 — real disk, process crash, threads, and actual runner wrapper.

These tests use the exact patched run wrapper but a fake Core execution body;
they do NOT certify HTTP ingress or real Core/Workspace/Git recovery.
"""
from __future__ import annotations

import ast
from pathlib import Path
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
SRC = BASE / 'src/maestro_gateway'


def load(name: str, file: Path):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


journal_mod = load('a2422b_journal', SRC / 'request_journal.py')
conv_mod = load('a2422b_conv', SRC / 'conversations.py' if (SRC / 'conversations.py').is_file()
                else BASE.parent / 'fixtures/conversations.py')
MissionRequestJournal = journal_mod.MissionRequestJournal
RequestReplayBlocked = journal_mod.RequestReplayBlocked
fingerprint = journal_mod.fingerprint

# Compile the actual run wrapper for isolation from the unavailable Core
# imports. No approximated implementation of its control flow is used.
tree = ast.parse((SRC / 'mission.py').read_text())
runner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'GovernedDirectMissionRunner')
run_func = next(node for node in runner.body if isinstance(node, ast.FunctionDef) and node.name == 'run')
namespace = dict(Any=object, MissionRequestJournal=MissionRequestJournal,
                 RequestReplayBlocked=RequestReplayBlocked, fingerprint=fingerprint)
exec(compile(ast.Module(body=[run_func], type_ignores=[]), 'patched_runner.run', 'exec'), namespace)
run_wrapper = namespace['run']


class RunnerHarness:
    run = run_wrapper

    def __init__(self, store, task='TASK-1'):
        self._conversations = store
        self.calls = 0
        self.task = task
        self.fail = False
        self.skip_turn = False

    def _run_untracked(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError('simulated Core post-effect failure')
        cid = kwargs['conversation_id'] or self._conversations.create()['id']
        result = {'ok': True, 'task_id': self.task, 'conversation_id': cid,
                  'output': 'verified response'}
        if not self.skip_turn:
            self._conversations.append_messages(cid, mission_id=self.task,
                messages=[{'role': 'user', 'content': kwargs['prompt']},
                          {'role': 'assistant', 'content': result['output'],
                           'metadata': {'mission': result}}])
        return result


class JournalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'chats'
        self.store = conv_mod.ConversationStore(self.root)
        self.journal = MissionRequestJournal(self.root)
        self.signature = fingerprint({'prompt': 'hello', 'conversation_id': None})

    def test_signature_stable_json_field_order(self):
        self.assertEqual(fingerprint({'a': 1, 'b': 2}), fingerprint({'b': 2, 'a': 1}))

    def test_reject_non_json_and_non_finite(self):
        for value in (float('nan'), float('inf'), object()):
            with self.subTest(v=str(value)), self.assertRaises(ValueError):
                fingerprint({'v': value})

    def test_invalid_request_ids(self):
        for s in ('', '../foo', 'bad/id', 'a', '☕abc', 'a' * 129, '\x00foo'):
            with self.subTest(s=s), self.assertRaises(ValueError):
                self.journal.open(s, self.signature)

    def test_start_is_durable_and_status_uncertain(self):
        with self.journal.open('REQ-0001', self.signature) as lease:
            self.assertIsNone(lease.existing)
            self.assertEqual(self.journal.inspect('REQ-0001', self.signature)['state'], 'STARTED_UNCERTAIN')
        with self.assertRaisesRegex(RequestReplayBlocked, '.*'):
            # Entry itself is observable; policy refusal occurs in wrapper.
            with self.journal.open('REQ-0001', self.signature) as entry:
                if entry.existing:
                    raise RequestReplayBlocked('No replay')

    def test_identical_completed_roundtrip(self):
        val = {'task_id': 'TASK-A', 'conversation_id': 'CHAT-ABCDEF123456'}
        with self.journal.open('REQ-0002', self.signature) as lease:
            lease.complete(val)
        self.assertEqual(self.journal.inspect('REQ-0002', self.signature)['state'], 'COMPLETED')
        with self.journal.open('REQ-0002', self.signature) as lease:
            self.assertEqual(lease.existing['result'], val)

    def test_conflicting_same_id_rejected(self):
        with self.journal.open('REQ-0003', self.signature):
            pass
        with self.assertRaisesRegex(RequestReplayBlocked, 'different request payload'):
            with self.journal.open('REQ-0003', fingerprint({'other': 'body'})):
                pass

    def test_exclusive_active_process_lock(self):
        with self.journal.open('REQ-0004', self.signature):
            with self.assertRaisesRegex(RequestReplayBlocked, 'already active'):
                with self.journal.open('REQ-0004', self.signature):
                    pass

    def test_record_tamper_blocks(self):
        with self.journal.open('REQ-0005', self.signature) as lease:
            lease.uncertain('CORE_FAILURE')
        target = next(self.journal.root.glob('*.json'))
        doc = json.loads(target.read_text())
        doc['state'] = 'COMPLETED'
        target.write_text(json.dumps(doc))
        with self.assertRaisesRegex(RequestReplayBlocked, 'checksum'):
            with self.journal.open('REQ-0005', self.signature):
                pass

    def test_deleted_receipt_does_not_reset_request(self):
        with self.journal.open('REQ-0006', self.signature):
            pass
        target = next(self.journal.root.glob('*.json'))
        target.unlink()
        with self.assertRaisesRegex(RequestReplayBlocked, 'Receipt missing'):
            with self.journal.open('REQ-0006', self.signature):
                pass

    def test_symlink_receipt_blocks(self):
        with self.journal.open('REQ-0007', self.signature):
            pass
        target = next(self.journal.root.glob('*.json'))
        target.unlink()
        outside = target.parent.parent / 'outside'
        outside.write_text('{}')
        target.symlink_to(outside)
        with self.assertRaises(RuntimeError):
            with self.journal.open('REQ-0007', self.signature):
                pass
        self.assertEqual(outside.read_text(), '{}')

    def test_symlink_journal_dir_blocks(self):
        other = self.root / 'other'; other.mkdir()
        import shutil
        shutil.rmtree(self.journal.root)
        self.journal.root.symlink_to(other, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            MissionRequestJournal(self.root)

    def test_insecure_directory_blocks(self):
        self.journal.root.chmod(0o755)
        with self.assertRaisesRegex(RuntimeError, 'group/other'):
            with self.journal.open('REQ-0008', self.signature):
                pass

    def test_result_replay_is_idempotent_at_runner(self):
        r = RunnerHarness(self.store)
        a = r.run(prompt='hello', request_id='REQ-1001')
        b = r.run(prompt='hello', request_id='REQ-1001')
        self.assertEqual(a, b)
        self.assertEqual(r.calls, 1)
        self.assertEqual(len(self.store.get(a['conversation_id'])['messages']), 2)

    def test_new_request_runs_separately(self):
        r = RunnerHarness(self.store)
        r.task = 'TASK-1'; r.run(prompt='first', request_id='REQ-1002')
        r.task = 'TASK-2'; r.run(prompt='first', request_id='REQ-1003')
        self.assertEqual(r.calls, 2)

    def test_same_id_different_prompt_is_blocked_before_core(self):
        r = RunnerHarness(self.store)
        r.run(prompt='first', request_id='REQ-1004')
        with self.assertRaises(RequestReplayBlocked):
            r.run(prompt='second', request_id='REQ-1004')
        self.assertEqual(r.calls, 1)

    def test_missing_conversation_receipt_blocks_replay(self):
        r = RunnerHarness(self.store)
        result = r.run(prompt='first', request_id='REQ-1005')
        self.store.delete(result['conversation_id'])
        with self.assertRaisesRegex(RequestReplayBlocked, 'missing/corrupt'):
            r.run(prompt='first', request_id='REQ-1005')
        self.assertEqual(r.calls, 1)

    def test_exception_after_effects_is_never_replayed(self):
        r = RunnerHarness(self.store)
        r.fail = True
        with self.assertRaisesRegex(RuntimeError, 'post-effect'):
            r.run(prompt='first', request_id='REQ-1006')
        r.fail = False
        with self.assertRaisesRegex(RequestReplayBlocked, 'Prior request'):
            r.run(prompt='first', request_id='REQ-1006')
        self.assertEqual(r.calls, 1)

    def test_no_receipt_return_is_uncertain_not_success(self):
        r = RunnerHarness(self.store)
        r.skip_turn = True
        r.run(prompt='first', request_id='REQ-1007')
        with self.assertRaisesRegex(RequestReplayBlocked, 'Prior request'):
            r.run(prompt='first', request_id='REQ-1007')
        self.assertEqual(r.calls, 1)

    def test_existing_clients_unmodified_without_request_id(self):
        r = RunnerHarness(self.store)
        r.run(prompt='first')
        r.task = 'TASK-2'
        r.run(prompt='first')
        self.assertEqual(r.calls, 2)  # Does NOT imply HTTP-level replay safety.

    def test_research_with_request_id_refused_before_execution(self):
        r = RunnerHarness(self.store)
        with self.assertRaisesRegex(ValueError, 'DIRECT mode only'):
            r.run(prompt='x', mode='M0_RESEARCH_FETCH', request_id='REQ-1008')
        self.assertEqual(r.calls, 0)

    def test_crash_before_receipt_is_uncertain(self):
        script = '''import os, sys\nfrom pathlib import Path\nsys.path.insert(0, sys.argv[2])\nfrom maestro_gateway.request_journal import MissionRequestJournal, fingerprint\nr=MissionRequestJournal(Path(sys.argv[1]))\nwith r.open('REQ-CRASH-1', fingerprint({'x':1})):\n os._exit(77)\n'''
        env = dict(os.environ); env['PYTHONPATH'] = str(SRC)
        process = subprocess.run([sys.executable, '-c', script, str(self.root), str(SRC.parent)], env=env)
        self.assertEqual(process.returncode, 77)
        state = self.journal.inspect('REQ-CRASH-1', fingerprint({'x': 1}))
        self.assertEqual(state['state'], 'STARTED_UNCERTAIN')
        with self.journal.open('REQ-CRASH-1', fingerprint({'x':1})) as lease:
            self.assertEqual(lease.existing['state'], 'STARTED_UNCERTAIN')

    def test_crash_after_completion_is_durable(self):
        script = '''import os, sys\nfrom pathlib import Path\nsys.path.insert(0, sys.argv[2])\nfrom maestro_gateway.request_journal import MissionRequestJournal, fingerprint\nr=MissionRequestJournal(Path(sys.argv[1]))\nwith r.open('REQ-CRASH-2', fingerprint({'x':2})) as l:\n l.complete({'task_id':'TASK-Z','conversation_id':'CHAT-000000000001'})\n os._exit(78)\n'''
        env = dict(os.environ); env['PYTHONPATH'] = str(SRC)
        process = subprocess.run([sys.executable, '-c', script, str(self.root), str(SRC.parent)], env=env)
        self.assertEqual(process.returncode, 78)
        self.assertEqual(self.journal.inspect('REQ-CRASH-2', fingerprint({'x':2}))['state'], 'COMPLETED')

    def test_concurrent_threads_same_request_one_core_execution(self):
        runner = RunnerHarness(self.store)
        errors = []; successes = []
        start = threading.Barrier(8)
        def worker():
            try:
                start.wait()
                successes.append(runner.run(prompt='hello', request_id='REQ-THREADS-1'))
            except RequestReplayBlocked as exc:
                errors.append(exc)
        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=4)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(runner.calls, 1)
        self.assertEqual(len(successes) + len(errors), 8)

    def test_receipt_permissions_private(self):
        r=RunnerHarness(self.store)
        r.run(prompt='secret request', request_id='REQ-PRIVATE-1')
        file = next(self.journal.root.glob('*.json'))
        self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.journal.root.stat().st_mode & 0o777, 0o700)
        self.assertNotIn('secret request', file.read_text())

    def test_no_auto_replay_when_partial_record_corrupt(self):
        r=RunnerHarness(self.store)
        r.run(prompt='secret', request_id='REQ-CORRUPT-1')
        file=next(self.journal.root.glob('*.json'))
        file.write_text('{broken')
        with self.assertRaises(RequestReplayBlocked):
            r.run(prompt='secret', request_id='REQ-CORRUPT-1')
        self.assertEqual(r.calls, 1)


if __name__ == '__main__':
    unittest.main()
