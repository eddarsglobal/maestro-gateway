from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

import maestro_gateway.model_qualification as model_qualification
from maestro_gateway.language_benchmark_evidence import (
    load_language_benchmark_evidence, reset_for_testing,
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

BEST='b'*64
WEAK='a'*64

def q(*,qualified=False,attempts=0,accepted=0,p95_ms=None):
    return SimpleNamespace(qualified=qualified,attempts=attempts,accepted=accepted,
                           rejected=0,timed_out=0,p95_ms=p95_ms)

class Fix5ExplicitLanguageEvidencePriorityTests(unittest.TestCase):
    def setUp(self):
        reset_for_testing(); model_qualification.reset_for_testing()
        model_qualification.update_verified_versions({'local::best':BEST,'local::weak':WEAK})
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'e.json'
        self.path.write_text(json.dumps({'schema':1,'source_report_sha256':'d'*64,'models':{
          'local::best':{'digest':BEST,'languages':{
            'de':{'passes':3,'total':3,'natural_pass':True,'elapsed_s':10.0},
            'ar':{'passes':2,'total':2,'natural_pass':True,'elapsed_s':8.0}}},
          'local::weak':{'digest':WEAK,'languages':{
            'de':{'passes':1,'total':3,'natural_pass':False,'elapsed_s':2.0},
            'ar':{'passes':0,'total':2,'natural_pass':False,'elapsed_s':2.0}}}}}),encoding='utf-8')
        self.assertEqual(load_language_benchmark_evidence(self.path),2)
        self.registry=Registry(Descriptor('local::best'),Descriptor('local::weak'))
    def tearDown(self):
        reset_for_testing(); model_qualification.reset_for_testing(); self.tmp.cleanup()

    def test_de_to_ar_switch_keeps_complete_benchmark_candidate_before_generic_history(self):
        m=PrimaryIntelligenceManager()
        first=m.select(self.registry,'Antworte auf Deutsch. Schreibe zwei Sätze.',ui_language='fr')
        self.assertEqual(first.decision.resource_id,'local::best')
        def fake_qualification(rid,task,language):
            if language=='ar' and rid=='local::weak': return q(qualified=True,attempts=3,accepted=3,p95_ms=500)
            if language=='ar' and rid=='local::best': return q(qualified=False,attempts=0,accepted=0)
            return q(qualified=False,attempts=0,accepted=0)
        with patch('maestro_gateway.primary_intelligence.qualification',side_effect=fake_qualification), \
             patch('maestro_gateway.primary_intelligence.task_qualification',return_value=q()):
            second=m.select(self.registry,'أجب باللغة العربية. اكتب جملتين.',ui_language='fr')
        self.assertEqual(second.decision.resource_id,'local::best')
        self.assertEqual(second.reason,'user_language_benchmark_evidence')
        self.assertEqual(second.decision.language,'ar')

    def test_same_benchmark_primary_is_still_marked_as_evidence_driven_for_new_explicit_language(self):
        m=PrimaryIntelligenceManager()
        m.select(self.registry,'Antworte auf Deutsch. Schreibe zwei Sätze.',ui_language='fr')
        got=m.select(self.registry,'أجب باللغة العربية. اكتب جملتين.',ui_language='fr')
        self.assertEqual(got.decision.resource_id,'local::best')
        self.assertEqual(got.reason,'user_language_benchmark_evidence')

    def test_without_complete_benchmark_existing_operational_policy_remains_available(self):
        reset_for_testing()
        m=PrimaryIntelligenceManager()
        with patch('maestro_gateway.primary_intelligence.qualification',return_value=q()), \
             patch('maestro_gateway.primary_intelligence.task_qualification',return_value=q()):
            got=m.select(Registry(Descriptor('local::weak')),'أجب باللغة العربية. اكتب جملتين.',ui_language='fr')
        self.assertEqual(got.decision.resource_id,'local::weak')
        self.assertNotEqual(got.reason,'user_language_benchmark_evidence')

if __name__=='__main__': unittest.main()
