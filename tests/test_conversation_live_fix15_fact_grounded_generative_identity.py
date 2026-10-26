from __future__ import annotations
import unittest
from pathlib import Path

from maestro_gateway.native_linguistic_revision import (
    fact_grounded_identity_instruction,
    maestro_identity_fact_capsule,
)

DE_PROMPT=(
 'Antworte auf Deutsch in genau zwei natürlichen und grammatikalisch korrekten Sätzen.\n'
 'Was ist MAESTRO und wozu dient es?\n'
 'MAESTRO ist mein lokaler KI-Assistent auf diesem Computer.\n'
 'Verwende durchgehend die Anrede „du“.')
AR_PROMPT=(
 'أجب بالعربية الفصحى الطبيعية في جملتين فقط.\n'
 'ما هو MAESTRO وما وظيفته؟\n'
 'MAESTRO هو مساعد الذكاء الاصطناعي المحلي الخاص بي على هذا الحاسوب.\n'
 'لا تستخدم كلمات إنجليزية أو أحرفًا غير عربية باستثناء اسم MAESTRO.')

class Fix15FactGroundedGenerativeIdentityTests(unittest.TestCase):
    def test_fact_capsules_are_constraints_not_full_answers(self):
        de=maestro_identity_fact_capsule('de'); ar=maestro_identity_fact_capsule('ar')
        self.assertIn('Identität lokaler KI-Assistent auf diesem Computer',de)
        self.assertIn('الهوية مساعد ذكاء اصطناعي محلي على هذا الحاسوب',ar)
        self.assertNotIn('MAESTRO ist dein lokaler KI-Assistent auf diesem Computer.',de)
        self.assertNotIn('MAESTRO هو مساعد ذكاء اصطناعي محلي يعمل على هذا الحاسوب.',ar)

    def test_generation_instructions_explicitly_require_free_formulation(self):
        de=fact_grounded_identity_instruction(DE_PROMPT,'de') or ''
        ar=fact_grounded_identity_instruction(AR_PROMPT,'ar') or ''
        self.assertIn('Formuliere selbstständig',de)
        self.assertIn('kopiere keinen vorgegebenen Mustersatz',de)
        self.assertIn('صغ بنفسك',ar)
        self.assertIn('لا تنسخ صياغة جاهزة',ar)

    def test_no_canonical_user_facing_answer_is_hardcoded(self):
        root=Path(__file__).parents[1]/'src/maestro_gateway'
        joined='\n'.join((root/name).read_text(encoding='utf-8') for name in ['mission.py','native_linguistic_revision.py','response_effort.py'])
        self.assertNotIn('MAESTRO ist dein lokaler KI-Assistent auf diesem Computer. Er unterstützt dich',joined)
        self.assertNotIn('MAESTRO هو مساعد ذكاء اصطناعي محلي يعمل على هذا الحاسوب. يساعدك',joined)

    def test_identity_lane_reaches_adapter_instead_of_deterministic_core(self):
        src=(Path(__file__).parents[1]/'src/maestro_gateway/mission.py').read_text(encoding='utf-8')
        self.assertIn('identity_fact_instruction = fact_grounded_identity_instruction',src)
        self.assertIn('structured_identity_slot_total = structured_identity_slot_count',src)
        self.assertIn('adapter = registry.adapter(resource_id)',src)
        self.assertNotIn('identity_core_candidate',src)

    def test_metrics_distinguish_fact_grounding_from_projection(self):
        src=(Path(__file__).parents[1]/'src/maestro_gateway/mission.py').read_text(encoding='utf-8')
        self.assertIn('"governed_identity_projection": False',src)
        self.assertIn('identity_fact_instruction or structured_identity_slot_total',src)

    def test_provider_neutral(self):
        src=(Path(__file__).parents[1]/'src/maestro_gateway/native_linguistic_revision.py').read_text(encoding='utf-8').casefold()
        for brand in ('llama3.2','gemma','qwen','ollama::'):
            self.assertNotIn(brand,src)

if __name__=='__main__': unittest.main()
