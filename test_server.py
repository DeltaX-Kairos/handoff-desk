"""Exercise actual HTTP handlers with in-memory wire requests; no sockets/model."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from server import Demo, handler_for


class WireConnection:
    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = io.BytesIO()

    def makefile(self, mode, *args):
        return self.input

    def sendall(self, data):
        self.output.write(data)


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.demo = Demo(self.root)
        self.port = 8765
        self.handler = handler_for(self.demo, self.port)

    def request(self, method='POST', path='/action', payload=None, headers=None, raw=None):
        body = raw if raw is not None else json.dumps(payload or {'action': 'review'}).encode()
        fields = {'Host': '127.0.0.1:8765', 'Origin': 'http://127.0.0.1:8765',
                  'X-Handoff-Token': self.demo.token, 'Content-Length': str(len(body))}
        fields.update(headers or {})
        request = (method + ' ' + path + ' HTTP/1.0\r\n' + ''.join(
            k + ': ' + v + '\r\n' for k, v in fields.items() if v is not None) + '\r\n').encode() + body
        connection = WireConnection(request)
        self.handler(connection, ('127.0.0.1', 12345), SimpleNamespace())
        head, response = connection.output.getvalue().split(b'\r\n\r\n', 1)
        lines = head.decode().split('\r\n')
        status = int(lines[0].split()[1])
        response_headers = dict(line.split(': ', 1) for line in lines[1:])
        return status, response_headers, response

    def action(self, action, **fields):
        status, headers, body = self.request(payload=dict(action=action, **fields))
        return status, json.loads(body)

    def test_invalid_host_token_origin_cannot_mutate(self):
        for headers in ({'Host': 'attacker.example'}, {'Host': '127.0.0.1:9999'},
                        {'X-Handoff-Token': 'wrong'}, {'X-Handoff-Token': None},
                        {'Origin': 'https://attacker.example'}, {'Origin': 'null'}):
            with self.subTest(headers=headers):
                status, _, _ = self.request(payload={'action': 'choose', 'file': 'final_v2.csv'}, headers=headers)
                self.assertEqual(status, 403)
                self.assertEqual(self.demo.session.choices, {})
        self.assertEqual(self.request(method='GET', path='/', headers={'Host': 'attacker.example'})[0], 403)

    def test_invalid_requests_fail_without_export(self):
        for raw in (b'not json', b'[]', b'{}'):
            with self.subTest(raw=raw):
                self.assertEqual(self.request(raw=raw)[0], 400)
        self.assertEqual(self.request(headers={'Content-Length': '16385'})[0], 400)
        self.assertEqual(self.request(path='/other')[0], 400)
        self.assertEqual(self.action('choose', file='../private.csv')[0], 400)
        self.assertEqual(self.action('definitions', meanings={'id': 'Identifier'})[0], 400)
        self.assertEqual(self.action('unknown')[0], 400)
        self.assertIsNone(self.demo.download)

    def test_human_selection_definitions_review_and_zip(self):
        # Assert the flow never constructs a model agent or sends SMTP mail.
        with patch('agent.Agent', side_effect=AssertionError('No model allowed')), \
             patch('smtplib.SMTP', side_effect=AssertionError('No email allowed')):
            status, result = self.action('review')
            self.assertEqual(status, 200)
            self.assertEqual(result['status'], 'unresolved')
            self.assertEqual(self.action('export')[0], 400)
            status, result = self.action('choose', file='final_v2.csv')
            self.assertEqual(result['status'], 'unresolved')
            meanings = {'id': 'Synthetic source identifier', 'period': 'Reporting month', 'amount': 'Synthetic units'}
            status, result = self.action('definitions', meanings=meanings)
            self.assertEqual(status, 200)
            self.assertEqual(result['status'], 'supported')
            self.assertEqual(self.action('review')[1]['status'], 'supported')
            status, result = self.action('export')
            self.assertEqual((status, result), (200, {'exported': True, 'email_sent': False}))
            status, headers, body = self.request(method='GET', path='/delivery.zip')
            self.assertEqual(status, 200)
            self.assertEqual(headers['Content-Type'], 'application/zip')
            self.assertEqual(headers['Cache-Control'], 'no-store')
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                names = set(archive.namelist())
                self.assertEqual(names, {'deliverables/final_v2.csv', 'deliverables/exceptions.csv',
                    'dictionaries/final_v2.csv.md', 'manifest.json', 'delivery-email.txt'})
                manifest = json.loads(archive.read('manifest.json'))
                for name, expected in manifest['package_files'].items():
                    self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), expected)
                self.assertIn(b'NOT SENT', archive.read('delivery-email.txt'))
                self.assertIn(b'2026-09', archive.read('delivery-email.txt'))
                self.assertNotIn(b'Unknown', archive.read('dictionaries/final_v2.csv.md'))

    def test_mutated_file_blocks_http_export(self):
        self.action('choose', file='final_v2.csv')
        self.action('definitions', meanings={'id': 'Identifier', 'period': 'Month', 'amount': 'Units'})
        self.assertTrue(self.action('export')[1]['exported'])
        self.root.joinpath('final_v2.csv').write_text('id,period\n001,2026-09\n')
        status, body = self.action('export')
        self.assertEqual(status, 400)
        self.assertIn('Changed after review', body['error'])
        self.assertIsNone(self.demo.download)
        self.assertEqual(self.request(method='GET', path='/delivery.zip')[0], 404)

    def test_new_choice_invalidates_previous_download(self):
        self.action('choose', file='final_v2.csv')
        self.action('definitions', meanings={'id': 'Identifier', 'period': 'Month', 'amount': 'Units'})
        self.assertTrue(self.action('export')[1]['exported'])
        self.action('choose', file='final.csv')
        self.assertEqual(self.request(method='GET', path='/delivery.zip')[0], 404)

    def test_persistent_restart_requires_fresh_review(self):
        first = Demo(self.root, persistent=True)
        first.act('choose', {'file': 'final_v2.csv'})
        first.act('definitions', {'meanings': {'id': 'Identifier', 'period': 'Month', 'amount': 'Units'}})
        self.assertTrue(first.act('export', {})['exported'])
        second = Demo(self.root, persistent=True)
        self.assertEqual(second.restore_status, 'restored')
        self.assertEqual(second.state()['choices'], {'clean-data': 'final_v2.csv'})
        self.assertTrue(second.state()['review_required'])
        self.assertIsNone(second.download)
        self.assertFalse(second.act('export', {})['exported'])
        self.assertEqual(second.act('review', {})['status'], 'supported')
        self.assertTrue(second.act('export', {})['exported'])

    def test_failed_confirmation_save_rolls_back(self):
        demo = Demo(self.root, persistent=True)
        with patch('session_store.save_session', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(ValueError, 'previous choices were retained'):
                demo.act('choose', {'file': 'final_v2.csv'})
        self.assertEqual(demo.session.choices, {})
        self.assertIsNone(demo.session.review_result)

    def test_live_investigation_is_disabled_by_default(self):
        self.assertFalse(self.demo.state()['investigation_enabled'])
        status, body = self.action('investigate', prompt='Inspect this delivery')
        self.assertEqual(status, 400)
        self.assertIn('not connected', body['error'])

    def test_investigation_requires_fresh_delivery_review(self):
        self.action('choose', file='final_v2.csv')
        self.action('definitions', meanings={'id': 'Identifier', 'period': 'Month', 'amount': 'Units'})
        self.assertIsNotNone(self.demo.session.review_result)
        self.demo.investigation = SimpleNamespace(investigate=lambda prompt: {
            'message': 'Offline test response', 'attempted_model_calls': 2, 'review_required': True})
        status, result = self.action('investigate', prompt='Inspect the delivery')
        self.assertEqual(status, 200)
        self.assertTrue(result['review_required'])
        self.assertIsNone(self.demo.session.review_result)
        self.assertFalse(self.action('export')[1]['exported'])

    def test_live_configuration_without_credential_refused(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'temporary Bedrock credential'):
                Demo(self.root, model_id='amazon.nova-lite-v1:0', region='us-east-2')

    def test_uploaded_project_export_and_restart(self):
        demo = Demo(self.root, persistent=True)
        original = (self.root / 'final.csv').read_bytes()
        project = {'files': [{'name': 'customer-export.csv', 'content': 'customer_id,total\n001,15\n1,25\n'}], 'required_columns': ['customer_id', 'total']}
        state = demo.act('new_project', {'project': project})
        self.assertEqual(state['dataset'], 'uploaded')
        self.assertEqual(state['files'][0]['headers'], ['customer_id', 'total'])
        self.assertEqual(state['files'][0]['sample_rows'][0]['customer_id'], '001')
        self.assertNotIn('row_accounting', state['checklist'][0])
        demo.act('choose', {'file': 'customer-export.csv'})
        demo.act('definitions', {'meanings': {'customer_id': 'Customer identifier as supplied', 'total': 'Total in synthetic units'}})
        self.assertTrue(demo.act('export', {})['exported'])
        with zipfile.ZipFile(io.BytesIO(demo.download)) as archive:
            self.assertIn('deliverables/customer-export.csv', archive.namelist())
            self.assertNotIn('deliverables/final.csv', archive.namelist())
        restarted = Demo(self.root, persistent=True)
        self.assertEqual(restarted.dataset, 'uploaded')
        self.assertEqual(restarted.restore_status, 'restored')
        self.assertEqual(restarted.session.choices, {'clean-data': 'customer-export.csv'})
        self.assertIsNone(restarted.session.review_result)
        self.assertEqual((self.root / 'final.csv').read_bytes(), original)

    def test_invalid_upload_preserves_current_project(self):
        self.action('choose', file='final_v2.csv')
        original_session = self.demo.session
        status, body = self.action('new_project', project={'files': [{'name': '../private.csv', 'content': 'id\n1\n'}], 'required_columns': ['id']})
        self.assertEqual(status, 400)
        self.assertIs(self.demo.session, original_session)
        self.assertEqual(self.demo.session.choices, {'clean-data': 'final_v2.csv'})


if __name__ == '__main__':
    unittest.main()
