from copy import deepcopy
from pathlib import Path
import os
import pytest

from backend.pipeline.claim_evidence_binding import coverage_context


@pytest.mark.parametrize('fault',[None,'membership','pending','checkpoint','commit','history','postimage','dependency','audit_pin'])
def test_actual_sequential_handoff_readonly(monkeypatch,fault):
    if not os.environ.get('WKP_EVIDENCE_COVERAGE_ACTUAL'):
        pytest.skip('explicit actual current SQL readonly test')
    from dotenv import load_dotenv
    load_dotenv('.env')
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline import viewpoint_resolution_runtime as runtime
    store=PostgresKnowledgeStore()
    run=os.environ['WKP_EVIDENCE_COVERAGE_ACTUAL']
    saved=store.get_cvp_group(run)
    definition=store.get_cvp_campaign(saved['campaign_sha256'])
    group=next(g for g in definition['groups'] if g['group_key']==saved['group_key'])
    root=Path(saved['reviewed']['evidence']['package']['payload']['source_repair_application']['run_root'])
    original={p:p.read_bytes() for p in root.iterdir() if p.is_file()}
    if fault=='membership':group={**group,'claim_ids':group['claim_ids']+['foreign']}
    elif fault=='pending':saved['reviewed']['evidence']['pending_cvp_claim_ids'].append('foreign')
    elif fault=='checkpoint':saved['checkpoint']='prepared'
    elif fault in {'commit','history'}:
        history=store.get_change_history
        def changed(cid):
            h=history(cid)
            if fault=='commit':h['status']='reviewed'
            else:h['operations'][0]['after_revision']+=1
            return h
        monkeypatch.setattr(store,'get_change_history',changed)
    elif fault=='postimage':
        get=store.get_record_state
        def changed(col,oid):
            row=get(col,oid)
            if col=='evidence_steps':row['payload']['statement']='foreign'
            return row
        monkeypatch.setattr(store,'get_record_state',changed)
    elif fault=='dependency':
        context=store.read_cvp_context
        def changed(ids):
            rows=context(ids)
            rows['viewpoint_claim_links','foreign']={'retired':False,'payload':{'claim_id':ids[0],'effective_state':'active'}}
            return rows
        monkeypatch.setattr(store,'read_cvp_context',changed)
    elif fault=='audit_pin':
        read=runtime.read_artifact
        def changed(path):
            value=read(path)
            if str(path).endswith('shared-owner-claim-audit/completion.json'):
                cid=value['claim_ids'][0];value['claim_pins'][cid][0]+=1
            return value
        monkeypatch.setattr(runtime,'read_artifact',changed)
    if fault:
        with pytest.raises(ValueError):coverage_context(store,saved,group)
    else:
        result=coverage_context(store,saved,group)
        assert result['coverage_claim_ids']==group['claim_ids']
        assert result['campaign_membership_changes']==0
        assert result['existing_cvp_content_approved'] is False
        assert result['pending_existing_cvp_record_keys']==saved['reviewed']['evidence']['pending_cvp_management_record_keys']
        assert all(not d['active_cvp_links'] and not d['new_coverage_authority'] for d in result['audited_dependency_impacts'])
    assert all(p.read_bytes()==v for p,v in original.items())
