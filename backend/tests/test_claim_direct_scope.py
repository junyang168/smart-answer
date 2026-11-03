"""One Claim hop, complete own evidence, and unchanged transactional protections."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.pipeline import claim_management as cm, claim_direct_scope as direct
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.source_reconciliation_runner import binding
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import operation_request, SyntheticPorts
from backend.tests.test_claim_management_human_source_follow_up import authorize
from backend.tests.test_claim_joint_update import shared
from backend.tests.test_claim_qualification_reuse import related


def bind(request, root, *, independent=False, proposal=False, required=()):
    policy = cm.Policy.model_validate_json((cm.DEFAULT_POLICY.parent/'wang_claim_management_joint_update_v4.json').read_text())
    policy.independent_audit = independent
    policy.budget.gemini_calls = 2 if independent else 0
    if proposal:
        policy.initial_candidate = 'proposal'; policy.budget.max_revisions = 0
    path = root/'one-hop-policy.json'; path.write_text(policy.model_dump_json())
    request.policy = cm.source.FileBinding.model_validate(binding(path))
    authorize(request, {'required_maintenance_keys': list(required)})


def relation(ident, left, right):
    return {'claim_relation_id': ident, 'source_id': left, 'target_id': right,
            'relation_type': 'supports', 'reason': 'synthetic declared relation'}


@pytest.fixture
def chain(action):
    store, _, _, _, root, _ = action
    store.apply_plan(store.plan_package({'package_id': 'one-hop-chain',
        'claims': [{'claim_id': c, 'statement': '甲是假设。'+c, 'claim_type': 'exegesis',
                    'occurrences': [{'source_id': 'S', 'anchors': []}], 'evidence_step_ids': ['E'+c]} for c in ['A','B','C']],
        'evidence_steps': [{'evidence_step_id': 'E'+c, 'statement': '甲是假设。', 'produced_claim_ids': [c]} for c in ['A','B','C']],
        'claim_relations': [relation('AB','A','B'), relation('BC','B','C')]}))
    request, output = operation_request(action, 'Update', ['A'])
    bind(request, root)
    return store, request, output, root


def yes(packet):
    return {'complete_review': True, 'findings': [], 'prior_findings': []}


@pytest.mark.parametrize('independent', [False, True])
def test_runtime_reads_direct_neighbour_not_its_evidence_or_next_claim(chain, independent):
    store, request, output, root = chain; bind(request, root, independent=independent)
    original = {c: store.get_record_state('claims', c) for c in ['A','B','C']}
    def opus(packet):
        assert {'claims/A','claims/B','evidence_steps/EA','claim_relations/AB','source_documents/S'} <= set(packet['denominator'])
        assert not {'claims/C','evidence_steps/EB','evidence_steps/EC','claim_relations/BC'} & set(packet['before'])
        assert packet['claim_scope']['neighbour_claim_keys'] == ['claims/B']
        assert set(packet['sources']) == {'S'}
        return yes(packet)
    def gemini(packet):
        from backend.tests.test_claim_management_execution_postgres import passing
        assert packet['claim_scope']['neighbour_claim_keys'] == ['claims/B']
        assert 'evidence_steps/EB' not in packet['actual_records']
        return passing(packet)
    ports = SyntheticPorts(store, root, opus=opus, gemini=gemini)
    result = execute_claim_management(store, request, root=output, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['opus'] + (['gemini'] if independent else [])
    assert store.claim_management_qualification('A')['ready']
    assert store.claim_management_qualification('B') is None
    assert store.claim_management_qualification('C') is None
    assert {c: store.get_record_state('claims', c) for c in original} == original
    assert execute_claim_management(store, request, root=output, ports=ports) == result
    assert ports.calls.count('opus') == 1


def test_cross_sermon_neighbour_keeps_own_identity_without_becoming_target_evidence(chain):
    store, request, output, root = chain
    foreign = root/'foreign.json'; foreign.write_text(json.dumps({'script': [{'text': '另一篇講道。'}]}))
    b = deepcopy(store.get_record_state('claims','B')['payload']); b['occurrences'] = [{'source_id':'T','anchors':[]}]
    store.apply_plan(store.plan_package({'package_id':'foreign-neighbour',
        'source_documents':[{'source_id':'T','title':'foreign sermon','source_type':'sermon_transcript',
            'source_path':str(foreign),'source_file_sha256':binding(foreign)['file_sha256']}], 'claims':[b]}))
    def opus(packet):
        assert packet['before']['claims/B']['payload']['occurrences'][0]['source_id'] == 'T'
        assert set(packet['sources']) == {'S'}
        assert 'source_documents/T' not in packet['before'] and 'evidence_steps/EB' not in packet['before']
        return yes(packet)
    result = execute_claim_management(store, request, root=output, ports=SyntheticPorts(store,root,opus=opus))
    assert result['status'] == 'completed', result
    assert store.claim_management_qualification('A')['ready'] and store.claim_management_qualification('B') is None


def test_own_multihop_evidence_is_complete_and_foreign_evidence_is_rejected(chain):
    store, request, _, _ = chain
    before = {cm.source.key(*k):cm.model_state(v) for k,v in store.read_claim_context([('claims','A')]).items()}
    before['evidence_steps/EA']['payload']['source_fragment_ids'] = ['FA']
    before['source_fragments/FA'] = {'payload':{'fragment_id':'FA','source_id':'S','paragraph_key':'S0001','verbatim_excerpt':'甲是假设。'},'retired':False}
    selected = direct.scope(request, before)
    assert {'claims/A','evidence_steps/EA','source_fragments/FA','source_documents/S'} <= set(selected['record_keys'])
    before['source_fragments/FA']['payload']['source_id'] = 'T'
    before['source_documents/T'] = {'payload':{'source_id':'T'},'retired':False}
    with pytest.raises(ValueError, match='own source'):
        direct.scope(request, before)


def test_old_policy_keeps_its_exact_transitive_scope(chain):
    store, request, _, root = chain
    before = {cm.source.key(*k):cm.model_state(v) for k,v in store.read_claim_context([('claims','A')]).items()}
    assert 'claims/C' not in cm.audit_keys(request, before)
    request.policy = cm.source.FileBinding.model_validate(binding(cm.DEFAULT_POLICY.parent/'wang_claim_management_joint_update_v3.json'))
    authorize(request, {'required_maintenance_keys': []})
    assert 'claims/C' in cm.audit_keys(request, before)
    old = cm.Policy.model_validate_json(Path(request.policy.path).read_text())
    assert 'claim_scope_contract' not in old.model_dump(mode='json')


def test_neighbour_new_relation_does_not_expand_or_invalidate_target_scope(chain):
    store, request, output, root = chain
    assert execute_claim_management(store,request,root=output,ports=SyntheticPorts(store,root,opus=yes))['status']=='completed'
    store.apply_plan(store.plan_package({'package_id':'extra-neighbour-edge','claim_relations':[relation('CB','C','B')]}))
    assert store.claim_management_qualification('A')['ready']


def test_new_target_relation_requires_new_bound_review(chain):
    store, request, output, root = chain
    assert execute_claim_management(store,request,root=output,ports=SyntheticPorts(store,root,opus=yes))['status']=='completed'
    store.apply_plan(store.plan_package({'package_id':'new-target-edge','claim_relations':[relation('AC','A','C')]}))
    assert not (store.claim_management_qualification('A') or {}).get('ready')


def test_relation_added_during_model_call_blocks_commit_without_repeating_review(chain):
    store, request, output, root = chain
    original = store.get_record_state('claims','A')
    def opus(packet):
        store.apply_plan(store.plan_package({'package_id':'concurrent-target-edge','claim_relations':[relation('AC','A','C')]}))
        return yes(packet)
    ports=SyntheticPorts(store,root,opus=opus)
    result=execute_claim_management(store,request,root=output,ports=ports)
    assert result['status']=='paused' and result['reason']=='engineering_required', result
    assert store.get_record_state('claims','A')==original and ports.calls==['opus']


@pytest.mark.parametrize('extra_drift', [None, 'content', 'source', 'scope'])
def test_reviewed_transaction_only_drift_reuses_exact_answers_or_rejects_content_drift(chain, extra_drift):
    from backend.pipeline.claim_review_recovery import recover_returned_review, VERSION
    store, request, output, root = chain
    bind(request, root, proposal=True)
    eb = deepcopy(store.get_record_state('evidence_steps', 'EB')['payload'])
    eb['source_fragment_ids'] = ['FB']
    store.apply_plan(store.plan_package({'package_id': 'readonly-neighbour-fragment',
        'evidence_steps': [eb], 'source_fragments': [{'fragment_id': 'FB', 'source_id': 'S',
            'paragraph_key': 'S0001', 'verbatim_excerpt': '甲是假设'}]}))
    citation = {'source_id': 'S', 'paragraph_key': 'S0001', 'exact_quote': '甲是假设，不是断言。'}
    def propose(packet):
        return {'changes': [{'record_key': 'claims/A',
            'fields_json': json.dumps({'statement': '甲是假设，不是断言。'}),
            'reason': 'faithful correction', 'citations': [citation]}],
            'findings': [], 'finding_resolutions': []}
    def opus(packet):
        assert 'source_fragments/FB' not in packet['before']
        changed = deepcopy(store.get_record_state('source_fragments', 'FB')['payload'])
        changed['verbatim_excerpt'] = '甲是假设，不是断言。'
        store.apply_plan(store.plan_package({'package_id': 'concurrent-readonly-fragment',
            'source_fragments': [changed]}))
        return yes(packet)
    ports = SyntheticPorts(store, root, proposal=propose, opus=opus)
    result = execute_claim_management(store, request, root=output, ports=ports)
    assert result['status'] == 'paused' and 'transaction current dependency drift:' in result['detail'], result
    assert result['reason'] == 'engineering_required'  # A source_fragments key is not a source-file edit.
    parent = store.get_cvp_group('CMA-' + cm.sha256_json(request.action_id))
    rid = next(iter(parent['latest_rounds'].values()))
    saved = store.get_cvp_group(rid)
    assert saved['checkpoint'] == 'prepared' and saved['opus_approved']
    assert 'source_fragments/FB' in saved['materials']['transaction_before']
    old_files = {name: (Path(parent['execution_root']) / rid / name).read_bytes()
        for name in ('gpt.raw.json', 'opus.raw.json', 'candidate.json')}
    if extra_drift == 'content':
        changed = deepcopy(store.get_record_state('claims', 'A')['payload'])
        changed['statement'] = 'a concurrent different judgment'
        store.apply_plan(store.plan_package({'package_id': 'concurrent-model-fact', 'claims': [changed]}))
    elif extra_drift == 'source':
        Path(request.source_bindings['S'].path).write_text(json.dumps({'script': [{'text': 'changed source'}]}))
    elif extra_drift == 'scope':
        store.apply_plan(store.plan_package({'package_id': 'concurrent-model-scope',
            'claim_relations': [relation('AC', 'A', 'C')]}))
    direction = root / 'transaction-context-direction.json'
    direction.write_text(json.dumps({'schema_version': VERSION, 'round_id': rid,
        'prior_metadata_sha256': cm.sha256_json({k: v for k, v in saved.items() if k not in {'run_id', 'status', 'error'}}), 'authorized_by': 'owner',
        'authorization_quote': 'continue this original Update without another model call',
        'human_content_approval': False, 'refresh_transaction_context': True}))
    if extra_drift:
        expected = 'physical file missing or changed' if extra_drift == 'source' else 'content scope drift'
        with pytest.raises(ValueError, match=expected):
            recover_returned_review(store, rid, authorization=binding(direction))
        assert store.get_cvp_group(rid) == saved
    else:
        recovered = recover_returned_review(store, rid, authorization=binding(direction))
        assert recover_returned_review(store, rid, authorization=binding(direction)) == recovered
        now = store.get_cvp_group(rid)
        after_parent = store.get_cvp_group(parent['run_id'])
        assert after_parent['call_reservations'] == parent['call_reservations']
        assert after_parent['request'] == parent['request'] and after_parent['policy'] == parent['policy']
        assert cm.model_packet(now['materials']) == cm.model_packet(saved['materials'])
        assert now['candidate'] == saved['candidate']
        assert now['engineering_review_recoveries'][-1]['transaction_context_refresh']['changed_transaction_keys'] == ['source_fragments/FB']
        assert all((Path(recovered) / name).read_bytes() == data for name, data in old_files.items())
        assert all((Path(parent['execution_root']) / rid / name).read_bytes() == data for name, data in old_files.items())
        assert execute_claim_management(store, request, root=output, ports=ports)['status'] == 'completed'
        assert store.claim_management_qualification('A')['ready']
    assert ports.calls == ['gpt', 'opus']


def test_shared_evidence_owner_checks_do_not_walk_owners_other_relations(shared):
    store,request,output,root=shared; bind(request,root,required=['evidence_steps/E'])
    store.apply_plan(store.plan_package({'package_id':'owner-neighbour',
        'claims':[{'claim_id':'D','statement':'D','claim_type':'exegesis','occurrences':[{'source_id':'S','anchors':[]}]}],
        'claim_relations':[relation('CD','C','D')]}))
    def opus(packet):
        assert 'claims/C' in packet['denominator']
        assert 'claims/C' in packet['claim_scope']['content_claim_keys']
        assert 'claims/D' not in packet['denominator'] and 'claim_relations/CD' not in packet['before']
        return yes(packet)
    result=execute_claim_management(store,request,root=output,ports=SyntheticPorts(store,root,opus=opus))
    assert result['status']=='completed',result
    assert store.claim_management_qualification('A')['ready'] and store.claim_management_qualification('C') is None


def test_shared_evidence_write_harming_readonly_owner_still_refused(shared):
    store,request,output,root=shared; bind(request,root,proposal=True,required=['evidence_steps/E'])
    old=store.get_record_state('evidence_steps','E')
    citation={'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}
    def propose(packet):
        assert 'claims/C' in packet['before']
        return {'changes':[{'record_key':'evidence_steps/E','fields_json':json.dumps({'statement':'甲是假设，不是断言。'}),
            'reason':'synthetic correction','citations':[citation]}],'findings':[],'finding_resolutions':[]}
    def opus(packet):
        return {'complete_review':True,'prior_findings':[],'findings':[{'target_key':'claims/C',
            'component':'shared_evidence','category':'evidence','reason':'shared write loses C support',
            'acceptance_condition':'preserve C','human_question':None,'citations':[citation],
            'affected_target_keys':[],'affected_write_keys':['evidence_steps/E'],'impact_reason':'actual write harms C'}]}
    ports=SyntheticPorts(store,root,proposal=propose,opus=opus)
    result=execute_claim_management(store,request,root=output,ports=ports)
    assert result['status']=='paused' and store.get_record_state('evidence_steps','E')==old
    assert ports.calls==['gpt','opus']


@pytest.mark.parametrize('failure', [None, 'rollback', 'reply_lost'])
def test_direct_scope_real_write_and_recovery_keep_neighbours_immutable(chain, monkeypatch, failure):
    store, request, output, root = chain; bind(request, root, proposal=True)
    original = {c: store.get_record_state('claims', c) for c in ['A','B','C']}
    citation = {'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}
    def propose(packet):
        return {'changes':[{'record_key':'claims/A','fields_json':json.dumps({'statement':'甲是假设，不是断言。'}),
            'reason':'faithful synthetic correction','citations':[citation]}],'findings':[],'finding_resolutions':[]}
    ports = SyntheticPorts(store, root, proposal=propose, opus=yes)
    apply = store.apply_plan
    check = store._assert_transaction_edges_and_reviews
    if failure == 'rollback':
        def reject(*args, **kwargs): raise RuntimeError('synthetic late write rejection')
        monkeypatch.setattr(store, '_assert_transaction_edges_and_reviews', reject)
    elif failure == 'reply_lost':
        def lose(*args, **kwargs):
            result = apply(*args, **kwargs)
            raise RuntimeError('synthetic lost commit response')
        monkeypatch.setattr(store, 'apply_plan', lose)
    result = execute_claim_management(store, request, root=output, ports=ports)
    if failure:
        assert result['status'] == 'paused', result
        if failure == 'rollback': assert store.get_record_state('claims','A') == original['A']
        monkeypatch.setattr(store, '_assert_transaction_edges_and_reviews', check)
        monkeypatch.setattr(store, 'apply_plan', apply)
        result = execute_claim_management(store, request, root=output, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['gpt','opus']
    assert store.get_record_state('claims','A')['revision'] == original['A']['revision'] + 1
    assert all(store.get_record_state('claims',c) == original[c] for c in ['B','C'])
    assert store.claim_management_qualification('A')['ready'] and store.claim_management_qualification('B') is None


@pytest.mark.parametrize('concurrent', [False, True])
def test_non_claim_support_is_complete_and_new_support_blocks_commit(chain, concurrent):
    store, request, output, root = chain
    def add_support(ident):
        store.apply_plan(store.plan_package({'package_id':'support-'+ident,
            'observations':[{'observation_id':ident,'statement':'甲是假设。'}],
            'knowledge_relations':[{'relation_id':'R'+ident,'from_id':ident,'to_id':'EA',
                'relation_type':'supports','reason':'target evidence support'}]}))
    add_support('O')
    def opus(packet):
        assert {'observations/O','knowledge_relations/RO'} <= set(packet['denominator'])
        assert 'evidence_steps/EB' not in packet['before']
        if concurrent: add_support('NEW')
        return yes(packet)
    ports = SyntheticPorts(store,root,opus=opus)
    result = execute_claim_management(store,request,root=output,ports=ports)
    assert result['status'] == ('paused' if concurrent else 'completed'), result
    assert ports.calls == ['opus']
    assert bool((store.claim_management_qualification('A') or {}).get('ready')) != concurrent


def test_upstream_observation_cannot_import_another_sermon(chain):
    store,request,_,_=chain
    before={cm.source.key(*k):cm.model_state(v) for k,v in store.read_claim_context([('claims','A')]).items()}
    before.update({
        'observations/O':{'payload':{'observation_id':'O','source_fragment_ids':['FT']}},
        'knowledge_relations/RO':{'payload':{'relation_id':'RO','from_id':'O','to_id':'EA','relation_type':'supports'}},
        'source_fragments/FT':{'payload':{'fragment_id':'FT','source_id':'T'}},
        'source_documents/T':{'payload':{'source_id':'T'}}})
    with pytest.raises(ValueError,match='own source'):
        direct.scope(request,before)


@pytest.mark.parametrize('operation',['Update','Review'])
@pytest.mark.parametrize('category',['content','source_uncertain'])
@pytest.mark.parametrize('disposition',['resolved','persistent'])
def test_direct_scope_keeps_all_objections_in_existing_gpt_resolution_flow(action, monkeypatch, operation, category, disposition):
    from backend.tests import test_claim_management_execution_postgres as regression
    monkeypatch.setattr(cm,'DEFAULT_POLICY',cm.DEFAULT_POLICY.parent/'wang_claim_management_opus_only_v3.json')
    regression.test_all_objections_reach_gpt_before_escalation_including_readonly_context(
        action,operation,category,disposition)


def test_different_peer_scope_revalidates_only_with_unchanged_original_evidence(related, monkeypatch):
    from backend.tests.test_claim_qualification_reuse import qualify_original, prune_peer
    monkeypatch.setattr(cm,'DEFAULT_POLICY',cm.DEFAULT_POLICY.parent/'wang_claim_management_opus_only_v3.json')
    store=related[0]
    qualify_original(related)
    _,ports=prune_peer(related)
    assert store.claim_management_qualification('D')['ready']
    assert store.claim_management_qualification('C')['ready']
    assert ports.calls==['opus','gpt','opus']


@pytest.fixture
def shared_position(chain):
    store, request, output, root = chain
    claims = []
    for cid in ['A', 'C']:
        claim = deepcopy(store.get_record_state('claims', cid)['payload'])
        claim['opposed_position_ids'] = ['P']
        claims.append(claim)
    store.apply_plan(store.plan_package({'package_id': 'shared-position',
        'position_nodes': [{'position_id': 'P', 'title': '被反駁的立場'}],
        'claims': claims + [{'claim_id': 'D', 'statement': 'D', 'claim_type': 'exegesis',
                            'occurrences': [{'source_id': 'S', 'anchors': []}]}],
        'claim_relations': [relation('CD', 'C', 'D')]}))
    request.permissions.append(cm.source.Permission(collection='position_nodes', object_id='P', fields=['title']))
    bind(request, root, required=['position_nodes/P'])
    return store, request, output, root


def test_shared_position_includes_all_owners_without_their_other_claim_relations(shared_position):
    store, request, output, root = shared_position
    def opus(packet):
        assert {'claims/A', 'claims/C', 'evidence_steps/EC', 'position_nodes/P'} <= set(packet['denominator'])
        assert 'claims/C' in packet['claim_scope']['content_claim_keys']
        assert not {'claims/D', 'claim_relations/CD'} & set(packet['before'])
        return yes(packet)
    ports = SyntheticPorts(store, root, opus=opus)
    result = execute_claim_management(store, request, root=output, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['opus']
    assert store.claim_management_qualification('A')['ready']
    assert store.claim_management_qualification('C') is None


def test_new_shared_position_owner_during_review_blocks_commit(shared_position):
    store, request, output, root = shared_position
    def opus(packet):
        store.apply_plan(store.plan_package({'package_id': 'new-position-owner',
            'claims': [{'claim_id': 'NEW', 'statement': 'new owner', 'claim_type': 'exegesis',
                        'occurrences': [{'source_id': 'S', 'anchors': []}],
                        'opposed_position_ids': ['P']}]}))
        return yes(packet)
    ports = SyntheticPorts(store, root, opus=opus)
    result = execute_claim_management(store, request, root=output, ports=ports)
    assert result['status'] == 'paused' and result['reason'] == 'engineering_required', result
    assert ports.calls == ['opus']


@pytest.mark.parametrize('already_reversed', [False, True])
def test_maintained_evidence_relation_keeps_both_endpoints_after_reversal(chain, already_reversed):
    store, request, output, root = chain
    store.apply_plan(store.plan_package({'package_id': 'evidence-relation',
        'knowledge_relations': [{'relation_id': 'R', 'from_id': 'EA' if already_reversed else 'EB',
            'to_id': 'EB' if already_reversed else 'EA', 'relation_type': 'supports', 'reason': 'synthetic direction'}]}))
    request.permissions.append(cm.source.Permission(collection='knowledge_relations', object_id='R', fields=['from_id', 'to_id']))
    bind(request, root, proposal=not already_reversed, required=['knowledge_relations/R'])
    if already_reversed:
        # This is the post-commit projection of an already admitted request,
        # not admission of a new downstream-only maintenance operation.
        before = {cm.source.key(*k): cm.model_state(v) for k, v in store.read_claim_context([('claims', 'A')]).items()}
        assert {'evidence_steps/EA', 'evidence_steps/EB', 'knowledge_relations/R'} <= set(direct.scope(request, before)['record_keys'])
        return
    def proposal(packet):
        assert 'evidence_steps/EB' in packet['before']
        return {'changes': [{'record_key': 'knowledge_relations/R',
            'fields_json': json.dumps({'from_id': 'EA', 'to_id': 'EB'}),
            'reason': 'synthetic direction correction',
            'citations': [{'source_id': 'S', 'paragraph_key': 'S0001', 'exact_quote': '甲是假设，不是断言。'}]}],
            'findings': [], 'finding_resolutions': []}
    ports = SyntheticPorts(store, root, proposal=proposal, opus=yes)
    result = execute_claim_management(store, request, root=output, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == (['opus'] if already_reversed else ['gpt', 'opus'])
    assert store.claim_management_qualification('A')['ready']
    before = {cm.source.key(*k): cm.model_state(v) for k, v in store.read_claim_context([('claims', 'A')]).items()}
    assert {'evidence_steps/EA', 'evidence_steps/EB', 'knowledge_relations/R'} <= set(direct.scope(request, before)['record_keys'])


@pytest.mark.parametrize('fault', [None, 'postimage_drift', 'missing_checkpoint_authority'])
def test_applied_readback_recovery_never_repeats_models_or_apply(chain, monkeypatch, fault):
    from backend.pipeline.claim_review_recovery import recover_returned_review, VERSION
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    store, request, output, root = chain
    bind(request, root, proposal=True)
    def proposal(packet):
        return {'changes': [{'record_key': 'claims/A', 'fields_json': json.dumps({'statement': '甲是假设，不是断言。'}),
            'reason': 'synthetic correction',
            'citations': [{'source_id': 'S', 'paragraph_key': 'S0001', 'exact_quote': '甲是假设，不是断言。'}]}],
            'findings': [], 'finding_resolutions': []}
    ports = SyntheticPorts(store, root, proposal=proposal, opus=yes)
    record = store.record_claim_management_stage
    def interrupted(round_id, receipt):
        if json.loads(Path(receipt['path']).read_text())['stage'] == 'readback':
            raise ValueError('synthetic readback validator failure after commit')
        return record(round_id, receipt)
    monkeypatch.setattr(store, 'record_claim_management_stage', interrupted)
    result = execute_claim_management(store, request, root=output, ports=ports)
    assert result['status'] == 'paused', result
    parent_id = 'CMA-' + cm.sha256_json(request.action_id)
    parent = store.get_cvp_group(parent_id)
    rid = next(iter(parent['latest_rounds'].values()))
    saved = store.get_cvp_group(rid)
    assert saved['checkpoint'] == 'applied' and saved['change_set_ids']
    calls = deepcopy(parent['call_reservations'])
    old_readback = (output/rid/'readback.json').read_bytes()
    before = store.get_record_state('claims', 'A')
    if fault == 'postimage_drift':
        payload = deepcopy(before['payload']); payload['statement'] = 'concurrent edit'
        store.apply_plan(store.plan_package({'package_id': 'drift-after-commit', 'claims': [payload]}))
    direction = root/'applied-readback-recovery.json'
    direction.write_text(json.dumps({'schema_version': VERSION, 'round_id': rid,
        'authorized_by': 'owner', 'authorization_quote': 'synthetic validator repair and zero-call readback',
        'human_content_approval': False, 'prior_metadata_sha256': cm.sha256_json({k:v for k,v in saved.items() if k not in {'run_id','status','error'}}),
        'resume_applied_readback': fault != 'missing_checkpoint_authority'}))
    if fault:
        with pytest.raises((ValueError, ChangeSetConflict)):
            recover_returned_review(store, rid, authorization=binding(direction))
        assert store.get_cvp_group(rid) == saved
        return
    recovered = recover_returned_review(store, rid, authorization=binding(direction))
    assert store.get_cvp_group(rid)['checkpoint'] == 'applied'
    monkeypatch.setattr(store, 'record_claim_management_stage', record)
    def no_apply(*args, **kwargs):
        raise AssertionError('applied recovery must not call apply_plan')
    monkeypatch.setattr(store, 'apply_plan', no_apply)
    result = execute_claim_management(store, request, root=output, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['gpt', 'opus']
    assert store.get_cvp_group(parent_id)['call_reservations'] == calls
    assert store.get_cvp_group(rid)['change_set_ids'] == saved['change_set_ids']
    assert store.get_record_state('claims', 'A') == before
    assert (output/rid/'readback.json').read_bytes() == old_readback
    assert Path(recovered, 'readback.json').is_file()
    assert store.claim_management_qualification('A')['ready']


@pytest.mark.parametrize('enabled',[False,True])
def test_declared_owner_is_resolvable_readonly_context_without_a_claim_frontier(chain,enabled):
    store,request,_,root=chain
    from backend.pipeline.claim_review_units import CONTRACT
    p=cm.Policy.model_validate_json((cm.DEFAULT_POLICY.parent/'wang_claim_management_joint_update_v8.json').read_text());p.review_unit_contract=CONTRACT;p.joint_update_max_targets=10
    pf=root/'owner-context-policy.json';pf.write_text(p.model_dump_json());request.policy=cm.source.FileBinding.model_validate(binding(pf))
    a=store.get_record_state('evidence_steps','EA')['payload']
    ec=store.get_record_state('evidence_steps','EC')['payload']
    store.apply_plan(store.plan_package({'package_id':'explicit-readonly-evidence-owner',
        'evidence_steps':[dict(a,produced_claim_ids=['A','C']),dict(ec,produced_claim_ids=['C','D']),{'evidence_step_id':'ED','statement':'甲是假设。','produced_claim_ids':['D']}],
        'claims':[{'claim_id':'D','statement':'甲是假设。','claim_type':'exegesis','evidence_step_ids':['ED'],
            'occurrences':[{'source_id':'S','anchors':[]}]}], 'claim_relations':[relation('CD','C','D')]}))
    request.permissions=[p for p in request.permissions if p.key in {'claims/A','claim_relations/AB'}]
    authorize(request,{'required_maintenance_keys':[],'include_evidence_owner_context':enabled,'evidence_owner_context_keys':['claims/C']})
    before={cm.source.key(*k):cm.model_state(v) for k,v in store.read_claim_context([('claims','A')]).items()}
    selected=direct.scope(request,before)
    assert ('claims/C' in selected['record_keys'])==enabled
    assert ('evidence_steps/EC' in selected['record_keys'])==enabled
    assert selected['content_claim_keys']==['claims/A']
    assert not {'claims/D','claim_relations/BC','claim_relations/CD'} & set(selected['record_keys'])
    if enabled:
        assert 'claims/C' not in selected['writable_record_keys']
        assert 'evidence_steps/EC' not in selected['writable_record_keys']
        assert 'claims/C' not in direct.scope(request,before,include_write_context=False)['record_keys']
        projected={k:before[k] for k in selected['record_keys']}
        assert direct.scope(request,projected)['record_keys']==selected['record_keys']
        candidate=cm.existing_candidate(projected)
        row=dict(before['claim_relations/AB']['payload'],target_id='C')
        candidate.records=[cm.source.Row(collection='claim_relations',object_id='AB',payload=row)]
        next(d for d in candidate.dispositions if d.record_key=='claim_relations/AB').action='update'
        post=direct.scope(request,projected,candidate)
        assert 'claims/C' in post['neighbour_claim_keys'] and post['content_claim_keys']==['claims/A']
        assert not {'claims/D','claim_relations/CD'} & set(post['record_keys'])


@pytest.mark.parametrize('write_reference',[False,True])
@pytest.mark.parametrize('private_owner',[False,True])
def test_provided_owner_can_receive_relation_without_write_or_new_qualification(chain,write_reference,private_owner):
    from backend.pipeline.claim_review_units import CONTRACT
    from backend.tests.test_claim_management_intake import pass_review
    store,request,execution,root=chain
    ea=store.get_record_state('evidence_steps','EA')['payload'];cl=store.get_record_state('claims','C')['payload']
    store.apply_plan(store.plan_package({'package_id':'same-source-provided-carrier',
        'source_fragments':[{'fragment_id':'FA','source_id':'S','paragraph_key':'S0001','verbatim_excerpt':'甲是假设，不是断言。'}],
        'evidence_steps':[dict(ea,produced_claim_ids=['A','C'],source_fragment_ids=['FA'],speaker='professor',
            stance='asserted',support_eligibility='eligible_candidate')],
        'claims':[dict(cl,evidence_step_ids=['EA','EC'])]}))
    if private_owner:
        ec=store.get_record_state('evidence_steps','EC')['payload']
        store.apply_plan(store.plan_package({'package_id':'private-readonly-owner-is-not-a-second-frontier',
            'evidence_steps':[dict(ec,produced_claim_ids=['C','D']),{'evidence_step_id':'ED',
                'statement':'甲是假设。','source_fragment_ids':['FA'],'produced_claim_ids':['D']}],
            'claims':[{'claim_id':'D','statement':'甲是假设。','claim_type':'exegesis',
                'evidence_step_ids':['ED'],'occurrences':[{'source_id':'S','anchors':[]}]}]}))
    p=cm.Policy.model_validate_json((cm.DEFAULT_POLICY.parent/'wang_claim_management_joint_update_v8.json').read_text());p.review_unit_contract=CONTRACT;p.joint_update_max_targets=10;p.initial_candidate='proposal'
    pf=root/'provided-carrier-policy.json';pf.write_text(p.model_dump_json());request.policy=cm.source.FileBinding.model_validate(binding(pf))
    request.permissions=[cm.source.Permission(collection='claims',object_id='A',fields=['statement']),
        cm.source.Permission(collection='claim_relations',object_id='AB',fields=['target_id','reason'])]
    authorize(request,{'required_maintenance_keys':[],'include_existing_evidence_owners':True,'include_evidence_owner_context':True,'evidence_owner_context_keys':['claims/C']})
    initial=store.get_record_state('claims','C');ec=store.get_record_state('evidence_steps','EC')
    cite={'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}
    def propose(packet):
        assert 'claims/C' in packet['before'] and 'evidence_steps/EC' in packet['before']
        assert 'claims/C' not in packet['claim_scope']['content_claim_keys']
        changes=[{'record_key':'claim_relations/AB','fields_json':json.dumps({'target_id':'C','reason':'synthetic support to the active carrier'}),
            'reason':'use the explicit provided carrier','citations':[cite]}]
        if write_reference:changes.append({'record_key':'claims/C','fields_json':json.dumps({'statement':'甲是假设，不是断言。'}),
            'reason':'not authorised by reference presence','citations':[cite]})
        return {'changes':changes,'creations':[],'retirements':[],'findings':[],'finding_resolutions':[]}
    def review(packet):
        assert set(packet['own_evidence_bindings'])=={'claims/A'}
        assert 'claims/C' in packet['claim_scope']['neighbour_claim_keys']
        assert 'claim_relations/BC' not in packet['before']
        return pass_review(packet)
    ports=SyntheticPorts(store,execution,proposal=propose,opus=review)
    result=execute_claim_management(store,request,root=execution,ports=ports)
    if write_reference:
        assert result['status']=='paused' and any(text in result['detail'] for text in ['read-only','hidden Claim']),result
        assert ports.calls==['gpt']
    else:
        assert result['status']=='completed',result
        assert ports.calls==['gpt','opus']
        assert store.get_record_state('claim_relations','AB')['payload']['target_id']=='C'
        assert store.claim_management_qualification('A')['ready']
    assert store.get_record_state('claims','C')==initial and store.get_record_state('evidence_steps','EC')==ec
    assert store.claim_management_qualification('C') is None


@pytest.mark.parametrize('late_relation',[False,True])
def test_postimage_check_does_not_reopen_private_reference_owners_but_detects_new_root_edges(chain,late_relation):
    from backend.pipeline.claim_review_units import CONTRACT
    from backend.pipeline.cvp_sql_commit import pins
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    store,request,_,root=chain
    policy=cm.Policy.model_validate_json((cm.DEFAULT_POLICY.parent/'wang_claim_management_joint_update_v8.json').read_text())
    policy.review_unit_contract=CONTRACT;policy.joint_update_max_targets=10
    pp=root/'postimage-policy.json';pp.write_text(policy.model_dump_json());request.policy=cm.source.FileBinding.model_validate(binding(pp))
    ea=store.get_record_state('evidence_steps','EA')['payload'];ec=store.get_record_state('evidence_steps','EC')['payload']
    store.apply_plan(store.plan_package({'package_id':'postimage-readonly-private-owner',
        'evidence_steps':[dict(ea,produced_claim_ids=['A','C']),dict(ec,produced_claim_ids=['C','D'])],
        'claims':[{'claim_id':'D','statement':'甲是假设。','claim_type':'exegesis',
            'occurrences':[{'source_id':'S','anchors':[]}]}],
        'claim_relations':[dict(store.get_record_state('claim_relations','AB')['payload'],target_id='C')]}))
    request.permissions=[p for p in request.permissions if p.key in {'claims/A','claim_relations/AB'}]
    authorize(request,{'required_maintenance_keys':[],'include_evidence_owner_context':True})
    records=store.read_claim_context([('claims','A')])
    states={cm.source.key(*k):cm.model_state(v) for k,v in records.items()}
    # The approved frame includes C's private EC as reference material but
    # never assigns a further review to its other owner D.
    own=set(direct.scope(request,states,include_write_context=False)['record_keys']) | {'claims/C','evidence_steps/EC'}
    protected={k:v for k,v in records.items() if cm.source.key(*k) in own}
    assert ('claims','D') not in protected
    if late_relation:
        store.apply_plan(store.plan_package({'package_id':'unreviewed-root-edge',
            'claim_relations':[relation('LATE','A','D')]}))
    with store.connect() as conn,conn.cursor() as cursor:
        if late_relation:
            with pytest.raises(ChangeSetConflict,match='omitted a current neighbour'):
                store._assert_claim_management_scope(cursor,pins(protected),records=protected,request=request,postimage=True)
        else:
            store._assert_claim_management_scope(cursor,pins(protected),records=protected,request=request,postimage=True)


def test_question_only_update_never_invents_write_impacts_at_reference_private_evidence(chain):
    from backend.pipeline.claim_review_units import CONTRACT
    from backend.tests.test_claim_management_intake import pass_review
    store,request,execution,root=chain
    ea=store.get_record_state('evidence_steps','EA')['payload'];ec=store.get_record_state('evidence_steps','EC')['payload']
    store.apply_plan(store.plan_package({'package_id':'question-only-private-reference',
        'source_fragments':[{'fragment_id':'FA','source_id':'S','paragraph_key':'S0001','verbatim_excerpt':'甲是假设，不是断言。'}],
        'evidence_steps':[dict(ea,source_fragment_ids=['FA'],produced_claim_ids=['A','C'],
            speaker='professor',stance='asserted',support_eligibility='eligible_candidate'),
            dict(ec,source_fragment_ids=['FA'],produced_claim_ids=['C','D'])],
        'claims':[{'claim_id':'D','statement':'甲是假设。','claim_type':'exegesis','occurrences':[{'source_id':'S','anchors':[]}]}],
        'questions':[{'question_id':'Q','text':'甲是否是斷言？','source_fragment_ids':['FA'],
            'answer_state':'unanswered','answer_claim_ids':[]}]}))
    policy=cm.Policy.model_validate_json((cm.DEFAULT_POLICY.parent/'wang_claim_management_joint_update_v8.json').read_text())
    policy.review_unit_contract=CONTRACT;policy.joint_update_max_targets=10;policy.initial_candidate='proposal'
    pp=root/'question-private-policy.json';pp.write_text(policy.model_dump_json());request.policy=cm.source.FileBinding.model_validate(binding(pp))
    request.permissions=[cm.source.Permission(collection='questions',object_id='Q',fields=['answer_state','answer_claim_ids'])]
    authorize(request,{'required_maintenance_keys':['questions/Q'],'include_evidence_owner_context':True,
        'evidence_owner_context_keys':['claims/C']})
    before={k:store.get_record_state(*cm.source.split_key(k)) for k in ['claims/A','claims/C','claims/D','evidence_steps/EA','evidence_steps/EC']}
    cite={'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}
    def proposal(packet):
        assert 'evidence_steps/EC' in packet['before'] and 'claims/D' not in packet['before']
        assert packet['claim_scope']['content_claim_keys']==['claims/A']
        return {'changes':[{'record_key':'questions/Q','fields_json':json.dumps({'answer_state':'answered','answer_claim_ids':['A']}),
            'reason':'link the existing direct answer','citations':[cite]}],'creations':[],'retirements':[],
            'findings':[],'finding_resolutions':[]}
    ports=SyntheticPorts(store,execution,proposal=proposal,opus=pass_review)
    result=execute_claim_management(store,request,root=execution,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['gpt','opus']
    assert store.claim_management_qualification('A')['ready']
    assert store.get_record_state('questions','Q')['payload']['answer_claim_ids']==['A']
    assert all(store.get_record_state(*cm.source.split_key(k))==v for k,v in before.items())
