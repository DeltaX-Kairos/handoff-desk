"""One explicitly invoked, bounded fictional-data Bedrock investigation."""
import datetime
import json
import logging
from pathlib import Path
import tempfile
import argparse
from live_agent import build_live_agent, Limits
from server import Demo


def main():
    logging.disable(logging.CRITICAL)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transport', choices=['bedrock-runtime','bedrock-mantle'], default='bedrock-runtime')
    parser.add_argument('--model', default='amazon.nova-lite-v1:0')
    args=parser.parse_args()
    receipt = {'checkedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'model': args.model, 'region': 'us-east-2', 'transport':args.transport,
               'fictional_inputs_only': True, 'email_sent': False,
               'model_response_received': False, 'attempted_model_calls': 0}
    with tempfile.TemporaryDirectory() as root:
        demo = Demo(root)
        try:
            agent, budget = build_live_agent(demo.session, receipt['model'], receipt['region'],
                Limits(model_calls=3, output_tokens=1024, session_seconds=60), transport=args.transport)
            result = agent('Read the delivery requirements and use check_delivery. Explain the blockers and ask the next human question. Do not export, select a candidate, or confirm definitions.')
            usage = dict(result.metrics.accumulated_usage)
            receipt.update({'attempted_model_calls': budget.calls, 'usage': usage,
                            'model_response_received': usage.get('outputTokens', 0) > 0,
                            'response': str(result), 'tool_calls': demo.session.tool_calls,
                            'choices_unchanged': demo.session.choices == {},
                            'definitions_unchanged': demo.session.dictionaries == {}})
        except Exception as exc:
            receipt['attempted_model_calls'] = budget.calls if 'budget' in locals() else 0
            receipt['error_type'] = type(exc).__name__
            response = getattr(exc, 'response', {})
            if isinstance(response, dict):
                code = response.get('Error', {}).get('Code')
                if code in {'AccessDeniedException', 'ValidationException', 'ThrottlingException', 'UnrecognizedClientException', 'ExpiredTokenException', 'ServiceUnavailableException'}:
                    receipt['provider_error_code'] = code
            receipt['status'] = 'Live investigation did not complete; no automatic retry'
    output = Path('output/live-probe')
    output.mkdir(parents=True, exist_ok=True)
    name = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json'
    (output / name).write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))
    return 0 if receipt['model_response_received'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
