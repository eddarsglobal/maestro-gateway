from __future__ import annotations
import unittest
from pathlib import Path
from maestro_gateway.native_linguistic_revision import (
    structured_identity_slot_instruction, structured_identity_slot_retry_instruction,
    validate_structured_identity_slot, identity_slot_meta_preamble,
    maestro_identity_grounded, maestro_identity_contradicted,
)

class Fix16Fixed5PositiveSlotPrompts(unittest.TestCase):
    def test_de_base_has_fact_fields_and_no_refusal_primes(self):
        value=structured_identity_slot_instruction(prompt='',language='de',slot=1)
        for part in ('Name=MAESTRO','Art=KI-Assistent','Betrieb=lokal','Ort=Computer'):
            self.assertIn(part,value)
        for forbidden in ('Person','Firma','lehne','Ablehnung','nicht dabei helfen'):
            self.assertNotIn(forbidden,value)
    def test_ar_base_has_fact_fields_and_no_negative_persona_priming(self):
        value=structured_identity_slot_instruction(prompt='',language='ar',slot=1)
        for part in ('الاسم=MAESTRO','النوع=مساعد ذكاء اصطناعي','التشغيل=محلي','المكان=حاسوب'):
            self.assertIn(part,value)
        for forbidden in ('شخص','شركة','اعتذار','لا تكتب'):
            self.assertNotIn(forbidden,value)
    def test_retry_no_refusal_priming(self):
        for lang in ('de','ar'):
            a=structured_identity_slot_retry_instruction(prompt='',language=lang,slot=1,reason='maestro_identity_refusal',attempt=1)
            b=structured_identity_slot_retry_instruction(prompt='',language=lang,slot=1,reason='maestro_identity_refusal',attempt=2)
            self.assertNotEqual(a,b)
            self.assertNotIn('Ich kann nicht dabei helfen',a)
            self.assertNotIn('لا أستطيع',a)
    def test_arabic_live_single_meta_prefix_is_rejected(self):
        real=('الجملة الجديدة: من خلال تشغيل معالج الحاسوب في الحاسوب المنزلي، '
              'يمكن تكوين مساعد ذكاء اصطناعي يُسمى MAESTRO الذي يعمل بانتظام '
              'لتقديم الدعم والاستشارة، بحيث يكون مساعدًا محليًا ذو ذكاء اصطناعي.')
        self.assertTrue(identity_slot_meta_preamble(real,'ar'))
        self.assertEqual(validate_structured_identity_slot(real,'ar',1)[1], 'identity_slot_meta_preamble')
    def test_german_meta_prefix_is_rejected(self):
        self.assertEqual(validate_structured_identity_slot('Antwort: MAESTRO ist ein lokaler KI-Assistent auf deinem Computer.','de',1)[1], 'identity_slot_meta_preamble')
    def test_correct_arabic_still_passes(self):
        a='MAESTRO هو مساعد ذكاء اصطناعي محلي يعمل على حاسوب المستخدم.'
        b='يساعدك في الأسئلة والمعلومات ضمن الصلاحيات المتاحة.'
        self.assertTrue(validate_structured_identity_slot(a,'ar',1)[0])
        self.assertTrue(validate_structured_identity_slot(b,'ar',2)[0])
        self.assertTrue(maestro_identity_grounded(a+' '+b,'ar'))
    def test_correct_german_still_passes(self):
        a='Auf deinem Computer ist MAESTRO ein lokal laufender KI-Assistent.'
        b='Du kannst mit seiner Hilfe Fragen beantworten und Aufgaben ordnen.'
        self.assertTrue(validate_structured_identity_slot(a,'de',1)[0])
        self.assertTrue(validate_structured_identity_slot(b,'de',2)[0])
        self.assertTrue(maestro_identity_grounded(a+' '+b,'de'))
    def test_false_identities_and_refusals_still_fail(self):
        for content,lang in (
            ('MAESTRO ist eine Firma.', 'de'),
            ('MAESTRO هو شركة محلية على الحاسوب.', 'ar'),
            ('Ich kann nicht dabei helfen, MAESTRO als lokalen KI-Assistenten auf deinem Computer zu erklären.', 'de'),
        ):
            self.assertFalse(validate_structured_identity_slot(content,lang,1)[0])
    def test_not_canned_and_no_vendor_override(self):
        src=Path(__file__).parents[1]/'src/maestro_gateway/native_linguistic_revision.py'
        text=src.read_text(encoding='utf-8')
        self.assertNotIn('MAESTRO ist ein lokaler KI-Assistent auf deinem Computer.', text)
        self.assertNotIn('MAESTRO هو مساعد ذكاء اصطناعي محلي يعمل على حاسوب المستخدم.',text)
        for vendor in ('ollama::','llama3.2','qwen3','gemma4'):
            self.assertNotIn(vendor,text)
    def test_bound_retries_and_preserve_same_resource(self):
        src=(Path(__file__).parents[1]/'src/maestro_gateway/mission.py').read_text()
        self.assertIn('structured_identity_slot_retry_counts.get(structured_identity_slot_index, 0) < 2:',src)
        self.assertIn('adapter = registry.adapter(resource_id)',src)
