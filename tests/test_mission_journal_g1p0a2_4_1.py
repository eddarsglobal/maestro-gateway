"""A2.4.1 crash-aware cancellation journal regression tests.

These verify state publication and recovery; they do NOT claim filesystem/Core
transaction atomicity or safe replay of all external side effects.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

from maestro_gateway.mission_control import (
    MissionCancelledError, MissionConflictError, MissionControl,
    MissionJournalError,
)


class JournalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name) / 'missions'
        self.store = MissionControl(self.directory)
        self.request_id = uuid.uuid4().hex

    def test_register_and_start(self):
        self.store.register(self.request_id)
        self.store.start(self.request_id)
        self.assertEqual(self.store.status(self.request_id)['state'], 'running')

    def test_record_private_permissions_and_json(self):
        self.store.register(self.request_id)
        path = self.store._path(self.request_id)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(json.loads(path.read_text())['request_id'], self.request_id)

    def test_cancel_queued_prevents_start_after_restart(self):
        ev = self.store.register(self.request_id)
        self.assertEqual(self.store.cancel(self.request_id)['state'], 'cancel_requested')
        self.assertTrue(ev.is_set())
        with self.assertRaises(MissionCancelledError):
            self.store.start(self.request_id)
        self.assertEqual(MissionControl(self.directory).status(self.request_id)['state'], 'interrupted')

    def test_cancelled_result_stays_cancelled(self):
        self.store.register(self.request_id)
        self.store.start(self.request_id)
        self.store.cancel(self.request_id)
        self.assertEqual(self.store.finish(self.request_id, success=False)['state'], 'cancelled')
        self.assertEqual(MissionControl(self.directory).status(self.request_id)['state'], 'cancelled')

    def test_cancel_after_proven_commit_preserves_completion(self):
        self.store.register(self.request_id)
        self.store.start(self.request_id)
        self.store.cancel(self.request_id)
        result = self.store.finish(self.request_id, success=True, durable_committed=True)
        self.assertEqual(result['state'], 'completed')
        self.assertTrue(result['late_cancel_requested'])
        self.assertTrue(result['durable_commit_observed'])

    def test_cancel_success_without_commit_proof_is_indeterminate(self):
        self.store.register(self.request_id)
        self.store.start(self.request_id)
        self.store.cancel(self.request_id)
        result = self.store.finish(self.request_id, success=True, durable_committed=False)
        self.assertEqual(result['state'], 'interrupted')
        self.assertTrue(result['outcome_ambiguous'])

    def test_success_without_commit_proof_is_indeterminate(self):
        self.store.register(self.request_id)
        self.store.start(self.request_id)
        result = self.store.finish(self.request_id, success=True, durable_committed=False)
        self.assertEqual(result['state'], 'interrupted')

    def test_completed_immutable_under_cancel_and_repeated_finish(self):
        self.store.register(self.request_id)
        self.store.start(self.request_id)
        result = self.store.finish(self.request_id, success=True, durable_committed=True)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(self.store.cancel(self.request_id), result)
        self.assertEqual(self.store.finish(self.request_id, success=False, cancelled=True), result)

    def test_no_registration_after_existing_record(self):
        self.store.register(self.request_id)
        with self.assertRaises(MissionConflictError):
            self.store.register(self.request_id)
        with self.assertRaises(MissionConflictError):
            MissionControl(self.directory).register(self.request_id)

    def test_register_failure_keeps_no_inmemory_token(self):
        with patch.object(self.store, '_store', side_effect=OSError('disk fault')):
            with self.assertRaises(OSError):
                self.store.register(self.request_id)
        self.assertNotIn(self.request_id, self.store._active)

    def test_finish_failure_keeps_cancellation_token(self):
        ev = self.store.register(self.request_id)
        self.store.start(self.request_id)
        with patch.object(self.store, '_store', side_effect=OSError('disk fault')):
            with self.assertRaises(OSError):
                self.store.finish(self.request_id, success=False)
        self.assertIs(ev, self.store._active[self.request_id])

    def test_corrupt_json_aborts_recovery(self):
        self.store._path(self.request_id).write_text('{broken')
        with self.assertRaises(MissionJournalError):
            MissionControl(self.directory)

    def test_cross_id_record_cannot_rewrite_other_record(self):
        rid2 = uuid.uuid4().hex
        self.store._path(self.request_id).write_text(json.dumps({'request_id': rid2, 'state':'running'}))
        with self.assertRaises(MissionJournalError):
            MissionControl(self.directory)
        self.assertFalse(self.store._path(rid2).exists())

    def test_symlink_record_rejected(self):
        target = self.directory / 'safe.txt'
        target.write_text('safe')
        self.store._path(self.request_id).symlink_to(target)
        with self.assertRaises(MissionJournalError):
            MissionControl(self.directory)
        self.assertEqual(target.read_text(), 'safe')

    def test_inflight_records_interrupted_terminal_records_kept(self):
        ids = [uuid.uuid4().hex for _ in range(4)]
        for rid in ids:
            self.store.register(rid)
        self.store.start(ids[1]); self.store.start(ids[2]); self.store.start(ids[3])
        self.store.cancel(ids[2])
        self.store.finish(ids[3], success=False)
        recovered = MissionControl(self.directory)
        for rid in ids[:3]:
            row = recovered.status(rid)
            self.assertEqual(row['state'], 'interrupted')
            self.assertTrue(row['outcome_ambiguous'])
        self.assertEqual(recovered.status(ids[3])['state'], 'failed')

    def test_isolated_concurrent_finishes(self):
        a, b = self.request_id, uuid.uuid4().hex
        self.store.register(a); self.store.register(b)
        self.store.start(a); self.store.start(b)
        self.store.cancel(a)
        errors = []
        def finish(rid, success):
            try:
                self.store.finish(rid, success=success, durable_committed=success)
            except Exception as exc:
                errors.append(exc)
        t1 = threading.Thread(target=finish, args=(a, False)); t2 = threading.Thread(target=finish, args=(b, True))
        t1.start();t2.start(); t1.join();t2.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.store.status(a)['state'], 'cancelled')
        self.assertEqual(self.store.status(b)['state'], 'completed')

    def test_temp_files_not_used_as_authority(self):
        self.directory.joinpath('f'*32+'.abcd.tmp').write_text('partial')
        self.store.register(self.request_id)
        recovered = MissionControl(self.directory)
        self.assertEqual(recovered.status(self.request_id)['state'], 'interrupted')

    def test_orphaned_transaction_temp_retires_request_id(self):
        orphan = self.directory / (self.request_id + '.' + uuid.uuid4().hex + '.tmp')
        orphan.write_bytes(b'{partial json')
        reopened = MissionControl(self.directory)
        row = reopened.status(self.request_id)
        self.assertEqual(row['state'], 'interrupted')
        self.assertTrue(row['outcome_ambiguous'])
        with self.assertRaises(MissionConflictError):
            reopened.register(self.request_id)
        self.assertEqual(orphan.read_bytes(), b'{partial json')

    def test_orphaned_temp_cannot_overwrite_completed_record(self):
        self.store.register(self.request_id)
        self.store.start(self.request_id)
        self.store.finish(self.request_id, success=True, durable_committed=True)
        orphan = self.directory / (self.request_id + '.' + uuid.uuid4().hex + '.tmp')
        orphan.write_bytes(b'incomplete')
        row = MissionControl(self.directory).status(self.request_id)
        self.assertEqual(row['state'], 'completed')
        self.assertTrue(orphan.exists())

    def test_real_abrupt_process_exit_reconciles_queued_record(self):
        mod = Path(__file__).resolve().parents[1] / 'src'
        rid = self.request_id
        code = ('import sys;from pathlib import Path;'
                'from maestro_gateway.mission_control import MissionControl;'
                f'MissionControl(Path({str(self.directory)!r})).register({rid!r});'
                'sys.stdout.flush();import os;os._exit(23)')
        env = dict(os.environ)
        env['PYTHONPATH'] = str(mod) + os.pathsep + env.get('PYTHONPATH', '')
        proc = subprocess.run([sys.executable, '-B', '-c', code], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        self.assertEqual(proc.returncode, 23, proc.stderr.decode(errors='replace'))
        row = MissionControl(self.directory).status(rid)
        self.assertEqual(row['state'], 'interrupted')
        self.assertEqual(row['previous_state_before_restart'], 'queued')
        with self.assertRaises(MissionConflictError):
            MissionControl(self.directory).register(rid)

    def test_durable_directory_sync_failure_never_publishes_active_token(self):
        real_fsync = os.fsync
        counter = [0]
        def fail_second(fd):
            counter[0] += 1
            if counter[0] == 2:
                raise OSError('injected directory sync failure')
            return real_fsync(fd)
        with patch('maestro_gateway.mission_control.os.fsync', side_effect=fail_second):
            with self.assertRaises(OSError):
                self.store.register(self.request_id)
        self.assertNotIn(self.request_id, self.store._active)
        # The rename may have landed before directory fsync failed; do not replay.
        if self.store._path(self.request_id).exists():
            with self.assertRaises(MissionConflictError):
                self.store.register(self.request_id)

    def test_server_finishes_with_explicit_core_tx_receipt(self):
        source = (Path(__file__).resolve().parents[1] / 'src' /
                  'maestro_gateway' / 'server.py').read_text()
        self.assertEqual(source.count('durable_committed=bool(result and result.get("tx_committed"))'), 1)

    def test_persistent_replace_and_directory_sync(self):
        real_fsync = os.fsync
        seen = []
        def recording(fd):
            seen.append(os.fstat(fd).st_mode)
            return real_fsync(fd)
        with patch('maestro_gateway.mission_control.os.fsync', side_effect=recording):
            self.store.register(self.request_id)
        self.assertGreaterEqual(len(seen), 2, 'File and directory fsync must both occur')


if __name__ == '__main__':
    unittest.main()
