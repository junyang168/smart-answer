import json
from types import SimpleNamespace

import pytest

from backend.api.sc_api.sermon_publication import FIELDS, SCHEMA_VERSION, publication_fields
from backend.api.sc_api.transcript_review import atomic, file_hash, read
from backend.pipeline.sermon_publication_migration import apply, plan


@pytest.fixture
def legacy(tmp_path):
    root = tmp_path/'data'
    item = 'legacy-sermon'
    atomic(root/'config/config.json', {'users': [{'id': 'publisher', 'name': '發佈同工'}]})
    atomic(root/'config/sermon.json', [{'item': item, 'title': '原標題', 'status': 'published',
                                      'assigned_to': 'editor', 'published_date': '2025-11-24 12:04'}])
    atomic(root/'script_published'/f'{item}.json', {'metadata': {
        'status': 'published', 'author': 'editor', 'last_updated': '2025-11-24T18:04:20Z',
    }, 'script': [{'index': 1, 'text': '原稿正文'}]})
    event = dict(item=item, context='publish', actor_id='publisher', actor_name='發佈時姓名',
                 timestamp='2025-11-24T18:04:20.123456Z',
                 changes={'published_date': {'old': None, 'new': '2025-11-24 12:04'}})
    log = root/'logs/sermon_meta_audit.log'
    log.parent.mkdir()
    log.write_text(json.dumps(event, ensure_ascii=False)+'\n')
    return root, item, event, log


def test_legacy_normalization_and_idempotence_preserve_every_manuscript_byte(legacy, tmp_path):
    root, item, _, log = legacy
    path = root/'script_published'/f'{item}.json'
    before = path.read_bytes()
    catalog_before = (root/'config/sermon.json').read_bytes()
    log.write_text('broken partial entry\n'+log.read_text())
    planned = plan(root)
    entry = planned['entries'][0]
    assert entry['fields']['published_by'] == 'publisher'
    assert entry['fields']['published_by_name'] == '發佈同工'
    assert entry['fields']['published_date'] == '2025-11-24T12:04:00-06:00'
    assert entry['evidence'] == 'legacy_publish_audit'
    report = apply(planned, tmp_path/'receipt')
    assert report['state'] == 'complete' and report['migrated'] == 1
    assert read(root/'config/sermon.json')[0]['title'] == '原標題'
    assert path.read_bytes() == before
    assert (tmp_path/'receipt/catalog.before.json').read_bytes() == catalog_before
    assert report['manuscripts'][0]['before_sha256'] == report['manuscripts'][0]['after_sha256']
    assert plan(root)['entries'] == []


@pytest.mark.parametrize('broken', ['item', 'date', 'timestamp', 'context', 'conflict', 'absent'])
def test_unbound_or_ambiguous_audit_never_supplies_a_publisher(legacy, broken):
    root, item, event, log = legacy
    if broken == 'item':
        event['item'] = 'other-sermon'
    elif broken == 'date':
        event['changes']['published_date']['new'] = '2025-11-23 12:04'
    elif broken == 'timestamp':
        event['timestamp'] = '2025-11-24T18:04:21Z'
    elif broken == 'context':
        event['context'] = 'unclaim'
    if broken == 'absent':
        log.unlink()
    elif broken == 'conflict':
        log.write_text(json.dumps(event)+'\n'+json.dumps({**event, 'actor_id': 'other-publisher'})+'\n')
    else:
        log.write_text(json.dumps(event)+'\n')
    entry = plan(root)['entries'][0]
    assert entry['fields']['published_by'] is None
    assert entry['fields']['published_by_name'] is None
    assert entry['fields']['publication_schema_version'] == SCHEMA_VERSION


def test_review_publication_uses_bound_revision_and_one_common_record(legacy):
    root, item, _, _ = legacy
    history = dict(kind='publish', revision=8, operation_id='release', actor='publisher',
                   time='2026-11-10T02:00:00Z')
    path = root/'transcript_review'/item/'revisions/00000008.json'
    atomic(path, history)
    atomic(root/'script_published'/f'{item}.json', {'metadata': {
        'status': 'published', 'review_revision': 8, 'review_operation_id': 'release',
        'review_snapshot_sha256': file_hash(path),
    }, 'script': []})
    entry = plan(root)['entries'][0]
    assert entry['fields'] == publication_fields('publisher', '發佈同工', history['time'])
    assert entry['evidence'] == 'publish_revision'
    history['actor'] = 'unbound-actor'
    atomic(path, history)
    assert plan(root)['entries'][0]['fields']['published_by'] is None


@pytest.mark.parametrize('target', ['snapshot', 'catalog', 'audit'])
def test_migration_fails_closed_on_changed_saved_inputs(legacy, tmp_path, target):
    root, item, _, log = legacy
    planned = plan(root)
    path = {'snapshot': root/'script_published'/f'{item}.json',
            'catalog': root/'config/sermon.json', 'audit': log}[target]
    path.write_bytes(path.read_bytes()+b'\n')
    before = (root/'config/sermon.json').read_bytes()
    with pytest.raises(ValueError, match='input changed'):
        apply(planned, tmp_path/'receipt')
    assert (root/'config/sermon.json').read_bytes() == before


def test_legacy_editor_writes_same_record_and_stale_edits_preserve_it(tmp_path):
    from backend.api.sc_api.sermon_manager import SermonManager
    from backend.api.sc_api.sermon_meta import SermonMetaManager

    root = tmp_path/'data'
    item = 'legacy-sermon'
    users = {'publisher': {'name': '發佈同工'}, 'editor': {'name': '認領同工'}}
    atomic(root/'config/sermon.json', [{'item': item, 'status': 'assigned', 'assigned_to': 'editor'}])
    atomic(root/'script'/f'{item}.json', {'entries': [
        {'index': '1', 'start': '00:00:00,000', 'end': '00:00:05,000', 'text': '正文'},
    ]})
    atomic(root/'script_review'/f'{item}.json', [{'index': '1', 'end_index': '1', 'text': '正文'}])
    stale = SermonMetaManager(str(root), lambda actor: users.get(actor, {}))
    manager = SermonManager.__new__(SermonManager)
    manager.base_folder = str(root)
    manager._acl = SimpleNamespace(get_user=lambda actor: users.get(actor, {}))
    manager._sm = SermonMetaManager(str(root), manager._acl.get_user)
    manager._audit_fields = ()
    manager.get_sermon_permissions = lambda *_: SimpleNamespace(canPublish=True)
    manager.publish('publisher', item)
    catalog = read(root/'config/sermon.json')[0]
    published = read(root/'script_published'/f'{item}.json')['metadata']
    assert {k: catalog[k] for k in FIELDS} == {k: published[k] for k in FIELDS}
    assert catalog['published_by'] == 'publisher' and catalog['published_by_name'] == '發佈同工'
    stale.sermons[0].assigned_to = None
    stale.save_sermon_metadata()
    assert {k: read(root/'config/sermon.json')[0][k] for k in FIELDS} == {k: catalog[k] for k in FIELDS}
