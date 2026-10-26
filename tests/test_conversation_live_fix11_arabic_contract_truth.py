from __future__ import annotations

import unittest

from maestro_gateway.response_constraints import evaluate_shape
from maestro_gateway.response_guard import evaluate_response


STRICT_AR = (
    'أجب بالعربية الفصحى الطبيعية في جملتين فقط.\n'
    'ما هو MAESTRO وما وظيفته؟\n'
    'MAESTRO هو مساعد الذكاء الاصطناعي المحلي الخاص بي على هذا الحاسوب.\n'
    'لا تستخدم كلمات إنجليزية أو أحرفًا غير عربية باستثناء اسم MAESTRO.'
)


class Fix11ArabicContractTruthTests(unittest.TestCase):
    def test_arabic_question_mark_counts_as_sentence_boundary(self):
        bad = (
            'ما هي وظيفة MAESTRO؟ هي مساعد ذكاء اصطناعي محلي على هذا الحاسوب. '
            'ماذا يفعل MAESTRO؟ يساعد المستخدم في أداء المهام اليومية.'
        )
        got = evaluate_shape(STRICT_AR, bad)
        self.assertEqual(got.status, 'FAIL_SHAPE')
        self.assertEqual(got.reason, 'expected_two_sentences_got_4')

    def test_real_diagnostic_meta_preamble_is_rejected(self):
        bad = (
            'سأجبه بالعربية الفصحى الطبيعية في جملتين فقط:\n\n'
            'ما هي وظيفة MAESTRO؟ هي مساعد ذكاء اصطناعي محلي على هذا الحاسوب.\n\n'
            'ماذا يفعل MAESTRO؟ يلعب دور مساعد ذكاء اصطناعي محلي على هذا الحاسوب، '
            'مما يساعد في أداء المهام التالية.'
        )
        got = evaluate_response(STRICT_AR, bad)
        self.assertFalse(got.accepted)
        self.assertEqual(got.reason, 'user_instruction_echo')

    def test_paraphrased_arabic_meta_preamble_is_rejected(self):
        bad = (
            'سأجيب بالعربية الفصحى الطبيعية في جملتين فقط: '
            'MAESTRO مساعد ذكاء اصطناعي محلي على هذا الحاسوب. '
            'وظيفته مساعدة المستخدم في تنفيذ المهام وفهم المعلومات.'
        )
        got = evaluate_response(STRICT_AR, bad)
        self.assertFalse(got.accepted)
        self.assertEqual(got.reason, 'user_instruction_echo')

    def test_direct_two_sentence_arabic_answer_still_passes_shape(self):
        good = (
            'MAESTRO هو مساعد ذكاء اصطناعي محلي يعمل على هذا الحاسوب لمساعدتك في فهم المعلومات وتنظيم المهام. '
            'وظيفته تقديم مساعدة عملية ضمن الصلاحيات والأدوات المتاحة له.'
        )
        shape = evaluate_shape(STRICT_AR, good)
        self.assertEqual(shape.status, 'PASS_SHAPE')
        got = evaluate_response(STRICT_AR, good)
        self.assertTrue(got.accepted, got.reason)

    def test_normal_arabic_answer_is_not_false_meta_echo(self):
        prompt = 'أجب بالعربية فقط: ما فائدة التنظيم الجيد للملفات؟'
        good = 'يساعد التنظيم الجيد للملفات على الوصول إلى المعلومات بسرعة ويقلل ضياع الوقت.'
        got = evaluate_response(prompt, good)
        self.assertTrue(got.accepted, got.reason)


if __name__ == '__main__':
    unittest.main()
