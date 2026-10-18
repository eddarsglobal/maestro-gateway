"""G1-P0-A2.2: authenticated mission cancellation through Workspace/terminal.run.

Uses a test-only fake planned argv for real subprocess races; production allowlist
is unchanged. No cloud connections, model loading, or mutable user workspaces.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from maestro_gateway.mission_control import MissionCancelledError, MissionControl
from maestro_gateway.workspace import WorkspaceRegistry
from maestro_gateway.terminal import run_inspection_command, COMMAND_PATHS
import maestro_gateway.terminal as terminal


class MissionTerminalCancelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'workspace'
        self.root.mkdir()
        self.store = WorkspaceRegistry(Path(self.temp.name) / 'workspaces.json')
        self.ws = self.store.authorize_path(str(self.root))['id']

    def _fake_program(self, program: str):
        return patch.object(terminal, '_plan_command', return_value=(
            sys.executable, [sys.executable, '-c', program], 'pwd'))

    def _run(self, event=None, timeout_ms=3000):
        return self.store.execute_tool(
            self.ws, 'terminal.run', {'argv': ['pwd'], 'timeout_ms': timeout_ms},
            cancellation=event)

    def test_real_allowlist_is_unchanged(self):
        self.assertEqual(set(COMMAND_PATHS), {'pwd', 'head', 'tail', 'wc', 'stat'})
        with self.assertRaises(PermissionError):
            self.store.execute_tool(self.ws, 'terminal.run', {'argv': ['python3']},
                                    cancellation=threading.Event())

    def test_precancelled_token_does_not_spawn(self):
        ev = threading.Event(); ev.set()
        with patch.object(terminal.subprocess, 'Popen', side_effect=AssertionError('spawned')):
            with self.assertRaises(MissionCancelledError):
                self._run(ev)

    def test_cancelled_at_workspace_dispatch_no_process(self):
        ev = threading.Event(); ev.set()
        with patch.object(terminal.subprocess, 'Popen', side_effect=AssertionError('spawned')):
            with self.assertRaises(MissionCancelledError):
                self.store.execute_tool(self.ws, 'terminal.run', {'argv': ['pwd']}, cancellation=ev)

    def test_success_with_live_token(self):
        ev = threading.Event()
        row = self._run(ev)
        self.assertEqual(row['exit_code'], 0)
        self.assertEqual(row['stdout'].strip(), str(self.root.resolve()))
        self.assertEqual(row['execution_evidence']['policy'], 'allowlisted_read_only')

    def test_legacy_path_without_token(self):
        self.assertEqual(self._run(None)['exit_code'], 0)

    def test_cancel_stops_real_process_before_late_write(self):
        ev = threading.Event()
        marker = self.root / 'late.txt'
        program = (f'import pathlib,time;time.sleep(1);'
                   f'pathlib.Path({str(marker)!r}).write_text("LATE")')
        results = {}
        with self._fake_program(program):
            t = threading.Thread(target=lambda: self._record(results, lambda: self._run(ev)), daemon=True)
            t.start(); time.sleep(.10); ev.set(); t.join(timeout=4)
        self.assertFalse(t.is_alive(), 'mission cancellation did not stop terminal.run')
        self.assertIsInstance(results.get('exception'), MissionCancelledError)
        time.sleep(.15)
        self.assertFalse(marker.exists(), 'late child write after cancellation')

    def test_cancel_stops_spawned_child_group(self):
        ev = threading.Event()
        marker = self.root / 'child_late.txt'
        ready = self.root / 'child_spawned.txt'
        child = f'import time,pathlib;time.sleep(1);pathlib.Path({str(marker)!r}).write_text("LATE")'
        parent = (f'import subprocess,sys,time,pathlib;'
                  f'subprocess.Popen([sys.executable,"-c",{child!r}]);'
                  f'pathlib.Path({str(ready)!r}).write_text("READY");time.sleep(2)')
        results = {}
        with self._fake_program(parent):
            t = threading.Thread(target=lambda: self._record(results, lambda: self._run(ev)), daemon=True)
            t.start()
            deadline = time.monotonic() + 2
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(ready.exists(), 'test never observed child process launch')
            ev.set();t.join(timeout=4)
        self.assertFalse(t.is_alive())
        self.assertIsInstance(results.get('exception'), MissionCancelledError)
        time.sleep(.2)
        self.assertFalse(marker.exists())

    def test_cancel_does_not_stop_unrelated_terminal(self):
        ev_cancel = threading.Event()
        ok_event = threading.Event()
        a = {}; b = {}
        with self._fake_program('import time;time.sleep(.4);print("complete")'):
            ta = threading.Thread(target=lambda: self._record(a, lambda: self._run(ev_cancel)), daemon=True)
            tb = threading.Thread(target=lambda: self._record(b, lambda: self._run(ok_event)), daemon=True)
            ta.start(); tb.start(); time.sleep(.10); ev_cancel.set()
            ta.join(timeout=4);tb.join(timeout=4)
        self.assertFalse(ta.is_alive());self.assertFalse(tb.is_alive())
        self.assertIsInstance(a.get('exception'), MissionCancelledError)
        self.assertEqual(b.get('value',{}).get('exit_code'),0)
        self.assertEqual(b['value']['stdout'].strip(),'complete')

    def test_timeout_without_cancel_signal(self):
        ev=threading.Event()
        with self._fake_program('import time;time.sleep(3)'):
            with self.assertRaises(TimeoutError):
                self._run(ev, timeout_ms=100)

    def test_mission_control_cancel_reaches_running_terminal(self):
        control = MissionControl(self.root.parent / 'mission-controls')
        ident = uuid.uuid4().hex
        event = control.register(ident); control.start(ident)
        results = {}
        with self._fake_program('import time;time.sleep(2)'):
            task = threading.Thread(target=lambda: self._record(results, lambda: self._run(event)), daemon=True)
            task.start();time.sleep(.10)
            self.assertEqual(control.cancel(ident)['state'],'cancel_requested')
            task.join(timeout=4)
        self.assertFalse(task.is_alive())
        self.assertIsInstance(results.get('exception'), MissionCancelledError)
        self.assertEqual(control.finish(ident,success=False)['state'],'cancelled')

    def test_no_cancelled_result_or_false_evidence(self):
        ev=threading.Event()
        with self._fake_program('import time;time.sleep(2);print("FAKE SUCCESS")'):
            result={}
            t=threading.Thread(target=lambda: self._record(result,lambda:self._run(ev)),daemon=True)
            t.start();time.sleep(.10);ev.set();t.join(timeout=4)
        self.assertNotIn('value',result)
        self.assertIsInstance(result.get('exception'),MissionCancelledError)

    def test_mission_runner_explicitly_forwards_cancellation(self):
        # Structural integration check only. Full suite runs on user's complete repository.
        source = Path(__file__).resolve().parents[1] / 'src/maestro_gateway/mission.py'
        tree = ast.parse(source.read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == 'execute_tool']
        self.assertEqual(len(calls),1)
        kw = {k.arg:k.value for k in calls[0].keywords}
        self.assertIn('cancellation',kw)
        self.assertIsInstance(kw['cancellation'],ast.Name)
        self.assertEqual(kw['cancellation'].id,'cancellation')

    @staticmethod
    def _record(container, fn):
        try: container['value']=fn()
        except Exception as exc: container['exception']=exc


if __name__ == '__main__':
    unittest.main()
