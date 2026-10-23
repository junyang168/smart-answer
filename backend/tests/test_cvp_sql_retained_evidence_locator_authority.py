from copy import deepcopy
import os
import pytest
from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.pipeline import cvp_sql_retained_evidence_locator_authority as a


def claims():
    x={'revision':3,'statement':'same assertion','review_status':'ai_consensus_reviewed',
        'attribution':'professor','occurrences':[{'source_id':'own'}],'evidence_step_ids':['E1','E2','E3']}
    return x,{**deepcopy(x),'revision':4,'evidence_step_ids':['E2']}


def test_exact_subset_is_not_new_evidence_or_claim_approval():
    before,after=claims();old=deepcopy((before,after));assert a.subset(before,after)==['E2'];assert(before,after)==old


@pytest.mark.parametrize('fault',['empty','new','duplicate','same','order','statement','attribution','occurrences','hold'])
def test_subset_counterexamples(fault):
    before,after=claims()
    if fault=='empty':after['evidence_step_ids']=[]
    if fault=='new':after['evidence_step_ids']=['FOREIGN']
    if fault=='duplicate':after['evidence_step_ids']=['E2','E2']
    if fault=='same':after['evidence_step_ids']=before['evidence_step_ids']
    if fault=='order':after['evidence_step_ids']=['E3','E2']
    if fault=='statement':after['statement']='changed meaning'
    if fault=='attribution':after['attribution']='editor'
    if fault=='occurrences':after['occurrences']=[]
    if fault=='hold':after['review_status']='human_hold'
    with pytest.raises(ValueError):a.subset(before,after)


@pytest.mark.parametrize('fault',['status','duplicate','operation','before','after','before_revision','after_revision','before_sha','after_sha'])
def test_actual_sql_operation_must_be_exact(fault):
    before,after=claims();o={'collection':'claims','object_id':'C','operation':'update','before':before,'after':after,
        'before_revision':3,'after_revision':4,'before_sha256':record_content_sha(before),'after_sha256':record_content_sha(after)}
    h={'status':'applied','operations':[deepcopy(o)]};a.exact_operation(h,'C',before,after)
    if fault=='status':h['status']='planned'
    elif fault=='duplicate':h['operations'].append(deepcopy(o))
    else:
        key={'before_sha':'before_sha256','after_sha':'after_sha256'}.get(fault,fault)
        h['operations'][0][key]='foreign'
    with pytest.raises(ValueError):a.exact_operation(h,'C',before,after)


@pytest.mark.skipif(os.environ.get('WKP357_REAL_SAVED_READONLY')!='1',reason='explicit real SQL read-only opt-in')
@pytest.mark.parametrize('fault',[None,'meaning','new_evidence','uncommitted','review_missing','foreign_alias','source','fragment','late_version'])
def test_real_saved_fixed_contract_and_counterexamples(fault):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    load_dotenv('.env');real=PostgresKnowledgeStore();cid='DK-72483dc200ad-CL002';row=real.get_record_state('claims',cid)
    class Store:
        def connect(self):return real.connect()
        def get_record_state(self,col,oid):
            r=real.get_record_state(col,oid)
            if r and fault=='source' and col=='source_documents' and not r['retired']:r['payload']['source_path']='foreign'
            if r and fault=='fragment' and col=='source_fragments':r['payload']['verbatim_excerpt']='foreign'
            return r
        def get_change_history(self,key):
            h=real.get_change_history(key)
            if h['source_kind']=='library_audit_evidence_remediation':
                if fault=='uncommitted':h['status']='planned'
                for o in h['operations']:
                    if o['object_id']==cid:
                        if fault=='meaning':o['after']['statement']='changed'
                        if fault=='new_evidence':o['after']['evidence_step_ids']=['new']
            if fault=='review_missing' and h['source_kind']=='review_decision':h['metadata']['review_event_id']='missing'
            if fault=='foreign_alias' and h['source_kind']=='wkp368_source_duplicate_cleanup':
                for o in h['operations']:
                    if o['object_id']==cid:o['after']['occurrences'][-1]['source_id']='foreign'
            return h
    if fault=='late_version':row['content_sha256']='foreign'
    if fault is None:
        p=a.authority(Store(),cid,row);assert p['model_calls']==p['master_data_mutations']==0
        assert len(p['exact_applied_change_set_ids'])==4 and len(p['bindings'])==4
    else:
        with pytest.raises(ValueError):a.authority(Store(),cid,row)
