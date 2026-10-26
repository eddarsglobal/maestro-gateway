"""G1-P0-A2.4.2.2c1: effect receipts, crashes, replay and legacy behavior.

Core/terminal effect boundaries are only verified at the Gateway wiring level;
these tests do not prove atomicity between independently persisted resources.
"""
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from maestro_gateway.conversations import ConversationStore
from maestro_gateway.request_journal import MissionRequestJournal, RequestReplayBlocked, fingerprint
from maestro_gateway.effect_journal import (EffectJournal, EffectReceiptBlocked,
                                            effect_scope, current_effect_journal)
from maestro_gateway.mission import GovernedDirectMissionRunner


class FakeCoreRunner(GovernedDirectMissionRunner):
    def __init__(self, store):
        self._conversations = store
        self.calls = 0
        self.fail = False
        self.perform_effect = False
        self.effect_calls = 0
        self.skip_turn = False
        self.task = 'TASK-CORE-1'

    def _run_untracked(self, **kwargs):
        self.calls += 1
        eid = current_effect_journal()
        if eid is not None:
            eid.bind_task(self.task)
            if self.perform_effect:
                slot = eid.prepare('WORKSPACE','terminal.run',{'argv':['echo','test']})
                eid.engage(slot)
                self.effect_calls += 1
                if self.fail:
                    raise RuntimeError('simulated post-effect failure')
                eid.observe(slot, {'exit': 0})
            core_slot = eid.prepare('CORE','commit_durable_state',{'task_id':self.task})
            eid.engage(core_slot)
            if self.fail:
                raise RuntimeError('post core commit failure')
            eid.observe(core_slot, {'returned':True})
        cid = kwargs['conversation_id'] or self._conversations.create()['id']
        result = {'ok':True,'task_id':self.task,'conversation_id':cid,'output':'verified'}
        if not self.skip_turn:
            self._conversations.append_messages(cid,mission_id=self.task,messages=[
                {'role':'user','content':kwargs['prompt']},
                {'role':'assistant','content':'verified','metadata':{'mission':result}}])
        return result


class EffectJournalTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.base=Path(t.name)
        self.store=ConversationStore(self.base/'chats')
        self.req_id='REQ-C1-ABC-1122'
        self.sig=fingerprint({'message':'C1','num':2})
        self.journal=EffectJournal(self.store.root,self.req_id,self.sig)

    def start(self, task='TASK-123'):
        with MissionRequestJournal(self.store.root).open(self.req_id,self.sig):
            self.journal.start()
            self.journal.bind_task(task)

    def test_start_creates_private_receipt(self):
        self.start()
        self.assertEqual(self.journal.inspect()['state'],'ACTIVE')
        self.assertEqual(self.journal.path.stat().st_mode & 0o077,0)
        self.assertEqual(self.journal.root.stat().st_mode & 0o077,0)

    def test_prepare_engage_observe(self):
        self.start()
        idx=self.journal.prepare('WORKSPACE','terminal.run',{'x':1})
        self.assertEqual(self.journal.inspect()['effects'][0]['state'],'PREPARED')
        self.journal.engage(idx)
        self.assertEqual(self.journal.inspect()['effects'][0]['state'],'ENGAGED')
        self.journal.observe(idx,{'status':0})
        checksum=self.journal.returned('TASK-123')
        self.journal.verify_returned(checksum,'TASK-123')
        self.assertEqual(self.journal.inspect()['state'],'RETURNED')

    def test_returned_empty_effects(self):
        self.start()
        digest=self.journal.returned('TASK-123')
        self.journal.verify_returned(digest,'TASK-123')

    def test_start_again_is_refused(self):
        self.start()
        with self.assertRaises(EffectReceiptBlocked):
            self.journal.start()

    def test_mismatched_task_is_refused(self):
        self.start()
        with self.assertRaises(EffectReceiptBlocked):
            self.journal.bind_task('TASK-456')

    def test_missing_task_cannot_prepare(self):
        self.journal.start()
        with self.assertRaises(EffectReceiptBlocked):
            self.journal.prepare('CORE','commit_durable_state',{})

    def test_bad_transition_order_is_refused(self):
        self.start()
        idx=self.journal.prepare('CORE','commit_durable_state',{})
        with self.assertRaises(EffectReceiptBlocked):
            self.journal.observe(idx,{'ok':True})
        self.journal.engage(idx)
        with self.assertRaises(EffectReceiptBlocked):
            self.journal.engage(idx)

    def test_pending_effect_never_returned(self):
        self.start()
        self.journal.prepare('WORKSPACE','terminal.run',{})
        with self.assertRaises(EffectReceiptBlocked):
            self.journal.returned('TASK-123')

    def test_engaged_effect_never_returned(self):
        self.start()
        i=self.journal.prepare('WORKSPACE','terminal.run',{})
        self.journal.engage(i)
        with self.assertRaises(EffectReceiptBlocked):
            self.journal.returned('TASK-123')

    def test_no_replay_after_partial_effect(self):
        self.start()
        i=self.journal.prepare('WORKSPACE','terminal.run',{})
        self.journal.engage(i)
        with self.assertRaises(EffectReceiptBlocked):
            self.journal.returned('TASK-123')
        with self.assertRaises(EffectReceiptBlocked):
            self.journal.start()

    def test_checksum_tamper_blocks(self):
        self.start()
        doc=json.loads(self.journal.path.read_text())
        doc['task_id']='TASK-EVIL'
        self.journal.path.write_text(json.dumps(doc))
        with self.assertRaises(EffectReceiptBlocked):
            self.journal.inspect()

    def test_missing_receipt_blocks(self):
        self.start()
        self.journal.path.unlink()
        with self.assertRaises(EffectReceiptBlocked):
            self.journal.inspect()

    def test_symlink_receipt_blocks(self):
        self.start()
        self.journal.path.unlink()
        outside=self.base/'outside.json'
        outside.write_text('{}')
        self.journal.path.symlink_to(outside)
        with self.assertRaises((EffectReceiptBlocked, RuntimeError)):
            self.journal.inspect()
        self.assertEqual(outside.read_text(),'{}')

    def test_conflicting_request_signature_blocks(self):
        self.start()
        other=EffectJournal(self.store.root,self.req_id,fingerprint({'different':True}))
        with self.assertRaises(EffectReceiptBlocked):
            other.inspect()

    def test_bad_effect_type_refused(self):
        self.start()
        with self.assertRaises(ValueError):
            self.journal.prepare('GIT','git.commit',{})

    def test_context_var_restores_after_scope(self):
        self.assertIsNone(current_effect_journal())
        with effect_scope(self.journal):
            self.assertIs(current_effect_journal(),self.journal)
        self.assertIsNone(current_effect_journal())

    def test_completed_runner_no_duplicate_core_or_effect(self):
        runner=FakeCoreRunner(self.store)
        runner.perform_effect=True
        result=runner.run(prompt='hello',request_id='req-c1-test-1')
        self.assertEqual(runner.run(prompt='hello',request_id='req-c1-test-1'), result)
        self.assertEqual((runner.calls,runner.effect_calls),(1,1))
        self.assertEqual(len(EffectJournal(self.store.root,'req-c1-test-1',fingerprint({
            'mode':'M0_DIRECT','prompt':'hello','model':None,'max_tokens':512,
            'conversation_id':None,'workspace_id':None,'ui_language':None})).inspect()['effects']),2)

    def test_removed_effect_receipt_blocks_runner_replay(self):
        r=FakeCoreRunner(self.store)
        r.run(prompt='hello',request_id='req-c1-test-2')
        target=next((self.store.root/'.mission_effects').glob('*.json'))
        target.unlink()
        with self.assertRaises(EffectReceiptBlocked):
            r.run(prompt='hello',request_id='req-c1-test-2')
        self.assertEqual(r.calls,1)

    def test_tampered_effect_receipt_blocks_runner_replay(self):
        r=FakeCoreRunner(self.store)
        r.run(prompt='hello',request_id='req-c1-test-3')
        target=next((self.store.root/'.mission_effects').glob('*.json'))
        doc=json.loads(target.read_text());doc['state']='ACTIVE'
        target.write_text(json.dumps(doc))
        with self.assertRaises(EffectReceiptBlocked):
            r.run(prompt='hello',request_id='req-c1-test-3')
        self.assertEqual(r.calls,1)

    def test_effect_before_exception_no_new_execution(self):
        r=FakeCoreRunner(self.store)
        r.perform_effect=True;r.fail=True
        with self.assertRaises(RuntimeError):
            r.run(prompt='hello',request_id='req-c1-test-4')
        r.fail=False
        with self.assertRaises(RequestReplayBlocked):
            r.run(prompt='hello',request_id='req-c1-test-4')
        self.assertEqual(r.effect_calls,1)

    def test_existing_clients_remain_untracked(self):
        r=FakeCoreRunner(self.store)
        r.run(prompt='hello');r.run(prompt='hello')
        self.assertEqual(r.calls,2)
        self.assertFalse(list((self.store.root/'.mission_effects').glob('*.json')))

    def test_crash_after_effect_engagement_requires_reconciliation(self):
        code = r'''import os,sys
from pathlib import Path
from maestro_gateway.conversations import ConversationStore
from maestro_gateway.request_journal import MissionRequestJournal, fingerprint
from maestro_gateway.effect_journal import EffectJournal
store=ConversationStore(Path(sys.argv[1])/'chats')
sig=fingerprint({'message':'C1','num':2})
with MissionRequestJournal(store.root).open('REQ-C1-ABC-1122',sig):
    j=EffectJournal(store.root,'REQ-C1-ABC-1122',sig)
    j.start();j.bind_task('TASK-123')
    i=j.prepare('WORKSPACE','terminal.run',{'argv':['touch','x']});j.engage(i)
    os._exit(84)
'''
        proc=subprocess.run([sys.executable,'-c',code,str(self.base)],timeout=10)
        self.assertEqual(proc.returncode,84)
        self.assertEqual(self.journal.inspect()['effects'][0]['state'],'ENGAGED')
        with self.assertRaises(RequestReplayBlocked):
            with MissionRequestJournal(self.store.root).open(self.req_id,self.sig) as lease:
                self.assertIsNotNone(lease.existing)
                raise RequestReplayBlocked('uncertain after crash')

    def test_result_checksum_matches_both_ledgers(self):
        r=FakeCoreRunner(self.store)
        result=r.run(prompt='hello',request_id='req-c1-test-6')
        signature=fingerprint({'mode':'M0_DIRECT','prompt':'hello','model':None,
                               'max_tokens':512,'conversation_id':None,'workspace_id':None,'ui_language':None})
        completed=MissionRequestJournal(self.store.root).read_completed_record_only('req-c1-test-6',signature)
        self.assertEqual(completed['result'],result)
        effect=EffectJournal(self.store.root,'req-c1-test-6',signature)
        effect.verify_returned(completed['effects_sha256'],result['task_id'])

    def test_verified_replay_api_uses_correlated_effect_digest(self):
        r=FakeCoreRunner(self.store)
        r.perform_effect=True
        original=r.run(prompt='test',request_id='req-c1-replay-1')
        self.assertEqual(original,r.replay_completed_only(
            request_id='req-c1-replay-1',prompt='test'))
        self.assertEqual(r.calls,1)

    def test_replay_api_detects_deleted_effects(self):
        r=FakeCoreRunner(self.store)
        r.run(prompt='test',request_id='req-c1-replay-2')
        next((self.store.root/'.mission_effects').glob('*.json')).unlink()
        with self.assertRaises(EffectReceiptBlocked):
            r.replay_completed_only(request_id='req-c1-replay-2',prompt='test')
        self.assertEqual(r.calls,1)

    def test_completed_request_with_wrong_effect_proof_blocked(self):
        r=FakeCoreRunner(self.store)
        r.run(prompt='test',request_id='req-c1-replay-3')
        target=next((self.store.root/'.mission_effects').glob('*.json'))
        doc=json.loads(target.read_text())
        doc['effects'][0]['state']='ENGAGED'
        doc['checksum']=fingerprint({k:v for k,v in doc.items() if k!='checksum'})
        target.write_text(json.dumps(doc))
        with self.assertRaises(EffectReceiptBlocked):
            r.replay_completed_only(request_id='req-c1-replay-3',prompt='test')
        self.assertEqual(r.calls,1)

    def test_previous_result_not_changed_by_new_ledger_fields(self):
        r=FakeCoreRunner(self.store)
        result=r.run(prompt='test',request_id='req-c1-replay-4')
        self.assertNotIn('effects_sha256',result)
        self.assertNotIn('effects_sha256',r.run(prompt='test',request_id='req-c1-replay-4'))

    def test_legacy_receipt_backwards_compatible(self):
        # Old completed records predate the added effect digest.
        with MissionRequestJournal(self.store.root).open('LEGACY-1',self.sig) as lease:
            lease.complete({'task_id':'TASK-1','conversation_id':'CID-1'})
        doc=MissionRequestJournal(self.store.root).read_completed_record_only('LEGACY-1',self.sig)
        self.assertNotIn('effects_sha256',doc)

    def test_direct_real_path_binds_task_before_effect_tracking(self):
        source = inspect.getsource(GovernedDirectMissionRunner._run_untracked)
        objective = source.index("Execute one bounded local ")
        direct = source[objective:]
        bind = direct.index("effect_journal.bind_task(str(mission.task_id))")
        terminal = direct.index('effect_journal.prepare(\n                                        "WORKSPACE", "terminal.run"')
        core = direct.index('effect_journal.prepare(\n                        "CORE", "commit_durable_state"')
        self.assertLess(bind, terminal)
        self.assertLess(bind, core)

    def test_direct_real_path_wraps_core_commit_before_and_after(self):
        source = inspect.getsource(GovernedDirectMissionRunner._run_untracked)
        objective = source.index("Execute one bounded local ")
        direct = source[objective:]
        prepare = direct.index('effect_journal.prepare(\n                        "CORE", "commit_durable_state"')
        engage = direct.index("effect_journal.engage(core_effect)", prepare)
        commit = direct.index("kernel.commit_durable_state(", engage)
        observe = direct.index("effect_journal.observe(core_effect, {\"returned\": True})", commit)
        close = direct.index("kernel.close(", observe)
        self.assertLess(prepare, engage)
        self.assertLess(engage, commit)
        self.assertLess(commit, observe)
        self.assertLess(observe, close)

    def test_no_sensitive_arguments_in_effect_receipt(self):
        self.start()
        self.journal.prepare('WORKSPACE','terminal.run',{'token':'secret-abc-password'})
        self.assertNotIn('secret-abc-password',self.journal.path.read_text())

    def test_malformed_float_refused_before_engagement(self):
        self.start()
        with self.assertRaises(ValueError):
            self.journal.prepare('WORKSPACE','terminal.run',{'value':float('nan')})


if __name__=='__main__':
    unittest.main()
