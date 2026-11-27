"""Append-only task failures and receipt-bound outcomes, in the write transaction.

Raw diagnostics stay in the private databases. Group notices use fixed labels,
never exception text, source messages, mailbox addresses or document URLs.
"""
from contextlib import closing
import json
import sqlite3
import traceback

LABELS = {
    'worker': '群消息處理程序', 'gmail_worker': '講員郵件接收程序',
    'fellowship_email': '團契通知郵件', 'ppt_readiness': '主日 PPT 準備',
    'message': '群消息登記', 'ppt_job': '主日 PPT 生成',
    'gmail_poll': 'Gmail 信箱讀取', 'mail_message': '講員郵件處理',
    'mail_file': '講員 PPT 歸檔',
}


def exception_detail(exc):
    return {'error_type': type(exc).__name__, 'error': str(exc)[:2000],
            'traceback': ''.join(traceback.format_exception(exc))[-10000:]}


def install_audit(db, *, mail=False):
    db.execute('''CREATE TABLE IF NOT EXISTS task_events(
      id INTEGER PRIMARY KEY,task_key TEXT NOT NULL,task_type TEXT NOT NULL,
      phase TEXT NOT NULL,attempt_id INTEGER,at TEXT NOT NULL,detail TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS task_events_key ON task_events(task_key,id)')
    if mail:
        specs = [
            ('mail_kv', "'poll:'||NEW.key", "'gmail_poll'", "NEW.key LIKE 'status:%'", "json_extract(NEW.value,'$.status')", 'NEW.value', "0"),
            ('mail_messages', "'message:'||NEW.key", "'mail_message'", '1', 'NEW.status',
             "json_object('status',NEW.status,'error',NEW.error,'updated',NEW.updated)", '0'),
            ('mail_files', "'file:'||NEW.id", "'mail_file'", '1', 'NEW.status',
             "json_object('status',NEW.status,'error',NEW.error,'plan',NEW.plan,'sha',NEW.sha,'archive',NEW.archive,'updated',NEW.updated)",
             "NEW.status='imported' AND length(NEW.sha)>0 AND length(NEW.archive)>0"),
        ]
    else:
        specs = [
            ('kv', "'kv:'||NEW.key", 'NEW.key', "NEW.key IN ('worker','gmail_worker','fellowship_email','ppt_readiness')",
             "json_extract(NEW.value,'$.status')", 'NEW.value',
             "(NEW.key='fellowship_email' AND json_extract(NEW.value,'$.completion_receipt.status')='sent' AND json_extract(NEW.value,'$.completion_receipt.date')=json_extract((SELECT detail FROM task_events WHERE task_key='kv:'||NEW.key AND phase='failure' ORDER BY id DESC LIMIT 1),'$.target_day')) OR (NEW.key='ppt_readiness' AND json_extract(NEW.value,'$.status')='ready' AND json_extract(NEW.value,'$.date')=json_extract((SELECT detail FROM task_events WHERE task_key='kv:'||NEW.key AND phase='failure' ORDER BY id DESC LIMIT 1),'$.date') AND json_extract(NEW.value,'$.version')=json_extract((SELECT detail FROM task_events WHERE task_key='kv:'||NEW.key AND phase='failure' ORDER BY id DESC LIMIT 1),'$.version') AND EXISTS(SELECT 1 FROM ppt_jobs WHERE day=json_extract(NEW.value,'$.date') AND version=json_extract(NEW.value,'$.version') AND status='ready' AND length(artifact_sha)>0))"),
            ('messages', "'message:'||NEW.id", "'message'", '1', 'NEW.status',
             "json_object('status',NEW.status,'error',NEW.error,'result',NEW.result,'attempts',NEW.attempts,'proposals',(SELECT json_group_array(json_object('id',id,'status',status,'payload',payload,'error',error)) FROM proposals WHERE message_id=NEW.id))",
             "NEW.status='done' AND EXISTS(SELECT 1 FROM proposals WHERE message_id=NEW.id AND status='applied' AND COALESCE(json_extract(payload,'$.partial_action'),0)=0) AND NOT EXISTS(SELECT 1 FROM proposals WHERE message_id=NEW.id AND status IN ('review','waiting_reply','applying')) AND COALESCE(CASE WHEN json_valid(NEW.result) THEN json_extract(NEW.result,'$.disposition') END,'')!='deferred'"),
            ('ppt_jobs', "'ppt:'||NEW.day||':'||NEW.version", "'ppt_job'", '1', 'NEW.status',
             "json_object('status',NEW.status,'date',NEW.day,'version',NEW.version,'artifact_sha',NEW.artifact_sha,'error',NEW.error,'updated',NEW.updated_at)",
             "NEW.status='ready' AND length(NEW.artifact_sha)>0"),
        ]
    for table, key, kind, scope, status, detail, receipt in specs:
        last = f'(SELECT phase FROM task_events WHERE task_key={key} ORDER BY id DESC LIMIT 1)'
        previous = f'(SELECT detail FROM task_events WHERE task_key={key} ORDER BY id DESC LIMIT 1)'
        attempt = f"(SELECT id FROM task_events WHERE task_key={key} AND phase='failure' ORDER BY id DESC LIMIT 1)"
        for operation in ('INSERT', 'UPDATE'):
            db.executescript(f'''CREATE TRIGGER IF NOT EXISTS audit_{table}_{operation.lower()}
            AFTER {operation} ON {table} WHEN {scope} BEGIN
              INSERT INTO task_events(task_key,task_type,phase,at,detail)
              SELECT {key},{kind},'failure',strftime('%Y-%m-%dT%H:%M:%fZ','now'),{detail}
              WHERE {status}='error' AND NOT (COALESCE({last},'')='failure' AND
                json_remove(COALESCE({previous},'{{}}'),'$.at','$.updated')=json_remove({detail},'$.at','$.updated'));
              INSERT INTO task_events(task_key,task_type,phase,attempt_id,at,detail)
              SELECT {key},{kind},CASE WHEN {status} IN ('processing','generating','publishing','queued') THEN 'resumed'
                WHEN {receipt} THEN 'completed' ELSE 'health_only' END,{attempt},strftime('%Y-%m-%dT%H:%M:%fZ','now'),{detail}
              WHERE {status}!='error' AND {last} IN ('failure','resumed','health_only')
                AND ({last}='failure' OR ({receipt}) OR ({last}='resumed' AND {status} NOT IN ('processing','generating','publishing','queued')));
            END;''')
        # Adopt errors already present at rollout without inventing old timestamps.
        sql = f'''INSERT INTO task_events(task_key,task_type,phase,at,detail)
          SELECT {key},{kind},'failure',strftime('%Y-%m-%dT%H:%M:%fZ','now'),{detail} FROM {table} NEW
          WHERE {scope} AND {status}='error' AND NOT EXISTS(SELECT 1 FROM task_events WHERE task_key={key})'''
        db.execute(sql)


def read_events(root, cursors):
    events, next_cursors = [], dict(cursors)
    for namespace, path in [('assistant', root/'assistant.sqlite3'), ('gmail', root/'gmail/mail.sqlite3')]:
        if not path.exists():
            continue
        with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='task_events'").fetchone():
                continue
            for row in db.execute('SELECT * FROM task_events WHERE id>? ORDER BY id LIMIT 1000', (cursors.get(namespace, 0),)):
                item = dict(row)
                item['ref'] = f"{namespace}:{row['attempt_id'] or row['id']}"
                item['detail'] = json.loads(row['detail'])
                item['label'] = LABELS.get(row['task_type'], '內部任務')
                events.append(item)
                next_cursors[namespace] = row['id']
    return events, next_cursors


def incident_detail(event):
    return {'ref': event['ref'], 'task_key': event['task_key'], 'task_type': event['task_type'],
            'label': event['label'], 'failed_at': event['at'], 'phase': 'failure',
            'diagnostic': event['detail']}


def group_summary(details):
    # Only program-owned labels and fixed error categories leave the private log.
    kinds = {'TimeoutError': '逾時', 'TimeoutExpired': '逾時', 'ReadTimeout': '連線逾時',
             'ConnectError': '連線失敗', 'ConnectionRefusedError': '連線失敗',
             'PermissionError': '權限不足', 'FileNotFoundError': '檔案不存在',
             'OperationalError': '資料庫操作失敗'}
    impacts = {'fellowship_email': '團契通知郵件可能未寄出',
               'gmail_worker': '講員 PPT 可能尚未接收', 'gmail_poll': '講員 PPT 可能尚未接收',
               'mail_message': '講員郵件可能尚未處理', 'mail_file': '講員 PPT 可能尚未歸檔',
               'message': '群消息資料可能尚未登記', 'ppt_job': '主日 PPT 可能尚未產生',
               'ppt_readiness': '主日 PPT 可能尚未準備好', 'worker': '部分群消息可能尚未處理'}
    lines = []
    for item in details:
        label = LABELS.get(item['task_type'], '內部任務')
        reason = kinds.get(item.get('diagnostic', {}).get('error_type'), '執行異常；具體原因已保留於管理頁')
        line = f"• {label}：{reason}；{impacts.get(item['task_type'], '此步驟可能尚未完成')}。"
        if line not in lines:
            lines.append(line)
    return '\n'.join(lines[:8])


def completed_summary(details):
    return '\n'.join(dict.fromkeys('• '+LABELS.get(d['task_type'],'內部任務')+'：已核對完成回執。' for d in details))


def latest_attempts(details):
    # Earlier failed attempts stay immutable; a successful later retry of the
    # same subject completes that task, not a different day's mail/PPT.
    latest={}
    for item in details:
        diagnostic=item.get('diagnostic',{})
        subject=(item['task_key'],diagnostic.get('target_day') or diagnostic.get('date') or '')
        latest[subject]=item
    return list(latest.values())


def incident_resolution(details, expected=0):
    latest=latest_attempts(details)
    return 'completed' if latest and len(latest)>=expected and all(d['phase']=='completed' for d in latest) else 'unverified'
