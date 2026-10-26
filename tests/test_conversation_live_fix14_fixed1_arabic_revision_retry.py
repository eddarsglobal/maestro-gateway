from __future__ import annotations

import re
import unittest
from pathlib import Path

from maestro_gateway.native_linguistic_revision import (
    native_revision_instruction,
    native_revision_retry_instruction,
)

AR_PROMPT = (
    'أجب بالعربية الفصحى الطبيعية في جملتين فقط.\n'
    'ما هو MAESTRO وما وظيفته؟\n'
    'MAESTRO هو مساعد الذكاء الاصطناعي المحلي الخاص بي على هذا الحاسوب.\n'
    'لا تستخدم كلمات إنجليزية أو أحرفًا غير عربية باستثناء اسم MAESTRO.'
)
AR_DRAFT = (
    'MAESTRO هو مساعد ذكاء اصطناعي محلي على هذا الحاسوب. '
    'يساعد المستخدم في الإجابة عن الأسئلة وتنظيم المعلومات ضمن الصلاحيات المتاحة.'
)


class Fix14Fixed1ArabicRevisionRetryTests(unittest.TestCase):
    def _latin_except_maestro(self, text: str) -> list[str]:
        return re.findall(r'[A-Za-z]+', re.sub('MAESTRO', '', text, flags=re.I))

    def test_first_arabic_revision_explicitly_forbids_foreign_letters(self):
        text = native_revision_instruction(prompt=AR_PROMPT, draft=AR_DRAFT, language='ar')
        self.assertIn('لا تستخدم أي أحرف غير عربية', text)
        self.assertEqual(self._latin_except_maestro(text), [])

    def test_arabic_revision_retry_is_native_and_keeps_identity_anchor(self):
        text = native_revision_retry_instruction(
            prompt=AR_PROMPT,
            draft=AR_DRAFT,
            language='ar',
            reason='explicit_shape_arabic_only_foreign_script',
        )
        self.assertIn('محاولة مراجعة لغوية أخيرة داخلية', text)
        self.assertIn('مساعد ذكاء اصطناعي محلي على هذا الحاسوب', text)
        self.assertIn(AR_DRAFT, text)
        self.assertEqual(self._latin_except_maestro(text), [])

    def test_retry_does_not_embed_failed_revision_or_generic_english_scaffold(self):
        text = native_revision_retry_instruction(
            prompt=AR_PROMPT,
            draft=AR_DRAFT,
            language='ar',
            reason='explicit_shape_arabic_only_foreign_script',
        )
        self.assertNotIn('CURRENT USER REQUEST', text)
        self.assertNotIn('RESPONSE QUALITY RETRY', text)
        self.assertNotIn('Reply in Arabic', text)

    def test_mission_retries_failed_revision_from_original_draft_same_resource(self):
        src = (Path(__file__).parents[1] / 'src/maestro_gateway/mission.py').read_text(encoding='utf-8')
        self.assertIn('linguistic_revision_retry = bool(', src)
        self.assertIn('draft=linguistic_revision_source', src)
        self.assertIn('native_revision_retry_instruction(', src)
        self.assertIn('and not linguistic_revision_retry', src)
        self.assertIn('"linguistic_revision_retry_used"', src)


if __name__ == '__main__':
    unittest.main()
