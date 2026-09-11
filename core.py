"""Deterministic handoff checks. No model calls, network, or email sending."""
import copy
import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import zipfile
from collections import Counter


class HandoffError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Workspace:
    def __init__(self, root, selected_files):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise HandoffError("Workspace must be a directory")
        self.selected = frozenset(self._name(name) for name in selected_files)
        self._reviews = {}
        for name in self.selected:
            self._path(name)

    @staticmethod
    def _name(name):
        if not isinstance(name, str) or not name or "\\" in name:
            raise HandoffError("Use a relative POSIX file path")
        p = PurePosixPath(name)
        if p.is_absolute() or any(part in ("..", ".", "") for part in name.split("/")):
            raise HandoffError("Path escapes or is not canonical")
        return str(p)

    def _path(self, name):
        name = self._name(name)
        if name not in self.selected:
            raise HandoffError("File was not selected: " + name)
        path = self.root
        for part in PurePosixPath(name).parts:
            path = path / part
            if path.is_symlink():
                raise HandoffError("Symlinks are not supported")
        if not path.resolve().is_relative_to(self.root):
            raise HandoffError("Path leaves workspace")
        if path.exists() and not path.is_file():
            raise HandoffError("Only regular files are supported")
        return path

    def _read(self, name):
        path = self._path(name)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise HandoffError("Cannot read selected file: " + name) from exc

    @staticmethod
    def _csv(data, name):
        try:
            reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")), strict=True)
            headers = reader.fieldnames or []
            rows = list(reader)
        except (UnicodeError, csv.Error) as exc:
            raise HandoffError("Invalid UTF-8 CSV: " + name) from exc
        if not headers or len(set(headers)) != len(headers) or any(not h for h in headers):
            raise HandoffError("CSV needs unique, nonempty headers")
        if any(None in row or None in row.values() for row in rows):
            raise HandoffError("CSV row width does not match header")
        return headers, rows

    def inspect_csv(self, name):
        data = self._read(name)
        headers, rows = self._csv(data, name)
        return {"file": name, "sha256": digest(data), "headers": headers,
                "row_count": len(rows), "sample_rows": rows[:3]}

    def generate_dictionary(self, name, meanings=None):
        evidence = self.inspect_csv(name)
        return self._dictionary(evidence, meanings)

    @staticmethod
    def _meaning_confirmed(value):
        if not isinstance(value, str) or not value.strip():
            return False
        normalized = value.strip().lower()
        return not (normalized in {"unknown", "tbd", "todo", "?", "n/a", "clarification required"}
                    or normalized.startswith("unknown") or "clarification required" in normalized)

    @staticmethod
    def _dictionary(evidence, meanings=None):
        meanings = meanings or {}
        if set(meanings) - set(evidence["headers"]):
            raise HandoffError("Meanings contain columns not observed")
        def esc(value):
            return str(value).replace("|", "\\|").replace("\n", " ")
        lines = ["# Data dictionary", "", "Source: " + evidence["file"],
                 "Source SHA-256: " + evidence["sha256"], "",
                 "Definitions marked user-provided are not inferred or independently verified.", "",
                 "| Observed column | Meaning | Basis |", "| --- | --- | --- |"]
        for header in evidence["headers"]:
            supplied = Workspace._meaning_confirmed(meanings.get(header))
            lines.append("| %s | %s | %s |" % (esc(header), esc(meanings[header]) if supplied else "Unknown — clarification required", "User-provided" if supplied else "Observed header only"))
        return "\n".join(lines) + "\n"

    def review(self, checklist, choices=None, dictionaries=None):
        choices = choices or {}
        dictionaries = copy.deepcopy(dictionaries or {})
        for name, definitions in dictionaries.items():
            self._path(name)
            if not isinstance(definitions, dict):
                raise HandoffError("Dictionary definitions must be a mapping")
        if not checklist:
            raise HandoffError("An explicit checklist is required")
        ids = [item["id"] for item in checklist]
        if len(ids) != len(set(ids)) or set(choices) - set(ids):
            raise HandoffError("Invalid or duplicate checklist ids/choices")
        results, snapshot, content, verified_dictionaries = [], {}, {}, {}
        def read_snapshot(name):
            if name not in snapshot:
                path = self._path(name)
                content[name] = self._read(name) if path.exists() else None
                snapshot[name] = digest(content[name]) if content[name] is not None else None
            if content[name] is None:
                raise HandoffError("Cannot read selected file: " + name)
            return content[name]
        for requirement in checklist:
            item = {"id": requirement["id"], "requirement": copy.deepcopy(requirement), "status": "unresolved"}
            candidates = list(dict.fromkeys(requirement["candidates"]))
            if not candidates:
                raise HandoffError("Each requirement needs explicit candidate paths")
            for name in candidates:
                self._path(name)
                try:
                    read_snapshot(name)
                except HandoffError:
                    pass
            chosen = choices.get(item["id"])
            if chosen is not None and chosen not in candidates:
                raise HandoffError("Choice is not a candidate")
            if len(candidates) > 1 and chosen is None:
                item["reason"] = "Explicit version choice required"
                item["candidates"] = []
                for name in candidates:
                    try:
                        item["candidates"].append(self.inspect_csv(name))
                    except HandoffError as exc:
                        item["candidates"].append({"file": name, "error": str(exc)})
                results.append(item)
                continue
            chosen = chosen or candidates[0]
            item["file"] = chosen
            item["selection_basis"] = "User choice" if item["id"] in choices else "Only explicit candidate"
            try:
                data = read_snapshot(chosen)
                headers, rows = self._csv(data, chosen)
                evidence = {"file": chosen, "sha256": digest(data), "headers": headers,
                            "row_count": len(rows), "sample_rows": rows[:3]}
                missing = sorted(set(requirement.get("required_columns", [])) - set(headers))
                problems = ["Missing columns: " + ", ".join(missing)] if missing else []
                if digest(data) != snapshot[chosen]:
                    problems.append("File changed during review; review again")
                if requirement.get("period"):
                    period = requirement["period"]
                    column, expected = period["column"], str(period["value"])
                    observed = sorted(set(row.get(column, "") for row in rows))
                    evidence["period"] = {
                        "column": column,
                        "expected": expected,
                        "observed": observed,
                        "mismatched_rows": [
                            {header: row.get(header, "") for header in headers}
                            for row in rows if row.get(column, "") != expected
                        ][:5],
                    }
                    if column not in headers or not rows or observed != [expected]:
                        problems.append("Period does not match explicit requirement")
                if requirement.get("require_dictionary"):
                    meanings = dictionaries.get(chosen, {})
                    if set(meanings) - set(headers):
                        problems.append("Dictionary contains unobserved columns")
                    undefined = [h for h in headers if not self._meaning_confirmed(meanings.get(h))]
                    evidence["dictionary"] = {"missing_definitions": undefined,
                                              "basis": "User-confirmed definitions supplied by application"}
                    if undefined:
                        problems.append("Missing confirmed definitions: " + ", ".join(undefined))
                    elif not set(meanings) - set(headers):
                        verified_dictionaries[chosen] = {
                            "meanings": {h: meanings[h].strip() for h in headers},
                            "evidence": copy.deepcopy(evidence)}
                if requirement.get("row_accounting"):
                    accounting = requirement["row_accounting"]
                    source, outputs, key = accounting["source"], accounting["outputs"], accounting["key"]
                    if (not isinstance(key, str) or not key.strip() or not isinstance(outputs, list)
                            or not outputs or len(set(outputs)) != len(outputs) or source in outputs):
                        raise HandoffError("Accounting requires one explicit key and distinct output files")
                    observed_files = {}
                    counts = {}
                    for name in [source] + outputs:
                        self._path(name)
                        try:
                            read_snapshot(name)
                        except HandoffError:
                            pass
                    for name in [source] + outputs:
                        self._path(name)
                        accounting_data = read_snapshot(name)
                        accounting_headers, accounting_rows = self._csv(accounting_data, name)
                        if key not in accounting_headers:
                            raise HandoffError("Accounting key missing in " + name)
                        if any(not row[key].strip() for row in accounting_rows):
                            raise HandoffError("Accounting key is empty or ambiguous in " + name)
                        counts[name] = Counter(row[key] for row in accounting_rows)
                        observed_files[name] = {"sha256": snapshot[name], "headers": accounting_headers,
                                                "row_count": len(accounting_rows)}
                    combined = Counter()
                    for name in outputs:
                        combined.update(counts[name])
                    lost = dict(counts[source] - combined)
                    extra = dict(combined - counts[source])
                    evidence["row_accounting"] = {"source": source, "outputs": list(outputs), "key": key,
                        "files": observed_files, "missing_occurrences": lost, "extra_occurrences": extra,
                        "basis": "Exact string-key multiplicities, not full row-value equality"}
                    if lost or extra:
                        problems.append("Output key occurrences do not partition source exactly")
                item.update(evidence=evidence, status="unresolved" if problems else "supported", problems=problems)
            except HandoffError as exc:
                item.update(status="missing" if not self._path(chosen).exists() else "unresolved", reason=str(exc))
            results.append(item)
        result = {"status": "supported" if all(i["status"] == "supported" for i in results) else "unresolved", "items": results, "snapshot": snapshot,
                  "verified_dictionaries": verified_dictionaries,
                  "scope": "Explicit CSV checks only; not client acceptance or business correctness."}
        encoded = json.dumps(result, sort_keys=True).encode()
        result["review_id"] = digest(encoded)
        self._reviews[result["review_id"]] = copy.deepcopy(result)
        return result

    def export(self, review, output_zip, dictionary=None):
        if self._reviews.get(review.get("review_id")) != review:
            raise HandoffError("Review is unknown or altered")
        if review["status"] != "supported":
            raise HandoffError("Resolve checklist before export")
        current = {}
        for name, previous in review["snapshot"].items():
            path = self._path(name)
            current[name] = self._read(name) if path.exists() else None
            now = digest(current[name]) if current[name] is not None else None
            if now != previous:
                raise HandoffError("Changed after review: " + name + "; review again")
        files = set(item["file"] for item in review["items"])
        for item in review["items"]:
            files.update(item["evidence"].get("row_accounting", {}).get("outputs", []))
        files = sorted(files)
        payload = {"deliverables/" + name: current[name] for name in files}
        for source, verified in review.get("verified_dictionaries", {}).items():
            payload["dictionaries/" + source + ".md"] = self._dictionary(verified["evidence"], verified["meanings"]).encode()
        if dictionary is not None:
            # Dictionary input is structured, never an unchecked replacement text.
            source = dictionary["file"]
            if source not in files:
                raise HandoffError("Dictionary source must be an exported deliverable")
            if source in review.get("verified_dictionaries", {}):
                supplied = dictionary.get("meanings", {})
                frozen = review["verified_dictionaries"][source]["meanings"]
                if supplied != frozen:
                    raise HandoffError("Required dictionary changed after review; review again")
            else:
                headers, rows = self._csv(current[source], source)
                evidence = {"file": source, "sha256": review["snapshot"][source], "headers": headers}
                payload["data-dictionary.md"] = self._dictionary(evidence, dictionary.get("meanings")).encode()
        manifest = copy.deepcopy(review)
        manifest["package_files"] = {name: digest(data) for name, data in payload.items()}
        payload["manifest.json"] = json.dumps(manifest, indent=2).encode()
        periods = [item["file"] + ": " + item["evidence"]["period"]["column"] + " = " + item["evidence"]["period"]["expected"]
                   for item in review["items"] if "period" in item["evidence"]]
        payload["delivery-email.txt"] = ("DRAFT — NOT SENT\nSubject: Deliverables ready for your review\n\nAttached: " + ", ".join(files) + ".\n"
            + ("Checked periods: " + "; ".join(periods) + ".\n" if periods else "")
            + "The included manifest records the explicit checks performed. Please review the deliverables; no client acceptance is implied.\n").encode()
        output = Path(output_zip)
        if output.resolve() in [self._path(name).resolve() for name in self.selected]:
            raise HandoffError("Export cannot overwrite selected input")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in payload.items():
                archive.writestr(name, data)
        # Exclusive creation avoids overwriting existing files or following symlinks.
        with output.open("xb") as stream:
            stream.write(buffer.getvalue())
        return str(output)
