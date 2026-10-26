"""Extract the exact production manager method to test CAS and storage wiring offline."""
import ast
import hashlib
import importlib.util
from pathlib import Path
import tempfile
from threading import RLock
import unittest

ROOT=Path(__file__).resolve().parents[1]
MGR=ROOT/'src/maestro_gateway/primary_intelligence.py'
LEDGER=ROOT/'src/maestro_gateway/sovereign_evidence_ledger.py'
spec=importlib.util.spec_from_file_location('p04_local_ledger',LEDGER)
ledger=importlib.util.module_from_spec(spec);spec.loader.exec_module(ledger)
module=ast.parse(MGR.read_text())
cls=next(x for x in module.body if isinstance(x,ast.ClassDef) and x.name=='PrimaryIntelligenceManager')
fn=next(x for x in cls.body if isinstance(x,ast.FunctionDef) and x.name=='note_sovereign_observation')
# The extracted FunctionDef is exactly from production, not a mock reimplementation.
method=ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[]))

class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=ledger.SovereignEvidenceLedger(Path(self.tmp.name)/'q')
        self.fp='a'*64
        ns={'EvidenceUnavailable':ledger.EvidenceUnavailable, 'hashlib': hashlib,
            'verified_version':lambda resource:self.fp}
        exec(compile(method,str(MGR),'exec'),ns)
        class Manager:pass
        self.manager=Manager()
        self.manager._lock=RLock()
        self.manager._primary='ollama::qwen2.5-coder:1.5b'
        self.manager._epoch=4
        self.manager._durable_evidence=self.db
        self.manager.note_sovereign_observation=ns['note_sovereign_observation'].__get__(self.manager,Manager)
    def observe(self,**kwargs):
        d=dict(resource_id=self.manager._primary,language='fr',task='normal',status='TECHNICAL_DELIVERED',receipt_id='mission-1',expected_epoch=4)
        d.update(kwargs)
        return self.manager.note_sovereign_observation(**d)
    def test_current_epoch_is_recorded(self):
        self.assertEqual(self.observe(),'recorded')
        self.assertEqual(self.db.snapshot(self.manager._primary,hashlib.sha256(self.fp.encode()).hexdigest())['sequence'],1)
    def test_stale_epoch_refused(self):
        self.assertEqual(self.observe(expected_epoch=3),'stale_primary_refused')
        self.assertFalse((self.db.root/'ledger.json').exists())
    def test_nonprimary_is_refused(self):
        self.assertEqual(self.observe(resource_id='ollama::other'),'stale_primary_refused')
        self.assertFalse((self.db.root/'ledger.json').exists())
    def test_model_digest_untrusted_refused(self):
        self.fp='latest'
        self.assertEqual(self.observe(),'unversioned_refused')
    def test_duplicate_receipt(self):
        self.assertEqual(self.observe(),'recorded')
        self.assertEqual(self.observe(),'deduplicated')
    def test_corruption_reports_no_success(self):
        self.assertEqual(self.observe(),'recorded')
        (self.db.root/'ledger.json').write_bytes(b'wrong')
        result=self.observe(receipt_id='mission-2')
        self.assertIn('store_unavailable_',result)
    def test_metadata_does_not_promote(self):
        self.assertEqual(self.observe(status='LINGUISTIC_DEFECT'),'recorded')
        x=self.db.snapshot(self.manager._primary,hashlib.sha256(self.fp.encode()).hexdigest())
        self.assertFalse(x['profiles'][0]['qualified'])
        self.assertFalse(x['profiles'][0]['linguistic_certified'])
if __name__=='__main__':unittest.main()
