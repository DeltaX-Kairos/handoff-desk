"""Loopback fictional demo. Model inference is disabled unless explicitly configured."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import json
import secrets
import tempfile
import uuid
import os
import re
from copy import deepcopy
from contextlib import ExitStack
from agent import HandoffSession
from core import Workspace, HandoffError


class Demo:
    def __init__(self, root, persistent=False, model_id=None, region=None, transport='bedrock-runtime', admission=None):
        if Path(root).is_symlink():
            raise ValueError('Session directory cannot be a symbolic link')
        # macOS temporary paths have a system /var alias; use the physical root.
        self.root = Path(root).resolve()
        self.persistent = persistent
        self.dataset = 'fictional'
        self.model_id, self.region = model_id, region
        self.transport = transport
        self.admission = admission
        self.restore_status = 'temporary'
        self.token = secrets.token_urlsafe(32)
        samples = {
            'source.csv': 'id,period,amount\n001,2026-09,10\n1,2026-09,20\n001,2026-09,10\n',
            'final.csv': 'id,period,amount\n001,2026-08,10\n1,2026-08,20\n',
            'final_v2.csv': 'id,period,amount\n001,2026-09,10\n1,2026-09,20\n',
            'exceptions.csv': 'id,period,amount\n001,2026-09,10\n',
        }
        for name, text in samples.items():
            path = self.root / name
            if path.is_symlink():
                raise ValueError('Demo files cannot be symbolic links')
            if not path.exists():
                path.write_text(text)
        self.session = HandoffSession(Workspace(self.root, samples), [{
            'id': 'clean-data', 'candidates': ['final.csv', 'final_v2.csv'],
            'required_columns': ['id', 'period', 'amount'],
            'period': {'column': 'period', 'value': '2026-09'},
            'require_dictionary': True,
            'row_accounting': {'source': 'source.csv',
                               'outputs': ['final_v2.csv', 'exceptions.csv'], 'key': 'id'},
        }], self.root / 'unused.zip')
        pointer = self.root / '.active-project.json'
        if persistent and pointer.exists():
            if pointer.is_symlink() or pointer.stat().st_size > 1024:
                raise ValueError('Saved project pointer is invalid')
            active = json.loads(pointer.read_text())
            if not isinstance(active, dict) or set(active) != {'project'} or not isinstance(active['project'], str) or not re.fullmatch(r'[A-Za-z0-9_-]+', active['project']):
                raise ValueError('Saved project pointer is invalid')
            from intake import load_project
            self.session = load_project(self.root / 'projects' / active['project'])
            self.dataset = 'uploaded'
        self.tools = {t.tool_name: t for t in self.session.tools()}
        self.download = None
        if persistent:
            from session_store import restore_session
            self.restore_status = restore_session(self.session, self.session.workspace.root / '.handoff-session.json')
        self.investigation = None
        if model_id or region:
            if not model_id or not region:
                raise ValueError('Both Bedrock model and region must be explicit')
            if not os.environ.get('AWS_BEARER_TOKEN_BEDROCK', '').strip():
                raise ValueError('A temporary Bedrock credential must be configured before enabling investigation')
            from investigation import Investigation
            self.investigation = Investigation(self.session, model_id, region, transport=transport, admission=admission)

    def save_confirmations(self, old_choices, old_dictionaries):
        if not self.persistent:
            return
        from session_store import save_session
        try:
            save_session(self.session, self.session.workspace.root / '.handoff-session.json')
        except (OSError, ValueError):
            self.session.choices = old_choices
            self.session.dictionaries = old_dictionaries
            self.session.review_result = None
            raise ValueError('Could not save these confirmations; previous choices were retained') from None

    def state(self):
        return {'persistent': self.persistent, 'restore_status': self.restore_status,
                'dataset': self.dataset,
                'files': [self.session.workspace.inspect_csv(name) for name in sorted(self.session.workspace.selected)],
                'checklist': deepcopy(self.session.checklist),
                'choices': deepcopy(self.session.choices),
                'definitions': deepcopy(self.session.dictionaries),
                'review_required': True,
                'investigation_enabled': self.investigation is not None}

    def new_project(self, payload):
        from intake import create_project
        parent = self.root / 'projects'
        if parent.is_symlink():
            raise ValueError('Project directory cannot be a symbolic link')
        parent.mkdir(exist_ok=True, mode=0o700)
        project_root, session = create_project(parent, payload)
        if self.persistent:
            fd, temporary = tempfile.mkstemp(prefix='.active-', dir=self.root)
            try:
                with os.fdopen(fd, 'w') as stream:
                    json.dump({'project': project_root.name}, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.root / '.active-project.json')
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        self.session = session
        self.tools = {t.tool_name: t for t in session.tools()}
        self.dataset = 'uploaded'
        self.restore_status = 'missing'
        if self.investigation is not None:
            from investigation import Investigation
            self.investigation = Investigation(session, self.model_id, self.region, transport=self.transport, admission=self.admission)
        return self.state()

    def act(self, action, payload):
        # Local human interface invokes the actual tool wrappers. This is not an LLM.
        # Any new action invalidates the prior download, including failed exports.
        self.download = None
        self.session.tool_calls = 0
        if action == 'new_project':
            return self.new_project(payload.get('project'))
        if action == 'investigate':
            if self.investigation is None:
                raise ValueError('Live investigation is not connected; local checks remain available')
            try:
                return self.investigation.investigate(payload.get('prompt'))
            finally:
                self.session.review_result = None
        if action == 'review':
            return self.tools['check_delivery']()
        if action == 'choose':
            old_choices, old_dictionaries = deepcopy(self.session.choices), deepcopy(self.session.dictionaries)
            self.session.choose('clean-data', payload['file'])
            self.save_confirmations(old_choices, old_dictionaries)
            return self.tools['check_delivery']()
        if action == 'definitions':
            chosen = self.session.choices.get('clean-data')
            if not chosen:
                raise ValueError('Choose a version first')
            old_choices, old_dictionaries = deepcopy(self.session.choices), deepcopy(self.session.dictionaries)
            self.session.confirm_definitions(chosen, payload['meanings'])
            self.save_confirmations(old_choices, old_dictionaries)
            return self.tools['check_delivery']()
        if action == 'export':
            self.session.output_path = self.root / (str(uuid.uuid4()) + '.zip')
            result = self.tools['export_package']()
            if result['exported']:
                self.download = self.session.output_path.read_bytes()
            return {'exported': result['exported'], 'email_sent': False}
        raise ValueError('Unknown action')


def handler_for(demo, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, data, mime='application/json'):
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.headers.get('Host') not in {f'127.0.0.1:{port}', f'localhost:{port}'}:
                return self.send(403, b'{}')
            if self.path == '/':
                page = Path(__file__).with_name('interface.html').read_text().replace('__TOKEN__', demo.token)
                return self.send(200, page.encode(), 'text/html; charset=utf-8')
            if self.path == '/state':
                try:
                    return self.send(200, json.dumps(demo.state()).encode())
                except (OSError, ValueError):
                    return self.send(400, b'{"error":"Selected files could not be inspected; check the local project files."}')
            if self.path in ('/interface.js', '/interface.css'):
                return self.send(200, Path(__file__).with_name(self.path[1:]).read_bytes(),
                                 'text/javascript' if self.path.endswith('.js') else 'text/css')
            if self.path == '/delivery.zip' and demo.download:
                return self.send(200, demo.download, 'application/zip')
            self.send(404, b'{}')

        def do_POST(self):
            if (self.headers.get('Host') not in {f'127.0.0.1:{port}', f'localhost:{port}'}
                    or self.headers.get('X-Handoff-Token') != demo.token
                    or self.headers.get('Origin', f'http://127.0.0.1:{port}') not in
                       {f'http://127.0.0.1:{port}', f'http://localhost:{port}'}):
                return self.send(403, b'{}')
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 3 * 1024 * 1024:
                    raise ValueError('Invalid request size')
                payload = json.loads(self.rfile.read(size))
                if self.path != '/action' or not isinstance(payload, dict):
                    raise ValueError('Invalid request')
                if payload.get('action') != 'new_project' and size > 16384:
                    raise ValueError('Invalid request size')
                result = demo.act(payload['action'], payload)
                self.send(200, json.dumps(result).encode())
            except (ValueError, KeyError, TypeError, HandoffError) as exc:
                self.send(400, json.dumps({'error': str(exc)}).encode())
    return Handler


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--session-dir', help='Keep fictional files and confirmed choices in this local directory')
    parser.add_argument('--bedrock-model', help='Explicitly enable credit-consuming investigation with this model')
    parser.add_argument('--bedrock-region', help='AWS region for explicitly enabled investigation')
    parser.add_argument('--bedrock-transport', choices=['bedrock-runtime','bedrock-mantle'], default='bedrock-runtime')
    args = parser.parse_args()
    with ExitStack() as stack:
        if args.session_dir:
            root = Path(args.session_dir).expanduser()
            if root.is_symlink():
                raise ValueError('Session directory cannot be a symbolic link')
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
        else:
            root = stack.enter_context(tempfile.TemporaryDirectory())
        demo = Demo(root, persistent=bool(args.session_dir), model_id=args.bedrock_model, region=args.bedrock_region, transport=args.bedrock_transport)
        server = HTTPServer(('127.0.0.1', args.port), handler_for(demo, args.port))
        print(f'Handoff Desk local preview: http://127.0.0.1:{args.port}', flush=True)
        server.serve_forever()
