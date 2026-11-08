"""Source-only scope, safe literal calibration, and legacy preparation regressions."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from backend.pipeline import claim_flash_source as source, claim_update_flash as flash
from backend.tests.test_claim_flash_source_assembly import scene


def fixture(tmp_path, texts=None, quotes=None):
    records, sources = scene(tmp_path, texts or ['教授原話。'], quotes or [('S0001', '教授原話。')])
    snap = sources['S']
    records['source_documents/S'] = {'retired': False, 'payload': {
        'source_id': 'S', 'source_type': 'sermon_transcript',
        'source_path': snap['file']['path'], 'source_file_sha256': snap['file']['file_sha256'],
        'source_body_sha256': snap['body_sha256'], 'source_sha256': snap['body_sha256']}}
    return records, sources


def test_source_stage_does_not_judge_or_change_statement_roles_or_qualification(tmp_path):
    records, sources = fixture(tmp_path)
    records['evidence_steps/E']['payload'].update(statement='此摘要故意與原文不相符。',
        stance='opposed', support_eligibility='withheld_unreviewed')
    before = deepcopy((records, sources))
    result = source.verify(records, sources, 'S')
    assert result['status'] == 'verified' and result['model_calls'] == result['database_writes'] == 0
    assert result['claim_qualification'] == 'not_granted'
    assert result['timestamp_precision'] == 'paragraph_not_word'
    assert not result['calibration_candidates']['updates'] and (records, sources) == before


def test_unique_move_returns_binding_preview_without_changing_shared_history(tmp_path):
    records, sources = fixture(tmp_path, ['別的內容。', '教授原話。'], [('S0002', '教授原話。')])
    records['source_fragments/F0']['payload']['paragraph_key'] = 'S0001'
    records['observations/O'] = {'retired': False, 'payload': {
        'observation_id': 'O', 'statement': '保留觀察。', 'source_fragment_ids': ['F0'],
        'argument_role': 'load_bearing', 'scripture_refs': ['馬太福音 1:1']}}
    before = deepcopy(records)
    result = source.verify(records, sources, 'S')
    assert result['status'] == 'calibration_available'
    updates = result['calibration_candidates']['updates']
    assert set(updates) == {'evidence_steps/E', 'observations/O'}
    assert updates['evidence_steps/E']['source_fragment_ids'] == updates['observations/O']['source_fragment_ids']
    assert len(result['calibration_candidates']['new_fragments']) == 1
    for key, post in updates.items():
        assert {k:v for k,v in post.items() if k not in {'source_fragment_id','source_fragment_ids'}} == {
            k:v for k,v in before[key]['payload'].items() if k not in {'source_fragment_id','source_fragment_ids'}}
    assert records == before


@pytest.mark.parametrize('field,value', [
    ('source_sha256','old'), ('paragraph_text_sha256','wrong'), ('verbatim_excerpt_sha256','wrong'),
    ('source_segment_index','other'), ('media_time',999), ('media_end_time',999)])
def test_each_binding_dimension_requires_calibration_not_a_false_valid(field, value, tmp_path):
    records, sources = fixture(tmp_path)
    records['source_fragments/F0']['payload'][field] = value
    result = source.verify(records, sources, 'S')
    assert result['status'] == 'calibration_available'
    assert result['records'][0]['status'] == 'calibration_available'
    assert not result['fragments'][0]['source_verified']


def test_all_singular_and_plural_fragments_are_checked(tmp_path):
    records, sources = fixture(tmp_path, ['教授原話。','另一段。'], [('S0001','教授原話。'),('S0002','另一段。')])
    p = records['evidence_steps/E']['payload'];p.update(source_fragment_id='F0',source_fragment_ids=['F1'])
    records['source_fragments/F1']['payload']['verbatim_excerpt']='不存在的原話。'
    result = source.verify(records, sources, 'S')
    assert result['status'] == 'needs_attention'
    assert result['records'][0]['fragment_ids'] == ['F0','F1']
    assert {x['fragment_id']:x['status'] for x in result['fragments']} == {'F0':'valid','F1':'unresolved'}


@pytest.mark.parametrize('failure', ['missing','foreign','deleted','ambiguous'])
def test_unresolved_sources_are_never_approved_or_resurrected(tmp_path, failure):
    records, sources = fixture(tmp_path, ['教授原話。','教授原話。'], [('S0001','教授原話。')])
    if failure == 'missing':del records['source_fragments/F0']
    elif failure == 'foreign':records['source_fragments/F0']['payload']['source_id']='OTHER'
    elif failure == 'deleted':records['source_fragments/F0']['payload']['anchor_state']='source_deleted'
    else:records['source_fragments/F0']['payload']['source_sha256']='old'
    before = deepcopy(records)
    result = source.verify(records, sources, 'S')
    assert result['status'] == 'needs_attention' and result['records'][0]['status'] == 'unresolved'
    assert not result['calibration_candidates']['updates'] and records == before


def test_unused_historical_fragments_are_disclosed_not_forced_into_current_use(tmp_path):
    records, sources = fixture(tmp_path)
    old=deepcopy(records['source_fragments/F0']);old['payload'].update(fragment_id='OLD',anchor_state='source_deleted')
    records['source_fragments/OLD']=old
    result=source.verify(records,sources,'S')
    assert result['status']=='verified'
    assert result['fragments'][1]['status']=='historical' and not result['fragments'][1]['source_verified']


def test_context_exclusion_is_revoked_when_claim_uses_it(tmp_path):
    records,sources=fixture(tmp_path)
    records['source_fragments/F0']['payload']['anchor_state']='source_deleted'
    records['evidence_steps/E']['payload'].update(step_type='dialogue_context',support_eligibility='context_only',produced_claim_ids=[])
    result=source.verify(records,sources,'S')
    assert result['records'][0]['status']=='historical_excluded' and result['status']=='verified'
    assert result['fragments'][0]['status']=='historical_excluded'
    records['claims/C']={'retired':False,'payload':{'claim_id':'C','evidence_step_ids':['E']}}
    assert source.verify(records,sources,'S')['status']=='needs_attention'


def test_protected_record_has_no_mechanical_update(tmp_path):
    records,sources=fixture(tmp_path)
    records['source_fragments/F0']['payload']['source_sha256']='old'
    records['evidence_steps/E']['payload']['review_status']='human_approved'
    result=source.verify(records,sources,'S')
    assert result['status']=='needs_attention' and not result['calibration_candidates']['updates']


def test_visual_semantics_and_word_alignment_do_not_become_this_release_gates(tmp_path):
    records,sources=fixture(tmp_path)
    records['source_fragments/F0']['payload'].update(source_modality='visual',verbatim_excerpt='')
    result=source.verify(records,sources,'S')
    assert result['status']=='verified' and result['records'][0]['status']=='future_release'
    assert result['fragments'][0]['status']=='future_release' and not result['fragments'][0]['source_verified']
    assert set(result['future_release'])=={'visual_semantics','word_level_audio_alignment'}


def test_registered_body_or_physical_file_drift_cannot_pass(tmp_path):
    records,sources=fixture(tmp_path)
    records['source_documents/S']['payload']['source_body_sha256']='other'
    with pytest.raises(ValueError,match='registered source body differs'):source.verify(records,sources,'S')
    records['source_documents/S']['payload']['source_body_sha256']=sources['S']['body_sha256']
    Path(sources['S']['file']['path']).write_text('{}')
    with pytest.raises(ValueError,match='source changed'):source.verify(records,sources,'S')


def test_wrong_fragment_identity_is_not_hidden_by_valid_quote(tmp_path):
    records,sources=fixture(tmp_path)
    records['source_fragments/F0']['payload']['fragment_id']='OTHER'
    result=source.verify(records,sources,'S')
    assert result['status']=='needs_attention' and result['records'][0]['reason']=='fragment_identity_mismatch'


def test_cli_needs_no_models_capacity_or_claim_list(tmp_path,monkeypatch,capsys):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
    records,sources=fixture(tmp_path)
    class Store:
        def get_record_state(self,col,oid):return records[col+'/'+oid]
    monkeypatch.setattr(PostgresKnowledgeStore,'__init__',lambda *a,**kw:None)
    monkeypatch.setattr(source.f,'bind_source',lambda *a:dict(source_id='S',path=sources['S']['file']['path'],file_sha256=sources['S']['file']['file_sha256']))
    monkeypatch.setattr(source.f,'load_source',lambda *a,**kw:(records,sources))
    monkeypatch.setattr(CodexSubscriptionClient,'__init__',lambda *a,**kw:pytest.fail('model created'))
    monkeypatch.setattr(sys,'argv',['flash','--verify-source','--source-id','S','--root',str(tmp_path/'report')])
    flash.main()
    result=json.loads(capsys.readouterr().out)
    assert result['stage']=='source' and result['status']=='verified'
    assert not (tmp_path/'report'/'commit-intent.json').exists()
    assert not (tmp_path/'report'/'verification.json').exists()
    assert source.run(Store(),'S',tmp_path/'report')==result
    records['evidence_steps/E']['payload']['source_fragment_ids']=[]
    with pytest.raises(ValueError,match='saved input differs'):source.run(Store(),'S',tmp_path/'report')


@pytest.mark.parametrize('extra',[['--apply'],['--plan-groups'],['--groups','anything'],['--semantic-groups']])
def test_source_cli_refuses_mutating_or_model_modes_before_database(tmp_path,monkeypatch,extra):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    monkeypatch.setattr(PostgresKnowledgeStore,'__init__',lambda *a,**kw:pytest.fail('database created'))
    monkeypatch.setattr(sys,'argv',['flash','--verify-source','--source-id','S','--root',str(tmp_path),*extra])
    with pytest.raises(SystemExit) as exc:flash.main()
    assert exc.value.code==2


def test_source_pin_drift_is_rejected_before_publishing(tmp_path,monkeypatch):
    records,sources=fixture(tmp_path)
    monkeypatch.setattr(source.f,'bind_source',lambda *a:{'source_id':'S','path':'old','file_sha256':'old'})
    monkeypatch.setattr(source.f,'load_source',lambda *a,**kw:(records,sources))
    with pytest.raises(ValueError,match='between binding and census'):
        source.run(None,'S',tmp_path/'report')
    assert not (tmp_path/'report'/'source-verification.json').exists()
