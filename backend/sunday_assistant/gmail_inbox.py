"""Durable speaker attachment intake; originals are never converted or merged."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from email.utils import parseaddr
from hashlib import sha256
import fcntl
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import zipfile

from backend.sunday_data_lock import sunday_data_lock
from backend.sunday_presenter import OneDrivePpts, service_day
from . import gmail_source as source
from .models import ZONE
from .store import dump, now_text


def digest(data):
    return sha256(data).hexdigest()


def safe_name(value):
    value = re.sub(r'[\\/\x00-\x1f:*?"<>|]', '_', value).strip(' .')
    value = value.encode('utf-8')[:120].decode('utf-8', errors='ignore')
    return value or '講員PPT'


def validate_ppt(data, extension):
    if not data or len(data) > source.MAX_ATTACHMENT:
        raise ValueError('附件空白或超過 35 MB')
    if extension == '.ppt':
        if not data.startswith(bytes.fromhex('D0CF11E0A1B11AE1')) or 'PowerPoint Document'.encode('utf-16le') not in data:
            raise ValueError('附件不是可識別的 PowerPoint PPT 原檔')
        return
    if extension != '.pptx':
        raise ValueError('只收存 PPT／PPTX 原檔')
    from lxml import etree
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 20000 or sum(item.file_size for item in entries) > 300 * 1024 * 1024:
                raise ValueError('PowerPoint 解壓大小超出可核對範圍')
            names = {entry.filename for entry in entries}
            if not {'[Content_Types].xml', 'ppt/presentation.xml'} <= names:
                raise ValueError('附件不是 PowerPoint PPTX 原檔')
            # Validate only the small package metadata; never rewrite the package.
            for name in ('[Content_Types].xml', 'ppt/presentation.xml'):
                if archive.getinfo(name).file_size > 2 * 1024 * 1024:
                    raise ValueError('PowerPoint metadata 超出大小限制')
                etree.fromstring(archive.read(name), parser=etree.XMLParser(resolve_entities=False, no_network=True))
    except (zipfile.BadZipFile, KeyError, etree.XMLSyntaxError) as exc:
        raise ValueError('附件不是完整的 PowerPoint PPTX') from exc


def file_hash(path):
    return digest(path.read_bytes()) if path.exists() and not path.is_symlink() else None


def atomic_copy(path, data, expected=None):
    if path.is_symlink() or (path.exists() and file_hash(path) != expected):
        raise ValueError('OneDrive 中的同名檔案已變更，保留原檔並請核對')
    fd, temporary = tempfile.mkstemp(prefix='.speaker-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink() or (path.exists() and file_hash(path) != expected):
            raise ValueError('OneDrive 檔案在準備期間已變更，未覆蓋')
        Path(temporary).replace(path)
        if file_hash(path) != digest(data):
            raise OSError('OneDrive 寫入後的檔案 SHA 不符')
    finally:
        Path(temporary).unlink(missing_ok=True)


class GmailInbox:
    def __init__(self, agent, *, client_factory=source.Gmail, classifier=source.classify, verifier=source.verify):
        self.agent = agent
        self.root = agent.store.root / 'gmail'
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.path = self.root / 'mail.sqlite3'
        self.client_factory, self.classifier, self.verifier = client_factory, classifier, verifier
        with self.db() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS mail_messages(key TEXT PRIMARY KEY,mailbox TEXT,message_id TEXT,payload TEXT,
                binding TEXT,status TEXT,error TEXT,updated TEXT);
              CREATE TABLE IF NOT EXISTS mail_files(id TEXT PRIMARY KEY,message_key TEXT,part_id TEXT,filename TEXT,
                decision TEXT,status TEXT,error TEXT,plan TEXT,sha TEXT,archive TEXT,updated TEXT);
              CREATE TABLE IF NOT EXISTS mail_assets(day TEXT,speaker TEXT,sha TEXT,path TEXT,PRIMARY KEY(day,speaker,sha));
              CREATE TABLE IF NOT EXISTS mail_current(day TEXT,speaker TEXT,sha TEXT,path TEXT,received TEXT,PRIMARY KEY(day,speaker));
              CREATE TABLE IF NOT EXISTS mail_senders(email TEXT PRIMARY KEY,worker TEXT,source_job TEXT,updated TEXT);
              CREATE TABLE IF NOT EXISTS mail_kv(key TEXT PRIMARY KEY,value TEXT);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA journal_mode=WAL')
            yield db

    @contextmanager
    def lock(self):
        with (self.root / 'inbox.lock').open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def get(self, key, default=None):
        with self.db() as db:
            row = db.execute('SELECT value FROM mail_kv WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO mail_kv VALUES (?,?)', (key, dump(value)))

    def state(self):
        config = source.configuration()
        if not config:
            return {'configured': False, 'mailboxes': [], 'files': []}
        with self.db() as db:
            message_errors = [dict(row) for row in db.execute("SELECT key,mailbox,payload,error FROM mail_messages WHERE status='error' AND NOT EXISTS (SELECT 1 FROM mail_files WHERE message_key=mail_messages.key) LIMIT 20")]
            rows = db.execute('SELECT f.*,m.mailbox,m.payload FROM mail_files f JOIN mail_messages m ON f.message_key=m.key ORDER BY f.updated DESC LIMIT 40').fetchall()
        files = []
        for row in rows:
            message, decision = json.loads(row['payload']), json.loads(row['decision'])
            if row['mailbox'] not in config['mailboxes']:
                continue
            files.append({'id': row['id'], 'filename': row['filename'], 'mailbox': row['mailbox'],
                          'from': message['from'], 'subject': message['subject'], 'sent_at': message['sent_at'],
                          'date': decision.get('service_date'), 'speaker': decision.get('speaker'),
                          'status': row['status'], 'reason': row['error'] or decision.get('reason'),
                          'sourceUrl': 'https://mail.google.com/mail/?authuser=' + row['mailbox'] + '#all/' + (message.get('thread_id') or message['id'])})
        for item in message_errors:
            packet = json.loads(item.pop('payload') or '{}')
            item['subject'] = packet.get('subject', '郵件讀取未完成')
        return {'message_errors': [item for item in message_errors if item['mailbox'] in config['mailboxes']], 'configured': True, 'enabled': config.get('enabled', False),
                'mailboxes': [{'email': mail, 'connected': source.token_path(config, mail).exists(),
                              **self.get('status:' + mail, {})} for mail in config['mailboxes']], 'files': files}

    def sender_workers(self, address):
        workers = {worker.name for worker in self.agent.repository.list_sunday_workers()
                   if worker.email and parseaddr(worker.email)[1].lower() == address}
        with self.db() as db:
            remembered = db.execute('SELECT worker FROM mail_senders WHERE email=?', (address,)).fetchone()
        if remembered:
            workers.add(remembered[0])
        return workers

    def poll(self, now=None):
        config = source.configuration()
        if not config or not config.get('enabled') or not self.agent.store.settings().enabled:
            return
        now = now or datetime.now(ZONE)
        with self.lock():
            # One mailbox/page and one new message per pass; independent of WhatsApp.
            index = self.get('mailbox_index', 0) % len(config['mailboxes']) if config['mailboxes'] else 0
            if not config['mailboxes']:
                return
            mailbox = config['mailboxes'][index]
            self.put('mailbox_index', index + 1)
            token = source.token_path(config, mailbox)
            if not token.exists():
                self.put('status:' + mailbox, {'status': 'needs_connection', 'at': now_text(), 'error': '請連接 Gmail 唯讀權限'})
                return
            if time.time() - self.get('last_poll:' + mailbox, 0) < 60:
                return
            self.put('last_poll:' + mailbox, time.time())
            try:
                client = self.client_factory(mailbox, token)
                self.scan(mailbox, client)
                services = source.calendar_context(self.agent.repository, now)
                self.resume_waiting(services)
                with self.db() as db:
                    interrupted = db.execute("SELECT id FROM mail_files f JOIN mail_messages m ON m.key=f.message_key WHERE m.mailbox=? AND f.status='publishing' LIMIT 1", (mailbox,)).fetchone()
                if interrupted:
                    self.publish(interrupted['id'], client=client)
                with self.db() as db:
                    row = db.execute("SELECT * FROM mail_messages WHERE mailbox=? AND status='queued' ORDER BY updated LIMIT 1", (mailbox,)).fetchone()
                if row:
                    self.process(dict(row), client, services)
                self.put('status:' + mailbox, {'status': 'ready', 'at': now_text(), 'error': None})
            except ValueError as exc:
                self.put('status:' + mailbox, {'status': 'needs_review', 'at': now_text(), 'error': str(exc)[:400]})
            except Exception:
                # Transport credentials and raw messages must not appear in logs/notices.
                self.put('status:' + mailbox, {'status': 'error', 'at': now_text(), 'error': 'Gmail 接收失敗，請查看後台並重試'})

    def scan(self, mailbox, client):
        cursor = self.get('cursor:' + mailbox)
        page = client.page(cursor)
        # Persist the whole page before advancing; a crash cannot skip its messages.
        with self.db() as db:
            for item in page.get('messages', []):
                key = digest((mailbox + ':' + item['id']).encode())
                db.execute("INSERT OR IGNORE INTO mail_messages VALUES (?,?,?,NULL,NULL,'queued',NULL,?)", (key, mailbox, item['id'], now_text()))
        self.put('cursor:' + mailbox, page.get('nextPageToken'))

    def resume_waiting(self, services):
        """Only changed missing inputs can resume a machine-held decision."""
        calendar = {row['date']: row for row in services}
        with self.db() as db:
            waiting = db.execute("SELECT f.decision,m.key,m.payload,m.binding FROM mail_files f JOIN mail_messages m ON f.message_key=m.key WHERE f.status='review' AND m.status='processed'").fetchall()
        for row in waiting:
            decision, packet = json.loads(row['decision']), json.loads(row['payload'])
            reason = decision.get('hold_type')
            changed = (reason == 'sender' and sorted(self.sender_workers(parseaddr(packet['from'])[1].lower())) != decision.get('sender_workers'))
            changed |= reason in {'calendar', 'grounding'} and calendar.get(decision.get('service_date')) != decision.get('binding')
            changed |= reason == 'ambiguous' and dump(services) != row['binding']
            if changed:
                with self.db() as db:
                    db.execute("UPDATE mail_messages SET status='queued',error=NULL,updated=? WHERE key=? AND status='processed'", (now_text(), row['key']))

    def process(self, row, client, services):
        try:
            message = json.loads(row['payload']) if row['payload'] else client.message(row['message_id'])
            with self.db() as db:
                db.execute('UPDATE mail_messages SET payload=?,binding=?,updated=? WHERE key=?', (dump(message), dump(services), now_text(), row['key']))
            if not message['attachments']:
                with self.db() as db:
                    db.execute("UPDATE mail_messages SET status='ignored',updated=? WHERE key=?", (now_text(), row['key']))
                return
            if message.get('intake_review'):
                for part in message['attachments']:
                    job_id = digest((row['key'] + ':' + part['part_id']).encode())
                    with self.db() as db:
                        db.execute("INSERT OR IGNORE INTO mail_files VALUES (?,?,?,?,?,'review',?,NULL,NULL,NULL,?)",
                                   (job_id,row['key'],part['part_id'],part['filename'],dump({'hold_type':'input'}),message['intake_review'],now_text()))
                with self.db() as db:
                    db.execute("UPDATE mail_messages SET status='processed',error=NULL,updated=? WHERE key=?", (now_text(),row['key']))
                return
            sender = parseaddr(message['from'])[1].lower()
            identities = self.sender_workers(sender)
            message['_verified_sender_worker'] = next(iter(identities)) if len(identities) == 1 else None
            packet_sha = digest(dump(source.match_packet(message, services)).encode())
            policy_sha = digest(Path(source.__file__).read_bytes())
            cached = self.get('match:' + policy_sha + ':' + packet_sha)
            result = source.Matches.model_validate(cached) if cached else self.classifier(message, services)
            if not cached:
                self.put('match:' + policy_sha + ':' + packet_sha, result.model_dump(mode='json'))
            expected = {part['part_id'] for part in message['attachments']}
            if len(result.attachments) != len(expected) or {m.part_id for m in result.attachments} != expected:
                raise RuntimeError('郵件附件判讀清單不完整')
            source_text = '\n'.join([message['subject'], message['body'], *[part['filename'] for part in message['attachments']]])
            for match in result.attachments:
                part = next(p for p in message['attachments'] if p['part_id'] == match.part_id)
                source_text = '\n'.join([message['subject'], message['body'], part['filename']])
                status, reason, verification, hold_type = 'review', match.reason, None, 'ambiguous'
                if match.kind in {'worship', 'unrelated'}:
                    status = 'ignored'
                elif part.get('size', 0) > source.MAX_ATTACHMENT:
                    reason, hold_type = 'PPT 附件超過 35 MB，請人工處理', 'input'
                elif match.kind == 'speaker':
                    valid = (match.service_date and match.service_date.weekday() == 6 and match.speaker and
                             any(s['date'] == str(match.service_date) and s['speaker'] == match.speaker for s in services))
                    same_slot = [m for m in result.attachments if m.kind == 'speaker' and m.service_date == match.service_date and m.speaker == match.speaker]
                    if not valid:
                        reason, hold_type = '日期或講員無法對回網站安排，請核對', 'calendar'
                    elif len(same_slot) > 1:
                        reason, hold_type = '同一主日有多份講道附件，請選擇要使用的版本', 'versions'
                    elif identities != {match.speaker}:
                        reason, hold_type = '寄件地址尚未對應此講員，請核對完整姓名及 email', 'sender'
                    elif not match.evidence or any(not item or item not in source_text for item in match.evidence):
                        reason, hold_type = '附件歸屬依據無法對回原郵件，請核對', 'grounding'
                    else:
                        review_key = 'review:' + policy_sha + ':' + packet_sha + ':' + digest(match.model_dump_json().encode())
                        cached_review = self.get(review_key)
                        verdict = source.Verdict.model_validate(cached_review) if cached_review else self.verifier(message, services, match)
                        if not cached_review:
                            self.put(review_key, verdict.model_dump())
                        hold_type = 'grounding'
                        verification = verdict.model_dump()
                        status, reason = ('ready', verdict.reason) if verdict.supported else ('review', verdict.reason)
                part = next(p for p in message['attachments'] if p['part_id'] == match.part_id)
                job_id = digest((row['key'] + ':' + match.part_id).encode())
                decision = match.model_dump(mode='json')
                decision['verification'] = verification
                decision['hold_type'] = hold_type
                decision['sender_workers'] = sorted(identities)
                decision['source_sha256'] = digest(dump(source.match_packet(message, services)).encode())
                decision['binding'] = next((s for s in services if s['date'] == decision.get('service_date')), None)
                with self.db() as db:
                    db.execute("INSERT INTO mail_files VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,?) ON CONFLICT(id) DO UPDATE SET decision=excluded.decision,status=excluded.status,error=excluded.error,updated=excluded.updated WHERE mail_files.status='review'",
                               (job_id, row['key'], match.part_id, part['filename'], dump(decision), status, reason, now_text()))
                if status == 'ready':
                    try:
                        self.publish(job_id, client=client)
                    except ValueError as exc:
                        self.hold(job_id, str(exc))
                    except Exception:
                        with self.db() as db:
                            db.execute("UPDATE mail_files SET status='error',error='附件收存失敗，請核對後重試',updated=? WHERE id=?", (now_text(), job_id))
                        raise
            with self.db() as db:
                db.execute("UPDATE mail_messages SET status='processed',error=NULL,updated=? WHERE key=?", (now_text(), row['key']))
        except Exception as exc:
            with self.db() as db:
                db.execute("UPDATE mail_messages SET status='error',error=?,updated=? WHERE key=?", (type(exc).__name__ + ': 郵件判讀或收存失敗，請重試', now_text(), row['key']))
            raise

    def publish(self, job_id, *, client=None, manual=False, day=None, speaker=None):
        with self.db() as db:
            row = dict(db.execute('SELECT f.*,m.mailbox,m.payload FROM mail_files f JOIN mail_messages m ON f.message_key=m.key WHERE f.id=?', (job_id,)).fetchone())
        if row['status'] in {'imported', 'duplicate', 'older_version', 'ignored'}:
            return
        message, match = json.loads(row['payload']), json.loads(row['decision'])
        day, speaker = day or match.get('service_date'), speaker or match.get('speaker')
        parsed = service_day(day or '')
        if parsed.weekday() != 6 or not speaker or speaker not in self.agent.worker_map():
            raise ValueError('請核對主日日期與完整講員姓名')
        config = source.configuration()
        if not config or row['mailbox'] not in config['mailboxes'] or not config.get('enabled'):
            raise ValueError('此 Gmail 接收已停用')
        if not manual and row['status'] not in {'ready', 'publishing', 'error'}:
            raise ValueError('此附件需要操作者核對')
        sender = parseaddr(message['from'])[1].lower()
        identities = self.sender_workers(sender)
        if not sender or ('@' not in sender) or (identities and identities != {speaker}):
            raise ValueError('此寄件地址已對應另一位同工或無效，請核對後再收存')
        if not manual and identities != {speaker}:
            self.hold(job_id, '此寄件地址尚未對應講員，請核對後再收存')
            return
        part = next(p for p in message['attachments'] if p['part_id'] == row['part_id'])
        extension = Path(part['filename']).suffix.lower()
        cache = self.root / 'blobs' / (row['sha'] + extension) if row.get('sha') else None
        if cache and cache.exists():
            data = cache.read_bytes()
            if digest(data) != row['sha']:
                raise ValueError('已儲存附件 SHA 不符，未收存')
        else:
            client = client or self.client_factory(row['mailbox'], source.token_path(config, row['mailbox']))
            data = client.attachment(message['id'], part)
        validate_ppt(data, extension)
        sha = digest(data)
        blobs = self.root / 'blobs'; blobs.mkdir(mode=0o700, exist_ok=True)
        cache = blobs / (sha + extension)
        if not cache.exists():
            atomic_copy(cache, data)
            cache.chmod(0o600)
        with sunday_data_lock():
            latest_config = source.configuration()
            if not latest_config or not latest_config.get('enabled') or row['mailbox'] not in latest_config['mailboxes']:
                self.hold(job_id, '郵箱接收已停用，未收存')
                return
            if self.sender_workers(sender) != identities:
                self.hold(job_id, '寄件 email 對應在準備期間已變更，請重新核對')
                return
            calendar = {entry.date: entry for entry in self.agent.repository.list_sunday_services()}
            entry = calendar.get(str(parsed))
            actual = {'date': entry.date, 'speaker': entry.sermon_speaker, 'title': entry.sermon_title} if entry else None
            if not actual or actual['speaker'] != speaker or (not manual and actual != match.get('binding')):
                self.hold(job_id, '網站日期／講員／題目已變更或尚未確認，請核對', 'calendar')
                return
            cloud = OneDrivePpts.configured()
            if not cloud:
                raise ValueError('網站尚未設定 OneDrive 主日資料夾')
            folder = cloud.folder(parsed);folder.mkdir(exist_ok=True)
            history = folder / '講員PPT版本'
            if history.is_symlink() or history.resolve().parent != folder.resolve():
                raise ValueError('講員 PPT 版本目錄不可連結到其他目錄')
            history.mkdir(exist_ok=True)
            target = folder / f'講道-{parsed}-{safe_name(speaker)}{extension}'
            with self.db() as db:
                current_row = db.execute('SELECT * FROM mail_current WHERE day=? AND speaker=?', (str(parsed), speaker)).fetchone()
                asset = db.execute('SELECT * FROM mail_assets WHERE day=? AND speaker=? AND sha=?', (str(parsed), speaker, sha)).fetchone()
            current = dict(current_row) if current_row else None
            archive = Path(asset['path']) if asset else history / f'{sha[:16]}-{safe_name(Path(part["filename"]).stem)}{extension}'
            if archive.is_symlink() or (archive.exists() and file_hash(archive) != sha):
                raise ValueError('原檔版本檔案已被更改，保留現狀並請核對')
            if not archive.exists():
                atomic_copy(archive, data)
            older = current and current['sha'] != sha and (bool(asset) or datetime.fromisoformat(message['sent_at']) < datetime.fromisoformat(current['received'])) and not manual
            previous_plan = json.loads(row['plan']) if row.get('plan') else None
            actual_sha = file_hash(target)
            baseline = current['sha'] if current and current['path'] == str(target) else None
            resumed = previous_plan and previous_plan.get('sha') == sha and actual_sha == sha
            if actual_sha not in {None, baseline, sha} and not resumed:
                if not manual:
                    self.hold(job_id, 'OneDrive 講道 PPT 已被人工修改或有同名檔案，未覆蓋', 'manual_file')
                    return
                manual_archive = history / ('manual-' + actual_sha[:16] + '-' + target.name)
                if not manual_archive.exists():
                    atomic_copy(manual_archive, target.read_bytes())
                baseline = actual_sha
            plan = {'day': str(parsed), 'speaker': speaker, 'sha': sha, 'target': str(target),
                    'archive': str(archive), 'before_sha': baseline, 'manual': manual}
            with self.db() as db:
                db.execute("UPDATE mail_files SET status='publishing',plan=?,sha=?,updated=? WHERE id=?", (dump(plan), sha, now_text(), job_id))
            if not older and actual_sha != sha:
                atomic_copy(target, data, expected=baseline)
            with self.db() as db:
                if manual and not identities:
                    db.execute('INSERT INTO mail_senders VALUES (?,?,?,?)', (sender, speaker, job_id, now_text()))
                db.execute('INSERT OR IGNORE INTO mail_assets VALUES (?,?,?,?)', (str(parsed), speaker, sha, str(archive)))
                if not older:
                    received = max((message['sent_at'], current['received']), key=datetime.fromisoformat) if current else message['sent_at']
                    db.execute('INSERT OR REPLACE INTO mail_current VALUES (?,?,?,?,?)', (str(parsed), speaker, sha, str(target), received))
                status = 'older_version' if older else 'duplicate' if asset else 'imported'
                db.execute('UPDATE mail_files SET status=?,error=NULL,archive=?,updated=? WHERE id=?', (status, str(archive), now_text(), job_id))
            # When native file types change, retire only an unchanged, archived old main file.
            if not older and current and current['path'] != str(target):
                old = Path(current['path'])
                with self.db() as db:
                    saved = db.execute('SELECT path FROM mail_assets WHERE day=? AND speaker=? AND sha=?', (str(parsed), speaker, current['sha'])).fetchone()
                if saved and file_hash(Path(saved[0])) == current['sha'] and file_hash(old) == current['sha']:
                    old.unlink()

    def hold(self, job_id, reason, hold_type='input'):
        with self.db() as db:
            row = db.execute('SELECT decision FROM mail_files WHERE id=?', (job_id,)).fetchone()
            decision = json.loads(row[0]);decision['hold_type'] = hold_type
            db.execute("UPDATE mail_files SET status='review',decision=?,error=?,updated=? WHERE id=?", (dump(decision), reason, now_text(), job_id))

    def decide(self, job_id, action, day=None, speaker=None):
        if not re.fullmatch('[a-f0-9]{64}', job_id):
            raise ValueError('無效附件 ID')
        with self.lock():
            with self.db() as db:
                row = db.execute('SELECT * FROM mail_files WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise ValueError('找不到此附件')
            if action == 'dismiss':
                with self.db() as db:
                    db.execute("UPDATE mail_files SET status='ignored',updated=? WHERE id=?", (now_text(), job_id))
            elif action == 'apply':
                self.publish(job_id, manual=True, day=day, speaker=speaker)
            else:
                raise ValueError('請選擇核對收存或略過')
            # A failed copy can interrupt the rest of a multi-attachment mail.
            # Resume its saved packet; existing terminal receipts stay immutable.
            with self.db() as db:
                db.execute("UPDATE mail_messages SET status='queued',error=NULL,updated=? WHERE key=? AND status='error'", (now_text(),row['message_key']))


    def retry_message(self, key):
        if not re.fullmatch('[a-f0-9]{64}', key):
            raise ValueError('無效郵件 ID')
        with self.lock(), self.db() as db:
            row = db.execute('SELECT mailbox,status FROM mail_messages WHERE key=?', (key,)).fetchone()
            config = source.configuration()
            if not row or row['status'] != 'error' or not config or row['mailbox'] not in config['mailboxes']:
                raise ValueError('此郵件不需重試或郵箱已停用')
            db.execute("UPDATE mail_messages SET status='queued',error=NULL,updated=? WHERE key=?", (now_text(), key))

    def followup_needed(self, day, *, now=None, check_deadline=False):
        from datetime import timedelta
        config, settings = source.configuration(), self.agent.store.settings()
        if not config or not config.get('enabled') or not config.get('saturday_followup', True):
            return False
        if not settings.enabled or not settings.admin_group or settings.admin_group in settings.source_group_ids:
            return False
        now = (now or datetime.now(ZONE)).astimezone(ZONE)
        sunday = service_day(day)
        if sunday.weekday() != 6 or sunday < now.date():
            return False
        if check_deadline:
            deadline = datetime.combine(sunday - timedelta(days=1), datetime.min.time(), ZONE).replace(hour=22)
            if now < deadline:
                return False
        entry = next((entry for entry in self.agent.repository.list_sunday_services() if entry.date == str(sunday)), None)
        speaker = entry.sermon_speaker if entry else None
        with self.db() as db:
            row = db.execute('SELECT sha,path FROM mail_current WHERE day=? AND speaker=?', (str(sunday), speaker)).fetchone()
        return not (row and file_hash(Path(row['path'])) == row['sha'])

    def remind(self, now=None):
        from datetime import timedelta
        now = (now or datetime.now(ZONE)).astimezone(ZONE)
        # Upcoming Sunday includes today. Catch up after a Saturday outage.
        sunday = now.date() + timedelta(days=(6 - now.weekday()) % 7)
        settings = self.agent.store.settings()
        if not self.followup_needed(str(sunday), now=now, check_deadline=True):
            return
        entry = next((entry for entry in self.agent.repository.list_sunday_services() if entry.date == str(sunday)), None)
        speaker = entry.sermon_speaker if entry and entry.sermon_speaker else '講員尚待確認'
        key = f'speaker-ppt-followup:{sunday}:' + digest(settings.admin_group.encode())[:12]
        text = (f'【講員PPT跟進提醒】\n{sunday} 主日 · {speaker}\n'
                '週六晚上 10 點（達拉斯時間）的收件期限已到，系統尚未確認收到並收存本次講道 PPT。\n'
                '請管理員跟進講員的郵件及附件，並確認放入該主日的 OneDrive 資料夾。\n'
                'https://dallas-hlc.org/admin/sunday-assistant')
        with self.agent.store.db() as db:
            db.execute("INSERT OR IGNORE INTO outbox(key,group_id,text,status,updated_at) VALUES (?,?,?,'queued',?)",
                       (key, settings.admin_group, text, now_text()))
