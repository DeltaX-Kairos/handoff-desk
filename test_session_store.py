import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from agent import HandoffSession
from core import Workspace
from session_store import save_session, restore_session, SessionStoreError, MAX_BYTES


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / 'data.csv').write_text('id,period\n001,2026-09\n')
        (self.root / 'other.csv').write_text('id,period\n002,2026-08\n')
        self.checklist = [{'id': 'data', 'candidates': ['data.csv', 'other.csv'],
            'required_columns': ['id', 'period'], 'require_dictionary': True}]
        self.path = self.root / '.handoff-session.json'

    def fresh(self):
        return HandoffSession(Workspace(self.root, ['data.csv', 'other.csv']), self.checklist, self.root / 'export.zip')

    def saved(self):
        session = self.fresh()
        session.choose('data', 'data.csv')
        session.confirm_definitions('data.csv', {'id': 'Fictional identifier', 'period': 'Reporting month'})
        session.review_result = session.workspace.review(session.checklist, session.choices, session.dictionaries)
        save_session(session, self.path)
        return session

    def test_roundtrip_private_atomic_and_requires_review(self):
        original = self.saved()
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(set(json.loads(self.path.read_text())), {'version', 'checklist', 'snapshot', 'choices', 'dictionaries'})
        restored = self.fresh()
        self.assertEqual(restore_session(restored, self.path), 'restored')
        self.assertEqual(restored.choices, original.choices)
        self.assertEqual(restored.dictionaries, original.dictionaries)
        self.assertIsNone(restored.review_result)
        self.assertEqual(list(self.root.glob('.handoff-state-*')), [])
        self.assertEqual(restored.workspace.review(restored.checklist, restored.choices, restored.dictionaries)['status'], 'supported')

    def test_changed_selected_file_invalidates_even_unchosen(self):
        self.saved()
        (self.root / 'other.csv').write_text('id,period\n003,2026-08\n')
        fresh = self.fresh()
        self.assertEqual(restore_session(fresh, self.path), 'invalidated')
        self.assertEqual((fresh.choices, fresh.dictionaries), ({}, {}))

    def test_changed_checklist_invalidates(self):
        self.saved()
        self.checklist[0]['required_columns'].append('amount')
        fresh = self.fresh()
        self.assertEqual(restore_session(fresh, self.path), 'invalidated')
        self.assertEqual(fresh.choices, {})

    def test_bad_records_never_partially_replay(self):
        self.saved()
        valid = json.loads(self.path.read_text())
        malformed_choice = dict(valid, choices={'data': 'outside.csv'})
        malformed_dictionary = dict(valid, dictionaries={'data.csv': {'not_a_column': 'Invalid'}})
        for text in ('not json', '[]', '{"version":1,"version":1}',
                     json.dumps(dict(valid, model_output='not allowed')),
                     json.dumps(malformed_choice), json.dumps(malformed_dictionary), ' ' * (MAX_BYTES + 1)):
            with self.subTest(text=text[:35]):
                self.path.write_text(text)
                fresh = self.fresh()
                self.assertEqual(restore_session(fresh, self.path), 'invalidated')
                self.assertEqual((fresh.choices, fresh.dictionaries), ({}, {}))

    def test_missing_symlink_and_permissions(self):
        self.assertEqual(restore_session(self.fresh(), self.path), 'missing')
        self.saved()
        linked = self.root / 'link.json'
        linked.symlink_to(self.path)
        self.assertEqual(restore_session(self.fresh(), linked), 'invalidated')
        with self.assertRaises(SessionStoreError):
            save_session(self.fresh(), linked)
        os.chmod(self.path, 0o644)
        self.assertEqual(restore_session(self.fresh(), self.path), 'invalidated')
        with self.assertRaises(SessionStoreError):
            save_session(self.fresh(), self.path)


if __name__ == '__main__':
    unittest.main()
