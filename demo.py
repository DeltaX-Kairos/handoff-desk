"""Reproducible fictional scenario. Scripted user choices; no model invocation."""
import json
from pathlib import Path
import tempfile
from agent import HandoffSession
from core import Workspace, HandoffError


def run_demo(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        samples = {
            'source.csv': 'id,period,amount\n001,2026-09,10\n1,2026-09,20\n001,2026-09,10\n',
            'final.csv': 'id,period,amount\n001,2026-08,10\n1,2026-08,20\n',
            'final_v2.csv': 'id,period,amount\n001,2026-09,10\n1,2026-09,20\n',
            'exceptions.csv': 'id,period,amount\n001,2026-09,10\n',
        }
        for name, text in samples.items():
            (root / name).write_text(text)
        checklist = [{
            'id': 'clean-data', 'candidates': ['final.csv', 'final_v2.csv'],
            'required_columns': ['id', 'period', 'amount'],
            'period': {'column': 'period', 'value': '2026-09'},
            'require_dictionary': True,
            'row_accounting': {'source': 'source.csv',
                               'outputs': ['final_v2.csv', 'exceptions.csv'], 'key': 'id'},
        }]
        session = HandoffSession(Workspace(root, samples), checklist, output / 'delivery.zip')
        tools = {t.tool_name: t for t in session.tools()}
        events = []
        events.append({'step': 'Initial conflicting versions', 'review': tools['check_delivery']()})
        session.choose('clean-data', 'final_v2.csv')
        events.append({'step': 'User selects September file; dictionary still missing',
                       'review': tools['check_delivery']()})
        session.confirm_definitions('final_v2.csv', {
            'id': 'Source customer identifier; leading zeros are significant.',
            'period': 'Reporting month in YYYY-MM format.',
            'amount': 'Fictional invoiced amount in USD.',
        })
        events.append({'step': 'User confirms all field meanings', 'review': tools['check_delivery']()})
        events.append({'step': 'Export checked package', 'result': tools['export_package']()})
        (root / 'final_v2.csv').write_text('id\n001\n')
        session.output_path = output / 'stale.zip'
        try:
            tools['export_package']()
        except HandoffError as exc:
            events.append({'step': 'Changed file rejected', 'reason': str(exc)})
        else:
            raise AssertionError('Stale data was incorrectly exported')
        evidence = {'mode': 'Scripted fictional scenario through real SDK tools; no LLM',
                    'events': events, 'model_calls': 0, 'email_sent': False}
        (output / 'scenario.json').write_text(json.dumps(evidence, indent=2) + '\n')
        return evidence


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', help='New output directory; existing archives are not overwritten')
    args = parser.parse_args()
    result = run_demo(args.output)
    for event in result['events']:
        print(event['step'], event.get('review', {}).get('status', event.get('reason', 'completed')))
