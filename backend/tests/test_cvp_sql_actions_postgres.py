"""Same SQL boundary for synthetic source/Claim revisions, not production calls."""
from copy import deepcopy
from pathlib import Path
import uuid

import pytest

from backend.api.canonical_repository.postgres_store import canonical_json,record_content_sha,ChangeSetConflict
from backend.pipeline.cvp_sql_actions import execute_source_action
from backend.pipeline.source_reconciliation import split_key
from backend.tests.test_source_reconciliation import scenario,FakePorts
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store


def source_run(store,scenario):
    manifest,states,_=scenario
    with store.connect() as conn:
        for key,payload in states.items():
            col,oid=split_key(key)
            conn.execute("""INSERT INTO wang_knowledge.objects(collection,object_id,revision,review_status,visibility,content_sha256,payload)
                            VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)""",(col,oid,payload['revision'],payload.get('review_status','candidate'),
                            payload.get('visibility','internal'),record_content_sha(payload),canonical_json(payload)))
    campaign=store.initialize_cvp_campaign({'accepted_grouping_sha256':uuid.uuid4().hex*2,
        'groups':[{'group_key':'g1','sequence':1,'claim_ids':['CL-test']}]})
    return store.prepare_cvp_group(campaign_sha256=campaign,group_key='g1',sequence=1,claim_ids=['CL-test'],materials={'fixture':'synthetic source action'})


def test_source_claim_workflow_uses_actual_transaction_and_no_legacy_ports(postgres_store,scenario,tmp_path):
    store=postgres_store;run=source_run(store,scenario);manifest,states,new=scenario
    ports=FakePorts(manifest,states,new,tmp_path)
    def forbidden(*args,**kwargs):raise AssertionError('legacy snapshot/apply must not execute')
    ports.snapshot=ports.apply=ports.plan=ports.readback=forbidden
    result=execute_source_action(store,run,manifest.model_dump(),root=tmp_path/'source-action',ports=ports)
    assert result['status']=='applied'
    assert ports.calls==['proposal','initial_review']
    assert store.get_cvp_group(run)['checkpoint']=='applied'
    assert store.get_record('claims','CL-test')['statement']=='甲是假设，不是断言。'
    history=store.get_change_history(result['change_set_id'])
    assert len(history['operations'])==6
    from backend.pipeline.cvp_sql_flow import current_claim_authority
    authority=current_claim_authority(store,'CL-test',store.get_record_state('claims','CL-test'))
    assert authority['decision']=='independent_terminal_reviewed'
    assert len(history['review_events'])==1
    assert execute_source_action(store,run,manifest.model_dump(),root=tmp_path/'source-action',ports=ports)['status']=='already_applied'
    assert ports.calls==['proposal','initial_review']
    assert len(store.get_change_history(result['change_set_id'])['operations'])==6


def test_source_late_validation_failure_rollback_and_saved_answers_reused(postgres_store,scenario,tmp_path,monkeypatch):
    store=postgres_store;run=source_run(store,scenario);manifest,states,new=scenario
    ports=FakePorts(manifest,states,new,tmp_path)
    before=deepcopy(store.get_record_state('claims','CL-test'))
    actual=store._assert_transaction_edges_and_reviews
    def failure(*args):raise ValueError('late source transaction guard')
    monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',failure)
    with pytest.raises(ValueError,match='late source transaction'):
        execute_source_action(store,run,manifest.model_dump(),root=tmp_path/'source-action',ports=ports)
    assert store.get_record_state('claims','CL-test')==before
    assert store.get_cvp_group(run)['checkpoint']=='reviewed'
    monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',actual)
    execute_source_action(store,run,manifest.model_dump(),root=tmp_path/'source-action',ports=ports)
    assert ports.calls==['proposal','initial_review']
