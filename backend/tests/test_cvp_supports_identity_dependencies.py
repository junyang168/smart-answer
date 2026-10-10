from contextlib import nullcontext
from copy import deepcopy
import pytest
from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.pipeline.cvp_sql_actions import supports_identity_dependencies
from backend.pipeline.cvp_sql_flow import exact_saved_upheld_items


def fixture():
    old={'decision_id':'D','claim_id':'C','viewpoint_id':'V','link_type':'equivalent_component'}
    decision={'identity_candidate_id':'I','review_status':'system_approved','resolved_viewpoint_id':'V',
              'claim_link_decisions':[{'claim_id':'C','link_type':'equivalent_component'}]}
    candidate={'identity_candidate_id':'I','candidate_claim_ids':['C'],'candidate_viewpoint_ids':[],
               'proposed_action':'create_new','revision':1,'review_status':'candidate'}
    row={'revision':2,'content_sha256':record_content_sha(candidate),'payload':candidate,'retired':False}
    records={('viewpoint_identity_decisions','D'):{'payload':decision}}
    class Store:
        def connect(self):return nullcontext(self)
        def execute(self,sql,keys):
            assert tuple(keys[:2])==('viewpoint_identity_candidates','I')
            row=self.extra.get(('viewpoint_identity_candidates','I'))
            self.result=None if row is None else ((row['revision'],row['content_sha256'],row['payload'],row['retired'])
                if 'retired_at' in sql else [({**row['payload'],'revision':row['revision']},row['content_sha256'],row['content_sha256'],'applied','retire' if row['retired'] else 'create')])
            if self.bad_history and 'object_versions' in sql:self.result=[]
            return self
        def fetchone(self):return self.result
        def fetchall(self):return self.result
    store=Store();store.extra={('viewpoint_identity_candidates','I'):row};store.bad_history=False
    return store,records,[{'before_link':old}]


def test_exact_forward_candidate_storage_version_separate_from_payload_revision():
    s,r,i=fixture();before=deepcopy(r)
    extra=supports_identity_dependencies(s,r,i)
    assert extra==s.extra and r==before
    assert extra['viewpoint_identity_candidates','I']['revision']==2


def test_retired_provenance_is_pinned_not_revived():
    s,r,i=fixture();s.extra['viewpoint_identity_candidates','I']['retired']=True
    extra=supports_identity_dependencies(s,r,i)
    assert extra['viewpoint_identity_candidates','I']['retired'] is True
    assert r=={('viewpoint_identity_decisions','D'):r['viewpoint_identity_decisions','D']}


@pytest.mark.parametrize('failure',['missing','history','hash','id','hold','claim','target','role','decision_hold','decision_missing','drift'])
def test_missing_foreign_held_or_changed_forward_proof_rejected(failure):
    s,r,i=fixture();row=s.extra['viewpoint_identity_candidates','I'];p=row['payload'];d=r['viewpoint_identity_decisions','D']['payload']
    if failure=='missing':s.extra={}
    elif failure=='history':s.bad_history=True
    elif failure=='hash':row['content_sha256']='bad'
    elif failure=='id':p['identity_candidate_id']='foreign'
    elif failure=='hold':p['review_status']='human_hold'
    elif failure=='claim':p['candidate_claim_ids']=['foreign']
    elif failure=='target':d['resolved_viewpoint_id']='foreign'
    elif failure=='role':d['claim_link_decisions'][0]['link_type']='supports'
    elif failure=='decision_hold':d['review_status']='withheld_unreviewed'
    elif failure=='decision_missing':r.clear()
    elif failure=='drift':r['viewpoint_identity_candidates','I']={**row,'content_sha256':'changed'}
    if failure not in {'hash','missing'}:row['content_sha256']=record_content_sha(p)
    with pytest.raises(ValueError):supports_identity_dependencies(s,r,i)


def test_saved_upheld_resumes_exact_original_action_not_rejected_proof():
    p={'items':[{'item_id':'x','audit_finding':{'reason':'original'},'claim':{'revision':1}}]}
    r={'actions':[{'item_id':'x','verdict':'audit_objection_upheld'}]}
    assert exact_saved_upheld_items(p,deepcopy(p),r,{'x'})=={'x'}
    assert exact_saved_upheld_items(p,p,{'actions':[{'item_id':'x','verdict':'audit_objection_rejected'}]},{'x'})==set()


@pytest.mark.parametrize('delta',['claim','finding','missing','extra_upheld'])
def test_saved_upheld_changed_scope_cannot_resume(delta):
    p={'items':[{'item_id':'x','audit_finding':{'reason':'original'},'claim':{'revision':1}}]};q=deepcopy(p)
    r={'actions':[{'item_id':'x','verdict':'audit_objection_upheld'}]}
    if delta=='claim':q['items'][0]['claim']['revision']=2
    elif delta=='finding':q['items'][0]['audit_finding']['reason']='changed'
    elif delta=='missing':q['items']=[]
    else:r['actions'].append({'item_id':'foreign','verdict':'audit_objection_upheld'})
    with pytest.raises(ValueError):exact_saved_upheld_items(p,q,r,{'x'})
