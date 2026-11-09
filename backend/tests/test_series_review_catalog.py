"""Series status and navigation follow intact Editor drafts without moving data."""
import copy

import pytest

from backend.api.sc_api.series_review_catalog import SeriesReviewCatalog
from backend.api.sc_api.transcript_review import atomic, file_hash

ITEM = 'INGEST-first'


def catalog(root, **changes):
    meta = dict(item=ITEM, status='processing', media_ingest_source_sha256='source')
    meta.update(changes)
    atomic(root/'config/sermon.json', [meta])
    return meta


def draft(root, **changes):
    meta = catalog(root, **changes)
    atomic(root/'script_review'/f'{ITEM}.json', [dict(index=1, text='原文')])
    context = dict(schema_version='transcript-review-context.v1', source_video_sha256='source',
                   script_sha256=file_hash(root/'script_review'/f'{ITEM}.json'))
    atomic(root/'transcript_review'/ITEM/'context.json', context)
    return meta, context


def test_private_beta_projects_owner_and_editor_without_mutating_catalog(tmp_path):
    prod, beta = tmp_path/'prod', tmp_path/'beta'
    original = catalog(prod)
    draft(beta, status='assigned', assigned_to='editor@example.com', assigned_to_name='同工')
    before = file_hash(prod/'config/sermon.json')
    result = SeriesReviewCatalog(prod, beta).for_item(ITEM)
    assert result['status'] == 'assigned'
    assert result['assigned_to'] == 'editor@example.com'
    assert result['editor_href'] == f'/admin/transcript-beta/{ITEM}'
    assert set(result) == {'status', 'assigned_to', 'assigned_to_name', 'assigned_to_date', 'published_date', 'editor_href'}
    assert original['status'] == 'processing'
    assert before == file_hash(prod/'config/sermon.json')
    # A later request sees an unclaim without waiting for the production watcher.
    catalog(beta, status='ready')
    assert SeriesReviewCatalog(prod, beta).for_item(ITEM)['status'] == 'ready'


@pytest.mark.parametrize('broken', ['missing', 'script', 'source', 'malformed'])
def test_incomplete_or_wrong_source_is_not_advertised_as_editor_ready(tmp_path, broken):
    prod, beta = tmp_path/'prod', tmp_path/'beta'
    catalog(prod)
    _, context = draft(beta, assigned_to='editor@example.com')
    path = beta/'transcript_review'/ITEM/'context.json'
    if broken == 'missing':
        path.unlink()
    elif broken == 'script':
        atomic(beta/'script_review'/f'{ITEM}.json', [])
    elif broken == 'source':
        context['source_video_sha256'] = 'another recording'
        atomic(path, context)
    else:
        atomic(path, [])
    assert SeriesReviewCatalog(prod, beta).for_item(ITEM) == {}


def test_production_draft_and_publication_take_precedence(tmp_path):
    prod, beta = tmp_path/'prod', tmp_path/'beta'
    draft(prod, status='published', assigned_to='production@example.com')
    draft(beta, status='assigned', assigned_to='beta@example.com')
    # The published flag alone does not prove a publication exists.
    assert SeriesReviewCatalog(prod, beta).for_item(ITEM)['status'] == 'assigned'
    atomic(prod/'script_published'/f'{ITEM}.json', [])
    result = SeriesReviewCatalog(prod, beta).for_item(ITEM)
    assert result['status'] == 'published'
    assert result['assigned_to'] == 'production@example.com'
    assert result['editor_href'] == f'/admin/surmons/{ITEM}'


def test_promotion_keeps_beta_url_and_reads_production_owner(tmp_path):
    prod, beta = tmp_path/'prod', tmp_path/'beta'
    draft(prod, assigned_to='new@example.com')
    draft(beta, assigned_to='old@example.com')
    receipt = prod/'transcript_review'/ITEM/'beta-promotion.json'
    atomic(receipt, dict(item=ITEM))
    marker = dict(schema_version='transcript-production-link.v1', item=ITEM,
                  production_root=str(prod.resolve()), receipt_sha256=file_hash(receipt))
    atomic(beta/'transcript_review'/ITEM/'production-link.json', marker)
    result = SeriesReviewCatalog(prod, beta).for_item(ITEM)
    assert result['assigned_to'] == 'new@example.com'
    assert result['editor_href'] == f'/admin/transcript-beta/{ITEM}'
    atomic(receipt, dict(item='wrong'))
    assert SeriesReviewCatalog(prod, beta).for_item(ITEM) == {}


def test_invalid_item_and_absent_drafts_leave_existing_metadata_alone(tmp_path):
    prod = tmp_path/'prod'
    meta = catalog(prod)
    saved = copy.deepcopy(meta)
    for item in [ITEM, '../secret', 'a/b']:
        assert SeriesReviewCatalog(prod).for_item(item) == {}
    assert meta == saved
