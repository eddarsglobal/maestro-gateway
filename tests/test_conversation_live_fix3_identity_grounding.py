from __future__ import annotations
import unittest
from maestro_gateway.response_effort import compact_chat_instruction


class CompactIdentityGroundingTests(unittest.TestCase):
    def test_two_sentence_identity_request_keeps_maestro_identity(self):
        prompt = (
            "Antworte auf Deutsch. Schreibe zwei natürliche und grammatikalisch "
            "korrekte deutsche Sätze darüber, was MAESTRO ist."
        )
        text = compact_chat_instruction(
            prompt=prompt, language_hint="German", task="normal", recent_context=""
        )
        self.assertIn("Du bist MAESTRO", text)
        self.assertIn("lokaler KI-Assistent", text)
        self.assertIn("Telefon-App", text)
        self.assertIn("genau zwei Sätzen", text)
        self.assertIn(prompt, text)

    def test_arabic_two_sentence_path_keeps_same_identity_ground(self):
        prompt = (
            "أجب باللغة العربية. اكتب جملتين عربيتين طبيعيتين وصحيحتين نحويًا "
            "تشرحان ما هو MAESTRO."
        )
        text = compact_chat_instruction(
            prompt=prompt, language_hint="Arabic", task="normal", recent_context=""
        )
        self.assertIn("أنت MAESTRO", text)
        self.assertIn("مساعد ذكاء اصطناعي محلي", text)
        self.assertIn("أجب بالعربية الفصحى الطبيعية", text)
        self.assertIn(prompt, text)

    def test_generic_normal_chat_keeps_identity_ground(self):
        text = compact_chat_instruction(
            prompt="Was kannst du tun?", language_hint="German", task="normal",
            recent_context="USER: Hallo"
        )
        self.assertTrue(text.startswith("Du bist MAESTRO"))
        self.assertIn("CONTEXT:\nUSER: Hallo", text)

    def test_structured_grounding_preserves_v810_compact_budget(self):
        prompt = (
            "Explique en trois étapes pratiques comment vérifier qu’un service web "
            "fonctionne entièrement sur mon ordinateur, sans cloud ni téléchargement. "
            "Cite une limite."
        )
        text = compact_chat_instruction(
            prompt=prompt, language_hint="French", task="normal", recent_context=""
        )
        self.assertIn("You are MAESTRO: a local AI assistant on this computer", text)
        self.assertIn("exactly three numbered items", text)
        self.assertIn("beginning 'Limite:'", text)
        self.assertLess(len(text), 480)

    def test_grounding_explicitly_disambiguates_product_identity(self):
        text = compact_chat_instruction(
            prompt="Was ist MAESTRO?", language_hint="German", task="normal",
            recent_context=""
        )
        self.assertIn("keine musik", text.casefold())
        self.assertIn("telefon-app", text.casefold())


if __name__ == '__main__':
    unittest.main()
