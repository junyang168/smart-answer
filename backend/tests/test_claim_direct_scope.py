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
