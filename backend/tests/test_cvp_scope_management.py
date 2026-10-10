"""Generic scope management: synthetic content, no real models/production writes."""
from copy import deepcopy
from pathlib import Path
import json

import pytest

from backend.pipeline import cvp_scope_management as m, source_reconciliation as c
from backend.pipeline import source_reconciliation_runner as r
from backend.pipeline import source_reconciliation_runtime as runtime
from backend.tests.test_source_reconciliation import scenario, add_viewpoint, put
from backend.tests.test_claim_issue_management import ScopeStore
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store


@pytest.fixture
def scoped(scenario,tmp_path):
    add_viewpoint(scenario)
    original,states,_=scenario
    states['source_documents/SRC-test']['source_path']=original.before_source.path
    states['claims/CL-test']['scripture_refs']=['新经文']
    states['viewpoint_claim_links/VCL-test']['component_locator']['claim_sha256']=c.semantic_record_sha(states['claims/CL-test'])
    states['viewpoint_claim_links/VCL-test']['evidence_bindings']=[{'evidence_step_id':'E-test','source_fragment_id':'FR-test'}]
    states['viewpoint_revisions/CVR-old']['scope']['scripture_scope']=['旧经文']
    states['viewpoint_relations/REL-test']={
        'viewpoint_relation_id':'REL-test','source_viewpoint_id':'CV-other','target_viewpoint_id':'CV-test',
        'validated_source_viewpoint_revision_id':'CVR-other','validated_target_viewpoint_revision_id':'CVR-old',
        'relation_type':'entails','reason':'真实已批准理由','effective_state':'active',
        'revision':1,'review_status':'system_approved','review_provenance':{'review_artifact_sha256':'a'*64,'basis_identity_decision_ids':[]}}
    # Unrelated context must stay identical; no source-root whole-sermon intake.
    states['canonical_viewpoints/CV-other']=dict(states['canonical_viewpoints/CV-test'],viewpoint_id='CV-other',current_revision_id='CVR-other')
    states['viewpoint_revisions/CVR-other']=dict(states['viewpoint_revisions/CVR-old'],viewpoint_revision_id='CVR-other',viewpoint_id='CV-other')
    policy=put(tmp_path/'policy.json',m.Policy(authorization_id='test-policy',authorization_quote='同一核心的常規範圍管理',scope_fields=['scripture_scope']).model_dump())
    report=put(tmp_path/'report.json',{'actual':'scope_mismatch'})
    store=ScopeStore(states)
    store.get_record=lambda col,oid:deepcopy(states[c.key(col,oid)])
    manifest=m.manifest_for_scope(store,'run','CV-test',policy,[report],tmp_path/'scope')
    frozen=c.freeze(manifest,states)
    return manifest,frozen,store,policy


def proposal(frozen, *, general=False):
    states=c.index(frozen['before_records'])
    value=frozen['viewpoint_scope_context']
    vid,rid=value['viewpoint_id'],value['before_revision_id']
    new=deepcopy(states['viewpoint_revisions/'+rid])
    new.update(viewpoint_revision_id='CVR-new',supersedes_revision_id=rid,review_status='candidate',
               revision=1,revision_number=1,provenance=None,approved_at=None,approved_by=None)
    new['scope']['scripture_scope']=[] if general else ['旧经文','新经文']
    changes={'viewpoint_revisions/CVR-new':new}
    for permission in c.Manifest.model_validate(frozen['manifest']).permissions:
        if permission.create:continue
        k=permission.key
        row=deepcopy(states[k]);row.update(revision=row.get('revision',1)+1,review_status='candidate')
        for field in permission.fields:
            row[field]=None if field=='review_provenance' else 'CVR-new'
        changes[k]=row
    affected=set(frozen['inventory']['affected_keys'])|set(changes)
    return m.Proposal(records=[c.Row.model_validate(x) for x in c.rows(changes)],
        dispositions=[c.Disposition(record_key=k,action='update' if k in changes else
            'impact_only' if c.split_key(k)[0] in c.IMPACT_ONLY else 'retain',reason='精确保留或版本更新') for k in sorted(affected)],
        scope_assessment=m.Assessment(action='scope_enrichment',reason='同一核心，不把证据出现经文误当适用限制',
            claim_ids=value['claim_ids'],citations=[m.Citation(claim_id='CL-test',source_id='SRC-test',paragraph_key='S0001',exact_quote='甲是假设。')]))


def wire(proposed):
    data=proposed.model_dump()
    data['records']=[{'collection':x.collection,'object_id':x.object_id,'payload_json':json.dumps(x.payload,ensure_ascii=False)} for x in proposed.records]
    data['unadmitted_candidates_json']=[];data.pop('unadmitted_candidates')
    return data


@pytest.mark.parametrize('general',[False,True])
def test_new_revision_and_complete_consumers_core_unchanged(scoped,general):
    manifest,frozen,_,_=scoped
    p=proposal(frozen,general=general)
    post=c.validate_proposal(frozen,p)
    before=c.index(frozen['before_records'])
    assert post['viewpoint_revisions/CVR-old']==before['viewpoint_revisions/CVR-old']
    for k in before:
        if k.startswith(('claims/','evidence_steps/','source_','canonical_viewpoints/CV-other')):assert post[k]==before[k]
    assert manifest.authorized_paragraph_keys==[] and frozen['sentence_inventory']==[]
    assert r.effective_proposal(frozen,wire(p))==p
    assert set(m.permissions(before,'CV-test')[0].fields)=={'current_revision_id'}


@pytest.mark.parametrize('field,value',[
    ('core_proposition','不同主张'),('proposition_signature',{'subject':'别的对象'}),
    ('attribution_subject','editor'),('not_a_direct_quote',False),('editorial_aliases',['新增综合说法'])])
def test_scope_cannot_change_identity(scoped,field,value):
    _,frozen,_,_=scoped
    p=proposal(frozen);post=c.index(frozen['before_records']);post.update(c.index(p.records))
    post['viewpoint_revisions/CVR-new'][field]=value
    with pytest.raises(ValueError,match='core/signature'):m.validate_postimages(frozen,p,post)


@pytest.mark.parametrize('mutate',[
    lambda p:p['claims/CL-test'].update(statement='改了原主张'),
    lambda p:p['viewpoint_claim_links/VCL-test'].update(link_type='supports'),
    lambda p:p['viewpoint_relations/REL-test'].update(relation_type='extends'),
    lambda p:p['viewpoint_relations/REL-test'].update(reason='更换理由'),
    lambda p:p['viewpoint_revisions/CVR-new']['scope'].update(audience_scope=['改变对象']),
    lambda p:p['viewpoint_revisions/CVR-new']['scope'].update(scripture_scope=['未经绑定的经文']),
    lambda p:p['viewpoint_relations/REL-test'].update(validated_target_viewpoint_revision_id='CVR-old'),
    lambda p:p['viewpoint_relations/REL-test'].update(review_provenance={'review_artifact_sha256':'a'*64}),
    lambda p:p.pop('viewpoint_revisions/CVR-old'),
])
def test_scope_semantic_and_exact_write_set_negatives(scoped,mutate):
    _,frozen,_,_=scoped
    p=proposal(frozen);post=c.index(frozen['before_records']);post.update(c.index(p.records))
    mutate(post)
    with pytest.raises((ValueError,KeyError)):m.validate_postimages(frozen,p,post)


@pytest.mark.parametrize('change',[
    lambda a:a.update(claim_ids=[]),
    lambda a:a.update(action='needs_direction'),
    lambda a:a['citations'][0].update(exact_quote='AI小标题'),
    lambda a:a['citations'][0].update(source_id='foreign'),
])
def test_full_denominator_and_exact_own_body(scoped,change):
    _,frozen,_,_=scoped
    p=proposal(frozen);value=p.model_dump();change(value['scope_assessment']);p=m.Proposal.model_validate(value)
    post=c.index(frozen['before_records']);post.update(c.index(p.records))
    with pytest.raises(ValueError):m.validate_postimages(frozen,p,post)


def test_only_typed_scope_problem_routes_and_rejections_do_not(scoped):
    packet={'items':[{'item_id':'4:CV:CL','viewpoint':{'viewpoint_id':'CV'},'audit_finding':{'issue':'scope_mismatch'}}]}
    for verdict,expected in [('unresolved',['CV']),('audit_objection_upheld',['CV']),('audit_objection_rejected',[])]:
        assert m.audit_targets(packet,{'actions':[{'item_id':'4:CV:CL','verdict':verdict}]})==expected
    packet['items'][0]['audit_finding']={'issue':'over_merge','reason':'scope mismatch words'}
    assert m.audit_targets(packet,{'actions':[{'item_id':'4:CV:CL','verdict':'unresolved'}]})==[]


def test_historical_structure_visible_but_never_rewritten(scoped):
    _,frozen,_,_=scoped
    before=c.index(frozen['before_records'])
    before['viewpoint_structure_revisions/VSR-history']={'structure_revision_id':'VSR-history','focal_viewpoints':[{'viewpoint_revision_id':'CVR-old'}]}
    inv=m.scope_inventory(before,'CV-test')
    assert 'viewpoint_structure_revisions/VSR-history' in inv['affected_keys']
    assert not inv['unknown_keys']
    assert 'viewpoint_structure_revisions' in c.IMPACT_ONLY


def historical_relation(frozen):
    from backend.api.canonical_repository.postgres_store import record_content_sha
    frozen=deepcopy(frozen);before=c.index(frozen['before_records'])
    row=deepcopy(before['viewpoint_relations/REL-test'])
    row.update(viewpoint_relation_id='REL-history',effective_state='retired',revision=2)
    before['viewpoint_relations/REL-history']=row
    frozen['before_records']=c.rows(before)
    frozen['inventory']=m.scope_inventory(before,'CV-test')
    frozen['sql_scope']={'read_pins':[{'collection':'viewpoint_relations','object_id':'REL-history',
        'pin':[2,record_content_sha(row),False]}]}
    return frozen


def test_exact_retired_relation_and_old_cvr_are_read_only_impacts(scoped):
    _,frozen,_,_=scoped;frozen=historical_relation(frozen)
    p=proposal(frozen)
    for d in p.dispositions:
        if d.record_key in {'viewpoint_relations/REL-history','viewpoint_revisions/CVR-old'}:
            d.action='impact_only'
    original=p.model_dump();before=c.index(frozen['before_records'])
    post=c.validate_proposal(frozen,p)
    assert p.model_dump()==original
    assert post['viewpoint_relations/REL-history']==before['viewpoint_relations/REL-history']
    assert post['viewpoint_revisions/CVR-old']==before['viewpoint_revisions/CVR-old']
    packet=c.review_packet(frozen,p)
    assert packet['proposal']==original
    raw={'artifact_sha256':'e'*64,'completed_at':'2026-10-10T00:00:00+00:00',
        'response':{'verdicts':[{'record_key':d.record_key,'approved':True,'reason':'full review','problems':[]} for d in p.dispositions]}}
    package=c.compile_package(frozen,p,raw)
    assert all(row['viewpoint_relation_id']!='REL-history' for row in package.get('viewpoint_relations',[]))
    assert all(row['viewpoint_revision_id']!='CVR-old' for row in package.get('viewpoint_revisions',[]))


@pytest.mark.parametrize('problem',['missing','sha','revision','physical_retired','ambiguous','hold','active','foreign','changed_old_cvr','missing_domain','write_history'])
def test_historical_scope_impact_proof_negatives(scoped,problem):
    _,frozen,_,_=scoped;frozen=historical_relation(frozen)
    if problem=='missing':frozen['sql_scope']['read_pins']=[]
    if problem=='sha':frozen['sql_scope']['read_pins'][0]['pin'][1]='foreign'
    if problem=='revision':frozen['sql_scope']['read_pins'][0]['pin'][0]=1
    if problem=='physical_retired':frozen['sql_scope']['read_pins'][0]['pin'][2]=True
    if problem=='ambiguous':frozen['sql_scope']['read_pins']*=2
    before=c.index(frozen['before_records'])
    if problem=='hold':before['viewpoint_relations/REL-history']['review_status']='human_hold'
    if problem=='active':before['viewpoint_relations/REL-history']['effective_state']='active'
    if problem=='foreign':before['viewpoint_relations/REL-history']['target_viewpoint_id']='foreign'
    if problem=='changed_old_cvr':before['viewpoint_revisions/CVR-old']['core_proposition']='different'
    frozen['before_records']=c.rows(before)
    if problem=='missing_domain':frozen.pop('viewpoint_scope_context')
    p=proposal(historical_relation(scoped[1]))
    for d in p.dispositions:
        if d.record_key in {'viewpoint_relations/REL-history','viewpoint_revisions/CVR-old'}:d.action='impact_only'
    if problem=='write_history':p.records.append(c.Row.model_validate(c.rows({'viewpoint_relations/REL-history':before['viewpoint_relations/REL-history']})[0]))
    with pytest.raises((ValueError,AttributeError)):c.validate_proposal(frozen,p)


def human_relation_reference(frozen):
    from backend.api.canonical_repository.postgres_store import record_content_sha
    frozen=deepcopy(frozen);before=c.index(frozen['before_records'])
    before['viewpoint_relations/REL-test']['review_status']='human_approved'
    frozen['before_records']=c.rows(before)
    frozen['sql_scope']={'read_pins':[{'collection':'viewpoint_relations','object_id':'REL-test',
        'pin':[1,record_content_sha(before['viewpoint_relations/REL-test']),False]}]}
    return frozen


def test_human_relation_meaning_remains_exact_in_scope_version_review(scoped):
    frozen=human_relation_reference(scoped[1]);p=proposal(frozen);before=c.index(frozen['before_records'])
    post=c.validate_proposal(frozen,p)
    assert m.approved_relation_reference_only(frozen,'viewpoint_relations/REL-test',c.index(p.records))
    for f in ['reason','relation_type','source_viewpoint_id','target_viewpoint_id','effective_state']:
        assert post['viewpoint_relations/REL-test'][f]==before['viewpoint_relations/REL-test'][f]
    assert post['viewpoint_relations/REL-test']['review_status']=='candidate'


@pytest.mark.parametrize('problem',['reason','type','endpoint','support','state','hold','superseded','missing_pin','pin_sha','retired','missing_scope','permission','old_revision','foreign_cvr','old_approval'])
def test_human_relation_reference_exception_is_not_semantic_authority(scoped,problem):
    frozen=human_relation_reference(scoped[1]);p=proposal(frozen);before=c.index(frozen['before_records']);proposed=c.index(p.records)
    row=proposed['viewpoint_relations/REL-test']
    if problem=='reason':row['reason']='changed'
    if problem=='type':row['relation_type']='extends'
    if problem=='endpoint':row['target_viewpoint_id']='foreign'
    if problem=='support':row['supporting_claim_ids']=['new']
    if problem=='state':row['effective_state']='retired'
    if problem=='hold':before['viewpoint_relations/REL-test']['review_status']='human_review_required'
    if problem=='superseded':before['viewpoint_relations/REL-test']['review_status']='superseded'
    if problem=='missing_pin':frozen['sql_scope']['read_pins']=[]
    if problem=='pin_sha':frozen['sql_scope']['read_pins'][0]['pin'][1]='changed'
    if problem=='retired':frozen['sql_scope']['read_pins'][0]['pin'][2]=True
    if problem=='missing_scope':frozen.pop('viewpoint_scope_context')
    if problem=='permission':frozen['manifest']['permissions']=[x for x in frozen['manifest']['permissions'] if x['object_id']!='REL-test']
    if problem=='old_revision':row['validated_target_viewpoint_revision_id']='CVR-old'
    if problem=='foreign_cvr':proposed['viewpoint_revisions/CVR-new']['supersedes_revision_id']='foreign'
    if problem=='old_approval':row['review_provenance']={'review_artifact_sha256':'a'*64}
    frozen['before_records']=c.rows(before)
    assert not m.approved_relation_reference_only(frozen,'viewpoint_relations/REL-test',proposed)


def test_action_budget_not_changed_by_policy_reason_or_root(scoped,tmp_path):
    manifest,frozen,store,policy=scoped
    from backend.pipeline.cvp_sql_actions import source_action_id
    expected=source_action_id(manifest)
    value=m.direction(manifest);value['authorization_id']='renamed';value['reports']=[]
    changed=manifest.model_copy(update={'viewpoint_scope_repair':c.FileBinding.model_validate(put(tmp_path/'renamed.json',value))})
    assert source_action_id(changed)==expected
    assert store.reads==[[('canonical_viewpoints','CV-test')]]


def test_old_manifest_serialization_is_exact_and_scope_models_keep_roles():
    adapter=runtime.model_adapter('proposal',scope=True)
    assert adapter.model_id=='gpt-6.1-sol' and adapter.backend=='codex_subscription'
    review=runtime.model_adapter('initial_review',scope=True)
    assert review.model_id=='claude-opus-5-5' and review.backend=='claude_subscription'


def test_compiler_binds_actual_review_not_old_relation_approval(scoped):
    _,frozen,_,_=scoped
    p=proposal(frozen)
    packet=c.review_packet(frozen,p)
    raw={'artifact_sha256':'e'*64,'completed_at':'2026-10-09T00:00:00+00:00',
         'response':{'verdicts':[{'record_key':d.record_key,'approved':True,'reason':'完整独立核查','problems':[]} for d in p.dispositions]}}
    result=c.compile_package(frozen,p,raw)
    rev=result['viewpoint_revisions'][0]
    relation=result['viewpoint_relations'][0]
    assert rev['provenance']['review_artifact_sha256']==relation['review_provenance']['review_artifact_sha256']=='e'*64
    assert relation['reason']=='真实已批准理由'
    assert len(result['viewpoint_identity_decisions'])==1
    assert not set(result)&{'claims','evidence_steps','source_documents','source_fragments'}


def test_real_request_budget_keeps_legacy_saved_identity(tmp_path):
    from backend.pipeline.viewpoint_resolution_runtime import authorized_request_limit
    from backend.pipeline.cvp_sql_flow import binding
    adapter=runtime.model_adapter('proposal',scope=True)
    auth=put(tmp_path/'auth.json',{'schema_version':'cvp_sql_request_limit_authorization.v1',
        'campaign_sha256':'campaign','max_request_bytes':600000,'authorized_by':'owner','owner_direction':'600K'})
    with authorized_request_limit(tmp_path/'execution','campaign',Path(auth['path'])):
        cap,budget=r.request_limit(adapter,tmp_path/'execution'/'action'/'proposal.raw.json')
        assert cap==600000
        assert r.request_limit(adapter,tmp_path/'execution'/'action'/'proposal.raw.json',saved={'request_budget':budget})==(cap,budget)
        assert r.request_limit(adapter,tmp_path/'outside'/'old.json',saved={})==(500000,None)
        with pytest.raises(ValueError):r.request_limit(adapter,tmp_path/'outside'/'new.json')
    assert r.request_limit(adapter,tmp_path/'execution'/'action'/'proposal.raw.json',saved={'request_budget':budget})==(cap,budget)


def test_consumed_incomplete_input_direction_does_not_replay_new_revision():
    from backend.pipeline.cvp_sql_adjudication_input_repair import applies_to_packet
    old={'items':[{'item_id':'x','viewpoint':{'current_revision_id':'old'}}]}
    assert applies_to_packet(old,deepcopy(old))
    assert not applies_to_packet(old,{'items':[{'item_id':'x','viewpoint':{'current_revision_id':'new'}}]})
    with pytest.raises(ValueError):applies_to_packet({'items':old['items']+[{'item_id':'y'}]},old)


@pytest.mark.parametrize('late_failure',[False,True])
def test_scope_action_shared_transaction_rollbacks_and_no_model_replay(scoped,tmp_path,postgres_store,monkeypatch,late_failure):
    from backend.pipeline.cvp_sql_actions import execute_source_action
    from backend.tests.test_cvp_sql_actions_postgres import source_run
    from backend.api.canonical_repository.postgres_store import record_content_sha
    manifest,frozen,_,_=scoped
    states=c.index(frozen['before_records'])
    # The relation endpoint/current revision context stays in the full readset.
    policy=frozen['viewpoint_scope_context']['policy']
    reports=frozen['viewpoint_scope_context']['reports']
    run=source_run(postgres_store,(manifest,states,None))
    manifest=m.manifest_for_scope(postgres_store,run,'CV-test',policy,reports,tmp_path/'sql-scope')
    calls=[]
    class Ports:
        def scope_adapter(self,stage):
            a=runtime.model_adapter(stage,scope=True)
            def generate(packet):
                calls.append(stage)
                if 'review' in stage:
                    return {'verdicts':[{'record_key':d['record_key'],'approved':True,'problems':[],'reason':'完整独立核查'} for d in packet['proposal']['dispositions']]}
                fake={'before_records':packet['records'],'manifest':packet['manifest'],'inventory':packet['inventory'],
                      'viewpoint_scope_context':packet['viewpoint_scope_context']}
                return wire(proposal(fake))
            a.generate=generate
            return a
    backup=put(tmp_path/'synthetic-backup.dump',{'not':'a production dump'})
    monkeypatch.setattr('backend.pipeline.cvp_sql_flow.verified_group_backup',lambda *a:{'dump':backup,'restore_catalog_nonempty':True})
    original=postgres_store._assert_transaction_edges_and_reviews
    before=postgres_store.get_record('canonical_viewpoints','CV-test')
    if late_failure:
        def fail(*args):raise ValueError('late scope validation')
        monkeypatch.setattr(postgres_store,'_assert_transaction_edges_and_reviews',fail)
        with pytest.raises(ValueError,match='late scope validation'):
            execute_source_action(postgres_store,run,manifest.model_dump(),root=tmp_path/'sql-action',ports=Ports())
        assert postgres_store.get_record('canonical_viewpoints','CV-test')==before
        assert postgres_store.get_record('viewpoint_revisions','CVR-new') is None
        monkeypatch.setattr(postgres_store,'_assert_transaction_edges_and_reviews',original)
    result=execute_source_action(postgres_store,run,manifest.model_dump(),root=tmp_path/'sql-action',ports=Ports())
    assert result['status']=='applied'
    assert calls==['proposal','initial_review']
    assert postgres_store.get_record('canonical_viewpoints','CV-test')['current_revision_id']=='CVR-new'
    assert postgres_store.get_record('viewpoint_revisions','CVR-old')==states['viewpoint_revisions/CVR-old']
    assert postgres_store.get_record('claims','CL-test')==states['claims/CL-test']
    relation=postgres_store.get_record('viewpoint_relations','REL-test')
    assert relation['validated_target_viewpoint_revision_id']=='CVR-new'
    assert relation['reason']==states['viewpoint_relations/REL-test']['reason']
    assert relation['review_provenance']['review_artifact_sha256']!='a'*64
    assert execute_source_action(postgres_store,run,manifest.model_dump(),root=tmp_path/'sql-action',ports=Ports())['status']=='already_applied'
    assert calls==['proposal','initial_review']
