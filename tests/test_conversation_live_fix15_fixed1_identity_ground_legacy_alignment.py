from __future__ import annotations
import unittest
from pathlib import Path

from maestro_gateway.response_effort import compact_chat_instruction

DE=(
    "Antworte auf Deutsch in genau zwei natürlichen und grammatikalisch korrekten Sätzen.\n"
    "Was ist MAESTRO und wozu dient es?\n"
    "MAESTRO ist mein lokaler KI-Assistent auf diesem Computer.\n"
    "Verwende durchgehend die Anrede „du“."
)
AR=(
    "أجب بالعربية الفصحى الطبيعية في جملتين فقط.\n"
    "ما هو MAESTRO وما وظيفته؟\n"
    "MAESTRO هو مساعد الذكاء الاصطناعي المحلي الخاص بي على هذا الحاسوب.\n"
    "لا تستخدم كلمات إنجليزية أو أحرفًا غير عربية باستثناء اسم MAESTRO."
)

class Fix15Fixed1IdentityGroundLegacyAlignmentTests(unittest.TestCase):
    def test_german_legacy_identity_marker_is_fact_ground_not_answer(self):
        text=compact_chat_instruction(prompt=DE,language_hint="German",task="normal")
        self.assertTrue(text.startswith("Du bist MAESTRO"))
        self.assertIn("lokaler KI-Assistent",text)
        self.assertIn("Telefon-App",text)
        self.assertLess(len(text),480)

    def test_arabic_legacy_identity_marker_is_fact_ground_not_answer(self):
        text=compact_chat_instruction(prompt=AR,language_hint="Arabic",task="normal")
        self.assertIn("أنت MAESTRO",text)
        self.assertIn("مساعد ذكاء اصطناعي محلي على هذا الحاسوب",text)
        self.assertLess(len(text),480)

    def test_old_canonical_user_facing_answers_remain_absent(self):
        root=Path(__file__).parents[1]/"src/maestro_gateway"
        joined="\n".join((root/name).read_text(encoding="utf-8") for name in (
            "mission.py","native_linguistic_revision.py","response_effort.py"))
        self.assertNotIn("MAESTRO ist dein lokaler KI-Assistent auf diesem Computer. Er unterstützt dich",joined)
        self.assertNotIn("MAESTRO هو مساعد ذكاء اصطناعي محلي يعمل على هذا الحاسوب. يساعدك",joined)

    def test_fix15_remains_model_generated_fact_grounded(self):
        mission=(Path(__file__).parents[1]/"src/maestro_gateway/mission.py").read_text(encoding="utf-8")
        self.assertIn("identity_fact_instruction = fact_grounded_identity_instruction",mission)
        # FIX16 supersedes the monolithic identity transcript with structured
        # generative identity slots. Preserve the original invariant (fact-grounded,
        # model-generated, no deterministic answer projection) without pinning the
        # obsolete implementation line.
        self.assertIn("structured_identity_slot_total = structured_identity_slot_count",mission)
        self.assertIn("identity_fact_instruction or structured_identity_slot_total",mission)
        self.assertNotIn("transcript = identity_fact_instruction or base_instruction",mission)
        self.assertIn("adapter = registry.adapter(resource_id)",mission)
        self.assertNotIn("identity_core_candidate",mission)

if __name__ == "__main__":
    unittest.main()
