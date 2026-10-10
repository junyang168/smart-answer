from copy import deepcopy
import pytest
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore,record_content_sha,ChangeSetConflict
from backend.api.canonical_repository.viewpoint_foundation import retained_historical_origin


def fixture():
    view={'viewpoint_id':'V','created_from_candidate_id':'I','review_status':'system_approved'}
    candidate={'identity_candidate_id':'I','revision':1,'review_status':'candidate',
               'proposed_action':'create_new','candidate_claim_ids':['retired-Claim'],'blocker_codes':[]}
    row={'payload':candidate,'revision':2,'content_sha256':record_content_sha(candidate),
         'retired':True,'retired_at':'committed retirement'}
    history=[({**candidate,'revision':2},row['content_sha256'],row['content_sha256'],'applied','retire')]
    class Cursor:
        def execute(self,sql,args):assert args==('I',2)
        def fetchall(self):return history
    return view,row,history,Cursor()


def test_storage_retirement_is_historical_origin_not_active_membership():
    view,row,history,cursor=fixture();before=deepcopy(row)
    row['historical_origin_authority']=PostgresKnowledgeStore._origin_candidate_history_authority(cursor,'I',row)
    assert retained_historical_origin('V',view,{('canonical_viewpoints','V'):{'payload':view},
                                             ('viewpoint_identity_candidates','I'):row})
    assert row['retired'] and row['payload']==before['payload']
    assert history[0][0]['revision']==2 and row['payload']['revision']==1


@pytest.mark.parametrize('error',['missing','ambiguous','status','operation','history_sha','after_sha','body','storage_revision','hash','foreign_id','active'])
def test_history_proof_fail_closed(error):
    _,row,h,cursor=fixture()
    if error=='missing':h.clear()
    elif error=='ambiguous':h.append(h[0])
    elif error=='status':h[0]=(*h[0][:3],'prepared','retire')
    elif error=='operation':h[0]=(*h[0][:4],'create')
    elif error=='history_sha':h[0]=(h[0][0],'wrong',*h[0][2:])
    elif error=='after_sha':h[0]=(*h[0][:2],'wrong',*h[0][3:])
    elif error=='body':h[0][0]['proposed_action']='match_existing'
    elif error=='storage_revision':h[0][0]['revision']=1
    elif error=='hash':row['content_sha256']='wrong'
    elif error=='foreign_id':row['payload']['identity_candidate_id']='foreign'
    elif error=='active':row['retired']=False
    with pytest.raises(ChangeSetConflict):PostgresKnowledgeStore._origin_candidate_history_authority(cursor,'I',row)


@pytest.mark.parametrize('error',['new_head','changed_origin','head_retired','head_hold','foreign_head','no_proof','stale_proof','candidate_active','candidate_hold','blocker','foreign_candidate','wrong_action'])
def test_origin_cannot_authorize_new_identity_or_revive(error):
    view,row,_,cursor=fixture()
    row['historical_origin_authority']=PostgresKnowledgeStore._origin_candidate_history_authority(cursor,'I',row)
    old={'payload':deepcopy(view)};existing={('canonical_viewpoints','V'):old,('viewpoint_identity_candidates','I'):row}
    if error=='new_head':existing.pop(('canonical_viewpoints','V'))
    elif error=='changed_origin':old['payload']['created_from_candidate_id']='foreign'
    elif error=='head_retired':old['retired']=True
    elif error=='head_hold':old['payload']['review_status']='human_hold'
    elif error=='foreign_head':old['payload']['viewpoint_id']='foreign'
    elif error=='no_proof':row.pop('historical_origin_authority')
    elif error=='stale_proof':row['historical_origin_authority'][0]=1
    elif error=='candidate_active':row.update(retired=False,retired_at=None)
    elif error=='candidate_hold':row['payload']['review_status']='human_hold'
    elif error=='blocker':row['payload']['blocker_codes']=['unresolved']
    elif error=='foreign_candidate':row['payload']['identity_candidate_id']='foreign'
    elif error=='wrong_action':row['payload']['proposed_action']='needs_direction'
    assert not retained_historical_origin('V',view,existing)


def route_scope_fixture():
    old={'viewpoint_id':'V','viewpoint_revision_id':'R1','review_status':'system_approved',
         'core_proposition':'same core','proposition_signature':{'subject':'same'},'scope':{'scripture_scope':['old']}}
    new={**deepcopy(old),'viewpoint_revision_id':'R2','supersedes_revision_id':'R1','scope':{'scripture_scope':[]}}
    head={'viewpoint_id':'V','current_revision_id':'R1','review_status':'system_approved'}
    route={'argument_route_id':'A','current_revision_id':'AR1','conclusion_viewpoint_id':'V','review_status':'system_approved'}
    revision={'argument_route_id':'A','argument_route_revision_id':'AR1','review_status':'system_approved',
        'validated_against_conclusion_viewpoint_revision_id':'R1','route_signature':{'conclusion_viewpoint_id':'V'}}
    existing={('canonical_viewpoints','V'):{'payload':head},('viewpoint_revisions','R1'):{'payload':old},
        ('argument_routes','A'):{'payload':route},('argument_route_revisions','AR1'):{'payload':revision}}
    normalized={'canonical_viewpoints':{'V':{**head,'current_revision_id':'R2'}},'viewpoint_revisions':{'R2':new}}
    return normalized,existing


@pytest.mark.parametrize('error',[None,'core','signature','audience','attribution','foreign','no_prior','route_write','hold','retired','route_retired'])
def test_scope_historical_route_allowset_is_not_new_route_authority(error):
    from backend.api.canonical_repository.viewpoint_foundation import scope_retained_route_revision_ids
    n,e=route_scope_fixture();new=n['viewpoint_revisions']['R2']
    if error=='core':new['core_proposition']='changed'
    elif error=='signature':new['proposition_signature']={'subject':'changed'}
    elif error=='audience':new['scope']['audience_scope']=['changed']
    elif error=='attribution':new['attribution_subject']='editor'
    elif error=='foreign':new['viewpoint_id']='foreign'
    elif error=='no_prior':new['supersedes_revision_id']='foreign'
    elif error=='route_write':n['argument_routes']={'A':e['argument_routes','A']['payload']}
    elif error=='hold':e['argument_route_revisions','AR1']['payload']['review_status']='human_hold'
    elif error=='retired':e['argument_route_revisions','AR1']['retired']=True
    elif error=='route_retired':e['argument_routes','A']['retired']=True
    assert scope_retained_route_revision_ids(n,e)==({'AR1'} if error is None else set())


def test_runtime_use_still_refuses_unrevalidated_route_and_checks_retired_relation_endpoints():
    from backend.api.canonical_repository.viewpoint_runtime_projection import validate_runtime_authoring_graph
    n,e=route_scope_fixture()
    graph={}
    for (c,i),row in e.items():graph.setdefault(c,{})[i]=row['payload']
    for c,rows in n.items():graph.setdefault(c,{}).update(rows)
    assert 'AR1: conclusion viewpoint revision is stale' in validate_runtime_authoring_graph(graph)
    assert validate_runtime_authoring_graph(graph,retained_route_revision_ids={'AR1'})==[]
    graph['viewpoint_relations']={'REL':{'source_viewpoint_id':'V','target_viewpoint_id':'V',
        'validated_source_viewpoint_revision_id':'R1','validated_target_viewpoint_revision_id':'R1',
        'effective_state':'retired'}}
    assert not any('REL:' in v for v in validate_runtime_authoring_graph(graph))
    graph['viewpoint_relations']['REL']['validated_target_viewpoint_revision_id']='missing'
    assert 'REL: invalid target viewpoint revision' in validate_runtime_authoring_graph(graph)
