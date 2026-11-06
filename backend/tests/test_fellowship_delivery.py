from datetime import date,datetime
import json
import sys
import smtplib

import pytest

from backend.fellowship_delivery import send_once
from backend.fellowship_reminder_status import record_fellowship_email_sent


def test_manual_and_scheduled_delivery_share_deduplication_and_legacy_status(tmp_path):
    status=tmp_path/'last_sent.json';calls=[]
    day=date(2026,11,20)
    assert send_once(day,lambda:calls.append('send'),status_file=status)
    assert not send_once(day,lambda:calls.append('again'),status_file=status)
    assert calls==['send']
    old=date(2026,11,6);record_fellowship_email_sent(old,status)
    assert not send_once(old,lambda:calls.append('old duplicate'),status_file=status)
    assert calls==['send']


def test_uncertain_delivery_is_never_blindly_repeated(tmp_path):
    status=tmp_path/'last_sent.json';calls=[]
    def interrupted():calls.append('send');raise TimeoutError('SMTP reply was lost')
    with pytest.raises(TimeoutError):send_once(date(2026,11,20),interrupted,status_file=status)
    with pytest.raises(RuntimeError,match='結果不明'):send_once(date(2026,11,20),lambda:calls.append('again'),status_file=status)
    assert calls==['send']


def test_definitely_unsubmitted_connection_failures_have_a_finite_retry_budget(tmp_path):
    status=tmp_path/'last_sent.json'
    def offline():raise ConnectionRefusedError('not connected')
    for _ in range(3):
        with pytest.raises(ConnectionRefusedError):send_once(date(2026,11,20),offline,status_file=status)
    with pytest.raises(RuntimeError,match='三次'):send_once(date(2026,11,20),offline,status_file=status)


def test_interrupted_process_reservation_blocks_automatic_duplicate(tmp_path):
    root=tmp_path/'fellowship-delivery';root.mkdir()
    (root/'2026-11-20.json').write_text(json.dumps({'status':'sending','attempts':1}))
    with pytest.raises(RuntimeError,match='結果不明'):send_once(date(2026,11,20),lambda:pytest.fail('must not send'),status_file=tmp_path/'last_sent.json')


@pytest.mark.parametrize('now,complete,expected',[
 ('2026-11-19T07:59:59-06:00',True,0),
 ('2026-11-19T10:00:00-06:00',False,0),
 ('2026-11-19T15:00:00-06:00',True,1),
 ('2026-11-20T10:00:00-06:00',True,1),
 ('2026-11-20T19:30:00-06:00',True,0),
])
def test_email_catch_up_after_details_are_completed_and_stops_before_meeting(tmp_path,monkeypatch,now,complete,expected):
    from backend import fellowship_reminder_job as job
    when=datetime.fromisoformat(now)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return when.astimezone(tz) if tz else when
    monkeypatch.setattr(job,'datetime',Clock)
    event=job.FellowshipEvent(date=date(2026,11,20),host='王明' if complete else None,title='饒恕',series='馬太福音18章')
    monkeypatch.setattr(job,'load_fellowship_events',lambda *_:[event])
    monkeypatch.setattr(job,'load_notification_recipients',lambda *_:['test@example.com'])
    monkeypatch.setattr(job,'BASE_DIR',str(tmp_path));monkeypatch.setattr(job,'STATUS_FILE',str(tmp_path/'last_sent.json'))
    calls=[];monkeypatch.setattr(job,'send_email',lambda *_:calls.append('sent'))
    monkeypatch.setenv('SMTP_HOST','test.invalid');monkeypatch.setattr(sys,'argv',['job','--catch-up'])
    job.main();assert len(calls)==expected
    if expected:job.main();assert len(calls)==1
