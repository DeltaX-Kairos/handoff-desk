"""Opt-in Bedrock CLI for synthetic data only. Default is an offline dry run."""
import argparse
from dataclasses import dataclass
import json
import logging
import os
import re
import tempfile
import threading
import time

from botocore.config import Config
from strands import Agent
from strands.hooks import BeforeModelCallEvent
from strands.models import BedrockModel
from agent import SYSTEM


@dataclass(frozen=True)
class Limits:
    model_calls: int = 6
    output_tokens: int = 1024
    projected_input_tokens: int = 8000
    connect_seconds: int = 5
    read_seconds: int = 30
    session_seconds: int = 180

    def __post_init__(self):
        for value in self.__dict__.values():
            if type(value) is not int or value <= 0:
                raise ValueError('Limits must be positive integers')


class CallBudget:
    """Counts attempted model calls across turns; cannot be reset by agent tools."""
    def __init__(self, limits, clock=time.monotonic, admission=None):
        self.limits = limits
        self.admission = admission
        self.clock = clock
        self.started = clock()
        self.calls = 0
        self.lock = threading.Lock()

    def register_hooks(self, registry, **kwargs):
        registry.add_callback(BeforeModelCallEvent, self.before_model)

    def begin_turn(self):
        """Human-request boundary only: renew deadline without renewing call quota."""
        with self.lock:
            self.started = self.clock()

    def before_model(self, event):
        with self.lock:
            if self.calls >= self.limits.model_calls:
                event.cancel = 'Session model-call limit reached'
            elif self.clock() - self.started >= self.limits.session_seconds:
                event.cancel = 'Turn deadline reached before model call'
            elif event.projected_input_tokens is None:
                event.cancel = 'Input estimate unavailable; refusing unbounded call'
            elif event.projected_input_tokens > self.limits.projected_input_tokens:
                event.cancel = 'Projected input exceeds session limit'
            else:
                if self.admission is not None:
                    try:
                        accepted = self.admission()
                    except Exception:
                        accepted = False
                    if not accepted:
                        event.cancel = 'Aggregate model allowance unavailable or exhausted'
                        return
                self.calls += 1


def _mantle_model(model_id, region, key, limits):
    # Lazy import keeps offline/default-runtime use independent of this extra.
    import httpx
    from strands.models.openai import OpenAIModel
    # Match Strands 1.55.1's inspected per-model Mantle routing. New model lines
    # require separate endpoint/model compatibility verification before use.
    path = '/openai/v1' if model_id.startswith(('openai.gpt-5.', 'xai.grok-4.', 'google.gemma-4-')) else '/v1'
    return OpenAIModel(model_id=model_id, stream=False,
        client_args={'api_key': key, 'base_url': f'https://bedrock-mantle.{region}.api.aws{path}',
                     'max_retries': 0,
                     'timeout': httpx.Timeout(limits.read_seconds, connect=limits.connect_seconds,
                                             write=limits.connect_seconds, pool=limits.connect_seconds)},
        params={'max_completion_tokens': limits.output_tokens})


def build_live_agent(session, model_id, region, limits=None, environ=None, transport='bedrock-runtime', admission=None):
    """Construct only; caller must explicitly invoke. Key enters via environment."""
    limits = limits or Limits()
    if not isinstance(model_id, str) or not model_id.strip() or not isinstance(region, str) or not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-\d+', region):
        raise ValueError('Explicit model and region required')
    if transport not in {'bedrock-runtime', 'bedrock-mantle'}:
        raise ValueError('Unsupported Bedrock transport')
    environment = os.environ if environ is None else environ
    key = environment.get('AWS_BEARER_TOKEN_BEDROCK')
    if not key or not key.strip():
        raise ValueError('AWS_BEARER_TOKEN_BEDROCK must be set in the environment')
    if transport == 'bedrock-mantle':
        model = _mantle_model(model_id, region, key, limits)
    else:
        config = Config(connect_timeout=limits.connect_seconds, read_timeout=limits.read_seconds,
                        retries={'total_max_attempts': 1, 'mode': 'standard'})
        model = BedrockModel(api_key=key, model_id=model_id, region_name=region,
                             boto_client_config=config, max_tokens=limits.output_tokens,
                             streaming=False, use_native_token_count=False)
    budget = CallBudget(limits, admission=admission)
    agent = Agent(model=model, tools=session.tools(), system_prompt=SYSTEM,
                  hooks=[budget], retry_strategy=None, callback_handler=None,
                  context_manager=False,
                  name='Handoff Desk synthetic Bedrock demo')
    return agent, budget


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Explicitly permit Bedrock inference charges')
    parser.add_argument('--model', required=True)
    parser.add_argument('--region', required=True)
    parser.add_argument('--transport', choices=['bedrock-runtime', 'bedrock-mantle'], default='bedrock-runtime')
    args = parser.parse_args(argv)
    limits = Limits()
    if not args.execute:
        print(json.dumps({'mode': 'offline dry run', 'model': args.model, 'region': args.region,
                          'transport': args.transport, 'limits': limits.__dict__, 'cloud_calls': 0,
                          'notice': 'Resource limits are not a dollar spending cap.'}, indent=2))
        return 0
    # Avoid SDK/provider debug output containing request authentication headers.
    logging.disable(logging.CRITICAL)
    from server import Demo
    with tempfile.TemporaryDirectory(prefix='handoff-live-') as root:
        demo = Demo(root)
        try:
            agent, budget = build_live_agent(demo.session, args.model, args.region, limits, transport=args.transport)
        except Exception:
            print('Could not initialize Bedrock. Check model, region and environment credential. No key details are shown.')
            return 1
        print('Synthetic files only. No email sending. Up to 6 model calls, 1024 output tokens each; not a dollar cap.')
        print('Enter a request; /choose final_v2.csv; /define {"id":"...","period":"...","amount":"..."}; /quit.')
        print('Choices and definitions are recorded by these human commands, never by the model. Export files are temporary.')
        while budget.calls < limits.model_calls:
            try:
                line = input('handoff> ').strip()
            except (EOFError, KeyboardInterrupt):
                break
            if line == '/quit':
                break
            if not line:
                continue
            if len(line) > 4000:
                print('Input is too long; limit is 4000 characters.')
                continue
            try:
                if line.startswith('/choose '):
                    demo.session.choose('clean-data', line[len('/choose '):].strip())
                    print('Choice recorded; ask the agent to check the delivery again.')
                elif line.startswith('/define '):
                    chosen = demo.session.choices.get('clean-data')
                    if not chosen:
                        print('Choose a version first.')
                        continue
                    demo.session.confirm_definitions(chosen, json.loads(line[len('/define '):]))
                    print('Definitions recorded; ask the agent to check the delivery again.')
                else:
                    budget.begin_turn()
                    result = agent(line)
                    print(str(result))
                    print('Attempted model calls: ' + str(budget.calls))
            except Exception:
                # Do not echo provider exception text: it can contain request details.
                print('Operation stopped or rejected. No automatic retry. Check input and session limits.')
        print('Session ended. Temporary demo files removed. Email sent: no.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
