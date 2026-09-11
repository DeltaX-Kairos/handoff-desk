"""Lazy bounded agent integration. Merely constructing this object costs nothing."""
from live_agent import build_live_agent


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
            raise ValueError('Prompt must contain 1–4000 characters')
        if self._build_failed:
            raise ValueError('Investigation unavailable; start a new session after checking configuration')
        try:
            if self._agent is None:
                try:
                    options = {'transport': self.transport} if self.transport != 'bedrock-runtime' else {}
                    if self.admission is not None:
                        options['admission'] = self.admission
                    self._agent, self._budget = self.builder(self.session, self.model_id, self.region, **options)
                except Exception:
                    self._build_failed = True
                    raise
            self._budget.begin_turn()
            response = self._agent(prompt)
            return {'message': str(response), 'attempted_model_calls': self._budget.calls,
                    'review_required': True}
        except Exception:
            # Do not return exception text, stack traces or provider request details.
            # Existing agent/budget is preserved; failures cannot reset call limits.
            raise ValueError('Investigation stopped or unavailable. Check configuration and session limits; no automatic retry.') from None
