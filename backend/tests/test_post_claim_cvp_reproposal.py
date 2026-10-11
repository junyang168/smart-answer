"""Source-first handoff is not content approval or a retry budget."""
from copy import deepcopy
from types import SimpleNamespace
import os

import pytest

from backend.pipeline import claim_first_management as m
from backend.pipeline import cvp_sql_flow as flow
from backend.pipeline import viewpoint_validation_context as context
from backend.pipeline.source_reconciliation_runner import write
from backend.pipeline.viewpoint_resolution_runtime import write_immutable
from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres, postgres_store


@pytest.fixture
def handoff(tmp_path,monkeypatch):
    root=tmp_path/'source'
    packet=root/'final_review.packet.json'
    write(packet,{'claim_first_management':{'schema_version':m.VERSION},'proposal_sha256':'proposal',
        'source_first_pending_cvp':{'pending_record_keys':['canonical_viewpoints/CV-old','viewpoint_claim_links/VCL-old']}})
    audit=root/'claim-first-audit/audit.json';write_immutable(audit,{'fixture':'actual bound audit'})
    payload={'evidence_step_id':'E1','revision':2}
    row={'payload':payload,'revision':2,'content_sha256':record_content_sha(payload),'retired':False}
    receipt_path=root/'claim-first-audit/claim-layer-completion.json'
    raw=root/'final_review.raw.json';raw_value=write(raw,{'stage':'final_review'})
    program=root/'final_review.raw.program-binding.json'
    write_immutable(program,{'raw_artifact_sha256':raw_value['artifact_sha256'],
        'bound_response':{'proposal_sha256':'proposal','verdicts':[
            {'record_key':k,'approved':False,'problems':[m.PENDING_PROBLEM]}
            for k in ['canonical_viewpoints/CV-old','viewpoint_claim_links/VCL-old']]}})
    receipt={'schema_version':m.VERSION,'status':'claim_layer_audited_cvp_pending',
        'claim_ids':['CL1'],'change_set_ids':['KCS-source'],'read_pins':flow.pin_rows(flow.pins({('evidence_steps','E1'):row})),
        'audit':flow.binding(audit),'source_proofs':[],
        'cvp_management_complete':False,'group_complete':False,'next_group_started':False}
    write_immutable(receipt_path,receipt)
    saved={'run_id':'run','checkpoint':'applied','claim_ids':['CL1'],'change_set_ids':['KCS-source'],
        'reviewed_sha256':'original-sql-review',
        'claim_layer_audit':{'status':receipt['status'],'receipt':flow.binding(receipt_path),'cvp_management_complete':False},
        'reviewed':{'plan':{'change_set_id':'KCS-source'},'evidence':{
            'pending_cvp_claim_ids':['CL1'],'action':{'action_id':'original-source-action'},
            'approval':{'terminal_review':flow.binding(raw),'final_review_program_binding':flow.binding(program)},
            'package':{'payload':{'source_repair_application':{'run_root':str(root),'run_id':'run',
                'review_sha256':raw_value['artifact_sha256'],'proposal_sha256':'proposal'}}}}}}
    history={'status':'applied','operations':[{'collection':'evidence_steps'}]}
    monkeypatch.setattr(context,'read_current_objects',lambda store,keys:{('evidence_steps','E1'):row})
    store=SimpleNamespace(get_change_history=lambda cid:history)
    return saved,store,row,history,root


def test_source_first_receipt_supersedes_only_uncommitted_cvp_proposal(handoff):
    saved,store,_,_,_=handoff;before=deepcopy(saved)
    result=m.coverage_context(store,saved,{'claim_ids':['CL1']})
    assert saved==before
    assert result['source_only_approval'] is True
    assert result['existing_cvp_content_approved'] is False
    assert result['schema_version']==m.REPROPOSAL_VERSION
    assert result['pending_existing_cvp_record_keys']==['canonical_viewpoints/CV-old','viewpoint_claim_links/VCL-old']
    assert result['old_proposals_and_budgets_preserved'] is True
    assert 'distinct viewpoint' in result['instructions']
    assert 'repeat a rejected' in result['instructions']


@pytest.mark.parametrize('change',['checkpoint','marker','complete','scope','pending','run','receipt_path',
    'receipt_sha','receipt_body','audit_sha','pin_drift','payload_drift','uncommitted','no_operations','cvp_committed',
    'foreign_changeset','no_scope','duplicate_scope','foreign_scope'])
def test_no_missing_stale_or_cvp_approval_can_enter_new_coverage(handoff,change):
    saved,store,row,history,root=handoff
    if change=='checkpoint':saved['checkpoint']='prepared'
    elif change=='marker':saved['claim_layer_audit']['status']='running'
    elif change=='complete':saved['claim_layer_audit']['cvp_management_complete']=True
    elif change=='scope':saved['claim_ids']=['foreign']
    elif change=='pending':saved['reviewed']['evidence']['pending_cvp_claim_ids']=[]
    elif change=='run':saved['reviewed']['evidence']['package']['payload']['source_repair_application']['run_id']='foreign'
    elif change=='receipt_path':saved['claim_layer_audit']['receipt']['path']=str(root/'other.json')
    elif change=='receipt_sha':saved['claim_layer_audit']['receipt']['file_sha256']='wrong'
    elif change=='receipt_body':
        from backend.pipeline.viewpoint_resolution_runtime import read_artifact
        p=root/'claim-first-audit/claim-layer-completion.json';r=read_artifact(p);r['group_complete']=True
        p.write_text(__import__('json').dumps(r));saved['claim_layer_audit']['receipt']=flow.binding(p)
    elif change=='audit_sha':(root/'claim-first-audit/audit.json').write_text('{}')
    elif change=='pin_drift':row['revision']=3
    elif change=='payload_drift':row['payload']['revision']=3
    elif change=='uncommitted':history['status']='planned'
    elif change=='no_operations':history['operations']=[]
    elif change=='cvp_committed':history['operations'][0]['collection']='viewpoint_claim_links'
    elif change=='foreign_changeset':saved['reviewed']['plan']['change_set_id']='foreign'
    else:
        p=root/'final_review.packet.json'
        values={'no_scope':[],'duplicate_scope':['viewpoint_claim_links/V','viewpoint_claim_links/V'],
            'foreign_scope':['claims/CL1']}
        write(p,{'claim_first_management':True,'source_first_pending_cvp':{'pending_record_keys':values[change]}},immutable=False)
    with pytest.raises(ValueError):m.coverage_context(store,saved,{'claim_ids':['CL1']})


def test_legacy_coverage_contract_is_unchanged():
    assert m.coverage_context(None,{},None) is None


@pytest.mark.parametrize('change',['raw_sha','raw_file','program_file','proposal','pending_verdict'])
def test_source_terminal_review_cannot_be_relabelled_as_identity_approval(handoff,change):
    saved,store,_,_,root=handoff
    application=saved['reviewed']['evidence']['package']['payload']['source_repair_application']
    if change=='raw_sha':application['review_sha256']='foreign'
    elif change=='raw_file':(root/'final_review.raw.json').write_text('{}')
    elif change=='program_file':(root/'final_review.raw.program-binding.json').write_text('{}')
    elif change=='proposal':application['proposal_sha256']='foreign'
    else:
        from backend.pipeline.viewpoint_resolution_runtime import read_artifact
        p=root/'final_review.raw.program-binding.json';value=read_artifact(p)
        value['bound_response']['verdicts'][0]['approved']=True;p.write_text(__import__('json').dumps(value))
        saved['reviewed']['evidence']['approval']['final_review_program_binding']=flow.binding(p)
    with pytest.raises(ValueError):m.coverage_context(store,saved,{'claim_ids':['CL1']})


def test_sql_completion_rejects_pending_existing_impacts_even_with_new_coverage(postgres_store,tmp_path):
    import uuid
    from backend.api.canonical_repository.postgres_store import canonical_json, ChangeSetConflict
    store=postgres_store
    campaign=store.initialize_cvp_campaign({'accepted_grouping_sha256':uuid.uuid4().hex*2,
        'groups':[{'sequence':1,'group_key':'g1','claim_ids':['CL1']}]})
    rid=store.prepare_cvp_group(campaign_sha256=campaign,group_key='g1',sequence=1,claim_ids=['CL1'],materials={})
    meta=store.get_cvp_group(rid);meta['checkpoint']='applied'
    meta['reviewed']={'evidence':{'approval':{},'package':{'payload':{}},'pending_cvp_claim_ids':[],
        'pending_cvp_management_record_keys':['viewpoint_claim_links/VCL-old']}}
    with store.connect() as conn:
        conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s',(canonical_json(meta),rid))
    audit=tmp_path/'audit.json';write_immutable(audit,{})
    before=store.get_cvp_group(rid)
    with pytest.raises(ChangeSetConflict,match='pending existing'):
        store.complete_cvp_group(rid,audit={},audit_binding=flow.binding(audit),read_pins={})
    assert store.get_cvp_group(rid)==before


@pytest.mark.parametrize('case',['new','saved','legacy_cache','update_old','source_write','foreign_claim'])
def test_common_coverage_consumes_current_contract_and_keeps_existing_impacts(tmp_path,monkeypatch,case):
    from backend.pipeline import cvp_sql_actions as actions
    saved={'run_id':'run','checkpoint':'applied','claim_ids':['CL1'],'claim_layer_audit':{'status':'done'},
        'reviewed_sha256':'same-parent-review','reviewed':{'evidence':{
            'package':{'payload':{}},'pending_cvp_claim_ids':['CL1'],'viewpoint_ids':['CV-old'],'authorization':{'bounded':True},
            'action':{'kind':'source_claim_revision','action_id':'source-action'},'disposition':None}}}
    resolution={'schema_version':m.REPROPOSAL_VERSION,'pending_existing_cvp_record_keys':['canonical_viewpoints/CV-old']}
    materials={'packet':{'claims':[{'claim_id':'CL1'}],'existing_links':[{'viewpoint_claim_link_id':'V-old'}]},
        'sources':[],'read_pins':[],'claim_management_resolution':deepcopy(resolution)}
    previous={'materials':materials} if case in {'saved','legacy_cache'} else None
    if case=='legacy_cache':materials['claim_management_resolution']={'old':True}
    calls=[];commits=[]
    store=SimpleNamespace(get_cvp_group=lambda rid:saved,bind_cvp_execution_root=lambda *a,**kw:None,
        get_cvp_action=lambda *a:previous,prepare_cvp_action=lambda *a,**kw:kw,
        plan_package=lambda *a,**kw:SimpleNamespace(operations=[SimpleNamespace(
            operation='update' if case=='update_old' else 'create',
            collection='claims' if case=='source_write' else 'viewpoint_claim_links')]))
    monkeypatch.setattr(flow,'prepare_group',lambda *a:deepcopy(materials))
    monkeypatch.setattr(m,'coverage_context',lambda *a:deepcopy(resolution))
    monkeypatch.setattr(actions,'initialize_subscription_runtime',lambda *a:None)
    monkeypatch.setattr(flow,'verified_group_backup',lambda *a:{})
    monkeypatch.setattr(actions,'commit_package',lambda *a,**kw:commits.append(kw) or {'status':'applied'})
    def reviewed(root,material,store,*,require_resolved):
        calls.append(root)
        assert require_resolved and material['packet']['existing_links']==materials['packet']['existing_links']
        assert material['claim_management_resolution']==resolution
        return {'viewpoint_claim_links':[{'claim_id':'foreign' if case=='foreign_claim' else 'CL1','viewpoint_id':'CV-new'}]}, {}, {'claims':[]}
    monkeypatch.setattr(flow,'reviewed_cvp_package',reviewed)
    def run():return actions.execute_coverage_action(store,'run',{'authorized_by':'owner'},
        {'claim_ids':['CL1']},root=tmp_path,ports=None)
    if case in {'legacy_cache','update_old','source_write','foreign_claim'}:
        with pytest.raises(ValueError):run()
        assert not commits
        if case=='legacy_cache':assert not calls
    else:
        assert run()['status']=='applied'
        assert len(calls)==1
        assert commits[0]['pending_cvp_management_record_keys']==['canonical_viewpoints/CV-old']
        assert commits[0]['viewpoint_ids']==['CV-new','CV-old']


@pytest.mark.skipif(os.getenv('WKP357_SAVED_REPROPOSAL_TEST')!='1',reason='actual saved read-only SQL input opt-in')
def test_saved_claim_first_receipt_and_current_group_prepare_no_model_or_write():
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    load_dotenv('.env');store=PostgresKnowledgeStore()
    rid='CVPG-0490be727b9eef01497d20f558f7b437fc7beacd434324c8d71c1a82ce024c83'
    saved=store.get_cvp_group(rid)
    definition=dict(store.get_cvp_campaign(saved['campaign_sha256']));definition['campaign_sha256']=saved['campaign_sha256']
    group=next(g for g in definition['groups'] if g['group_key']==saved['group_key'])
    result=m.coverage_context(store,saved,group)
    assert len(result['pending_existing_cvp_record_keys'])==24
    materials=flow.prepare_group(store,definition,group)
    assert len(materials['packet']['claims'])==20
    assert len(materials['packet']['existing_links'])==25
    assert materials['metrics']['historical_snapshot_reads']==0
    assert store.get_cvp_group(rid)==saved
