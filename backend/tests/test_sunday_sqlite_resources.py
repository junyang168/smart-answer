"""Connection lifetimes must not depend on cyclic GC in long-running workers."""
import subprocess
import sys

from backend.tests.test_sunday_assistant import agent


def test_store_monitor_and_mail_polling_fit_under_launchd_fd_limit(tmp_path):
    # A child process prevents changing the runner's GC or resource limits.
    program = '''
import gc,resource,sys
from pathlib import Path
from backend.sunday_assistant.store import Store
from backend.sunday_assistant.admin_monitor import AdminMonitor
from backend.sunday_assistant.gmail_inbox import GmailInbox
from types import SimpleNamespace
root=Path(sys.argv[1]);store=Store(root)
store.put('settings',{'enabled':False});store.put('probe',1)
monitor=AdminMonitor(root,env={})
mail=GmailInbox(SimpleNamespace(store=store))
gc.disable()
soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE)
resource.setrlimit(resource.RLIMIT_NOFILE,(min(128,hard),hard))
for _ in range(300):
    assert store.get('probe')==1
    monitor.settings_snapshot()
    with monitor.db() as db: db.execute('SELECT COUNT(*) FROM notices').fetchone()
    mail.put('probe',1);assert mail.get('probe')==1
try:
    with store.db() as db:
        db.execute("INSERT INTO kv VALUES ('rolled-back','1')")
        raise ValueError('rollback')
except ValueError: pass
assert store.get('rolled-back') is None
# Every context releases its handle on both normal and exceptional exits.
with store.db() as db: pass
import sqlite3
try: db.execute('SELECT 1')
except sqlite3.ProgrammingError: pass
else: raise AssertionError('store connection stayed open')
print('300 cycles completed with GC disabled and 128 file descriptors')
'''
    result=subprocess.run([sys.executable,'-c',program,str(tmp_path)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert '300 cycles completed' in result.stdout


def test_named_spouse_absence_with_confirmed_short_alias(agent):
    from backend.api.models import SundayWorker
    from backend.sunday_assistant.models import ChatMessage,Extraction
    for name in ('王冬丽','余克宇'):
        agent.repository.workers[name]=SundayWorker(name=name)
    settings=agent.store.settings()
    settings.sender_workers={'sender':['王冬丽']}
    settings.households=[['王冬丽','余克宇']]
    settings.worker_aliases={'Lily':'王冬丽','Harry Yue':'余克宇','Harry':'余克宇'}
    agent.store.put('settings',settings.model_dump())
    text='我和Harry12月1日至12月6日外出，请帮我们写上不能服事'
    message=ChatMessage(message_id='named-spouse',group_id=settings.availability_group,sender_id='sender',sent_at='2026-11-03T01:31:28Z',text=text)
    request=Extraction.model_validate({'absence_changes':[{'worker':name,'start':'2026-12-01','end':'2026-12-06','evidence':text} for name in ('王冬丽','余克宇')]})
    plan=agent.build_plan(message,request)
    assert not plan['questions']
    assert {item['worker'] for item in plan['operations']}=={'王冬丽','余克宇'}
    # Without an explicit alias, the spouse's identity is not guessed.
    del settings.worker_aliases['Harry'];agent.store.put('settings',settings.model_dump())
    assert any('余克宇' in q for q in agent.build_plan(message,request)['questions'])
