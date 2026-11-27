"""Read-only, source-dated absence answers with durable original-group replies."""
from datetime import date, timedelta
import json
import re

from backend.sunday_data_lock import sunday_data_lock
from .models import ZONE
from .store import dump, now_text

PREFIX = '【主日請假查詢】'


def question_day(message):
    """Resolve one unambiguous Sunday from this message, never wall-clock time."""
    from opencc import OpenCC
    normalized = OpenCC('t2s').convert(message.text).lower()
    text = re.sub(r'\s+', '', normalized)
    if not re.search(r'谁|哪些|哪几|名单|列出|\bwho\b', normalized):
        raise ValueError('此工具只回答明確詢問請假名單的問題')
    if not re.search(r'不能|不来|不在|无法|外出|请假|没法|没空|缺席|absen|unavailable|cannot|can\x27t|won\x27t|notcoming|unable|away', text):
        raise ValueError('只查詢已登記不能服事者，不推定誰會出席')
    local = (message.source_sent_at or message.sent_at).astimezone(ZONE).date()
    base = local + timedelta(days=(6-local.weekday()) % 7)
    candidates = set()
    full = list(re.finditer(r'(?<!\d)(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})(?:日|号)?(?!\d)', text))
    for match in full:
        candidates.add(date(*map(int,match.groups())))
    remaining = text
    for match in reversed(full):
        remaining = remaining[:match.start()] + ' ' + remaining[match.end():]
    for match in re.finditer(r'(?<!\d)(\d{1,2})[/月](\d{1,2})(?:日|号)?(?!\d)', remaining):
        month,day=map(int,match.groups())
        valid=[]
        for year in (local.year-1,local.year,local.year+1):
            try: valid.append(date(year,month,day))
            except ValueError: pass
        if not valid:
            raise ValueError('查詢日期不存在')
        candidates.add(min(valid,key=lambda d:abs((d-local).days)))
    sunday=r'(?:主日|周日|星期日|星期天|礼拜天|sunday)'
    relative=[(r'(?:(?<![上下再])下(?:个)?(?:周|星期|礼拜)?'+sunday+r'|nextsunday)',7),
              (r'(?:(?<![上下再])上(?:个)?(?:周|星期|礼拜)?'+sunday+r'|lastsunday)',-7),
              (r'(?:(?:这|本)(?:个)?(?:周|星期|礼拜)?'+sunday+r'|thissunday)',0)]
    for expression,offset in relative:
        if re.search(expression,text):
            candidates.add(base+timedelta(days=offset))
    if len(candidates)!=1:
        raise ValueError('請提供一個明確主日，例如「這週主日」或「11/8」；多個或不明日期先留後台核對')
    day=candidates.pop()
    if day.weekday()!=6:
        raise ValueError('查詢日期不是主日，請核對日期')
    return day


def answer_snapshot(agent, day):
    names=sorted(worker.name for worker in agent.repository.list_sunday_workers() if not worker.is_available_on(day.isoformat()))
    text=f'{PREFIX}\n主日：{day.isoformat()}\n'
    text+=('已登記不能服事的同工：\n'+'\n'.join('• '+name for name in names)) if names else '目前沒有同工登記當天不能服事。'
    text+='\n以上依網站已保存的請假資料；未登記請假不代表已確認會出席。'
    # No reasons, private contact details or unrelated dates are disclosed.
    return {'date':str(day),'workers':names,'text':text}


def queue_answer(agent, message):
    settings=agent.store.settings()
    if (not settings.enabled or message.group_id!=settings.availability_group
            or message.group_id==settings.admin_group or message.initial_sync or message.revoked or message.unverified_edit):
        raise ValueError('只在已啟用的教會外出登記原群回答新查詢')
    day=question_day(message)
    key='absence-query:'+message.message_id
    with sunday_data_lock(), agent.store.db() as db:
        existing=db.execute('SELECT text,status FROM outbox WHERE key=?',(key,)).fetchone()
        if not existing:
            snapshot=answer_snapshot(agent,day)
            db.execute("INSERT INTO outbox(key,group_id,text,status,updated_at) VALUES (?,?,?,'queued',?)",(key,message.group_id,snapshot['text'],now_text()))
            db.execute('INSERT OR REPLACE INTO kv VALUES (?,?)',('absence-query:'+message.message_id,dump(snapshot)))
        # This is a query receipt, not a form update or an absence registration.
        db.execute("UPDATE messages SET status='done',error=NULL WHERE id=?",(message.message_id,))
    return {'status':'done','query':'sunday_absences','date':str(day)}


def prepare_delivery(agent, row):
    """Re-read before the first send, so a queued answer cannot use stale data."""
    settings=agent.store.settings()
    if not settings.enabled or row['group_id']!=settings.availability_group or row['group_id']==settings.admin_group:
        return None
    with agent.store.db() as db:
        saved=db.execute('SELECT payload FROM messages WHERE id=?',(row['key'][len('absence-query:'):],)).fetchone()
    if not saved:
        return None
    from .models import ChatMessage
    message=ChatMessage.model_validate_json(saved[0])
    if message.group_id!=row['group_id'] or message.initial_sync or message.revoked or message.unverified_edit:
        return None
    with sunday_data_lock():
        snapshot=answer_snapshot(agent,question_day(message))
        with agent.store.db() as db:
            # Uncertain/submitted replies are never changed or blindly retried.
            db.execute("UPDATE outbox SET text=?,updated_at=? WHERE key=? AND status='queued'",(snapshot['text'],now_text(),row['key']))
            db.execute('INSERT OR REPLACE INTO kv VALUES (?,?)',(row['key'],dump(snapshot)))
    return snapshot['text']
