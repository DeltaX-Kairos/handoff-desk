import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from core import Workspace, HandoffError, digest


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.root.joinpath("final.csv").write_text("id,period,amount\nA,2026-08,12\n")
        self.root.joinpath("final_v2.csv").write_text("id,period,amount\nA,2026-09,15\n")
        self.root.joinpath("private.txt").write_text("unselected")
        self.req = [{"id": "clean-data", "candidates": ["final.csv", "final_v2.csv"],
                     "required_columns": ["id", "period", "amount"],
                     "period": {"column": "period", "value": "2026-09"}}]
        self.ws = Workspace(self.root, ["final.csv", "final_v2.csv"])

    def ready(self):
        return self.ws.review(self.req, {"clean-data": "final_v2.csv"})

    def test_conflict_choice_export(self):
        unresolved = self.ws.review(self.req)
        self.assertEqual(unresolved["status"], "unresolved")
        self.assertEqual(len(unresolved["items"][0]["candidates"]), 2)
        with self.assertRaises(HandoffError):
            self.ws.export(unresolved, self.root / "bad.zip")
        review = self.ready()
        self.assertEqual(review["items"][0]["evidence"]["period"]["observed"], ["2026-09"])
        output = self.root / "out.zip"
        self.ws.export(review, output)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(set(archive.namelist()), {"deliverables/final_v2.csv", "manifest.json", "delivery-email.txt"})
            data = archive.read("deliverables/final_v2.csv")
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(digest(data), manifest["package_files"]["deliverables/final_v2.csv"])
            self.assertIn(b"NOT SENT", archive.read("delivery-email.txt"))

    def test_wrong_period_blocks(self):
        result = self.ws.review(self.req, {"clean-data": "final.csv"})
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["items"][0]["evidence"]["period"]["mismatched_rows"],
                         [{"id": "A", "period": "2026-08", "amount": "12"}])

    def test_change_after_review(self):
        review = self.ready()
        self.root.joinpath("final_v2.csv").write_text("id,period\nA,2026-09\n")
        with self.assertRaisesRegex(HandoffError, "Changed after review"):
            self.ws.export(review, self.root / "out.zip")
        self.assertFalse((self.root / "out.zip").exists())
        self.assertEqual(self.ready()["status"], "unresolved")

    def test_missing_column_blocks(self):
        self.req[0]["required_columns"].append("customer_id")
        review = self.ready()
        self.assertIn("Missing columns: customer_id", review["items"][0]["problems"])
        with self.assertRaises(HandoffError):
            self.ws.export(review, self.root / "out.zip")

    def test_dictionary_unknowns(self):
        text = self.ws.generate_dictionary("final_v2.csv", {"id": "Fictional row identifier"})
        self.assertEqual(text.count("Unknown — clarification required"), 2)
        self.assertIn("User-provided", text)
        output = self.root / "out.zip"
        self.ws.export(self.ready(), output, {"file": "final_v2.csv", "meanings": {"id": "Fictional row identifier"}})
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.read("data-dictionary.md").decode(), text)

    def test_traversal_unselected_symlink(self):
        for path in ("../private.txt", "/etc/passwd", "./final.csv", "a/../final.csv"):
            with self.assertRaises(HandoffError):
                Workspace(self.root, [path])
        with self.assertRaises(HandoffError):
            self.ws.inspect_csv("private.txt")
        (self.root / "link.csv").symlink_to(self.root / "final.csv")
        with self.assertRaises(HandoffError):
            Workspace(self.root, ["link.csv"])

    def test_symlink_replacement_after_review(self):
        review = self.ready()
        path = self.root / "final_v2.csv"
        path.unlink()
        path.symlink_to(self.root / "final.csv")
        with self.assertRaises(HandoffError):
            self.ws.export(review, self.root / "out.zip")

    def test_review_tampering_and_empty(self):
        review = self.ready()
        review["items"][0]["file"] = "private.txt"
        with self.assertRaises(HandoffError):
            self.ws.export(review, self.root / "out.zip")
        with self.assertRaises(HandoffError):
            self.ws.review([])

    def test_required_dictionary_partial_complete_and_frozen(self):
        self.req[0]["require_dictionary"] = True
        choices = {"clean-data": "final_v2.csv"}
        missing = self.ws.review(self.req, choices)
        self.assertEqual(missing["status"], "unresolved")
        self.assertEqual(missing["items"][0]["evidence"]["dictionary"]["missing_definitions"], ["id", "period", "amount"])
        definitions = {"id": "Synthetic identifier", "period": "unknown", "amount": "   "}
        partial = self.ws.review(self.req, choices, {"final_v2.csv": definitions})
        self.assertEqual(partial["status"], "unresolved")
        with self.assertRaises(HandoffError):
            self.ws.export(partial, self.root / "bad.zip", {"file": "final_v2.csv", "meanings": {"id": "ID", "period": "Month", "amount": "Units"}})
        definitions.update(period="Reporting month", amount="Synthetic units")
        ready = self.ws.review(self.req, choices, {"final_v2.csv": definitions})
        self.assertEqual(ready["status"], "supported")
        definitions["amount"] = "Changed after review"
        with self.assertRaisesRegex(HandoffError, "dictionary changed"):
            self.ws.export(ready, self.root / "bad.zip", {"file": "final_v2.csv", "meanings": definitions})
        # Caller mutations do not alter the frozen, reviewed definitions.
        self.ws.export(ready, self.root / "ok.zip")
        with zipfile.ZipFile(self.root / "ok.zip") as archive:
            text = archive.read("dictionaries/final_v2.csv.md")
            self.assertIn(b"Synthetic units", text)
            self.assertNotIn(b"Changed after review", text)
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(digest(text), manifest["package_files"]["dictionaries/final_v2.csv.md"])
        ready["verified_dictionaries"]["final_v2.csv"]["meanings"]["id"] = "Forged"
        with self.assertRaises(HandoffError):
            self.ws.export(ready, self.root / "forged.zip")

    def accounting(self, source="id,value\n001,a\n1,b\n1,c\n", clean="id,value\n001,a\n1,b\n", exceptions="id,value\n1,c\n"):
        for name, text in (("source.csv", source), ("clean.csv", clean), ("exceptions.csv", exceptions)):
            self.root.joinpath(name).write_text(text)
        ws = Workspace(self.root, ["source.csv", "clean.csv", "exceptions.csv"])
        req = [{"id": "partition", "candidates": ["clean.csv"], "required_columns": ["id"],
                "row_accounting": {"source": "source.csv", "outputs": ["clean.csv", "exceptions.csv"], "key": "id"}}]
        return ws, req

    def test_accounting_partition_preserves_duplicates_and_source_exclusion(self):
        ws, req = self.accounting()
        review = ws.review(req)
        self.assertEqual(review["status"], "supported")
        self.assertEqual(set(review["snapshot"]), {"source.csv", "clean.csv", "exceptions.csv"})
        ws.export(review, self.root / "partition.zip")
        with zipfile.ZipFile(self.root / "partition.zip") as archive:
            self.assertIn("deliverables/exceptions.csv", archive.namelist())
            self.assertNotIn("deliverables/source.csv", archive.namelist())
        self.root.joinpath("source.csv").write_text("id,value\n001,a\n")
        with self.assertRaisesRegex(HandoffError, "Changed after review"):
            ws.export(review, self.root / "stale.zip")

    def test_accounting_lost_duplicate_and_string_coercion(self):
        for output, expected_missing, expected_extra in [
            ("id,value\n", {"1": 1}, {}),
            ("id,value\n1,c\n1,d\n", {}, {"1": 1}),
            ("id,value\n001,c\n", {"1": 1}, {"001": 1}),
        ]:
            with self.subTest(output=output):
                ws, req = self.accounting(exceptions=output)
                review = ws.review(req)
                self.assertEqual(review["status"], "unresolved")
                evidence = review["items"][0]["evidence"]["row_accounting"]
                self.assertEqual(evidence["missing_occurrences"], expected_missing)
                self.assertEqual(evidence["extra_occurrences"], expected_extra)
                with self.assertRaises(HandoffError):
                    ws.export(review, self.root / "bad.zip")

    def test_accounting_key_ambiguity(self):
        for source in ("id,id\n1,1\n", "other,value\n1,a\n", "id,value\n ,a\n"):
            with self.subTest(source=source):
                ws, req = self.accounting(source=source)
                self.assertEqual(ws.review(req)["status"], "unresolved")
        ws, req = self.accounting()
        req[0]["row_accounting"]["outputs"] = ["clean.csv", "clean.csv"]
        self.assertEqual(ws.review(req)["status"], "unresolved")

    def test_placeholder_definitions_do_not_satisfy(self):
        self.req[0]["require_dictionary"] = True
        for placeholder in ("Unknown — clarification required", "TBD", "?", " ", None, 4):
            with self.subTest(placeholder=placeholder):
                result = self.ws.review(self.req, {"clean-data": "final_v2.csv"},
                    {"final_v2.csv": {"id": placeholder, "period": "Month", "amount": "Units"}})
                self.assertEqual(result["status"], "unresolved")


if __name__ == "__main__":
    unittest.main()
