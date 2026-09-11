"""Lazy bounded agent integration. Merely constructing this object costs nothing."""
from live_agent import build_live_agent


class InvestigationError(ValueError):
    """Public error assembled solely from fixed messages, never exception text."""
    MESSAGES = {
        'model_call_limit': 'This project has reached its AI call allowance. Continue with the delivery checks and human choices; no automatic retry was made.',
        'turn_deadline': 'AI investigation stopped at the turn time limit before another model call. Try a narrower question or continue with the delivery checks.',
        'input_estimate_unavailable': 'AI investigation stopped because its input size could not be estimated. Continue with the delivery checks; no automatic retry was made.',
        'input_limit': 'AI investigation stopped because the estimated input exceeded its limit. Use fewer or smaller files in a new project, or continue with the delivery checks.',
        'aggregate_allowance': 'The shared AI allowance is exhausted or unavailable. Continue with the delivery checks; the operator must check the allowance before further AI use.',
        'configuration_unavailable': 'AI investigation could not initialize. The operator must check the model configuration and access. Delivery checks remain available; no automatic retry was made.',
        'service_unavailable': 'AI investigation could not complete. The model service or investigation may be unavailable. Delivery checks remain available; no automatic retry was made.',
        'invalid_prompt': 'Enter a question containing 1–4000 characters.',
    }

    def __init__(self, code):
        self.code = code if isinstance(code, str) and code in self.MESSAGES else 'service_unavailable'
        super().__init__(self.MESSAGES[self.code])


class Investigation:
    def __init__(self, session, model_id, region, builder=build_live_agent, transport='bedrock-runtime', admission=None):
        if not isinstance(model_id, str) or not model_id.strip() or not isinstance(region, str) or not region.strip():
            raise ValueError('Explicit model and region required')
        self.admission = admission
        self.session = session
        self.model_id = model_id
        self.region = region
        self.builder = builder
        self.transport = transport
        self._agent = None
        self._budget = None
        self._build_failed = False

    def investigate(self, prompt):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
            raise InvestigationError('invalid_prompt')
        if self._build_failed:
            raise InvestigationError('configuration_unavailable')
        try:
            if self._agent is None:
                try:
                    options = {'transport': self.transport} if self.transport != 'bedrock-runtime' else {}
                    if self.admission is not None:
                        options['admission'] = self.admission
                    self._agent, self._budget = self.builder(self.session, self.model_id, self.region, **options)
                except Exception:
                    self._build_failed = True
                    raise InvestigationError('configuration_unavailable') from None
            self._budget.begin_turn()
            response = self._agent(prompt)
            # SDK cancellation may return a result instead of raising. Do not
            # present an explicitly stopped run as a completed investigation.
            stop_code = getattr(self._budget, 'stop_code', None)
            if stop_code:
                raise InvestigationError(stop_code)
            return {'message': str(response), 'attempted_model_calls': self._budget.calls,
                    'review_required': True}
        except InvestigationError:
            raise
        except Exception:
            # Do not return exception text, stack traces or provider request details.
            # Existing agent/budget is preserved; failures cannot reset call limits.
            raise InvestigationError(getattr(self._budget, 'stop_code', None)) from None
