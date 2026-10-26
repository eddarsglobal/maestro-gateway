"""Root language contract replay from the real 2026-10-23 Mac audit.
These are negative proofs only, not grammatical or model competence certificates.
"""
import json
import unittest
from pathlib import Path
from maestro_gateway.multilingual_mission_contract import parse_mission_contract, obvious_contract_failure
from maestro_gateway.response_constraints import evaluate_shape, explicit_two_sentences
from maestro_gateway.response_guard import evaluate_response

RECORDS=json.loads((Path(__file__).parent/'fixtures/root_language_evidence_22.json').read_text(encoding='utf-8'))

class MultilingualRootContractTests(unittest.TestCase):
    def test_audit_has_twenty_two_real_missions(self):
        self.assertEqual(len(RECORDS),22)

    def test_english_four_sentences_rejected(self):
        r=RECORDS[0]
        self.assertEqual(parse_mission_contract(r['prompt']).sentence_count,2)
        self.assertFalse(evaluate_response(r['prompt'],r['output']).accepted)

    def test_french_missing_name_rejected(self):
        r=RECORDS[2]
        self.assertEqual(evaluate_response(r['prompt'],r['output']).reason,'contract_identity_name_missing')

    def test_arabic_identity_not_hallucinated_away(self):
        r=RECORDS[6]
        self.assertEqual(parse_mission_contract(r['prompt']).language,'ar')
        self.assertEqual(evaluate_response(r['prompt'],r['output']).reason,'contract_identity_name_missing')

    def test_arabic_refusal_to_safe_definition_rejected(self):
        r=RECORDS[7]
        self.assertEqual(evaluate_response(r['prompt'],r['output']).reason,'contract_safe_definition_refusal')

    def test_croatian_english_substitution_rejected(self):
        r=RECORDS[13]
        self.assertEqual(evaluate_response(r['prompt'],r['output']).reason,'contract_obvious_english_substitution')

    def test_chinese_sentence_count_rejected(self):
        r=RECORDS[16]
        self.assertEqual(parse_mission_contract(r['prompt']).language,'zh')
        self.assertFalse(evaluate_response(r['prompt'],r['output']).accepted)

    def test_hindi_english_meta_response_rejected(self):
        r=RECORDS[18]
        self.assertEqual(evaluate_response(r['prompt'],r['output']).reason,'contract_output_language_script_mismatch')

    def test_swahili_missing_brand_rejected(self):
        r=RECORDS[20]
        self.assertEqual(evaluate_response(r['prompt'],r['output']).reason,'contract_identity_name_missing')

    def test_russian_single_sentence_rejected(self):
        r=RECORDS[21]
        self.assertEqual(parse_mission_contract(r['prompt']).sentence_count,2)
        self.assertFalse(evaluate_response(r['prompt'],r['output']).accepted)

    def test_all_official_and_exploratory_language_tags(self):
        expected=['en','en','fr','fr','de','de','ar','ar','pt','pt','es','es','hr','hr','it','it','zh','ja','hi','tr','sw','ru']
        self.assertEqual([parse_mission_contract(r['prompt']).language for r in RECORDS],expected)

    def test_single_sentence_count_in_eight_languages(self):
        for i in [1,3,5,7,9,11,13,15]:
            with self.subTest(index=i):
                self.assertEqual(parse_mission_contract(RECORDS[i]['prompt']).sentence_count,1)

    def test_identity_two_count_in_eight_languages(self):
        for i in [0,2,4,6,8,10,12,14]:
            with self.subTest(index=i):
                self.assertEqual(parse_mission_contract(RECORDS[i]['prompt']).sentence_count,2)

    def test_exploratory_two_sentence_count_in_six_languages(self):
        for i in range(16,22):
            with self.subTest(index=i):
                self.assertEqual(parse_mission_contract(RECORDS[i]['prompt']).sentence_count,2)

    def test_cjk_no_whitespace_sentence_boundaries(self):
        prompt=RECORDS[16]['prompt']
        answer='MAESTRO在这台电脑上本地运行。它根据授权协助处理问题。'
        self.assertEqual(evaluate_shape(prompt,answer).status,'PASS_SHAPE')

    def test_good_arabic_with_brand_and_two_sentences_not_falsely_rejected(self):
        prompt=RECORDS[6]['prompt']
        answer='MAESTRO مساعد ذكاء اصطناعي يعمل محليًا على هذا الحاسوب. يساعد المستخدم في المهام المسموح بها.'
        self.assertTrue(evaluate_response(prompt,answer).accepted)

    def test_good_croatian_not_falsely_rejected(self):
        prompt=RECORDS[12]['prompt']
        answer='MAESTRO je lokalni AI pomoćnik na ovom računalu. Pomaže pri pitanjima u okviru dopuštenih mogućnosti.'
        self.assertTrue(evaluate_response(prompt,answer).accepted)

    def test_language_agnostic_unconstrained_reply_unchanged(self):
        self.assertIsNone(obvious_contract_failure('Calcule 17 + 25.', '42'))
        self.assertTrue(evaluate_response('Calcule 17 + 25.', '42').accepted)

    def test_ambiguous_language_in_prompt_abstains(self):
        x=parse_mission_contract('Introduce MAESTRO in English and French, exactly two sentences.')
        self.assertIsNone(x.language)

    def test_unknown_language_not_marked_certified(self):
        x=parse_mission_contract('Skriv två meningar på svenska om MAESTRO.')
        self.assertIsNone(x.language)
        self.assertIsNone(x.sentence_count)

    def test_quoted_code_not_treated_as_contract(self):
        x=parse_mission_contract('Analyse ce code:\n```\nprint("English two sentences")\n```')
        self.assertIsNone(x.sentence_count)
        self.assertFalse(x.identity_request)

    def test_no_provider_specific_selection_or_canned_answer(self):
        import maestro_gateway.multilingual_mission_contract as m
        text=Path(m.__file__).read_text(encoding='utf-8').casefold()
        for bad in ('qwen2.5', 'llama3.2', 'ollama::', 'model.generate(', 'subprocess.', 'http://', 'https://'):
            self.assertNotIn(bad,text)

if __name__ == '__main__':unittest.main()
