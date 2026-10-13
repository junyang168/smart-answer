from copy import deepcopy
from types import SimpleNamespace
import os
import pytest
from backend.pipeline.cvp_sql_absent_anchor_authority import withdrawn_postimage
from backend.pipeline.cvp_sql_flow import current_claim_authority

def fixture():
    before={'claim_id':'C','revision':2,'statement':'teaching','review_status':'ai_consensus_reviewed',
        'evidence_step_ids':['E1','E2'],'eligible_evidence_step_ids':['E1','E2'],'withheld_evidence_step_ids':[],
        'occurrences':[{'source_id':'source','transcript_id':'source','local_source_evidence_ids':['1','2'],
            'canonical_evidence_step_ids':['E1','E2'],'anchors':[
                {'evidence_id':'1','proposed_highlight':{'text':'real quote'}},
                {'evidence_id':'2','proposed_highlight':{'text':'absent machine quote'}}]}]}
    after=deepcopy(before);after.update(revision=3,eligible_evidence_step_ids=['E1'],withheld_evidence_step_ids=['E2'])
    after['occurrences'][0]['anchors'].pop()
    return before,after,SimpleNamespace(rows=[SimpleNamespace(text='real quote')])

def test_exact_withdrawal_does_not_alter_claim_or_input():
    b,a,index=fixture();old=deepcopy((b,a));removed,ids=withdrawn_postimage(b,a,index)
    assert len(removed)==1 and ids==['E2'] and (b,a)==old

@pytest.mark.parametrize('fault',['statement','source','speaker','order','new_anchor','revive_eligibility',
    'new_evidence','drop_other_field','present_removed_text','empty_text','drop_all_anchors','new_withheld'])
def test_withdrawal_is_not_semantic_or_permission_waiver(fault):
    b,a,index=fixture()
    if fault=='statement':a['statement']='other'
    if fault=='source':a['occurrences'][0]['source_id']='foreign'
    if fault=='speaker':a['attribution']='editor'
    if fault=='order':a['evidence_step_ids'].reverse()
    if fault=='new_anchor':a['occurrences'][0]['anchors'].append({'proposed_highlight':{'text':'new'}})
    if fault=='revive_eligibility':a['eligible_evidence_step_ids']=['E1','E2']
    if fault=='new_evidence':a['evidence_step_ids'].append('E3')
    if fault=='drop_other_field':a.pop('review_status')
    if fault=='present_removed_text':index.rows.append(SimpleNamespace(text='absent machine quote'))
    if fault=='empty_text':b['occurrences'][0]['anchors'][1]['proposed_highlight']['text']=''
    if fault=='drop_all_anchors':a['occurrences'][0]['anchors']=[]
    if fault=='new_withheld':a['withheld_evidence_step_ids'].append('foreign')
    with pytest.raises(ValueError):withdrawn_postimage(b,a,index)

ACTUAL_IDS=['CL-0028','CL-0027','DK-1004660290a2-P03-CL013','DK-1004660290a2-P06-CL001','DK-1004660290a2-P06-CL004']

@pytest.mark.skipif(os.environ.get('WKP357_REAL_SAVED_READONLY')!='1',reason='explicit actual SQL read-only')
@pytest.mark.parametrize('cid',ACTUAL_IDS)
@pytest.mark.parametrize('fault',[None,'meaning','uncommitted','source','current_sha','old_review','held','foreign_dependency','operation_set'])
def test_actual_fixed_types_and_negative_gates(cid,fault):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    load_dotenv('.env');real=PostgresKnowledgeStore();original=real.get_record_state('claims',cid)
    class Store:
        def connect(self):return real.connect()
        def get_record_state(self,*args):return real.get_record_state(*args)
        def get_change_history(self,kcs):
            h=real.get_change_history(kcs)
            if h['source_kind'] in {'wkp368_absent_machine_anchor_cleanup','wkp353_relation_id_incident_recovery'}:
                if fault=='uncommitted':h['status']='prepared'
                if fault=='meaning':
                    for op in h['operations']:
                        if op['collection']=='claims' and op['object_id']==cid:op['after']['statement']='different'
                if fault=='held':
                    for op in h['operations']:
                        if op['collection']=='claims' and op['object_id']==cid:op['after']['review_status']='human_hold'
                if fault=='foreign_dependency':
                    for op in h['operations']:
                        if op['collection']=='claims' and op['object_id']==cid:op['after']['evidence_step_ids']=['foreign']
                if fault=='operation_set':h['operations']=h['operations'][:-1]
            if fault=='source' and h['source_kind']=='wkp368_source_contract_cleanup':
                h['metadata']['source_plan_file_sha256']='f'*64
            if fault=='old_review' and h['source_kind']=='review_decision':h['metadata']['review_event_id']='foreign'
            if fault=='old_review' and h['source_kind']=='wkp353_relation_id_incident_recovery':
                h['metadata']['authority']['authoritative_packages']=[]
            return h
    row=deepcopy(original)
    if fault=='current_sha':row['content_sha256']='f'*64
    if fault is None:
        proof=current_claim_authority(Store(),cid,row)
        assert proof['model_calls']==proof['master_data_mutations']==0
        assert proof['current_dependency_pins']
    else:
        with pytest.raises(ValueError):current_claim_authority(Store(),cid,row)
    assert real.get_record_state('claims',cid)==original

@pytest.mark.parametrize('completed,legacy,correct_pin,expected',[
    (False,False,False,'current_authority_verified'),
    (True,False,True,'current_audited_generation'),
    (True,True,False,'historically_admitted_unchanged_generation'),
    (True,False,False,'current_authority_verified'),
    (False,True,True,'current_authority_verified')])
def test_inventory_uses_completion_only_for_exact_successful_current_generation(monkeypatch,completed,legacy,correct_pin,expected):
    from backend.pipeline import cvp_sql_claim_inventory as m
    from backend.api.canonical_repository.postgres_store import record_content_sha
    payload={'claim_id':'C','revision':1};sha=record_content_sha(payload)
    class Result:
        def __init__(self,rows):self.rows=rows
        def fetchall(self):return self.rows
    class Store:
        def get_cvp_campaign(self,sha):return {'groups':[{'sequence':1,'group_key':'G','claim_ids':['C']}]}
        def cvp_campaign_progress(self,sha):return [{'group_key':'G','run_id':'R','checkpoint':'audited' if completed else 'prepared','status':'succeeded' if completed else 'running'}]
        def connect(self):return self
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,query,args):
            if "metadata->'completion'" in query:
                return Result([('R',{'read_pins':[{'collection':'claims','object_id':'C','pin':[1,sha,False]}]})] if correct_pin else [])
            if "import_certificate" in query:return Result([('R',)] if legacy else [])
            if 'retired_at' in query:return Result([('C',1,sha,payload,False)])
            return Result([('C',1,'K',sha,'knowledge_package','applied')])
    calls=[]
    monkeypatch.setattr(m,'current_claim_authority',lambda *a:(calls.append(a) or {}))
    result=m.inventory(Store(),'campaign')
    assert result['results'][0]['status']==expected
    assert bool(calls)==(expected=='current_authority_verified')
    assert result['model_calls']==result['registry_writes']==0

def test_inventory_never_converts_qualification_failure_to_pass(monkeypatch):
    from backend.pipeline import cvp_sql_claim_inventory as m
    class Store:
        def get_cvp_campaign(self,sha):return {'groups':[{'claim_ids':['C','C']}]}
    with pytest.raises(ValueError,match='duplicate'):m.inventory(Store(),'campaign')

@pytest.mark.parametrize('fault',[None,'eligible','context_only','foreign','bound','missing'])
def test_unavailable_evidence_is_disclosed_not_restored_or_used(fault):
    from backend.api.canonical_repository.knowledge_models import ClaimRecord,EvidenceStepRecord,SourceFragmentRecord
    from backend.api.canonical_repository.viewpoint_resolution import compile_review_claim
    claim=ClaimRecord(claim_id='C',statement='teaching',claim_type='theology',evidence_step_ids=['E','W'])
    good=EvidenceStepRecord(evidence_step_id='E',statement='own support',step_type='reasoning',produced_claim_ids=['C'],
        source_fragment_ids=['F'],support_eligibility='eligible_candidate')
    data={'evidence_step_id':'W','statement':'unavailable quotation','step_type':'reasoning','produced_claim_ids':['C'],
        'support_eligibility':'withheld_missing_anchor','anchor_quality':'missing'}
    if fault=='eligible':data['support_eligibility']='eligible_candidate'
    if fault=='context_only':data['support_eligibility']='contextual_only'
    if fault=='foreign':data['produced_claim_ids']=['other']
    if fault=='bound':data['source_fragment_ids']=['F']
    steps={'E':good}
    if fault!='missing':steps['W']=EvidenceStepRecord(**data)
    fragment=SourceFragmentRecord(fragment_id='F',source_id='S',verbatim_excerpt='own support',
        source_sha256='sha',anchor_state='source_version_bound')
    result,errors=compile_review_claim(claim=claim,evidence_index=steps,fragment_index={'F':fragment},
        citation_index={},coverage_source_sha256={'S':'sha'},attestation_sha='proof',
        unavailable_evidence_ids=frozenset({'W'}))
    if fault is None:
        assert not errors and [e.evidence_step_id for e in result.evidence]==['E']
        _,old_errors=compile_review_claim(claim=claim,evidence_index=steps,fragment_index={'F':fragment},
            citation_index={},coverage_source_sha256={'S':'sha'},attestation_sha='proof')
        assert old_errors # unchanged default callers still fail on unbound rows
    else:assert errors
