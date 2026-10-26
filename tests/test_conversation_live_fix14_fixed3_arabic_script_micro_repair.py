from __future__ import annotations
import re, unittest
from pathlib import Path
from maestro_gateway.native_linguistic_revision import foreign_alpha_tokens_except_maestro, native_revision_instruction, native_revision_retry_instruction
AR_PROMPT=('أجب بالعربية الفصحى الطبيعية في جملتين فقط.\nما هو MAESTRO وما وظيفته؟\nMAESTRO هو مساعد الذكاء الاصطناعي المحلي الخاص بي على هذا الحاسوب.\nلا تستخدم كلمات إنجليزية أو أحرفًا غير عربية باستثناء اسم MAESTRO.')
AR_DRAFT='MAESTRO هو local AI assistant على هذا الحاسوب. يساعد المستخدم في تنظيم المعلومات.'
class Fix14Fixed3ArabicScriptMicroRepairTests(unittest.TestCase):
    def test_foreign_token_diagnostic_excludes_maestro(self):
        self.assertEqual(foreign_alpha_tokens_except_maestro('أنا MAESTRO local AI مساعد.'),('local','AI'))
    def test_first_revision_translates_foreign_words(self):
        t=native_revision_instruction(prompt=AR_PROMPT,draft=AR_DRAFT,language='ar'); self.assertIn('عبّر بالعربية عن أي كلمة أجنبية أخرى',t)
        scaffold=t.split('المسودة:\n',1)[0]; self.assertEqual(re.findall(r'[A-Za-z]+',re.sub('MAESTRO','',scaffold,flags=re.I)),[])
    def test_retry_is_script_micro_repair(self):
        t=native_revision_retry_instruction(prompt=AR_PROMPT,draft='MAESTRO هو local مساعد.',language='ar',reason='explicit_shape_arabic_only_foreign_script')
        self.assertIn('محاولة مراجعة لغوية أخيرة داخلية',t); self.assertIn('لا تستخدم أي أحرف غير عربية',t); self.assertIn('حوّل كل كلمة أجنبية أخرى إلى تعبير عربي',t)
    def test_arabic_revision_budget_is_128(self):
        s=(Path(__file__).parents[1]/'src/maestro_gateway/mission.py').read_text(); self.assertIn('min(max_tokens, 128)',s); self.assertIn('normalized_language(route_language) == "ar"',s)
    def test_foreign_script_retry_uses_failed_revision_candidate(self):
        s=(Path(__file__).parents[1]/'src/maestro_gateway/mission.py').read_text(); self.assertIn('draft=_revision_retry_draft',s); self.assertIn('"failed_revision_candidate"',s); self.assertIn('"foreign_script_tokens"',s)
if __name__=='__main__': unittest.main()
