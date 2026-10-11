"""Partial accounting never approves remaining pending business records."""
from copy import deepcopy
import os
import pytest
from backend.pipeline import cvp_source_impact_resolution as impact
from backend.api.canonical_repository.postgres_store import record_content_sha,sha256_json


def fixture(col):
    payload={'revision':1,'review_status':'candidate','relation_type':'supports','from_id':'E1','to_id':'E2'}
    retired=col=='knowledge_relations'
    if not retired:payload={'revision':2,'review_status':'independent_terminal_reviewed','support_eligibility':'eligible','statement':'own source'}
    after={**payload,'revision':2}
    sha=record_content_sha(payload)
    row={'payload':payload,'revision':2,'content_sha256':sha,'retired':retired}
    op={'collection':col,'object_id':'item','operation':'retire' if retired else 'update',
        'before_revision':1,'after_revision':2,'before_sha256':'old','after_sha256':sha,
        'before':payload,'after':after}
    review={'plan':{'change_set_id':'KCS','fingerprint_sha256':'fingerprint','source_sha256':'source','operations':[deepcopy(op)]},
            'evidence':{'approval':{'exact':True}}}
    history={'change_set_id':'KCS','status':'applied','source_kind':'wang_claim_relation_retirement_v1',
        'fingerprint_sha256':'fingerprint','source_sha256':'source','operations':[op],
        'metadata':{'reviewed_sha256':sha256_json(review),'approval':review['evidence']['approval']}}
    return row,history,review


@pytest.mark.parametrize('col',['knowledge_relations','evidence_steps'])
def test_partial_current_committed_generation_only(col):
    row,h,review=fixture(col)
    item=impact.pending_commit_item(col+'/item',row,h,review)
    assert item['pin']==[2,row['content_sha256'],col=='knowledge_relations']
    assert 'group_complete' not in item and 'approved' not in item


@pytest.mark.parametrize('fault',['missing','hold','sha','version','changed_after','missing_operation','extra_write','fingerprint','status','approval','review','source','operation','retired','qualification'])
@pytest.mark.parametrize('col',['knowledge_relations','evidence_steps'])
def test_partial_commit_refuses_drift_and_unapproved_fields(col,fault):
    row,h,review=fixture(col)
    if fault=='missing':row=None
    elif fault=='hold':row['payload']['review_status']='human_hold';row['content_sha256']=record_content_sha(row['payload'])
    elif fault=='sha':row['content_sha256']='wrong'
    elif fault=='version':row['revision']=3
    elif fault=='changed_after':h['operations'][0]['after']['extra']='foreign'
    elif fault=='missing_operation':h['operations']=[]
    elif fault=='extra_write':h['operations'].append({**h['operations'][0],'object_id':'foreign'})
    elif fault in {'fingerprint','source'}:h[fault+'_sha256']='wrong'
    elif fault=='status':h['status']='prepared'
    elif fault=='approval':h['metadata']['approval']={'foreign':True}
    elif fault=='review':h['metadata']['reviewed_sha256']='wrong'
    elif fault=='operation':h['operations'][0]['operation']='create'
    elif fault=='retired':row['retired']=not row['retired']
    elif fault=='qualification':
        if col=='knowledge_relations':h['source_kind']='ordinary_model_package'
        else:row['payload']['support_eligibility']='withheld_missing_anchor';row['content_sha256']=record_content_sha(row['payload'])
    with pytest.raises(ValueError):impact.pending_commit_item(col+'/item',row,h,review)


def test_actual_saved_pending_commit_accounting_readonly(monkeypatch):
    run=os.environ.get('WKP_SOURCE_PENDING_ACTUAL')
    if not run:pytest.skip('explicit actual readonly source pending test')
    from dotenv import load_dotenv
    load_dotenv('.env')
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline import source_reconciliation_runner as source,viewpoint_resolution_runtime as runtime
    def forbidden(*a,**kw):raise AssertionError('saved-only verification must not write/call')
    monkeypatch.setattr(source,'write',forbidden)
    monkeypatch.setattr(runtime,'write_immutable',forbidden)
    store=PostgresKnowledgeStore();value=store.get_cvp_group(run)
    before=deepcopy(value)
    result=impact.verify_pending_commits(store,value)
    assert value==before and store.get_cvp_group(run)==before
    assert {i['record_key'] for i in result['items']}=={'knowledge_relations/AR-0041','evidence_steps/L3-E057'}
    assert len(result['pending_record_keys'])==16 and len(result['remaining_record_keys'])==14
    assert not result['group_complete'] and not result['content_write_authority']
    assert result['new_model_calls']==result['registry_writes']==0
