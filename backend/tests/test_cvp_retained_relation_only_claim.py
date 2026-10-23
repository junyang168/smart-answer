from copy import deepcopy
import pytest
from backend.pipeline import cvp_retained_relation_only_claim as h
from backend.api.canonical_repository.postgres_store import sha256_json


def pair():
    review={'claim_reviews':[{'claim_id':'C','decision':'changes_suggested',
        'issues':[{'issue_type':'relation_error'}],'human_review_reason':'','spot_check_selected':False}]}
    answer={'claim_id':'C','decision':'accept','patch':{'route_type':'unchanged',
        'excluded_claim_relation_ids':['CR'],'statement':'','claim_kind':'','anchor_additions':[],
        'excluded_anchor_indexes':[],'scripture_refs':[],'structural_notes':[]}}
    outcome={'results':[{'claim_id':'C','status':'auto_applied','openai':answer,'claude_reconsideration':None}]}
    return review,outcome


def test_exact_closed_relation_not_claim_approval():
    r,o=pair();before=deepcopy((r,o));assert h.relation_only_result(r,o,'C')==['CR'];assert (r,o)==before


@pytest.mark.parametrize('fault',['content','hold','spot','unresolved','reject','correction','statement',
    'anchor','scope','route','no_exclusion','duplicate','foreign','ambiguous'])
def test_no_content_or_hold_expansion(fault):
    r,o=pair();rr=r['claim_reviews'][0];result=o['results'][0];patch=result['openai']['patch']
    if fault=='content':rr['issues'][0]['issue_type']='unsupported_claim'
    if fault=='hold':rr['human_review_reason']='owner hold'
    if fault=='spot':rr['spot_check_selected']=True
    if fault=='unresolved':result['status']='human_review_required'
    if fault=='reject':result['openai']['decision']='reject'
    if fault=='correction':result['claude_reconsideration']={'decision':'accept'}
    if fault=='statement':patch['statement']='new text'
    if fault=='anchor':patch['anchor_additions']=[{'new':'anchor'}]
    if fault=='scope':patch['scripture_refs']=['new']
    if fault=='route':patch['route_type']='new'
    if fault=='no_exclusion':patch['excluded_claim_relation_ids']=[]
    if fault=='duplicate':patch['excluded_claim_relation_ids']=['CR','CR']
    if fault=='foreign':result['claim_id']='OTHER'
    if fault=='ambiguous':o['results'].append(deepcopy(result))
    with pytest.raises(ValueError):h.relation_only_result(r,o,'C')


@pytest.mark.parametrize('status,retired,original',[('human_hold',False,False),
    ('rejected',False,False),('human_review_required',True,False),('human_review_required',False,True)])
def test_true_holds_and_original_population_never_use_context_branch(status,retired,original):
    item={'claim':{'claim_id':'C'}};audit={'meta':{'registry_read_scope':{'claim_ids':['C'] if original else []}}}
    with pytest.raises(ValueError):h.authority(None,item,audit,{'payload':{'review_status':status},'retired':retired})


@pytest.mark.parametrize('fault',['seal','write','call','schema'])
def test_receipt_does_not_create_approval_or_retry(fault):
    p={'schema_version':'wang_retained_closed_relation_only_claim_v1','master_data_mutations':0,
        'new_model_calls':0,'bindings':[],'coordinate_authority':{}}
    if fault=='write':p['master_data_mutations']=1
    if fault=='call':p['new_model_calls']=1
    if fault=='schema':p['schema_version']='foreign'
    p['artifact_sha256']=sha256_json(p)
    if fault=='seal':p['artifact_sha256']='changed'
    with pytest.raises(ValueError):h.verify(p)
