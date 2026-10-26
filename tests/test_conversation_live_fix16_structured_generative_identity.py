from __future__ import annotations
import re
import unittest
from pathlib import Path

from maestro_gateway.native_linguistic_revision import (
    compose_structured_identity_slots,
    structured_identity_slot_count,
    structured_identity_slot_instruction,
    validate_structured_identity_slot,
)

DE_PROMPT=(
    'Antworte auf Deutsch in genau zwei natürlichen und grammatikalisch korrekten Sätzen.\n'
    'Was ist MAESTRO und wozu dient es?\n'
    'MAESTRO ist mein lokaler KI-Assistent auf diesem Computer.\n'
    'Verwende durchgehend die Anrede „du“.'
)
AR_PROMPT=(
    'أجب بالعربية الفصحى الطبيعية في جملتين فقط.\n'
    'ما هو MAESTRO وما وظيفته؟\n'
    'MAESTRO هو مساعد الذكاء الاصطناعي المحلي الخاص بي على هذا الحاسوب.\n'
    'لا تستخدم كلمات إنجليزية أو أحرفًا غير عربية باستثناء اسم MAESTRO.'
)
AR_SINGLE='أجب بالعربية فقط ومن دون أي كلمة إنجليزية: اشرح في جملة واحدة ما هو MAESTRO.'

class Fix16StructuredGenerativeIdentityTests(unittest.TestCase):
    def test_two_sentence_identity_is_two_model_generated_slots(self):
        self.assertEqual(structured_identity_slot_count(DE_PROMPT,'de'),2)
        self.assertEqual(structured_identity_slot_count(AR_PROMPT,'ar'),2)

    def test_single_identity_is_one_model_generated_slot(self):
        self.assertEqual(structured_identity_slot_count(AR_SINGLE,'ar'),1)

    def test_slot_prompts_contain_facts_but_no_complete_user_answer(self):
        de1=structured_identity_slot_instruction(prompt=DE_PROMPT,language='de',slot=1)
        de2=structured_identity_slot_instruction(prompt=DE_PROMPT,language='de',slot=2)
        ar1=structured_identity_slot_instruction(prompt=AR_PROMPT,language='ar',slot=1)
        ar2=structured_identity_slot_instruction(prompt=AR_PROMPT,language='ar',slot=2)
        self.assertIn('MAESTRO',de1); self.assertIn('Art=KI-Assistent',de1); self.assertIn('Betrieb=lokal',de1); self.assertIn('Ort=Computer',de1)
        self.assertIn('du-Form',de2)
        self.assertIn('MAESTRO',ar1); self.assertIn('النوع=مساعد ذكاء اصطناعي',ar1); self.assertIn('التشغيل=محلي',ar1); self.assertIn('المكان=حاسوب',ar1)
        self.assertIn('وظيفة',ar2)
        joined='\n'.join((de1,de2,ar1,ar2))
        self.assertNotIn('MAESTRO ist dein lokaler KI-Assistent auf diesem Computer. Er unterstützt dich',joined)
        self.assertNotIn('MAESTRO هو مساعد ذكاء اصطناعي محلي يعمل على هذا الحاسوب. يساعدك',joined)

    def test_slot_validation_accepts_generated_content_and_rejects_bad_shape(self):
        ok,_,s1=validate_structured_identity_slot('MAESTRO ist mein lokal ausgeführter KI-Assistent auf diesem Computer.','de',1)
        self.assertTrue(ok); self.assertTrue(s1.endswith('.'))
        ok,_,s2=validate_structured_identity_slot('Er unterstützt dich bei Fragen und Aufgaben innerhalb der verfügbaren Fähigkeiten.','de',2)
        self.assertTrue(ok)
        ok,_,a1=validate_structured_identity_slot('MAESTRO هو مساعد ذكاء اصطناعي محلي على هذا الحاسوب.','ar',1)
        self.assertTrue(ok)
        ok,_,a2=validate_structured_identity_slot('يساعدك في الأسئلة وتنظيم المعلومات ضمن القدرات المتاحة.','ar',2)
        self.assertTrue(ok)
        bad=validate_structured_identity_slot('MAESTRO مساعد محلي. وهذه جملة ثانية.','ar',1)
        self.assertFalse(bad[0])

    def test_arabic_slot_rejects_foreign_script(self):
        ok,reason,_=validate_structured_identity_slot('MAESTRO هو مساعد recherches محلي على هذا الحاسوب.','ar',1)
        self.assertFalse(ok); self.assertEqual(reason,'explicit_shape_arabic_only_foreign_script')

    def test_composition_only_joins_model_sentences(self):
        a='MAESTRO ist ein lokaler KI-Assistent auf diesem Computer.'
        b='Er hilft dir bei Fragen innerhalb seiner verfügbaren Fähigkeiten.'
        self.assertEqual(compose_structured_identity_slots([a,b]),a+' '+b)

    def test_mission_uses_structured_slots_and_bypasses_old_revision_cascade(self):
        src=(Path(__file__).parents[1]/'src/maestro_gateway/mission.py').read_text(encoding='utf-8')
        self.assertIn('structured_identity_slot_total = structured_identity_slot_count',src)
        self.assertIn('structured_identity_slot_instruction(',src)
        self.assertIn('validate_structured_identity_slot(',src)
        self.assertIn('compose_structured_identity_slots(',src)
        self.assertIn('and not structured_identity_slot_total',src)
        self.assertIn('"structured_identity_generation": bool(structured_identity_slot_total)',src)

    def test_no_canonical_user_facing_answer_is_hardcoded(self):
        root=Path(__file__).parents[1]/'src/maestro_gateway'
        joined='\n'.join((root/name).read_text(encoding='utf-8') for name in ('mission.py','native_linguistic_revision.py','response_effort.py'))
        self.assertNotIn('MAESTRO ist dein lokaler KI-Assistent auf diesem Computer. Er unterstützt dich',joined)
        self.assertNotIn('MAESTRO هو مساعد ذكاء اصطناعي محلي يعمل على هذا الحاسوب. يساعدك',joined)

if __name__=='__main__': unittest.main()
