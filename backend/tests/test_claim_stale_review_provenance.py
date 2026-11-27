"""Only proven inherited AI review fields are reset; real history never changes."""
from copy import deepcopy
import json
import pytest
from backend.pipeline import claim_management as cm,claim_management_intake as intake,claim_management_batch as batch
from backend.pipeline.claim_metadata_projection import compile_projection,project_candidate,FIELD,RESET,STALE_CONTRACT
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.tests.test_claim_management_intake import claims,pass_review
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts


def seed(store,*,same=False):
    store.record_review('claims','C',decision='ai_consensus_reviewed',reviewer_kind='ai',reviewer_id='old-model',reason='Old statement review')
    reviewed=store.get_record_state('claims','C')
    fields={'claim_id':'C','claim_type':reviewed['payload']['claim_type'],
            'statement':reviewed['payload']['statement'] if same else '甲是假设，不是断言。','topic_terms':['new index']}
    store.apply_plan(store.plan_package({'package_id':'later-generation','claims':[fields]}))
    return reviewed,store.get_record_state('claims','C')


def prepared(store,root):
    saved=intake.prepare(['C'],root,instruction='Full Update, preserve real review history',store=store)
    request,execution=next(iter(batch.load(saved['manifest']['path'],root/'batch',store=store)[1].values()))
    return request,execution


def events(store):
    with store.connect() as c:
        return c.execute("SELECT review_event_id,object_revision,reviewer_kind,reviewer_id,decision,reason,artifact FROM wang_knowledge.review_events WHERE collection='claims' AND object_id='C' ORDER BY review_event_id").fetchall()


def test_initial_review_sees_full_program_reset_without_a_gpt_call(claims):
    store,root=claims;reviewed,old=seed(store);history=events(store)
    request,execution=prepared(store,root)
    def review(packet):
        row=next(r for r in packet['candidate']['records'] if r['object_id']=='C' and r['collection']=='claims')
        assert all(row['payload'].get(k)==v for k,v in RESET.items())
        assert row['payload']['statement']==old['payload']['statement']
        assert packet['before']['claims/C']['payload']==old['payload']
        assert 'stale_ai_review_provenance' in packet['program_owned_fields']
        return pass_review(packet)
    ports=SyntheticPorts(store,execution,opus=review)
    result=execute_claim_management(store,request,root=execution,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['opus']
    after=store.get_record_state('claims','C')
    assert {k:v for k,v in after['payload'].items() if k not in set(RESET)|{'revision'}}=={k:v for k,v in old['payload'].items() if k not in set(RESET)|{'revision'}}
    assert all(after['payload'].get(k)==v for k,v in RESET.items())
    assert events(store)==history
    with store.connect() as c:
        original=c.execute("SELECT payload FROM wang_knowledge.object_versions WHERE collection='claims' AND object_id='C' AND revision=%s",(reviewed['revision'],)).fetchone()[0]
    assert original==reviewed['payload']
    assert store.claim_management_qualification('C')['ready']


@pytest.mark.parametrize('fault',['same_statement','human_history','not_ai','changed_note','missing_version','retired'])
def test_unproved_or_human_review_is_never_reset(claims,fault):
    store,root=claims;reviewed,old=seed(store,same=fault=='same_statement')
    if fault=='human_history':
        with store.connect() as c:
            c.execute("UPDATE wang_knowledge.review_events SET reviewer_kind='human' WHERE collection='claims' AND object_id='C'")
    elif fault=='not_ai':
        with store.connect() as c:c.execute("UPDATE wang_knowledge.review_events SET decision='candidate' WHERE collection='claims' AND object_id='C'")
    elif fault=='missing_version':
        with store.connect() as c:c.execute("DELETE FROM wang_knowledge.object_versions WHERE collection='claims' AND object_id='C' AND revision=%s",(reviewed['revision'],))
    elif fault=='changed_note':old=deepcopy(old);old['payload']['review_note']='A different later review'
    elif fault=='retired':old=deepcopy(old);old['retired']=True
    request,_=prepared(store,root)
    with store.connect() as c,c.cursor() as cur:proof=compile_projection(cur,request,{'claims/C':old},cm.Policy.model_validate_json(cm.source.verify_file(request.policy).read_text()))
    assert not proof or proof['contract']!=STALE_CONTRACT


@pytest.mark.parametrize('fault',['reviewer','statement_without_citation','extra_field_without_citation','readonly','pin','human_race'])
def test_projection_cannot_manufacture_approval_hide_content_or_ignore_races(claims,fault):
    store,root=claims;_,old=seed(store);request,execution=prepared(store,root)
    before={cm.source.key(*k):cm.model_state(v) for k,v in store.read_claim_context([('claims','C'),('source_documents','S')]).items()}
    policy=cm.Policy.model_validate_json(cm.source.verify_file(request.policy).read_text())
    with store.connect() as c,c.cursor() as cur:proof=compile_projection(cur,request,before,policy)
    materials={'unit_request':request.model_dump(mode='json'),'before':before,FIELD:proof}
    if fault=='readonly':materials['unit_request']['permissions']=[]
    if fault=='pin':proof['stale_review_markers']['claims/C']['before_pin'][0]+=1
    if fault in {'readonly','pin'}:
        with pytest.raises(ValueError):project_candidate(cm.existing_candidate(before),{},materials)
        return
    candidate,_=project_candidate(cm.existing_candidate(before),{},materials)
    if fault=='human_race':
        from backend.pipeline.claim_metadata_projection import assert_current
        with store.connect() as c:
            c.execute("UPDATE wang_knowledge.review_events SET reviewer_kind='human' WHERE collection='claims' AND object_id='C'")
        with store.connect() as c,c.cursor() as cur:
            with pytest.raises(ValueError,match='history drift'):assert_current(cur,request,before,policy,proof)
        assert store.get_record_state('claims','C')==old
        return
    row=next(r for r in candidate.records if r.key=='claims/C')
    if fault=='reviewer':row.payload['reviewed_by']='owner'
    elif fault=='statement_without_citation':row.payload['statement']='Unsupported different teaching'
    else:row.payload['topic_terms']=['different']
    with pytest.raises(ValueError):cm.validate_candidate(request,before,candidate,cm.load_authority(request,source_documents=cm.source_documents(before))[1],metadata_projection=proof)


@pytest.mark.parametrize('fault',[None,'repeat','no_finding','human_stop','too_many_calls','wrong_parent','wrong_code','failed_tests','no_projection','foreign_target'])
def test_one_engineering_pair_is_bound_to_the_fixed_defect_and_history(claims,fault):
    from pathlib import Path
    import hashlib
    from backend.pipeline.claim_metadata_projection import repair_extension
    store,root=claims;_,old=seed(store);request,execution=prepared(store,root)
    before={cm.source.key(*k):cm.model_state(v) for k,v in store.read_claim_context([('claims','C'),('source_documents','S')]).items()}
    original_policy=cm.Policy.model_validate_json(cm.source.verify_file(request.policy).read_text())
    with store.connect() as c,c.cursor() as cur:projection=compile_projection(cur,request,before,original_policy)
    previous={'kind':'claim_management_action','action_id':request.action_id,'execution_root':str(execution),'request':request.model_dump(mode='json'),
        'policy':original_policy.model_dump(mode='json'),'result':{'status':'paused','reason':'engineering_required'},
        'original_goal':cm.original_goal(request,before),'created_keys':[],'replacements':{},'call_reservations':{},'revision_rounds':{},
        'findings':{'F':{'category':'engineering','component':'review_note','target_key':'claims/C','affected_target_keys':['claims/C']}}}
    policy=original_policy.model_copy(deep=True);policy.initial_candidate='proposal';policy.budget.gpt_calls=1;policy.budget.opus_calls=1;policy.budget.max_revisions=1
    names=('claim_metadata_projection.py','claim_management.py','cvp_sql_actions.py')
    directory=Path(cm.__file__).parent
    implementation={str(Path('backend/pipeline')/name):hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in names}
    report=intake.immutable(root/'fixed-tests.json',{'passed':13,'failed':0,'implementation':implementation})
    proof={'defect':'stale_ai_review_provenance_v1','predecessor_action_id':request.action_id,
        'prior_metadata_sha256':cm.sha256_json(previous),'finding_id':'F','projection':projection,
        'fixed_commit':'a'*40,'implementation':implementation,'test_evidence':report}
    if fault=='repeat':previous['original_goal']['engineering_repair_defects']=[proof['defect']]
    elif fault=='no_finding':previous['findings']={}
    elif fault=='human_stop':previous['result']['reason']='human_required'
    elif fault=='too_many_calls':policy.budget.opus_calls=2
    elif fault=='wrong_parent':proof['predecessor_action_id']='foreign'
    elif fault=='wrong_code':proof['implementation']={}
    elif fault=='failed_tests':proof['test_evidence']=intake.immutable(root/'failed-tests.json',{'passed':1,'failed':1,'implementation':implementation})
    elif fault=='no_projection':proof['projection']=None
    elif fault=='foreign_target':proof['projection']=deepcopy(projection);proof['projection']['stale_review_markers']['claims/D']=proof['projection']['stale_review_markers'].pop('claims/C')
    proof['prior_metadata_sha256']=cm.sha256_json(previous)
    authority={'stale_ai_provenance_repair':proof}
    if fault:
        with pytest.raises((ValueError,KeyError)):repair_extension(previous,request,policy,authority)
    else:
        assert repair_extension(previous,request,policy,authority)==proof
        # The common continuation preserves the original goal and records the
        # stable defect, so another successor cannot obtain this pair again.
        successor=request.model_copy(deep=True,update={'action_id':request.action_id+'-repair','predecessor_action_id':request.action_id})
        original_auth=json.loads(cm.source.verify_file(request.authorization).read_text())
        original_auth.update(authority)
        original_auth['owner_call_extension']={'predecessor_action_id':request.action_id,'prior_metadata_sha256':cm.sha256_json(previous),
            'budget':policy.budget.model_dump(mode='json'),'authorization_quote':'工程修复后大模型调动应该自动增加一次','basis':'remaining_findings',
            'current_objects_sha256':cm.sha256_json(before),'related_action_metadata_sha256':{}}
        successor.authorization=cm.source.FileBinding.model_validate(intake.immutable(root/'engineering-authority.json',original_auth))
        value=cm.continuation_budget(previous,successor,policy)
        assert value['engineering_repair']==proof
        assert value['original_goal']['engineering_repair_defects']==[proof['defect']]
        assert value['prior_call_reservations']==previous['call_reservations']
        # SQL stores each successor's fresh original_goal separately from its
        # continuation. A later ordinary handoff must not erase the old repair.
        hop=deepcopy(previous)
        hop.update(request=successor.model_dump(mode='json'),policy=policy.model_dump(mode='json'),
                   original_goal=cm.original_goal(successor,before),continuation=value)
        assert not hop['original_goal'].get('engineering_repair_defects')
        repeated=deepcopy(proof)
        repeated.update(predecessor_action_id=successor.action_id,prior_metadata_sha256=cm.sha256_json(hop))
        with pytest.raises(ValueError,match='one-time'):
            repair_extension(hop,successor,policy,{'stale_ai_provenance_repair':repeated})
        hop['result']={'status':'paused','reason':'budget_exhausted'}
        ordinary=successor.model_copy(deep=True,update={'action_id':successor.action_id+'-ordinary',
                                                      'predecessor_action_id':successor.action_id})
        ordinary_auth=deepcopy(original_auth)
        ordinary_auth.pop('stale_ai_provenance_repair')
        ordinary_auth['owner_call_extension'].update(predecessor_action_id=successor.action_id,
                                                    prior_metadata_sha256=cm.sha256_json(hop))
        ordinary.authorization=cm.source.FileBinding.model_validate(intake.immutable(root/'ordinary-authority.json',ordinary_auth))
        carried=cm.continuation_budget(hop,ordinary,policy)
        assert carried['engineering_repair_defects']==[proof['defect']]
        assert 'engineering_repair' not in carried  # carry usage, never another grant
        later=deepcopy(hop)
        later.update(request=ordinary.model_dump(mode='json'),original_goal=cm.original_goal(ordinary,before),
                     continuation=carried,result={'status':'paused','reason':'engineering_required'})
        repeated.update(predecessor_action_id=ordinary.action_id,prior_metadata_sha256=cm.sha256_json(later))
        with pytest.raises(ValueError,match='one-time'):
            repair_extension(later,ordinary,policy,{'stale_ai_provenance_repair':repeated})
