"""Deterministic, versioned PPT completion, separate from model tool decisions."""
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re

from pptx import Presentation

from backend.sunday_data_lock import sunday_data_lock
from backend.sunday_service_skeletons import skeleton_status, maintain_skeletons
from .role_assignment import assign_readers
from .models import FRIDAY_FIELDS, ZONE
from .store import dump, now_text

PREFIX = '【主日PPT已準備好】'
DOWNLOAD = 'https://dallas-hlc.org/api/admin/sunday-assistant/ppt-ready'


class PptReady:
    def __init__(self, agent, *, generator=None, template=None):
        self.agent, self.store = agent, agent.store
        if generator is None or template is None:
            from backend.api.service import generate_sunday_service_ppt, PPT_TEMPLATE_FILE
            if generator is None:
                from functools import partial
                generator = partial(generate_sunday_service_ppt, require_complete=True)
            template = template or PPT_TEMPLATE_FILE
        self.generator, self.template = generator, Path(template)

    def artifact(self, day, version):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) or not re.fullmatch(r'[a-f0-9]{64}', version):
            raise ValueError('無效的主日 PPT 版本')
        return self.store.root / 'ppt-ready' / day / (version + '.pptx')

    def snapshot(self, day):
        services = self.agent.services()
        entry = services.get(day)
        if not entry:
            return None, ['本週主日表單']
        current = entry.model_dump(mode='json', by_alias=True)
        # These are upload/email metadata, never automatic PPT render inputs.
        current.pop('finalPptFilename', None)
        current.pop('emailBodyHtml', None)
        missing = [label for key, label in FRIDAY_FIELDS.items() if not current.get(key) or (isinstance(current.get(key), str) and not current[key].strip())]
        if current.get('donationAmount') is None:
            missing.append('本週奉獻總額')  # zero is a valid explicitly saved amount
        reader_names = [name.strip() for name in entry.scripture_readers if name.strip()]
        if len(set(reader_names)) < len(entry.scripture) or len(reader_names) != len(set(reader_names)):
            missing.append('自動安排讀經同工')
        songs = []
        catalog = self.agent.repository.list_sunday_songs()
        for title in [entry.hymn, entry.response_hymn]:
            matches = [song for song in catalog if song.title == title]
            if len(matches) != 1 or not (matches[0].lyrics_markdown or '').strip():
                missing.append('詩歌目錄／完整歌詞：' + (title or '未指定'))
            else:
                songs.append(matches[0].model_dump(mode='json', by_alias=True))
        future = []
        for row in skeleton_status(self.agent.repository, date.fromisoformat(day)):
            if not row['complete']:
                missing.append(row['date'] + '：' + '、'.join(row['missing']))
            saved = services.get(row['date'])
            future.append({key: saved.model_dump(by_alias=True).get(key) if saved else None for key in
                           ('date', 'sermonSpeaker', 'presider', 'worshipLeader', 'pianist')})
        days = {day, *[row['date'] for row in skeleton_status(self.agent.repository, date.fromisoformat(day))]}
        if any(row['date'] in days for row in self.agent.conflicts()):
            missing.append('同工安排與請假衝突')
        if missing:
            return None, missing
        # Template/renderer changes require a new output even if the form is identical.
        renderer_files = [Path(__file__).parents[1] / 'api' / name for name in ('service.py', 'ppt_generator.py')]
        renderer_files += [Path(__file__).parents[1] / 'sunday_song_performance.py', Path(__file__)]
        from backend.api.scripture import BIBLE_API_TRANSLATION_ZH
        fellowship = []
        if hasattr(self.agent.repository, 'list_fellowships'):
            from backend.api.service import _parse_fellowship_date
            fellowship = sorted({parsed.isoformat() for item in self.agent.repository.list_fellowships()
                                 if (parsed := _parse_fellowship_date(item.date))
                                 and date.fromisoformat(day) <= parsed <= date.fromisoformat(day)+timedelta(days=14)})
        inputs = {'service': current, 'songs': songs, 'future': future,
                  'fellowship_dates': fellowship, 'bible_translation': BIBLE_API_TRANSLATION_ZH,
                  'template': sha256(self.template.read_bytes()).hexdigest(),
                  'renderer': [sha256(path.read_bytes()).hexdigest() for path in renderer_files]}
        return sha256(dump(inputs).encode()).hexdigest(), []

    def pending(self, day):
        """Do not publish a form while new events or current-week reviews remain."""
        first = date.fromisoformat(day) - timedelta(days=6)
        relevant = {day, *( (date.fromisoformat(day) + timedelta(days=7*n)).isoformat() for n in (1,2))}
        with self.store.db() as db:
            rows = db.execute("SELECT * FROM messages WHERE status IN ('queued','processing','error','review','waiting_reply') "
                              "AND COALESCE(json_extract(payload,'$.initial_sync'),0)=0").fetchall()
            for row in rows:
                msg = json.loads(row['payload'])
                if msg['group_id'] not in self.store.settings().source_group_ids:
                    continue
                if msg.get('origin') == 'schedule':
                    if msg.get('schedule_date') in relevant:
                        return True
                    continue
                if row['status'] in {'queued', 'processing', 'error'}:
                    return True
                plans = db.execute("SELECT payload FROM proposals WHERE message_id=? AND status IN ('review','waiting_reply','applying')", (row['id'],)).fetchall()
                dates = {op['date'] for plan in plans for op in json.loads(plan[0]).get('operations', []) if op.get('kind') == 'service'}
                if dates and not dates & relevant:
                    continue
                stamp = msg.get('source_sent_at') or msg['sent_at']
                from datetime import datetime
                if dates & relevant or first <= datetime.fromisoformat(stamp).astimezone(ZONE).date() <= date.fromisoformat(day):
                    return True
        return False

    def synced(self, now):
        try:
            from datetime import datetime
            state = self.agent.bridge.status()
            stamp = datetime.fromisoformat(state['lastSyncAt'].replace('Z', '+00:00'))
            return (state.get('state') == 'ready' and state.get('synced') and not state.get('pending', 1)
                    and -5 <= (now-stamp).total_seconds() <= 60)
        except Exception:
            return False

    def tick(self, now):
        from .engine import upcoming_sunday
        config = self.store.settings()
        if not (config.enabled and config.auto_apply and config.auto_generate_ppt and config.admin_group):
            return
        day = upcoming_sunday(now.astimezone(ZONE).date()).isoformat()
        if self.pending(day) or not self.synced(now):
            self.store.put('ppt_readiness', {'date': day, 'status': 'waiting', 'missing': ['等待消息處理／同步完成']})
            self.cancel_queued()
            return
        try:
            with sunday_data_lock():
                maintain_skeletons(self.agent.repository, self.store, date.fromisoformat(day)-timedelta(days=1))
                maintain_skeletons(self.agent.repository, self.store, date.fromisoformat(day))
                assign_readers(self.agent.repository, self.store, [(date.fromisoformat(day)+timedelta(days=7*n)).isoformat() for n in range(3)])
                version, missing = self.snapshot(day)
                self.cancel_queued(day, version)
                self.store.put('ppt_readiness', {'date': day, 'version': version, 'status': 'waiting' if missing else 'complete', 'missing': missing})
                if missing:
                    return
                path = self.artifact(day, version)
                with self.store.db() as db:
                    job = db.execute('SELECT * FROM ppt_jobs WHERE day=? AND version=?', (day, version)).fetchone()
                if job and job['status'] in {'error', 'blocked'}:
                    self.store.put('ppt_readiness', {'date': day, 'status': 'error', 'error': job['error']} if job['status'] == 'error' else {'date': day, 'status': 'waiting', 'missing': [job['error']]})
                    return  # explicit retry or a changed input; no expensive retry loop
                built = False
                if not job or job['status'] != 'ready' or not self.valid_artifact(path, job['artifact_sha']):
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with self.store.db() as db:
                        db.execute("INSERT INTO ppt_jobs VALUES (?,?,'generating',NULL,NULL,?) ON CONFLICT(day,version) DO UPDATE SET status='generating',error=NULL,updated_at=excluded.updated_at", (day, version, now_text()))
                    self.store.put('worker', {'status': 'processing', 'at': now_text(), 'task': 'generate_ppt'})
                    temp = path.with_suffix('.building.pptx')
                    self.generator(day, output_path=temp)
                    if not self.valid_artifact(temp):
                        raise ValueError('PPT 產生器沒有返回可讀且有投影片的檔案')
                    # Revalidate even with a shared write lock: render hooks can update inputs.
                    if self.snapshot(day)[0] != version:
                        temp.unlink(missing_ok=True)
                        with self.store.db() as db:
                            db.execute("UPDATE ppt_jobs SET status='superseded',updated_at=? WHERE day=? AND version=?", (now_text(),day,version))
                        return
                    temp.chmod(0o600)
                    temp.replace(path)
                    digest = sha256(path.read_bytes()).hexdigest()
                    built = True
                # Publish only after the complete saved input and artifact were
                # revalidated. Custom render paths remain private until here.
                from backend.sunday_presenter import OneDrivePpts, service_day
                cloud = OneDrivePpts.configured()
                if cloud:
                    target = cloud.generated(service_day(day))
                    if not target.exists() or sha256(target.read_bytes()).hexdigest() != sha256(path.read_bytes()).hexdigest():
                        cloud.publish_generated(service_day(day), path)
                if built:
                    with self.store.db() as db:
                        db.execute("UPDATE ppt_jobs SET status='ready',artifact_sha=?,error=NULL,updated_at=? WHERE day=? AND version=?", (digest, now_text(), day, version))
                        db.execute("UPDATE ppt_jobs SET status='superseded' WHERE day=? AND version!=? AND status='error'", (day,version))
                self.store.put('ppt_readiness', {'date': day, 'version': version, 'status': 'ready', 'missing': []})
                # Rendering may take time; do not announce before another ingestion barrier.
                if self.pending(day) or not self.synced(now):
                    return
                config = self.store.settings()
                if not (config.enabled and config.auto_apply and config.auto_generate_ppt and config.admin_group):
                    return
                key = f'ppt-ready:{day}:{version}:' + sha256(config.admin_group.encode()).hexdigest()[:12]
                text = f'{PREFIX}\n{day} 主日 PPT 已產生，可以下載。\n{DOWNLOAD}/{day}/{version}\n請登入教會網站後下載。'
                with self.store.db() as db:
                    db.execute("INSERT OR IGNORE INTO outbox(key,group_id,text,status,updated_at) VALUES (?,?,?,'queued',?)", (key,config.admin_group,text,now_text()))
                    db.execute("UPDATE outbox SET status='queued' WHERE key=? AND status='cancelled'", (key,))
        except Exception as exc:
            # Input/clarification failures are waiting states, not system incidents.
            from fastapi import HTTPException
            blocked = isinstance(exc, HTTPException) and exc.status_code == 400
            detail = str(exc.detail if isinstance(exc, HTTPException) else exc)[:1000]
            with self.store.db() as db:
                if 'version' in locals() and version:
                    db.execute("UPDATE ppt_jobs SET status=?,error=?,updated_at=? WHERE day=? AND version=?", ('blocked' if blocked else 'error',detail,now_text(),day,version))
            self.store.put('ppt_readiness', {'date': day, 'status': 'waiting', 'missing': [detail]} if blocked else {'date': day, 'status': 'error', 'error': detail})

    @staticmethod
    def valid_artifact(path, digest=None):
        try:
            return bool(len(Presentation(path).slides)) and (digest is None or sha256(path.read_bytes()).hexdigest() == digest)
        except Exception:
            return False

    def cancel_queued(self, day=None, version=None):
        with self.store.db() as db:
            rows = db.execute("SELECT key FROM outbox WHERE key LIKE 'ppt-ready:%' AND status='queued'").fetchall()
            for row in rows:
                if not day or not version or not row['key'].startswith(f'ppt-ready:{day}:{version}:'):
                    db.execute("UPDATE outbox SET status='cancelled' WHERE key=?", (row['key'],))

    def downloadable(self, day, version):
        with sunday_data_lock():
            if self.snapshot(day)[0] != version or self.pending(day):
                raise ValueError('此 PPT 資料已有更新或待核對項目，請等候新版完成')
            with self.store.db() as db:
                job = db.execute("SELECT * FROM ppt_jobs WHERE day=? AND version=? AND status='ready'", (day,version)).fetchone()
            path = self.artifact(day,version)
            if not job or not self.valid_artifact(path, job['artifact_sha']):
                raise ValueError('此 PPT 尚未準備好或檔案需要重新產生')
            return path

    def retry(self, day):
        with self.store.db() as db:
            db.execute("UPDATE ppt_jobs SET status='queued',error=NULL WHERE day=? AND status='error'", (day,))
