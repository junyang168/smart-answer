from datetime import datetime
from hashlib import sha256
from functools import lru_cache
from io import BytesIO
import json
from pathlib import Path

import pytest
from pptx import Presentation

from backend.api.models import SundayServiceEntry, SundayWorker
from backend.sunday_assistant import gmail_source as source
from backend.sunday_assistant.gmail_inbox import GmailInbox, validate_ppt, safe_name
from backend.sunday_assistant.store import dump, now_text
from backend.sunday_presenter import OneDrivePpts
from backend.tests.test_sunday_assistant import agent


@lru_cache(maxsize=None)
def deck(text='original'):
    from pptx.util import Inches
    p = Presentation(); s = p.slides.add_slide(p.slide_layouts[6])
    s.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(2)).text = text
    out = BytesIO(); p.save(out); return out.getvalue()


@pytest.fixture
def mail(agent, tmp_path, monkeypatch):
    config = tmp_path/'config/sunday_gmail.json';config.parent.mkdir(exist_ok=True)
    config.write_text(json.dumps({'enabled': True, 'mailboxes': ['jun@example.com','jian@example.com'],
                                 'reviewers': ['jun@example.com'], 'token_directory': str(tmp_path/'private-tokens')}))
    monkeypatch.setenv('SUNDAY_GMAIL_CONFIG', str(config))
    cloud_root = tmp_path/'onedrive';cloud_root.mkdir()
    presenter = tmp_path/'presenter.json';presenter.write_text(json.dumps({'syncRoot': str(cloud_root), 'webRootUrl':'https://onedrive.live.com/my?id=%2Fpersonal%2Ftest%2FDocuments%2F主日'}))
    monkeypatch.setenv('SUNDAY_PRESENTER_CONFIG', str(presenter))
    agent.repository.workers['陳牧師'] = SundayWorker(name='陳牧師', email='pastor@example.com')
    agent.repository.services['2026-11-08'] = SundayServiceEntry(date='2026-11-08',sermonSpeaker='陳牧師',sermonTitle='盼望')
    payloads = {}
    class Client:
        calls = 0
        def attachment(self, message_id, part):
            self.calls += 1
            return payloads[message_id]
    client = Client()
    def classify(message, services):
        return source.Matches(attachments=[source.Match(part_id=p['part_id'],kind='speaker',service_date='2026-11-08',speaker='陳牧師',evidence=['11/8'],reason='明確日期') for p in message['attachments']])
    inbox = GmailInbox(agent, client_factory=lambda *_: client, classifier=classify,
                       verifier=lambda *_: source.Verdict(supported=True, reason='來源及網站日期講員相符'))
    return agent,inbox,client,payloads,cloud_root


def stage(mail, key='one', mailbox='jun@example.com', data=None, sender='陳牧師 <pastor@example.com>', at='2026-11-07T18:00:00-06:00', count=1, filename='hope.pptx'):
    agent,inbox,client,payloads,root = mail
    payloads[key] = data if data is not None else deck()
    message = {'id':key,'thread_id':key,'from':sender,'subject':'11/8 講道：盼望','body':'講道 PPT 附件','sent_at':at,
               'attachments':[{'part_id':str(i),'filename':filename,'attachment_id':str(i),'size':len(payloads[key])} for i in range(count)]}
    with inbox.db() as db:
        db.execute("INSERT INTO mail_messages VALUES (?,?,?,?,NULL,'queued',NULL,?)",(key,mailbox,key,dump(message),now_text()))
        row=dict(db.execute('SELECT * FROM mail_messages WHERE key=?',(key,)).fetchone())
    inbox.process(row,client,[{'date':'2026-11-08','speaker':'陳牧師','title':'盼望'}])
    with inbox.db() as db:
        return [dict(row) for row in db.execute('SELECT * FROM mail_files WHERE message_key=?',(key,))]


def main_file(root):
    return root/'11-8-2026/講道-2026-11-08-陳牧師.pptx'


def test_original_bytes_same_folder_and_worship_and_final_untouched(mail):
    agent,inbox,client,payloads,root=mail
    folder=root/'11-8-2026';folder.mkdir()
    (folder/'主日崇拜-2026-11-08-自動生成.pptx').write_bytes(b'worship')
    (folder/'final.pptx').write_bytes(b'final')
    original=deck('keep every native slide object')
    files=stage(mail,data=original)
    assert files[0]['status']=='imported'
    assert main_file(root).read_bytes()==original
    assert Path(files[0]['archive']).read_bytes()==original
    assert (folder/'主日崇拜-2026-11-08-自動生成.pptx').read_bytes()==b'worship'
    assert (folder/'final.pptx').read_bytes()==b'final'
    assert not agent.bridge.sent and not agent.store.outbox()


def test_two_mailboxes_deduplicate_and_newer_version_keeps_old_original(mail):
    _,inbox,_,_,root=mail
    first=deck('v1');second=deck('v2')
    stage(mail,data=first)
    assert stage(mail,key='two',mailbox='jian@example.com',data=first)[0]['status']=='duplicate'
    assert len(list((root/'歷史版本/11-8-2026/講員PPT版本').iterdir()))==1
    stage(mail,key='three',data=second,at='2026-11-07T19:00:00-06:00')
    assert main_file(root).read_bytes()==second
    assert stage(mail,key='old-forward',data=first,at='2026-11-07T20:00:00-06:00')[0]['status']=='older_version'
    assert main_file(root).read_bytes()==second
    assert len(list((root/'歷史版本/11-8-2026/講員PPT版本').iterdir()))==2


def test_earlier_email_cannot_overwrite_later_unknown_hash(mail):
    _,_,_,_,root=mail
    newer=deck('new');older=deck('old')
    stage(mail,data=newer,at='2026-11-07T20:00:00-06:00')
    assert stage(mail,key='late-delivery',data=older,at='2026-11-07T19:00:00-06:00')[0]['status']=='older_version'
    assert main_file(root).read_bytes()==newer


def test_email_mapping_precedes_name_and_unknown_address_requires_operator(mail):
    _,inbox,client,_,root=mail
    seen=[];original=inbox.classifier
    def classify(message, services):
        seen.append(message['_verified_sender_worker']);return original(message,services)
    inbox.classifier=classify
    rows=stage(mail,sender='陳牧師 <unmapped@example.com>')
    assert rows[0]['status']=='review' and client.calls==0 and seen==[None]
    assert not main_file(root).exists()
    inbox.decide(rows[0]['id'],'apply','2026-11-08','陳牧師')
    assert inbox.sender_workers('unmapped@example.com')=={'陳牧師'}
    assert stage(mail,key='subsequent',sender='a totally different display name <unmapped@example.com>')[0]['status']=='duplicate'
    assert seen[-1]=='陳牧師'


def test_address_already_belongs_to_different_worker_is_never_rebound(mail):
    agent,inbox,_,_,root=mail
    agent.repository.workers['王明']=SundayWorker(name='王明',email='other@example.com')
    rows=stage(mail,sender='陳牧師 <other@example.com>')
    assert rows[0]['status']=='review'
    with pytest.raises(ValueError,match='另一位'):
        inbox.decide(rows[0]['id'],'apply','2026-11-08','陳牧師')
    assert not main_file(root).exists()


def test_worship_and_unrelated_decks_are_ignored_without_attachment_download(mail):
    _,inbox,client,_,root=mail
    inbox.classifier=lambda *_:source.Matches(attachments=[source.Match(part_id='0',kind='worship',reason='主日流程')])
    assert stage(mail)[0]['status']=='ignored'
    assert client.calls==0 and not list(root.iterdir())


def test_multiple_versions_in_one_mail_require_a_choice(mail):
    _,_,client,_,root=mail
    rows=stage(mail,count=2)
    assert all(row['status']=='review' for row in rows)
    assert client.calls==0 and not main_file(root).exists()


def test_calendar_change_during_verification_is_held(mail):
    agent,inbox,_,_,root=mail
    def verify(*_):
        agent.repository.services['2026-11-08'].sermon_title='changed by owner'
        return source.Verdict(supported=True,reason='old input')
    inbox.verifier=verify
    assert stage(mail)[0]['status']=='review'
    assert not main_file(root).exists()


def test_manual_onedrive_edit_is_preserved_until_explicit_review(mail):
    _,inbox,_,_,root=mail
    stage(mail,data=deck('old'))
    changed=deck('human edit');main_file(root).write_bytes(changed)
    newest=deck('new email')
    rows=stage(mail,key='newest',data=newest,at='2026-11-07T19:00:00-06:00')
    assert rows[0]['status']=='review' and main_file(root).read_bytes()==changed
    inbox.decide(rows[0]['id'],'apply','2026-11-08','陳牧師')
    assert main_file(root).read_bytes()==newest
    assert any(path.read_bytes()==changed for path in (root/'歷史版本/11-8-2026/講員PPT版本').iterdir())


def test_interruption_after_copy_resumes_from_saved_sha_without_duplicate_notice(mail,monkeypatch):
    _,inbox,_,_,root=mail
    from backend.sunday_assistant import gmail_inbox
    original=gmail_inbox.atomic_copy
    def interrupted(path,data,expected=None):
        original(path,data,expected)
        if path==main_file(root):raise OSError('process interrupted after atomic copy')
    monkeypatch.setattr(gmail_inbox,'atomic_copy',interrupted)
    with pytest.raises(OSError):stage(mail)
    with inbox.db() as db:row=dict(db.execute('SELECT * FROM mail_files').fetchone())
    assert row['status']=='error' and row['plan'] and main_file(root).exists()
    monkeypatch.setattr(gmail_inbox,'atomic_copy',original)
    inbox.decide(row['id'],'apply','2026-11-08','陳牧師')
    with inbox.db() as db:assert db.execute('SELECT status FROM mail_files').fetchone()[0]=='imported'
    assert len(list((root/'歷史版本/11-8-2026/講員PPT版本').iterdir()))==1


def test_untrusted_filename_stays_in_date_directory(mail):
    _,_,_,_,root=mail
    files=stage(mail,filename='../../other/講道.pptx')
    assert Path(files[0]['archive']).parent==root/'歷史版本/11-8-2026/講員PPT版本'
    assert not (root/'other').exists()
    assert len(safe_name('中文'*200).encode())<=120


def test_invalid_powerpoint_is_review_input_not_an_internal_job_error(mail):
    _,inbox,_,_,root=mail
    assert stage(mail,data=b'not a powerpoint')[0]['status']=='review'
    with inbox.db() as db:assert db.execute('SELECT status FROM mail_messages').fetchone()[0]=='processed'
    assert not main_file(root).exists()


def test_parser_recurses_mime_and_retains_inline_attachment_and_plain_text():
    import base64
    encode=lambda text:base64.urlsafe_b64encode(text).decode().rstrip('=')
    raw={'id':'id','internalDate':'1794074400000','payload':{'headers':[{'name':'From','value':'pastor@example.com'},{'name':'Subject','value':'11/8'}],
        'parts':[{'mimeType':'multipart/mixed','parts':[{'mimeType':'text/plain','body':{'data':encode(b'original body')}},
         {'partId':'1.2','filename':'slides.pptx','body':{'size':3,'data':encode(b'abc')}}]}]}}
    packet=source.message_packet(raw)
    assert packet['body']=='original body'
    assert packet['attachments'][0]['part_id']=='1.2' and source.decode(packet['attachments'][0]['inline_data'])==b'abc'


@pytest.mark.parametrize('data,extension',[(b'word','.pptx'),(b'word','.ppt'),(b'word','.pdf')])
def test_file_validation_rejects_renamed_non_powerpoint(data,extension):
    with pytest.raises(ValueError):validate_ppt(data,extension)


def test_saturday_10pm_followup_only_goes_to_admin_and_is_deduplicated(mail,monkeypatch):
    agent,inbox,_,_,_=mail
    settings=agent.store.settings();settings.admin_group='999@g.us';settings.group_questions_enabled=False;settings.reminders_enabled=False
    agent.store.put('settings',settings.model_dump())
    before=datetime.fromisoformat('2026-11-07T21:59:59-06:00')
    due=datetime.fromisoformat('2026-11-07T22:00:00-06:00')
    inbox.remind(before);assert not agent.store.outbox()
    inbox.remind(due);inbox.remind(due)
    rows=agent.store.outbox();assert len(rows)==1 and rows[0]['group_id']=='999@g.us'
    assert '2026-11-08' in rows[0]['text'] and '10 點' in rows[0]['text']
    # Freeze delivery's calendar rather than relying on the machine's real date.
    original=inbox.followup_needed
    monkeypatch.setattr(GmailInbox,'followup_needed',lambda self,day,**kwargs: original(day,**(kwargs | {'now':due})))
    agent.deliver_outbox();agent.deliver_outbox()
    assert len(agent.bridge.sent)==1 and agent.bridge.sent[0][1]=='999@g.us'
    reopened=GmailInbox(agent);reopened.remind(due)
    assert len(agent.store.outbox())==1


def test_arrival_before_reminder_delivery_cancels_queued_followup(mail,monkeypatch):
    agent,inbox,_,_,_=mail
    settings=agent.store.settings();settings.admin_group='999@g.us';agent.store.put('settings',settings.model_dump())
    due=datetime.fromisoformat('2026-11-07T22:00:00-06:00')
    inbox.remind(due);assert agent.store.outbox()[0]['status']=='queued'
    stage(mail)
    original=inbox.followup_needed
    monkeypatch.setattr(GmailInbox,'followup_needed',lambda self,day,**kwargs: original(day,**(kwargs | {'now':due})))
    agent.deliver_outbox()
    assert agent.store.outbox()[0]['status']=='cancelled' and not agent.bridge.sent


def test_received_ppt_suppresses_deadline_reminder_but_missing_file_requires_followup(mail):
    agent,inbox,_,_,root=mail
    settings=agent.store.settings();settings.admin_group='999@g.us';agent.store.put('settings',settings.model_dump())
    stage(mail)
    due=datetime.fromisoformat('2026-11-07T22:00:00-06:00')
    inbox.remind(due);assert not agent.store.outbox()
    main_file(root).unlink()
    inbox.remind(due);assert len(agent.store.outbox())==1


def test_sunday_catchup_and_disabled_mail_config(mail):
    agent,inbox,_,_,_=mail
    settings=agent.store.settings();settings.admin_group='999@g.us';agent.store.put('settings',settings.model_dump())
    cfg=source.configuration();cfg['saturday_followup']=False
    source.config_path().write_text(json.dumps(cfg))
    due=datetime.fromisoformat('2026-11-08T08:00:00-06:00')
    inbox.remind(due);assert not agent.store.outbox()
    cfg['saturday_followup']=True;source.config_path().write_text(json.dumps(cfg))
    inbox.remind(due);assert len(agent.store.outbox())==1
    # Monday's next Sunday is not yet due; last week's reminder is not recreated.
    inbox.remind(datetime.fromisoformat('2026-11-09T08:00:00-06:00'))
    assert len(agent.store.outbox())==1


def test_mailbox_profile_mismatch_stops_before_any_message_read(tmp_path,monkeypatch):
    from backend import google_oauth_token
    from google.auth.transport import requests
    class Credentials:
        def has_scopes(self,scopes):return scopes==[source.SCOPE]
    calls=[]
    class Response:
        ok=True
        def json(self):return {'emailAddress':'wrong@example.com'}
    class Session:
        def __init__(self,*_):pass
        def get(self,url,**kwargs):calls.append(url);return Response()
    monkeypatch.setattr(google_oauth_token,'load_credentials',lambda *_:Credentials())
    monkeypatch.setattr(requests,'AuthorizedSession',Session)
    with pytest.raises(ValueError,match='帳號'):
        source.Gmail('expected@example.com',tmp_path/'token')
    assert calls==['https://gmail.googleapis.com/gmail/v1/users/me/profile']


def test_bad_mail_config_does_not_block_whatsapp_ack_or_bridge_configuration(agent,tmp_path,monkeypatch):
    from backend.tests.test_sunday_assistant import receive
    from backend.api import sunday_assistant
    cfg=tmp_path/'bad-mail.json';cfg.write_text('invalid')
    monkeypatch.setenv('SUNDAY_GMAIL_CONFIG',str(cfg))
    monkeypatch.setattr(sunday_assistant,'get_assistant',lambda:agent)
    result={'absence_changes':[{'worker':'王明','start':'2026-11-15','end':'2026-11-16','evidence':'我11/15到16不在'}]}
    receive(agent,result,group='111@g.us',text='我11/15到16不在')
    assert len(agent.bridge.sent)==1 and agent.bridge.sent[0][2].startswith('【請假登記回覆】')
    assert sunday_assistant.bridge_config()['enabled']
    assert not sunday_assistant.bridge_config()['allow_speaker_followup']


def test_sender_mapping_change_resumes_only_the_waiting_input(mail):
    agent,inbox,client,_,root=mail
    rows=stage(mail,sender='陳牧師 <new@example.com>')
    assert rows[0]['status']=='review'
    services=[{'date':'2026-11-08','speaker':'陳牧師','title':'盼望'}]
    inbox.resume_waiting(services)
    with inbox.db() as db:
        assert db.execute("SELECT status FROM mail_messages WHERE key='one'").fetchone()[0]=='processed'
    agent.repository.workers['陳牧師'].email='new@example.com'
    inbox.resume_waiting(services)
    with inbox.db() as db:
        row=dict(db.execute("SELECT * FROM mail_messages WHERE key='one'").fetchone())
    assert row['status']=='queued'
    inbox.process(row,client,services)
    assert main_file(root).exists()


def test_matching_and_review_answers_reused_across_mailboxes(mail):
    _,inbox,_,_,_=mail
    calls=[];classify=inbox.classifier;verify=inbox.verifier
    def classified(*args): calls.append('match');return classify(*args)
    def verified(*args): calls.append('review');return verify(*args)
    inbox.classifier=classified;inbox.verifier=verified
    original=deck()
    stage(mail,data=original)
    stage(mail,key='copy',mailbox='jian@example.com',data=original)
    assert calls==['match','review']


def test_scan_persists_entire_page_before_cursor_and_restart(mail):
    _,inbox,_,_,_=mail
    class Listing:
        def page(self,cursor):
            assert cursor is None
            return {'messages':[{'id':str(i)} for i in range(50)],'nextPageToken':'next'}
    inbox.scan('jun@example.com',Listing())
    with inbox.db() as db:
        assert db.execute('SELECT COUNT(*) FROM mail_messages').fetchone()[0]==50
    assert inbox.get('cursor:jun@example.com')=='next'


def test_large_attachment_is_input_review_without_download(mail):
    _,inbox,client,_,_=mail
    original=inbox.classifier
    def classify(message,services):
        message['attachments'][0]['size']=source.MAX_ATTACHMENT+1
        return original(message,services)
    inbox.classifier=classify
    assert stage(mail)[0]['status']=='review'
    assert client.calls==0


def test_malformed_package_metadata_is_input_error():
    import zipfile
    out=BytesIO()
    with zipfile.ZipFile(out,'w') as archive:
        archive.writestr('[Content_Types].xml','<broken')
        archive.writestr('ppt/presentation.xml','<broken')
    with pytest.raises(ValueError,match='完整'):
        validate_ppt(out.getvalue(),'.pptx')
