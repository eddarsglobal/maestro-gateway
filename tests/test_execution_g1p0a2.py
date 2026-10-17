"""G1-P0-A2.1: real bounded subprocess cancellation, per-attempt fence.

Does not certify mission-wide cancellation, terminal.run, research TCP abort,
or atomic MissionControl publication. Tests never touch user workspaces.
"""
from __future__ import annotations
import hashlib
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from maestro_gateway.execution import (
    _run_bounded_process, ExecutionProposalStore, ExecutionStateError,
)
from maestro_gateway.workspace import WorkspaceRegistry


class BoundedExecutionCancelTests(unittest.TestCase):
    def call(self, tmp, command, timeout_ms=3000, cancellation=None):
        return _run_bounded_process(
            argv=[sys.executable, '-B', '-c', command],
            cwd=pathlib.Path(tmp),
            env=dict(os.environ),
            timeout_ms=timeout_ms,
            cancellation=cancellation,
        )

    def test_precancelled_never_spawns(self):
        ev = threading.Event(); ev.set()
        with tempfile.TemporaryDirectory() as td, \
                mock.patch('maestro_gateway.execution.subprocess.Popen') as popen:
            row = self.call(td, "raise Exception('MUST NOT RUN')", cancellation=ev)
            popen.assert_not_called()
        self.assertTrue(row['cancelled'])
        self.assertIsNone(row['exit_code'])
        self.assertEqual(row['stdout_sha256'], hashlib.sha256(b'').hexdigest())

    def test_success_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            row = self.call(td, "print('SAFE')")
        self.assertFalse(row['cancelled'])
        self.assertFalse(row['timed_out'])
        self.assertEqual(row['exit_code'], 0)
        self.assertIn('SAFE', row['stdout'])

    def test_timeout_remains_distinct(self):
        with tempfile.TemporaryDirectory() as td:
            row = self.call(td, 'import time; time.sleep(6)', timeout_ms=90)
        self.assertTrue(row['timed_out'])
        self.assertFalse(row['cancelled'])
        self.assertTrue(row['process_group_terminated'])

    def test_cancel_kills_parent_before_late_write(self):
        with tempfile.TemporaryDirectory() as td:
            marker = pathlib.Path(td) / 'UNWANTED_LATE_WRITE'
            ev = threading.Event()
            done = {}
            source = f"import pathlib,time;time.sleep(1);pathlib.Path({str(marker)!r}).write_text('late')"
            th = threading.Thread(target=lambda: done.setdefault('result',
                                   self.call(td, source, cancellation=ev)), daemon=True)
            th.start()
            time.sleep(0.2); ev.set(); th.join(timeout=2.5)
            self.assertFalse(th.is_alive(), 'cancel did not interrupt process promptly')
            self.assertTrue(done['result']['cancelled'])
            self.assertTrue(done['result']['process_group_terminated'])
            time.sleep(0.95)
            self.assertFalse(marker.exists(), 'late write after cancellation')

    @unittest.skipUnless(os.name == 'posix', 'process-group test requires POSIX')
    def test_cancel_kills_spawned_child_process_group(self):
        with tempfile.TemporaryDirectory() as td:
            marker = pathlib.Path(td) / 'UNWANTED_CHILD_WRITE'
            child = f"import time,pathlib;time.sleep(0.9);pathlib.Path({str(marker)!r}).write_text('child late')"
            parent = ("import subprocess,sys,time;"
                      f"subprocess.Popen([sys.executable, '-c', {child!r}]);"
                      "time.sleep(7)")
            ev = threading.Event(); done = {}
            th = threading.Thread(target=lambda: done.setdefault('result',
                                   self.call(td, parent, cancellation=ev)), daemon=True)
            th.start(); time.sleep(0.3); ev.set(); th.join(timeout=2.5)
            self.assertFalse(th.is_alive())
            self.assertTrue(done['result']['cancelled'])
            time.sleep(1)
            self.assertFalse(marker.exists(), 'child survived cancelled process group')

    def test_cancel_does_not_kill_unrelated_invocation(self):
        with tempfile.TemporaryDirectory() as td:
            ev = threading.Event(); done = {}
            th = threading.Thread(target=lambda: done.setdefault('cancelled',
                                   self.call(td, 'import time;time.sleep(3)', cancellation=ev)),
                                  daemon=True)
            th.start(); time.sleep(0.15)
            success = self.call(td, "print('OTHER MISSION OK')")
            ev.set(); th.join(timeout=2)
            self.assertEqual(success['exit_code'], 0)
            self.assertFalse(success['cancelled'])
            self.assertTrue(done['cancelled']['cancelled'])


class ApprovedLedgerCancelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = pathlib.Path(self.temp.name)
        root = base / 'project'; root.mkdir()
        (root / 'pyproject.toml').write_text('[project]\nname="cancel-test"\nversion="0.0.0"\n')
        tests = root / 'tests'; tests.mkdir()
        (tests / 'test_sandbox.py').write_text('import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n')
        workspaces = WorkspaceRegistry(base / 'workspaces.json')
        workspace = workspaces.authorize_path(str(root))
        self.store = ExecutionProposalStore(base / 'executions', workspaces)
        self.proposal = self.store.propose(workspace_id=workspace['id'], kind='test', profile='python_unittest')
        self.store.approve(self.proposal['id'])
        self.run_id = self.proposal['id']

    def test_cancel_before_approval_consumption_does_not_spawn(self):
        ev = threading.Event(); ev.set()
        with mock.patch('maestro_gateway.execution._run_bounded_process') as runner:
            with self.assertRaises(ExecutionStateError):
                self.store.execute(self.run_id, confirm_execution=True, cancellation=ev)
            runner.assert_not_called()
        self.assertEqual(self.store.get(self.run_id)['status'], 'APPROVED')

    def test_normal_approved_execution_still_passes(self):
        row = self.store.execute(self.run_id, confirm_execution=True)
        self.assertEqual(row['status'], 'PASSED')
        self.assertFalse(row['cancelled'])

    def test_cancel_during_post_scan_does_not_commit_passed(self):
        ev = threading.Event()
        entered = threading.Event(); release = threading.Event(); done = {}
        snapshot = self.store._detector.snapshot
        count = 0
        def gated_snapshot(*args, **kwargs):
            nonlocal count
            count += 1
            if count >= 2:
                entered.set()
                if not release.wait(timeout=3):
                    raise TimeoutError('test gate timeout')
            return snapshot(*args, **kwargs)
        def runner():
            try:
                done['result'] = self.store.execute(self.run_id, confirm_execution=True,
                                                    cancellation=ev)
            except BaseException as exc:
                done['error'] = exc
        with mock.patch.object(self.store._detector, 'snapshot', side_effect=gated_snapshot):
            th = threading.Thread(target=runner, daemon=True)
            th.start()
            self.assertTrue(entered.wait(timeout=3), 'post scan never reached')
            ev.set(); release.set(); th.join(timeout=3)
        self.assertFalse(th.is_alive())
        self.assertNotIn('error', done, str(done.get('error')))
        self.assertEqual(done['result']['status'], 'CANCELLED')
        self.assertNotIn('PASSED', [e['action'] for e in done['result']['history']])
        self.assertTrue(done['result']['cancelled'])


if __name__ == '__main__':
    unittest.main()
