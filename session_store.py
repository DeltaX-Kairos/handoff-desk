"""Local decision recovery only; never stores credentials, model text or reviews.

restore_session returns 'missing', 'invalidated', or 'restored'. Restore requires a
fresh HandoffSession; invalid or unsafe state is never replayed. Successful
restoration replays human decisions; callers must rerun check_delivery afterward.
"""
import json
import os
from pathlib import Path
import stat
import tempfile
from copy import deepcopy

from agent import HandoffSession
from core import digest

MAX_BYTES = 1024 * 1024
FIELDS = {'version', 'checklist', 'snapshot', 'choices', 'dictionaries'}


class SessionStoreError(ValueError):
    pass


def _location(path):
    path = Path(path).absolute()
    for part in [path] + list(path.parents):
        if part.is_symlink():
            raise SessionStoreError('Session path cannot contain symlinks')
    parent = path.parent
    info = parent.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise SessionStoreError('Session parent must be owned by this user and not writable by others')
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SessionStoreError('Session file must be a private user-owned regular file')
        if info.st_nlink != 1:
            raise SessionStoreError('Hard-linked session files are not supported')
    return path


def _snapshot(workspace):
    result = {}
    for name in sorted(workspace.selected):
        path = workspace._path(name)
        result[name] = digest(workspace._read(name)) if path.exists() else None
    return result


def _validate(record):
    if not isinstance(record, dict) or set(record) != FIELDS or type(record['version']) is not int or record['version'] != 1:
        raise SessionStoreError('Unexpected session record')
    if not isinstance(record['checklist'], list) or not record['checklist'] or not all(isinstance(i, dict) for i in record['checklist']):
        raise SessionStoreError('Invalid checklist')
    if not isinstance(record['snapshot'], dict) or not all(
        isinstance(k, str) and (v is None or isinstance(v, str) and len(v) == 64 and all(c in '0123456789abcdef' for c in v))
        for k, v in record['snapshot'].items()):
        raise SessionStoreError('Invalid file snapshot')
    if not isinstance(record['choices'], dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in record['choices'].items()):
        raise SessionStoreError('Invalid choices')
    if not isinstance(record['dictionaries'], dict) or not all(
        isinstance(k, str) and isinstance(v, dict) and all(
            isinstance(column, str) and isinstance(meaning, str) and len(meaning) <= 4096
            for column, meaning in v.items()) for k, v in record['dictionaries'].items()):
        raise SessionStoreError('Invalid dictionaries')


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SessionStoreError('Duplicate JSON key')
        result[key] = value
    return result


def save_session(session, path):
    path = _location(path)
    if path.resolve() in [session.workspace._path(n).resolve() for n in session.workspace.selected]:
        raise SessionStoreError('Session state cannot overwrite selected input')
    record = {'version': 1, 'checklist': session.checklist, 'snapshot': _snapshot(session.workspace),
              'choices': session.choices, 'dictionaries': session.dictionaries}
    _validate(record)
    data = json.dumps(record, sort_keys=True, allow_nan=False).encode('utf-8')
    if len(data) > MAX_BYTES:
        raise SessionStoreError('Session record exceeds size limit')
    fd, temporary = tempfile.mkstemp(prefix='.handoff-state-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _location(path)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(path)


def _restore(session, path):
    if session.choices or session.dictionaries or session.review_result is not None:
        raise SessionStoreError('Restore requires a fresh session')
    path = _location(path)
    if not path.exists():
        return 'missing'
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise SessionStoreError('Invalid or oversized session record')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(MAX_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_BYTES:
        raise SessionStoreError('Oversized session record')
    try:
        record = json.loads(data, object_pairs_hook=_pairs)
        _validate(record)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise SessionStoreError('Corrupt or unexpected session record') from exc
    if record['checklist'] != session.checklist or record['snapshot'] != _snapshot(session.workspace):
        return 'invalidated'
    # Validate the entire replay before changing the caller's session.
    probe = HandoffSession(session.workspace, session.checklist, session.output_path)
    try:
        for key, value in record['choices'].items():
            probe.choose(key, value)
        for key, value in record['dictionaries'].items():
            probe.confirm_definitions(key, value)
    except (ValueError, KeyError, TypeError, StopIteration) as exc:
        raise SessionStoreError('Saved decisions do not match the session') from exc
    for key, value in record['choices'].items():
        session.choose(key, value)
    for key, value in record['dictionaries'].items():
        session.confirm_definitions(key, value)
    return 'restored'


def restore_session(session, path):
    """Recover decisions into a fresh session; corrupt/unsafe state fails closed."""
    previous = (deepcopy(session.choices), deepcopy(session.dictionaries), session.review_result)
    try:
        return _restore(session, path)
    except (ValueError, OSError, KeyError, TypeError, RecursionError):
        session.choices, session.dictionaries, session.review_result = previous
        return 'invalidated'
