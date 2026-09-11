"""Exercise real Strands tool wrappers without model inference or network access."""
import tempfile
import unittest
import zipfile
from pathlib import Path
from agent import HandoffSession
from core import Workspace, HandoffError


class ToolFlow(unittest.TestCase):
    def test_model_cannot_confirm_or_mutate_dictionary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'clean.csv').write_text('id\n001\n')
            session = HandoffSession(Workspace(root, ['clean.csv']),
                [{'id': 'delivery', 'candidates': ['clean.csv'],
                  'require_dictionary': True}], root / 'out.zip')
            tools = {t.tool_name: t for t in session.tools()}
            self.assertNotIn('confirm_definitions', tools)
            self.assertEqual(tools['check_delivery']()['status'], 'unresolved')
            tools['draft_dictionary']('clean.csv')
            self.assertEqual(tools['check_delivery']()['status'], 'unresolved')
            meanings = {'id': 'Customer identifier preserving leading zeros'}
            session.confirm_definitions('clean.csv', meanings)
            meanings['id'] = 'Changed caller-owned value'
            exposed = tools['delivery_requirements']()
            exposed['confirmed_definitions']['clean.csv']['id'] = 'Forged model change'
            self.assertEqual(session.dictionaries['clean.csv']['id'],
                             'Customer identifier preserving leading zeros')
            self.assertEqual(tools['check_delivery']()['status'], 'supported')
            session.confirm_definitions('clean.csv', {'id': ''})
            self.assertFalse(tools['export_package']()['exported'])
            self.assertEqual(tools['check_delivery']()['status'], 'unresolved')

    def test_clarification_export_and_stale_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'final.csv').write_text('id,period\n001,August\n')
            (root / 'final_v2.csv').write_text('id,period\n001,September\n')
            session = HandoffSession(
                Workspace(root, ['final.csv', 'final_v2.csv']),
                [{'id': 'delivery', 'candidates': ['final.csv', 'final_v2.csv'],
                  'required_columns': ['id', 'period'],
                  'period': {'column': 'period', 'value': 'September'}}],
                root / 'package.zip')
            tools = {t.tool_name: t for t in session.tools()}
            self.assertNotIn('choose', tools)
            self.assertEqual(tools['check_delivery']()['status'], 'unresolved')
            with self.assertRaises(HandoffError):
                tools['export_package']()
            session.choose('delivery', 'final_v2.csv')
            self.assertFalse(tools['export_package']()['exported'])
            self.assertEqual(tools['check_delivery']()['status'], 'supported')
            self.assertTrue(tools['export_package']()['exported'])
            with zipfile.ZipFile(root / 'package.zip') as archive:
                self.assertIn('deliverables/final_v2.csv', archive.namelist())
                self.assertNotIn('deliverables/final.csv', archive.namelist())
                self.assertIn(b'001,September', archive.read('deliverables/final_v2.csv'))
                self.assertIn(b'NOT SENT', archive.read('delivery-email.txt'))
            (root / 'final_v2.csv').write_text('id\n001\n')
            session.output_path = root / 'stale.zip'
            with self.assertRaisesRegex(HandoffError, 'Changed after review'):
                tools['export_package']()
            self.assertFalse((root / 'stale.zip').exists())


if __name__ == '__main__':
    unittest.main()
