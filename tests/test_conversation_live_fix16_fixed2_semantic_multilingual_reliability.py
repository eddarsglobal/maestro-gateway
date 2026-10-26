from __future__ import annotations
import unittest
from pathlib import Path
from maestro_gateway.native_linguistic_revision import (
    validate_structured_identity_slot, maestro_identity_contradicted,
    maestro_identity_grounded, structured_identity_slot_count,
)
import ast
import re
from maestro_gateway.response_constraints import evaluate_shape, explicit_two_sentences

# Evaluate the exact production router regex/function in isolation so this
# package-level test does not require the full Core to be installed on the
# machine validating the archive. The full Mac suite tests real integration.
_router = Path(__file__).parents[1]/'src/maestro_gateway/adaptive_router.py'
_tree = ast.parse(_router.read_text())
_nodes = [n for n in _tree.body
          if (isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
              and n.target.id == '_EXPLICIT_LANGUAGE_REQUESTS')
          or (isinstance(n, ast.FunctionDef) and n.name == 'explicit_requested_language')]
assert len(_nodes) == 2
_ns = {'re': re}
exec(compile(ast.fix_missing_locations(ast.Module(body=_nodes, type_ignores=[])), str(_router), 'exec'), _ns)
explicit_requested_language = _ns['explicit_requested_language']

class Fix16Fixed2SemanticMultilingualReliability(unittest.TestCase):
    def test_ar_negation_not_false_person_identity(self):
        self.assertEqual(validate_structured_identity_slot(
            'MAESTRO ليس شخصًا، بل هو مساعد ذكاء اصطناعي محلي يعمل على هذا الحاسوب.', 'ar', 1)[0], True)

    def test_ar_normal_grammatical_variant(self):
        self.assertTrue(validate_structured_identity_slot(
            'MAESTRO هو مساعد يعمل بالذكاء الاصطناعي محليًا على هذا الحاسوب.', 'ar', 1)[0])

    def test_de_ki_assistenzsystem_synonym(self):
        self.assertTrue(validate_structured_identity_slot(
            'MAESTRO ist ein auf deinem Rechner betriebenes KI-Assistenzsystem.', 'de', 1)[0])

    def test_de_du_kannst_role(self):
        self.assertTrue(validate_structured_identity_slot(
            'Du kannst damit auf deinem Computer Fragen stellen und Aufgaben bearbeiten.', 'de', 2)[0])

    def test_ar_negative_person_still_rejected(self):
        self.assertEqual(validate_structured_identity_slot('MAESTRO ist ein Mensch.', 'de', 1)[0], False)
        for sentence in ('MAESTRO هو شخص على هذا الحاسوب.', 'MAESTRO شركة محلية على هذا الحاسوب.'):
            self.assertEqual(validate_structured_identity_slot(sentence, 'ar', 1)[1], 'maestro_identity_contradicted')

    def test_de_false_identity_still_rejected(self):
        for sentence in ('MAESTRO ist eine Person.', 'MAESTRO ist eine Firma.', 'MAESTRO ist ein Musikprogramm.'):
            self.assertTrue(maestro_identity_contradicted(sentence, 'de'), sentence)

    def test_de_not_a_person_is_not_false_contradiction(self):
        self.assertFalse(maestro_identity_contradicted('MAESTRO ist keine Person, sondern ein lokaler KI-Assistent auf meinem Rechner.', 'de'))

    def test_ar_false_company_and_reversal(self):
        self.assertTrue(maestro_identity_contradicted('MAESTRO ليس مساعدًا، بل شركة.', 'ar'))
        self.assertFalse(maestro_identity_contradicted('MAESTRO ليست شركة بل مساعد ذكاء اصطناعي محلي.', 'ar'))

    def test_ar_foreign_script_still_rejected(self):
        self.assertEqual(validate_structured_identity_slot('MAESTRO هو مساعد AI محلي على الحاسوب.', 'ar', 1)[1],
                         'explicit_shape_arabic_only_foreign_script')

    def test_two_sentences_composed_as_generated_are_grounded(self):
        self.assertTrue(maestro_identity_grounded(
            'MAESTRO ist ein auf deinem Rechner betriebenes KI-Assistenzsystem. Du kannst damit auf deinem Computer Fragen stellen und Aufgaben bearbeiten.', 'de'))
        self.assertTrue(maestro_identity_grounded(
            'MAESTRO هو مساعد يعمل بالذكاء الاصطناعي محليًا على هذا الحاسوب. يساعدك على تنظيم المعلومات ضمن الصلاحيات المتاحة.', 'ar'))

    def test_false_company_and_only_generic_words_still_fail_grounding(self):
        self.assertFalse(validate_structured_identity_slot('MAESTRO ist eine lokale Musikfirma auf deinem Computer.', 'de', 1)[0])
        self.assertFalse(validate_structured_identity_slot('MAESTRO هو مساعد محلي على هذا الحاسوب.', 'ar', 1)[0])
        self.assertFalse(validate_structured_identity_slot('Ein Computer kann Aufgaben erledigen.', 'de', 2)[0])

    def test_one_sentence_and_multi_sentence_shape(self):
        self.assertFalse(validate_structured_identity_slot('MAESTRO ist ein lokaler KI-Assistent. Er hilft dir.', 'de', 1)[0])
        self.assertFalse(validate_structured_identity_slot('MAESTRO هو مساعد محلي. ويجيب عن الأسئلة.', 'ar', 1)[0])

    def test_complete_typo_is_a_german_request(self):
        prompt='ntworte auf Deutsch in genau zwei natürlichen Sätzen. Was ist MAESTRO? Verwende durchgehend du.'
        self.assertEqual(explicit_requested_language(prompt), 'German')
        self.assertEqual(structured_identity_slot_count(prompt, 'de'), 2)
        self.assertTrue(explicit_two_sentences(prompt))

    def test_ui_english_fallback_fails_german_shape(self):
        prompt='ntworte auf Deutsch in genau zwei natürlichen Sätzen. Was ist MAESTRO?'
        english='Maestro is an AI assistant that helps you on your computer. It can perform various tasks and provide information.'
        result=evaluate_shape(prompt, english)
        self.assertEqual((result.status, result.reason), ('FAIL_SHAPE', 'german_only_english_fallback'))

    def test_correct_german_passes_german_fallback_filter(self):
        prompt='ntworte auf Deutsch in genau zwei natürlichen Sätzen. Was ist MAESTRO?'
        german='MAESTRO ist ein lokaler KI-Assistent auf deinem Computer. Du kannst ihm Fragen stellen und mit ihm Aufgaben bearbeiten.'
        self.assertEqual(evaluate_shape(prompt, german).status, 'PASS_SHAPE')

    def test_english_fallback_fails_single_german_slot(self):
        result=validate_structured_identity_slot('MAESTRO is an AI assistant that helps you on your computer.', 'de',1)
        self.assertEqual(result[1], 'explicit_shape_german_only_english_fallback')

    def test_no_monolithic_transcript_or_canned_answer(self):
        root=Path(__file__).parents[1]/'src/maestro_gateway'
        m=(root/'mission.py').read_text()
        assert 'structured_identity_slot_total = structured_identity_slot_count' in m
        self.assertNotIn('transcript = identity_fact_instruction or base_instruction', m)
        self.assertNotIn('identity_core_candidate',m)
        source=(root/'native_linguistic_revision.py').read_text()
        self.assertNotIn('MAESTRO ist dein lokaler KI-Assistent auf diesem Computer. Er unterstützt dich',source)

if __name__ == '__main__': unittest.main()
