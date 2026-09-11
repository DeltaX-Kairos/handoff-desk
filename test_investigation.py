from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from investigation import Investigation, InvestigationError
from live_agent import CallBudget, Limits


class InvestigationTests(unittest.TestCase):
    def test_long_clarification_pause_keeps_shared_call_limit(self):
        clock = Mock(return_value=0)
        budget = CallBudget(Limits(model_calls=2, session_seconds=5), clock)
        def model(prompt):
            event = SimpleNamespace(projected_input_tokens=20, cancel=False)
            budget.before_model(event)
            if event.cancel:
                raise ValueError(event.cancel)
            return 'Clarification needed'
        builder = Mock(return_value=(model, budget))
        investigation = Investigation(Mock(), 'model', 'region', builder)
        self.assertEqual(investigation.investigate('Check files')['attempted_model_calls'], 1)
        clock.return_value = 900
        self.assertEqual(investigation.investigate('Check again after clarification')['attempted_model_calls'], 2)
        clock.return_value = 1800
        with self.assertRaises(ValueError):
            investigation.investigate('Cannot renew exhausted quota')
        self.assertEqual(budget.calls, 2)
        self.assertEqual(builder.call_count, 1)

    def test_lazy_build_reused_and_response_shape(self):
        session = Mock()
        budget = SimpleNamespace(calls=0, begin_turn=Mock())
        def respond(prompt):
            budget.calls += 1
            return 'Observed synthetic headers; clarification remains.'
        agent = Mock(side_effect=respond)
        builder = Mock(return_value=(agent, budget))
        investigation = Investigation(session, 'explicit-model', 'us-east-2', builder)
        builder.assert_not_called()
        for count in (1, 2):
            response = investigation.investigate('Check selected files')
            self.assertEqual(response, {'message': 'Observed synthetic headers; clarification remains.',
                'attempted_model_calls': count, 'review_required': True})
            self.assertNotIn('accepted', response)
            self.assertNotIn('delivered', response)
        builder.assert_called_once_with(session, 'explicit-model', 'us-east-2')
        self.assertEqual(budget.begin_turn.call_count, 2)
        session.choose.assert_not_called()
        session.confirm_definitions.assert_not_called()

    def test_invalid_prompt_before_build(self):
        builder = Mock()
        investigation = Investigation(Mock(), 'model', 'region', builder)
        for prompt in ('', '   ', None, 9, {}, 'a' * 4001):
            with self.subTest(prompt=str(prompt)[:10]):
                with self.assertRaises(ValueError):
                    investigation.investigate(prompt)
        builder.assert_not_called()

    def test_provider_error_sanitized_budget_preserved(self):
        budget = SimpleNamespace(calls=0, begin_turn=Mock())
        def fail(prompt):
            budget.calls += 1
            raise RuntimeError('secret-provider-auth-value')
        agent = Mock(side_effect=fail)
        builder = Mock(return_value=(agent, budget))
        investigation = Investigation(Mock(), 'model', 'region', builder)
        for _ in range(2):
            with self.assertRaises(ValueError) as caught:
                investigation.investigate('Check files')
            self.assertNotIn('secret-provider-auth-value', str(caught.exception))
            self.assertEqual(caught.exception.code, 'service_unavailable')
            self.assertTrue(caught.exception.__suppress_context__)
        self.assertEqual(budget.calls, 2)
        self.assertEqual(builder.call_count, 1)

    def test_known_budget_stops_survive_sdk_raise_or_return(self):
        cases = [
            ('model_call_limit', {'calls':6}, 20, None),
            ('turn_deadline', {}, 20, 181),
            ('input_estimate_unavailable', {}, None, None),
            ('input_limit', {}, 8001, None),
            ('aggregate_allowance', {}, 20, None),
        ]
        for code, attributes, estimate, elapsed in cases:
            for raises in (False, True):
                with self.subTest(code=code,raises=raises):
                    clock=Mock(return_value=0)
                    budget=CallBudget(Limits(),clock,admission=(lambda:False) if code=='aggregate_allowance' else None)
                    for key,value in attributes.items():setattr(budget,key,value)
                    def run(prompt):
                        if elapsed is not None:clock.return_value=elapsed
                        event=SimpleNamespace(projected_input_tokens=estimate,cancel=None)
                        budget.before_model(event)
                        self.assertTrue(event.cancel)
                        if raises:raise RuntimeError('secret-provider-auth-value')
                        return 'secret-provider-cancellation-result'
                    investigation=Investigation(Mock(),'model','region',Mock(return_value=(run,budget)))
                    with self.assertRaises(InvestigationError) as caught:
                        investigation.investigate('Check files')
                    self.assertEqual(caught.exception.code,code)
                    self.assertEqual(str(caught.exception),InvestigationError.MESSAGES[code])
                    self.assertNotIn('secret',str(caught.exception))

    def test_new_turn_does_not_mislabel_provider_failure_with_old_stop(self):
        budget=CallBudget(Limits())
        budget.stop_code='input_limit'
        agent=Mock(side_effect=RuntimeError('secret-provider-auth-value'))
        investigation=Investigation(Mock(),'model','region',Mock(return_value=(agent,budget)))
        with self.assertRaises(InvestigationError) as caught:
            investigation.investigate('Check files')
        self.assertEqual(caught.exception.code,'service_unavailable')
        self.assertIsNone(budget.stop_code)

    def test_failed_initialization_does_not_automatically_retry(self):
        builder = Mock(side_effect=RuntimeError('secret-provider-auth-value'))
        investigation = Investigation(Mock(), 'model', 'region', builder)
        for _ in range(2):
            with self.assertRaises(ValueError) as caught:
                investigation.investigate('Check files')
            self.assertNotIn('secret-provider-auth-value', str(caught.exception))
        self.assertEqual(builder.call_count, 1)


if __name__ == '__main__':
    unittest.main()
