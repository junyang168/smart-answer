from contextlib import contextmanager
from copy import deepcopy

import pytest

from backend.api.canonical_repository.postgres_store import record_content_sha,sha256_json
from backend.pipeline.cvp_sql_pair_review_authority import KIND,pair_review


@pytest.fixture
def pair_case():
    before={'claim_id':'C','revision':2,'statement':'Unchanged teaching.',
        'review_status':'ai_consensus_reviewed','evidence_step_ids':['E1']}
    after=dict(before,revision=3,evidence_step_ids=['E1','E2'])
    records={}
    for eid in ['E1','E2']:
        records['evidence_steps',eid]={'revision':1,'evidence_step_id':eid,
            'support_eligibility':'eligible','produced_claim_ids':['C'],'source_fragment_ids':['F'+eid]}
        records['source_fragments','F'+eid]={'revision':1,'source_id':'SRC','verbatim_excerpt':'Own source.',
            'source_sha256':'s'*64,'anchor_state':'source_version_bound'}
    action={'claim_id':'C','pair_id':'P','evidence_step_id':'E2','claim_revision':2,
        'claim_content_sha256':record_content_sha(before),'claim_review_status':'ai_consensus_reviewed',
        'evidence_revision':1,'evidence_content_sha256':record_content_sha(records['evidence_steps','E2']),
        'mismatch_type':'evidence_only','reviewed_decision':'include','effective_decision':'include',
        'decision_basis':'independent_model_consensus_include'}
    auth={'schema_version':'wang_claim_evidence_pair_repair_authorization_v1',
        'policy':'downstream_claim_projection_with_independent_pair_review_v1',
        'actions':[action],'audit_artifact_sha256':'a'*64}
    def seal(history):
        a=history['metadata']['claim_evidence_reciprocity_repair']['authorization']
        a['artifact_sha256']=sha256_json({k:v for k,v in a.items() if k!='artifact_sha256'})
        history['metadata']['claim_evidence_reciprocity_repair']['pair_adjudication_authorization_sha256']=a['artifact_sha256']
        history['source_sha256']=sha256_json({'schema_version':'wang_claim_evidence_pair_repair_plan_v1',
            'audit_artifact_sha256':a['audit_artifact_sha256'],'authorization_artifact_sha256':a['artifact_sha256'],
            'actions_sha256':sha256_json(a['actions'])})
    history={'source_kind':KIND,'status':'applied','fingerprint_sha256':'b'*64,
        'metadata':{'claim_evidence_reciprocity_repair':{'authorization':auth}},
        'operations':[{'operation':'update','before_revision':2,'after_revision':3,
            'before_sha256':record_content_sha(before),'after_sha256':record_content_sha(after),'before':before,'after':after}]}
    seal(history)
    class Result:
        def __init__(self,value):self.value=value
        def fetchone(self):return self.value
        def fetchall(self):return self.value
    class Store:
        def __init__(self):
            self.records=deepcopy(records);self.old=deepcopy(records);self.reviewed=deepcopy(records);self.calls=[]
            self.events=[('REV','ai','ai_consensus_reviewed',{'change_set_id':'KCS-review'})]
            self.base=('KCS-review',record_content_sha(before),before,'base-time')
            self.committed=('applied','REV')
        @contextmanager
        def connect(self):yield self
        def execute(self,sql,params):
            self.calls.append(params)
            if 'recorded_at<=' in sql:
                rows=self.reviewed if params[2]=='base-time' else self.old
                value=rows.get(tuple(params[:2]));return Result((value['revision'],record_content_sha(value),value) if value else None)
            if 'review_events' in sql:return Result(self.events)
            if 'change_sets' in sql:return Result(self.committed)
            if 'SELECT recorded_at' in sql:return Result(('pair-time',))
            return Result(self.base)
        def get_record_state(self,collection,oid):
            value=self.records.get((collection,oid))
            return {'payload':value,'revision':value['revision'],'content_sha256':record_content_sha(value),
                'retired':value.get('retired',False)} if value else None
    return Store(),('KCS-'+('b'*20),record_content_sha(after),after),history,seal


def run(case,operations=()):
    s,previous,history,_=case
    return pair_review(s,'C',previous,history,{'source_id':'SRC','source_body_sha256':'s'*64},None,operations)


def test_exact_committed_pair_additions_carry_unique_review_without_writes(pair_case):
    result=run(pair_case)
    assert result['review_event_id']=='REV' and result['pre_pair_reviewed_revision']==2
    assert len(result['current_dependency_pins'])==4
    assert not any('FOR UPDATE' in str(c) for c in pair_case[0].calls)


@pytest.mark.parametrize('mutation',[
    'uncommitted','foreign_kind','seal','source_sha','fingerprint','meaning','status','deleted_evidence',
    'duplicate_evidence','duplicate_action','missing_action','unresolved','exclude','foreign_claim',
    'claim_pin','evidence_pin','missing_review','duplicate_review','foreign_review','uncommitted_base',
    'base_sha','current_evidence','current_fragment','missing_fragment','held_evidence','retired',
    'not_reciprocal','foreign_source','unproved_locator','no_fragments','changed_source','held_fragment',
    'original_changed_before_pair',
])
def test_pair_bridge_rejects_unproved_or_semantic_changes(pair_case,mutation):
    s,previous,h,seal=pair_case;op=h['operations'][0];a=h['metadata']['claim_evidence_reciprocity_repair']['authorization']['actions'][0]
    if mutation=='uncommitted':h['status']='pending'
    elif mutation=='foreign_kind':h['source_kind']='knowledge_package'
    elif mutation=='seal':h['metadata']['claim_evidence_reciprocity_repair']['authorization']['policy']='other'
    elif mutation=='source_sha':h['source_sha256']='x'*64
    elif mutation=='fingerprint':h['fingerprint_sha256']='x'*64
    elif mutation in {'meaning','status','deleted_evidence','duplicate_evidence'}:
        if mutation=='meaning':op['after']['statement']='Changed teaching.'
        if mutation=='status':op['after']['review_status']='human_hold'
        if mutation=='deleted_evidence':op['after']['evidence_step_ids']=['E2']
        if mutation=='duplicate_evidence':op['after']['evidence_step_ids']=['E1','E2','E2']
        op['after_sha256']=record_content_sha(op['after']);pair_case=(s,(previous[0],op['after_sha256'],op['after']),h,seal)
    elif mutation=='duplicate_action':h['metadata']['claim_evidence_reciprocity_repair']['authorization']['actions']*=2;seal(h)
    elif mutation=='missing_action':h['metadata']['claim_evidence_reciprocity_repair']['authorization']['actions']=[];seal(h)
    elif mutation in {'unresolved','exclude','foreign_claim','claim_pin','evidence_pin'}:
        field,value={'unresolved':('reviewed_decision','needs_human'),'exclude':('effective_decision','exclude'),
            'foreign_claim':('claim_id','OTHER'),'claim_pin':('claim_revision',1),'evidence_pin':('evidence_content_sha256','x'*64)}[mutation]
        a[field]=value;seal(h)
    elif mutation=='missing_review':s.events=[]
    elif mutation=='duplicate_review':s.events*=2
    elif mutation=='foreign_review':s.events[0][3]['change_set_id']='OTHER'
    elif mutation=='uncommitted_base':s.committed=('pending','REV')
    elif mutation=='base_sha':s.base=(s.base[0],'x'*64,*s.base[2:])
    elif mutation=='current_evidence':s.records['evidence_steps','E2']['statement']='changed'
    elif mutation=='current_fragment':s.records['source_fragments','FE2']['verbatim_excerpt']='changed'
    elif mutation=='missing_fragment':del s.old['source_fragments','FE2']
    elif mutation=='held_evidence':
        for r in (s.old,s.records):r['evidence_steps','E1']['support_eligibility']='context_only'
    elif mutation=='retired':s.records['evidence_steps','E2']['retired']=True
    elif mutation=='not_reciprocal':
        for r in (s.old,s.records):r['evidence_steps','E1']['produced_claim_ids']=[]
    elif mutation=='foreign_source':
        for r in (s.old,s.records):r['source_fragments','FE1']['source_id']='OTHER'
    elif mutation=='changed_source':
        for r in (s.old,s.records):r['source_fragments','FE1']['source_sha256']='x'*64
    elif mutation=='held_fragment':
        for r in (s.old,s.records):r['source_fragments','FE1']['review_status']='human_hold'
    elif mutation=='original_changed_before_pair':s.reviewed['evidence_steps','E1']['statement']='other meaning'
    elif mutation=='no_fragments':
        for r in (s.old,s.records):r['evidence_steps','E1']['source_fragment_ids']=[]
    operations=[{'collection':'evidence_steps','object_id':'E1','operation':'update'}] if mutation=='unproved_locator' else []
    with pytest.raises(ValueError):run(pair_case,operations)
