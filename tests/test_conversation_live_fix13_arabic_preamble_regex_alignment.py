from __future__ import annotations
import unittest
from maestro_gateway.response_guard import evaluate_response

AR_PROMPT=(
    "أجب بالعربية الفصحى الطبيعية في جملتين فقط.\n"
    "ما هو MAESTRO وما وظيفته؟\n"
    "MAESTRO هو مساعد الذكاء الاصطناعي المحلي الخاص بي على هذا الحاسوب.\n"
    "لا تستخدم كلمات إنجليزية أو أحرفًا غير عربية باستثناء اسم MAESTRO."
)

class Fix13ArabicPreambleRegexAlignmentTests(unittest.TestCase):
    def test_live_fix12_preamble_variants_are_rejected_and_direct_answer_passes(self):
        bad=[
            "الجواب هي: \nMAESTRO هو مساعد الذكاء الاصطناعي المحلي الخاص بي على هذا الحاسوب.\nوظيفته هي تقديم الدعم للمستخدم.",
            "الجواب هو: MAESTRO مساعد ذكاء اصطناعي محلي. يساعدك في المهام اليومية.",
            "الإجابة عن السؤال هي: MAESTRO مساعد ذكاء اصطناعي محلي. يساعدك في المهام اليومية.",
        ]
        for out in bad:
            with self.subTest(out=out):
                r=evaluate_response(AR_PROMPT,out,completion_reason="stop")
                self.assertFalse(r.accepted)
                self.assertEqual(r.reason,"direct_answer_meta_preamble")
        good="MAESTRO مساعد ذكاء اصطناعي محلي على هذا الحاسوب. يساعدك في معالجة الأسئلة والمهام ضمن الصلاحيات المتاحة."
        r=evaluate_response(AR_PROMPT,good,completion_reason="stop")
        self.assertTrue(r.accepted,r.reason)

if __name__=="__main__": unittest.main()
