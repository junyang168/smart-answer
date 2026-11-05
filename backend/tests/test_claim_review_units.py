"""Bounded group boundaries, legacy compatibility, and shared-write protection."""
import json

import pytest

from backend.pipeline import claim_management as cm, claim_direct_scope as direct
from backend.pipeline import claim_management_intake as intake, claim_management_batch as batch
from backend.pipeline.claim_review_units import CONTRACT, validate_members
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.source_reconciliation_runner import binding
from backend.tests.test_claim_management_intake import claims, pass_review
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts, passing
from backend.tests.test_claim_direct_scope import chain
from backend.tests.test_claim_joint_update import shared
from backend.tests.test_claim_management_human_source_follow_up import authorize
from backend.pipeline import claim_review_reuse
from backend.pipeline import claim_review_units


def policy_file(root):
    policy = cm.Policy.model_validate_json(intake.DEFAULT_POLICY.read_text())
    policy.review_unit_contract = CONTRACT
    policy.joint_update_max_targets = 10
    path = root / 'bounded-policy.json'
    path.write_text(policy.model_dump_json())
    return path


@pytest.mark.parametrize('recovery_fault',[None,'readback','created_drift','new_relation'])
def test_unit_can_reserve_and_commit_two_claims_without_extra_model_rounds(claims,monkeypatch,recovery_fault):
    from backend.api.canonical_repository.claim_identity_allocation import allocated_ids
    store,root=claims
    policy=policy_file(root.parent)
    prepared=claim_review_units.prepare(['C'],root,instruction='Review C and its two unrecorded source judgments',
        creation_count=2,policy=policy,store=store)
    _,jobs=batch.load(prepared['prepared_update']['manifest']['path'],root/'batch',store=store)
    request,execution=next(iter(jobs.values()))
    authority=json.loads(cm.source.verify_file(request.authorization).read_text())
    ids=allocated_ids(authority['claim_creation_allocations'])
    assert len(ids)==len(set(ids))==2
    citation={'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}
    def review(packet):
        result=pass_review(packet)
        if not packet['unresolved']:
            result['findings']=[{'target_key':'evidence_steps/EC','component':'independent_judgement_without_claim',
                'category':'mapping','reason':'synthetic two source judgments need Claim records',
                'acceptance_condition':'create both separately and retain the old owner','citations':[citation],
                'affected_target_keys':['claims/C'],'affected_write_keys':[],'impact_reason':'own evidence maintenance'}]
        else:
            result['prior_findings']=[{'finding_id':fid,'disposition':'resolved','reason':'both created',
                'citations':[citation],'affected_target_keys':['claims/C'],
                'affected_write_keys':['evidence_steps/EC'],'impact_reason':'reciprocal owners preserved'} for fid in packet['unresolved']]
        return result
    def propose(packet):
        return {'changes':[{'record_key':'evidence_steps/EC',
            'fields_json':json.dumps({'statement':'甲是假设，不是断言。','produced_claim_ids':['C',*ids]}),
            'reason':'retain old owner and bind both new records','citations':[citation]}],
            'creations':[{'collection':'claims','object_id':cid,'fields_json':json.dumps({
                'statement':statement,'claim_type':'exegesis','evidence_step_ids':['EC'],
                'occurrences':[{'source_id':'S','anchors':[]}]}),'reason':'synthetic independent judgment','citations':[citation]}
                for cid,statement in zip(ids,['甲是假设。','甲不是断言。'])],
            'retirements':[],'findings':[],'finding_resolutions':[{'finding_id':fid,'disposition':'resolved',
                'reason':'both created','citations':[citation],'affected_target_keys':['claims/C'],
                'affected_write_keys':['evidence_steps/EC'],'impact_reason':'both claims bind existing evidence'} for fid in packet['unresolved']]}
    ports=SyntheticPorts(store,execution,proposal=propose,opus=review)
    if recovery_fault:
        from copy import deepcopy
        from pathlib import Path
        from backend.pipeline.claim_review_recovery import recover_returned_review,VERSION
        from backend.api.canonical_repository.postgres_store import ChangeSetConflict
        record=store.record_claim_management_stage
        def interrupted(rid,receipt):
            if json.loads(Path(receipt['path']).read_text())['stage']=='readback':
                if recovery_fault=='new_relation':
                    store.apply_plan(store.plan_package({'package_id':'late-unreviewed-neighbour',
                        'claims':[{'claim_id':'D','statement':'甲是假设。','claim_type':'exegesis',
                            'occurrences':[{'source_id':'S','anchors':[]}]}],
                        'claim_relations':[{'claim_relation_id':'LATE','from_id':'C','to_id':'D','relation_type':'supports'}]}))
                    return record(rid,receipt)
                raise ValueError('synthetic committed-creation readback stop')
            return record(rid,receipt)
        monkeypatch.setattr(store,'record_claim_management_stage',interrupted)
        stopped=execute_claim_management(store,request,root=execution,ports=ports)
        assert stopped['status']=='paused',stopped
        parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
        rid=next(iter(parent['latest_rounds'].values()));saved=store.get_cvp_group(rid)
        assert saved['checkpoint']=='applied'
        if recovery_fault=='new_relation':
            assert 'omitted a current neighbour' in stopped['detail'],stopped
            assert ports.calls==['opus','gpt','opus']
            return
        ledger=deepcopy(parent['call_reservations']);changes=list(saved['change_set_ids'])
        original_raw=(execution/rid/'opus.raw.json').read_bytes()
        if recovery_fault=='created_drift':
            row=deepcopy(store.get_record_state('claims',ids[0])['payload']);row['statement']='external changed teaching'
            store.apply_plan(store.plan_package({'package_id':'created-postimage-drift','claims':[row]}))
        direction=root/'creation-readback-recovery.json'
        direction.write_text(json.dumps({'schema_version':VERSION,'round_id':rid,'authorized_by':'owner',
            'authorization_quote':'synthetic exact creation readback recovery','human_content_approval':False,
            'prior_metadata_sha256':cm.sha256_json({k:v for k,v in saved.items() if k not in {'run_id','status','error'}}),
            'resume_applied_readback':True}))
        if recovery_fault=='created_drift':
            with pytest.raises((ValueError,ChangeSetConflict)):
                recover_returned_review(store,rid,authorization=binding(direction))
            assert store.get_cvp_group(rid)==saved
            return
        recover_returned_review(store,rid,authorization=binding(direction))
        monkeypatch.setattr(store,'record_claim_management_stage',record)
        def no_apply(*args,**kwargs):raise AssertionError('committed creations must never be applied twice')
        monkeypatch.setattr(store,'apply_plan',no_apply)
    assert execute_claim_management(store,request,root=execution,ports=ports)['status']=='completed'
    assert ports.calls==['opus','gpt','opus']
    if recovery_fault:
        assert store.get_cvp_group(parent['run_id'])['call_reservations']==ledger
        assert store.get_cvp_group(rid)['change_set_ids']==changes
        assert (execution/rid/'opus.raw.json').read_bytes()==original_raw
    assert store.get_record_state('evidence_steps','EC')['payload']['produced_claim_ids']==['C',*ids]
    assert all(store.claim_management_qualification(cid)['ready'] for cid in ids)
    assert claim_review_units.prepare(['C'],root,instruction='Review C and its two unrecorded source judgments',
        creation_count=2,policy=policy,store=store)==prepared
    with pytest.raises(ValueError):
        claim_review_units.prepare(['C'],root,instruction='Review C and its two unrecorded source judgments',
            creation_count=3,policy=policy,store=store)


@pytest.mark.parametrize('count',[0,-1,True,1.5])
def test_unit_rejects_invalid_creation_count_before_preparing(tmp_path,count):
    with pytest.raises(ValueError,match='positive integer'):
        claim_review_units.prepare(['C'],tmp_path,instruction='Review C',creation_count=count)


@pytest.mark.parametrize('groups', [[[]], [['A'] * 2], [['A'], ['A']], [[str(i) for i in range(11)]]])
def test_membership_rejects_duplicates_and_oversized_units(groups):
    with pytest.raises(ValueError):
        validate_members(groups)


def test_old_policy_bytes_do_not_acquire_unit_contract():
    policy = cm.Policy.model_validate_json(intake.DEFAULT_POLICY.read_text())
    assert 'review_unit_contract' not in policy.model_dump(mode='json')
    assert validate_members([['A'], ['B', 'C']]) == [['A'], ['B', 'C']]


def test_write_capability_does_not_promote_related_claims(chain):
    store, request, output, root = chain
    path = policy_file(root)
    request.policy = cm.source.FileBinding.model_validate(binding(path))
    # Do not change authority or dispatch this synthetic request; inspect scope only.
    request.permissions += [cm.source.Permission(collection='claim_relations', object_id='AB', fields=['reason']),
                            cm.source.Permission(collection='evidence_steps', object_id='EB', fields=['statement'])]
    states = store.read_claim_context([('claims', 'A')])
    before = {cm.source.key(*key): value for key, value in states.items()}
    selected = direct.scope(request, before)
    assert selected['content_claim_keys'] == ['claims/A']
    assert selected['neighbour_claim_keys'] == ['claims/B']
    assert 'claims/C' not in selected['record_keys']
    # An actual EB change still has its owner checked, even without a target B.
    payload = dict(before['evidence_steps/EB']['payload'], statement='Changed shared evidence')
    candidate = cm.existing_candidate(before)
    candidate.records = [cm.source.Row(collection='evidence_steps', object_id='EB', payload=payload)]
    next(d for d in candidate.dispositions if d.record_key == 'evidence_steps/EB').action = 'update'
    impacted = direct.scope(request, before, candidate)
    assert 'claims/B' in impacted['impact_claim_keys']
    assert impacted['content_claim_keys'] == ['claims/A']
    assert 'evidence_steps/EB' in impacted['record_keys']


def test_nonblocking_leads_do_not_force_a_gpt_round(chain):
    _, request, _, root = chain
    request.policy = cm.source.FileBinding.model_validate(binding(policy_file(root)))
    context = {'F': {'target_key': 'claims/B', 'affected_target_keys': [], 'affected_write_keys': [],
                     'impact_reason': 'unrelated old issue'}}
    assert cm.pending_context_adjudications({}, cm.Candidate(records=[], dispositions=[]), context, request=request) == {}
    assert context['F']['target_key'] == 'claims/B'  # No fake resolution or deletion.


@pytest.mark.parametrize('obligation', [None, 'support', 'direct', 'shared_owner', 'required', 'written', 'impact'])
def test_contextual_evidence_stays_visible_without_becoming_a_private_repair_obligation(chain, obligation):
    """A neighbour's background is not our premise; real bindings still block."""
    _, request, _, root = chain
    request.policy = cm.source.FileBinding.model_validate(binding(policy_file(root)))
    rows = {
        'source_documents/S': {'source_id': 'S'},
        'claims/A': {'claim_id': 'A', 'occurrences': [{'source_id': 'S', 'anchors': []}], 'evidence_step_ids': ['EA']},
        'claims/B': {'claim_id': 'B', 'occurrences': [{'source_id': 'S', 'anchors': []}], 'evidence_step_ids': ['EB']},
        'evidence_steps/EA': {'evidence_step_id': 'EA', 'produced_claim_ids': ['A'], 'source_fragment_ids': ['FA']},
        'evidence_steps/EB': {'evidence_step_id': 'EB', 'produced_claim_ids': ['B'], 'source_fragment_ids': ['FB']},
        'source_fragments/FA': {'fragment_id': 'FA', 'source_id': 'S'},
        'source_fragments/FB': {'fragment_id': 'FB', 'source_id': 'S'},
        'knowledge_relations/BACKGROUND': {'relation_id': 'BACKGROUND', 'from_id': 'EB', 'to_id': 'EA',
                                         'relation_type': 'contextualizes'},
    }
    if obligation == 'support':
        # A second path is a real premise, even though a background edge also exists.
        rows['knowledge_relations/PREMISE'] = dict(rows['knowledge_relations/BACKGROUND'],
                                                  relation_id='PREMISE', relation_type='supports')
    if obligation == 'direct':
        rows['claims/A']['evidence_step_ids'].append('EB')
    if obligation == 'shared_owner':
        rows['evidence_steps/EB']['produced_claim_ids'].append('A')
    if obligation == 'required':
        authorize(request, {'required_maintenance_keys': ['source_fragments/FB']})
    before = {k: {'payload': v, 'retired': False} for k, v in rows.items()}
    candidate = cm.existing_candidate(before)
    if obligation == 'written':
        next(d for d in candidate.dispositions if d.record_key == 'source_fragments/FB').action = 'update'
        candidate.records = [cm.source.Row(collection='source_fragments', object_id='FB', payload=rows['source_fragments/FB'])]
    finding = {'target_key': 'source_fragments/FB', 'affected_target_keys': ['claims/A'] if obligation == 'impact' else [],
               'affected_write_keys': [], 'impact_reason': 'Background-only unless explicitly bound, written or impacting the target.'}
    visible = direct.scope(request, before, candidate)
    assert 'source_fragments/FB' in visible['record_keys']
    assert 'source_fragments/FB' in visible['writable_record_keys']  # Existing packet/capabilities stay unchanged.
    assert cm.finding_blocks(finding, request, candidate, records=before) is (obligation is not None)
    edge = dict(finding, target_key='knowledge_relations/BACKGROUND', affected_target_keys=[])
    assert cm.finding_blocks(edge, request, candidate, records=before)  # Our direct relation is still ours.


def test_three_member_unit_uses_one_existing_update_and_retains_all_results(claims):
    store, root = claims
    store.apply_plan(store.plan_package({'package_id': 'third-unit-member',
        'claims': [{'claim_id': 'E', 'statement': '甲是假设。', 'claim_type': 'exegesis',
                    'occurrences': [{'source_id': 'S', 'anchors': []}], 'evidence_step_ids': ['EE']}],
        'evidence_steps': [{'evidence_step_id': 'EE', 'statement': '甲是假设。',
                            'produced_claim_ids': ['E'], 'source_fragment_ids': ['FC'],
                            'support_eligibility': 'eligible_candidate', 'speaker': 'professor', 'stance': 'asserted'}]}))
    policy = policy_file(root.parent)
    prepared = intake.prepare(['C', 'D', 'E'], root, instruction='Review this fixed source unit',
                              groups=[['C', 'D', 'E']], policy=policy, store=store)
    _, jobs = batch.load(prepared['manifest']['path'], root / 'batch', store=store)
    assert len(jobs) == 1
    request, execution = next(iter(jobs.values()))
    assert request.target_keys == ['claims/C', 'claims/D', 'claims/E']
    def review(packet):
        assert set(packet['claim_scope']['content_claim_keys']) == set(request.target_keys)
        from backend.pipeline.claim_evidence_review import BindingFindingsReview, validate_assessments
        from backend.pipeline.source_reconciliation_runner import body, read
        material = body(read(execution/packet['round_id']/'materials.json'))
        candidate = cm.Candidate.model_validate(body(read(execution/packet['round_id']/'candidate.json')))
        incomplete = pass_review(packet)
        incomplete['binding_assessments'].pop()
        with pytest.raises(ValueError, match='each content Claim'):
            validate_assessments(BindingFindingsReview.model_validate(incomplete), material, candidate)
        return pass_review(packet)
    ports = SyntheticPorts(store, execution, opus=review)
    result = execute_claim_management(store, request, root=execution, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['opus']
    assert all(store.claim_management_qualification(cid)['ready'] for cid in ['C', 'D', 'E'])
    assert execute_claim_management(store, request, root=execution, ports=ports)['status'] == 'completed'
    assert ports.calls == ['opus']


@pytest.mark.parametrize('drift', [None, 'evidence', 'new_relation', 'hold'])
def test_full_prior_bystander_pass_reuses_without_model_and_fails_after_drift(claims, drift):
    store, root = claims
    store.apply_plan(store.plan_package({'package_id': 'review-both', 'claim_relations': [
        {'claim_relation_id': 'R', 'from_id': 'D', 'to_id': 'C', 'relation_type': 'supports'}]}))
    prepared = intake.prepare(['C'], root, instruction='Review C', store=store)
    _, jobs = batch.load(prepared['manifest']['path'], root / 'batch', store=store)
    request, execution = next(iter(jobs.values()))
    ports = SyntheticPorts(store, execution, opus=pass_review)
    result = execute_claim_management(store, request, root=execution, ports=ports)
    assert result['status'] == 'completed', result
    assert store.claim_management_qualification('D') is None
    run_id = store.claim_management_qualification('C')['run_id']
    claim_review_reuse.admit(store, 'D', run_id)
    assert store.claim_management_qualification('D')['ready']
    assert ports.calls == ['opus']

    if drift == 'evidence':
        e = store.get_record_state('evidence_steps', 'ED')['payload']
        store.apply_plan(store.plan_package({'package_id': 'evidence-drift', 'evidence_steps': [dict(e, statement='Different evidence')]}))
    elif drift == 'new_relation':
        store.apply_plan(store.plan_package({'package_id': 'relation-drift',
            'claims': [{'claim_id': 'X', 'statement': 'X', 'claim_type': 'exegesis', 'occurrences': [{'source_id': 'S'}]}],
            'claim_relations': [{'claim_relation_id': 'RX', 'from_id': 'D', 'to_id': 'X', 'relation_type': 'supports'}]}))
    elif drift == 'hold':
        c = store.get_record_state('claims', 'D')['payload']
        store.apply_plan(store.plan_package({'package_id': 'hold-drift', 'claims': [dict(c, review_status='human_hold')]}))
    if drift:
        qualification = store.claim_management_qualification('D')
        assert not qualification or not qualification.get('ready')
    assert ports.calls == ['opus']



def test_unit_prepare_excludes_a_previous_pass_from_new_targets(claims):
    store, root = claims
    store.apply_plan(store.plan_package({'package_id': 'first-review-context', 'claim_relations': [
        {'claim_relation_id': 'R', 'from_id': 'D', 'to_id': 'C', 'relation_type': 'supports'}]}))
    old = intake.prepare(['C'], root, instruction='Review C', store=store)
    _, jobs = batch.load(old['manifest']['path'], root/'batch', store=store)
    request, execution = next(iter(jobs.values()))
    ports = SyntheticPorts(store, execution, opus=pass_review)
    assert execute_claim_management(store, request, root=execution, ports=ports)['status'] == 'completed'
    # No caller-provided pass: prepare discovers and verifies D's complete
    # historical review, and uses current C as a reference too.
    prepared = claim_review_units.prepare(['C', 'D'], root.parent/'fixed-unit',
        instruction='Reuse this already reviewed unit', policy=policy_file(root.parent), store=store)
    assert prepared['target_claim_ids'] == []
    assert prepared['reference_claim_ids'] == ['C', 'D']
    assert prepared['prepared_update'] is None
    assert claim_review_units.finish(prepared, store=store)['completed']
    assert ports.calls == ['opus']


def test_reference_only_reading_cannot_be_imported_as_a_complete_review(claims):
    store, root = claims
    store.apply_plan(store.plan_package({'package_id': 'reference-only', 'claim_relations': [
        {'claim_relation_id': 'R', 'from_id': 'D', 'to_id': 'C', 'relation_type': 'supports'}]}))
    prepared = intake.prepare(['C'], root, instruction='Review C only', groups=[['C']],
                              policy=policy_file(root.parent), store=store)
    _, jobs = batch.load(prepared['manifest']['path'], root/'batch', store=store)
    request, execution = next(iter(jobs.values()))
    result = execute_claim_management(store, request, root=execution,
                                      ports=SyntheticPorts(store, execution, opus=pass_review))
    assert result['status'] == 'completed', result
    with pytest.raises(ValueError, match='reference-only'):
        claim_review_reuse.build(store, 'D', store.claim_management_qualification('C')['run_id'])


def test_legacy_complete_review_reuse_does_not_require_neighbours_private_evidence(claims):
    store, root = claims
    store.apply_plan(store.plan_package({'package_id': 'legacy-related', 'claim_relations': [
        {'claim_relation_id': 'R', 'from_id': 'D', 'to_id': 'C', 'relation_type': 'supports'}]}))
    prepared = intake.prepare(['C'], root, instruction='Legacy full review', store=store)
    _, jobs = batch.load(prepared['manifest']['path'], root/'batch', store=store)
    request, execution = next(iter(jobs.values()))
    request.policy = cm.source.FileBinding.model_validate(binding(
        intake.DEFAULT_POLICY.parent/'wang_claim_management_opus_only_v2.json'))
    request.allowed_operations = ['Update']
    request.creation_permissions = []
    authorize(request, {'required_maintenance_keys': []})
    result = execute_claim_management(store, request, root=execution,
                                      ports=SyntheticPorts(store, execution, opus=passing))
    assert result['status'] == 'completed', result
    rid = store.claim_management_qualification('C')['run_id']
    proof = claim_review_reuse.admit(store, 'D', rid)
    assert 'evidence_steps/EC' not in {p['collection']+'/'+p['object_id'] for p in proof['read_pins']}
    assert store.claim_management_qualification('D')['ready']


def test_actual_shared_write_harming_a_reference_still_blocks(shared):
    store, request, execution, root = shared
    evidence = store.get_record_state('evidence_steps', 'E')['payload']
    store.apply_plan(store.plan_package({'package_id': 'bound-shared-evidence',
        'source_fragments': [{'fragment_id': 'F', 'source_id': 'S', 'paragraph_key': 'S0001',
                              'verbatim_excerpt': '甲是假设，不是断言。'}],
        'evidence_steps': [dict(evidence, source_fragment_ids=['F'], support_eligibility='eligible_candidate',
                                speaker='professor', stance='asserted')]}))
    path = policy_file(root)
    policy = cm.Policy.model_validate_json(path.read_text())
    policy.initial_candidate = 'proposal'
    policy.budget.max_revisions = 0
    path.write_text(policy.model_dump_json())
    request.policy = cm.source.FileBinding.model_validate(binding(path))
    authorize(request, {'required_maintenance_keys': ['evidence_steps/E']})
    old = store.get_record_state('evidence_steps', 'E')
    cite = {'source_id': 'S', 'paragraph_key': 'S0001', 'exact_quote': '甲是假设，不是断言。'}
    def propose(packet):
        return {'changes':[{'record_key':'evidence_steps/E',
                            'fields_json':json.dumps({'statement':'甲是假设，不是断言。'}),
                            'reason':'synthetic shared correction','citations':[cite]}],
                'findings':[], 'finding_resolutions':[], 'creations':[], 'retirements':[]}
    def review(packet):
        assert packet['claim_scope']['content_claim_keys'] == ['claims/A', 'claims/B']
        assert packet['claim_scope']['impact_claim_keys'] == ['claims/C']
        result = pass_review(packet)
        result['findings'] = [{'target_key':'claims/C','component':'shared_evidence','category':'evidence',
            'reason':'shared write loses C support','acceptance_condition':'preserve C',
            'citations':[cite],'human_question':None,'affected_target_keys':[],
            'affected_write_keys':['evidence_steps/E'],'impact_reason':'actual write harms C'}]
        return result
    ports = SyntheticPorts(store, execution, proposal=propose, opus=review)
    result = execute_claim_management(store, request, root=execution, ports=ports)
    assert result['status'] == 'paused', result
    assert store.get_record_state('evidence_steps', 'E') == old
    assert ports.calls == ['gpt', 'opus']


def test_shared_fragment_consumers_are_frozen_references_not_extra_targets(claims):
    store, root = claims
    d = store.get_record_state('evidence_steps', 'ED')['payload']
    store.apply_plan(store.plan_package({'package_id': 'shared-fragment',
        'evidence_steps': [dict(d, source_fragment_ids=['FC'])]}))
    path = policy_file(root.parent)
    policy = cm.Policy.model_validate_json(path.read_text())
    policy.initial_candidate = 'proposal'
    path.write_text(policy.model_dump_json())
    prepared = intake.prepare(['C'], root, instruction='Correct C with shared evidence protected',
                              groups=[['C']], policy=path, store=store)
    _, jobs = batch.load(prepared['manifest']['path'], root/'batch', store=store)
    request, execution = next(iter(jobs.values()))
    assert not any(p.key == 'evidence_steps/ED' for p in request.permissions)
    def propose(packet):
        assert 'evidence_steps/ED' in packet['before']
        return {'changes':[{'record_key':'source_fragments/FC',
            'fields_json':json.dumps({'verbatim_excerpt':'甲是假设'}), 'reason':'synthetic concise bound quote',
            'citations':[{'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设'}]}],
            'findings':[], 'finding_resolutions':[], 'creations':[], 'retirements':[]}
    def review(packet):
        assert list(packet['own_evidence_bindings']) == ['claims/C']
        assert 'claims/D' in packet['claim_scope']['impact_claim_keys']
        return pass_review(packet)
    ports = SyntheticPorts(store, execution, proposal=propose, opus=review)
    result = execute_claim_management(store, request, root=execution, ports=ports)
    assert result['status'] == 'completed', result
    assert store.claim_management_qualification('C')['ready']
    assert store.claim_management_qualification('D') is None
    assert ports.calls == ['gpt', 'opus']


def test_readonly_shared_relation_has_complete_endpoints_after_projection(claims):
    store, root = claims
    other = store.get_record_state('evidence_steps', 'ED')['payload']
    store.apply_plan(store.plan_package({'package_id':'outgoing-shared-reference',
        'evidence_steps':[dict(other, evidence_step_id='EZ')],
        'knowledge_relations':[{'relation_id':'K','from_id':'EC','to_id':'EZ',
                                'relation_type':'supports'}]}))
    prepared = intake.prepare(['C'], root, instruction='Review C; outgoing use is reference',
                              groups=[['C']], policy=policy_file(root.parent), store=store)
    _, jobs = batch.load(prepared['manifest']['path'], root/'batch', store=store)
    request, execution = next(iter(jobs.values()))
    before = {cm.source.key(*k):v for k,v in store.read_claim_context([('claims','C')]).items()}
    selected = direct.scope(request, before)
    assert {'knowledge_relations/K','evidence_steps/EZ'} <= set(selected['record_keys'])
    assert 'evidence_steps/EZ' not in selected['writable_record_keys']
    projected = {k:before[k] for k in selected['record_keys']}
    assert direct.scope(request, projected)['record_keys'] == selected['record_keys']
    result = execute_claim_management(store, request, root=execution,
        ports=SyntheticPorts(store, execution, opus=pass_review))
    assert result['status']=='completed', result


def test_unstarted_scope_guard_failure_recovers_same_unit_without_spending_or_resetting_budget(claims, monkeypatch):
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    from backend.pipeline.claim_review_recovery import resume_saved_pre_review
    store, root = claims
    d = store.get_record_state('evidence_steps', 'ED')['payload']
    store.apply_plan(store.plan_package({'package_id':'old-missing-shared-reference',
        'evidence_steps':[dict(d, source_fragment_ids=['FC'])]}))
    prepared = intake.prepare(['C'], root, instruction='Review C only', groups=[['C']],
                              policy=policy_file(root.parent), store=store)
    _, jobs = batch.load(prepared['manifest']['path'], root/'batch', store=store)
    request, execution = next(iter(jobs.values()))
    ports = SyntheticPorts(store, execution, opus=pass_review)
    original_scope = direct.scope
    def old_projection(*args, **kwargs):
        result = original_scope(*args, **kwargs)
        result['record_keys'] = [k for k in result['record_keys'] if k != 'evidence_steps/ED']
        return result
    monkeypatch.setattr(direct, 'scope', old_projection)
    original_guard = store._assert_claim_management_scope
    def fail_before_call(*args, **kwargs):
        raise ChangeSetConflict('direct Claim scope omitted a current neighbour or maintenance owner')
    monkeypatch.setattr(store, '_assert_claim_management_scope', fail_before_call)
    result = execute_claim_management(store, request, root=execution, ports=ports)
    assert result['status'] == 'paused' and ports.calls == []
    parent_id = 'CMA-'+cm.sha256_json(request.action_id)
    parent = store.get_cvp_group(parent_id)
    slots = parent['call_reservations']
    rid = next(iter(parent['latest_rounds'].values()))
    monkeypatch.setattr(store, '_assert_claim_management_scope', original_guard)
    monkeypatch.setattr(direct, 'scope', original_scope)
    with pytest.raises(ValueError, match='resume existing actions'):
        claim_review_units.prepare(['C'], root.parent/'duplicate-unit', instruction='Do not reset this work',
                                   policy=policy_file(root.parent), store=store)
    assert store.get_cvp_group(parent_id)['call_reservations'] == slots
    resume_saved_pre_review(store, rid)
    assert 'evidence_steps/ED' in store.get_cvp_group(rid)['materials']['before']
    assert store.get_cvp_group(parent_id)['call_reservations'] == slots
    result = execute_claim_management(store, request, root=execution, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['opus']


@pytest.mark.parametrize('affects_target', [False, True])
def test_reference_lead_and_real_contradiction_have_distinct_qualification_effects(claims, affects_target):
    store, root = claims
    store.apply_plan(store.plan_package({'package_id':'reference-edge', 'claim_relations':[
        {'claim_relation_id':'R','from_id':'C','to_id':'D','relation_type':'supports'}]}))
    path = policy_file(root.parent)
    first = intake.prepare(['C'], root/'first', instruction='Review C', groups=[['C']], policy=path, store=store)
    _, jobs = batch.load(first['manifest']['path'], root/'first'/'batch', store=store)
    request, execution = next(iter(jobs.values()))
    assert execute_claim_management(store, request, root=execution,
        ports=SyntheticPorts(store, execution, opus=pass_review))['status']=='completed'
    assert store.claim_management_qualification('C')['ready']
    p=cm.Policy.model_validate_json(path.read_text());p.budget.max_revisions=0;path.write_text(p.model_dump_json())
    second = intake.prepare(['D'], root/'second', instruction='Review D; C is reference only',
                            groups=[['D']], policy=path, store=store)
    _, jobs = batch.load(second['manifest']['path'], root/'second'/'batch', store=store)
    request, execution = next(iter(jobs.values()))
    def review(packet):
        assert list(packet['own_evidence_bindings']) == ['claims/D']
        result=pass_review(packet)
        result['findings']=[{'target_key':'claims/C','component':'synthetic_reference_question','category':'content',
            'reason':'synthetic reference objection','acceptance_condition':'adjudicate if relevant',
            'citations':[{'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}],
            'affected_target_keys':['claims/D'] if affects_target else [], 'affected_write_keys':[],
            'impact_reason':'undermines D premise' if affects_target else 'unrelated reference lead only'}]
        return result
    ports=SyntheticPorts(store, execution, opus=review)
    result=execute_claim_management(store, request, root=execution, ports=ports)
    assert (result['status']=='completed') is (not affects_target), result
    q=store.claim_management_qualification('C')
    assert bool(q and q.get('ready')) is (not affects_target)
    assert ports.calls==['opus']


def test_repaired_fragment_use_keeps_unanswered_audio_question_as_context(claims):
    store,root=claims
    original=store.get_record_state('source_fragments','FC')
    evidence=store.get_record_state('evidence_steps','EC')['payload']
    store.apply_plan(store.plan_package({'package_id':'alternative-bound-fragment',
        'evidence_steps':[dict(evidence,source_fragment_ids=['FC','FD'])]}))
    prepared=intake.prepare(['C'],root,instruction='Correct unsupported use without claiming speaker verification',
        groups=[['C']],policy=policy_file(root.parent),store=store)
    _,jobs=batch.load(prepared['manifest']['path'],root/'batch',store=store)
    request,execution=next(iter(jobs.values()))
    citation={'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}
    finding={'target_key':'source_fragments/FC','component':'speaker','category':'source_uncertain',
        'reason':'synthetic audio speaker is unknown','human_question':'Who spoke?',
        'acceptance_condition':'Do not rely on an unconfirmed speaker','citations':[citation],
        'affected_target_keys':['claims/C'],'affected_write_keys':[],'impact_reason':'C currently uses this fragment'}
    def resolution(fid):
        return {'finding_id':fid,'disposition':'persistent','reason':'speaker still unknown; unsupported use removed',
            'citations':[citation],'affected_target_keys':[],'affected_write_keys':[],
            'impact_reason':'No postimage record or Claim uses FC; the alternative evidence was fully reviewed'}
    def review(packet):
        result=pass_review(packet)
        if not packet['unresolved']:result['findings']=[finding]
        else:result['prior_findings']=[resolution(fid) for fid in packet['unresolved']]
        return result
    def propose(packet):
        return {'changes':[{'record_key':'evidence_steps/EC','fields_json':json.dumps({'source_fragment_ids':['FD']}),
            'reason':'use independently bound alternative','citations':[citation]}],
            'creations':[],'retirements':[],'findings':[],
            'finding_resolutions':[resolution(fid) for fid in packet['unresolved']]}
    ports=SyntheticPorts(store,execution,proposal=propose,opus=review)
    result=execute_claim_management(store,request,root=execution,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['opus','gpt','opus']
    assert store.get_record_state('source_fragments','FC')==original
    assert store.claim_management_qualification('C')['ready']
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
    assert not parent['findings'] and next(iter(parent['context_findings'].values()))['human_question']=='Who spoke?'


def test_explicit_orphan_question_maintenance_is_read_without_unused_permission_roots(claims):
    from pathlib import Path
    from backend.pipeline.claim_joint_update import context_roots
    from backend.tests.test_claim_management_human_source_follow_up import authorize
    store,root=claims
    # Synthetic old Question payloads exercise the read/authority boundary.
    store.apply_plan(store.plan_package({'package_id':'unlinked-question-maintenance',
        'source_fragments':[{'fragment_id':'FQ','source_id':'S','paragraph_key':'S0001','verbatim_excerpt':'甲是假设，不是断言。'},
            {'fragment_id':'FU','source_id':'S','paragraph_key':'S0001','verbatim_excerpt':'甲是假设'}],
        'questions':[{'question_id':'Q','text':'甲是否是斷言？','source_fragment_ids':['FQ'],
            'answer_state':'unanswered','answer_claim_ids':[]},
            {'question_id':'UNUSED','text':'另一個問題？','source_fragment_ids':['FU'],
                'answer_state':'unanswered','answer_claim_ids':[]}]}))
    prepared=intake.prepare(['C'],root,instruction='Update the explicit Question answer; keep the Claim',
        policy=policy_file(root.parent),groups=[['C']],store=store)
    request,execution=next(iter(batch.load(prepared['manifest']['path'],root/'batch',store=store)[1].values()))
    p=cm.Policy.model_validate_json(Path(request.policy.path).read_text());p.initial_candidate='proposal'
    pp=root/'q-policy.json';pp.write_text(p.model_dump_json());request.policy=cm.source.FileBinding.model_validate(binding(pp))
    a=json.loads(cm.source.verify_file(request.authorization).read_text());a['required_maintenance_keys']=['questions/Q']
    request.permissions=[cm.source.Permission(collection='questions',object_id='Q',fields=['answer_state','answer_claim_ids'])]
    authorize(request,{k:v for k,v in a.items() if k not in {'request','policy'}})
    with_unused_grant=request.model_copy(deep=True,update={'permissions':request.permissions+[
        cm.source.Permission(collection='questions',object_id='UNUSED',fields=['answer_claim_ids'])]})
    assert ('questions','Q') in context_roots(with_unused_grant) and ('questions','UNUSED') not in context_roots(with_unused_grant)
    original=store.get_record_state('claims','C')
    cite={'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}
    def propose(packet):
        assert 'questions/Q' in packet['before'] and 'questions/UNUSED' not in packet['before']
        return {'changes':[{'record_key':'questions/Q','fields_json':json.dumps({'answer_state':'answered','answer_claim_ids':['C']}),
            'reason':'synthetic direct answer link','citations':[cite]}], 'creations':[],'retirements':[],
            'findings':[],'finding_resolutions':[]}
    ports=SyntheticPorts(store,execution,proposal=propose,opus=pass_review)
    result=execute_claim_management(store,request,root=execution,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['gpt','opus'] and store.get_record_state('claims','C')==original
    assert store.get_record_state('questions','Q')['payload']['answer_claim_ids']==['C']
    assert store.get_record_state('questions','UNUSED')['payload']['answer_claim_ids']==[]
    assert store.claim_management_qualification('C')['ready']


@pytest.mark.parametrize('bad',['missing_question','missing_evidence','hidden_claim','missing_list'])
def test_invalid_explicit_maintenance_is_durably_refused_without_calls_or_content(claims,bad):
    store,root=claims
    prepared=intake.prepare(['C'],root,instruction='Validate mandatory maintenance before any review',
        policy=policy_file(root.parent),groups=[['C']],store=store)
    request,execution=next(iter(batch.load(prepared['manifest']['path'],root/'batch',store=store)[1].values()))
    extra=json.loads(cm.source.verify_file(request.authorization).read_text())
    extra={k:v for k,v in extra.items() if k not in {'request','policy'}}
    if bad=='missing_list':extra.pop('required_maintenance_keys')
    else:extra['required_maintenance_keys']=[{'missing_question':'questions/ABSENT','missing_evidence':'evidence_steps/ABSENT',
                                            'hidden_claim':'claims/ABSENT'}[bad]]
    authorize(request,extra)
    before=store.get_record_state('claims','C');ports=SyntheticPorts(store,execution)
    result=execute_claim_management(store,request,root=execution,ports=ports)
    assert result['status']=='paused' and result['reason']=='engineering_required',result
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
    assert parent['admission_refusal'] and not parent['call_reservations'] and not parent['latest_rounds']
    assert ports.calls==[] and store.get_record_state('claims','C')==before
    assert execute_claim_management(store,request,root=execution,ports=ports)==result
