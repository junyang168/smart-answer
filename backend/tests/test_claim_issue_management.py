"""Unified domain lifecycle, synthetic content and disposable PostgreSQL only."""
from copy import deepcopy
from types import SimpleNamespace as NS
import json

import pytest

from backend.pipeline import claim_issue_management as m, source_reconciliation as c
from backend.pipeline import source_reconciliation_runner as r
from backend.pipeline.cvp_sql_actions import execute_source_action, source_action_id
from backend.api.canonical_repository.postgres_store import ChangeSetConflict
from backend.tests.test_source_reconciliation import scenario, put
from backend.tests.test_cvp_sql_actions_postgres import source_run
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres, postgres_store


def test_production_policy_keeps_separate_domains_and_no_human_content_approval():
    from pathlib import Path
    path=Path(__file__).resolve().parents[1]/'pipeline/policies/wang_claim_issue_management_v1.json'
    policy=m.Policy.model_validate_json(path.read_text())
    assert set(policy.claim_fields)==m.CLAIM_FIELDS
    assert set(policy.evidence_fields)==m.EVIDENCE_FIELDS
    assert set(policy.link_fields)==m.LINK_FIELDS
    assert not policy.human_content_approval and policy.cvp_permissions==[]


@pytest.fixture
def issue_setup(scenario,tmp_path):
    manifest,states,_=scenario
    states=deepcopy(states)
    states['source_documents/SRC-test']['source_path']=manifest.before_source.path
    states['claims/CL-test']['occurrences']=[{'source_id':'SRC-test','anchors':[]}]
    states['evidence_steps/E-test'].update(support_eligibility='eligible',valid_for_identity_review=True)
    report=put(tmp_path/'finder.json',{'fixture':'actual saved finding'})
    policy=m.Policy(authorization_id='owner-bounded-test',authorization_quote='只处理既有主张及自身证据；完整检查观点影响',
                    claim_fields=['statement','occurrences'],evidence_fields=['source_fragment_ids','statement'],
                    link_fields=['pinned_claim_revision','component_locator','evidence_bindings'])
    policy_binding=put(tmp_path/'policy.json',policy.model_dump())
    return manifest,states,report,policy_binding


def issue_for(states,report,**kwargs):
    claim=states['claims/CL-test'];evidence=[states['evidence_steps/E-test']]
    return m.make_issue(claim,evidence,states['source_documents/SRC-test'],
        spans=[(0,len(claim['statement']),claim['statement'])],origin='opus_initial',report=report,reason='检查假设与断言',
        fragments=m.bound_fragments(evidence,lambda fid:states['source_fragments/'+fid]),**kwargs)


class ScopeStore:
    def __init__(self,states):
        self.states=states
        self.reads=[]
    def get_cvp_group(self,run):return {'claim_ids':['CL-test'],'campaign_sha256':'test'}
    def read_affected_context(self,ids):
        self.reads.append(ids)
        return {c.split_key(k):{'payload':deepcopy(p),'retired':False} for k,p in self.states.items()}
    def get_cvp_campaign(self,ident):return {'groups':[{'group_key':'g1','claim_ids':['CL-test']}]}
    def cvp_campaign_progress(self,ident):return []


def materials(issue_setup,tmp_path):
    _,states,report,policy=issue_setup
    issue=issue_for(states,report)
    manifest=m.manifest_for_issues(ScopeStore(states),'original-run',[issue],policy,tmp_path/'scope')
    frozen=c.freeze(manifest,states)
    return manifest,frozen,issue


def retain_proposal(frozen,issue,cause='already_sufficient'):
    cite={'source_id':'SRC-test','paragraph_key':'S0001','exact_quote':'甲是假设。'}
    affected=frozen['inventory']['affected_keys']
    return m.Proposal(records=[],dispositions=[c.Disposition(record_key=k,action='retain',reason='准确保留') for k in affected],
        claim_assessments=[m.Assessment(issue_id=issue.issue_id,cause=cause,reason='原文准确',citations=[cite])],
        cvp_impacts=[m.Impact(record_key=k,action='retain',reason='完整成员未改变',citations=[cite])
                     for k in affected if c.split_key(k)[0] in {'viewpoint_claim_links','canonical_viewpoints','viewpoint_revisions','viewpoint_relations'}])


def test_scope_has_no_source_write_or_sentence_reextraction(issue_setup,tmp_path):
    manifest,frozen,issue=materials(issue_setup,tmp_path)
    assert manifest.before_source==manifest.current_sources[0]
    assert not manifest.authorized_paragraph_keys and frozen['sentence_inventory']==[]
    assert all(p.collection!='source_documents' for p in manifest.permissions)
    assert frozen['after'][0]['paragraphs']['S0001']['text']=='甲是假设。'
    assert frozen['claim_issue_context']['issue_identities'][0]['issue_id']==issue.issue_id
    assert c.validate_proposal(frozen,retain_proposal(frozen,issue))==c.index(frozen['before_records'])


def test_finders_and_reasons_do_not_purchase_new_identity(issue_setup):
    _,states,report,_=issue_setup
    issue=issue_for(states,report)
    for origin in ('admission','gpt_proposal','opus_final','gemini_audit','owner'):
        assert issue.model_copy(update={'origin':origin,'reason':'另一阶段表达'}).issue_id==issue.issue_id
    changed=deepcopy(states);changed['source_fragments/FR-test']['paragraph_key']='S0002'
    assert issue_for(changed,report).issue_id!=issue.issue_id
    changed=deepcopy(states);changed['claims/CL-test']['revision']+=1
    assert issue_for(changed,report).issue_id!=issue.issue_id


def test_physical_report_and_exact_component_are_mandatory(issue_setup):
    _,states,report,_=issue_setup
    with pytest.raises(ValueError):issue_for(states,{**report,'file_sha256':'a'*64})
    issue=issue_for(states,report)
    with pytest.raises(ValueError):
        m.make_issue(states['claims/CL-test'],[],states['source_documents/SRC-test'],
            spans=[(-1,2,'xx')],origin=issue.origin,report=report,reason='bad span')


@pytest.mark.parametrize('cause',['evidence_binding','cvp_link','already_sufficient'])
def test_evidence_or_link_diagnosis_cannot_rewrite_meaning(issue_setup,tmp_path,cause):
    _,frozen,issue=materials(issue_setup,tmp_path)
    proposal=retain_proposal(frozen,issue,cause)
    post=c.index(frozen['before_records']);post['claims/CL-test']['statement']='甲是断言。'
    with pytest.raises(ValueError,match='rewrite|retained'):m.validate_resolution(frozen,proposal,post)


@pytest.mark.parametrize('quote,source',[('编辑标题','SRC-test'),('甲是假设。','foreign')])
def test_headings_and_other_source_are_not_claim_evidence(issue_setup,tmp_path,quote,source):
    _,frozen,issue=materials(issue_setup,tmp_path)
    proposal=retain_proposal(frozen,issue)
    proposal.claim_assessments[0].citations[0]=m.Citation(source_id=source,paragraph_key='S0001',exact_quote=quote)
    with pytest.raises(ValueError,match='approved body'):c.validate_proposal(frozen,proposal)


def test_held_evidence_not_promoted_and_old_quotes_not_removed(issue_setup,tmp_path):
    _,frozen,issue=materials(issue_setup,tmp_path)
    proposal=retain_proposal(frozen,issue,'evidence_binding')
    post=c.index(frozen['before_records']);post['evidence_steps/E-test']['source_fragment_ids']=[]
    with pytest.raises(ValueError,match='represented|discard'):m.validate_resolution(frozen,proposal,post)
    post=c.index(frozen['before_records']);post['evidence_steps/E-test']['support_eligibility']='context_only'
    with pytest.raises(ValueError,match='eligible bound'):m.validate_resolution(frozen,proposal,post)


@pytest.mark.parametrize('cause,only_context,accepted',[
    ('claim_statement',False,True),('claim_statement',True,False),
    ('evidence_binding',False,False)])
def test_statement_diagnosis_context_is_not_automatically_evidence(issue_setup,tmp_path,cause,only_context,accepted):
    _,frozen,issue=materials(issue_setup,tmp_path)
    frozen['after'][0]['paragraphs']['S0002']={'text':'另一句说明旧词过强。'}
    proposal=retain_proposal(frozen,issue,cause)
    if only_context:proposal.claim_assessments[0].citations=[]
    proposal.claim_assessments[0].citations.append(m.Citation(
        source_id='SRC-test',paragraph_key='S0002',exact_quote='另一句说明旧词过强。'))
    post=c.index(frozen['before_records'])
    post['claims/CL-test']['statement']='甲属于假设。'
    if cause=='evidence_binding':post['claims/CL-test']['statement']=c.index(frozen['before_records'])['claims/CL-test']['statement']
    if accepted:
        assert m.validate_resolution(frozen,proposal,post)==post
        assert set(post)==set(c.index(frozen['before_records']))
        assert post['evidence_steps/E-test']==c.index(frozen['before_records'])['evidence_steps/E-test']
    else:
        with pytest.raises(ValueError,match='eligible bound'):m.validate_resolution(frozen,proposal,post)


def test_binding_diagnosis_can_use_permitted_statement_operation_without_reclassifying(issue_setup,tmp_path):
    _,frozen,issue=materials(issue_setup,tmp_path)
    frozen['after'][0]['paragraphs']['S0002']={'text':'另一句说明旧词过强。'}
    proposal=retain_proposal(frozen,issue,'evidence_binding')
    proposal.claim_assessments[0].citations.append(m.Citation(
        source_id='SRC-test',paragraph_key='S0002',exact_quote='另一句说明旧词过强。'))
    post=c.index(frozen['before_records'])
    post['claims/CL-test']['statement']='甲属于假设。'
    proposal.records=[c.Row(collection='claims',object_id='CL-test',payload=deepcopy(post['claims/CL-test']))]
    next(d for d in proposal.dispositions if d.record_key=='claims/CL-test').action='update'
    original=proposal.model_dump()
    assert m.validate_resolution(frozen,proposal,post)==post
    assert proposal.model_dump()==original
    post['evidence_steps/E-test']['statement']='different'
    with pytest.raises(ValueError,match='unchanged Evidence graph'):
        m.validate_resolution(frozen,proposal,post)


def test_evidence_repair_adds_actual_own_quote_keeps_old_context_and_statement(issue_setup,tmp_path):
    _,states,report,policy_binding=issue_setup
    own=put(tmp_path/'complete-own-source.json',{'script':[{'text':'甲是假设。'},{'text':'甲不是断言。'}]})
    body=c.source_snapshot(own)
    states['source_documents/SRC-test'].update(source_path=own['path'],source_file_sha256=own['file_sha256'],
        source_body_sha256=body['body_sha256'],source_sha256=body['body_sha256'])
    states['source_fragments/FR-test']['source_sha256']=body['body_sha256']
    setup=(None,states,report,policy_binding)
    _,frozen,issue=materials(setup,tmp_path)
    post=c.index(frozen['before_records']);claim=post['claims/CL-test'];old_statement=claim['statement']
    claim['occurrences'][0]['anchors']=[{'evidence_id':'E-test','paragraph_key':'S0002','proposed_highlight':{'text':'甲不是断言。'}}]
    claim.update(revision=2,review_status='candidate')
    post['evidence_steps/E-test'].update(source_fragment_ids=['FR-test','FR-added'],statement='甲是假设，不是断言。',revision=2,review_status='candidate')
    fragment=deepcopy(post['source_fragments/FR-test'])
    fragment.update(fragment_id='FR-added',paragraph_key='S0002',verbatim_excerpt='甲不是断言。')
    post['source_fragments/FR-added']=fragment
    changed={k:p for k,p in post.items() if p!=c.index(frozen['before_records']).get(k)}
    proposal=retain_proposal(frozen,issue,'evidence_binding')
    proposal.claim_assessments[0].citations=[m.Citation(source_id='SRC-test',paragraph_key='S0002',exact_quote='甲不是断言。')]
    proposal.records=[c.Row.model_validate(row) for row in c.rows(changed)]
    proposal.dispositions=[c.Disposition(record_key=k,action='update' if k in changed else 'retain',reason='自身正文绑定或保留')
                           for k in set(frozen['inventory']['affected_keys'])|set(changed)]
    actual=c.validate_proposal(frozen,proposal)
    assert actual['claims/CL-test']['statement']==old_statement
    assert actual['evidence_steps/E-test']['source_fragment_ids']==['FR-test','FR-added']


def test_unchanged_and_shared_cvp_members_require_explicit_impact(issue_setup,tmp_path):
    _,frozen,issue=materials(issue_setup,tmp_path)
    frozen['inventory']['affected_keys']+=['viewpoint_claim_links/L1','canonical_viewpoints/V1','viewpoint_revisions/R1']
    for row in [('viewpoint_claim_links','L1'),('canonical_viewpoints','V1'),('viewpoint_revisions','R1')]:
        id_field=c.KNOWLEDGE_COLLECTIONS[row[0]][1]
        frozen['before_records'].append({'collection':row[0],'object_id':row[1],'payload':{id_field:row[1],'fixture':True}})
    proposal=retain_proposal(frozen,issue)
    post=c.index(frozen['before_records'])
    assert m.validate_resolution(frozen,proposal,post)==post
    proposal.cvp_impacts.pop()
    with pytest.raises(ValueError,match='complete CVP impact'):m.validate_resolution(frozen,proposal,post)


@pytest.mark.parametrize('mutation',['source','policy','claim','evidence','fragment','hold'])
def test_auth_current_version_or_scope_drift_fails_closed(issue_setup,tmp_path,mutation):
    manifest,frozen,issue=materials(issue_setup,tmp_path)
    states=c.index(frozen['before_records'])
    if mutation=='source':manifest=manifest.model_copy(update={'current_sources':[manifest.authorization]})
    elif mutation=='policy':manifest=manifest.model_copy(update={'permissions':[]})
    elif mutation=='claim':states['claims/CL-test']['revision']+=1
    elif mutation=='evidence':states['evidence_steps/E-test']['statement']='不同'
    elif mutation=='fragment':states['source_fragments/FR-test']['paragraph_key']='S0002'
    elif mutation=='hold':states['claims/CL-test']['review_status']='human_review_required'
    with pytest.raises(ValueError):m.validate_authority(manifest,states)


def test_policy_cannot_smuggle_cross_domain_or_membership_permission(issue_setup):
    _,states,_,policy_binding=issue_setup
    policy=m.read_policy(policy_binding)
    for field in ['review_status','evidence_step_ids','source_sha256']:
        with pytest.raises(ValueError):m.policy_permissions(policy.model_copy(update={'claim_fields':[field]}),states,'SRC-test',['CL-test'])
    for col in ['source_documents','claims','canonical_viewpoints']:
        permission=c.Permission(collection=col,object_id='foreign',fields=['statement'])
        with pytest.raises(ValueError):m.policy_permissions(policy.model_copy(update={'cvp_permissions':[permission]}),states,'SRC-test',['CL-test'])


def test_typed_finder_routes_proposed_defer_and_opus_evidence_not_link_errors():
    spans=[NS(start_char=0,end_char=1,exact_text='甲')]
    proposal=NS(claim_decisions=[NS(claim_id='C',components=[NS(disposition='deferred',reason='缺证据',spans=spans)])])
    assert m.review_findings(proposal,None)[0]['claim_id']=='C'
    review=NS(change_reviews=[NS(claim_id='C',component_index=0,coverage_requirement='evidence_insufficient',coverage_reason='正文不足')])
    assert len(m.review_findings(proposal,review))==1
    review.change_reviews[0].coverage_requirement='source_conflict'
    assert m.review_findings(proposal,review)==[{'claim_id':'C','spans':[(0,1,'甲')],'reason':'正文不足'}]
    review.change_reviews[0].coverage_requirement='cvp_required'
    assert m.review_findings(proposal,review)==[]


def test_audit_only_upheld_claims_route_no_rejected_or_link_disguise():
    packet={'items':[{'item_id':i,'claim':{'claim_id':'C','statement':'甲'}} for i in ['3:upheld','3:rejected','4:link']]}
    report={'actions':[{'item_id':'3:upheld','verdict':'audit_objection_upheld'},
                       {'item_id':'3:rejected','verdict':'audit_objection_rejected'},
                       {'item_id':'4:link','verdict':'audit_objection_upheld'}]}
    assert len(m.audit_claim_findings(packet,report))==1


def test_declined_supports_is_classification_not_prose_matched_approval():
    from backend.pipeline.viewpoint_authorized_source_repair_357 import Review
    target={'claim':{'claim_id':'C','statement':'甲乙'},'before_link':{
        'claim_id':'C','viewpoint_claim_link_id':'L','link_type':'equivalent_component'}}
    review=Review.model_validate({'verdicts':[{'claim_id':'C','approved':False,
        'reason':'Unclassified diagnostic; do not infer permission.','problems':['connection or source needs classification']}]})
    before=deepcopy(target); raw=review.model_dump(mode='json')
    finding=m.declined_supports_findings([target],review)[0]
    assert finding['spans']==[(0,2,'甲乙')]
    diagnostic=json.loads(finding['reason'])
    assert diagnostic['content_approval'] is False and diagnostic['verdict']==raw['verdicts'][0]
    assert target==before and review.model_dump(mode='json')==raw
    review.verdicts[0].approved=True;review.verdicts[0].problems=[]
    assert m.declined_supports_findings([target],review)==[]
    review.verdicts[0].approved=False
    assert m.declined_supports_findings([target],review)==[]  # malformed refusal stays strict validation


@pytest.mark.parametrize('mutation',['foreign','duplicate','inactive','non_equivalent','generation','population'])
def test_declined_supports_scope_refuses_unsafe_diagnostics(mutation):
    from backend.pipeline.viewpoint_authorized_source_repair_357 import Review
    target={'claim':{'claim_id':'C','statement':'甲乙'},'before_link':{
        'claim_id':'C','viewpoint_claim_link_id':'L','link_type':'equivalent_component'}}
    targets=[target]
    review=Review.model_validate({'verdicts':[{'claim_id':'C','approved':False,'reason':'reason','problems':['problem']}]})
    if mutation=='foreign':target['before_link']['claim_id']='OTHER'
    if mutation=='duplicate':targets.append(deepcopy(target))
    if mutation=='inactive':target['before_link']['effective_state']='retired'
    if mutation=='non_equivalent':target['before_link']['link_type']='supports'
    if mutation=='generation':
        other=deepcopy(target);other['before_link']['viewpoint_claim_link_id']='L2';other['claim']['statement']='不同';targets.append(other)
    if mutation=='population':review.verdicts[0].claim_id='OTHER'
    with pytest.raises(ValueError):m.declined_supports_findings(targets,review)


@pytest.mark.parametrize('mutation',[None,'root','origin','pin','reason','held','retired','claim','answered_pass'])
def test_declined_supports_dependency_is_bound_diagnostic_only(tmp_path,monkeypatch,mutation):
    from backend.pipeline import viewpoint_resolution_runtime as runtime
    from backend.pipeline.viewpoint_authorized_source_repair_357 import Review
    from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha
    a=tmp_path/'bound-action';a.mkdir();report=put(a/'raw-opus.json',{'original':'raw'})
    claim={'claim_id':'C','revision':2,'statement':'甲乙','review_status':'ai_consensus_reviewed'}
    pin=[2,semantic_record_sha(claim),False]
    target={'claim':claim,'before_link':{'claim_id':'C','viewpoint_claim_link_id':'L','link_type':'equivalent_component'}}
    packet={'schema_version':'wang_audit_link_repair_packet_v1','targets':[target]}
    put(a/'packet.json',packet)
    response={'verdicts':[{'claim_id':'C','approved':False,'reason':'diagnostic','problems':['classify source or link']}]}
    review=Review.model_validate(response);finding=m.declined_supports_findings([target],review)[0]
    issue=NS(report=NS(path=report['path']),origin='opus_initial',claim_id='C',reason=finding['reason'],
        spans=finding['spans'],claim_revision=2,claim_sha256=pin[1])
    value={'claim_ids':['ORIGINAL'],'execution_roots':{a.name:str(a)},'reviewed':{'evidence':{'read_pins':[
        {'collection':'claims','object_id':'C','pin':pin.copy()}]}}}
    state={'revision':2,'content_sha256':pin[1],'retired':False,'payload':deepcopy(claim)}
    monkeypatch.setattr(runtime,'subscription_client',lambda *args:NS(model='claude-opus-5-5',backend='claude_subscription'))
    monkeypatch.setattr(runtime,'read_saved_model_response',lambda adapter,p,path:response)
    if mutation=='root':value['execution_roots']={}
    if mutation=='origin':issue.origin='owner'
    if mutation=='pin':value['reviewed']['evidence']['read_pins'][0]['pin'][0]=1
    if mutation=='reason':issue.reason='invented'
    if mutation=='held':state['payload']['review_status']='human_hold'
    if mutation=='retired':state['retired']=True
    if mutation=='claim':state['payload']['statement']='changed'
    if mutation=='answered_pass':response['verdicts'][0].update(approved=True,problems=[])
    if mutation:
        from backend.pipeline.claim_owner_revision import dependency_issue
        with pytest.raises(ValueError):dependency_issue(issue,value,state)
    else:
        before=deepcopy((value,state,packet,response))
        from backend.pipeline.claim_owner_revision import dependency_issue
        dependency_issue(issue,value,state)
        assert (value,state,packet,response)==before and value['claim_ids']==['ORIGINAL']
        action={'action_id':a.name,'kind':'audit_supports_correction',
            'materials':{'packet':packet,'read_pins':deepcopy(value['reviewed']['evidence']['read_pins'])}}
        action['materials_sha256']=m.sha256_json(action['materials'])
        value['reviewed']['evidence']['read_pins']=[]
        dependency_issue(issue,value,state,action=action)
        for field,bad in [('action_id','foreign'),('kind','source_claim_revision'),('materials_sha256','wrong')]:
            invalid=deepcopy(action);invalid[field]=bad
            with pytest.raises(ValueError):dependency_issue(issue,value,state,action=invalid)
        invalid=deepcopy(action);invalid['materials']['read_pins'][0]['pin'][0]=1
        invalid['materials_sha256']=m.sha256_json(invalid['materials'])
        with pytest.raises(ValueError):dependency_issue(issue,value,state,action=invalid)


def test_prior_management_blocks_same_claim_not_unrelated_claim_or_link_action():
    evidence={'action':{'action_id':'CM','materials':{'claim_issue_context':{'issues':[{'claim_id':'A'}]}}}}
    assert m.prior_management_action(evidence,[{'claim_id':'A'}])=='CM'
    assert m.prior_management_action(evidence,[{'claim_id':'B'}]) is None
    evidence={'action':{'action_id':'coverage','materials':{'claim_management_resolution':{'management_action_id':'CM','issues':[{'claim_id':'A'}]}}}}
    assert m.prior_management_action(evidence,[{'claim_id':'A'}])=='CM'
    assert m.prior_management_action({'action':{'action_id':'link'}},[{'claim_id':'A'}]) is None


def test_new_issue_schema_does_not_modify_historical_model_contract():
    from backend.pipeline.source_reconciliation_runtime import model_adapter
    old=model_adapter('proposal');new=model_adapter('proposal',issue=True)
    assert 'claim_assessments' not in old._response_model.model_fields
    assert 'claim_assessments' in new._response_model.model_fields
    assert old.prompt_sha256!=new.prompt_sha256


def test_sql_intake_reuses_identity_and_can_only_tighten_budget(postgres_store,issue_setup):
    manifest,states,report,_=issue_setup
    run=source_run(postgres_store,(manifest,states,{}))
    issue=issue_for(states,report)
    initial=postgres_store.register_claim_issue(run,issue.model_dump(mode='json'))
    assert initial['state']=='detected'
    again=issue.model_copy(update={'origin':'gemini_audit','correction_used':True})
    record=postgres_store.register_claim_issue(run,again.model_dump(mode='json'))
    assert len(record['reports'])==2 and record['correction_used']
    record=postgres_store.register_claim_issue(run,issue.model_dump(mode='json'))
    assert record['correction_used']
    with pytest.raises(ChangeSetConflict,match='budget'):postgres_store.bind_claim_issue_action([issue.issue_id],run_id=run,action_id='new-root')
    assert postgres_store.get_record('claims','CL-test')==states['claims/CL-test']


def test_sql_intake_requires_original_membership_and_same_action(postgres_store,issue_setup):
    manifest,states,report,_=issue_setup
    run=source_run(postgres_store,(manifest,states,{}));issue=issue_for(states,report)
    with pytest.raises(ChangeSetConflict):postgres_store.register_claim_issue('missing',issue.model_dump(mode='json'))
    postgres_store.register_claim_issue(run,issue.model_dump(mode='json'))
    postgres_store.bind_claim_issue_action([issue.issue_id],run_id=run,action_id='A')
    postgres_store.bind_claim_issue_action([issue.issue_id],run_id=run,action_id='A')
    with pytest.raises(ChangeSetConflict):postgres_store.bind_claim_issue_action([issue.issue_id],run_id=run,action_id='B')


class IssuePorts:
    def __init__(self,issue,*,refuse=False,change=False):
        self.issue=issue;self.calls=[];self.refuse=refuse;self.change=change
    def claim_issue_adapter(self,stage):
        from backend.pipeline.source_reconciliation_runtime import model_adapter
        adapter=model_adapter(stage,issue=True)
        def generate(packet):
            self.calls.append(stage)
            if 'review' in stage:
                return {'verdicts':[{'record_key':d['record_key'],'approved':not self.refuse,
                    'reason':'完整后像检查','problems':['不一致'] if self.refuse else []} for d in packet['proposal']['dispositions']]}
            if stage=='correction':return deepcopy(packet['initial_proposal'])
            frozen={'inventory':packet['inventory']}
            p=retain_proposal(frozen,self.issue)
            records=[]
            if self.change:
                payload=deepcopy(c.index(packet['records'])['claims/CL-test'])
                payload.update(statement='甲属于假设。',revision=payload['revision']+1,review_status='candidate')
                records=[{'collection':'claims','object_id':'CL-test','payload_json':json.dumps(payload,ensure_ascii=False)}]
                next(d for d in p.dispositions if d.record_key=='claims/CL-test').action='update'
                p.claim_assessments[0].cause='claim_statement'
            return {**p.model_dump(exclude={'records','unadmitted_candidates'}),
                    'unadmitted_candidates_json':[],'records':records}
        adapter.generate=generate
        return adapter


def test_joint_issue_review_uses_same_sql_commit_and_saved_answers(postgres_store,issue_setup,tmp_path,monkeypatch):
    manifest,states,report,policy=issue_setup
    run=source_run(postgres_store,(manifest,states,{}));issue=issue_for(states,report)
    postgres_store.register_claim_issue(run,issue.model_dump(mode='json'))
    scoped=m.manifest_for_issues(postgres_store,run,[issue],policy,tmp_path/'scoped')
    ports=IssuePorts(issue)
    monkeypatch.setattr('backend.pipeline.cvp_sql_commit.verified_group_backup',lambda *args:{'dump':report,'restore_catalog_nonempty':True})
    before=deepcopy(postgres_store.get_record_state('claims','CL-test'))
    result=execute_source_action(postgres_store,run,scoped.model_dump(),root=tmp_path/'action',ports=ports)
    assert result['status']=='no_content_write' and result['change_set_id'] is None
    assert postgres_store.get_record_state('claims','CL-test')==before
    assert ports.calls==['proposal','initial_review']
    assert postgres_store.get_cvp_group(run)['reviewed']['evidence']['pending_cvp_claim_ids']==['CL-test']
    assert execute_source_action(postgres_store,run,scoped.model_dump(),root=tmp_path/'action',ports=ports)['status']=='already_applied'
    assert ports.calls==['proposal','initial_review']
    # Even a caller bypassing run_group cannot declare the group complete
    # while the separate CVP coverage obligation is still pending.
    audit={'fixture':'not a semantic production audit'}
    audit_binding=put(tmp_path/'audit.json',audit)
    with pytest.raises(ChangeSetConflict,match='remains pending'):
        postgres_store.complete_cvp_group(run,audit=audit,audit_binding=audit_binding,read_pins={})
    assert postgres_store.get_cvp_group(run)['checkpoint']=='applied'


def test_joint_terminal_disagreement_one_correction_no_write_or_new_budget(postgres_store,issue_setup,tmp_path):
    manifest,states,report,policy=issue_setup
    run=source_run(postgres_store,(manifest,states,{}));issue=issue_for(states,report)
    postgres_store.register_claim_issue(run,issue.model_dump(mode='json'))
    scoped=m.manifest_for_issues(postgres_store,run,[issue],policy,tmp_path/'scoped')
    ports=IssuePorts(issue,refuse=True)
    for _ in range(2):
        with pytest.raises(r.Stop,match='terminal Opus'):execute_source_action(postgres_store,run,scoped.model_dump(),root=tmp_path/'action',ports=ports)
    assert ports.calls==['proposal','initial_review','correction','final_review']
    assert postgres_store.get_cvp_group(run)['checkpoint']=='prepared'
    assert not postgres_store.get_cvp_group(run)['change_set_ids']


def test_joint_change_late_failure_rolls_back_and_exact_saved_review_resumes(postgres_store,issue_setup,tmp_path,monkeypatch):
    manifest,states,report,policy=issue_setup
    run=source_run(postgres_store,(manifest,states,{}));issue=issue_for(states,report)
    postgres_store.register_claim_issue(run,issue.model_dump(mode='json'))
    scoped=m.manifest_for_issues(postgres_store,run,[issue],policy,tmp_path/'scoped')
    ports=IssuePorts(issue,change=True)
    monkeypatch.setattr('backend.pipeline.cvp_sql_commit.verified_group_backup',lambda *args:{'dump':report,'restore_catalog_nonempty':True})
    before=deepcopy(postgres_store.get_record_state('claims','CL-test'))
    original=postgres_store._assert_transaction_edges_and_reviews
    def fail(*args):raise ValueError('last joint transaction validator')
    monkeypatch.setattr(postgres_store,'_assert_transaction_edges_and_reviews',fail)
    with pytest.raises(ValueError,match='last joint transaction'):
        execute_source_action(postgres_store,run,scoped.model_dump(),root=tmp_path/'action',ports=ports)
    assert postgres_store.get_record_state('claims','CL-test')==before
    assert postgres_store.get_cvp_group(run)['checkpoint']=='reviewed'
    monkeypatch.setattr(postgres_store,'_assert_transaction_edges_and_reviews',original)
    result=execute_source_action(postgres_store,run,scoped.model_dump(),root=tmp_path/'action',ports=ports)
    assert result['status']=='applied' and ports.calls==['proposal','initial_review']
    assert postgres_store.get_record('claims','CL-test')['statement']=='甲属于假设。'
    assert len(postgres_store.get_change_history(result['change_set_id'])['operations'])==1


@pytest.mark.parametrize('scale',[1,10,100])
def test_fixed_readset_not_unrelated_current_rows_or_history(postgres_store,issue_setup,tmp_path,scale):
    import time
    import resource
    from backend.api.canonical_repository.postgres_store import canonical_json,record_content_sha
    from backend.pipeline.cvp_sql_actions import prepare_source_action
    manifest,states,report,policy=issue_setup
    run=source_run(postgres_store,(manifest,states,{}));issue=issue_for(states,report)
    with postgres_store.connect() as conn:
        for index in range(scale*10):
            payload={'source_id':f'UNRELATED-{index}','title':'unrelated '+('x'*1000),'source_type':'sermon_transcript','revision':1}
            conn.execute("INSERT INTO wang_knowledge.objects(collection,object_id,revision,content_sha256,payload) VALUES ('source_documents',%s,1,%s,%s::jsonb)",
                         (payload['source_id'],record_content_sha(payload),canonical_json(payload)))
            conn.execute("INSERT INTO wang_knowledge.pipeline_runs(run_id,subject_kind,subject_id,stage,trigger,status,metadata) VALUES (%s,'batch','unrelated','cvp_group','cli','succeeded',%s::jsonb)",
                         (f'HISTORY-{index}',canonical_json({'kind':'unrelated','large_history':'x'*1000})))
            # Unrelated Claims in the SAME own source are not affected merely
            # because their SourceDocument is common. Shared Evidence remains
            # a genuine reverse dependency and is tested independently.
            cid,eid,fid=f'OTHER-C-{index}',f'OTHER-E-{index}',f'OTHER-F-{index}'
            values=[('claims',cid,{**states['claims/CL-test'],'claim_id':cid,'evidence_step_ids':[eid]}),
                    ('evidence_steps',eid,{**states['evidence_steps/E-test'],'evidence_step_id':eid,
                         'source_fragment_ids':[fid],'produced_claim_ids':[cid]}),
                    ('source_fragments',fid,{**states['source_fragments/FR-test'],'fragment_id':fid})]
            for col,oid,row in values:
                conn.execute("INSERT INTO wang_knowledge.objects(collection,object_id,revision,content_sha256,payload) VALUES (%s,%s,1,%s,%s::jsonb)",
                             (col,oid,record_content_sha(row),canonical_json(row)))
    scoped=m.manifest_for_issues(postgres_store,run,[issue],policy,tmp_path/'scoped')
    root=tmp_path/'prepare'
    started=time.perf_counter()
    action=prepare_source_action(postgres_store,run,scoped,root=root)
    elapsed=time.perf_counter()-started
    rows=action['materials']['before_records']
    assert {c.key(row['collection'],row['object_id']) for row in rows}=={
        'claims/CL-test','evidence_steps/E-test','source_fragments/FR-test','source_documents/SRC-test','questions/Q-test'}
    assert len(canonical_json(action['materials']).encode())<100000 and elapsed<5
    # Warm resume is indexed immutable SQL/material equality, never a rebuild.
    started=time.perf_counter()
    assert prepare_source_action(postgres_store,run,scoped,root=root)==action
    print(json.dumps({'scale':scale,'affected_rows':len(rows),'first_seconds':elapsed,
        'resume_seconds':time.perf_counter()-started,'material_bytes':len(canonical_json(action['materials']).encode()),
        'historical_snapshot_reads':0,'first_scoped_compilations':1,'resume_scoped_compilations':0,
        'peak_rss_platform_units':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))


@pytest.mark.skipif(__import__('os').environ.get('WKP_CLAIM_ISSUE_REAL_PREVIEW')!='1',reason='explicit read-only saved g064 preview')
def test_real_g064_saved_intake_no_model_no_mutation(monkeypatch):
    import time
    from pathlib import Path
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.cvp_sql_flow import PROJECT,binding
    from backend.pipeline import viewpoint_batch_resolution_runner as batch
    from backend.pipeline.deferred_claim_cvp_continuation import adapters
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact,read_saved_model_response
    from backend.api.canonical_repository.viewpoint_resolution import ReviewClaim
    load_dotenv(PROJECT/'.env')
    class ReadOnly(PostgresKnowledgeStore):
        def connect(self):
            conn=super().connect();conn.execute('SET TRANSACTION READ ONLY');return conn
    store=ReadOnly()
    run='CVPG-45b5a3432dd06acf49f2d4d425b2bcfa9b79fd4a0abd404a16cfc4d167826e6f'
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution')/run/'normal-review'
    saved=store.get_cvp_group(run);materials=saved['materials'];packet=materials['packet']
    assert saved['checkpoint']=='prepared' and not saved['change_set_ids']
    consumed=[];started=time.perf_counter()
    def only_saved(adapter,request,path):
        assert path.is_file(),'new model is prohibited'
        consumed.append(path.name)
        raw=read_saved_model_response(adapter,request,path)
        from backend.api.canonical_repository.viewpoint_response_binding import bind_response
        # Pure program binding after exact old request/config/raw validation;
        # unlike call_model this preview writes no derived proof/envelope.
        return bind_response(adapter._response_model,raw,request),0,0.0
    monkeypatch.setattr(batch,'_call',only_saved)
    monkeypatch.setattr(batch,'_write_immutable',lambda *args:None)
    def route(**kw):
        findings=m.review_findings(kw['proposal'],kw['review'])
        if findings:raise m.IssuesDetected(findings,origin=kw['origin'],report=binding(kw['report_path']))
    with pytest.raises(m.IssuesDetected) as found:
        batch.run_batch(batch_id=packet['batch_id'],scope_label=packet['scope_label'],
            claims=[ReviewClaim.model_validate(p) for p in packet['claims']],registry_context=packet['registry_context'],
            pending_candidates=[],current_claim_links=packet['existing_links'],output_dir=root,
            dependents_loader=store.load_revision_dependents,claim_issue_handler=route,**adapters({'cvp_policy':materials['policy']}))
    assert found.value.origin=='opus_initial' and len(found.value.findings)==1
    assert consumed==['raw-proposal.json','raw-consolidation.json','raw-review.json']
    records=store.read_cvp_context([f['claim_id'] for f in found.value.findings])
    f=found.value.findings[0];claim=records['claims',f['claim_id']]['payload']
    evidence=[records['evidence_steps',eid]['payload'] for eid in sorted(claim['evidence_step_ids'])]
    fragments=m.bound_fragments(evidence,lambda fid:records['source_fragments',fid]['payload'])
    ids={p['source_id'] for p in fragments};assert len(ids)==1
    source=records['source_documents',ids.pop()]['payload']
    issue=m.make_issue(claim,evidence,source,spans=f['spans'],origin=found.value.origin,
                       report=found.value.report,reason=f['reason'],fragments=fragments)
    projection=c.source_snapshot(c.FileBinding(path=source['source_path'],file_sha256=source['source_file_sha256']),source=source)
    assert projection['body_sha256']==source['source_body_sha256']
    assert all('## ' not in p['text'] for p in projection['paragraphs'].values())
    assert store.get_cvp_group(run)==saved
    elapsed=time.perf_counter()-started;assert elapsed<5
    print(json.dumps({'issue_id':issue.issue_id,'origin':issue.origin,'claim_id':issue.claim_id,
        'claim_revision':issue.claim_revision,'claim_sha256':issue.claim_sha256,
        'actual_saved_roles':consumed,'current_rows':len(records),'seconds':elapsed,
        'historical_snapshot_reads':0,'new_model_calls':0,'production_writes':0},ensure_ascii=False))
