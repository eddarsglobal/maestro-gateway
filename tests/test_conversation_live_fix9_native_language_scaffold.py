from __future__ import annotations
import unittest
from maestro_gateway.response_effort import compact_chat_instruction
from maestro_gateway.quality_retry_contract import contract_retry_hint


class Fix9NativeLanguageScaffoldTests(unittest.TestCase):
    def test_arabic_two_sentence_scaffold_has_no_latin_meta_text_except_maestro(self):
        prompt=(
            "أجب بالعربية الفصحى الطبيعية في جملتين فقط.\n"
            "ما هو MAESTRO وما وظيفته؟\n"
            "لا تستخدم كلمات إنجليزية أو أحرفًا غير عربية باستثناء اسم MAESTRO."
        )
        text=compact_chat_instruction(prompt=prompt,language_hint="Arabic",task="normal")
        import re
        cleaned=re.sub("MAESTRO","",text,flags=re.I)
        # The user's own prompt is Arabic-only too, so any Latin letter here is
        # an internal scaffold regression.
        self.assertIsNone(re.search(r"[A-Za-z]",cleaned))
        self.assertIn("الطلب:",text)
        self.assertIn("جملتين كاملتين فقط",text)

    def test_german_two_sentence_scaffold_is_native_german(self):
        prompt=(
            "Antworte auf Deutsch in genau zwei natürlichen und grammatikalisch korrekten Sätzen. "
            "Was ist MAESTRO?"
        )
        text=compact_chat_instruction(prompt=prompt,language_hint="German",task="normal")
        self.assertIn("Antworte natürlich auf Deutsch",text)
        self.assertIn("ANFRAGE:",text)
        self.assertNotIn("Reply in German",text)
        self.assertNotIn("CURRENT USER REQUEST",text)

    def test_other_languages_keep_legacy_scaffold(self):
        prompt="Reply in exactly two sentences. What is MAESTRO?"
        text=compact_chat_instruction(prompt=prompt,language_hint="English",task="normal")
        self.assertIn("Reply in English",text)
        self.assertIn("CURRENT USER REQUEST",text)

    def test_arabic_foreign_script_retry_hint_is_native_arabic(self):
        hint=contract_retry_hint("explicit_shape_arabic_only_foreign_script","ar")
        self.assertIn("قاعدة داخلية",hint)
        self.assertIn("MAESTRO",hint)
        self.assertNotIn("INTERNAL RETRY RULE",hint)

    def test_arabic_instruction_echo_retry_hint_is_native_arabic(self):
        hint=contract_retry_hint("user_instruction_echo","ar")
        self.assertIn("أجب عن السؤال نفسه فقط",hint)
        self.assertNotIn("INTERNAL RETRY RULE",hint)

    def test_german_instruction_echo_retry_hint_is_native_german(self):
        hint=contract_retry_hint("user_instruction_echo","de")
        self.assertIn("Interne Regel",hint)
        self.assertIn("Beantworte nur die eigentliche Frage",hint)

    def test_native_ui_prompts_stay_under_v810_compact_budget(self):
        de=(
            "Antworte auf Deutsch in genau zwei natürlichen und grammatikalisch korrekten Sätzen.\n"
            "Was ist MAESTRO und wozu dient es?\n"
            "MAESTRO ist mein lokaler KI-Assistent auf diesem Computer.\n"
            "Verwende durchgehend die Anrede „du“."
        )
        ar=(
            "أجب بالعربية الفصحى الطبيعية في جملتين فقط.\n"
            "ما هو MAESTRO وما وظيفته؟\n"
            "MAESTRO هو مساعد الذكاء الاصطناعي المحلي الخاص بي على هذا الحاسوب.\n"
            "لا تستخدم كلمات إنجليزية أو أحرفًا غير عربية باستثناء اسم MAESTRO."
        )
        self.assertLess(len(compact_chat_instruction(prompt=de,language_hint="German",task="normal")),480)
        self.assertLess(len(compact_chat_instruction(prompt=ar,language_hint="Arabic",task="normal")),480)

    def test_fix9_does_not_name_or_prefer_any_model(self):
        src=(__import__('pathlib').Path(__file__).resolve().parents[1]/'src/maestro_gateway/response_effort.py').read_text()
        self.assertNotIn('llama3.2',src)
        self.assertNotIn('gemma:2b',src)
        self.assertNotIn('qwen',src)


if __name__=='__main__': unittest.main()
