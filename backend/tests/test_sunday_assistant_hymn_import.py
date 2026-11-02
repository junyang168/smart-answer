from datetime import datetime

import pytest

from backend.api.models import HymnMetadata, SundaySong
from backend.sunday_assistant import hymn_import as h
from backend.sunday_assistant.grounding import GroundingVerdict
from backend.sunday_assistant.models import ChatMessage
from backend.tests.test_sunday_assistant import agent

RAW = '測試聖詩\n1 甲乙丙丁，戊己庚辛。\n2 壬癸天地，玄黃宇宙。'
FORMATTED = '甲乙丙丁，\n戊己庚辛。\n\n壬癸天地，\n玄黃宇宙。'


@pytest.fixture
def hymns(agent, monkeypatch):
    metadata = HymnMetadata(index=17, title='測試聖詩', lyricsUrl='https://fcccc.net/hymns/Hymns_HymnsForChurch/017.htm')
    calls = {'fetch': 0, 'create': 0, 'model': 0}
    def get_metadata(number):
        if number != 17: raise ValueError('not found')
        return metadata
    def create(payload):
        calls['create'] += 1
        song = SundaySong(id='imported', **payload.model_dump())
        agent.repository.songs.append(song)
        return song
    def update(id, payload):
        song=SundaySong(id=id, **payload.model_dump())
        agent.repository.songs=[song if old.id==id else old for old in agent.repository.songs]
        return song
    def complete(schema, *_):
        calls['model'] += 1
        return h.LyricLayout(start_line=1,end_line=2,lyrics=FORMATTED) if schema==h.LyricLayout else GroundingVerdict(supported=True)
    def fetch(_):
        calls['fetch'] += 1
        return RAW
    monkeypatch.setattr(agent.repository, 'get_hymn_metadata', get_metadata, raising=False)
    monkeypatch.setattr(agent.repository, 'create_sunday_song', create, raising=False)
    monkeypatch.setattr(agent.repository, 'update_sunday_song', update, raising=False)
    monkeypatch.setattr(h, 'complete', complete)
    monkeypatch.setattr(h, 'fetch_lyrics_text', fetch)
    settings=agent.store.settings();settings.songs_group='333@g.us';agent.store.put('settings',settings.model_dump())
    msg=ChatMessage(message_id='hymn-import',group_id='333@g.us',sender_id='member',sent_at=datetime.fromisoformat('2026-11-02T16:00:00+00:00'),text='11月8日主日詩歌用聖詩第17首測試聖詩。')
    agent.store.receive(msg)
    return agent,msg,metadata,calls


def test_import_formats_all_stanzas_preserves_source_and_is_idempotent(hymns):
    agent,msg,metadata,calls=hymns
    assert h.lookup_hymnal(agent,17)['lyrics_present'] is False
    result=h.import_hymnal(agent,msg,17)
    assert result['status']=='imported'
    assert agent.repository.songs[-1].lyrics_markdown==FORMATTED
    assert calls=={'fetch':1,'create':1,'model':3}
    assert h.import_hymnal(agent,msg,17)['status']=='existing'
    assert calls=={'fetch':1,'create':1,'model':3}
    with agent.store.db() as db:
        import json
        receipt=json.loads(db.execute("SELECT value FROM kv WHERE key LIKE 'hymn_import:%'").fetchone()[0])
    assert receipt['raw_source']==RAW
    assert receipt['status']=='applied' and receipt['source_sha256'] and receipt['lyrics_sha256']


def test_existing_manual_lyrics_are_never_overwritten(hymns):
    agent,msg,_,calls=hymns
    agent.repository.songs.append(SundaySong(id='manual',title='測試聖詩',hymnalIndex=17,source='hymnal',lyricsMarkdown='人工修訂'))
    assert h.import_hymnal(agent,msg,17)['preserved_existing_lyrics']
    assert agent.repository.songs[-1].lyrics_markdown=='人工修訂'
    assert calls=={'fetch':0,'create':0,'model':0}


def test_existing_entry_with_empty_lyrics_is_filled_without_duplicate(hymns):
    agent,msg,_,calls=hymns
    agent.repository.songs.append(SundaySong(id='empty',title='測試聖詩',hymnalIndex=17,source='hymnal'))
    assert h.import_hymnal(agent,msg,17)['status']=='imported'
    assert calls['create']==0 and len(agent.repository.songs)==2
    assert agent.repository.songs[-1].id=='empty'
    assert agent.repository.songs[-1].lyrics_markdown==FORMATTED


@pytest.mark.parametrize('lyrics',[FORMATTED.replace('甲','乙'),FORMATTED.split('\n\n')[0],FORMATTED+'\n新添歌詞'])
def test_changed_or_missing_lyric_words_are_rejected_before_import(hymns,monkeypatch,lyrics):
    agent,msg,_,calls=hymns
    monkeypatch.setattr(h,'complete',lambda schema,*_: h.LyricLayout(start_line=1,end_line=2,lyrics=lyrics) if schema==h.LyricLayout else GroundingVerdict(supported=True))
    with pytest.raises(ValueError,match='改動或遺漏'):
        h.import_hymnal(agent,msg,17)
    assert calls['create']==0


def test_independent_check_rejects_a_source_range_that_drops_a_whole_stanza(hymns,monkeypatch):
    agent,msg,_,calls=hymns
    count=0
    def incomplete(schema,*_):
        nonlocal count
        count+=1
        if schema==h.LyricLayout:return h.LyricLayout(start_line=1,end_line=1,lyrics='甲乙丙丁，戊己庚辛。')
        return GroundingVerdict(supported=count==1,questions=['來源還有第二節，不能只取第一節'] if count>1 else [])
    monkeypatch.setattr(h,'complete',incomplete)
    with pytest.raises(ValueError,match='完整性'):
        h.import_hymnal(agent,msg,17)
    assert calls['create']==0


def test_manual_edit_during_fetch_is_preserved(hymns,monkeypatch):
    agent,msg,_,calls=hymns
    def fetch(_):
        agent.repository.songs.append(SundaySong(id='manual',title='測試聖詩',hymnalIndex=17,lyricsMarkdown='人工先完成'))
        return RAW
    monkeypatch.setattr(h,'fetch_lyrics_text',fetch)
    assert h.import_hymnal(agent,msg,17)['status']=='existing'
    assert agent.repository.songs[-1].lyrics_markdown=='人工先完成'
    assert calls['create']==0


def test_pause_during_preparation_reuses_prepared_lyrics_on_resume(hymns,monkeypatch):
    agent,msg,_,calls=hymns
    def fetch(_):
        calls['fetch']+=1
        settings=agent.store.settings();settings.enabled=False;agent.store.put('settings',settings.model_dump())
        return RAW
    monkeypatch.setattr(h,'fetch_lyrics_text',fetch)
    with pytest.raises(RuntimeError,match='暫停'):
        h.import_hymnal(agent,msg,17)
    settings=agent.store.settings();settings.enabled=True;agent.store.put('settings',settings.model_dump())
    assert h.import_hymnal(agent,msg,17)['status']=='imported'
    assert calls=={'fetch':1,'create':1,'model':3}


@pytest.mark.parametrize('preview', [True,False])
def test_history_and_preview_do_not_import(hymns,preview):
    agent,msg,_,calls=hymns
    if preview:
        settings=agent.store.settings();settings.auto_apply=False;agent.store.put('settings',settings.model_dump())
    else:msg.initial_sync=True
    assert h.import_hymnal(agent,msg,17)['status']=='preview_only'
    assert calls=={'fetch':0,'create':0,'model':0}


def test_wrong_group_or_unrelated_evidence_cannot_import(hymns):
    agent,msg,_,calls=hymns
    with pytest.raises(ValueError,match='當前消息'):
        h.import_hymnal(agent,msg,17,'not-in-this-case')
    msg.group_id='111@g.us'
    with pytest.raises(ValueError,match='不是詩歌來源'):
        h.import_hymnal(agent,msg,17)
    assert calls['create']==0


def test_network_failure_does_not_create_an_empty_song(hymns,monkeypatch):
    agent,msg,_,calls=hymns
    monkeypatch.setattr(h,'fetch_lyrics_text',lambda _: '')
    with pytest.raises(ValueError,match='空白'):
        h.import_hymnal(agent,msg,17)
    assert calls['create']==0


def test_layout_normalization_does_not_drop_numeric_lyric_words():
    assert h.normalize_lyric_words('100 years of song')=='100yearsofsong'
    assert h.normalize_lyric_words('1 測試歌詞')=='測試歌詞'
