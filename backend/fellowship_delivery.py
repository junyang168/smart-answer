"""Shared durable send reservation; uncertain SMTP delivery never auto-repeats."""
from contextlib import contextmanager, closing
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import smtplib
import socket
import sqlite3
import tempfile

try:
    from .fellowship_reminder_status import load_last_sent_fellowship_date,resolve_fellowship_reminder_status_file,record_fellowship_email_sent
except ImportError:
    from fellowship_reminder_status import load_last_sent_fellowship_date,resolve_fellowship_reminder_status_file,record_fellowship_email_sent


def write_private(path,value):
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.delivery-')
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(value,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
        os.chmod(tmp,0o600);Path(tmp).replace(path)
    finally:Path(tmp).unlink(missing_ok=True)


def send_once(day,action,*,status_file=None,force=False):
    status_file=Path(status_file or resolve_fellowship_reminder_status_file())
    root=status_file.parent/'fellowship-delivery';root.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=root/(day.isoformat()+'.json')
    with (root/'send.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        saved=json.loads(path.read_text()) if path.exists() else {}
        if not force and (load_last_sent_fellowship_date(status_file)==day.isoformat() or saved.get('status')=='sent'):
            return False
        if not force and saved.get('status') in {'sending','unknown'}:
            raise RuntimeError('先前團契郵件的寄送結果不明，請管理員核對郵件紀錄；未自動重寄')
        attempts=saved.get('attempts',0)+1
        if attempts>3 and not force:
            raise RuntimeError('團契郵件連線已重試三次，請管理員協助；未繼續重試')
        value={'date':day.isoformat(),'status':'sending','attempts':attempts,'at':datetime.now().astimezone().isoformat()}
        write_private(path,value)
        try:action()
        except Exception as exc:
            # These failures occur before SMTP accepts any recipient/message.
            retryable=isinstance(exc,(ConnectionRefusedError,socket.gaierror,smtplib.SMTPConnectError,smtplib.SMTPAuthenticationError))
            write_private(path,{**value,'status':'retryable' if retryable else 'unknown','error_type':type(exc).__name__})
            raise
        record_fellowship_email_sent(day,status_file)
        write_private(path,{**value,'status':'sent'})
        return True


def ready_for_email(event,base_dir):
    if not event.host or not event.title or not event.series:
        return False
    db_path=Path(base_dir)/'sunday-assistant/assistant.sqlite3'
    if not db_path.exists():return True
    with closing(sqlite3.connect(f'file:{db_path}?mode=ro',uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fellowship_cases'").fetchone():return True
        case=db.execute('SELECT data FROM fellowship_cases WHERE target_day=?',(event.date.isoformat(),)).fetchone()
    if not case:return True
    data=json.loads(case[0])
    confirmed=data.get('confirmed',{})
    optional={'sequence':event.sequence}
    return not data.get('questions') and all(value or (key in confirmed and confirmed[key]==value) for key,value in optional.items())


def catch_up_allowed(base_dir):
    path=Path(base_dir)/'sunday-assistant/assistant.sqlite3'
    if not path.exists():return True  # Explicit standalone CLI use.
    with closing(sqlite3.connect(f'file:{path}?mode=ro',uri=True)) as db:
        row=db.execute("SELECT value FROM kv WHERE key='settings'").fetchone()
    settings=json.loads(row[0]) if row else {}
    return bool(settings.get('enabled') and settings.get('fellowship_enabled'))
