"""Business contract counterexamples, no provider calls or production objects."""
from copy import deepcopy

import pytest

from backend.pipeline import claim_management as m
from backend.pipeline import source_reconciliation as c


def policy(**limits):
    return m.Policy(policy_id="test-explicit-budget", budget=m.Budget(
        max_revisions=3, gpt_calls=4, opus_calls=8, gemini_calls=8,
        max_request_bytes=500000, max_output_tokens=64000, **limits),
        models={r: {"model_id": v[0], "backend": v[1], "effort": "high"}
                for r, v in m.MODELS.items()})


def test_audit_model_selection_preserves_flash_and_explicitly_binds_argon():
    from pathlib import Path
    old = policy()
    assert old.models['gemini']['model_id'] == 'gemini-3.8-flash'
    path = Path(m.__file__).parent / 'policies/wang_claim_management_argon_v1.json'
    current = m.Policy.model_validate_json(path.read_text())
    assert current.models['gemini']['model_id'] == 'gemini-4-argon'
    assert current.budget.max_output_tokens <= 32768
    for field, bad in [('model_id', 'gemini-pro-latest'), ('backend', 'codex_subscription'), ('effort', 'low')]:
        altered = current.model_dump(mode='json')
        altered['models']['gemini'][field] = bad
        with pytest.raises(ValueError, match='exact model'):
            m.Policy.model_validate(altered)


def test_opus_only_policy_is_explicit_and_does_not_change_old_policy_serialization(case):
    old=policy();saved=old.model_dump(mode='json')
    assert 'independent_audit' not in saved
    assert m.Policy.model_validate(saved).model_dump(mode='json')==saved
    new=m.Policy.model_validate_json(m.DEFAULT_POLICY.read_text())
    assert not new.independent_audit_required and new.budget.gemini_calls==0
    request=case[0]
    assert m.required_roles(request,0,new)==['opus']
    assert m.required_roles(request,1,new)==['gpt','opus']
    assert m.required_roles(request,0,old)==['opus','gemini']


@pytest.fixture
def case():
    sources = {"S": {"paragraphs": {"S0001": {"text": "甲是假设。乙只在特定情况下成立。"}}}}
    payload = {"claim_id": "C", "statement": "甲是假设。", "claim_type": "exegesis",
               "occurrences": [{"source_id": "S", "anchors": []}],
               "evidence_step_ids": ["E"], "revision": 1, "review_status": "candidate"}
    before = {"claims/C": {"payload": payload, "retired": False},
              "evidence_steps/E": {"payload": {"evidence_step_id": "E", "statement": "甲是假设。",
                   "produced_claim_ids": ["C"], "source_fragment_ids": ["F"]}, "retired": False},
              "source_fragments/F": {"payload": {"fragment_id": "F", "source_id": "S",
                   "paragraph_key": "S0001", "verbatim_excerpt": "甲是假设。"}, "retired": False}}
    binding = {"path": "/explicit-test-binding", "file_sha256": "a" * 64}
    request = m.Request(action_id="owner-test-action", operation="Update", allowed_operations=["Update"],
        target_keys=["claims/C"], source_bindings={"S": binding}, authorization=binding, policy=binding,
        permissions=[c.Permission(collection="claims", object_id="C", fields=["statement", "occurrences"])])
    candidate = m.Candidate(records=[], dispositions=[
        m.ObjectDisposition(record_key=k, action="retain", reason="逐项保留") for k in before])
    return request, before, candidate, sources


def citation(source_id="S", quote="甲是假设。"):
    return m.Citation(source_id=source_id, paragraph_key="S0001", exact_quote=quote)


def change(candidate, key, action, payload=None):
    result = candidate.model_copy(deep=True)
    result.dispositions = [d for d in result.dispositions if d.record_key != key]
    result.dispositions.append(m.ObjectDisposition(record_key=key, action=action,
        reason="完整自身来源支持的处置", citations=[citation()]))
    if payload is not None:
        result.records.append(c.Row(collection=key.split("/", 1)[0], object_id=key.split("/", 1)[1], payload=payload))
    return result


def test_existing_candidate_keeps_exact_content_without_invented_gpt_origin(case):
    request, before, candidate, sources = case
    post, writes = m.validate_candidate(request, before, candidate, sources)
    assert post == {k: v["payload"] for k, v in before.items()}
    assert writes == {}
    assert "generated_by" not in post["claims/C"]


def test_occurrence_transcript_alias_resolves_to_the_same_registered_fragment_source(case):
    _, before, _, _ = case
    records = {key: deepcopy(state['payload']) for key, state in before.items()}
    records['source_documents/S'] = {'source_id':'S','transcript_id':'lecture-name'}
    records['claims/C']['occurrences'] = [{'source_id':'lecture-name','transcript_id':'lecture-name'}]
    assert m.own_source_ids(records['claims/C'],records) == {'S'}
    records['source_documents/OTHER'] = {'source_id':'OTHER','transcript_id':'lecture-name'}
    with pytest.raises(ValueError,match='alias is ambiguous'):
        m.own_source_ids(records['claims/C'],records)


def test_opposed_position_is_in_the_review_packet_but_does_not_change_claim_own_source(case):
    request, before, _, _ = case
    before['claims/C']['payload']['opposed_position_ids'] = ['P']
    before['position_nodes/P'] = {'payload':{'position_id':'P','source_id':'OTHER-SOURCE'},'retired':False}
    for sid in ('S','OTHER-SOURCE'):
        before['source_documents/'+sid] = {'payload':{'source_id':sid},'retired':False}
    records={key:state['payload'] for key,state in before.items()}
    assert m.own_source_ids(records['claims/C'],records) == {'S'}
    assert 'position_nodes/P' in m.audit_keys(request,before)
    del before['position_nodes/P']
    with pytest.raises(ValueError,match='missing dependency'):
        m.audit_keys(request,before)


def test_update_includes_complete_dependency_denominator(case):
    request, before, candidate, sources = case
    payload = {**before["claims/C"]["payload"], "statement": "甲属于假设。", "revision": 2}
    candidate = change(candidate, "claims/C", "update", payload)
    post, writes = m.validate_candidate(request, before, candidate, sources)
    assert post["claims/C"]["statement"] == payload["statement"]
    assert set(writes) == {"claims/C"}
    candidate.dispositions.pop(0)
    with pytest.raises(ValueError, match="dependency disposition"):
        m.validate_candidate(request, before, candidate, sources)


@pytest.mark.parametrize("operation", ["Read", "Review"])
def test_read_and_review_are_independent_operations_without_write_authority(case, operation):
    request, before, candidate, sources = case
    request = request.model_copy(update={"operation": operation, "allowed_operations": [operation], "permissions": []})
    assert m.validate_candidate(request, before, candidate, sources)[1] == {}
    candidate = change(candidate, "claims/C", "update", before["claims/C"]["payload"])
    with pytest.raises(ValueError, match="not permission"):
        m.validate_candidate(request, before, candidate, sources)


def test_shared_read_only_owner_cannot_become_writable(case):
    request, before, candidate, sources = case
    before["claims/OTHER"] = {"payload": {**before["claims/C"]["payload"], "claim_id": "OTHER"}, "retired": False}
    candidate.dispositions.append(m.ObjectDisposition(record_key="claims/OTHER", action="retain", reason="共享 owner 只读"))
    candidate = change(candidate, "claims/OTHER", "update", {**before["claims/OTHER"]["payload"], "statement": "无许可的变更"})
    with pytest.raises(ValueError, match="read-only owner"):
        m.validate_candidate(request, before, candidate, sources)


@pytest.mark.parametrize("field,value", [("claim_type", "new-type"), ("attribution", "professor")])
def test_update_cannot_expand_field_permission(case, field, value):
    request, before, candidate, sources = case
    candidate = change(candidate, "claims/C", "update", {**before["claims/C"]["payload"], field: value})
    with pytest.raises(ValueError, match="field permission"):
        m.validate_candidate(request, before, candidate, sources)


@pytest.mark.parametrize("hold", sorted(m.HELD))
def test_protected_hold_stops_a_content_change(case, hold):
    request, before, candidate, sources = case
    before["claims/C"]["payload"]["review_status"] = hold
    candidate = change(candidate, "claims/C", "update", {**before["claims/C"]["payload"], "statement": "甲属于假设。"})
    with pytest.raises(ValueError, match="protected"):
        m.validate_candidate(request, before, candidate, sources)


def test_retirement_preserves_history_and_restore_requires_retired_state(case):
    request, before, candidate, sources = case
    request = request.model_copy(update={"operation": "Retire", "allowed_operations": ["Retire", "Restore"]})
    retired = change(candidate, "claims/C", "retire")
    post, writes = m.validate_candidate(request, before, retired, sources)
    assert post["claims/C"] == before["claims/C"]["payload"]
    assert writes["claims/C"]["payload"] == before["claims/C"]["payload"]
    restored = change(candidate, "claims/C", "restore", before["claims/C"]["payload"])
    with pytest.raises(ValueError, match="retired current state"):
        m.validate_candidate(request, before, restored, sources)
    before["claims/C"]["retired"] = True
    assert m.validate_candidate(request, before, restored, sources)[1]["claims/C"]["action"] == "restore"


def test_split_requires_every_original_and_new_target_mapping(case):
    request, before, candidate, sources = case
    request = request.model_copy(update={"operation": "Split", "allowed_operations": ["Split"],
        "creation_permissions": [m.CreationPermission(collection="claims", source_id="S",
             fields=["statement", "claim_type", "occurrences", "evidence_step_ids"])]})
    candidate = change(candidate, "claims/C", "retire")
    for cid in ["A", "B"]:
        candidate = change(candidate, "claims/" + cid, "create", {**before["claims/C"]["payload"], "claim_id": cid})
    candidate.replacements = {"claims/C": ["claims/A", "claims/B"]}
    assert set(m.validate_candidate(request, before, candidate, sources)[1]) == {"claims/C", "claims/A", "claims/B"}
    candidate.replacements["claims/C"].pop()
    with pytest.raises(ValueError, match="replacement mapping"):
        m.validate_candidate(request, before, candidate, sources)


def test_create_needs_source_scoped_permission_and_absent_identity(case):
    request, before, candidate, sources = case
    request = request.model_copy(update={"operation": "Create", "allowed_operations": ["Create"],
        "creation_permissions": [m.CreationPermission(collection="claims", source_id="S",
             fields=["statement", "claim_type", "occurrences", "evidence_step_ids"])]})
    created = change(candidate, "claims/NEW", "create", {**before["claims/C"]["payload"], "claim_id": "NEW"})
    assert m.validate_candidate(request, before, created, sources)[1]["claims/NEW"]["action"] == "create"
    occupied = {**before, "claims/NEW": {"payload": created.records[0].payload, "retired": False}}
    with pytest.raises(ValueError, match="absent identity"):
        m.validate_candidate(request, occupied, created, sources)


def test_creating_evidence_resolves_its_own_source_from_fragment(case):
    request, before, candidate, sources = case
    request = request.model_copy(update={"allowed_operations": ["Update", "Create"],
        "creation_permissions": [m.CreationPermission(collection="evidence_steps", source_id="S",
             fields=["statement", "produced_claim_ids", "source_fragment_ids"])]})
    created = change(candidate, "evidence_steps/NEW", "create", {
        "evidence_step_id": "NEW", "statement": "甲是假设。", "produced_claim_ids": ["C"], "source_fragment_ids": ["F"]})
    assert m.validate_candidate(request, before, created, sources)[1]["evidence_steps/NEW"]["action"] == "create"


def test_relation_source_id_is_a_claim_endpoint_not_a_source_document(case):
    request, before, candidate, sources = case
    second = {**before['claims/C']['payload'], 'claim_id': 'D'}
    before['claims/D'] = {'payload': second, 'retired': False}
    candidate.dispositions.append(m.ObjectDisposition(record_key='claims/D', action='retain', reason='endpoint'))
    relation = {'constraint_id': 'R', 'source_id': 'C', 'target_id': 'D', 'reason': 'same source context'}
    request = request.model_copy(update={'allowed_operations': ['Update', 'Create'],
        'creation_permissions': [m.CreationPermission(collection='claim_relation_constraints', source_id='S',
            fields=['source_id', 'target_id', 'reason'])]})
    candidate = change(candidate, 'claim_relation_constraints/R', 'create', relation)
    assert m.own_source_ids(relation, {k: v['payload'] for k, v in before.items()}) == {'S'}
    assert m.validate_candidate(request, before, candidate, sources)[1]['claim_relation_constraints/R']['action'] == 'create'


def test_observation_relation_audit_includes_fragment_and_evidence(case):
    request, before, _, _ = case
    before['source_documents/S'] = {'payload': {'source_id': 'S'}, 'retired': False}
    before['observations/O'] = {'payload': {'observation_id': 'O', 'source_fragment_id': 'F'}, 'retired': False}
    before['knowledge_relations/R'] = {'payload': {'relation_id': 'R', 'from_id': 'O', 'to_id': 'E'}, 'retired': False}
    request = request.model_copy(update={'permissions': [c.Permission(collection='knowledge_relations', object_id='R', fields=['reason'])]})
    assert m.own_source_ids(before['knowledge_relations/R']['payload'], {k: v['payload'] for k, v in before.items()}) == {'S'}
    assert set(m.audit_keys(request, before)) == set(before)


def test_relation_cycles_cannot_supply_missing_source_ownership():
    records = {'position_nodes/P': {'position_id': 'P'},
               'knowledge_relations/R': {'relation_id': 'R', 'from_id': 'P', 'to_id': 'P'}}
    assert m.own_source_ids(records['position_nodes/P'], records) == set()


@pytest.mark.parametrize("cit", [citation("foreign"), citation(quote="源文里没有")])
def test_review_cannot_borrow_other_source_or_nonexistent_quote(case, cit):
    _, _, _, sources = case
    with pytest.raises(ValueError, match="exact quote"):
        m.validate_citations([cit], sources)


def test_same_problem_identity_survives_reason_and_candidate_changes():
    finding = m.Finding(target_key="claims/C", component="judgment", category="limitation",
        citations=[citation()], reason="少了限定", acceptance_condition="保留限定")
    revised = finding.model_copy(update={"reason": "新一轮表达", "acceptance_condition": "同一实质要求"})
    assert revised.identity("one-action") == finding.identity("one-action")


def reviewed(candidate, keys, questions, prior=None, **extra):
    return m.Review(proposal_sha256=candidate.digest, verdicts=[
        c.Verdict(record_key=k, approved=True, problems=[], reason="完整原文审核") for k in keys],
        findings=[], prior_findings=prior or [], answered_questions=sorted(questions), **extra)


def test_review_is_bound_to_candidate_original_questions_and_every_old_problem(case):
    _, before, candidate, sources = case
    questions = m.QUESTIONS["Split"] | m.QUESTIONS["Update"]
    review = reviewed(candidate, before, questions)
    assert m.validate_review(candidate, review, before, questions, {}, sources)
    review.answered_questions.remove("original_complete_mapping")
    with pytest.raises(ValueError, match="original operation"):
        m.validate_review(candidate, review, before, questions, {}, sources)
    review = reviewed(candidate, before, questions)
    with pytest.raises(ValueError, match="every prior"):
        m.validate_review(candidate, review, before, questions, {"old-finding": {}}, sources)
    review.proposal_sha256 = "a" * 64
    with pytest.raises(ValueError, match="different complete candidate"):
        m.validate_review(candidate, review, before, questions, {}, sources)


def test_partial_review_cannot_become_complete(case):
    _, before, candidate, sources = case
    review = reviewed(candidate, before, m.QUESTIONS["Update"])
    review.verdicts.pop()
    with pytest.raises(ValueError, match="denominator"):
        m.validate_review(candidate, review, before, m.QUESTIONS["Update"], {}, sources)


def test_mechanical_provenance_cannot_manufacture_revision_progress(case):
    _, before, candidate, _ = case
    original = change(candidate, "claims/C", "update", before["claims/C"]["payload"])
    altered = original.model_copy(deep=True)
    altered.records[0].payload["review_note"] = "changed mechanical metadata"
    assert m.candidate_state(before, original) == m.candidate_state(before, altered)


def test_candidate_transport_schema_is_closed_and_roundtrips_full_payload(case):
    import json
    from backend.pipeline.source_reconciliation_runtime import claim_management_model_adapter
    _, before, candidate, sources = case
    adapter = claim_management_model_adapter('gpt', policy())
    payload = deepcopy(before['claims/C']['payload'])
    payload['extension'] = {'nested': [{'literal': '引文\\n', 'null': None}], '未知欄位': 42}
    wire = {'records': [{'collection': 'claims', 'object_id': 'C', 'payload_json': json.dumps(payload)}],
            'dispositions': [d.model_dump(mode='json') for d in candidate.dispositions],
            'replacements_json': '{}', 'findings': [], 'operation': 'Update'}
    def provider(**request):
        schema = request['json_schema']['schema']
        def closed(node):
            if isinstance(node, dict):
                if node.get('type') == 'object':
                    assert 'properties' in node, node
                    assert node['additionalProperties'] is False
                    assert set(node['required']) == set(node['properties'])
                for value in node.values(): closed(value)
            elif isinstance(node, list):
                for value in node: closed(value)
        closed(schema)
        return wire
    adapter._client.generate_json = provider
    parsed, _ = m.normalize_candidate(adapter.generate({'before': before}), sources)
    assert parsed.records[0].payload == payload
    assert parsed.replacements == {}
    for field, bad in [('replacements_json', '{"x":[],"x":[]}')]:
        with pytest.raises(ValueError, match='duplicate Candidate'):
            m.normalize_candidate({**wire, field: bad}, sources)
    wire['records'][0]['payload_json'] = '{"claim_id":"C","claim_id":"D"}'
    with pytest.raises(ValueError, match='duplicate Candidate'):
        m.normalize_candidate(wire, sources)


def test_readonly_finding_requires_reviewers_explicit_target_impact(case):
    request, before, candidate, sources = case
    def review(affected):
        finding = m.Finding(target_key='evidence_steps/E', component='statement', category='source_uncertain',
            citations=[citation()], reason='context wording is uncertain', acceptance_condition='check source',
            human_question='which meaning?', affected_target_keys=affected,
            impact_reason='target uses an independent clear part' if affected == [] else 'target depends on this wording')
        return m.Review(proposal_sha256=candidate.digest,
            verdicts=[c.Verdict(record_key=k, approved=k!='evidence_steps/E', problems=[] if k!='evidence_steps/E' else ['uncertain'],
                reason='specific judgment') for k in before], findings=[finding], prior_findings=[], answered_questions=['own_source'])
    contextual = review([])
    assert m.validate_review(candidate, contextual, before, ['own_source'], {}, sources, request=request)
    assert m.unresolved_after('A', {}, contextual, request=request, candidate=candidate) == {}
    for affected in (None, ['claims/C']):
        blocking = review(affected)
        assert not m.validate_review(candidate, blocking, before, ['own_source'], {}, sources, request=request)
        assert m.failure_reason(m.unresolved_after('A', {}, blocking, request=request, candidate=candidate)) == 'human_required'
    finding = contextual.findings[0]
    finding.target_key = 'claims/C'
    with pytest.raises(ValueError, match='cannot be dismissed'):
        m.validate_review(candidate, contextual, before, ['own_source'], {}, sources, request=request)
    finding.target_key = 'evidence_steps/E'; finding.impact_reason = None
    with pytest.raises(ValueError, match='impact and explanation'):
        m.validate_review(candidate, contextual, before, ['own_source'], {}, sources, request=request)


def test_persistent_unrelated_prior_finding_is_disclosed_not_falsely_resolved(case):
    request,before,candidate,sources=case
    finding=m.Finding(target_key='evidence_steps/E',component='statement',category='source_uncertain',
        citations=[citation()],reason='ambiguous independent clause',acceptance_condition='check source',human_question='which word?')
    unresolved={finding.identity('original'):finding.model_dump(mode='json')}
    resolution=m.FindingResolution(finding_id=next(iter(unresolved)),disposition='persistent',
        reason='ambiguity persists without affecting target',citations=[citation()],affected_target_keys=[],
        impact_reason='C uses the independent clear clause')
    review=m.Review(proposal_sha256=candidate.digest,verdicts=[c.Verdict(record_key=k,approved=k!='evidence_steps/E',
        problems=[] if k!='evidence_steps/E' else ['ambiguity persists'],reason='specific judgment') for k in before],
        findings=[],prior_findings=[resolution],answered_questions=['own_source'])
    assert m.validate_review(candidate,review,before,['own_source'],unresolved,sources,request=request)
    assert m.unresolved_after('continued',unresolved,review,request=request,candidate=candidate)=={}
    assert m.resolution_finding(resolution,unresolved)['human_question']=='which word?'
    resolution.affected_target_keys=['claims/C']
    assert not m.validate_review(candidate,review,before,['own_source'],unresolved,sources,request=request)
    assert m.unresolved_after('continued',unresolved,review,request=request,candidate=candidate)


def test_single_record_table_packet_preserves_current_and_original_facts(case):
    request,before,_,sources=case
    goal={'operation':'Update','target_keys':request.target_keys,'required_questions':['own_source'],'original_objects':deepcopy(before)}
    older=deepcopy(goal);older['original_objects']['claims/C']['payload']['statement']='原来旧的假设。'
    materials={'packet_profile':'claim_single_record_table_v1','before':before,'request':request.model_dump(mode='json'),
        'original_goal':goal,'sources':sources,'continuation':{'original_goal':older,'prior_call_reservations':{
            'old:opus':{'role':'opus','round_id':'old','state':'completed','intent':{'large_execution_proof':'not a content fact'}}}}}
    packet=m.model_packet(materials)
    assert packet['sources']==sources and packet['before']==before
    assert packet['original_goal']['original_objects']['claims/C']=={'__claim_record_ref__':'claims/C'}
    assert packet['continuation']['original_goal']['original_objects']['claims/C']==older['original_objects']['claims/C']
    assert packet['continuation']['prior_call_reservations']['old:opus']=={'role':'opus','round_id':'old','state':'completed'}
    assert materials['original_goal']['original_objects']==before
    reduced=policy();reduced.initial_candidate='existing';request.predecessor_action_id='old'
    assert m.required_roles(request,0,reduced)==['opus','gemini']
    assert m.required_roles(request,1,reduced)==['gpt','opus','gemini']
    assert m.required_roles(request,0,policy())==['gpt','opus','gemini']


def test_one_finding_cannot_explain_another_rejected_object_by_prose(case):
    request,before,candidate,sources=case
    findings=[m.Finding(target_key='evidence_steps/E',component='E and F',category='content',
        reason='Both E and F have this issue',citations=[citation()],acceptance_condition='Fix both',
        affected_target_keys=['claims/C'],impact_reason='This evidence supports C')]
    review=m.Review(proposal_sha256=candidate.digest,answered_questions=list(m.QUESTIONS['Update']),
        verdicts=[c.Verdict(record_key=k,approved=k=='claims/C',reason='explicit review',
            problems=[] if k=='claims/C' else ['same issue']) for k in before],findings=findings,prior_findings=[])
    with pytest.raises(ValueError,match='findings: source_fragments/F'):
        m.validate_review(candidate,review,before,m.QUESTIONS['Update'],{},sources,request=request)
    review.findings.append(findings[0].model_copy(update={'target_key':'source_fragments/F'}))
    assert not m.validate_review(candidate,review,before,m.QUESTIONS['Update'],{},sources,request=request)


@pytest.mark.parametrize('kind',['duplicate','contradictory','repeated_prior'])
def test_explicit_context_findings_cannot_duplicate_or_approve_the_rejected_object(case,kind):
    request,before,candidate,sources=case
    f=m.Finding(target_key='evidence_steps/E',component='statement',category='evidence',
        reason='independent issue',citations=[citation()],acceptance_condition='clarify independent clause',
        affected_target_keys=[],impact_reason='C does not use this independent clause')
    review=m.Review(proposal_sha256=candidate.digest,answered_questions=['own_source'],prior_findings=[],
        findings=[f],verdicts=[c.Verdict(record_key=k,approved=k!='evidence_steps/E',
            problems=[] if k!='evidence_steps/E' else ['independent issue'],reason='specific review') for k in before])
    unresolved={}
    if kind=='repeated_prior':
        fid=f.identity('original');unresolved={fid:f.model_dump(mode='json')}
        review.prior_findings=[m.FindingResolution(finding_id=fid,disposition='persistent',
            reason='same issue still exists',citations=[citation()],affected_target_keys=[],impact_reason='independent issue')]
        error='repeats a stable finding'
    elif kind=='duplicate':
        review.findings.append(f.model_copy(update={'reason':'same issue reworded','affected_target_keys':['claims/C']}))
        error='repeats a stable finding'
    else:
        for v in review.verdicts:
            if v.record_key=='evidence_steps/E':v.approved=True;v.problems=[]
        error='finding contradicts an approved object'
    with pytest.raises(ValueError,match=error):
        m.validate_review(candidate,review,before,['own_source'],unresolved,sources,request=request)



@pytest.mark.parametrize('scope',['context','blocking','resolved','omitted'])
def test_proposer_issue_needs_independent_review_instead_of_automatic_blocking(case,scope):
    request,before,candidate,sources=case
    f=m.Finding(target_key='evidence_steps/E',component='statement',category='source_uncertain',
        reason='proposer flags uncertainty',citations=[citation()],acceptance_condition='check source',human_question='which clause?',
        affected_target_keys=['source_fragments/F'],impact_reason='provisional reporter scope is not reviewer authority')
    candidate.findings=[f]
    pending=m.review_findings(request.action_id,{},candidate)
    fid=next(iter(pending))
    resolution=m.FindingResolution(finding_id=fid,disposition='resolved' if scope=='resolved' else 'persistent',
        reason='independent source judgment',citations=[citation()],
        affected_target_keys=['claims/C'] if scope=='blocking' else [],impact_reason='independent judgment of target effect')
    review=m.Review(proposal_sha256=candidate.digest,answered_questions=['own_source'],findings=[],
        prior_findings=[] if scope=='omitted' else [resolution],
        verdicts=[c.Verdict(record_key=k,approved=k!='evidence_steps/E' or scope in {'omitted','resolved'},
            problems=['uncertain'] if k=='evidence_steps/E' and scope in {'context','blocking'} else [],reason='independent judgment') for k in before])
    if scope=='omitted':
        with pytest.raises(ValueError,match='reconcile every prior stable finding'):
            m.validate_review(candidate,review,before,['own_source'],{},sources,request=request)
    else:
        assert m.validate_review(candidate,review,before,['own_source'],{},sources,request=request)==(scope!='blocking')
        assert bool(m.unresolved_after(request.action_id,{},review,request=request,candidate=candidate))==(scope=='blocking')
        assert bool(m.context_findings_after(request.action_id,{},review,request=request,candidate=candidate))==(scope=='context')


def test_gpt_adjudication_requires_every_objection_and_exact_source_quote(case):
    _,_,candidate,sources=case
    pending={'first':{},'context':{}}
    candidate.finding_resolutions=[m.FindingResolution(finding_id='first',disposition='resolved',reason='rejected',citations=[citation()])]
    with pytest.raises(ValueError,match='every audit finding'):m.validate_proposer_resolutions(candidate,pending,sources)
    candidate.finding_resolutions.append(m.FindingResolution(finding_id='context',disposition='persistent',reason='source unclear',citations=[citation()]))
    m.validate_proposer_resolutions(candidate,pending,sources)
    assert m.adjudication_outcome({'new':{'category':'source_uncertain'}},candidate)=='revision_required'
    assert m.adjudication_outcome({'context':{'category':'source_uncertain'}},candidate)=='human_required'
    assert m.adjudication_outcome({'first':{'category':'source_uncertain'}},candidate)=='disagreement'
