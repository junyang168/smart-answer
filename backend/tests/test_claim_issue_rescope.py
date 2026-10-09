"""Scope amendment is bounded, additive and never an implicit retry."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from backend.pipeline import claim_issue_rescope as a
from backend.pipeline import source_reconciliation as c
from backend.pipeline import source_reconciliation_runner as r
from backend.pipeline import claim_issue_management as m
from backend.api.canonical_repository.postgres_store import sha256_json
from backend.tests.test_claim_issue_management import issue_setup, materials, retain_proposal, ScopeStore
from backend.tests.test_source_reconciliation import scenario, put
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store
from backend.tests.test_cvp_sql_actions_postgres import source_run
from backend.tests.test_claim_issue_management import issue_for


@pytest.fixture
def amendment(issue_setup,tmp_path,monkeypatch):
    manifest,old,issue=materials(issue_setup,tmp_path)
    old['sql_scope']={'run_id':'original-run','read_pins':[]}
    root=tmp_path/'action'/'CVPA-old'
    r.write(root/'scoped-materials.json',old)
    packet=r.build_proposal_packet(old)
    r.write(root/'proposal.packet.json',packet)
    proposal=retain_proposal(old,issue)
    proposal.dispositions[0].action='unresolved'
    raw={'response':proposal.model_dump(mode='json'),'artifact_sha256':'fixture'}
    r.write(root/'proposal.raw.json',raw)
    monkeypatch.setattr(a,'validate_raw',lambda *args:raw)
    policy=m.Policy(authorization_id='amendment',authorization_quote='exact test scope',
        claim_fields=[],evidence_fields=[],link_fields=[],evidence_eligibility_review=True)
    direction={'schema_version':a.SCHEMA,'authorized_by':'owner','authorization_quote':'同意,繼續',
        'human_content_approval':False,'additional_proposal_limit':1,'run_id':'original-run',
        'campaign_sha256':'test','old_action_root':str(root),'old_action_id':root.name,
        'new_policy':put(tmp_path/'new-policy.json',policy.model_dump()),
        'evidence_ids':['E-test'],'link_permissions':[],'context_claim_ids':[],
        **{n:r.binding(root/f) for n,f in [('old_materials','scoped-materials.json'),('old_packet','proposal.packet.json'),('old_raw','proposal.raw.json')]}}
    direction['artifact_sha256']=sha256_json(direction)
    return direction,old,raw,root


def seal(value):
    value['artifact_sha256']=sha256_json({k:v for k,v in value.items() if k!='artifact_sha256'})
    return value


def test_original_answer_stays_unresolved_and_scope_does_not_approve(amendment):
    direction,old,raw,root=amendment
    assert a.validate_direction(direction)==(old,raw)
    assert raw['response']['dispositions'][0]['action']=='unresolved'
    assert raw['response']['records']==[]
    assert not direction['human_content_approval']


@pytest.mark.parametrize('change',['owner','human_pass','limit','run','raw_sha','root','policy','duplicate_evidence','unknown_evidence'])
def test_scope_cannot_drift_or_reset(amendment,change):
    direction,_,_,_=amendment
    d=deepcopy(direction)
    if change=='owner':d['authorized_by']='model'
    elif change=='human_pass':d['human_content_approval']=True
    elif change=='limit':d['additional_proposal_limit']=2
    elif change=='run':d['run_id']='another-group'
    elif change=='raw_sha':d['old_raw']['file_sha256']='a'*64
    elif change=='root':d['old_action_root']+='/foreign'
    elif change=='policy':d['new_policy']=d['old_raw']
    elif change=='duplicate_evidence':d['evidence_ids']*=2
    else:d['evidence_ids']=['foreign']
    with pytest.raises(ValueError):a.validate_direction(seal(d))


@pytest.mark.parametrize('stage',['initial_review','correction','final_review','final_review_input_repair'])
@pytest.mark.parametrize('suffix',['reservation.json','raw.json','raw.call-intent.json'])
def test_consumed_or_unknown_review_never_acquires_amendment(amendment,stage,suffix):
    direction,_,_,root=amendment
    r.write(root/(stage+'.'+suffix),{'existing':'must retain'})
    with pytest.raises(ValueError,match='budget'):a.validate_direction(direction)


@pytest.mark.parametrize('fields',[['viewpoint_id'],['claim_id'],['link_type'],['effective_state','component_locator'],['statement']])
def test_link_amendment_never_acquires_endpoint_or_core_write(amendment,fields):
    direction,_,_,_=amendment
    d=deepcopy(direction)
    d['link_permissions']=[{'collection':'viewpoint_claim_links','object_id':'L-test','fields':fields}]
    with pytest.raises(ValueError):a.validate_direction(seal(d))


def test_amendment_single_sql_commit_saved_answers_rollback_and_original_budget_retained(postgres_store,issue_setup,tmp_path,monkeypatch):
    from backend.tests.test_claim_evidence_eligibility_review import EligibilityPorts
    from backend.pipeline.cvp_sql_actions import prepare_source_action,source_action_id,ScopedSourceModelStages
    manifest,states,report,policy=issue_setup
    states=deepcopy(states)
    states['evidence_steps/E-test'].update(support_eligibility='context_only',valid_for_identity_review=False,
        speaker='professor',stance='asserted',discourse_role='dramatic_paraphrase_of_Jesus')
    store=postgres_store;run=source_run(store,(manifest,states,{}));issue=issue_for(states,report)
    store.register_claim_issue(run,issue.model_dump(mode='json'))
    work=tmp_path/'issues'/'original'
    scoped=m.manifest_for_issues(store,run,[issue],policy,work)
    aid=source_action_id(scoped);oldroot=work/'action'/aid
    old=prepare_source_action(store,run,scoped.model_dump(),root=oldroot)
    store.bind_claim_issue_action([issue.issue_id],run_id=run,action_id=aid)
    # A saved diagnosis is not approval. These synthetic transport fixtures do
    # not replace real production raw authentication (read-only test below).
    packet=r.build_proposal_packet(old['materials']);r.write(oldroot/'proposal.packet.json',packet)
    p=retain_proposal(old['materials'],issue);p.dispositions[0].action='unresolved'
    raw=r.write(oldroot/'proposal.raw.json',{'response':p.model_dump(mode='json')})
    monkeypatch.setattr(a,'validate_raw',lambda *args:raw)
    newpolicy=m.Policy(authorization_id='newscope',authorization_quote='bounded fixture',human_content_approval=False,
        claim_fields=[],evidence_fields=[],link_fields=[],evidence_eligibility_review=True)
    direction=seal({'schema_version':a.SCHEMA,'authorized_by':'owner','authorization_quote':'同意,繼續',
        'human_content_approval':False,'additional_proposal_limit':1,'run_id':run,
        'campaign_sha256':store.get_cvp_group(run)['campaign_sha256'],'old_action_root':str(oldroot),'old_action_id':aid,
        'new_policy':put(tmp_path/'newpolicy.json',newpolicy.model_dump()),'evidence_ids':['E-test'],
        'link_permissions':[],'context_claim_ids':[],**{n:r.binding(oldroot/f) for n,f in
        [('old_materials','scoped-materials.json'),('old_packet','proposal.packet.json'),('old_raw','proposal.raw.json')]}})
    authorization=put(tmp_path/'direction.json',direction)
    ports=EligibilityPorts(issue)
    monkeypatch.setattr('backend.pipeline.cvp_sql_flow.verified_group_backup',lambda *args:{'dump':report,'restore_catalog_nonempty':True})
    original=store._assert_transaction_edges_and_reviews
    def fail(*args):raise ValueError('late amendment rollback')
    monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',fail)
    with pytest.raises(ValueError,match='late amendment rollback'):
        a.execute(store,run,authorization=authorization,root=tmp_path/'issues',ports=ports)
    assert store.get_record('evidence_steps','E-test')['support_eligibility']=='context_only'
    monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',original)
    result=a.execute(store,run,authorization=authorization,root=tmp_path/'issues',ports=ports)
    assert result['status']=='applied'
    assert ports.calls==['proposal','initial_review']
    assert len(store.get_change_history(result['change_set_id'])['operations'])==1
    row=store.get_claim_issue(issue.issue_id)
    assert row['action']=={'run_id':run,'action_id':aid}
    assert row['scope_amendment']['action_id']!=aid
    assert store.get_record('claims','CL-test')==states['claims/CL-test']
    assert r.read(oldroot/'proposal.raw.json')==raw
    assert a.execute(store,run,authorization=authorization,root=tmp_path/'issues',ports=ports)['status']=='already_applied'
    assert ports.calls==['proposal','initial_review']


@pytest.mark.skipif(os.environ.get('WKP_G094_ELIGIBILITY_READONLY')!='1',reason='explicit saved production packet read-only check')
def test_actual_g094_scope_complete_current_context_without_registry_writes(tmp_path):
    from dotenv import load_dotenv
    load_dotenv('.env')
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.cvp_sql_flow import pins,pin_rows
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059')
    path=root/'owner-g094-joint-management-scope-amendment.json'
    direction=json.loads(path.read_text());store=PostgresKnowledgeStore()
    old,raw=a.validate_live(store,direction)
    before_group=store.get_cvp_group(direction['run_id'])
    manifest=a.amend_manifest(store,direction,r.binding(path),tmp_path/'scope')
    keys=[c.split_key(k) for k in c.index(old['before_records'])]
    keys += [('claims',cid) for cid in direction['context_claim_ids']]
    records=store.read_affected_context(keys)
    states={c.key(col,oid):row['payload'] for (col,oid),row in records.items() if not row['retired']}
    frozen=c.freeze(manifest,states)
    packet=r.build_proposal_packet(frozen)
    selected=c.index(packet['records'])
    assert 'claims/DK-da9666998a59c56d3a70-P01-CL002' in selected
    assert 'claims/DK-e50720f3684d-P01-CL001' not in selected
    source_ids={s['source_id'] for s in packet['context_sources']}|{manifest.source_id}
    evidence=selected['claims/DK-da9666998a59c56d3a70-P01-CL002']['evidence_step_ids']
    for e in evidence:
        for f in m.bound_fragments([selected[c.key('evidence_steps',e)]],lambda fid:selected[c.key('source_fragments',fid)]):
            assert f['source_id'] in source_ids
    assert {p.object_id for p in manifest.permissions if p.collection=='evidence_steps'}==set(direction['evidence_ids'])
    assert {p.object_id for p in manifest.permissions if p.collection=='viewpoint_claim_links'}=={p['object_id'] for p in direction['link_permissions']}
    assert not any(p.collection in {'claims','canonical_viewpoints','viewpoint_revisions','source_documents'} for p in manifest.permissions)
    assert raw['response']['records']==[] and any(d['action']=='unresolved' for d in raw['response']['dispositions'])
    assert store.get_cvp_group(direction['run_id'])==before_group
    from backend.pipeline.source_reconciliation_runtime import model_adapter
    from backend.pipeline.viewpoint_resolution_runtime import response_adapter
    adapter=response_adapter(model_adapter('proposal',issue=True,evidence_eligibility=True),None)
    print('G094 amended full proposal request bytes',adapter.request_bytes(packet))
    print('G094 complete current member context; unchanged SQL group; no model or Registry writes; original raw retained')
