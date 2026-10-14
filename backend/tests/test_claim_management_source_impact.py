"""Local source changes use the same read-only Review and SQL proof boundary."""
from copy import deepcopy
import json
from pathlib import Path
import uuid

import pytest

from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.source_reconciliation_runner import binding
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts, passing, seed_claims, operation_request
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres


def source_request(action, *, targets=('C','D'), batch_size=None, source_changes=None):
    store, original, _, _, root, _ = action
    prior = cm.Policy.model_validate_json(Path(original.policy.path).read_text())
    prior.review_purpose = 'source_impact'
    prior.source_impact_batch_size = batch_size
    policy = root / 'source-impact-policy.json'
    policy.write_text(prior.model_dump_json())
    old_file = root / 'preserved-before-source.json'
    old_file.write_bytes(Path(original.source_bindings['S'].path).read_bytes())
    current_file = Path(original.source_bindings['S'].path)
    current_file.write_text(json.dumps({'script': [{'text': '甲是假设，不是断言。'}, {'text': '但只在乙的语境成立。'}]}))
    source_doc = deepcopy(store.get_record_state('source_documents','S')['payload'])
    source_doc['source_file_sha256'] = binding(current_file)['file_sha256']
    source_doc.update(source_changes or {})
    applied = store.apply_plan(store.plan_package({'package_id': 'synthetic-source-change', 'source_documents': [source_doc]}))
    transition = root / 'source-transition.json'
    transition.write_text(cm.SourceTransition(before_sources={'S': binding(old_file)},
        source_change_set_ids={'S': applied['change_set_id']}).model_dump_json())
    authorization = root / 'source-impact-owner-direction.json'
    request = cm.Request(action_id='source-impact-' + uuid.uuid4().hex, operation='Review', allowed_operations=['Review'],
        target_keys=['claims/' + cid for cid in targets], source_bindings={'S': binding(current_file)},
        policy=binding(policy), source_transition=binding(transition),
        authorization={'path': str(authorization),'file_sha256': 'a'*64})
    direction = {'schema_version': cm.VERSION, 'authorized_by':'owner', 'authorization_quote':'Synthetic source impact Review only',
        'human_content_approval':False, 'request':request.model_dump(mode='json',exclude={'authorization','policy'}),
        'policy':request.policy.model_dump(mode='json')}
    authorization.write_text(json.dumps(direction))
    request.authorization = cm.source.FileBinding.model_validate(binding(authorization))
    return request, root / 'source-impact-root'


def impact_result(packet, *, maintenance=()):
    result = passing(packet)
    result['source_impacts'] = []
    for key in packet['denominator']:
        if not key.startswith('claims/'):
            continue
        affected = key in maintenance
        citations = [{'source_id':'S','paragraph_key':'S0002','exact_quote':'但只在乙的语境成立。'}]
        result['source_impacts'].append({'record_key':key,'conclusion':'maintenance' if affected else 'unaffected',
            'operation':'Update' if affected else None,'reason':'synthetic full before/current assessment', 'citations':citations})
        if affected:
            result['findings'].append({'target_key':key,'component':'limitation','category':'limitation',
                'citations':citations,'reason':'synthetic changed unanchored context limits original statement',
                'acceptance_condition':'preserve the new contextual limit'})
    return result


def test_unaffected_review_reuses_real_proof_but_does_not_upgrade_missing_review(action):
    store, original, _, _, root, _ = action
    assert execute_claim_management(store, original, root=root/'original-root',
        ports=SyntheticPorts(store,root))['status'] == 'completed'
    seed_claims(store,['D'])
    prior = {cid:store.get_record_state('claims',cid) for cid in ('C','D')}
    request, output = source_request(action)
    ports = SyntheticPorts(store,root,opus=impact_result,gemini=impact_result)
    result = execute_claim_management(store,request,root=output,ports=ports)
    assert result['status'] == 'completed', result
    assert result['source_impact_reviewed'] and not result['qualification_confirmed']
    assert result['source_revalidated_keys'] == ['claims/C']
    assert result['pending_content_review_keys'] == ['claims/D']
    assert ports.calls == ['opus','gemini']
    assert store.claim_management_qualification('C')['ready'] is True
    assert store.claim_management_qualification('D')['ready'] is False
    assert {cid:store.get_record_state('claims',cid) for cid in ('C','D')} == prior
    with store.connect() as conn:
        events=conn.execute("SELECT object_id,object_revision,decision FROM wang_knowledge.review_events WHERE decision='source_revalidated'").fetchall()
    assert events == [('C',prior['C']['revision'],'source_revalidated')]


def test_changed_other_paragraph_requires_update_despite_unchanged_quote(action):
    store, original, _, _, root, _ = action
    assert execute_claim_management(store,original,root=root/'original-root',ports=SyntheticPorts(store,root))['status']=='completed'
    seed_claims(store,['D'])
    request, output=source_request(action)
    ports=SyntheticPorts(store,root,opus=lambda p:impact_result(p,maintenance={'claims/C'}),
        gemini=lambda p:impact_result(p,maintenance={'claims/C'}))
    result=execute_claim_management(store,request,root=output,ports=ports)
    assert result['status']=='completed' and result['pending_maintenance'], result
    assert store.claim_management_qualification('C')['ready'] is False
    assert store.get_record_state('claims','C')['revision']==1


def test_source_review_cannot_omit_an_owner_without_old_approval(action):
    store, _, _, _, root, _=action
    seed_claims(store,['C','D'])
    request, output=source_request(action,targets=('C',))
    ports=SyntheticPorts(store,root,opus=impact_result,gemini=impact_result)
    with pytest.raises(ValueError,match='complete source/indirect owner population'):
        execute_claim_management(store,request,root=output,ports=ports)
    assert ports.calls==[]


def test_source_impact_disagreement_pauses_without_content_change_or_resend(action):
    store, _, _, _, root, _=action
    seed_claims(store,['C','D'])
    request, output=source_request(action)
    ports=SyntheticPorts(store,root,opus=impact_result,gemini=lambda p:impact_result(p,maintenance={'claims/C'}))
    result=execute_claim_management(store,request,root=output,ports=ports)
    assert result['status']=='paused' and result['reason']=='disagreement', result
    assert execute_claim_management(store,request,root=output,ports=ports)==result
    assert ports.calls==['opus','gemini']
    assert store.get_record_state('claims','C')['revision']==1


def test_duplicate_paragraphs_do_not_get_a_first_occurrence_mapping():
    def snapshot(texts,sha):
        return {'file':{'path':'/synthetic','file_sha256':sha*64},'body_sha256':sha*64,
            'paragraphs':{f'S{i:04d}':{'index':i,'text':text} for i,text in enumerate(texts,1)}}
    delta=cm.source_delta(snapshot(['重复。','重复。','乙。'],'a'),snapshot(['新增。','重复。','重复。','乙。'],'b'))
    assert delta['unique_exact_mapping']=={'S0003':'S0004'}
    assert any(item['kind']=='ambiguous' for item in delta['change_groups'])
    assert delta['semantic_approval'] is False


def test_deleted_original_quote_is_bound_to_before_epoch():
    sources={'S':{'paragraphs':{'S0001':{'text':'校订后的字句。'}},
        'before':{'paragraphs':{'S0001':{'text':'旧转录字句。'}}}}}
    cm.validate_citations([cm.Citation(source_id='S',paragraph_key='S0001',exact_quote='旧转录字句。',source_epoch='before')],sources)
    with pytest.raises(ValueError,match='exact quote'):
        cm.validate_citations([cm.Citation(source_id='S',paragraph_key='S0001',exact_quote='旧转录字句。')],sources)


def test_explicit_batches_share_one_budget_and_qualify_only_their_owned_targets(action):
    store, original, _, _, root, _ = action
    seed_claims(store,['C','D'])
    store.apply_plan(store.plan_package({'package_id':'overlapping-review-context','claim_relations':[
        {'claim_relation_id':'R','from_id':'C','to_id':'D','relation_type':'supports','reason':'synthetic related context'}]}))
    content_review, content_root = operation_request(action,'Review',['C','D'],confirm_current=True)
    assert execute_claim_management(store,content_review,root=content_root,ports=SyntheticPorts(store,root))['status']=='completed'
    request, output = source_request(action, batch_size=1)
    ports = SyntheticPorts(store,root,opus=impact_result,gemini=impact_result)
    result = execute_claim_management(store,request,root=output,ports=ports)
    assert result['status']=='completed', result
    assert ports.calls == ['opus','gemini','opus','gemini']
    parent = store.get_cvp_group('CMA-' + cm.sha256_json(request.action_id))
    assert len(parent['call_reservations']) == 4
    assert store.claim_management_qualification('C')['ready']
    assert store.claim_management_qualification('D')['ready']
    assert 'baselines' not in parent['original_goal']['source_transition_context']
    for rid in parent['latest_rounds'].values():
        child = store.get_cvp_group(rid)
        assert set(child['claim_ids']) == {'C','D'}  # actual overlap, not disjoint scopes
        assert set(child.get('source_revalidations', {})) == set(child['materials']['original_goal']['target_keys'])
        assert 'baselines' not in child['materials']['original_goal']['source_transition_context']


def test_unaffected_cannot_hide_a_finding_on_its_evidence_dependency():
    bound={'path':'/synthetic','file_sha256':'a'*64}
    request=cm.Request(action_id='synthetic-impact',operation='Review',allowed_operations=['Review'],
        target_keys=['claims/C'],source_bindings={'S':bound},authorization=bound,policy=bound)
    records={'claims/C':{'payload':{'claim_id':'C','evidence_step_ids':['E']},'retired':False},
        'evidence_steps/E':{'payload':{'evidence_step_id':'E','produced_claim_ids':['C'],'source_fragment_ids':['F']},'retired':False},
        'source_fragments/F':{'payload':{'fragment_id':'F','source_id':'S','paragraph_key':'S0001',
            'verbatim_excerpt':'旧字句。','source_sha256':'a'*64},'retired':False},
        'source_documents/S':{'payload':{'source_id':'S'},'retired':False}}
    citation=cm.Citation(source_id='S',paragraph_key='S0001',exact_quote='旧字句。')
    review=cm.Review(proposal_sha256='a'*64,verdicts=[],prior_findings=[],answered_questions=[],
        findings=[cm.Finding(target_key='source_fragments/F',component='anchor',category='mapping',
            citations=[citation],reason='synthetic obsolete anchor',acceptance_condition='current exact location')],
        source_impacts=[cm.SourceImpact(record_key='claims/C',conclusion='unaffected',reason='synthetic',citations=[citation])])
    sources={'S':{'body_sha256':'a'*64,'paragraphs':{'S0001':{'text':'旧字句。'}}}}
    with pytest.raises(ValueError,match='unresolved dependency finding'):
        cm.validate_source_impacts(review,records,sources,request=request,records=records,unresolved={})
    review.findings=[]
    sources['S']['body_sha256']='b'*64
    with pytest.raises(ValueError,match='current fragment/source binding'):
        cm.validate_source_impacts(review,records,sources,request=request,records=records,unresolved={})


def test_source_review_cannot_replace_source_identity_along_with_its_text(action):
    store, _, _, _, root, _ = action
    seed_claims(store,['C','D'])
    ports = SyntheticPorts(store,root,opus=impact_result,gemini=impact_result)
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStoreError
    with pytest.raises(PostgresKnowledgeStoreError,match='cannot change identity'):
        request, output = source_request(action, source_changes={'transcript_id':'different-sermon'})
        execute_claim_management(store,request,root=output,ports=ports)
    assert ports.calls == []


def test_historical_audit_consumes_its_frozen_schema_instead_of_new_runtime_schema(action,monkeypatch):
    from backend.pipeline.cvp_sql_actions import validate_claim_audit
    from backend.pipeline.source_reconciliation_runner import read, body
    store, request, _, _, root, _ = action
    assert execute_claim_management(store,request,root=root/'original-root',ports=SyntheticPorts(store,root))['status']=='completed'
    parent=store.get_cvp_group('CMA-' + cm.sha256_json(request.action_id))
    child=store.get_cvp_group(next(iter(parent['latest_rounds'].values())))
    receipt=body(read(Path(child['stage_results']['audit']['path'])))
    report=json.loads(Path(receipt['audit']['path']).read_text())
    monkeypatch.setattr(cm.Review,'model_json_schema',lambda **kwargs:{'synthetic_future_schema':True})
    passed,_,_=validate_claim_audit(report,child['materials'],cm.Candidate.model_validate(child['candidate']),historical=True)
    assert passed
