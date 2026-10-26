from __future__ import annotations
import re
import unittest
from pathlib import Path

from maestro_gateway.native_linguistic_revision import (
    native_revision_instruction,
    native_revision_retry_instruction,
    repairable_linguistic_draft,
)

AR_PROMPT=(
    'أجب بالعربية الفصحى الطبيعية في جملتين فقط.\n'
    'ما هو MAESTRO وما وظيفته؟\n'
    'MAESTRO هو مساعد الذكاء الاصطناعي المحلي الخاص بي على هذا الحاسوب.\n'
    'لا تستخدم كلمات إنجليزية أو أحرفًا غير عربية باستثناء اسم MAESTRO.'
)
DE_PROMPT=(
    'Antworte auf Deutsch in genau zwei natürlichen und grammatikalisch korrekten Sätzen.\n'
    'Was ist MAESTRO und wozu dient es?\n'
    'MAESTRO ist mein lokaler KI-Assistent auf diesem Computer.\n'
    'Verwende durchgehend die Anrede „du“.'
)

class Fix14Fixed2DraftThenRevisionTests(unittest.TestCase):
    def test_repairable_arabic_preamble_can_enter_revision_but_is_not_delivery_acceptance(self):
        self.assertTrue(repairable_linguistic_draft('direct_answer_meta_preamble','الجواب هو: MAESTRO مساعد محلي.'))
        self.assertTrue(repairable_linguistic_draft('explicit_shape_expected_two_sentences_got_3','ثلاث جمل. هنا. الآن.'))

    def test_semantic_or_authority_failure_cannot_enter_revision_lane(self):
        self.assertFalse(repairable_linguistic_draft('unverified_git_commit_execution','سأنفذ ذلك.'))
        self.assertFalse(repairable_linguistic_draft('unsupported_system_file_access','سأقرأ ملفات النظام.'))
        self.assertFalse(repairable_linguistic_draft('direct_answer_meta_preamble',''))

    def test_revision_prompts_do_not_embed_original_question(self):
        ar=native_revision_instruction(prompt=AR_PROMPT,draft='MAESTRO مساعد محلي. يساعد المستخدم.',language='ar')
        de=native_revision_instruction(prompt=DE_PROMPT,draft='MAESTRO ist ein lokaler KI-Assistent. Er hilft dir.',language='de')
        self.assertNotIn('ما هو MAESTRO وما وظيفته؟',ar)
        self.assertNotIn('Was ist MAESTRO und wozu dient es?',de)
        self.assertNotIn('طلب المستخدم',ar)
        self.assertNotIn('Nutzeranfrage',de)

    def test_arabic_revision_scaffolds_have_no_latin_except_maestro(self):
        for text in (
            native_revision_instruction(prompt=AR_PROMPT,draft='MAESTRO مساعد محلي. يساعد المستخدم.',language='ar'),
            native_revision_retry_instruction(prompt=AR_PROMPT,draft='MAESTRO مساعد محلي. يساعد المستخدم.',language='ar',reason='direct_answer_meta_preamble'),
        ):
            rest=re.sub('MAESTRO','',text,flags=re.I)
            self.assertEqual(re.findall(r'[A-Za-z]+',rest),[])
            self.assertIn('مساعد ذكاء اصطناعي محلي على هذا الحاسوب',text)

    def test_mission_revises_repairable_draft_before_generic_retry(self):
        src=(Path(__file__).parents[1]/'src/maestro_gateway/mission.py').read_text(encoding='utf-8')
        gate='quality.accepted or repairable_linguistic_draft(quality.reason, candidate)'
        self.assertIn(gate,src)
        self.assertIn('linguistic_revision_draft_repairable',src)
        self.assertIn('linguistic_revision_source_quality_reason',src)
        self.assertLess(src.index(gate),src.index('linguistic_revision_retry = bool('))

if __name__=='__main__': unittest.main()
