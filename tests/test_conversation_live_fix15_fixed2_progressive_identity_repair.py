from __future__ import annotations
import re
import unittest
from pathlib import Path

from maestro_gateway.native_linguistic_revision import (
    fact_grounded_identity_instruction,
    maestro_identity_contradicted,
    maestro_identity_grounded,
    native_revision_instruction,
    native_revision_retry_instruction,
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

class Fix15Fixed2ProgressiveIdentityRepairTests(unittest.TestCase):
    def test_real_live_german_person_confusion_is_rejected(self):
        bad=(
            'Ich kann keine persönlichen Informationen oder Ansichten zu Personen wie MAESTRO erstellen. '
            'Als lokaler KI-Assistent auf diesem Computer bin ich darauf programmiert, nur innerhalb meiner verfügbaren und autorisierten Fähigkeiten zu arbeiten.'
        )
        self.assertTrue(maestro_identity_contradicted(bad,'de'))
        self.assertFalse(maestro_identity_grounded(bad,'de'))

    def test_grounding_requires_identity_first_and_role_second(self):
        good=(
            'MAESTRO ist ein lokaler KI-Assistent auf diesem Computer. '
            'Er unterstützt dich bei Fragen und Aufgaben innerhalb seiner verfügbaren Fähigkeiten.'
        )
        reversed_bad=(
            'Ich helfe dir bei verschiedenen Aufgaben auf diesem Computer. '
            'MAESTRO ist ein lokaler KI-Assistent auf diesem Computer.'
        )
        self.assertTrue(maestro_identity_grounded(good,'de'))
        self.assertFalse(maestro_identity_grounded(reversed_bad,'de'))

    def test_fact_grounded_prompts_assign_one_semantic_job_per_sentence(self):
        de=fact_grounded_identity_instruction(DE_PROMPT,'de') or ''
        ar=fact_grounded_identity_instruction(AR_PROMPT,'ar') or ''
        self.assertIn('Satz 1 nennt MAESTRO ausdrücklich',de)
        self.assertIn('Satz 2 erklärt',de)
        self.assertIn('keine Person',de)
        self.assertIn('الجملة الأولى تذكر MAESTRO',ar)
        self.assertIn('الجملة الثانية تشرح',ar)
        self.assertIn('لا تكتب جملة ثالثة',ar)

    def test_arabic_revision_scrubs_mixed_script_from_internal_draft(self):
        draft='MAESTRO هو أفkar مساعد محلي. وظيفته تنظيم الفكRA والمعلومات.'
        text=native_revision_instruction(prompt=AR_PROMPT,draft=draft,language='ar')
        self.assertNotIn('أفkar',text)
        self.assertNotIn('الفكRA',text)
        self.assertEqual(re.findall(r'[A-Za-z]+', re.sub('MAESTRO','',text,flags=re.I)),[])

    def test_identity_retry_uses_two_sentence_slots(self):
        ar=native_revision_retry_instruction(prompt=AR_PROMPT,draft='MAESTRO مساعد محلي.',language='ar',reason='maestro_identity_not_grounded')
        de=native_revision_retry_instruction(prompt=DE_PROMPT,draft='MAESTRO ist lokal.',language='de',reason='maestro_identity_contradicted')
        self.assertIn('جملتين فقط لا ثلاثاً',ar)
        self.assertIn('الأولى تذكر MAESTRO',ar)
        self.assertIn('Satz 1 identifiziert MAESTRO',de)
        self.assertIn('Keine Personen- oder Datenschutzablehnung',de)

    def test_mission_progresses_from_latest_identity_revision_candidate(self):
        src=(Path(__file__).parents[1]/'src/maestro_gateway/mission.py').read_text(encoding='utf-8')
        self.assertIn('_identity_progressive_retry = bool(identity_fact_instruction)',src)
        self.assertIn('latest_identity_revision_candidate',src)
        self.assertIn('maestro_identity_contradicted',src)
        self.assertIn('(identity_fact_instruction or base_instruction) + _retry_note',src)

    def test_no_user_facing_identity_answer_is_hardcoded(self):
        root=Path(__file__).parents[1]/'src/maestro_gateway'
        joined='\n'.join((root/name).read_text(encoding='utf-8') for name in ('mission.py','native_linguistic_revision.py','response_effort.py'))
        self.assertNotIn('MAESTRO ist dein lokaler KI-Assistent auf diesem Computer. Er unterstützt dich',joined)
        self.assertNotIn('MAESTRO هو مساعد ذكاء اصطناعي محلي يعمل على هذا الحاسوب. يساعدك',joined)

if __name__=='__main__': unittest.main()
