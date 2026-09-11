"""Visitor-isolated WSGI gateway, intended behind an HTTPS reverse proxy.

Importing constructs no application and invokes no model. Local preview remains
server.py; hosted startup requires an explicit public HTTPS origin and data root.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.cookies import SimpleCookie, CookieError
from pathlib import Path
from urllib.parse import urlsplit
import json
import os
import secrets
import shutil
import tempfile
import threading
import time

from server import Demo
from core import HandoffError

COOKIE = '__Host-handoff'


@dataclass
class Visitor:
    demo: Demo
    root: Path
    touched: float
    lock: object = field(default_factory=threading.Lock)
    users: int = 0
    projects: int = 0
    actions: int = 0


class Sessions:
    def __init__(self, root, factory, maximum=32, ttl=3600, clock=time.monotonic):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink() or self.root.stat().st_mode & 0o077:
            raise ValueError('Session root must be a private directory')
        self.factory, self.maximum, self.ttl, self.clock = factory, maximum, ttl, clock
        self.entries = {}
        self.lock = threading.Lock()

    def sweep(self):
        with self.lock:
            now = self.clock()
            for key, entry in list(self.entries.items()):
                if not entry.users and now-entry.touched >= self.ttl:
                    shutil.rmtree(entry.root)
                    del self.entries[key]

    @contextmanager
    def lease(self, identifier=None, create=False):
        with self.lock:
            now = self.clock()
            for key, entry in list(self.entries.items()):
                if not entry.users and now-entry.touched >= self.ttl:
                    shutil.rmtree(entry.root)
                    del self.entries[key]
            entry = self.entries.get(identifier)
            if entry is None:
                if not create:
                    raise PermissionError('Session expired; reload the page')
                if len(self.entries) >= self.maximum:
                    raise OverflowError('Demo capacity is full; try again later')
                root = Path(tempfile.mkdtemp(prefix='visitor-', dir=self.root))
                try:
                    entry = Visitor(self.factory(root), root, now)
                except Exception:
                    shutil.rmtree(root)
                    raise
                identifier = secrets.token_urlsafe(32)
                self.entries[identifier] = entry
            entry.users += 1
        try:
            with entry.lock:
                yield identifier, entry
        finally:
            with self.lock:
                entry.users -= 1
                entry.touched = self.clock()


class Application:
    def __init__(self, origin, root, *, model_id=None, region=None,
                 transport='bedrock-mantle', maximum_model_calls=None,
                 factory=None, clock=time.monotonic):
        parsed = urlsplit(origin)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError('An exact HTTPS public origin without a path is required')
        self.origin, self.host = origin, parsed.netloc
        root = Path(root)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.is_symlink() or root.stat().st_mode & 0o077:
            raise ValueError('Application data root must be private')
        admission = None
        if model_id or region:
            from admission import Admission
            admission = Admission(root/'model-allowance.sqlite', maximum_model_calls).reserve
        self.sessions = Sessions(root/'sessions', factory or (lambda path: Demo(
            path, model_id=model_id, region=region, transport=transport, admission=admission)), clock=clock)
        self.inference = threading.BoundedSemaphore(1)

    def __call__(self, environ, start_response):
        headers = [('Cache-Control','no-store'), ('X-Content-Type-Options','nosniff'),
                   ('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'"),
                   ('Referrer-Policy','no-referrer')]
        def reply(status, value, mime='application/json'):
            data = value if isinstance(value, bytes) else json.dumps(value).encode()
            start_response(status, headers+[('Content-Type',mime), ('Content-Length',str(len(data)))])
            return [data]
        if environ.get('HTTP_HOST') != self.host:
            return reply('403 Forbidden', {'error':'Invalid request origin'})
        method, path = environ.get('REQUEST_METHOD'), environ.get('PATH_INFO')
        if method == 'GET' and path in ('/interface.js','/interface.css'):
            return reply('200 OK', Path(__file__).with_name(path[1:]).read_bytes(),
                         'text/javascript' if path.endswith('.js') else 'text/css')
        if method not in ('GET','POST'):
            return reply('405 Method Not Allowed', {'error':'Unsupported method'})
        if path not in ('/','/state','/delivery.zip','/action'):
            return reply('404 Not Found', {'error':'Not found'})
        try:
            cookies = SimpleCookie()
            cookies.load(environ.get('HTTP_COOKIE',''))
            identifier = cookies[COOKIE].value if COOKIE in cookies else None
            with self.sessions.lease(identifier, create=method=='GET' and path=='/') as (key, visitor):
                demo = visitor.demo
                if method == 'GET':
                    if path == '/':
                        headers.append(('Set-Cookie', f'{COOKIE}={key}; Path=/; Secure; HttpOnly; SameSite=Strict'))
                        page = Path(__file__).with_name('interface.html').read_text().replace('__TOKEN__', demo.token)
                        return reply('200 OK', page.encode(), 'text/html; charset=utf-8')
                    if path == '/state':
                        state = demo.state()
                        state.update(hosted=True, retention_seconds=self.sessions.ttl)
                        return reply('200 OK', state)
                    if path == '/delivery.zip' and demo.download:
                        return reply('200 OK', demo.download, 'application/zip')
                    return reply('404 Not Found', {'error':'Not found'})
                if path != '/action' or environ.get('HTTP_ORIGIN') != self.origin or not secrets.compare_digest(environ.get('HTTP_X_HANDOFF_TOKEN',''), demo.token):
                    return reply('403 Forbidden', {'error':'Invalid request origin or session token'})
                if environ.get('CONTENT_TYPE','').split(';')[0] != 'application/json':
                    return reply('415 Unsupported Media Type', {'error':'JSON required'})
                size = int(environ.get('CONTENT_LENGTH','0'))
                if not 0 < size <= 3*1024*1024:
                    raise ValueError('Invalid request size')
                body = environ['wsgi.input'].read(size)
                if len(body) != size:
                    raise ValueError('Incomplete request')
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError('Invalid request')
                action = payload.get('action')
                if action != 'new_project' and size > 16384:
                    raise ValueError('Invalid request size')
                if visitor.actions >= 120 or (action=='new_project' and visitor.projects >= 8):
                    return reply('429 Too Many Requests', {'error':'Session action allowance reached'})
                visitor.actions += 1
                if action == 'new_project':
                    visitor.projects += 1
                if action == 'investigate':
                    if not self.inference.acquire(blocking=False):
                        return reply('429 Too Many Requests', {'error':'Another investigation is running; try later'})
                    try:
                        result = demo.act(action, payload)
                    finally:
                        self.inference.release()
                else:
                    result = demo.act(action, payload)
                if action == 'new_project':
                    result.update(hosted=True, retention_seconds=self.sessions.ttl)
                return reply('200 OK', result)
        except PermissionError:
            return reply('401 Unauthorized', {'error':'Session expired; reload the page'})
        except OverflowError:
            return reply('503 Service Unavailable', {'error':'Demo capacity is full; try later'})
        except (ValueError, KeyError, TypeError, CookieError, HandoffError):
            return reply('400 Bad Request', {'error':'Request could not be completed; check the supplied files and fields'})
        except Exception:
            return reply('503 Service Unavailable', {'error':'Demo temporarily unavailable'})


def create_from_environment():
    """Gunicorn factory: one worker only; no model calls during initialization."""
    origin = os.environ['HANDOFF_PUBLIC_ORIGIN']
    root = os.environ['HANDOFF_DATA_ROOT']
    model = os.environ.get('HANDOFF_MODEL')
    region = os.environ.get('HANDOFF_REGION')
    maximum = int(os.environ['HANDOFF_MODEL_CALLS']) if model or region else None
    app = Application(origin, root, model_id=model, region=region, maximum_model_calls=maximum)
    # Cleanup is independent of incoming traffic. Restart recovery of expired
    # orphan directories still requires the operator's retention job.
    def cleanup():
        while True:
            time.sleep(60)
            try:
                app.sessions.sweep()
            except OSError:
                pass
    threading.Thread(target=cleanup, daemon=True, name='handoff-expiry').start()
    return app
