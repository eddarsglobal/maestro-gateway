"""FIX4-FIXED1 CLEAN2: preserve V8.8/V8.9 literal compact-contract wording under V8.10 budget."""
from __future__ import annotations
import unittest
from maestro_gateway.response_effort import compact_chat_instruction

PROMPT = ('Explique en trois étapes pratiques comment vérifier qu’un service web '
          'fonctionne entièrement sur mon ordinateur, sans cloud ni téléchargement. '
          'Cite une limite.')

class CompactLegacyContractTests(unittest.TestCase):
    def test_legacy_contract_phrases_and_v810_budget_coexist(self):
        text = compact_chat_instruction(prompt=PROMPT, language_hint='French', task='normal')
        self.assertIn('exactly three numbered items', text)
        self.assertIn("beginning 'Limite:'", text)
        self.assertIn('at most 12 words', text)
        self.assertIn('No preamble or conclusion', text)
        self.assertIn('CURRENT USER REQUEST', text)
        self.assertLess(len(text), 480)

    def test_maestro_identity_grounding_survives_compaction(self):
        text = compact_chat_instruction(prompt=PROMPT, language_hint='French', task='normal')
        self.assertIn("You are MAESTRO: a local AI assistant on this computer", text)
        self.assertIn("not music, a person, company, or phone app", text)

if __name__ == '__main__':
    unittest.main()
