from datetime import date, datetime
from pathlib import Path

from pptx import Presentation
from pptx.util import Inches

from backend.api import service
from backend.api.models import FellowshipEntry
from backend.api.ppt_generator import _update_fellowship_labels
from backend.api.sc_api.fellowship_schedule import compute_next_fellowship
from backend.fellowship_email import build_fellowship_email_content
from backend.fellowship_timing import TIMEZONE, START_TIME, next_biweekly_date
from backend.tests.test_sunday_fellowship import fellowship, agent


def test_transition_keeps_biweekly_cycle_and_historical_dates():
    assert next_biweekly_date(date(2026,11,20)) == date(2026,12,3)
    assert next_biweekly_date(date(2026,12,3)) == date(2026,12,17)
    entries = [{'date':'11/20/2026','host':'支勇','title':'已完成'}]
    target, updated, changed = compute_next_fellowship(entries,datetime(2026,11,22))
    assert target == '12/03/2026'
    assert updated == entries and not changed


def test_dallas_20_hour_follows_summer_and_winter():
    assert datetime.combine(date(2027,1,7),START_TIME,TIMEZONE).utcoffset().total_seconds() == -6*3600
    assert datetime.combine(date(2027,7,8),START_TIME,TIMEZONE).utcoffset().total_seconds() == -5*3600
    subject,text,html=build_fellowship_email_content(event_date=date(2026,12,3))
    assert '週四晚 8:00（美國中部時間）' in subject
    assert '週四晚 8:00（美國中部時間）' in html
    assert 'Thursday at 8:00 PM Central Time' in text
    assert '7:30' not in html


def test_ppt_upcoming_two_thursdays_match_real_schedule(monkeypatch):
    monkeypatch.setattr(service,'list_fellowships',lambda:[FellowshipEntry(date='12/03/2026')])
    replacements={}
    service._populate_fellowship_replacements(replacements,datetime(2026,11,22))
    assert replacements == {'fellowDate1':'11/26','hasFellow1':'無','fellowDate2':'12/03','hasFellow2':'有'}


def test_ppt_label_preserves_style_and_handles_split_runs(tmp_path):
    deck=Presentation();slide=deck.slides.add_slide(deck.slide_layouts[6])
    frame=slide.shapes.add_textbox(0,0, Inches(4),Inches(1)).text_frame
    frame.paragraphs[0].add_run().text='週五'
    frame.paragraphs[0].add_run().text='团契'
    first=frame.paragraphs[0].runs[0];first.font.bold=True
    _update_fellowship_labels(deck)
    path=tmp_path/'thursday.pptx';deck.save(path)
    frame=Presentation(path).slides[0].shapes[0].text_frame
    assert frame.text=='週四線上查經'
    assert frame.paragraphs[0].runs[0].font.bold


def test_thursday_event_requests_next_sunday_and_reminds_following_thursday(fellowship):
    agent,worker,entries=fellowship
    entries.clear()
    entries['12/03/2026']=FellowshipEntry(date='12/03/2026',host='支勇',title='查經',series='馬太福音')
    worker.maintain(datetime.fromisoformat('2026-12-06T14:59:00-06:00'))
    assert not agent.store.outbox()
    worker.maintain(datetime.fromisoformat('2026-12-06T15:00:00-06:00'))
    assert len(agent.store.outbox())==1
    assert worker.cases()[0]['target_day']=='2026-12-17'
    worker.maintain(datetime.fromisoformat('2026-12-10T08:59:00-06:00'))
    assert len(agent.store.outbox())==1
    worker.maintain(datetime.fromisoformat('2026-12-10T09:00:00-06:00'))
    assert len(agent.store.outbox())==2
    worker.maintain(datetime.fromisoformat('2026-12-10T10:00:00-06:00'))
    assert len(agent.store.outbox())==2


def test_saved_schedule_migration_keeps_history_and_retargets_pending_case(tmp_path):
    import json, sqlite3, subprocess, sys
    (tmp_path/'config').mkdir();(tmp_path/'sunday-assistant').mkdir()
    records=[{'date':'11/20/2026','title':'歷史主題','emailSubject':'舊通知'},
             {'date':'12/04/2026','host':'王明','title':'下次主題'}]
    path=tmp_path/'config/fellowship.json';path.write_text(json.dumps(records))
    database=tmp_path/'sunday-assistant/assistant.sqlite3'
    with sqlite3.connect(database) as db:
        db.executescript("CREATE TABLE outbox(key TEXT,status TEXT); CREATE TABLE fellowship_changes(target_day TEXT); CREATE TABLE fellowship_cases(previous_day TEXT,target_day TEXT,data TEXT);")
        db.execute('INSERT INTO fellowship_cases VALUES (?,?,?)',('2026-11-20','2026-12-04','{"confirmed":{}}'))
    command=[sys.executable,str(Path(__file__).parents[2]/'scripts/migrate-fellowship-thursday.py'),'--data-root',str(tmp_path),'--apply']
    subprocess.run(command,check=True,capture_output=True)
    after=json.loads(path.read_text())
    assert after[0]==records[0]
    assert after[1]=={**records[1],'date':'12/03/2026'}
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT * FROM fellowship_cases').fetchone()==('2026-11-20','2026-12-03','{"confirmed":{}}')
    subprocess.run(command,check=True,capture_output=True)
    assert json.loads(path.read_text())==after
    assert len(list((tmp_path/'notification').glob('fellowship-thursday-backup-*')))==1
