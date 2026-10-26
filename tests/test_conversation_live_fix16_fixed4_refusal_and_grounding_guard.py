from __future__ import annotations
import unittest
from maestro_gateway.native_linguistic_revision import (
    identity_answer_evasion, validate_structured_identity_slot, maestro_identity_grounded,
    structured_identity_slot_instruction,structured_identity_slot_retry_instruction,
)
GOOD_DE_A='MAESTRO ist ein lokaler KI-Assistent auf deinem Computer.'
GOOD_DE_B='Du kannst mit MAESTRO Fragen klären und Informationen ordnen.'
GOOD_AR_A='يعد MAESTRO مساعد ذكاء اصطناعي يُستخدم في تشغيل محلي على حاسوب المستخدم.'
GOOD_AR_B='يساعدك في إجابة الأسئلة ضمن الصلاحيات المتاحة.'
REAL_DE_REFUSAL=('Ich kann nicht dabei helfen, einen Satz zu formulieren, der die Identität von '
  'MAESTRO als KI-Assistent, der auf einem Computer im lokalen Umfeld betrieben wird, unterstützt.')
class Fix16Fixed4RefusalAndGroundingGuard(unittest.TestCase):
    def test_real_false_positive_now_rejected(self):
        self.assertEqual(validate_structured_identity_slot(REAL_DE_REFUSAL,'de',1)[1], 'maestro_identity_refusal')
    def test_real_false_positive_cannot_pass_composition(self):
        self.assertFalse(maestro_identity_grounded(REAL_DE_REFUSAL+' '+GOOD_DE_B,'de'))
    def test_german_refusal_variants(self):
        for s in ('Ich kann dir nicht dabei helfen, MAESTRO als lokalen KI-Assistenten auf deinem Computer zu beschreiben.',
                  'Ich bin nicht in der Lage, MAESTRO als lokalen KI-Assistenten auf deinem Computer zu erklären.',
                  'Es tut mir leid, MAESTRO ist ein lokaler KI-Assistent auf deinem Computer.'):
            with self.subTest(s=s): self.assertFalse(validate_structured_identity_slot(s,'de',1)[0])
    def test_arabic_refusal_variants(self):
        for s in ('لا أستطيع الإجابة عن MAESTRO وهو مساعد ذكاء اصطناعي محلي على الحاسوب.',
                  'لا يمكنني وصف MAESTRO مساعد ذكاء اصطناعي محلي على الحاسوب.',
                  'أعتذر عن وصف MAESTRO مساعد ذكاء اصطناعي محلي على الحاسوب.'):
            with self.subTest(s=s): self.assertFalse(validate_structured_identity_slot(s,'ar',1)[0])
    def test_no_refusal_false_positive_on_negating_person(self):
        self.assertFalse(identity_answer_evasion('MAESTRO ist kein Mensch, sondern ein lokaler KI-Assistent auf deinem Computer.','de'))
        self.assertFalse(identity_answer_evasion('MAESTRO ليس شخصًا بل مساعد ذكاء اصطناعي محلي على هذا الحاسوب.','ar'))
    def test_de_not_only_is_not_refusal(self):
        self.assertFalse(identity_answer_evasion('Ich kann nicht nur Fragen beantworten, sondern dir auch beim Ordnen helfen.','de'))
    def test_correct_de_stays_accepted(self):
        self.assertTrue(validate_structured_identity_slot(GOOD_DE_A,'de',1)[0]); self.assertTrue(validate_structured_identity_slot(GOOD_DE_B,'de',2)[0])
        self.assertTrue(maestro_identity_grounded(GOOD_DE_A+' '+GOOD_DE_B,'de'))
    def test_real_ar_variant_not_falsely_rejected(self):
        self.assertTrue(validate_structured_identity_slot(GOOD_AR_A,'ar',1)[0]);self.assertTrue(validate_structured_identity_slot(GOOD_AR_B,'ar',2)[0])
        self.assertTrue(maestro_identity_grounded(GOOD_AR_A+' '+GOOD_AR_B,'ar'))
    def test_malformed_arabic_or_foreign_script_refused(self):
        self.assertFalse(validate_structured_identity_slot('MAESTRO هو مساعد local على الحاسوب.','ar',1)[0])
    def test_false_company_refused(self):
        self.assertFalse(validate_structured_identity_slot('MAESTRO ist eine Firma auf deinem Computer.','de',1)[0]);self.assertFalse(validate_structured_identity_slot('MAESTRO هو شركة محلية على الحاسوب.','ar',1)[0])
    def test_retry_specific_feedback(self):
        for lang in ('ar','de'):
            p=structured_identity_slot_retry_instruction(prompt='',language=lang,slot=1,reason='maestro_identity_refusal',attempt=1)
            self.assertTrue(p);self.assertNotIn(REAL_DE_REFUSAL,p)
    def test_instructions_not_hard_coded_final_response(self):
        for lang in ('ar','de'):
            ins=structured_identity_slot_instruction(prompt='',language=lang,slot=1)
            self.assertNotIn(GOOD_DE_A,ins);self.assertNotIn(GOOD_AR_A,ins)
    def test_installed_gateway_refusal_guard_is_self_contained(self):
        # Installed Gateway unit tests must not depend on an unpacked archive's live/ scripts.
        # Full script self-test and 49 -> 52 check preservation are verified by package tests.
        refusal = REAL_DE_REFUSAL + ' ' + GOOD_DE_B
        self.assertFalse(validate_structured_identity_slot(REAL_DE_REFUSAL, 'de', 1)[0])
        self.assertFalse(maestro_identity_grounded(refusal, 'de'))
        self.assertTrue(validate_structured_identity_slot(GOOD_DE_A, 'de', 1)[0])

    def test_installed_gateway_arabic_identity_guard_is_self_contained(self):
        self.assertTrue(validate_structured_identity_slot(GOOD_AR_A, 'ar', 1)[0])
        self.assertTrue(validate_structured_identity_slot(GOOD_AR_B, 'ar', 2)[0])
        self.assertFalse(validate_structured_identity_slot('MAESTRO هو شركة محلية على الحاسوب.', 'ar', 1)[0])
        self.assertTrue(maestro_identity_grounded(GOOD_AR_A + ' ' + GOOD_AR_B, 'ar'))
