"""Poll the existing email job separately; slow SMTP cannot stall WhatsApp."""
from pathlib import Path
import os
import json
from datetime import datetime
import plistlib
import subprocess
import sys
import threading

from .models import ZONE
from .store import now_text


def start_fellowship_mail_worker(agent):
    def run():
        while True:
            settings=agent.store.settings()
            if settings.enabled and settings.fellowship_enabled:
                try:
                    # Use the existing notification service's protected production
                    # mail settings; never switch it to a developer/test recipient.
                    plist=Path.home()/'Library/LaunchAgents/com.smart_answer.fellowshipreminder.plist'
                    values=plistlib.loads(plist.read_bytes()).get('EnvironmentVariables',{})
                    env={**os.environ,**{key:str(value) for key,value in values.items() if key.startswith(('SMTP_','REMINDER_')) or key in {'PRODUCTION','DATA_BASE_DIR'}}}
                    if Path(env.get('DATA_BASE_DIR','')).resolve()!=agent.store.root.parent.resolve():
                        raise RuntimeError('Mail and assistant data directories differ')
                    job=Path(__file__).resolve().parents[1]/'fellowship_reminder_job.py'
                    result=subprocess.run([sys.executable,str(job),'--catch-up'],cwd=job.parent.parent,env=env,capture_output=True,text=True,timeout=90)
                    if result.returncode:
                        raise RuntimeError('Fellowship email job failed')
                    ledger=Path(env['DATA_BASE_DIR'])/'notification/fellowship-delivery'
                    today=datetime.now(ZONE).date().isoformat()
                    for path in sorted(ledger.glob('*.json'))[-60:]:
                        value=json.loads(path.read_text())
                        if value.get('date','')>=today and value.get('status') in {'sending','unknown'}:
                            raise RuntimeError('Unresolved email delivery receipt')
                    agent.store.put('fellowship_email',{'status':'checked','at':now_text()})
                except Exception:
                    agent.store.put('fellowship_email',{'status':'error','at':now_text(),'error':'團契通知郵件執行失敗，請核對受保護的寄送紀錄；結果不明時未重寄'})
            threading.Event().wait(60)
    thread=threading.Thread(target=run,name='fellowship-email',daemon=True);thread.start();return thread
