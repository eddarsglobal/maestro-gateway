from __future__ import annotations
import unittest
from pathlib import Path

from maestro_gateway.native_linguistic_revision import (
    fact_grounded_identity_instruction,
    maestro_identity_fact_capsule,
    maestro_identity_grounding_required,
)

DE_PROMPT = (
    'Antworte auf Deutsch in genau zwei natürlichen und grammatikalisch korrekten Sätzen.\n'
    'Was ist MAESTRO und wozu dient es?\n'
    'MAESTRO ist mein lokaler KI-Assistent auf diesem Computer.\n'
    'Verwende durchgehend die Anrede „du“.\n'
)
AR_PROMPT = (
    'أجب بالعربية الفصحى الطبيعية في جملتين فقط.\n'
    'ما هو MAESTRO وما وظيفته؟\n'
    'MAESTRO هو مساعد الذكاء الاصطناعي المحلي الخاص بي على هذا الحاسوب.\n'
    'لا تستخدم كلمات إنجليزية أو أحرفًا غير عربية باستثناء اسم MAESTRO.'
)

class Fix14Fixed4DeprecatedProjectionCompatibilityTests(unittest.TestCase):
    def test_identity_remains_governed_as_facts(self):
        self.assertTrue(maestro_identity_grounding_required(DE_PROMPT, 'de'))
        self.assertTrue(maestro_identity_grounding_required(AR_PROMPT, 'ar'))
        self.assertIn('MAESTRO', maestro_identity_fact_capsule('de'))
        self.assertIn('MAESTRO', maestro_identity_fact_capsule('ar'))

    def test_identity_is_now_generative_not_projected_text(self):
        self.assertIsNotNone(fact_grounded_identity_instruction(DE_PROMPT, 'de'))
        self.assertIsNotNone(fact_grounded_identity_instruction(AR_PROMPT, 'ar'))
        self.assertIsNone(fact_grounded_identity_instruction('Was ist MAESTRO?', 'de'))

    def test_old_canonical_german_answer_is_not_in_generation_module(self):
        src=(Path(__file__).parents[1]/'src/maestro_gateway/native_linguistic_revision.py').read_text(encoding='utf-8')
        self.assertNotIn('MAESTRO ist dein lokaler KI-Assistent auf diesem Computer. Er unterstützt dich', src)

    def test_old_canonical_arabic_answer_is_not_in_generation_module(self):
        src=(Path(__file__).parents[1]/'src/maestro_gateway/native_linguistic_revision.py').read_text(encoding='utf-8')
        self.assertNotIn('MAESTRO هو مساعد ذكاء اصطناعي محلي يعمل على هذا الحاسوب. يساعدك', src)

    def test_mission_no_longer_has_identity_projection_lane(self):
        src=(Path(__file__).parents[1]/'src/maestro_gateway/mission.py').read_text(encoding='utf-8')
        self.assertNotIn('governed_maestro_identity_projection', src)
        self.assertNotIn('identity_core_candidate', src)
        self.assertIn('fact_grounded_identity_instruction', src)
        self.assertIn('sovereign_identity_fact_grounding', src)

if __name__ == '__main__': unittest.main()
