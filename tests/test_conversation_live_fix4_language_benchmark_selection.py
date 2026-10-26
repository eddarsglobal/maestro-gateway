from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import dataclass

import maestro_gateway.model_qualification as model_qualification
from maestro_gateway.language_benchmark_evidence import (
    benchmark, evidence_status, load_language_benchmark_evidence,
    preferred_language_resource, reset_for_testing,
)
from maestro_gateway.primary_intelligence import PrimaryIntelligenceManager


@dataclass(frozen=True)
class Descriptor:
    resource_id: str
    local: bool = True
    capabilities: tuple[str, ...] = ('DIRECT',)
    status: str = 'AVAILABLE'

class Registry:
    def __init__(self,*items): self.items=items
    def all(self): return self.items

D1='a'*64
D2='b'*64
D3='c'*64

def write_evidence(path:Path, *, first_complete=True, stale=False):
    doc={
      'schema':1,
      'source_report_sha256':'d'*64,
      'models':{
        'local::weak':{'digest': D1 if not stale else 'f'*64,'languages':{
          'de':{'passes':1,'total':3,'natural_pass':False,'elapsed_s':3.0},
          'ar':{'passes':1,'total':2,'natural_pass':False,'elapsed_s':2.0}}},
        'local::best':{'digest':D2,'languages':{
          'de':{'passes':3 if first_complete else 2,'total':3,'natural_pass':first_complete,'elapsed_s':20.0},
          'ar':{'passes':2 if first_complete else 1,'total':2,'natural_pass':first_complete,'elapsed_s':8.0}}},
        'local::other':{'digest':D3,'languages':{
          'de':{'passes':3,'total':3,'natural_pass':True,'elapsed_s':40.0},
          'ar':{'passes':2,'total':2,'natural_pass':True,'elapsed_s':16.0}}},
      }}
    path.write_text(json.dumps(doc),encoding='utf-8')

class Fix4LanguageBenchmarkSelectionTests(unittest.TestCase):
    def setUp(self):
        reset_for_testing(); model_qualification.reset_for_testing()
        model_qualification.update_verified_versions({'local::weak':D1,'local::best':D2,'local::other':D3})
        self.t=tempfile.TemporaryDirectory(); self.path=Path(self.t.name)/'e.json'
        write_evidence(self.path); self.assertEqual(load_language_benchmark_evidence(self.path),3)
    def tearDown(self):
        reset_for_testing(); model_qualification.reset_for_testing(); self.t.cleanup()

    def test_complete_digest_bound_evidence_is_read(self):
        e=benchmark('local::best',D2,'de')
        self.assertIsNotNone(e); self.assertTrue(e.complete); self.assertEqual(e.passes,3)

    def test_digest_mismatch_revokes_evidence(self):
        self.assertIsNone(benchmark('local::best','0'*64,'de'))

    def test_preferred_candidate_uses_complete_evidence_then_elapsed_tie_break(self):
        got=preferred_language_resource(['local::other','local::best'],'de',model_qualification.verified_version)
        self.assertEqual(got,'local::best')

    def test_incomplete_evidence_never_promotes(self):
        write_evidence(self.path,first_complete=False); load_language_benchmark_evidence(self.path)
        self.assertEqual(preferred_language_resource(['local::best','local::other'],'de',model_qualification.verified_version),'local::other')

    def test_explicit_german_cold_start_uses_benchmark_candidate(self):
        m=PrimaryIntelligenceManager()
        got=m.select(Registry(Descriptor('local::weak'),Descriptor('local::best')),
                     'Antworte auf Deutsch. Schreibe zwei Sätze.',ui_language='en')
        self.assertEqual(got.decision.resource_id,'local::best')
        self.assertEqual(got.reason,'user_language_benchmark_evidence')
        self.assertEqual(got.decision.language,'de')

    def test_explicit_arabic_cold_start_uses_benchmark_candidate(self):
        m=PrimaryIntelligenceManager()
        got=m.select(Registry(Descriptor('local::weak'),Descriptor('local::best')),
                     'أجب باللغة العربية. اكتب جملتين.',ui_language='en')
        self.assertEqual(got.decision.resource_id,'local::best')
        self.assertEqual(got.reason,'user_language_benchmark_evidence')
        self.assertEqual(got.decision.language,'ar')

    def test_existing_unbenchmarked_primary_switches_on_explicit_language(self):
        reset_for_testing()  # choose weak before importing evidence
        m=PrimaryIntelligenceManager()
        first=m.select(Registry(Descriptor('local::weak')),'Hello',ui_language='en')
        self.assertEqual(first.decision.resource_id,'local::weak')
        load_language_benchmark_evidence(self.path)
        got=m.select(Registry(Descriptor('local::weak'),Descriptor('local::best')),
                     'Antworte auf Deutsch. Schreibe zwei Sätze.',ui_language='en')
        self.assertEqual(got.decision.resource_id,'local::best')
        self.assertEqual(got.reason,'user_language_benchmark_evidence')

    def test_benchmarked_primary_is_kept_without_oscillation(self):
        m=PrimaryIntelligenceManager()
        a=m.select(Registry(Descriptor('local::best'),Descriptor('local::other')),
                   'Antworte auf Deutsch. Schreibe zwei Sätze.',ui_language='en')
        b=m.select(Registry(Descriptor('local::best'),Descriptor('local::other')),
                   'Antworte auf Deutsch. Schreibe nochmals zwei Sätze.',ui_language='en')
        self.assertEqual(a.decision.resource_id,'local::best')
        self.assertEqual(b.decision.resource_id,'local::best')

    def test_non_explicit_ordinary_chat_is_not_forced_by_benchmark(self):
        reset_for_testing()
        m=PrimaryIntelligenceManager()
        got=m.select(Registry(Descriptor('local::weak')),'Hello',ui_language='en')
        self.assertEqual(got.decision.resource_id,'local::weak')

    def test_remote_benchmark_candidate_cannot_be_selected(self):
        m=PrimaryIntelligenceManager()
        got=m.select(Registry(Descriptor('local::weak'),Descriptor('local::best',local=False)),
                     'Antworte auf Deutsch. Schreibe zwei Sätze.',ui_language='en')
        self.assertEqual(got.decision.resource_id,'local::weak')

    def test_invalid_file_fails_closed_and_clears_snapshot(self):
        self.path.write_text('{bad',encoding='utf-8')
        self.assertEqual(load_language_benchmark_evidence(self.path),0)
        self.assertEqual(evidence_status()['models'],0)

    def test_missing_evidence_file_fails_closed(self):
        self.assertEqual(load_language_benchmark_evidence(Path(self.t.name)/'missing.json'),0)
        self.assertIsNone(preferred_language_resource(['local::best'],'de',model_qualification.verified_version))

    def test_evidence_never_claims_fluency_certification(self):
        self.assertFalse(evidence_status()['semantic_fluency_certified'])

    def test_language_specific_evidence_does_not_cross_promote(self):
        # Remove Arabic row for best: German proof must not imply Arabic proof.
        doc=json.loads(self.path.read_text()); doc['models']['local::best']['languages'].pop('ar')
        self.path.write_text(json.dumps(doc)); load_language_benchmark_evidence(self.path)
        self.assertEqual(preferred_language_resource(['local::best','local::other'],'ar',model_qualification.verified_version),'local::other')

if __name__=='__main__': unittest.main()
