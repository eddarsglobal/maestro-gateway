"""A2.4.2.1: atomic one-file conversation turn; not cross-store atomicity."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from maestro_gateway.conversations import ConversationStore


class TurnTests(unittest.TestCase):
    def setUp(self):
        td=tempfile.TemporaryDirectory();self.addCleanup(td.cleanup)
        self.root=Path(td.name)/'chats';self.store=ConversationStore(self.root)
        self.cid=self.store.create(first_message='question')['id']

    def turn(self, cid=None, label='question'):
        return self.store.append_messages(cid or self.cid, messages=[
            {'role':'user','content':label},
            {'role':'assistant','content':'answer','metadata':{'mission':{'tx_committed':True}}}])

    def test_atomic_batch_contains_both_roles(self):
        row=self.turn();self.assertEqual([m['role'] for m in row['messages']],['user','assistant'])
        self.assertTrue(row['messages'][1]['metadata']['mission']['tx_committed'])

    def test_only_one_publication(self):
        with patch.object(self.store,'_write',wraps=self.store._write) as call:
            self.turn();self.assertEqual(call.call_count,1)
            self.assertEqual(len(call.call_args.args[0]['messages']),2)

    def test_invalid_second_message_blocks_first(self):
        with self.assertRaises(ValueError):self.store.append_messages(self.cid,messages=[
            {'role':'user','content':'q'},{'role':'assistant','content':'   '}])
        self.assertEqual(self.store.get(self.cid)['messages'],[])

    def test_invalid_metadata_blocks_batch(self):
        with self.assertRaises(ValueError):self.store.append_messages(self.cid,messages=[
            {'role':'user','content':'q'},{'role':'assistant','content':'a','metadata':'bad'}])
        self.assertEqual(self.store.get(self.cid)['messages'],[])

    def test_empty_batch_refused(self):
        for messages in ([],None,'bad'):
            with self.subTest(messages=messages),self.assertRaises(ValueError):
                self.store.append_messages(self.cid,messages=messages)

    def test_rename_failure_leaves_old_revision(self):
        old=self.store._path(self.cid).read_bytes()
        with patch('maestro_gateway.conversations.os.replace',side_effect=OSError('rename fault')):
            with self.assertRaises(OSError):self.turn()
        self.assertEqual(self.store._path(self.cid).read_bytes(),old)

    def test_fsync_failure_before_rename_leaves_old_revision(self):
        old=self.store._path(self.cid).read_bytes()
        with patch('maestro_gateway.conversations.os.fsync',side_effect=OSError('disk fault')):
            with self.assertRaises(OSError):self.turn()
        self.assertEqual(self.store._path(self.cid).read_bytes(),old)

    def _crash(self, code, expected):
        env=dict(os.environ);env['PYTHONPATH']=os.pathsep.join(sys.path)
        child=f'''import os,sys
from pathlib import Path
import maestro_gateway.conversations as c
s=c.ConversationStore(Path(sys.argv[1])); cid=sys.argv[2]
orig=c.os.replace
{code}
s.append_messages(cid,messages=[{{'role':'user','content':'Q'}},{{'role':'assistant','content':'A'}}])
'''
        p=subprocess.run([sys.executable,'-B','-c',child,str(self.root),self.cid],env=env,timeout=8)
        self.assertEqual(p.returncode,expected)

    def test_crash_before_rename_keeps_old_messages(self):
        self._crash('def replace(a,b): os._exit(79)\nc.os.replace=replace',79)
        self.assertEqual(self.store.get(self.cid)['messages'],[])
        self.turn();self.assertEqual(len(self.store.get(self.cid)['messages']),2)

    def test_crash_after_rename_keeps_complete_turn(self):
        self._crash('def replace(a,b): orig(a,b);os._exit(81)\nc.os.replace=replace',81)
        self.assertEqual([m['role'] for m in self.store.get(self.cid)['messages']],['user','assistant'])

    def test_threads_same_chat_do_not_interleave_turns(self):
        errors=[]
        def writer(label):
            try:
                for i in range(5):self.store.append_messages(self.cid,messages=[
                    {'role':'user','content':label},{'role':'assistant','content':label}])
            except Exception as exc:errors.append(exc)
        workers=[threading.Thread(target=writer,args=(str(i),)) for i in range(4)]
        for t in workers:t.start()
        for t in workers:t.join()
        self.assertFalse(errors)
        messages=self.store.get(self.cid)['messages'];self.assertEqual(len(messages),40)
        for i in range(0,len(messages),2):self.assertEqual(messages[i]['content'],messages[i+1]['content'])

    def test_separate_conversations_isolated(self):
        other=self.store.create(first_message='another')['id']
        self.turn();self.turn(other,'other')
        self.assertNotEqual(self.store.get(self.cid)['messages'][0]['content'],self.store.get(other)['messages'][0]['content'])

    def test_legacy_append_remains_valid(self):
        self.store.append_message(self.cid,role='user',content='older')
        self.assertEqual(self.store.get(self.cid)['messages'][0]['content'],'older')

    def test_failed_attempt_batch_keeps_hidden_telemetry(self):
        record=self.store.append_messages(self.cid,messages=[
            {'role':'user','content':'Q'},
            {'role':'system','content':'MAESTRO_MISSION_FAILED','metadata':{'mission':{'ok':False}}}])
        self.assertEqual([m['role'] for m in record['messages']],['user','system'])

    def test_private_file_and_no_success_temp(self):
        self.turn();self.assertEqual(self.store._path(self.cid).stat().st_mode & 0o777,0o600)
        self.assertEqual(list(self.root.glob('*.tmp')),[])

    def test_symlinked_record_refused(self):
        file=self.store._path(self.cid);target=self.root/'target';target.write_text('safe')
        file.unlink();file.symlink_to(target)
        with self.assertRaises(ValueError):self.turn()
        self.assertEqual(target.read_text(),'safe')

    def test_symlinked_root_refused(self):
        link=self.root.parent/'link';link.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(ValueError):ConversationStore(link)

    def test_corrupt_record_cannot_be_overwritten(self):
        self.store._path(self.cid).write_text('{invalid')
        with self.assertRaises(ValueError):self.turn()
        self.assertEqual(self.store._path(self.cid).read_text(),'{invalid')

    def test_title_from_first_user_and_preview(self):
        row=self.store.create()
        self.turn(row['id'],'A distinctive question')
        self.assertTrue(self.store.get(row['id'])['title'].startswith('A distinctive question'))
        self.assertEqual(next(x for x in self.store.list() if x['id']==row['id'])['preview'],'answer')

if __name__=='__main__': unittest.main()
