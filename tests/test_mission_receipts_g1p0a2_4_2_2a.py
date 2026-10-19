"""G1-P0-A2.4.2.2a: conversation receipt integration and crash safety."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from maestro_gateway.conversations import ConversationStore


class MissionReceiptsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'chats'
        self.store = ConversationStore(self.root)
        self.cid = self.store.create()['id']

    def msgs(self, task='TASK-A', answer='Réponse'):
        return [{'role': 'user', 'content': 'مرحبا'},
                {'role': 'assistant', 'content': answer,
                 'metadata': {'mission': {'task_id': task, 'tx_committed': True}}}]

    def publish(self, task='TASK-A', answer='Réponse'):
        return self.store.append_messages(self.cid, mission_id=task,
                                          messages=self.msgs(task, answer))

    def tamper(self, update):
        path = self.store._path(self.cid)
        value = json.loads(path.read_text())
        update(value)
        path.write_text(json.dumps(value))

    def test_receipt_published_atomically(self):
        row = self.publish()
        self.assertEqual(len(row['messages']), 2)
        self.assertEqual(row['mission_turns']['TASK-A']['start'], 0)
        self.assertEqual(self.store.inspect_mission_turn(self.cid, mission_id='TASK-A')['status'], 'PUBLISHED')

    def test_identical_retry_does_not_write(self):
        self.publish()
        with patch.object(self.store, '_write', wraps=self.store._write) as writer:
            self.publish()
            self.assertEqual(writer.call_count, 0)
        self.assertEqual(len(self.store.get(self.cid)['messages']), 2)

    def test_changed_response_conflicts(self):
        self.publish()
        with self.assertRaisesRegex(ValueError, 'Conflicting retry'):
            self.publish(answer='different')
        self.assertEqual(len(self.store.get(self.cid)['messages']), 2)

    def test_changed_metadata_conflicts(self):
        self.publish()
        items = self.msgs()
        items[-1]['metadata']['mission']['metrics'] = {'latency': 5}
        with self.assertRaisesRegex(ValueError, 'Conflicting retry'):
            self.store.append_messages(self.cid, mission_id='TASK-A', messages=items)

    def test_invalid_identity_rejected(self):
        for bad in ('', '../escape', 'invalid identity', 'path/key', 'x'*161, '\x00'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.store.append_messages(self.cid, mission_id=bad, messages=self.msgs())

    def test_mismatching_core_id_rejected(self):
        with self.assertRaisesRegex(ValueError, 'mismatches'):
            self.store.append_messages(self.cid, mission_id='TASK-A', messages=self.msgs('TASK-B'))
        self.assertEqual(self.store.get(self.cid)['messages'], [])

    def test_legacy_turn_requires_manual_reconciliation(self):
        self.store.append_messages(self.cid, messages=self.msgs())
        self.assertEqual(self.store.inspect_mission_turn(self.cid, mission_id='TASK-A')['status'], 'LEGACY_UNCERTAIN')
        with self.assertRaisesRegex(RuntimeError, 'manual reconciliation'):
            self.publish()

    def test_missing_is_not_autoreplay_permission(self):
        self.assertEqual(self.store.inspect_mission_turn(self.cid, mission_id='TASK-Z')['status'], 'ABSENT')

    def test_tamper_receipt_digest_fails_closed(self):
        self.publish()
        self.tamper(lambda row: row['mission_turns']['TASK-A'].update(sha256='0'*64))
        with self.assertRaisesRegex(ValueError, 'fingerprint mismatch'):
            self.publish()

    def test_tamper_persisted_message_fails_closed(self):
        self.publish()
        self.tamper(lambda row: row['messages'][1].update(content='tampered'))
        with self.assertRaisesRegex(ValueError, 'fingerprint mismatch'):
            self.store.inspect_mission_turn(self.cid, mission_id='TASK-A')

    def test_truncated_group_fails_closed(self):
        self.publish()
        self.tamper(lambda row: row['messages'].pop())
        with self.assertRaisesRegex(ValueError, 'Corrupt mission receipt'):
            self.publish()

    def test_invalid_receipt_container_fails_closed(self):
        self.tamper(lambda row: row.update(mission_turns=[]))
        with self.assertRaisesRegex(ValueError, 'Invalid mission receipts'):
            self.publish()

    def test_rename_failure_leaves_no_receipt_or_messages(self):
        with patch('maestro_gateway.conversations.os.replace', side_effect=OSError('fault')):
            with self.assertRaises(OSError): self.publish()
        row = self.store.get(self.cid)
        self.assertEqual(row['messages'], [])
        self.assertNotIn('mission_turns', row)

    def test_real_crash_before_rename_preserves_absent_receipt(self):
        script = '''''''''import os,sys
from pathlib import Path
import maestro_gateway.conversations as c
store=c.ConversationStore(Path(sys.argv[1]))
def replace(a,b):os._exit(86)
c.os.replace=replace
store.append_messages(sys.argv[2],mission_id='TASK-A',messages=[{'role':'user','content':'مرحبا'},{'role':'assistant','content':'Réponse','metadata':{'mission':{'task_id':'TASK-A','tx_committed':True}}}])
'''''''''
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
        run = subprocess.run([sys.executable, '-B', '-c', script, str(self.root), self.cid],
                             env=env, timeout=10)
        self.assertEqual(run.returncode, 86)
        self.assertEqual(self.store.inspect_mission_turn(self.cid, mission_id='TASK-A')['status'], 'ABSENT')
        self.assertEqual(self.store.get(self.cid)['messages'], [])

    def test_real_crash_after_rename_retains_receipt(self):
        script = '''import os,sys
from pathlib import Path
import maestro_gateway.conversations as c
store=c.ConversationStore(Path(sys.argv[1])); real=c.os.replace
def replace(a,b):real(a,b);os._exit(87)
c.os.replace=replace
store.append_messages(sys.argv[2],mission_id='TASK-A',messages=[{'role':'user','content':'مرحبا'},{'role':'assistant','content':'Réponse','metadata':{'mission':{'task_id':'TASK-A','tx_committed':True}}}])
'''
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
        run = subprocess.run([sys.executable, '-B', '-c', script, str(self.root), self.cid],
                             env=env, timeout=10)
        self.assertEqual(run.returncode, 87)
        self.assertEqual(self.store.inspect_mission_turn(self.cid, mission_id='TASK-A')['status'], 'PUBLISHED')
        self.publish()
        self.assertEqual(len(self.store.get(self.cid)['messages']), 2)

    def test_multiple_task_receipts_are_separate(self):
        self.publish('TASK-A')
        self.publish('TASK-B')
        self.publish('TASK-A')
        self.assertEqual(len(self.store.get(self.cid)['messages']), 4)
        self.assertEqual(self.store.inspect_mission_turn(self.cid, mission_id='TASK-B')['start'], 2)

    def test_concurrent_same_mission_not_duplicated(self):
        errors = []
        def worker():
            try: self.publish()
            except Exception as exc: errors.append(exc)
        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.store.get(self.cid)['messages']), 2)

    def test_failed_mission_receipted(self):
        msgs = [{'role':'user','content':'Q'}, {'role':'system','content':'MAESTRO_MISSION_FAILED',
                'metadata':{'mission':{'task_id':'TASK-F','ok':False}}}]
        self.store.append_messages(self.cid,mission_id='TASK-F',messages=msgs)
        self.store.append_messages(self.cid,mission_id='TASK-F',messages=msgs)
        self.assertEqual(len(self.store.get(self.cid)['messages']), 2)

    def test_legacy_manual_message_kept(self):
        self.store.append_message(self.cid,role='user',content='previous')
        self.publish()
        self.assertEqual(self.store.inspect_mission_turn(self.cid, mission_id='TASK-A')['start'], 1)

    def test_symlinked_conversation_refused(self):
        target = self.root / 'other'; target.write_text('safe')
        self.store._path(self.cid).unlink()
        self.store._path(self.cid).symlink_to(target)
        with self.assertRaises(ValueError): self.publish()
        self.assertEqual(target.read_text(), 'safe')

if __name__ == '__main__': unittest.main()
