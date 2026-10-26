from __future__ import annotations
import unittest
from maestro_gateway.response_guard import evaluate_response
from maestro_gateway.quality_retry_contract import contract_retry_hint
from maestro_gateway.response_effort import compact_chat_instruction

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

class Fix12DirectAnswerQualityGuardTests(unittest.TestCase):
    def test_rejects_arabic_answer_preamble(self):
        out='الجواب على السؤال هو: MAESTRO مساعد ذكاء اصطناعي محلي. وظيفته مساعدتك في المهام اليومية.'
        r=evaluate_response(AR_PROMPT,out,completion_reason='stop')
        self.assertFalse(r.accepted); self.assertEqual(r.reason,'direct_answer_meta_preamble')

    def test_rejects_arabic_question_restatement(self):
        out='ما هو MAESTRO وما وظيفته؟ MAESTRO مساعد ذكاء اصطناعي محلي على هذا الحاسوب.'
        r=evaluate_response(AR_PROMPT,out,completion_reason='stop')
        self.assertFalse(r.accepted); self.assertEqual(r.reason,'direct_answer_question_restatement')

    def test_accepts_direct_arabic_two_sentence_answer(self):
        out='MAESTRO مساعد ذكاء اصطناعي محلي على هذا الحاسوب. يساعدك في معالجة الأسئلة والمهام ضمن الصلاحيات المتاحة.'
        r=evaluate_response(AR_PROMPT,out,completion_reason='stop')
        self.assertTrue(r.accepted, r.reason)

    def test_rejects_german_question_restatement(self):
        out='Was ist MAESTRO und wozu dient es? MAESTRO ist dein lokaler KI-Assistent auf diesem Computer.'
        r=evaluate_response(DE_PROMPT,out,completion_reason='stop')
        self.assertFalse(r.accepted); self.assertEqual(r.reason,'direct_answer_question_restatement')

    def test_retry_hints_are_native(self):
        self.assertIn('جملتين خبريتين',contract_retry_hint('direct_answer_question_restatement','ar'))
        self.assertIn('Aussagesätzen',contract_retry_hint('direct_answer_meta_preamble','de'))

    def test_native_scaffolds_explicitly_forbid_question_restatement(self):
        ar=compact_chat_instruction(prompt=AR_PROMPT,language_hint='Arabic',task='normal')
        de=compact_chat_instruction(prompt=DE_PROMPT,language_hint='German',task='normal')
        self.assertIn('لا تكرر السؤال',ar)
        self.assertIn('weder Frage noch Anweisung',de)

    def test_ui_scaffolds_remain_bounded(self):
        ar=compact_chat_instruction(prompt=AR_PROMPT,language_hint='Arabic',task='normal')
        de=compact_chat_instruction(prompt=DE_PROMPT,language_hint='German',task='normal')
        self.assertLess(len(ar),520)
        self.assertLess(len(de),520)

if __name__=='__main__': unittest.main()
