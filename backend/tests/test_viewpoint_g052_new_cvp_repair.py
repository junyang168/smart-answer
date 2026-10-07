from copy import deepcopy
import pytest
from backend.pipeline.viewpoint_g052_new_cvp_repair import CLAIM, PIN, validate_scoped_proposal, validate_plan, verify_ledger
from backend.api.canonical_repository.viewpoint_foundation import sha256_json


def proposal():
    return {'claim_decisions':[{'claim_id':CLAIM,'components':[{'disposition':'new_viewpoint','target_viewpoint_revision_id':None,
        'local_new_viewpoint_key':'NON-MERIT','spans':[{'start_char':0,'end_char':24,'exact_text':'饒恕的條件關係並不表示人的行為能夠賺取神的恩典。'}],
        'evidence_step_ids':['DK-0bdf88fe8565-P09-E011'],'source_fragment_ids':['FR-notes_manuscript_6_3-0bdf88fe8565-DK-0bdf88fe8565-P09-E011-01']}]}],
        'new_viewpoint_candidates':[{'local_key':'NON-MERIT'}]}


def ledger():
    p=proposal();c=p['claim_decisions'][0]['components'][0]
    c={k:v for k,v in c.items() if k in {'spans','disposition','target_viewpoint_revision_id','local_new_viewpoint_key'}}
    c.update(disposition='deferred',local_new_viewpoint_key=None)
    rows=[{'claim_id':CLAIM,'components':[c],'resolution_status':'deferred','apply_status':'applied'}]
    rows += [{'claim_id':f'C-{i}','components':[],'resolution_status':'resolved','apply_status':'applied'} for i in range(13)]
    return {'status':'incomplete','grouping_claim_ids':sorted(r['claim_id'] for r in rows),'claim_dispositions':rows}


def seal(p): return p|{'artifact_sha256':sha256_json(p)}


def plan():
    items={'canonical_viewpoints':{'viewpoint_id':'CV-X','current_revision_id':'CVR-X'},
           'viewpoint_revisions':{'viewpoint_revision_id':'CVR-X','viewpoint_id':'CV-X'},
           'viewpoint_claim_links':{'claim_id':CLAIM,'pinned_claim_revision':3,'link_type':'equivalent_component',
                'component_locator':{'statement_component':'饒恕的條件關係並不表示人的行為能夠賺取神的恩典。','claim_sha256':PIN,
                    'canonical_spans':[{'start_char':0,'end_char':24,'exact_text':'饒恕的條件關係並不表示人的行為能夠賺取神的恩典。'}]},
                'viewpoint_id':'CV-X','validated_against_viewpoint_revision_id':'CVR-X','decision_id':'VID-X'},
           'viewpoint_identity_decisions':{'identity_decision_id':'VID-X','identity_candidate_id':'VIC-X'},
           'viewpoint_identity_candidates':{'identity_candidate_id':'VIC-X'}}
    return {'operations':[{'collection':c,'operation':'create','before_revision':None,'after_revision':1,'payload':p} for c,p in items.items()]}


def test_exact_positive_boundaries():
    p=ledger();verify_ledger(seal(p),p['grouping_claim_ids'])
    validate_scoped_proposal(proposal());assert validate_plan(plan())=='CV-X'


@pytest.mark.parametrize('field', ['viewpoint_revisions','viewpoint_relations','viewpoint_structures','argument_route_candidates'])
def test_reject_new_scope(field):
    p=proposal();p[field]=[{'new':True}]
    with pytest.raises(ValueError):validate_scoped_proposal(p)


@pytest.mark.parametrize('mutation', ['extra_claim','extra_candidate','defer','partial','foreign_evidence','foreign_fragment','existing_target','wrong_local_key'])
def test_reject_proposal_drift(mutation):
    p=proposal();c=p['claim_decisions'][0]['components'][0]
    if mutation=='extra_claim':p['claim_decisions'].append(deepcopy(p['claim_decisions'][0]))
    elif mutation=='extra_candidate':p['new_viewpoint_candidates'].append({'local_key':'SECOND'})
    elif mutation=='defer':c['disposition']='deferred'
    elif mutation=='partial':c['spans'][0]['end_char']=23
    elif mutation=='foreign_evidence':c['evidence_step_ids']=['FOREIGN']
    elif mutation=='foreign_fragment':c['source_fragment_ids']=['FOREIGN']
    elif mutation=='existing_target':c['target_viewpoint_revision_id']='CVR-OLD'
    else:c['local_new_viewpoint_key']='OTHER'
    with pytest.raises(ValueError):validate_scoped_proposal(p)


@pytest.mark.parametrize('mutation', ['extra','delete','update','before','revision','wrong_claim','partial_member','endpoint','decision','locator','pin','wrong_full_storage_type'])
def test_reject_plan_drift(mutation):
    p=plan();ops=p['operations'];link=ops[2]['payload']
    if mutation=='extra':ops.append(deepcopy(ops[0]))
    elif mutation=='delete':ops.pop()
    elif mutation=='update':ops[0]['operation']='update'
    elif mutation=='before':ops[0]['before_revision']=1
    elif mutation=='revision':ops[0]['after_revision']=2
    elif mutation=='wrong_claim':link['claim_id']='OTHER'
    elif mutation=='partial_member':link['link_type']='supports'
    elif mutation=='endpoint':link['viewpoint_id']='OTHER'
    elif mutation=='decision':link['decision_id']='OLD'
    elif mutation=='locator':link['component_locator']['canonical_spans'][0]['end_char']=23
    elif mutation=='pin':link['component_locator']['claim_sha256']='0'*64
    else:link.update(link_type='equivalent_full',component_locator=None)
    with pytest.raises(ValueError):validate_plan(p)


@pytest.mark.parametrize('mutation',['missing','duplicate','extra_defer','unapplied','hold','span','sha','wrong_denominator'])
def test_reject_ledger_drift(mutation):
    p=ledger();ids=p['grouping_claim_ids'][:]
    if mutation=='missing':p['claim_dispositions'].pop()
    elif mutation=='duplicate':p['claim_dispositions'][-1]=deepcopy(p['claim_dispositions'][-2])
    elif mutation=='extra_defer':p['claim_dispositions'][1]['resolution_status']='deferred'
    elif mutation=='unapplied':p['claim_dispositions'][0]['apply_status']='pending'
    elif mutation=='hold':p['blocked_claims']=[CLAIM]
    elif mutation=='span':p['claim_dispositions'][0]['components'][0]['spans'][0]['end_char']=23
    elif mutation=='wrong_denominator':ids=ids[:-1]
    sealed=seal(p)
    if mutation=='sha':sealed['artifact_sha256']='0'*64
    with pytest.raises(ValueError):verify_ledger(sealed,ids)
