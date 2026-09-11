import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from admission import Admission
from hosted import Application
from live_agent import CallBudget, Limits
from investigation import Investigation, InvestigationError
from unittest.mock import Mock


class HostedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = [0]
        self.app = Application('https://demo.example', Path(self.temp.name)/'app', clock=lambda:self.now[0])
        self.addCleanup(lambda: self.app.sessions.owner.close())

    def call(self, path='/', cookie=None, token=None, payload=None, origin='https://demo.example', host='demo.example'):
        data = json.dumps(payload).encode() if payload is not None else b''
        env = {'HTTP_HOST':host, 'PATH_INFO':path, 'REQUEST_METHOD':'POST' if payload is not None else 'GET',
               'wsgi.input':io.BytesIO(data), 'CONTENT_LENGTH':str(len(data)), 'CONTENT_TYPE':'application/json', 'HTTP_ORIGIN':origin}
        if cookie: env['HTTP_COOKIE']=cookie
        if token: env['HTTP_X_HANDOFF_TOKEN']=token
        response={}
        def start(status,headers): response.update(status=status,headers=dict(headers))
        response['body']=b''.join(self.app(env,start))
        return response

    def visitor(self):
        r=self.call()
        self.assertEqual(r['status'],'200 OK')
        cookie=r['headers']['Set-Cookie'].split(';')[0]
        token=re.search(rb'name="handoff-token" content="([^"]+)"',r['body']).group(1).decode()
        return cookie,token

    def test_two_visitors_cannot_share_choices_tokens_or_downloads(self):
        a,at=self.visitor();b,bt=self.visitor()
        self.assertNotEqual(a,b);self.assertNotEqual(at,bt)
        self.assertEqual(self.call('/action',a,bt,{'action':'review'})['status'],'403 Forbidden')
        self.assertEqual(self.call('/action',a,at,{'action':'choose','file':'final_v2.csv'})['status'],'200 OK')
        self.assertEqual(json.loads(self.call('/state',b)['body'])['choices'],{})
        self.call('/action',a,at,{'action':'definitions','meanings':{'id':'ID','period':'Month','amount':'USD'}})
        self.call('/action',a,at,{'action':'export'})
        self.assertEqual(self.call('/delivery.zip',a)['status'],'200 OK')
        self.assertEqual(self.call('/delivery.zip',b)['status'],'404 Not Found')
        self.assertEqual(self.call('/delivery.zip')['status'],'401 Unauthorized')

    def test_origin_host_and_unknown_cookie(self):
        a,t=self.visitor()
        self.assertEqual(self.call('/action',a,t,{'action':'review'},origin='https://evil.example')['status'],'403 Forbidden')
        self.assertEqual(self.call('/state',a,host='evil.example')['status'],'403 Forbidden')
        self.assertEqual(self.call('/state','__Host-handoff=guessed')['status'],'401 Unauthorized')

    def test_investigation_errors_are_actionable_and_sanitized_over_http(self):
        cookie,token=self.visitor()
        demo=next(iter(self.app.sessions.entries.values())).demo
        for exhausted in (False,True):
            budget=CallBudget(Limits())
            if exhausted:budget.calls=6
            def run(prompt):
                event=SimpleNamespace(projected_input_tokens=20,cancel=None)
                budget.before_model(event)
                raise RuntimeError('secret-provider-auth-value')
            demo.investigation=Investigation(demo.session,'model','region',Mock(return_value=(run,budget)))
            response=self.call('/action',cookie,token,{'action':'investigate','prompt':'Check files'})
            expected='model_call_limit' if exhausted else 'service_unavailable'
            self.assertEqual(response['status'],'400 Bad Request')
            self.assertEqual(json.loads(response['body']),{'error':InvestigationError.MESSAGES[expected],'error_code':expected})
            self.assertNotIn(b'secret',response['body'])

    def test_expiration_deletes_files(self):
        a,t=self.visitor();entry=next(iter(self.app.sessions.entries.values()));path=entry.root
        self.now[0]=3601
        self.assertEqual(self.call('/state',a)['status'],'401 Unauthorized')
        self.assertFalse(path.exists())

    def test_new_project_is_private_and_hosted_state(self):
        a,t=self.visitor();b,_=self.visitor()
        project={'files':[{'name':'own.csv','content':'id\n001\n'}],'required_columns':['id']}
        r=self.call('/action',a,t,{'action':'new_project','project':project})
        self.assertTrue(json.loads(r['body'])['hosted'])
        self.assertEqual(json.loads(self.call('/state',a)['body'])['files'][0]['file'],'own.csv')
        self.assertNotIn('own.csv',[x['file'] for x in json.loads(self.call('/state',b)['body'])['files']])

    def test_storage_and_action_capacity(self):
        self.app.sessions.maximum=1
        a,t=self.visitor()
        self.assertEqual(self.call()['status'],'503 Service Unavailable')
        entry=next(iter(self.app.sessions.entries.values()));entry.actions=120
        self.assertEqual(self.call('/action',a,t,{'action':'review'})['status'],'429 Too Many Requests')

    def test_restart_removes_orphans_preserves_allowance_and_invalidates_cookie(self):
        cookie,_=self.visitor()
        visitor=next(iter(self.app.sessions.entries.values())).root
        outside=Path(self.temp.name)/'preserved.txt';outside.write_text('keep')
        (visitor/'external-link').symlink_to(outside)
        root=self.app.sessions.root.parent
        database=root/'model-allowance.sqlite'
        self.assertTrue(Admission(database,1).reserve())
        before=database.read_bytes()
        # Simulate the old process exiting; its OS lock is then released.
        self.app.sessions.owner.close()
        self.app=Application('https://demo.example',root)
        self.assertFalse(visitor.exists())
        self.assertEqual(outside.read_text(),'keep')
        self.assertEqual(database.read_bytes(),before)
        self.assertFalse(Admission(database,1).reserve())
        self.assertEqual(self.call('/state',cookie)['status'],'401 Unauthorized')
        self.visitor()

    def test_second_worker_cannot_clean_live_sessions(self):
        self.visitor()
        visitor=next(iter(self.app.sessions.entries.values())).root
        with self.assertRaises(BlockingIOError):
            Application('https://demo.example',self.app.sessions.root.parent)
        self.assertTrue(visitor.is_dir())

    def test_startup_rejects_unexpected_or_symlinked_session_entries(self):
        self.visitor()
        visitor=next(iter(self.app.sessions.entries.values())).root
        root=self.app.sessions.root
        self.app.sessions.owner.close()
        outside=Path(self.temp.name)/'outside';outside.mkdir()
        (outside/'keep').write_text('preserve')
        for name,symlink in [('unrelated',False),('visitor-abcdefgh',True)]:
            with self.subTest(name=name):
                suspect=root/name
                if symlink:suspect.symlink_to(outside,target_is_directory=True)
                else:suspect.write_text('unrelated')
                with self.assertRaises(ValueError):
                    Application('https://demo.example',root.parent)
                self.assertTrue(visitor.is_dir())
                self.assertEqual((outside/'keep').read_text(),'preserve')
                suspect.unlink()


class AdmissionTests(unittest.TestCase):
    def test_concurrent_reservations_and_restart_do_not_reset(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'calls.sqlite';budget=Admission(path,7)
            with ThreadPoolExecutor(max_workers=10) as pool:
                accepted=list(pool.map(lambda _:budget.reserve(),range(30)))
            self.assertEqual(sum(accepted),7)
            self.assertFalse(Admission(path,7).reserve())
            with self.assertRaises(ValueError):Admission(path,8)

    def test_new_agent_budgets_share_allowance_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            admission=Admission(Path(root)/'calls.sqlite',1)
            first=CallBudget(Limits(),admission=admission.reserve)
            second=CallBudget(Limits(),admission=admission.reserve)
            event=SimpleNamespace(projected_input_tokens=10,cancel=None)
            first.before_model(event);self.assertIsNone(event.cancel)
            second.begin_turn();second.before_model(event)
            self.assertIsNotNone(event.cancel);self.assertEqual(second.calls,0)
            def broken():raise OSError('do not expose provider or filesystem details')
            broken_budget=CallBudget(Limits(),admission=broken)
            event=SimpleNamespace(projected_input_tokens=10,cancel=None)
            broken_budget.before_model(event)
            self.assertIsNotNone(event.cancel);self.assertEqual(broken_budget.calls,0)
