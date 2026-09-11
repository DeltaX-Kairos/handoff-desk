import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from live_agent import CallBudget, Limits, build_live_agent, main
from strands import Agent, tool
from strands.models import Model


class OfflineModel(Model):
    def __init__(self):
        self.calls = 0
        self.config = {'context_window_limit': 10000}

    def update_config(self, **kwargs):
        self.config.update(kwargs)

    def get_config(self):
        return self.config

    async def structured_output(self, *args, **kwargs):
        raise AssertionError('Structured output is not used')
        yield

    async def stream(self, *args, **kwargs):
        self.calls += 1
        yield {'messageStart': {'role': 'assistant'}}
        yield {'contentBlockDelta': {'contentBlockIndex': 0, 'delta': {'text': 'Offline response'}}}
        yield {'contentBlockStop': {'contentBlockIndex': 0}}
        yield {'messageStop': {'stopReason': 'end_turn'}}
        yield {'metadata': {'usage': {'inputTokens': 10, 'outputTokens': 2, 'totalTokens': 12}, 'metrics': {'latencyMs': 1}}}


class OfflineToolModel(OfflineModel):
    """A real Strands model protocol, with no remote provider or token endpoint."""
    async def stream(self, *args, **kwargs):
        self.calls += 1
        yield {'messageStart': {'role': 'assistant'}}
        if self.calls == 1:
            yield {'contentBlockStart': {'contentBlockIndex': 0,
                'start': {'toolUse': {'toolUseId': 'fixture-1', 'name': 'fixture_check'}}}}
            yield {'contentBlockDelta': {'contentBlockIndex': 0,
                'delta': {'toolUse': {'input': '{}'}}}}
            stop = 'tool_use'
        else:
            yield {'contentBlockDelta': {'contentBlockIndex': 0,
                'delta': {'text': 'The synthetic fixture check completed.'}}}
            stop = 'end_turn'
        yield {'contentBlockStop': {'contentBlockIndex': 0}}
        yield {'messageStop': {'stopReason': stop}}
        yield {'metadata': {'usage': {'inputTokens': 20, 'outputTokens': 10, 'totalTokens': 30}, 'metrics': {'latencyMs': 1}}}


class LiveAgentTests(unittest.TestCase):
    def test_human_pause_renews_deadline_but_not_call_quota(self):
        clock = Mock(return_value=0)
        budget = CallBudget(Limits(model_calls=2, session_seconds=5), clock)
        def attempt():
            event = SimpleNamespace(projected_input_tokens=20, cancel=False)
            budget.before_model(event)
            return event.cancel
        budget.begin_turn()
        self.assertFalse(attempt())
        clock.return_value = 6
        self.assertIn('deadline', attempt())
        self.assertEqual(budget.calls, 1)
        clock.return_value = 600  # Human took ten minutes to clarify.
        budget.begin_turn()
        self.assertFalse(attempt())
        self.assertEqual(budget.calls, 2)
        clock.return_value = 1200
        budget.begin_turn()
        self.assertIn('model-call limit', attempt())
        self.assertEqual(budget.calls, 2)

    def test_mantle_explicit_aws_key_endpoint_and_limits(self):
        session = Mock()
        with patch('strands.models.openai.OpenAIModel') as provider, patch('live_agent.Agent') as agent_type:
            agent, budget = build_live_agent(session, 'openai.gpt-oss-20b', 'us-east-2',
                environ={'AWS_BEARER_TOKEN_BEDROCK': 'synthetic-test-token', 'OPENAI_API_KEY': 'do-not-use'},
                transport='bedrock-mantle')
            options = provider.call_args.kwargs
            client = options['client_args']
            self.assertEqual(client['base_url'], 'https://bedrock-mantle.us-east-2.api.aws/v1')
            self.assertEqual(client['api_key'], 'synthetic-test-token')
            self.assertEqual(client['max_retries'], 0)
            self.assertEqual((client['timeout'].read, client['timeout'].connect), (30, 5))
            self.assertEqual(options['params'], {'max_completion_tokens': 1024})
            self.assertFalse(options['stream'])
            self.assertNotIn('bedrock_mantle_config', options)
            self.assertEqual(agent_type.call_args.kwargs['hooks'], [budget])
            self.assertIsNone(agent_type.call_args.kwargs['retry_strategy'])
            agent.assert_not_called()

    def test_mantle_model_formats_bounded_tool_request_without_client(self):
        from live_agent import _mantle_model
        with patch('openai.AsyncOpenAI', side_effect=AssertionError('No remote client')):
            model = _mantle_model('openai.gpt-oss-20b', 'us-east-2', 'synthetic-test-token', Limits())
            request = model.format_request([{'role': 'user', 'content': [{'text': 'Inspect the fixture'}]}],
                [{'name': 'fixture_check', 'description': 'Check local fixture',
                  'inputSchema': {'json': {'type': 'object', 'properties': {}}}}], 'Local fixture test only')
            self.assertEqual(request['max_completion_tokens'], 1024)
            self.assertFalse(request['stream'])
            self.assertEqual(request['tools'][0]['function']['name'], 'fixture_check')
            self.assertNotIn('api_key', request)

    def test_mantle_dry_run_and_region_injection_rejected(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch('live_agent._mantle_model') as provider:
            main(['--model', 'openai.gpt-oss-20b', '--region', 'us-east-2', '--transport', 'bedrock-mantle'])
            provider.assert_not_called()
            with self.assertRaises(ValueError):
                build_live_agent(Mock(), 'model', 'attacker.example/path', transport='bedrock-mantle',
                    environ={'AWS_BEARER_TOKEN_BEDROCK': 'synthetic-test-token'})
        self.assertIn('bedrock-mantle', output.getvalue())

    def test_real_sdk_tool_loop_has_estimates_and_cancels_third_inference(self):
        observed = []
        checked = []

        class RecordingBudget(CallBudget):
            def before_model(self, event):
                observed.append(event.projected_input_tokens)
                super().before_model(event)

        @tool
        def fixture_check() -> dict:
            """Check the original synthetic fixture locally."""
            checked.append(True)
            return {'rows': 3, 'source': 'synthetic fixture'}

        model = OfflineToolModel()
        model.update_config(use_native_token_count=False)
        budget = RecordingBudget(Limits(model_calls=2))
        agent = Agent(model=model, tools=[fixture_check], hooks=[budget],
            retry_strategy=None, callback_handler=None, context_manager=False)
        result = agent('Use fixture_check and explain the result.')
        self.assertEqual(checked, [True])
        self.assertEqual(model.calls, 2)
        self.assertEqual(budget.calls, 2)
        self.assertEqual(len(observed), 2)
        self.assertTrue(all(isinstance(value, int) and value > 0 for value in observed))
        self.assertIn('synthetic fixture check completed', str(result))
        try:
            agent('Another request must be cancelled before inference.')
        except Exception:
            pass
        self.assertEqual(model.calls, 2)
        self.assertEqual(len(observed), 3)
        self.assertIsNotNone(observed[-1])

    def test_real_sdk_invocation_cancels_before_fake_model(self):
        model = OfflineModel()
        budget = CallBudget(Limits(model_calls=1))
        agent = Agent(model=model, hooks=[budget], retry_strategy=None,
                      callback_handler=None, context_manager=False)
        agent('First offline test')
        self.assertEqual(model.calls, 1)
        try:
            agent('Second offline test must not reach model')
        except Exception:
            pass  # SDK cancellation may return a stop result or raise.
        self.assertEqual(model.calls, 1)
        self.assertEqual(budget.calls, 1)

    def test_hook_budget_across_calls(self):
        budget = CallBudget(Limits(model_calls=2))
        registry = Mock()
        budget.register_hooks(registry)
        event_class, callback = registry.add_callback.call_args.args
        self.assertEqual(event_class.__name__, 'BeforeModelCallEvent')
        for expected in (False, False, 'Session model-call limit reached'):
            event = SimpleNamespace(projected_input_tokens=200, cancel=False)
            callback(event)
            self.assertEqual(event.cancel, expected)
        self.assertEqual(budget.calls, 2)

    def test_hook_estimate_and_elapsed_limits(self):
        clock = Mock(return_value=10)
        budget = CallBudget(Limits(projected_input_tokens=500, session_seconds=5), clock)
        for estimate in (None, 501):
            event = SimpleNamespace(projected_input_tokens=estimate, cancel=False)
            budget.before_model(event)
            self.assertTrue(event.cancel)
        self.assertEqual(budget.calls, 0)
        clock.return_value = 15
        event = SimpleNamespace(projected_input_tokens=20, cancel=False)
        budget.before_model(event)
        self.assertIn('deadline', event.cancel)
        self.assertEqual(budget.calls, 0)

    def test_builder_config_and_no_inference(self):
        session = Mock()
        session.tools.return_value = ['fixture tool']
        with patch('live_agent.BedrockModel') as model_type, patch('live_agent.Agent') as agent_type:
            agent, budget = build_live_agent(session, 'explicit-model', 'us-east-2',
                environ={'AWS_BEARER_TOKEN_BEDROCK': 'synthetic-test-token'})
            options = model_type.call_args.kwargs
            self.assertEqual(options['model_id'], 'explicit-model')
            self.assertEqual(options['region_name'], 'us-east-2')
            self.assertEqual(options['max_tokens'], 1024)
            self.assertFalse(options['streaming'])
            self.assertFalse(options['use_native_token_count'])
            config = options['boto_client_config']
            self.assertEqual(config.retries['total_max_attempts'], 1)
            self.assertEqual((config.connect_timeout, config.read_timeout), (5, 30))
            self.assertIsNone(agent_type.call_args.kwargs['retry_strategy'])
            self.assertEqual(agent_type.call_args.kwargs['hooks'], [budget])
            agent.assert_not_called()
            model_type.return_value.stream.assert_not_called()

    def test_dry_run_never_constructs_provider_or_reads_key(self):
        with patch('live_agent.BedrockModel', side_effect=AssertionError('No provider')), \
             patch('live_agent.os.environ', {}):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(['--model', 'explicit-model', '--region', 'us-east-2'])
            self.assertEqual(code, 0)
            self.assertIn('offline dry run', output.getvalue())
            self.assertIn('not a dollar', output.getvalue())

    def test_missing_key_refuses_before_provider(self):
        with patch('live_agent.BedrockModel') as provider:
            with self.assertRaises(ValueError):
                build_live_agent(Mock(), 'model', 'region', environ={})
            provider.assert_not_called()

    def test_invalid_limits(self):
        for value in (0, -1, True, 2.5):
            with self.assertRaises(ValueError):
                Limits(model_calls=value)


if __name__ == '__main__':
    unittest.main()
