from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from intake import create_project, load_project, FILE_LIMIT, TOTAL_LIMIT


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name).resolve()
        self.payload = {'files': [{'name': 'version1.csv', 'content': 'id,period\r\n001,2026-09\r\n'},
                                  {'name': 'version2.csv', 'content': 'id,period\n1,2026-09\n'}],
                        'required_columns': ['id', 'period'], 'period': {'column': 'period', 'value': '2026-09'}}

    def test_roundtrip_preserves_bytes_and_explicit_scope(self):
        root, session = create_project(self.parent, self.payload)
        self.assertEqual(root, root.resolve())
        self.assertEqual((root / 'version1.csv').read_bytes(), self.payload['files'][0]['content'].encode())
        self.assertEqual(session.workspace.inspect_csv('version1.csv')['sample_rows'][0]['id'], '001')
        self.assertEqual(session.workspace.review(session.checklist)['status'], 'unresolved')
        metadata = json.loads((root / 'project.json').read_text())
        self.assertEqual(metadata, {'selected': ['version1.csv', 'version2.csv'], 'checklist': session.checklist})
        self.assertNotIn(str(self.parent), (root / 'project.json').read_text())
        session.choose('clean-data', 'version1.csv')
        session.confirm_definitions('version1.csv', {'id': 'Identifier', 'period': 'Month'})
        self.assertEqual(session.workspace.review(session.checklist, session.choices, session.dictionaries)['status'], 'supported')

    def test_names_rejected_and_existing_work_untouched(self):
        (self.parent / 'existing.csv').write_text('preserve')
        for name in ('../bad.csv', '/bad.csv', 'a\\bad.csv', 'bad\x00.csv', '.hidden.csv', 'bad.txt', 'a:b.csv'):
            with self.subTest(name=name):
                payload = deepcopy(self.payload)
                payload['files'][0]['name'] = name
                with self.assertRaises(ValueError):
                    create_project(self.parent, payload)
        payload = deepcopy(self.payload)
        payload['files'][1]['name'] = 'VERSION1.CSV'
        with self.assertRaises(ValueError):
            create_project(self.parent, payload)
        self.assertEqual(list(self.parent.iterdir()), [self.parent / 'existing.csv'])
        self.assertEqual((self.parent / 'existing.csv').read_text(), 'preserve')

    def test_invalid_csv_cleanup(self):
        for content in ('', 'id,id\n1,2\n', 'id,name\n1\n', 'id\n1,2\n', 'id\n"unterminated'):
            with self.subTest(content=content):
                payload = deepcopy(self.payload)
                payload['files'][0]['content'] = content
                with self.assertRaises(ValueError):
                    create_project(self.parent, payload)
                self.assertEqual(list(self.parent.iterdir()), [])

    def test_size_limits(self):
        payload = deepcopy(self.payload)
        payload['files'][0]['content'] = 'x' * (FILE_LIMIT + 1)
        with self.assertRaises(ValueError):
            create_project(self.parent, payload)
        payload['files'] = [{'name': f'{n}.csv', 'content': 'x' * FILE_LIMIT} for n in range(5)]
        self.assertGreater(sum(len(f['content']) for f in payload['files']), TOTAL_LIMIT)
        with self.assertRaises(ValueError):
            create_project(self.parent, payload)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_schema_rejects_unexpected_or_malformed(self):
        for update in ({'extra': True}, {'files': []}, {'required_columns': []},
                       {'required_columns': ['id', 'id']}, {'required_columns': ['x' * 129]},
                       {'period': None}, {'period': {'column': 'id', 'value': 1}},
                       {'period': {'column': 'id', 'value': '', 'extra': True}}):
            with self.subTest(update=update):
                payload = deepcopy(self.payload)
                payload.update(update)
                with self.assertRaises(ValueError):
                    create_project(self.parent, payload)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_load_roundtrip_and_modified_content(self):
        root, original = create_project(self.parent, self.payload)
        loaded = load_project(root)
        self.assertEqual(loaded.checklist, original.checklist)
        self.assertEqual(loaded.choices, {})
        self.assertEqual(loaded.dictionaries, {})
        (root / 'version1.csv').write_text('id,period\nchanged,2026-09\n')
        self.assertEqual(load_project(root).workspace.inspect_csv('version1.csv')['sample_rows'][0]['id'], 'changed')

    def test_load_rejects_metadata_escape_and_unknown_rules(self):
        root, _ = create_project(self.parent, self.payload)
        metadata_path = root / 'project.json'
        original = json.loads(metadata_path.read_text())
        bad = deepcopy(original)
        bad['selected'][0] = '../outside.csv'
        bad['checklist'][0]['candidates'] = bad['selected']
        metadata_path.write_text(json.dumps(bad))
        with self.assertRaises(ValueError):
            load_project(root)
        bad = deepcopy(original)
        bad['checklist'][0]['row_accounting'] = {'source': 'secret.csv'}
        metadata_path.write_text(json.dumps(bad))
        with self.assertRaises(ValueError):
            load_project(root)
        metadata_path.write_text(json.dumps(original))
        (root / 'version1.csv').unlink()
        (root / 'version1.csv').symlink_to(root / 'version2.csv')
        with self.assertRaises(ValueError):
            load_project(root)


if __name__ == '__main__':
    unittest.main()
