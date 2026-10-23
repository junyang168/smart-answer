"""A judgement the professor asserts must not live only inside another Claim's Evidence.

#439 found 「耶穌每次講人子都加定冠詞」 recorded only as an EvidenceStep under the
「人子強調神性」 Claim, with 「每次」 dropped from the step. No Claim carried it, so
the viewpoint built on it could only link the bare assertion. Update repairs this
inside its own source: it creates the sibling Claim, which takes over the
existing step on both sides, and restores the qualifier. Synthetic model
replies only; no provider calls.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.pipeline import claim_management as cm
from backend.pipeline import source_reconciliation as c
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.source_reconciliation_runner import binding
from backend.pipeline.source_reconciliation_runtime import claim_management_model_adapter
from backend.tests.test_claim_management import policy
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts, operation_request, passing
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres  # noqa: F401

TEXT = '你看耶穌每次講人子都加上一個定冠詞。耶穌從來沒有說a son of man。耶穌稱祂自己是人子，乃是說我就是神。'
QUOTE = '你看耶穌每次講人子都加上一個定冠詞。'


def cite(quote=QUOTE):
    return cm.Citation(source_id='S', paragraph_key='S0001', exact_quote=quote)


@pytest.fixture
def case():
    sources = {'S': {'paragraphs': {'S0001': {'text': TEXT}}}}
    before = {
        'claims/C': {'payload': {'claim_id': 'C', 'statement': '耶穌稱自己為人子，是在表明自己就是神。', 'claim_type': 'exegesis',
            'occurrences': [{'source_id': 'S', 'anchors': []}], 'evidence_step_ids': ['E'], 'revision': 1,
            'review_status': 'candidate'}, 'retired': False},
        'evidence_steps/E': {'payload': {'evidence_step_id': 'E', 'statement': '耶穌所說的是帶定冠詞的 the Son of Man。',
            'produced_claim_ids': ['C'], 'source_fragment_ids': ['F']}, 'retired': False},
        'source_fragments/F': {'payload': {'fragment_id': 'F', 'source_id': 'S', 'paragraph_key': 'S0001',
            'verbatim_excerpt': QUOTE}, 'retired': False},
        'source_documents/S': {'payload': {'source_id': 'S'}, 'retired': False}}
    b = {'path': '/explicit-test-binding', 'file_sha256': 'a' * 64}
    request = cm.Request(action_id='hidden-judgement', operation='Update', allowed_operations=['Update', 'Create'],
        target_keys=['claims/C'], source_bindings={'S': b}, authorization=b, policy=b,
        permissions=[c.Permission(collection='evidence_steps', object_id='E', fields=['statement', 'produced_claim_ids'])],
        creation_permissions=[cm.CreationPermission(collection='claims', source_id='S',
            fields=['statement', 'claim_type', 'occurrences', 'evidence_step_ids'])])
    return request, before, sources


def sibling(before, *, reciprocal=True, restore_qualifier=True):
    new = {'claim_id': 'N', 'statement': '耶穌每次說「人子」都帶定冠詞。', 'claim_type': 'exegesis',
           'occurrences': [{'source_id': 'S', 'anchors': []}], 'evidence_step_ids': ['E'], 'review_status': 'candidate'}
    step = deepcopy(before['evidence_steps/E']['payload'])
    if restore_qualifier:
        step['statement'] = '耶穌每次說「人子」都帶定冠詞 the Son of Man。'
    if reciprocal:
        step['produced_claim_ids'] = ['C', 'N']
    records = [c.Row(collection='claims', object_id='N', payload=new)]
    dispositions = [cm.ObjectDisposition(record_key=k, action='retain', reason='unchanged')
                    for k in before if k != 'evidence_steps/E']
    if step != before['evidence_steps/E']['payload']:
        records.append(c.Row(collection='evidence_steps', object_id='E', payload=step))
        dispositions.append(cm.ObjectDisposition(record_key='evidence_steps/E', action='update',
                                                 reason='restore his qualifier; the new Claim owns this step', citations=[cite()]))
    else:
        dispositions.append(cm.ObjectDisposition(record_key='evidence_steps/E', action='retain', reason='unchanged'))
    dispositions.append(cm.ObjectDisposition(record_key='claims/N', action='create',
                                             reason='his separately asserted judgement', citations=[cite()]))
    return cm.Candidate(records=records, dispositions=dispositions)


def test_update_creates_the_sibling_claim_on_the_existing_step_and_keeps_the_target(case):
    request, before, sources = case
    post, writes = cm.validate_candidate(request, before, sibling(before), sources)
    assert {k: v['action'] for k, v in writes.items()} == {'claims/N': 'create', 'evidence_steps/E': 'update'}
    assert post['claims/C'] == before['claims/C']['payload']
    assert post['claims/N']['evidence_step_ids'] == ['E'] and post['evidence_steps/E']['produced_claim_ids'] == ['C', 'N']
    assert '每次' in post['evidence_steps/E']['statement']
    # The new Claim and the step it now owns are reviewed in the same round.
    assert {'claims/N', 'evidence_steps/E', 'claims/C'} <= set(cm.audit_keys(request, before, sibling(before)))


def test_one_sided_ownership_is_refused_in_either_direction(case):
    request, before, sources = case
    with pytest.raises(ValueError, match='reciprocal: N<->E'):
        cm.validate_candidate(request, before, sibling(before, reciprocal=False), sources)
    candidate = sibling(before)
    candidate.records[0].payload['evidence_step_ids'] = []
    with pytest.raises(ValueError, match='reciprocal: N<->E'):
        cm.validate_candidate(request, before, candidate, sources)


def test_creating_the_sibling_needs_create_and_step_write_permission(case):
    request, before, sources = case
    for update, match in [({'creation_permissions': []}, 'creation permission'),
                          ({'allowed_operations': ['Update']}, 'exceeds authorized operations'),
                          ({'permissions': []}, 'read-only owner')]:
        with pytest.raises(ValueError, match=match):
            cm.validate_candidate(request.model_copy(update=update), before, sibling(before), sources)


def test_legacy_one_sided_pairs_elsewhere_do_not_block_an_unrelated_write(case):
    request, before, sources = case
    before['claims/OLD'] = {'payload': {'claim_id': 'OLD', 'statement': '舊的單向連接', 'claim_type': 'exegesis',
        'occurrences': [{'source_id': 'S', 'anchors': []}], 'evidence_step_ids': ['E']}, 'retired': False}
    candidate = sibling(before)
    # OLD<->E was already one-sided; this candidate writes E but does not create
    # that pair, so it stays the reciprocity repair's work.
    cm.validate_candidate(request, before, candidate, sources)


def test_retiring_a_claim_does_not_count_its_stale_back_reference(case):
    request, before, sources = case
    request = request.model_copy(update={'allowed_operations': ['Update', 'Retire']})
    candidate = cm.Candidate(records=[], dispositions=[
        cm.ObjectDisposition(record_key=k, action='retain', reason='unchanged') for k in before if k != 'claims/C'] + [
        cm.ObjectDisposition(record_key='claims/C', action='retire', reason='synthetic', citations=[cite()])])
    request.permissions.append(c.Permission(collection='claims', object_id='C', fields=['statement']))
    assert cm.validate_candidate(request, before, candidate, sources)[1]['claims/C']['action'] == 'retire'


@pytest.mark.parametrize('name', ['claim_management_review_v2.md', 'claim_management_proposal_v2.md',
                                  'claim_management_audit_v2.md', 'claim_management_audit_joint_v1.md',
                                  'claim_management_compact_v1.md', 'claim_management_compact_v2.md'])
def test_every_reviewer_and_proposer_prompt_names_the_hidden_judgement_rule(name):
    text = (Path(cm.__file__).parent / 'prompts' / name).read_text()
    assert 'independent_judgement_without_claim' in text
    assert '「每次」「從來沒有」「唯一」' in text
    if 'compact' in name:
        assert '联合 Update 没有创建权限' in text
    if 'proposal' in name:
        assert 'produced_claim_ids 加入新 Claim' in text


@pytest.mark.parametrize('role', ['gpt', 'opus'])
def test_single_target_policy_composes_the_rule_into_the_live_prompt(role):
    p = cm.Policy.model_validate_json(cm.DEFAULT_POLICY.read_text())
    assert p.joint_update_contract is None
    prompt = claim_management_model_adapter(role, p)._base_prompt
    assert 'independent_judgement_without_claim' in prompt
    assert ('新建一条同源 Claim' in prompt) == (role == 'gpt')


def seed_bundled(store):
    store.apply_plan(store.plan_package({'package_id': 'bundled-judgement',
        'claims': [{'claim_id': 'C', 'statement': '甲是假设。', 'claim_type': 'exegesis',
                    'occurrences': [{'source_id': 'S', 'anchors': []}], 'evidence_step_ids': ['E']}],
        'evidence_steps': [{'evidence_step_id': 'E', 'statement': '甲是假设。', 'produced_claim_ids': ['C']}]}))


def hidden_judgement_request(action):
    request, root = operation_request(action, 'Update', ['C'])
    request.allowed_operations = ['Update', 'Create']
    request.permissions = [cm.source.Permission(collection='evidence_steps', object_id='E',
                                                fields=['statement', 'produced_claim_ids'])]
    request.creation_permissions = [cm.CreationPermission(collection='claims', source_id='S',
        fields=sorted({'occurrences'} | set(cm.KNOWLEDGE_COLLECTIONS['claims'][0].model_fields)))]
    path = Path(request.authorization.path)
    direction = json.loads(path.read_text())
    direction['request'] = request.model_dump(mode='json', exclude={'authorization', 'policy'})
    path.write_text(json.dumps(direction))
    request.authorization = cm.source.FileBinding.model_validate(binding(path))
    return cm.Request.model_validate(request.model_dump(mode='json')), root


def flag_hidden_judgement(packet):
    report = passing(packet)
    for verdict in report['verdicts']:
        if verdict['record_key'] == 'evidence_steps/E':
            verdict.update(approved=False, problems=['separately asserted judgement has no Claim'])
    report['findings'] = [{'target_key': 'evidence_steps/E', 'component': 'independent_judgement_without_claim',
        'category': 'mapping', 'citations': [{'source_id': 'S', 'paragraph_key': 'S0001', 'exact_quote': '甲是假设，不是断言。'}],
        'reason': '「不是断言」是教授单独断言的判断，只藏在 E 中', 'acceptance_condition': '新建同源 Claim 并与 E 双向归属'}]
    return report


def propose_sibling(packet, *, reciprocal=True):
    result = cm.existing_candidate(packet['before'])
    step = deepcopy(packet['before']['evidence_steps/E']['payload'])
    step['statement'] = '甲是假设，不是断言。'
    if reciprocal:
        step['produced_claim_ids'] = ['C', 'N']
    quote = [cm.Citation(source_id='S', paragraph_key='S0001', exact_quote='甲是假设，不是断言。')]
    result.records = [cm.source.Row(collection='evidence_steps', object_id='E', payload=step),
                      cm.source.Row(collection='claims', object_id='N', payload={'claim_id': 'N', 'statement': '甲不是断言。',
                          'claim_type': 'exegesis', 'occurrences': [{'source_id': 'S', 'anchors': []}], 'evidence_step_ids': ['E']})]
    result.dispositions = [d for d in result.dispositions if d.record_key != 'evidence_steps/E'] + [
        cm.ObjectDisposition(record_key='evidence_steps/E', action='update', reason='restore qualifier, add owner', citations=quote),
        cm.ObjectDisposition(record_key='claims/N', action='create', reason='separately asserted judgement', citations=quote)]
    return result.model_dump(mode='json')


def test_flagged_hidden_judgement_is_repaired_in_one_committed_round(action):
    store, _, _, _, tmp_path, _ = action
    seed_bundled(store)
    request, root = hidden_judgement_request(action)
    ports = SyntheticPorts(store, tmp_path, proposal=propose_sibling,
                           opus=lambda p: flag_hidden_judgement(p) if p['round_number'] == 0 else passing(p))
    result = execute_claim_management(store, request, root=root, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['opus', 'gpt', 'opus', 'gemini']
    new = store.get_record_state('claims', 'N')['payload']
    step = store.get_record_state('evidence_steps', 'E')['payload']
    assert new['evidence_step_ids'] == ['E'] and step['produced_claim_ids'] == ['C', 'N']
    assert step['statement'] == '甲是假设，不是断言。'
    assert store.get_record_state('claims', 'C')['revision'] == 1
    assert store.claim_management_qualification('N')['ready'] is True
    assert execute_claim_management(store, request, root=root, ports=ports) == result


def test_one_sided_sibling_proposal_never_reaches_the_database(action):
    store, _, _, _, tmp_path, _ = action
    seed_bundled(store)
    request, root = hidden_judgement_request(action)
    ports = SyntheticPorts(store, tmp_path, proposal=lambda p: propose_sibling(p, reciprocal=False),
                           opus=flag_hidden_judgement)
    result = execute_claim_management(store, request, root=root, ports=ports)
    assert result['status'] == 'paused' and 'reciprocal: N<->E' in result['detail'], result
    assert store.get_record_state('claims', 'N') is None
    assert store.get_record_state('evidence_steps', 'E')['payload']['produced_claim_ids'] == ['C']


@pytest.mark.parametrize('name', ['detailed_knowledge_extraction.md', 'detailed_notes_knowledge_extraction.md'])
def test_extraction_keeps_separately_asserted_judgements_as_their_own_claims(name):
    text = (Path(cm.__file__).parent / 'prompts' / name).read_text()
    assert '最小判断规则' in text and '`produced_claim_ids` 同时列出两条 Claim' in text
    assert '「每次」「從來沒有」「唯一」「都」' in text


def test_coverage_adjudication_does_not_count_a_hidden_judgement_as_covered():
    from backend.pipeline import observation_coverage_adjudication as oca
    text = oca.PROMPT_PATH.read_text()
    assert '這種判斷只作為另一條結論的根據，**不算**覆蓋' in text
    assert '丟掉了，也**不算**覆蓋' in text


def capacity_refused(ports):
    factory = ports.claim_management_adapter

    def adapter(role, policy):
        result = factory(role, policy)
        if role == 'gpt':
            def refused(packet):
                ports.calls.append('gpt')
                result._client.last_raw_response = {'last_message': None, 'stdout': '',
                    'stderr': 'ERROR: ' + cm.CAPACITY_REJECTION + '\nERROR: ' + cm.CAPACITY_REJECTION}
                raise RuntimeError('synthetic capacity refusal')
            result.generate = refused
        return result
    ports.claim_management_adapter = adapter


def successor(action, previous_request, previous):
    request, root = hidden_judgement_request(action)
    request.predecessor_action_id = previous_request.action_id
    path = Path(request.authorization.path)
    direction = json.loads(path.read_text())
    direction['request'] = request.model_dump(mode='json', exclude={'authorization', 'policy'})
    path.write_text(json.dumps(direction))
    request.authorization = cm.source.FileBinding.model_validate(binding(path))
    return cm.Request.model_validate(request.model_dump(mode='json')), root


def test_capacity_refusal_continues_the_sibling_repair_with_the_same_create_grants(action):
    store, _, _, _, tmp_path, _ = action
    seed_bundled(store)
    request, root = hidden_judgement_request(action)
    ports = SyntheticPorts(store, tmp_path, proposal=propose_sibling, opus=flag_hidden_judgement)
    capacity_refused(ports)
    assert execute_claim_management(store, request, root=root, ports=ports)['reason'] == 'model_failed'
    assert ports.calls == ['opus', 'gpt']
    previous = store.get_cvp_group('CMA-' + cm.sha256_json(request.action_id))
    following, following_root = successor(action, request, previous)
    remaining = cm.Policy.model_validate(previous['policy'])
    remaining.budget.opus_calls -= 1
    proof = cm.continuation_budget(previous, following, remaining)
    # The refused call generated nothing, so it is not spent.
    assert proof['spent_generations'] == {'gpt': 0, 'opus': 1, 'gemini': 0}
    assert [v['kind'] for v in proof['pre_generation_schema_rejections'].values()] == ['capacity']
    # A successor may not widen what it can create.
    wider = following.model_copy(deep=True)
    wider.creation_permissions.append(cm.CreationPermission(collection='evidence_steps', source_id='S', fields=['statement']))
    with pytest.raises(ValueError, match='structural writes'):
        cm.continuation_budget(previous, wider, remaining)
    policy_file = tmp_path / 'remaining-policy.json'
    policy_file.write_text(remaining.model_dump_json())
    following.policy = cm.source.FileBinding.model_validate(binding(policy_file))
    path = Path(following.authorization.path)
    direction = json.loads(path.read_text())
    direction['policy'] = following.policy.model_dump(mode='json')
    path.write_text(json.dumps(direction))
    following.authorization = cm.source.FileBinding.model_validate(binding(path))
    resumed = SyntheticPorts(store, tmp_path, proposal=propose_sibling, opus=passing)
    result = execute_claim_management(store, following, root=following_root, ports=resumed)
    assert result['status'] == 'completed', result
    assert resumed.calls == ['gpt', 'opus', 'gemini']
    assert store.get_record_state('claims', 'N')['payload']['evidence_step_ids'] == ['E']
    assert store.get_record_state('evidence_steps', 'E')['payload']['produced_claim_ids'] == ['C', 'N']
    assert execute_claim_management(store, request, root=root, ports=ports)['reason'] == 'continued_in_linked_action'


@pytest.mark.parametrize('raw', [
    {'last_message': 'partial', 'stdout': '', 'stderr': 'ERROR: ' + cm.CAPACITY_REJECTION},
    {'last_message': None, 'stdout': '{"partial": true}', 'stderr': 'ERROR: ' + cm.CAPACITY_REJECTION},
    {'last_message': None, 'stdout': '', 'stderr': 'ERROR: something else went wrong'},
    {'last_message': None, 'stdout': '', 'stderr': ''},
    {'last_message': None, 'stdout': '', 'stderr': 'ERROR: ' + cm.CAPACITY_REJECTION + '\nERROR: '
        + json.dumps({'status': 400, 'error': {'code': 'invalid_json_schema', 'param': 'text.format.schema'}})},
])
def test_only_a_clean_refusal_counts_as_generating_nothing(raw):
    assert cm.pre_generation_rejection(raw) is None


def test_clean_refusals_are_recognised():
    schema = json.dumps({'status': 400, 'error': {'code': 'invalid_json_schema', 'param': 'text.format.schema'}})
    assert cm.pre_generation_rejection({'stdout': '', 'stderr': 'ERROR: ' + schema}) == 'schema'
    assert cm.pre_generation_rejection({'stdout': '', 'stderr': 'noise\nERROR: ' + cm.CAPACITY_REJECTION}) == 'capacity'
