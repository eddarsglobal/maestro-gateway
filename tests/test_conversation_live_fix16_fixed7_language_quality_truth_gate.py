"""FIX16-FIXED7: replay real multilingual false PASS cases without inventing certification."""
from pathlib import Path
import unittest
from maestro_gateway.language_quality_governor import (
    assess_language_quality, verified_surface_violation, SessionLanguageEvidence)
from maestro_gateway.native_linguistic_revision import validate_structured_identity_slot


class Fix16Fixed7LanguageQualityTruthGate(unittest.TestCase):
    def test_replay_real_qwen_arabic_identity_slot(self):
        s='MAESTRO هو مساعد ذكاء اصطناعي مخصص لتشغيل محليًا على حاسوب المستخدم.'
        self.assertEqual(validate_structured_identity_slot(s,'ar',1)[1], 'linguistic_unnatural_purpose')
    def test_replay_real_qwen_arabic_role(self):
        s='MAESTRO يوفر خدمة ممتازة لتوفير الأسئلة والمعلومات والمهام المطلوبة ضمن الصلاحيات التي تمتلكها، مما يعزز منفعة وروعة استخدامه.'
        ok,r,_=validate_structured_identity_slot(s,'ar',2)
        self.assertFalse(ok)
        self.assertIn(r,{'linguistic_question_provision','linguistic_unfounded_superlative'})
    def test_replay_real_llama_arabic_repetitive_answer(self):
        s='يساعد في تقديم معلومات واضحة وموثوقة حول الموضوعات المختلفة، وتقديمها بنجاح وتحسينها باستمرار، وتقديمها بشكل محدد ووضعي، وتقديمها بنجاح وتحسينها باستمرار.'
        self.assertEqual(verified_surface_violation(s,'ar'),'linguistic_repeated_phrase')
    def test_replay_real_llama_false_prior_memory(self):
        s='كما ذكرت سائرتنا السابقة، يعتبر MAESTRO مساعد ذكاء اصطناعي محلي على الحاسوب.'
        self.assertEqual(verified_surface_violation(s,'ar'),'linguistic_hallucinated_prior_context')
    def test_replay_real_llama_arabic_meta(self):
        s='هذا هو الجملة الخبرية العربية الفصحى: يعمل MAESTRO على حاسوب المستخدم.'
        self.assertEqual(verified_surface_violation(s,'ar'),'linguistic_meta_explanatory')
    def test_replay_real_llama_unsourced_ai_control(self):
        s='يعمل MAESTRO على إدارة المهام والتحكم في الذكاء الاصطناعي.'
        self.assertEqual(verified_surface_violation(s,'ar'),'linguistic_unsupported_ai_control')
    def test_replay_real_llama_german_inverted_agent(self):
        s='Wenn du Fragen, Informationen oder Aufgaben hast, die du im Rahmen meiner vorhandenen und autorisierten Fähigkeiten unterstützen kannst, stehe ich dir zur Verfügung.'
        ok,reason,_=validate_structured_identity_slot(s,'de',2)
        self.assertFalse(ok)
        self.assertEqual(reason,'linguistic_reversed_agent')
    def test_previous_real_german_refusal_remains_denied(self):
        s='Ich kann nicht dabei helfen, einen Satz über MAESTRO als KI-Assistent zu formulieren.'
        self.assertFalse(validate_structured_identity_slot(s,'de',1)[0])
    def test_positive_arabic_identity_not_automatically_certified(self):
        s='MAESTRO مساعد ذكاء اصطناعي محلي يعمل على حاسوب المستخدم.'
        finding=assess_language_quality(s,'ar')
        self.assertEqual(finding.status,'unverified')
        self.assertFalse(finding.certified)
        self.assertTrue(validate_structured_identity_slot(s,'ar',1)[0])
    def test_positive_arabic_role_not_automatically_certified(self):
        s='يساعد المستخدم في الإجابة عن الأسئلة وتنظيم المعلومات ضمن الصلاحيات المتاحة.'
        self.assertTrue(validate_structured_identity_slot(s,'ar',2)[0])
        self.assertEqual(assess_language_quality(s,'ar').status,'unverified')
    def test_positive_german_role_still_valid(self):
        s='Du kannst mit MAESTRO Fragen beantworten und Aufgaben bearbeiten.'
        self.assertTrue(validate_structured_identity_slot(s,'de',2)[0])
        self.assertFalse(assess_language_quality(s,'de').certified)
    def test_known_defect_status_exposes_reason(self):
        r=assess_language_quality('MAESTRO يوفر خدمة ممتازة للمستخدم.', 'ar')
        self.assertEqual(r.status,'known_defect')
        self.assertEqual(r.reason,'linguistic_unfounded_superlative')
        self.assertFalse(r.certified)
    def test_other_languages_not_claimed_certified(self):
        for lang in ('fr','it','pt','es','hr','en'):
            self.assertFalse(assess_language_quality('Bonjour',lang).certified)
    def test_empty_or_missing_not_assessed(self):
        self.assertEqual(assess_language_quality('','ar').status,'not_assessed')
        self.assertEqual(assess_language_quality('MAESTRO',None).status,'not_assessed')
    def test_per_language_per_digest_isolation(self):
        e=SessionLanguageEvidence()
        for _ in range(2):
            self.assertTrue(e.observe('provider::a','ar','a'*64,accepted=False,reason='linguistic_unnatural_purpose'))
        self.assertTrue(e.should_trial_alternative('provider::a','ar','a'*64))
        self.assertFalse(e.should_trial_alternative('provider::a','de','a'*64))
        self.assertFalse(e.should_trial_alternative('provider::a','ar','b'*64))
        self.assertFalse(e.should_trial_alternative('provider::b','ar','a'*64))
        self.assertFalse(e.snapshot()['fluency_certified'])
    def test_success_does_not_mean_fluent(self):
        e=SessionLanguageEvidence()
        self.assertTrue(e.observe('provider::a','ar','a'*64,accepted=True,reason='accepted'))
        self.assertEqual(e.evidence('provider::a','ar','a'*64).completed,1)
        self.assertFalse(e.snapshot()['fluency_certified'])
    def test_failed_transport_not_grammar_evidence(self):
        e=SessionLanguageEvidence()
        for reason in ('timeout','transport_failure','cancellation'):
            self.assertFalse(e.observe('provider::a','ar','a'*64,accepted=False,reason=reason))
        self.assertFalse(e.should_trial_alternative('provider::a','ar','a'*64))
    def test_runtime_metadata_separates_technical_and_linguistic_truth(self):
        code=(Path(__file__).parents[1]/'src/maestro_gateway/mission.py').read_text()
        self.assertIn("metrics['technical_contract_status']",code)
        self.assertIn("metrics['language_quality_status']",code)
        self.assertIn("metrics['language_quality_certified'] = False",code)
        self.assertIn('assess_language_quality(output, route_language)',code)
    def test_composed_result_screened_again_for_cross_slot_repetition(self):
        code=(Path(__file__).parents[1]/'src/maestro_gateway/mission.py').read_text()
        self.assertIn('whole_defect = verified_surface_violation(candidate, route_language)',code)
        self.assertIn('raise QualityContractFailure(whole_defect, route_language)',code)
    def test_slot_uses_negative_guard_at_runtime(self):
        code=(Path(__file__).parents[1]/'src/maestro_gateway/native_linguistic_revision.py').read_text()
        self.assertIn('verified_surface_violation(sentence, lang)',code)
    def test_neutral_local_primary_guards_unchanged(self):
        code=(Path(__file__).parents[1]/'src/maestro_gateway/primary_intelligence.py').read_text()
        self.assertIn("getattr(d, 'local', None) is not True",code)
        self.assertIn('self._primary != resource_id or self._epoch != expected_epoch',code)
        self.assertNotIn('download_model(',code)
    def test_no_full_user_answers_hardcoded_in_runtime(self):
        code=(Path(__file__).parents[1]/'src/maestro_gateway/language_quality_governor.py').read_text()
        self.assertNotIn('MAESTRO هو مساعد ذكاء اصطناعي',code)
        self.assertNotIn('MAESTRO ist ein lokaler KI-Assistent',code)
        self.assertNotIn('qwen2.5',code.casefold())
        self.assertNotIn('llama3.2',code.casefold())
