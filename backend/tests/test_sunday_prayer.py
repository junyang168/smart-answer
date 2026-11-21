import json
import pytest
from backend.api import storage
from backend.api.models import SundayServiceEntry


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_BASE_DIR', str(tmp_path))
    for name in ['SUNDAY_SERVICE_FILE','SUNDAY_SONGS_FILE','SUNDAY_WORKERS_FILE']:
        path = tmp_path / (name+'.json'); path.write_text('[]')
        monkeypatch.setattr(storage,name,path)
    monkeypatch.setattr(storage.ArticleRepository,'_sunday_today',staticmethod(lambda:'2026-11-21'))
    return object.__new__(storage.ArticleRepository)


def test_migrate_last_prayer_and_share_with_existing_future_skeletons(repo):
    repo._save_sunday_service_entries([
        SundayServiceEntry(date='2026-11-15',health_prayer_markdown='请为身体健康祷告。'),
        SundayServiceEntry(date='2026-11-22',sermonTitle='本週題目'),
        SundayServiceEntry(date='2026-12-06')])
    shared=repo.get_sunday_prayer()
    assert shared['markdown']=='請爲身體健康禱告。'
    assert shared['source_date']=='2026-11-15'
    assert repo.get_sunday_service('2026-11-22').health_prayer_markdown==shared['markdown']
    assert repo.get_sunday_service('2026-11-22').sermon_title=='本週題目'
    assert repo.get_sunday_service('2026-11-15').health_prayer_markdown=='请为身体健康祷告。'
    repo.create_sunday_service(SundayServiceEntry(date='2026-12-13'))
    assert repo.get_sunday_service('2026-12-13').health_prayer_markdown==shared['markdown']


def test_shared_update_overrides_stale_weekly_form_and_keeps_history(repo):
    repo._save_sunday_service_entries([SundayServiceEntry(date='2026-11-15',health_prayer_markdown='舊內容'),SundayServiceEntry(date='2026-11-22')])
    before=repo.get_sunday_prayer()
    current=repo.save_sunday_prayer('新的代祷事项',before['revision'])
    stale=repo.get_sunday_service('2026-11-22').model_copy(update={'health_prayer_markdown':'STALE'})
    repo.update_sunday_service(stale.date,stale)
    assert repo.get_sunday_service(stale.date).health_prayer_markdown==current['markdown']
    assert repo.get_sunday_service('2026-11-15').health_prayer_markdown=='舊內容'
    with pytest.raises(ValueError,match='其他同工更新'):
        repo.save_sunday_prayer('不能覆蓋',before['revision'])
    cleared=repo.save_sunday_prayer('',current['revision'])
    assert repo.get_sunday_prayer()['markdown']==''
    assert repo.get_sunday_service(stale.date).health_prayer_markdown==''
    assert cleared['revision']!=current['revision']


def test_received_prose_traditional_preserves_links_identifiers_and_slides(repo):
    url='https://example.org/讲道?q=简体'
    entry=repo.create_sunday_service(SundayServiceEntry(date='2026-11-22',sermonTitle='微小的声音',hymnNotes='第一段\n\n回应诗歌 '+url,presider='刘纳',scripture=['1ki-19-1-18']))
    assert entry.sermon_title=='微小的聲音'
    assert entry.hymn_notes=='第一段\n\n回應詩歌 '+url
    assert entry.presider=='刘纳' and entry.scripture==['1ki-19-1-18']


def test_week_rollover_freezes_previous_sunday_snapshot(repo,monkeypatch):
    repo._save_sunday_service_entries([SundayServiceEntry(date='2026-11-15',health_prayer_markdown='上週'),SundayServiceEntry(date='2026-11-22')])
    shared=repo.get_sunday_prayer()
    shared=repo.save_sunday_prayer('本週代禱',shared['revision'])
    monkeypatch.setattr(storage.ArticleRepository,'_sunday_today',staticmethod(lambda:'2026-11-23'))
    repo.save_sunday_prayer('下週代禱',shared['revision'])
    assert repo.get_sunday_service('2026-11-22').health_prayer_markdown=='本週代禱'
    repo.create_sunday_service(SundayServiceEntry(date='2026-11-29'))
    assert repo.get_sunday_service('2026-11-29').health_prayer_markdown=='下週代禱'


def test_shared_api_routes_and_conflict_status(repo,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.router import sunday_service_router
    from backend.api import service
    monkeypatch.setattr(service,'repository',repo)
    app=FastAPI();app.include_router(sunday_service_router)
    client=TestClient(app)
    original=client.get('/admin/sunday-services/prayer')
    assert original.status_code==200 and original.headers['cache-control']=='no-store'
    payload={'markdown':'请为教会祷告','revision':original.json()['revision']}
    updated=client.put('/admin/sunday-services/prayer',json=payload)
    assert updated.status_code==200 and updated.json()['markdown']=='請爲教會禱告'
    assert client.put('/admin/sunday-services/prayer',json=payload).status_code==409
