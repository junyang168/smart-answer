from datetime import datetime, timezone
import json
from pathlib import Path
import plistlib
from types import SimpleNamespace

import pytest

from backend.sunday_assistant.admin_monitor import AdminMonitor, monitor_state
from backend.sunday_assistant.store import Store
from backend.sunday_assistant.gmail_inbox import GmailInbox
from backend.sunday_assistant.fellowship_mail import check_fellowship_mail
from backend.tests.test_sunday_admin_monitor import Client, CONFIG, ENV, NOW


@pytest.fixture
def setup(tmp_path):
    store=Store(tmp_path/'data/sunday-assistant')
    store.put('settings',{**CONFIG,'fellowship_enabled':True})
    store.put('worker',{'status':'idle','at':datetime.fromtimestamp(NOW,timezone.utc).isoformat()})
    monitor=AdminMonitor(store.root,env=ENV,client=Client(),started=NOW)
    return store,monitor


def incident(monitor):
    return monitor_state(monitor.root)['incidents'][0]


def events(store):
    with store.db() as db:return [dict(r) for r in db.execute('SELECT * FROM task_events ORDER BY id')]


def fail_mail(store):
    store.put('fellowship_email',{'status':'error','at':'then','error_type':'TimeoutExpired',
        'error':'SMTP secret private-source-message','stderr':'private SMTP traceback',
        'stage':'mail_subprocess','completed_steps':['mail_configuration_checked'],'target_day':'2027-03-05'})


def test_fast_failure_and_successful_check_are_durable_even_between_monitor_polls(setup):
    store,monitor=setup
    fail_mail(store)
    store.put('fellowship_email',{'status':'checked','at':'later'})
    monitor.tick(NOW)
    saved=incident(monitor)
    assert saved['active']==0 and saved['resolution']=='unverified'
    assert saved['details'][0]['diagnostic']['stderr']=='private SMTP traceback'
    assert saved['details'][0]['diagnostic']['completed_steps']==['mail_configuration_checked']
    assert saved['details'][0]['phase']=='health_only'
    assert len(monitor.client.sent)==1
    text=monitor.client.sent[0]['text']
    assert '團契通知郵件' in text and '逾時' in text and '仍待核實' in text
    assert 'secret' not in text and 'private' not in text
    reopened=AdminMonitor(store.root,env=ENV,client=monitor.client,started=NOW)
    reopened.tick(NOW+10)
    assert len(monitor.client.sent)==1
    assert 'private SMTP traceback' in json.dumps(incident(reopened))


def test_normal_check_is_not_completion_and_late_matching_receipt_completes_once(setup):
    store,monitor=setup
    fail_mail(store);monitor.tick(NOW)
    assert incident(monitor)['active']==1
    store.put('fellowship_email',{'status':'checked','target_day':'2027-03-05'})
    monitor.tick(NOW+10)
    assert '完成仍待核實' in monitor.client.sent[-1]['text']
    assert incident(monitor)['resolution']=='unverified'
    # An unrelated sent mail is not evidence that this failed mail was sent.
    store.put('fellowship_email',{'status':'checked','completion_receipt':{'status':'sent','date':'2027-03-12'}})
    monitor.tick(NOW+20)
    assert len(monitor.client.sent)==2
    store.put('fellowship_email',{'status':'checked','completion_receipt':{'status':'sent','date':'2027-03-05','attempts':2}})
    monitor.tick(NOW+30);monitor.tick(NOW+40)
    assert len(monitor.client.sent)==3 and '補做結果已核實' in monitor.client.sent[-1]['text']
    assert incident(monitor)['resolution']=='completed'
    assert events(store)[0]['phase']=='failure' and 'secret' in events(store)[0]['detail']


def test_failure_journal_rolls_back_with_task_write(setup):
    store,_=setup
    with pytest.raises(RuntimeError),store.db() as db:
        db.execute("INSERT INTO kv VALUES('fellowship_email',?)",(json.dumps({'status':'error','error':'not committed'}),))
        raise RuntimeError('transaction failed')
    assert not events(store)
    assert store.path.stat().st_mode&0o777==0o600


def test_ppt_retry_requires_actual_saved_artifact_receipt(setup):
    store,monitor=setup
    with store.db() as db:
        db.execute("INSERT INTO ppt_jobs VALUES('2027-03-07','v','error',NULL,'render failed','then')")
    monitor.tick(NOW)
    with store.db() as db:db.execute("UPDATE ppt_jobs SET status='generating',error=NULL")
    monitor.tick(NOW+10)
    assert incident(monitor)['resolution']=='unverified'
    assert incident(monitor)['active']==1 and len(monitor.client.sent)==1
    with store.db() as db:db.execute("UPDATE ppt_jobs SET status='ready',artifact_sha='verified-sha'")
    monitor.tick(NOW+20)
    assert incident(monitor)['resolution']=='completed'
    assert incident(monitor)['details'][0]['outcome']['artifact_sha']=='verified-sha'


def test_partial_message_result_and_failure_survive_later_status_changes(setup):
    store,monitor=setup
    with store.db() as db:
        db.execute("INSERT INTO messages(id,payload,status,received_at,result,error) VALUES('m','{}','error','then',?,'failed after patch')",(json.dumps({'saved':['title']}),))
    monitor.tick(NOW)
    with store.db() as db:db.execute("UPDATE messages SET status='review',error=NULL")
    monitor.tick(NOW+10)
    detail=incident(monitor)['details'][0]
    assert json.loads(detail['diagnostic']['result'])=={'saved':['title']}
    assert detail['phase']=='health_only'
    with store.db() as db:
        db.execute("INSERT INTO proposals(message_id,payload,status,updated_at) VALUES('m','{}','applied','now')")
        db.execute("UPDATE messages SET status='done',result=?",(json.dumps({'status':'applied','saved':['title','scripture']}),))
    monitor.tick(NOW+20)
    assert incident(monitor)['resolution']=='completed'


def test_mail_database_also_retains_overwritten_errors_without_monitor_poll(setup):
    store,monitor=setup
    inbox=GmailInbox(SimpleNamespace(store=store))
    inbox.put('status:private@example.com',{'status':'error','error':'private token','error_type':'ConnectError'})
    inbox.put('status:private@example.com',{'status':'ready','error':None})
    monitor.tick(NOW)
    assert incident(monitor)['details'][0]['label']=='Gmail 信箱讀取'
    assert 'private@example.com' not in monitor.client.sent[0]['text']
    assert 'private token' not in monitor.client.sent[0]['text']


def test_mail_check_saves_subprocess_diagnostics_but_noop_is_not_delivery(setup,monkeypatch,tmp_path):
    store,monitor=setup
    home=tmp_path/'home';folder=home/'Library/LaunchAgents';folder.mkdir(parents=True)
    (folder/'com.smart_answer.fellowshipreminder.plist').write_bytes(plistlib.dumps({'EnvironmentVariables':{'DATA_BASE_DIR':str(store.root.parent)}}))
    monkeypatch.setattr(Path,'home',lambda:home)
    calls=[]
    def failed(command,**kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1,stderr='Actual private subprocess exception')
    monkeypatch.setattr('backend.sunday_assistant.fellowship_mail.subprocess.run',failed)
    agent=SimpleNamespace(store=store)
    check_fellowship_mail(agent)
    saved=store.get('fellowship_email')
    assert saved['returncode']==1 and saved['stderr']=='Actual private subprocess exception'
    assert saved['completed_steps']==['mail_configuration_checked']
    monkeypatch.setattr('backend.sunday_assistant.fellowship_mail.subprocess.run',lambda *a,**k:SimpleNamespace(returncode=0,stderr=''))
    check_fellowship_mail(agent);monitor.tick(NOW)
    assert incident(monitor)['resolution']=='unverified'
    assert calls[0][-1]=='--catch-up'  # Existing deduplicated delivery path only.


def test_deferred_or_only_partially_applied_message_is_never_completed(setup):
    store,monitor=setup
    with store.db() as db:
        db.execute("INSERT INTO messages(id,payload,status,received_at,error) VALUES('m','{}','error','then','failure')")
    monitor.tick(NOW)
    with store.db() as db:
        db.execute("INSERT INTO proposals(message_id,payload,status,updated_at) VALUES('m',?,'applied','now')",(json.dumps({'partial_action':True}),))
        db.execute("UPDATE messages SET status='done',result=?",(json.dumps({'disposition':'deferred','form_changed':False}),))
    monitor.tick(NOW+10)
    assert incident(monitor)['resolution']=='unverified'
    assert '補做結果已核實' not in monitor.client.sent[-1]['text']


def test_successful_second_retry_finishes_same_task_without_rewriting_first_failure(setup):
    store,monitor=setup
    with store.db() as db:db.execute("INSERT INTO ppt_jobs VALUES('2027-03-07','v','error',NULL,'first error','then')")
    monitor.tick(NOW)
    with store.db() as db:db.execute("UPDATE ppt_jobs SET status='generating',error=NULL")
    with store.db() as db:db.execute("UPDATE ppt_jobs SET status='error',error='second error'")
    monitor.tick(NOW+10)
    with store.db() as db:db.execute("UPDATE ppt_jobs SET status='ready',error=NULL,artifact_sha='verified'")
    monitor.tick(NOW+20)
    saved=incident(monitor)
    assert saved['resolution']=='completed'
    assert len(saved['details'])==2
    assert [d['diagnostic']['error'] for d in saved['details']]==['first error','second error']


def test_monitor_migration_keeps_old_incident_writer_compatible(setup):
    store,monitor=setup
    with monitor.db() as db:
        assert len(db.execute('PRAGMA table_info(incidents)').fetchall())==7
        db.execute("INSERT INTO incidents VALUES('old','worker','old worker',0,1,0,0)")
    assert monitor_state(store.root)['incidents'][0]['details']==[]


def test_ready_check_only_completes_ppt_when_same_day_and_version_have_artifact(setup):
    store,monitor=setup
    store.put('ppt_readiness',{'date':'2027-03-07','version':'v','status':'error','error':'failed'})
    monitor.tick(NOW)
    store.put('ppt_readiness',{'date':'2027-03-07','version':'v','status':'ready'})
    monitor.tick(NOW+10)
    assert incident(monitor)['resolution']=='unverified'
    with store.db() as db:db.execute("INSERT INTO ppt_jobs VALUES('2027-03-07','v','ready','verified-sha',NULL,'later')")
    with store.db() as db:db.execute("INSERT INTO ppt_jobs VALUES('2027-03-07','other','ready','different-input-sha',NULL,'later')")
    store.put('ppt_readiness',{'date':'2027-03-07','version':'other','status':'ready'})
    monitor.tick(NOW+20)
    assert incident(monitor)['resolution']=='unverified'
    store.put('ppt_readiness',{'date':'2027-03-07','version':'v','status':'ready'})
    monitor.tick(NOW+30)
    assert incident(monitor)['resolution']=='completed'


def test_multiple_failed_attempts_and_completion_between_polls_are_one_completed_task(setup):
    store,monitor=setup
    with store.db() as db:
        db.execute("INSERT INTO ppt_jobs VALUES('2027-03-07','v','error',NULL,'first failure','then')")
        db.execute("UPDATE ppt_jobs SET status='generating'")
        db.execute("UPDATE ppt_jobs SET status='error',error='second failure'")
        db.execute("UPDATE ppt_jobs SET status='ready',artifact_sha='verified'")
    monitor.tick(NOW);monitor.tick(NOW+10)
    saved=incident(monitor)
    assert saved['resolution']=='completed' and not saved['active']
    assert saved['count']==1 and len(saved['details'])==2
    assert len(monitor.client.sent)==1 and '完成回執' in monitor.client.sent[0]['text']
