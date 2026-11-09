"""Poll existing deduplicated email delivery separately from WhatsApp."""
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
from .task_audit import exception_detail


def check_fellowship_mail(agent):
    """A successful check is not a send receipt. Never bypass send_once here."""
    previous=agent.store.get('fellowship_email',{})
    stage='mail_configuration';steps=[];diagnostics={};ledger=None
    try:
        agent.store.put('fellowship_email',{'status':'processing','at':now_text(),'stage':stage,**({'target_day':previous['target_day']} if previous.get('target_day') else {})})
        plist=Path.home()/'Library/LaunchAgents/com.smart_answer.fellowshipreminder.plist'
        values=plistlib.loads(plist.read_bytes()).get('EnvironmentVariables',{})
        env={**os.environ,**{key:str(value) for key,value in values.items() if key.startswith(('SMTP_','REMINDER_')) or key in {'PRODUCTION','DATA_BASE_DIR'}}}
        if Path(env.get('DATA_BASE_DIR','')).resolve()!=agent.store.root.parent.resolve():
            raise RuntimeError('Mail and assistant data directories differ')
        ledger=Path(env['DATA_BASE_DIR'])/'notification/fellowship-delivery'
        steps.append('mail_configuration_checked');stage='mail_subprocess'
        job=Path(__file__).resolve().parents[1]/'fellowship_reminder_job.py'
        result=subprocess.run([sys.executable,str(job),'--catch-up'],cwd=job.parent.parent,env=env,capture_output=True,text=True,timeout=90)
        if result.returncode:
            diagnostics={'returncode':result.returncode,'stderr':result.stderr[-6000:]}
            raise RuntimeError('Fellowship email job failed')
        steps.append('mail_job_exited_successfully');stage='mail_receipt_check'
        today=datetime.now(ZONE).date().isoformat()
        receipts=[json.loads(path.read_text()) for path in sorted(ledger.glob('*.json'))[-60:]]
        for value in receipts:
            if value.get('date','')>=today and value.get('status') in {'sending','unknown'}:
                diagnostics={'target_day':value.get('date'),'delivery_receipt':value}
                raise RuntimeError('Unresolved email delivery receipt; not resent')
        steps.append('delivery_receipts_checked')
        value={'status':'checked','at':now_text(),'stage':stage,'completed_steps':steps}
        target=previous.get('target_day')
        if target:
            value['target_day']=target
            receipt=next((r for r in receipts if r.get('date')==target and r.get('status')=='sent'),None)
            if receipt:value['completion_receipt']=receipt
        agent.store.put('fellowship_email',value)
    except Exception as exc:
        # Retain the actual subprocess failure privately before the next check can
        # replace current health. Do not expose SMTP diagnostics in group notices.
        if ledger and 'target_day' not in diagnostics:
            try:
                candidates=[json.loads(path.read_text()) for path in sorted(ledger.glob('*.json'))[-60:]]
                candidate=next((r for r in reversed(candidates) if r.get('date','')>=datetime.now(ZONE).date().isoformat() and r.get('status') in {'retryable','sending','unknown'}),None)
                if candidate:diagnostics.update(target_day=candidate.get('date'),delivery_receipt=candidate)
            except (OSError,ValueError):pass
        agent.store.put('fellowship_email',{'status':'error','at':now_text(),**exception_detail(exc),
            'stage':stage,'completed_steps':steps,**diagnostics})


def start_fellowship_mail_worker(agent):
    def run():
        while True:
            settings=agent.store.settings()
            if settings.enabled and settings.fellowship_enabled:
                check_fellowship_mail(agent)
            threading.Event().wait(60)
    thread=threading.Thread(target=run,name='fellowship-email',daemon=True);thread.start();return thread
