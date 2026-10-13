"""Fresh Evidence: existing Claim/source/SQL, never production model fixtures."""
from copy import deepcopy
import json

import pytest

from backend.pipeline import claim_evidence_intake as intake, claim_issue_management as m
from backend.pipeline import source_reconciliation as c, source_reconciliation_runner as r
from backend.tests.test_source_reconciliation import scenario, put
from backend.tests.test_claim_issue_management import (
    issue_setup, issue_for, ScopeStore, retain_proposal, IssuePorts,
)
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres, postgres_store
from backend.tests.test_cvp_sql_actions_postgres import source_run
from backend.pipeline.cvp_sql_actions import execute_source_action
from backend.api.canonical_repository.postgres_store import ChangeSetConflict


def intake_policy(tmp_path):
    return put(tmp_path/'intake-policy.json', m.Policy(
        authorization_id='fresh-evidence-test', authorization_quote='新增自身来源证据并完整复验',
        claim_fields=[], evidence_fields=[], link_fields=sorted(intake.LINK_FIELDS), evidence_intake=intake.VERSION).model_dump())


def setup(issue_setup, tmp_path):
    _, states, report, _ = issue_setup
    issue = issue_for(states, report)
    manifest = m.manifest_for_issues(ScopeStore(states), 'original-run', [issue],
                                   intake_policy(tmp_path), tmp_path/'scope')
    return c.freeze(manifest, states), issue


def draft(frozen, issue):
    p = retain_proposal(frozen, issue, 'evidence_binding')
    for d in p.dispositions:
        if c.split_key(d.record_key)[0] in c.IMPACT_ONLY:d.action='impact_only'
    before = c.index(frozen['before_records'])
    claim = deepcopy(before['claims/CL-test'])
    claim['evidence_step_ids'].append('E-fresh')
    if 'eligible_evidence_step_ids' in claim:
        claim['eligible_evidence_step_ids'].append('E-fresh')
    claim['revision'] += 1
    evidence = {'evidence_step_id':'E-fresh', 'revision':1, 'statement':'甲属于假设的论据。',
                'step_type':'explanation', 'speaker':'professor', 'stance':'asserted',
                'discourse_role':'supporting_argument', 'anchor_quality':'exact',
                'source_fragment_ids':['FR-test'], 'produced_claim_ids':['CL-test'],
                'support_eligibility':'eligible_with_label', 'valid_for_identity_review':True,
                'review_status':'candidate'}
    p.records = [c.Row(collection='claims',object_id='CL-test',payload=claim),
                 c.Row(collection='evidence_steps',object_id='E-fresh',payload=evidence)]
    next(d for d in p.dispositions if d.record_key=='claims/CL-test').action = 'update'
    p.dispositions.append(c.Disposition(record_key='evidence_steps/E-fresh',action='update',reason='新自身论据'))
    for k, old in before.items():
        if k.startswith('viewpoint_claim_links/') and old['claim_id']=='CL-test' and old['effective_state']=='active':
            link=deepcopy(old);link.update(revision=old['revision']+1,review_status='candidate',pinned_claim_revision=claim['revision'])
            link['component_locator']['claim_sha256']=c.semantic_record_sha(claim)
            p.records.append(c.Row(collection='viewpoint_claim_links',object_id=old['viewpoint_claim_link_id'],payload=link))
            next(d for d in p.dispositions if d.record_key==k).action='update'
            next(i for i in p.cvp_impacts if i.record_key==k).action='rebind'
    return p


def test_existing_policy_and_prompt_remain_opt_out(issue_setup,tmp_path):
    from backend.pipeline.source_reconciliation_runtime import model_adapter
    from backend.tests.test_claim_issue_management import materials
    _, old, issue = materials(issue_setup,tmp_path)
    base = model_adapter('proposal',issue=True)
    assert intake.scoped_adapter(base,old) is base
    proposal = draft(old,issue)
    with pytest.raises(ValueError):c.validate_proposal(old,proposal)


def test_append_and_complete_review_denominator(issue_setup,tmp_path):
    frozen, issue = setup(issue_setup,tmp_path)
    proposal = draft(frozen,issue)
    post = c.validate_proposal(frozen,proposal)
    assert post['claims/CL-test']['statement']==c.index(frozen['before_records'])['claims/CL-test']['statement']
    packet = c.review_packet(frozen,proposal)
    review = {'proposal_sha256':packet['proposal_sha256'],'verdicts':[
        {'record_key':d.record_key,'approved':True,'reason':'synthetic domain fixture','problems':[]}
        for d in proposal.dispositions]}
    assert c.validate_review(packet,review)
    review['verdicts'] = [v for v in review['verdicts'] if v['record_key']!='evidence_steps/E-fresh']
    with pytest.raises(ValueError):c.validate_review(packet,review)


@pytest.mark.parametrize('damage', ['remove_old','change_claim','change_old_evidence','foreign_owner',
    'no_owner','nonreciprocal','context_only','held','heading','foreign_fragment','uncited',
    'duplicate_id','source_change','missing_disposition'])
def test_intake_refuses_permission_ownership_source_and_hold_drift(issue_setup,tmp_path,damage):
    frozen,issue=setup(issue_setup,tmp_path);p=draft(frozen,issue)
    claim=p.records[0].payload;e=p.records[1].payload
    if damage=='remove_old':claim['evidence_step_ids']=['E-fresh']
    if damage=='change_claim':claim['statement']='甲是断言。'
    if damage=='change_old_evidence':
        old=deepcopy(c.index(frozen['before_records'])['evidence_steps/E-test']);old['support_eligibility']='eligible_with_label'
        p.records.append(c.Row(collection='evidence_steps',object_id='E-test',payload=old))
        next(d for d in p.dispositions if d.record_key=='evidence_steps/E-test').action='update'
    if damage=='foreign_owner':e['produced_claim_ids']=['CL-foreign']
    if damage=='no_owner':e['produced_claim_ids']=[]
    if damage=='nonreciprocal':claim['evidence_step_ids']=['E-test']
    if damage=='context_only':e['support_eligibility']='context_only'
    if damage=='held':e['review_status']='human_review_required'
    if damage=='heading':p.claim_assessments[0].citations[0].exact_quote='编辑标题'
    if damage=='foreign_fragment':e['source_fragment_ids']=['FR-foreign']
    if damage=='uncited':p.claim_assessments[0].citations=[]
    if damage=='duplicate_id':claim['evidence_step_ids'].append('E-fresh')
    if damage=='source_change':
        doc=deepcopy(c.index(frozen['before_records'])['source_documents/SRC-test']);doc['title']='改源'
        p.records.append(c.Row(collection='source_documents',object_id='SRC-test',payload=doc))
        p.dispositions.append(c.Disposition(record_key='source_documents/SRC-test',action='update',reason='不允许'))
    if damage=='missing_disposition':p.dispositions=[d for d in p.dispositions if d.record_key!='evidence_steps/E-fresh']
    with pytest.raises((ValueError,KeyError)):c.validate_proposal(frozen,p)


class IntakePorts(IssuePorts):
    def claim_issue_adapter(self,stage):
        adapter=super().claim_issue_adapter(stage)
        if 'review' not in stage:
            def generate(packet):
                self.calls.append(stage)
                if stage=='correction':return deepcopy(packet['initial_proposal'])
                p=draft({'before_records':packet['records'],'inventory':packet['inventory']},self.issue)
                return {**p.model_dump(exclude={'records','unadmitted_candidates'}),
                    'unadmitted_candidates_json':[], 'records':[
                        {'collection':x.collection,'object_id':x.object_id,'payload_json':json.dumps(x.payload,ensure_ascii=False)}
                        for x in p.records]}
            adapter.generate=generate
        return adapter


@pytest.mark.parametrize('late_failure',[False,True])
def test_real_common_sql_atomic_intake_cache_and_pending_completion(postgres_store,issue_setup,tmp_path,monkeypatch,late_failure):
    manifest,states,report,_=issue_setup
    run=source_run(postgres_store,(manifest,states,{}));issue=issue_for(states,report)
    postgres_store.register_claim_issue(run,issue.model_dump(mode='json'))
    scoped=m.manifest_for_issues(postgres_store,run,[issue],intake_policy(tmp_path),tmp_path/'scoped')
    ports=IntakePorts(issue)
    monkeypatch.setattr('backend.pipeline.cvp_sql_flow.verified_group_backup',lambda *args:{'dump':report,'restore_catalog_nonempty':True})
    old=deepcopy(postgres_store.get_record_state('claims','CL-test'))
    boundary=postgres_store._assert_transaction_edges_and_reviews
    if late_failure:
        def fail(*args):raise ValueError('last transaction validation')
        monkeypatch.setattr(postgres_store,'_assert_transaction_edges_and_reviews',fail)
        with pytest.raises(ValueError,match='last transaction'):
            execute_source_action(postgres_store,run,scoped.model_dump(),root=tmp_path/'action',ports=ports)
        assert postgres_store.get_record_state('claims','CL-test')==old
        assert postgres_store.get_record_state('evidence_steps','E-fresh') is None
        monkeypatch.setattr(postgres_store,'_assert_transaction_edges_and_reviews',boundary)
    result=execute_source_action(postgres_store,run,scoped.model_dump(),root=tmp_path/'action',ports=ports)
    assert result['status']=='applied'
    assert ports.calls==['proposal','initial_review']
    assert len(postgres_store.get_change_history(result['change_set_id'])['operations'])==2
    assert postgres_store.get_record('claims','CL-test')['evidence_step_ids']==['E-test','E-fresh']
    assert postgres_store.get_record('evidence_steps','E-fresh')['reviewed_by']=='claude-opus-5-5'
    assert postgres_store.get_record('evidence_steps','E-test')==states['evidence_steps/E-test']
    assert execute_source_action(postgres_store,run,scoped.model_dump(),root=tmp_path/'action',ports=ports)['status']=='already_applied'
    assert ports.calls==['proposal','initial_review']
    audit={'fixture':'not a provider audit'}
    with pytest.raises((ChangeSetConflict,ValueError),match='pending'):
        postgres_store.complete_cvp_group(run,audit=audit,audit_binding=put(tmp_path/'audit.json',audit),read_pins={})


def test_terminal_rejection_never_commits_or_retries(postgres_store,issue_setup,tmp_path,monkeypatch):
    manifest,states,report,_=issue_setup
    run=source_run(postgres_store,(manifest,states,{}));issue=issue_for(states,report)
    postgres_store.register_claim_issue(run,issue.model_dump(mode='json'))
    scoped=m.manifest_for_issues(postgres_store,run,[issue],intake_policy(tmp_path),tmp_path/'scoped')
    ports=IntakePorts(issue,refuse=True)
    for _ in range(2):
        with pytest.raises(r.Stop,match='terminal Opus'):
            execute_source_action(postgres_store,run,scoped.model_dump(),root=tmp_path/'action',ports=ports)
    assert ports.calls==['proposal','initial_review','correction','final_review']
    assert postgres_store.get_record_state('evidence_steps','E-fresh') is None
    assert not postgres_store.get_cvp_group(run)['change_set_ids']


@pytest.mark.parametrize('joint',[False,True])
def test_complete_claim_audit_handoff_and_real_sql_completion_boundary(postgres_store,issue_setup,tmp_path,monkeypatch,joint):
    """Synthetic provider results only exercise transaction domains, not approval."""
    from pathlib import Path
    from backend.pipeline import claim_first_management as first, viewpoint_content_blocked_fallback as fallback
    from backend.pipeline.cvp_sql_flow import pins,pin_rows,binding,write_immutable
    from backend.tests.test_source_reconciliation import add_viewpoint
    manifest,states,report,_=issue_setup
    if joint:
        synthetic_after=c.source_snapshot(manifest.current_sources[0])
        manifest.current_sources=[manifest.before_source];manifest.authorized_paragraph_keys=[]
        add_viewpoint((manifest,states,synthetic_after))
    run=source_run(postgres_store,(manifest,states,{}));issue=issue_for(states,report)
    postgres_store.register_claim_issue(run,issue.model_dump(mode='json'))
    scoped=m.manifest_for_issues(postgres_store,run,[issue],intake_policy(tmp_path),tmp_path/'scoped')
    ports=IntakePorts(issue)
    monkeypatch.setattr('backend.pipeline.cvp_sql_flow.verified_group_backup',lambda *args:{'dump':report,'restore_catalog_nonempty':True})
    execute_source_action(postgres_store,run,scoped.model_dump(),root=tmp_path/'action',ports=ports)
    saved=postgres_store.get_cvp_group(run)
    authority=intake.committed_context(saved,postgres_store)
    assert authority['joint_links_approved']==joint
    root=Path(saved['reviewed']['evidence']['package']['payload']['source_repair_application']['run_root'])
    material=r.body(r.read(root/'scoped-materials.json'))
    current={(row['collection'],row['object_id']):postgres_store.get_record_state(row['collection'],row['object_id'])
             for row in material['sql_scope']['read_pins']}
    for op in saved['reviewed']['plan']['operations']:
        current[op['collection'],op['object_id']]=postgres_store.get_record_state(op['collection'],op['object_id'])
    readset=pin_rows(pins(current))
    intent={'schema_version':first.VERSION,'claim_ids':saved['claim_ids'],'viewpoint_ids':[],
        'read_pins':readset,'change_set_ids':saved['change_set_ids'],'model':'gemini-3.8-flash',
        'workers':2,'layers':['1','2','3'],'automatic_retry':False}
    audit={'meta':{'model':'gemini-3.8-flash','registry_read_scope':{'kind':'sql_transaction_group',
        'complete_target_members':True,'claim_ids':['CL-test'],'viewpoint_ids':[],'read_pins':readset}},
        'layers':{'1':{'counts':{'pass':1},'findings':[]},'2':{},
                  '3':{'complete':True,'judged':1,'results':[{'claim_id':'CL-test','verdict':'supported'}]}}}
    write_immutable(root/'claim-first-audit/audit-intent.json',intent)
    write_immutable(root/'claim-first-audit/audit.json',audit)
    monkeypatch.setattr(fallback,'apply',lambda path,*a:path)
    monkeypatch.setattr(fallback,'verify_saved_full_audit',lambda *a,**kw:[])
    receipt=first.finish_claim_audit(postgres_store,run,root)
    assert receipt['group_complete'] is False
    saved=postgres_store.get_cvp_group(run)
    handoff=first.coverage_context(postgres_store,saved,{'claim_ids':saved['claim_ids']})
    assert handoff['existing_cvp_content_approved'] is False
    assert receipt['read_pins']==readset and any(p['object_id']=='E-fresh' for p in readset)
    full=deepcopy(audit)
    full['meta']['registry_read_scope']['viewpoint_ids']=['CV-test'] if joint else []
    full['layers']['4']={'complete':True,'judged':int(joint),
        'results':[{'viewpoint_id':'CV-test','verdict':'same'}] if joint else []}
    path=tmp_path/'full-transaction-domain-audit.json';write_immutable(path,full)
    kwargs={'audit':full,'audit_binding':binding(path),'read_pins':pins(current)}
    if not joint:
        with pytest.raises(ChangeSetConflict,match='pending'):postgres_store.complete_cvp_group(run,**kwargs)
        assert postgres_store.get_cvp_group(run)['checkpoint']=='applied'
        return
    # Missing new Evidence pin is NOT full current-generation Claim approval.
    bad=deepcopy(receipt);bad['read_pins']=[p for p in readset if p['object_id']!='E-fresh']
    proof=put(tmp_path/'missing-new-evidence.json',bad)
    corrupted=deepcopy(saved);corrupted['claim_layer_audit']['receipt']=proof
    with postgres_store.connect() as conn,conn.cursor() as cursor,pytest.raises(ValueError):
        intake.validate_completion(corrupted,cursor)
    original=postgres_store._assert_claim_issue_committed
    def late(*a,**kw):raise ValueError('late completion validation')
    monkeypatch.setattr(postgres_store,'_assert_claim_issue_committed',late)
    with pytest.raises(ValueError,match='late completion'):postgres_store.complete_cvp_group(run,**kwargs)
    assert postgres_store.get_cvp_group(run)==saved
    monkeypatch.setattr(postgres_store,'_assert_claim_issue_committed',original)
    postgres_store.complete_cvp_group(run,**kwargs)
    assert postgres_store.get_cvp_group(run)['checkpoint']=='audited'
    postgres_store.complete_cvp_group(run,**kwargs)
    assert ports.calls==['proposal','initial_review']


@pytest.mark.parametrize('damage',[None,'missing_owner_assessment','missing_append','held_owner'])
def test_shared_evidence_requires_all_existing_owners(issue_setup,tmp_path,damage):
    _,states,report,_=issue_setup
    other=deepcopy(states['claims/CL-test']);other['claim_id']='CL-other'
    states['claims/CL-other']=other
    states['evidence_steps/E-test']['produced_claim_ids'].append('CL-other')
    source=states['source_documents/SRC-test'];ev=[states['evidence_steps/E-test']]
    issues=[m.make_issue(states['claims/'+cid],ev,source,
        spans=[(0,len(other['statement']),other['statement'])],origin='owner',report=report,
        reason='synthetic shared intake',fragments=[states['source_fragments/FR-test']])
        for cid in ['CL-test','CL-other']]
    class SharedStore(ScopeStore):
        def get_cvp_group(self,run):return {'claim_ids':['CL-test','CL-other'],'campaign_sha256':'test'}
        def get_cvp_campaign(self,ident):return {'groups':[{'group_key':'g1','claim_ids':['CL-test','CL-other']}]}
    manifest=m.manifest_for_issues(SharedStore(states),'original-run',issues,intake_policy(tmp_path),tmp_path/'scope')
    frozen=c.freeze(manifest,states);p=draft(frozen,issues[0])
    claim=deepcopy(other);claim['evidence_step_ids'].append('E-fresh');claim['revision']+=1
    p.records.append(c.Row(collection='claims',object_id='CL-other',payload=claim))
    next(d for d in p.dispositions if d.record_key=='claims/CL-other').action='update'
    p.records[1].payload['produced_claim_ids'].append('CL-other')
    a=deepcopy(p.claim_assessments[0]);a.issue_id=issues[1].issue_id;p.claim_assessments.append(a)
    if damage=='missing_owner_assessment':p.claim_assessments.pop()
    if damage=='missing_append':p.records[-1].payload['evidence_step_ids']=['E-test']
    if damage=='held_owner':
        next(r['payload'] for r in frozen['before_records'] if r['object_id']=='CL-other')['review_status']='human_hold'
    if damage:
        with pytest.raises(ValueError):c.validate_proposal(frozen,p)
    else:assert c.validate_proposal(frozen,p)['claims/CL-other']['evidence_step_ids']==['E-test','E-fresh']


def test_new_fragment_and_duplicate_basis_guard(issue_setup,tmp_path):
    frozen,issue=setup(issue_setup,tmp_path);p=draft(frozen,issue)
    fragment=deepcopy(c.index(frozen['before_records'])['source_fragments/FR-test'])
    fragment.update(fragment_id='FR-fresh',revision=1,review_status='candidate')
    p.records.append(c.Row(collection='source_fragments',object_id='FR-fresh',payload=fragment))
    p.dispositions.append(c.Disposition(record_key='source_fragments/FR-fresh',action='update',reason='精确自身片段'))
    p.records[1].payload['source_fragment_ids']=['FR-fresh']
    assert c.validate_proposal(frozen,p)['evidence_steps/E-fresh']['source_fragment_ids']==['FR-fresh']
    p.records[1].payload['statement']=c.index(frozen['before_records'])['evidence_steps/E-test']['statement']
    with pytest.raises(ValueError,match='duplicate existing'):c.validate_proposal(frozen,p)


@pytest.mark.parametrize('stage',['proposal','initial_review','correction','final_review'])
def test_intake_prompt_has_distinct_identity_original_adapter_immutable(issue_setup,tmp_path,stage):
    from backend.pipeline.source_reconciliation_runtime import model_adapter
    frozen,_=setup(issue_setup,tmp_path);old=model_adapter(stage,issue=True)
    previous=old.prompt_sha256;new=intake.scoped_adapter(old,frozen)
    assert new.prompt_sha256!=previous and old.prompt_sha256==previous
    assert (new._client.model,new._client.backend)==(old._client.model,old._client.backend)
    assert intake.PROMPT in new._prompt


def test_actual_saved_policy_and_whole_campaign_immutable(tmp_path,monkeypatch):
    import os
    if os.getenv('WKP357_EVIDENCE_INTAKE_ACTUAL')!='1':pytest.skip('explicit SELECT-only saved production check')
    from dotenv import load_dotenv
    from pathlib import Path
    from backend.tests.test_statement_impact_completion import ReadOnly
    from backend.pipeline.cvp_sql_actions import validate_source_package
    load_dotenv('.env');store=ReadOnly()
    campaign='a155c59979a3a1fe65fccd3368c9abca8ea0f1d4238b1624f4a7cfdbdc522ced'
    before=deepcopy(store.cvp_campaign_progress(campaign))
    value=store.get_cvp_group('CVPG-a85c3d6eb392f97e945d88a18fbf33a76ed136eefefbb4554759e82e0e26deb2')
    from backend.pipeline.cvp_source_impact_resolution import applied_review
    # Exact historical source approval, not the later merge/new intake authority.
    history=store.get_change_history('KCS-7582009328b076487938')
    reviewed=applied_review(store,value,history['metadata']['reviewed_sha256'])
    package=reviewed['evidence']['package']['payload']
    material=package['source_repair_application']['materials']
    frozen=r.body(r.read(Path(material['path'])))
    originals={Path(material['path']):c.file_sha256(Path(material['path']))}
    root=Path(package['source_repair_application']['run_root'])
    originals.update({p:c.file_sha256(p) for p in root.iterdir() if p.is_file()})
    def forbidden(*a,**kw):raise AssertionError('saved test cannot call model or write file/SQL')
    monkeypatch.setattr(r,'write',forbidden)
    assert not intake.enabled(frozen)
    validate_source_package(package)
    assert store.cvp_campaign_progress(campaign)==before
    assert len(before)==156 and all(v['checkpoint']=='audited' and v['status']=='succeeded' for v in before)
    assert all(c.file_sha256(p)==sha for p,sha in originals.items())


def test_actual_cli_source_dispatch_audits_intake_then_exits_partial(tmp_path,monkeypatch):
    import ast,inspect
    from pathlib import Path
    from types import SimpleNamespace as NS
    from backend.pipeline import cvp_sql_flow as flow,cvp_sql_actions as actions,claim_first_management as first
    tree=ast.parse(inspect.getsource(flow.main))
    branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=="args.action == 'source'")
    fn=ast.FunctionDef(name='dispatch',args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),
                       body=branch.body,decorator_list=[])
    module=ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[]))
    root=tmp_path/'source/CVPA-test';calls=[]
    state={'reviewed':{'evidence':{'package':{'payload':{'source_repair_application':{'run_root':str(root)}}}}}}
    monkeypatch.setattr(actions,'read_source_manifest',lambda path:{'fixture':'registered scoped intake'})
    monkeypatch.setattr(intake,'committed_context',lambda *a,**k:{'group_complete':False})
    monkeypatch.setattr(first,'finish_claim_audit',lambda store,rid,path:calls.append(('audit',path)) or {'group_complete':False})
    env={'args':NS(manifest=tmp_path/'manifest',source_permission_recovery_authorization=None,claim_first=False,run_id='run'),
         'action_root':tmp_path,'store':NS(get_cvp_group=lambda rid:state),'ProductionPorts':lambda:None,
         'execute_source_action':lambda *a,**kw:calls.append(('source',kw['root'])),
         'stage_event':lambda *a,**kw:calls.append(('event',a[0],kw['group_complete'])),'Path':Path}
    exec(compile(module,'<actual source dispatch>','exec'),env)
    assert env['dispatch']()==0
    assert calls==[('source',tmp_path/'source'),('audit',root),
                   ('event','evidence_intake_claim_layer_audited_cvp_pending',False)]
