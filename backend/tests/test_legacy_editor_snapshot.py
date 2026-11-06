import json
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.sc_api.legacy_editor_snapshot import legacy_editor_document
from backend.api.sc_api.transcript_review import atomic, file_hash, ReviewError
from backend.api.sc_api.transcript_review_router import create_review_router
from backend.tests.test_transcript_review import setup, signed


def saved_root(root, item, text):
    atomic(root / 'config/sermon.json', [{'item': item, 'title': text, 'status': 'ready'}])
    atomic(root / 'script' / f'{item}.json', {'entries': [
        {'index': '1', 'start': '00:00:01,000', 'end': '00:00:05,000'}]})
    for folder in ('script_review', 'script_patched'):
        atomic(root / folder / f'{item}.json', [{'index': '1', 'type': 'content', 'text': text}])


def promoted(tmp_path):
    item = '舊版講道'
    root = tmp_path / 'live'
    backup = tmp_path / 'backup'
    saved_root(root, item, '全新稿件')
    saved_root(backup / 'production', item, '人工修改過的舊稿')
    files = {str(p.relative_to(backup / 'production')): {'sha256': file_hash(p)}
             for p in (backup / 'production').rglob('*.json')}
    atomic(backup / 'backup.json', {'schema_version': 'transcript-promotion-backup.v1',
        'intent': {'item': item, 'production_root': str(root)}, 'files': {'production': files}})
    atomic(root / 'transcript_review' / item / 'beta-promotion.json', {
        'schema_version': 'transcript-production-promotion.v1', 'item': item,
        'production_root': str(root), 'backup_root': str(backup),
        'backup_sha256': file_hash(backup / 'backup.json')})
    return root, backup, item


def test_promoted_editor_uses_old_text_metadata_and_clocks_without_writes(tmp_path):
    root, backup, item = promoted(tmp_path)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    doc = legacy_editor_document(root, item)
    assert doc['header']['title'] == '人工修改過的舊稿'
    assert doc['script'][0]['text'] == '人工修改過的舊稿'
    assert doc['script'][0]['end_time'] == 5
    assert doc['read_only'] and doc['script_sha256'] is None
    assert {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
    assert legacy_editor_document(root, item, changes=True)['script']


@pytest.mark.parametrize('target', ['backup.json', 'production/config/sermon.json',
    'production/script_review/舊版講道.json', 'production/script/舊版講道.json',
    'production/script_patched/舊版講道.json'])
def test_snapshot_corruption_never_falls_back_to_new_text(tmp_path, target):
    root, backup, item = promoted(tmp_path)
    (backup / target).write_text('[]')
    with pytest.raises(ReviewError):
        legacy_editor_document(root, item)


def test_unmigrated_and_original_adoption_snapshot(tmp_path):
    root = tmp_path / 'live'; item = '講道'
    saved_root(root, item, '原有逐字稿')
    assert legacy_editor_document(root, item)['script'][0]['text'] == '原有逐字稿'
    atomic(root / 'transcript_review' / item / 'context.json', {})
    with pytest.raises(ReviewError, match='找不到'):
        legacy_editor_document(root, item)
    atomic(root / 'transcript_review' / item / 'legacy-baseline.json', {
        'script': [{'index': '1', 'text': '搬遷之前'}],
        'patched': [{'index': '1', 'text': '初稿'}],
        'timeline': {'entries': [{'index': '1', 'start': '00:00:01,000', 'end': '00:00:05,000'}]}})
    assert legacy_editor_document(root, item)['script'][0]['text'] == '搬遷之前'


def test_legacy_endpoint_requires_current_signed_reader_and_no_store(setup, monkeypatch):
    store, root, source = setup
    item = "講道 test"
    saved_root(root, item, "尚未搬遷的舊稿")
    monkeypatch.setenv('TRANSCRIPT_REVIEW_ACTION_SECRET', 'test-secret')
    app = FastAPI(); app.include_router(create_review_router(root, role_root=root, beta_owner="a"), prefix='/sc_api')
    client = TestClient(app)
    path = '/sc_api/review_document/' + quote(item, safe='') + '/legacy'
    assert client.get(path).status_code == 401
    response = client.get(path, headers=signed('GET', path, actor='a'))
    assert response.status_code == 200, response.text
    assert response.json()['read_only'] is True
    assert client.get(path, headers=signed("GET", path, actor="b")).status_code == 403
    assert response.headers['cache-control'] == 'private, no-store'
    body = json.dumps({"version": "unused", "operation_id": "not-a-write"}).encode()
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert client.post(path, content=body, headers={**signed("POST", path, body, actor="a"),
        "if-match": "unused"}).status_code == 404
    assert {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
