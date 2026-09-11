"""Explicit single-deliverable CSV version intake; no inference or external I/O."""
import json
from pathlib import Path
import shutil
import tempfile

from agent import HandoffSession
from core import Workspace

FILE_LIMIT = 512 * 1024
TOTAL_LIMIT = 2 * 1024 * 1024


def _text(value, maximum=128):
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= maximum
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _validate_payload(payload):
    if not isinstance(payload, dict) or not {'files', 'required_columns'} <= set(payload) or set(payload) - {'files', 'required_columns', 'period'}:
        raise ValueError('Expected files, required_columns and optional period only')
    files, columns = payload['files'], payload['required_columns']
    if not isinstance(files, list) or not 1 <= len(files) <= 8:
        raise ValueError('Upload 1–8 candidate CSV files')
    if not isinstance(columns, list) or not 1 <= len(columns) <= 40 or not all(_text(c) for c in columns) or len(set(columns)) != len(columns):
        raise ValueError('Required columns must contain 1–40 unique nonempty names')
    period = payload.get('period')
    if 'period' in payload and (not isinstance(period, dict) or set(period) != {'column', 'value'}
                              or not _text(period['column']) or not _text(period['value'])):
        raise ValueError('Period must contain a bounded nonempty column and value')
    validated, names, total = [], set(), 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {'name', 'content'}:
            raise ValueError('Each file must contain name and content only')
        name, content = item['name'], item['content']
        if (not _text(name, 128) or '/' in name or '\\' in name or name in {'.', '..'}
                or name.startswith('.') or not name.lower().endswith('.csv') or ':' in name
                or name != name.strip() or name.casefold() in names):
            raise ValueError('Use distinct safe CSV basenames')
        if not isinstance(content, str):
            raise ValueError('CSV content must be UTF-8 text')
        try:
            data = content.encode('utf-8')
        except UnicodeError as exc:
            raise ValueError('CSV content must be valid UTF-8') from exc
        total += len(data)
        if len(data) > FILE_LIMIT or total > TOTAL_LIMIT:
            raise ValueError('CSV upload exceeds file or total size limit')
        names.add(name.casefold())
        validated.append((name, data))
    return validated, columns, period


def create_project(parent, payload):
    """Return (canonical Path, fresh session); isolated files, no network/model."""
    validated, columns, period = _validate_payload(payload)
    parent = Path(parent).resolve(strict=True)
    if not parent.is_dir():
        raise ValueError('Project parent must be an existing directory')
    root = Path(tempfile.mkdtemp(prefix='handoff-project-', dir=parent)).resolve()
    try:
        for name, data in validated:
            with (root / name).open('xb') as stream:
                stream.write(data)
            (root / name).chmod(0o600)
        selected = [name for name, _ in validated]
        workspace = Workspace(root, selected)
        for name in selected:
            workspace.inspect_csv(name)
        requirement = {'id': 'clean-data', 'candidates': selected,
                       'required_columns': list(columns), 'require_dictionary': True}
        if period is not None:
            requirement['period'] = dict(period)
        checklist = [requirement]
        metadata = {'selected': selected, 'checklist': checklist}
        with (root / 'project.json').open('x', encoding='utf-8') as stream:
            json.dump(metadata, stream, ensure_ascii=False, indent=2)
        (root / 'project.json').chmod(0o600)
        return root, HandoffSession(workspace, checklist, root / 'unused.zip')
    except Exception:
        shutil.rmtree(root)
        raise


def load_project(root):
    """Load bounded explicit project metadata, never confirmations or review."""
    original = Path(root).absolute()
    if any(p.is_symlink() for p in [original] + list(original.parents)):
        raise ValueError('Project location cannot contain symlinks')
    root = original.resolve(strict=True)
    metadata_path = root / 'project.json'
    if metadata_path.is_symlink() or not metadata_path.is_file() or metadata_path.stat().st_size > 65536:
        raise ValueError('Invalid project metadata file')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate metadata key')
            result[key] = value
        return result
    try:
        metadata = json.loads(metadata_path.read_bytes(), object_pairs_hook=pairs)
        if not isinstance(metadata, dict) or set(metadata) != {'selected', 'checklist'}:
            raise ValueError('Unexpected project metadata')
        selected, checklist = metadata['selected'], metadata['checklist']
        if not isinstance(selected, list) or not 1 <= len(selected) <= 8 or not isinstance(checklist, list) or len(checklist) != 1:
            raise ValueError('Invalid selected files or checklist')
        requirement = checklist[0]
        expected = {'id', 'candidates', 'required_columns', 'require_dictionary'}
        if not isinstance(requirement, dict) or not expected <= set(requirement) or set(requirement) - (expected | {'period'}):
            raise ValueError('Unsupported checklist metadata')
        if requirement['id'] != 'clean-data' or requirement['candidates'] != selected or requirement['require_dictionary'] is not True:
            raise ValueError('Metadata must describe candidate versions of one deliverable')
        # Validate names before opening them, using the shared intake schema.
        payload = {'files': [{'name': name, 'content': ''} for name in selected],
                   'required_columns': requirement['required_columns']}
        if 'period' in requirement:
            payload['period'] = requirement['period']
        _validate_payload(payload)
        for item in payload['files']:
            path = root / item['name']
            if path.is_symlink() or not path.is_file() or path.stat().st_size > FILE_LIMIT:
                raise ValueError('Invalid selected file')
            item['content'] = path.read_bytes().decode('utf-8')
        _validate_payload(payload)
        workspace = Workspace(root, selected)
        for name in selected:
            workspace.inspect_csv(name)
        return HandoffSession(workspace, checklist, root / 'unused.zip')
    except (UnicodeError, KeyError, TypeError, RecursionError) as exc:
        raise ValueError('Malformed project metadata') from exc
