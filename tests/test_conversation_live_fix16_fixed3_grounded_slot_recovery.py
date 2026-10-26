from __future__ import annotations
import unittest
from pathlib import Path
from maestro_gateway.native_linguistic_revision import (
    structured_identity_slot_instruction, structured_identity_slot_retry_instruction,
    validate_structured_identity_slot, compose_structured_identity_slots,
    maestro_identity_grounded,
)

class Fix16Fixed3GroundedSlotRecovery(unittest.TestCase):
    def test_fielded_facts_without_canned_full_answer_de(self):
        p=structured_identity_slot_instruction(prompt='Was ist MAESTRO?',language='de',slot=1)
        for frag in ('Name=MAESTRO','Art=KI-Assistent','Betrieb=lokal','Ort=Computer'):
            self.assertIn(frag,p)
        self.assertNotIn('MAESTRO ist ein lokaler KI-Assistent auf diesem Computer.',p)

    def test_fielded_facts_without_canned_full_answer_ar(self):
        p=structured_identity_slot_instruction(prompt='ما هو MAESTRO؟',language='ar',slot=1)
        for frag in ('الاسم=MAESTRO','النوع=مساعد ذكاء اصطناعي','التشغيل=محلي','المكان=حاسوب'):
            self.assertIn(frag,p)
        self.assertNotIn('MAESTRO هو مساعد ذكاء اصطناعي محلي على هذا الحاسوب.',p)

    def test_retry_de_grounding_reason_actionable(self):
        p=structured_identity_slot_retry_instruction(prompt='',language='de',slot=1,
                                                     reason='maestro_identity_not_grounded')
        self.assertIn('Rechner',p)
        self.assertIn('KI-Assistent',p)
    def test_retry_ar_grounding_reason_actionable(self):
        p=structured_identity_slot_retry_instruction(prompt='',language='ar',slot=1,
                                                     reason='maestro_identity_not_grounded')
        self.assertIn('حقائق',p)
        self.assertIn('حاسوب',p)
    def test_retries_progress_boundedly(self):
        one=structured_identity_slot_retry_instruction(prompt='',language='de',slot=1,
                     reason='maestro_identity_not_grounded',attempt=1)
        two=structured_identity_slot_retry_instruction(prompt='',language='de',slot=1,
                     reason='maestro_identity_not_grounded',attempt=2)
        self.assertNotEqual(one,two)
        src=(Path(__file__).parents[1]/'src/maestro_gateway/mission.py').read_text()
        self.assertIn('structured_identity_slot_retry_counts.get(structured_identity_slot_index, 0) < 2:',src)
        self.assertIn('attempt=structured_identity_slot_retry_counts[structured_identity_slot_index]',src)
    def test_accepted_de_grounded_and_free_form(self):
        a='Auf deinem Computer ist MAESTRO ein lokal laufender KI-Assistent.'
        b='Du kannst mit seiner Hilfe Fragen beantworten und Aufgaben ordnen.'
        self.assertTrue(validate_structured_identity_slot(a,'de',1)[0])
        self.assertTrue(validate_structured_identity_slot(b,'de',2)[0])
        self.assertTrue(maestro_identity_grounded(compose_structured_identity_slots([a,b]),'de'))
    def test_accepted_ar_grounded_and_free_form(self):
        a='على هذا الحاسوب يعمل MAESTRO بوصفه مساعد ذكاء اصطناعي محليًا.'
        b='يساعدك في فهم المعلومات ضمن الصلاحيات المتاحة.'
        self.assertTrue(validate_structured_identity_slot(a,'ar',1)[0])
        self.assertTrue(validate_structured_identity_slot(b,'ar',2)[0])
        self.assertTrue(maestro_identity_grounded(compose_structured_identity_slots([a,b]),'ar'))
    def test_neither_name_only_nor_generic_identity_passes(self):
        self.assertFalse(validate_structured_identity_slot('MAESTRO ist ein Assistent.','de',1)[0])
        self.assertFalse(validate_structured_identity_slot('MAESTRO هو مساعد محلي.','ar',1)[0])
    def test_false_identities_remain_denied(self):
        for s,lang in [('MAESTRO ist eine Firma.','de'),('MAESTRO هو شخص.','ar')]:
            self.assertEqual(validate_structured_identity_slot(s,lang,1)[0],False)
    def test_english_only_de_denied(self):
        self.assertFalse(validate_structured_identity_slot('MAESTRO is a local AI assistant on your computer.','de',1)[0])
    def test_ar_foreign_text_denied(self):
        self.assertFalse(validate_structured_identity_slot('MAESTRO هو local مساعد على هذا الحاسوب.','ar',1)[0])
    def test_slot2_cannot_be_unrelated_sentence(self):
        self.assertFalse(validate_structured_identity_slot('Der Himmel ist blau.','de',2)[0])
        self.assertFalse(validate_structured_identity_slot('السماء صافية.','ar',2)[0])
    def test_one_sentence_only(self):
        self.assertFalse(validate_structured_identity_slot('MAESTRO ist lokal. Es hilft dir.','de',1)[0])
    def test_no_model_vendor_steering(self):
        s=(Path(__file__).parents[1]/'src/maestro_gateway/native_linguistic_revision.py').read_text().casefold()
        for x in ('llama3.2','ollama::','qwen','gemma:2b'):
            self.assertNotIn(x,s)
