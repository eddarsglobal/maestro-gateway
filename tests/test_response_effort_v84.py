"""V8.4 response effort and bounded generative latency guardrails."""
import unittest
from maestro_gateway.response_effort import bounded_response_tokens, local_execution_profile


class V84ResponseEffortTests(unittest.TestCase):
    def test_fast_first_every_task_class(self):
        for task in ('quick','normal','complex','code'):
            with self.subTest(task=task):
                profile = local_execution_profile(task)
                self.assertIs(profile.thinking, False)
                self.assertEqual(profile.profile_ref, 'gateway-v8.4-efficient-first')

    def test_deep_reasoning_is_bounded_to_existing_quality_retry(self):
        for task in ('complex','code'):
            profile = local_execution_profile(task, quality_retry=1)
            self.assertIs(profile.thinking, True)
            self.assertEqual(profile.profile_ref, 'gateway-v8.4-quality-retry')
        for task in ('quick','normal'):
            self.assertIs(local_execution_profile(task, quality_retry=1).thinking, False)

    def test_manual_override_preserves_prior_execution_policy(self):
        self.assertIsNone(local_execution_profile('code', manual_override=True))

    def test_normal_token_budget_is_limited_but_not_too_short(self):
        self.assertEqual(bounded_response_tokens('normal','Comment fonctionne un serveur?',320),320)
        self.assertEqual(bounded_response_tokens('normal','Explique en détail le protocole',320),320)
        self.assertEqual(bounded_response_tokens('normal','Explain in one sentence.',320),112)
        self.assertEqual(bounded_response_tokens('normal','Explique en une phrase.',80),80)

    def test_no_complex_code_truncation(self):
        self.assertEqual(bounded_response_tokens('complex','Analyse cette architecture',1024),1024)
        self.assertEqual(bounded_response_tokens('code','Écris un programme',2048),2048)

    def test_quick_cap_and_unknown_task_unchanged(self):
        self.assertEqual(bounded_response_tokens('quick','Bonjour',320),128)
        self.assertEqual(bounded_response_tokens('unknown','...',320),320)

    def test_profile_is_core_execution_contract(self):
        from maestro.sovereign import IntelligenceRequest, IntelligenceCapability
        req = IntelligenceRequest(
            task_id='test', capability=IntelligenceCapability.DIRECT,
            instruction='bonjour', max_tokens=128,
            execution_profile=local_execution_profile('quick'),
        )
        self.assertIs(req.execution_profile.thinking, False)

class V84ActualProviderContractTests(unittest.TestCase):
    def test_execution_profile_forces_no_think_when_model_tags_do_not_advertise_thinking(self):
        from maestro.providers.ollama.models import OllamaModelInfo
        from maestro.providers.ollama.sovereign_adapter import OllamaSovereignResourceAdapter
        from maestro.sovereign import IntelligenceRequest, IntelligenceCapability
        from maestro.autonomous.provider_adapters import ProviderResponse

        class DummyProvider:
            def __init__(self):
                self.request = None
            def generate(self, request):
                self.request = request
                return ProviderResponse(provider='ollama', model_id=request.model_id,
                                        output='A real model would answer here.', metadata={})

        provider = DummyProvider()
        model = OllamaModelInfo(name='provider-neutral-local', size=1000, digest='test',
                                capabilities=('completion',))
        adapter = OllamaSovereignResourceAdapter(model, provider)
        result = adapter.execute(IntelligenceRequest(
            task_id='test', capability=IntelligenceCapability.DIRECT,
            instruction='test', max_tokens=128,
            execution_profile=local_execution_profile('complex')))
        self.assertEqual(result.output, 'A real model would answer here.')
        self.assertIs(provider.request.context.get('think'), False)
        self.assertEqual(provider.request.context['options']['num_predict'],128)

    def test_quality_retry_can_raise_effort_without_changing_resource(self):
        from maestro.providers.ollama.models import OllamaModelInfo
        from maestro.providers.ollama.sovereign_adapter import OllamaSovereignResourceAdapter
        from maestro.sovereign import IntelligenceRequest, IntelligenceCapability
        from maestro.autonomous.provider_adapters import ProviderResponse

        class DummyProvider:
            def __init__(self):
                self.calls=[]
            def generate(self, request):
                self.calls.append(request.context.get('think'))
                return ProviderResponse(provider='ollama', model_id=request.model_id,
                                        output='answer', metadata={})

        provider = DummyProvider()
        adapter = OllamaSovereignResourceAdapter(
            OllamaModelInfo(name='a-local-model', size=1000, digest='test', capabilities=('completion',)),
            provider)
        for i in (0,1):
            adapter.execute(IntelligenceRequest(
                task_id='test', capability=IntelligenceCapability.DIRECT,
                instruction='test', max_tokens=128,
                execution_profile=local_execution_profile('code',quality_retry=i)))
        self.assertEqual(provider.calls,[False,True])
