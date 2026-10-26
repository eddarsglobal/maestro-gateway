from __future__ import annotations
import re
import unittest
from pathlib import Path

from maestro_gateway.native_linguistic_revision import (
    linguistic_revision_requested,
    maestro_identity_grounded,
    maestro_identity_grounding_required,
    native_revision_instruction,
)

AR_PROMPT = (
    'أجب بالعربية الفصحى الطبيعية في جملتين فقط.\n'
    'ما هو MAESTRO وما وظيفته؟\n'
    'MAESTRO هو مساعد الذكاء الاصطناعي المحلي الخاص بي على هذا الحاسوب.\n'
    'لا تستخدم كلمات إنجليزية أو أحرفًا غير عربية باستثناء اسم MAESTRO.'
)
DE_PROMPT = (
    'Antworte auf Deutsch in genau zwei natürlichen und grammatikalisch korrekten Sätzen.\n'
    'Was ist MAESTRO und wozu dient es?\n'
    'MAESTRO ist mein lokaler KI-Assistent auf diesem Computer.\n'
    'Verwende durchgehend die Anrede „du“.'
)

class Fix14NativeLinguisticRevisionTests(unittest.TestCase):
    def test_revision_is_narrowly_requested_for_the_explicit_native_quality_turns(self):
        self.assertTrue(linguistic_revision_requested(DE_PROMPT, 'de'))
        self.assertTrue(linguistic_revision_requested(AR_PROMPT, 'ar'))
        self.assertFalse(linguistic_revision_requested('Antworte nur mit JA.', 'de'))
        self.assertFalse(linguistic_revision_requested('أجب بكلمة نعم فقط.', 'ar'))

    def test_identity_grounding_rejects_the_human_failed_arabic_answer(self):
        bad = (
            'هذا الاسم يُستخدم كمرتبك للمساعدة الذكية الموجودة في الحاسوب. '
            'يستخدم هذا الاسم لتوضيح الوظيفة المحددة للمساعد الذكي.'
        )
        self.assertTrue(maestro_identity_grounding_required(AR_PROMPT, 'ar'))
        self.assertFalse(maestro_identity_grounded(bad, 'ar'))
        good = (
            'MAESTRO هو مساعد ذكاء اصطناعي محلي على هذا الحاسوب. '
            'يساعد المستخدم في الإجابة عن الأسئلة وتنظيم المعلومات ضمن الصلاحيات المتاحة.'
        )
        self.assertTrue(maestro_identity_grounded(good, 'ar'))

    def test_identity_grounding_accepts_clear_natural_german_identity(self):
        self.assertTrue(maestro_identity_grounding_required(DE_PROMPT, 'de'))
        good = (
            'MAESTRO ist ein lokaler KI-Assistent auf deinem Computer. '
            'Er unterstützt dich bei Fragen und Aufgaben innerhalb der verfügbaren Möglichkeiten.'
        )
        self.assertTrue(maestro_identity_grounded(good, 'de'))

    def test_revision_prompts_are_native_and_keep_identity_anchor(self):
        ar = native_revision_instruction(prompt=AR_PROMPT, draft='MAESTRO هو نظام حاسوبي. وظيفته تنظيم المعلومات.', language='ar')
        de = native_revision_instruction(prompt=DE_PROMPT, draft='MAESTRO ist ein Assistent. Er hilft dir.', language='de')
        self.assertIn('مراجعة لغوية داخلية', ar)
        self.assertIn('مساعد ذكاء اصطناعي محلي على هذا الحاسوب', ar)
        self.assertIn('Interne sprachliche Überarbeitung', de)
        self.assertIn('lokaler KI-Assistent auf diesem Computer', de)
        # Arabic internal scaffold contains no Latin words except the product name.
        self.assertEqual(re.findall(r'[A-Za-z]+', re.sub('MAESTRO', '', ar, flags=re.I)), [])

    def test_mission_records_same_resource_revision_telemetry(self):
        src = (Path(__file__).parents[1] / 'src/maestro_gateway/mission.py').read_text(encoding='utf-8')
        self.assertIn('linguistic_revision_draft_accepted', src)
        self.assertIn('linguistic_revision_same_resource', src)
        self.assertIn('linguistic_revision_resource = resource_id', src)
        self.assertIn('native_revision_instruction(', src)

if __name__ == '__main__':
    unittest.main()
