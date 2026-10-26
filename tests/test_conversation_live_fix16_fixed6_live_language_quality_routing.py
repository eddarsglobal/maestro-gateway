"""FIX16-FIXED6: conservative independent observed-evidence qualification."""
from __future__ import annotations
import unittest
from pathlib import Path
from maestro_gateway.language_quality_governor import (
    SessionLanguageEvidence, verified_surface_violation,
)
from maestro_gateway.native_linguistic_revision import validate_structured_identity_slot

class Fix16Fixed6LanguageQualityRouting(unittest.TestCase):
    def test_real_de_hallo_dir_rejected(self):
        text='Hallo dir, ich kann dir bei Fragen und Aufgaben helfen.'
        self.assertEqual(verified_surface_violation(text,'de'),'linguistic_greeting_unnatural')
        self.assertEqual(validate_structured_identity_slot(text,'de',2)[1],'linguistic_greeting_unnatural')
    def test_real_ar_translated_maestro_is_not_identity(self):
        self.assertEqual(verified_surface_violation('المعلم الكبير يقدّم مساعدة فنية.', 'ar'),
                         'linguistic_false_translated_identity')
        self.assertEqual(validate_structured_identity_slot('المعلم الكبير يقدّم مساعدة فنية.', 'ar',2)[1],
                         'linguistic_false_translated_identity')
    def test_real_ar_ungrammatical_local_operation_rejected(self):
        text='MAESTRO هو مساعد ذكاء اصطناعي تشغيله محلي على حاسوب المستخدم.'
        self.assertEqual(verified_surface_violation(text, 'ar'),'linguistic_unnatural_local_operation')
        self.assertEqual(validate_structured_identity_slot(text,'ar',1)[1], 'linguistic_unnatural_local_operation')
    def test_real_ar_gender_agreement_rejected(self):
        self.assertEqual(verified_surface_violation('تتيح MAESTRO للمستخدمين مساعداً محلياً.', 'ar'),
                         'linguistic_wrong_maestro_agreement')
    def test_accepted_ar_and_de_not_affected(self):
        ar='MAESTRO مساعد ذكاء اصطناعي محلي يعمل على حاسوب المستخدم.'
        de='MAESTRO ist ein lokaler KI-Assistent auf deinem Computer.'
        self.assertIsNone(verified_surface_violation(ar,'ar'))
        self.assertIsNone(verified_surface_violation(de,'de'))
        self.assertTrue(validate_structured_identity_slot(ar,'ar',1)[0])
        self.assertTrue(validate_structured_identity_slot(de,'de',1)[0])
    def test_acceptance_never_claims_fluency(self):
        store=SessionLanguageEvidence()
        self.assertTrue(store.observe('local::a','de','a'*64,accepted=True,reason='accepted'))
        self.assertEqual(store.evidence('local::a','de','a'*64).completed,1)
        self.assertFalse(store.snapshot()['fluency_certified'])
    def test_single_bad_mission_does_not_disqualify(self):
        s=SessionLanguageEvidence()
        s.observe('local::a','ar','a'*64,accepted=False,reason='maestro_identity_not_grounded')
        self.assertFalse(s.should_trial_alternative('local::a','ar','a'*64))
    def test_two_real_bad_missions_trigger_bounded_trial(self):
        s=SessionLanguageEvidence()
        for _ in range(2): s.observe('local::a','ar','a'*64,accepted=False,reason='linguistic_false_translated_identity')
        self.assertTrue(s.should_trial_alternative('local::a','ar','a'*64))
        self.assertFalse(s.should_trial_alternative('local::a','de','a'*64))
        self.assertFalse(s.should_trial_alternative('local::a','ar','b'*64))
    def test_cancel_and_timeout_do_not_poison_quality(self):
        s=SessionLanguageEvidence()
        for r in ('timeout','cancellation','transport_failure'):
            self.assertFalse(s.observe('local::a','de','a'*64,accepted=False,reason=r))
        self.assertFalse(s.should_trial_alternative('local::a','de','a'*64))
    def test_bare_model_id_or_invalid_digest_not_evidence(self):
        s=SessionLanguageEvidence()
        self.assertFalse(s.observe('','de','a'*64,accepted=False,reason='identity_slot_not_one_sentence'))
        self.assertFalse(s.observe('local::a','de','short',accepted=False,reason='maestro_identity_not_grounded'))
    def test_no_english_spanish_cross_language_penalty(self):
        s=SessionLanguageEvidence()
        for _ in range(2): s.observe('local::a','de','a'*64,accepted=False,reason='maestro_identity_refusal')
        self.assertFalse(s.should_trial_alternative('local::a','en','a'*64))
        self.assertFalse(s.should_trial_alternative('local::a','es','a'*64))
    def test_live_model_calls_do_not_multiply_per_slot(self):
        code=(Path(__file__).parents[1]/'src/maestro_gateway/mission.py').read_text()
        self.assertEqual(code.count('primary_intelligence.note_language_quality('),1)
        self.assertIn('expected_epoch=primary_selection.epoch',code)
    def test_manager_only_selects_authorized_locals_and_cas(self):
        code=(Path(__file__).parents[1]/'src/maestro_gateway/primary_intelligence.py').read_text()
        self.assertIn('if self._primary != resource_id or self._epoch != expected_epoch:',code)
        self.assertIn('preferred_alternatives.values()',code)
        self.assertIn("getattr(d, 'local', None) is not True",code)
        self.assertNotIn('automatic_download_model(',code)
    def test_no_model_names_or_prebaked_answers(self):
        for rel in ('language_quality_governor.py','native_linguistic_revision.py'):
            code=(Path(__file__).parents[1]/'src/maestro_gateway'/rel).read_text()
            for name in ('ollama::llama3.2','qwen3:','gemma4:'):
                self.assertNotIn(name,code)
        self.assertFalse(SessionLanguageEvidence().snapshot()['fluency_certified'])
