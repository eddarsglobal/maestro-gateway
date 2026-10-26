"""P0.4.1 adversarial and recovery tests, stdlib only, no LLM/Cloud."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import stat
import sys
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

SRC = Path(__file__).resolve().parents[1]/'src/maestro_gateway/sovereign_evidence_ledger.py'
spec = importlib.util.spec_from_file_location('p04_ledger', SRC)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
V1='a'*64
V2='b'*64

class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'ledger'
        self.store=m.SovereignEvidenceLedger(self.root)

    def record(self, status='TECHNICAL_DELIVERED', receipt='id-01', version=V1,
               resource='ollama::model:1.0', language='fr', task='normal'):
        return self.store.record(resource,version,language,task,status,receipt_id=receipt)

    def test_empty_store_has_no_fluency(self):
        x=self.store.snapshot('ollama::model:1.0',V1)
        self.assertEqual(x['profiles'],[])
        self.assertFalse(x['fluency_certified'])
        self.assertFalse(x['objective_certified'])

    def test_technical_delivery_is_not_semantic_promotion(self):
        self.record()
        x=self.store.snapshot('ollama::model:1.0',V1)
        self.assertEqual(x['profiles'][0]['counts']['TECHNICAL_DELIVERED'],1)
        self.assertFalse(x['profiles'][0]['qualified'])
        self.assertFalse(x['profiles'][0]['linguistic_certified'])

    def test_duplicate_mission_is_not_double_counted(self):
        self.assertTrue(self.record())
        self.assertFalse(self.record())
        self.assertEqual(self.store.snapshot('ollama::model:1.0',V1)['sequence'],1)

    def test_restart_persists(self):
        self.record(); other=m.SovereignEvidenceLedger(self.root)
        self.assertEqual(other.snapshot('ollama::model:1.0',V1)['sequence'],1)

    def test_digest_change_isolated(self):
        self.record(); self.record(version=V2,receipt='id2',status='LINGUISTIC_DEFECT')
        a=self.store.snapshot('ollama::model:1.0',V1)
        b=self.store.snapshot('ollama::model:1.0',V2)
        self.assertEqual(a['profiles'][0]['counts']['LINGUISTIC_DEFECT'],0)
        self.assertEqual(b['profiles'][0]['counts']['LINGUISTIC_DEFECT'],1)
        self.assertFalse(self.store.should_trial_alternative('ollama::model:1.0',V1,'fr'))

    def test_language_and_task_are_scoped(self):
        self.record('LINGUISTIC_DEFECT','x1')
        self.record('LINGUISTIC_DEFECT','x2',language='ar',task='code')
        self.assertFalse(self.store.should_trial_alternative('ollama::model:1.0',V1,'fr'))
        self.assertFalse(self.store.should_trial_alternative('ollama::model:1.0',V1,'ar'))

    def test_two_independent_bad_missions_mark_degraded_not_promoted(self):
        self.record('LINGUISTIC_DEFECT','x1')
        self.record('TECHNICAL_DELIVERED','x2')
        self.record('LINGUISTIC_DEFECT','x3')
        self.assertTrue(self.store.should_trial_alternative('ollama::model:1.0',V1,'fr'))
        self.assertFalse(self.store.snapshot('ollama::model:1.0',V1)['profiles'][0]['qualified'])

    def test_reject_forged_objective_verified(self):
        with self.assertRaises(ValueError):
            self.record('OBJECTIVE_VERIFIED')
        self.assertFalse((self.root/'ledger.json').exists())

    def test_missing_digest_refused(self):
        with self.assertRaises(ValueError):
            self.record(version='latest')

    def test_no_raw_content_or_ids_persisted(self):
        self.record(receipt='secret-opaque-mission-id')
        data=(self.root/'ledger.json').read_text()
        for forbidden in ['secret-opaque-mission-id','assistant answer','conversation_id','prompt']:
            self.assertNotIn(forbidden,data)

    def test_owner_only_permissions(self):
        self.record()
        for name in ('ledger.json','ledger.key','ledger.lock'):
            self.assertEqual((self.root/name).stat().st_mode & 0o077,0)
        self.assertEqual(self.root.stat().st_mode & 0o077,0)

    def test_reject_file_symlink(self):
        self.root.mkdir(mode=0o700)
        target=Path(self.tmp.name)/'stolen'
        target.write_text('DO NOT MODIFY')
        (self.root/'ledger.json').symlink_to(target)
        with self.assertRaises(m.EvidenceUnavailable):
            self.record()
        self.assertEqual(target.read_text(),'DO NOT MODIFY')

    def test_reject_directory_symlink(self):
        target=Path(self.tmp.name)/'actual'; target.mkdir()
        self.root.symlink_to(target, target_is_directory=True)
        with self.assertRaises(m.EvidenceUnavailable):
            self.record()

    def test_untrusted_symlink_ancestor_refused(self):
        actual=Path(self.tmp.name)/'actual'
        actual.mkdir(mode=0o700)
        alias=Path(self.tmp.name)/'redirect'
        alias.symlink_to(actual, target_is_directory=True)
        trial=m.SovereignEvidenceLedger(alias/'qualification')
        with self.assertRaisesRegex(m.EvidenceUnavailable, 'symlink ancestor forbidden'):
            trial.record('ollama::model:1.0', V1, 'fr', 'normal',
                         'TECHNICAL_DELIVERED', receipt_id='x')
        self.assertFalse((actual/'qualification').exists())

    def test_system_alias_allowlist_is_exact_and_os_scoped(self):
        self.assertEqual(m._DARWIN_SYSTEM_ALIASES,
                         {'/var':'/private/var','/tmp':'/private/tmp'})
        self.assertFalse(m._trusted_darwin_system_alias(Path(self.tmp.name)))
        self.assertFalse(m._trusted_darwin_system_alias(Path('/private/var')))

    def test_darwin_alias_rejects_spoofed_owner_and_destination(self):
        from unittest import mock
        def checked(owner=0, destination='/private/var'):
            def stats(p):
                if str(p) == '/var':
                    return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777,
                                           st_uid=owner)
                if str(p) in ('/private', '/private/var'):
                    return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755,
                                           st_uid=0)
                raise FileNotFoundError(str(p))
            with mock.patch.object(m.sys, 'platform', 'darwin'), \
                 mock.patch.object(Path, 'lstat', autospec=True, side_effect=stats), \
                 mock.patch.object(Path, 'resolve', autospec=True,
                                   side_effect=lambda p, strict=False: Path(destination)):
                return m._trusted_darwin_system_alias(Path('/var'))
        self.assertTrue(checked())
        self.assertFalse(checked(owner=501))
        self.assertFalse(checked(destination='/opt/attacker'))

    @unittest.skipUnless(sys.platform == 'darwin', 'requires macOS filesystem')
    def test_real_macos_temp_directory_allowed(self):
        # Reproduce the original 14 test errors on a stock macOS /var path.
        self.assertTrue(Path(self.tmp.name).is_dir())
        self.assertTrue(self.record(receipt='mac-temp-probe'))
        self.assertEqual(self.store.snapshot('ollama::model:1.0', V1)['sequence'],1)

    def test_missing_main_recover_backup(self):
        self.record(receipt='a'); self.record(receipt='b')
        (self.root/'ledger.json').unlink()
        self.assertEqual(self.store.snapshot('ollama::model:1.0',V1)['sequence'],1)
        self.record(receipt='c')
        self.assertEqual(self.store.snapshot('ollama::model:1.0',V1)['sequence'],2)

    def test_corruption_recovers_trusted_backup(self):
        self.record(receipt='a'); self.record(receipt='b')
        (self.root/'ledger.json').write_bytes(b'not json')
        x=self.store.snapshot('ollama::model:1.0',V1)
        self.assertEqual(x['storage'],'recovered_from_backup')
        self.store.record('ollama::model:1.0',V1,'fr','normal','TECHNICAL_DELIVERED',receipt_id='c')
        self.assertEqual(self.store.snapshot('ollama::model:1.0',V1)['storage'],'healthy')

    def test_both_corrupt_fail_closed(self):
        self.record(receipt='a'); self.record(receipt='b')
        (self.root/'ledger.json').write_bytes(b'bad')
        (self.root/'ledger.previous.json').write_bytes(b'bad')
        with self.assertRaises(m.EvidenceUnavailable):
            self.store.snapshot('ollama::model:1.0',V1)
        with self.assertRaises(m.EvidenceUnavailable):
            self.record(receipt='c')

    def test_modified_data_even_json_is_rejected(self):
        self.record()
        p=self.root/'ledger.json'
        value=json.loads(p.read_text()); value['payload']['profiles'].clear()
        p.write_text(json.dumps(value))
        with self.assertRaises(m.EvidenceUnavailable):
            self.store.snapshot('ollama::model:1.0',V1)

    def test_world_readable_store_denied(self):
        self.record(); (self.root/'ledger.json').chmod(0o644)
        with self.assertRaises(m.EvidenceUnavailable):
            self.store.snapshot('ollama::model:1.0',V1)

    def test_no_model_case_never_creates_global_store(self):
        # Constructing the ledger itself has no file side effects.
        s=m.SovereignEvidenceLedger(Path(self.tmp.name)/'uncreated')
        self.assertFalse(s.root.exists())

    def test_interprocess_lock_no_lost_updates(self):
        script='''import importlib.util,sys
s=importlib.util.spec_from_file_location("ledger",sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
l=m.SovereignEvidenceLedger(sys.argv[2]);
for i in range(12): l.record("ollama::model:1.0", "'''+V1+'''", "en", "normal", "TECHNICAL_DELIVERED", receipt_id=sys.argv[3]+str(i))
'''
        children=[subprocess.Popen([sys.executable,'-B','-c',script,str(SRC),str(self.root),str(j)],stderr=subprocess.PIPE) for j in range(3)]
        for p in children:
            _, err=p.communicate(timeout=20)
            self.assertEqual(p.returncode,0,err.decode())
        self.assertEqual(self.store.snapshot('ollama::model:1.0',V1)['sequence'],36)

if __name__=='__main__': unittest.main()
