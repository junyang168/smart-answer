"""New Claim layer: updated Evidence, dedup, reciprocal links and atomic SQL."""
from copy import deepcopy
import json

import pytest

from backend.pipeline import claim_refresh as c, evidence_refresh as e
from backend.tests.test_evidence_refresh import sample
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres


def answer(request, selected):
    name, spec = next(iter(request['questions'].items()))
    return {'model': e.MODEL, 'answers': {name: {'type': 'choice', 'choice': selected,
        'probabilities': {k: float(k == selected) for k in spec['criteria']}}}}


class Ports:
    identity = {'jev': e.MODEL, 'gpt': {'backend': 'test', 'model': 'test', 'effort': 'medium'}}

    def __init__(self, *, create=False, repair=True, proposal=None, equivalent=False, before_reply=None):
        self.create, self.fix = create, repair
        self.proposal, self.equivalent, self.before_reply = proposal, equivalent, before_reply
        self.calls = []
        self.judgments = []

    def judge(self, request):
        name = next(iter(request['questions']))
        self.judgments.append(request)
        self.calls.append('jev:' + name)
        selected = ('no' if name == 'unsupported_part_exists' else
                    'yes' if name == 'support' and self.fix else
                    'yes' if name == 'missing' and self.create else
                    next(k for k in request['questions'][name]['criteria'] if k.startswith('K'))
                    if name == 'identity' and self.equivalent and request['state']['candidate'] != '沒有聽從祂的話，顯明沒有真正相信祂。' else
                    'NONE' if name == 'identity' else
                    'none' if name == 'relation' else
                    'yes' if name in {'covered','promotion_support'} else 'no')
        return answer(request, selected)

    def repair(self, request):
        self.calls.append('gpt')
        self.last_request = request
        if self.before_reply:
            self.before_reply()
        if self.proposal:
            return self.proposal(request)
        if 'relation_reason_jobs' in request['input']:
            return {'reasons': [{'job_id': j['job_id'], 'action': 'write',
                'reason': '真正相信包括聽從，因此未聽從顯明沒有真正相信。'}
                for j in request['input']['relation_reason_jobs']]}
        updates = [{'claim_id': f['claim_id'], 'action': 'repair',
            'statement': '沒有聽從祂的話，顯明沒有真正相信祂。',
            'evidence_step_ids': ['E'], 'scripture_refs': [], 'reason': '保留原文的行為判準。'}
            for f in request['input']['claim_findings']]
        new = [{'statement': '不知道神的旨意，就無法遵行神的旨意。',
            'evidence_step_ids': ['E'], 'scripture_refs': [], 'answer_question_ids': ['Q'],
            'reason': '原文直接回答如何能遵行，不把後續相信的結論當成這項判斷。'}] if self.create else []
        return {'updates': updates, 'new_claims': new, 'needs_more_creation_space': False,
                'judgment_dispositions': [{'evidence_id':f['evidence_id'],'action':'new_claim' if self.create else 'retain_evidence',
                    'claim_ids':[],'reason':'保留論證角色，必要才獨立保存。'} for f in request['input']['missing_judgment_findings']]}


@pytest.fixture
def group(sample):
    store, root, source = sample
    evidence = deepcopy(store.get_record_state('evidence_steps', 'E')['payload'])
    evidence['statement'] = '沒有聽從祂的話，顯明沒有真正相信祂。'
    claim = deepcopy(store.get_record_state('claims', 'C')['payload'])
    claim.update(statement='只要知道神旨意就已經真正相信祂。', attribution='professor')
    store.apply_plan(store.plan_package({'package_id': 'claim-group-fixture',
        'evidence_steps': [evidence], 'claims': [claim], 'questions': [{
            'question_id': 'Q', 'text': '不知道神旨意，能遵行嗎？', 'source_fragment_id': 'F',
            'answer_claim_ids': ['C'], 'answer_state': 'answered'}]}))
    return store, root, source


def test_new_layers_repair_and_create_without_old_executor(group, monkeypatch):
    store, root, _ = group
    from backend.pipeline import cvp_sql_actions
    monkeypatch.setattr(cvp_sql_actions, 'execute_claim_management', lambda *a, **k: pytest.fail('old executor'))
    ports = Ports(create=True)
    result = c.run(store, ['C'], root, ports, apply=True)
    assert result['status'] == 'applied' and len(result['new_claim_ids']) == 1
    new = result['new_claim_ids'][0]
    assert store.get_record_state('claims', 'C')['payload']['statement'] == '沒有聽從祂的話，顯明沒有真正相信祂。'
    assert store.get_record_state('claims', new)['payload']['evidence_step_ids'] == ['E']
    assert set(store.get_record_state('evidence_steps', 'E')['payload']['produced_claim_ids']) == {'C', new}
    assert store.get_record_state('questions', 'Q')['payload']['answer_claim_ids'] == ['C', new]
    assert ports.calls.count('gpt') == 1
    count = len(ports.calls)
    assert c.run(store, ['C'], root, ports, apply=True)['applied']
    assert len(ports.calls) == count


def test_preview_and_apply_reuse_the_same_answers(group):
    store, root, _ = group
    ports = Ports(create=True)
    original = store.get_record_state('claims', 'C')
    preview = c.run(store, ['C'], root, ports)
    assert preview['status'] == 'verified' and not preview['applied']
    assert store.get_record_state('claims', 'C') == original
    n = len(ports.calls)
    assert c.run(store, ['C'], root, ports, apply=True)['applied']
    assert len(ports.calls) == n


def test_independent_judgment_can_remain_evidence_without_a_new_claim(group):
    store, root, _ = group
    def retain(request):
        raw = Ports(create=False).repair(request)
        assert raw['judgment_dispositions'][0]['action'] == 'retain_evidence'
        return raw
    ports = Ports(create=True, repair=False, proposal=retain)
    result = c.run(store, ['C'], root, ports, apply=True)
    assert result['applied'] and result['new_claim_ids'] == []
    assert result['judgment_dispositions'][0]['action'] == 'retain_evidence'
    assert ports.calls.count('gpt') == 1 and 'jev:identity' not in ports.calls


def test_judgment_can_be_merged_into_an_authorized_existing_claim(group):
    store, root, _ = group
    def merge(request):
        return {'updates': [{'claim_id': 'C', 'action': 'repair',
                    'statement': '沒有聽從祂的話，顯明沒有真正相信祂。',
                    'evidence_step_ids': ['E'], 'scripture_refs': [], 'reason': '完整保存同一論點。'}],
                'new_claims': [], 'needs_more_creation_space': False,
                'judgment_dispositions': [{'evidence_id': 'E', 'action': 'represented_by_claims',
                    'claim_ids': ['C'], 'reason': '補入本組既有主張，不必另建卡。'}]}
    result = c.run(store, ['C'], root, Ports(create=True, repair=False, proposal=merge), apply=True)
    assert result['applied'] and not result['new_claim_ids']
    assert store.get_record_state('claims', 'C')['payload']['statement'] == '沒有聽從祂的話，顯明沒有真正相信祂。'


def test_judgment_candidate_cannot_silently_disappear(group):
    store, root, _ = group
    def omit(request):
        raw = Ports(create=False).repair(request)
        raw['judgment_dispositions'] = []
        return raw
    original = store.get_record_state('claims', 'C')
    with pytest.raises(ValueError, match='every judgment candidate'):
        c.run(store, ['C'], root, Ports(create=True, repair=False, proposal=omit), apply=True)
    assert store.get_record_state('claims', 'C') == original


def test_supporting_assertion_without_further_evidence_is_not_promoted(group):
    store, root, _ = group
    class NoFurtherSupport(Ports):
        def judge(self, request):
            if 'promotion_support' in request['questions']:
                self.calls.append('jev:promotion_support')
                return answer(request, 'no')
            return super().judge(request)
    ports = NoFurtherSupport(create=True)
    result = c.run(store, ['C'], root, ports, apply=True)
    assert result['applied'] and result['new_claim_ids'] == []
    assert result['candidate_dispositions'][0]['action'] == 'retain_evidence'
    assert store.get_record_state('questions', 'Q')['payload']['answer_claim_ids'] == ['C']
    assert ports.calls.count('gpt') == 1


def test_leaf_evidence_can_answer_question_without_becoming_a_claim(group):
    store, root, _ = group
    class LeafAnswer(Ports):
        def judge(self, request):
            if 'promotion_support' in request['questions']:
                return answer(request, 'no')
            if 'relation' in request['questions']:
                criteria = request['questions']['relation']['criteria']
                assert 'a_to_b_answers' in criteria and 'b_to_a_supports' not in criteria
                return answer(request, 'a_to_b_answers')
            return super().judge(request)
    result = c.run(store, ['C'], root, LeafAnswer(create=True), apply=True)
    assert result['applied'] and not result['new_claim_ids']
    relation = next(p for k,p in result['updates'].items() if k.startswith('knowledge_relations/'))
    assert (relation['from_id'], relation['to_id'], relation['relation_type']) == ('E','Q','answers')
    assert store.get_record_state('questions','Q')['payload']['answer_claim_ids'] == ['C']


def test_exact_duplicate_links_existing_question_answer_without_new_identity(group):
    store, root, _ = group
    existing = {'claim_id': 'D', 'statement': '不知道神的旨意，就無法遵行神的旨意。',
                'claim_type': 'exegesis', 'occurrences': [{'source_id': 'S', 'anchors': []}],
                'evidence_step_ids': ['E']}
    ev = deepcopy(store.get_record_state('evidence_steps', 'E')['payload'])
    ev['produced_claim_ids'].append('D')
    store.apply_plan(store.plan_package({'package_id': 'existing-direct-answer', 'claims': [existing], 'evidence_steps': [ev]}))
    ports = Ports(create=True)
    result = c.run(store, ['C'], root, ports, apply=True)
    assert result['new_claim_ids'] == []
    assert store.get_record_state('questions', 'Q')['payload']['answer_claim_ids'] == ['C', 'D']
    assert not any(q['state'].get('candidate') == existing['statement']
                   for q in ports.judgments if 'identity' in q['questions'])


def test_synonym_duplicate_requires_exact_pair_confirmation(group):
    store, root, _ = group
    claim = deepcopy(store.get_record_state('claims', 'C')['payload'])
    claim['statement'] = '人若不明白神的旨意，就不能照祂的旨意行。'
    store.apply_plan(store.plan_package({'package_id': 'same-judgment', 'claims': [claim]}))
    ports = Ports(create=True, repair=False, equivalent=True)
    result = c.run(store, ['C'], root, ports, apply=True)
    assert result['new_claim_ids'] == [] and 'jev:covered' in ports.calls


def test_changed_evidence_cannot_reuse_an_old_fidelity_check(group):
    store, root, _ = group
    proof = root / 'proof'
    ports = Ports()
    e.run(store, ['E'], proof, ports)
    row = deepcopy(store.get_record_state('evidence_steps', 'E')['payload'])
    row['statement'] = '改變了原本被審的意思。'
    store.apply_plan(store.plan_package({'package_id': 'evidence-drift', 'evidence_steps': [row]}))
    count = len(ports.calls)
    with pytest.raises(ValueError, match='Evidence changed'):
        c.run(store, ['C'], root / 'claim', ports, evidence_root=proof)
    assert len(ports.calls) == count


def test_added_owner_does_not_force_a_second_evidence_review(group):
    store, root, _ = group
    ports = Ports(create=True)
    c.run(store, ['C'], root / 'first', ports, apply=True)
    current = deepcopy(store.get_record_state('claims', 'C')['payload'])
    current['statement'] = '沒有聽從祂的話，顯明沒有真正相信祂。'
    store.apply_plan(store.plan_package({'package_id': 'current', 'claims': [current]}))
    later = Ports(repair=False)
    c.run(store, ['C'], root / 'second', later, evidence_root=root / 'first/evidence',
          max_new_claims=0, apply=True)
    assert 'jev:unsupported_part_exists' not in later.calls and 'gpt' not in later.calls


@pytest.mark.parametrize('fault', ['foreign_evidence', 'foreign_question', 'duplicate_target'])
def test_invalid_gpt_proposals_do_not_write(group, fault):
    store, root, _ = group
    before = store.get_record_state('claims', 'C')
    def bad(request):
        raw = Ports(create=True).repair(request)
        if fault == 'foreign_evidence': raw['updates'][0]['evidence_step_ids'] = ['OTHER']
        if fault == 'foreign_question': raw['new_claims'][0]['answer_question_ids'] = ['OTHER']
        if fault == 'duplicate_target': raw['updates'].append(raw['updates'][0])
        return raw
    with pytest.raises(ValueError):
        c.run(store, ['C'], root, Ports(create=True, proposal=bad), apply=True)
    assert store.get_record_state('claims', 'C') == before


def test_concurrent_source_claim_creation_is_detected_inside_apply(group):
    store, root, _ = group
    def concurrent():
        store.apply_plan(store.plan_package({'package_id': 'concurrent-peer', 'claims': [{
            'claim_id': 'LATE', 'statement': '同篇新增的另一項判斷。', 'claim_type': 'exegesis',
            'occurrences': [{'source_id': 'S', 'anchors': []}]}]}))
    original = store.get_record_state('claims', 'C')
    with pytest.raises(Exception, match='source Claim identities'):
        c.run(store, ['C'], root, Ports(create=True, before_reply=concurrent), apply=True)
    assert store.get_record_state('claims', 'C') == original


def test_explicit_transcript_alias_is_used_for_dedup_and_anchor_refresh(group):
    store, root, _ = group
    document = deepcopy(store.get_record_state('source_documents', 'S')['payload'])
    document.update(source_id='S2', transcript_id='講道舊ID')
    fragment = deepcopy(store.get_record_state('source_fragments', 'F')['payload'])
    fragment.update(fragment_id='F2', source_id='S2')
    evidence = deepcopy(store.get_record_state('evidence_steps', 'E')['payload'])
    evidence.update(source_fragment_id=None, source_fragment_ids=['F2'])
    claim = deepcopy(store.get_record_state('claims', 'C')['payload'])
    claim['occurrences'][0]['source_id'] = '講道舊ID'
    peer = deepcopy(claim)
    peer.update(claim_id='D', statement='不知道神的旨意，就無法遵行神的旨意。')
    question = deepcopy(store.get_record_state('questions', 'Q')['payload'])
    question.update(source_fragment_id='F2')
    store.apply_plan(store.plan_package({'package_id': 'alias', 'questions': [question], 'source_documents': [document],
        'source_fragments': [fragment], 'evidence_steps': [evidence], 'claims': [claim, peer]}))
    result = c.run(store, ['C'], root, Ports(create=True), apply=True)
    assert result['applied'] and not result['new_claim_ids']
    assert store.get_record_state('claims', 'C')['payload']['occurrences'][0]['source_id'] == 'S2'
    assert store.get_record_state('questions', 'Q')['payload']['answer_claim_ids'] == ['C', 'D']


def test_unknown_source_name_is_not_guessed_as_an_alias(group):
    store, root, _ = group
    claim = deepcopy(store.get_record_state('claims', 'C')['payload'])
    claim['occurrences'][0]['source_id'] = '另一篇'
    store.apply_plan(store.plan_package({'package_id': 'foreign', 'claims': [claim]}))
    with pytest.raises(ValueError, match='cross-source'):
        c.run(store, ['C'], root, Ports(), max_new_claims=0, apply=True)


def test_oral_emphasis_is_not_generated_as_a_claim_anchor(group):
    store, _, _ = group
    states, sources = e.load(store, ['E'])
    states['source_fragments/ORAL'] = {'payload': {'fragment_id': 'ORAL', 'source_id': 'S',
        'verbatim_excerpt': '所以那一點很重要。', 'paragraph_key': 'S0001'}, 'retired': False}
    states['evidence_steps/E']['payload']['source_fragment_ids'].append('ORAL')
    assert all(a['source_fragment_id'] != 'ORAL' for a in c.anchors(['E'], states, 'S')[0]['anchors'])


def test_no_zero_length_array_wire_constraint_when_only_new_claims_are_needed(group):
    store, root, _ = group
    ports = Ports(create=True, repair=False)
    c.run(store, ['C'], root, ports)
    assert '"maxItems": 0' not in json.dumps(ports.last_request['schema'])


class RelationPorts(Ports):
    def __init__(self, kind, *, reason='yes', during_relation=None):
        super().__init__(repair=False)
        self.kind, self.reason, self.during_relation = kind, reason, during_relation

    def judge(self, request):
        name = next(iter(request['questions']))
        if name in {'relation', 'reason_valid'}:
            self.calls.append('jev:' + name)
            self.judgments.append(request)
            if name == 'relation' and self.during_relation:
                self.during_relation()
            return answer(request, self.kind if name == 'relation' else self.reason)
        return super().judge(request)


def test_relation_reference_uses_document_transcript_alias_without_false_cross_source(tmp_path):
    records = {
        'source_documents/S': {'payload': {'source_id': 'S', 'transcript_id': '舊講道ID'}},
        'claims/C': {'payload': {'statement': '前提。', 'occurrences': [{'source_id': 'S'}]}},
        'claims/D': {'payload': {'statement': '結論。', 'occurrences': [{'source_id': '舊講道ID'}]}},
        'claim_relations/R': {'payload': {'from_id': 'C', 'to_id': 'D',
            'relation_type': 'supports', 'reason': '前提支持結論。'}},
    }
    ports = RelationPorts('a_to_b_supports')
    result = c.relations.review(records, {}, [], tmp_path, ports, ['C'])
    request = next(q for q in ports.judgments if 'relation' in q['questions'])
    assert request['state']['A']['source_ids'] == request['state']['B']['source_ids'] == ['S']
    assert not result['updates'] and 'gpt' not in ports.calls


def seed_relation(store):
    store.apply_plan(store.plan_package({'package_id': 'relation-fixture',
        'claims': [{'claim_id': 'D', 'statement': '真正相信包括聽從祂的話。', 'claim_type': 'exegesis',
                    'occurrences': [{'source_id': 'S', 'anchors': []}]}],
        'claim_relations': [{'claim_relation_id': 'R', 'from_id': 'C', 'to_id': 'D',
            'relation_type': 'supports', 'reason': '真正相信包括聽從，因此未聽從顯明沒有真正相信。'}]}))


def test_relation_direction_is_applied_from_jev_without_gpt(group):
    store, root, _ = group
    seed_relation(store)
    original_reason = store.get_record_state('claim_relations', 'R')['payload']['reason']
    ports = RelationPorts('b_to_a_supports')
    result = c.run(store, ['C'], root, ports, max_new_claims=0, apply=True)
    row = store.get_record_state('claim_relations', 'R')['payload']
    assert result['applied'] and (row['from_id'], row['to_id']) == ('D', 'C')
    assert row['reason'] == original_reason and 'gpt' not in ports.calls


def test_relation_reason_replacement_preserves_original_in_sql_history(group):
    store, root, _ = group
    seed_relation(store)
    reason = store.get_record_state('claim_relations', 'R')['payload']['reason']
    ports = RelationPorts('b_to_a_supports', reason='no')
    result = c.run(store, ['C'], root, ports,
                   max_new_claims=0, apply=True)
    new = store.get_record_state('claim_relations', 'R')['payload']
    assert new['reason_origin'] == 'gpt_grounded_relation_reason'
    assert ports.calls.count('gpt') == 1
    history = store.get_change_history(result['commit']['change_set_id'])
    operation = next(op for op in history['operations'] if op['object_id'] == 'R')
    assert operation['before']['reason'] == reason


def test_missing_group_relation_is_discovered_and_reason_written_once(group):
    store, root, _ = group
    claim = deepcopy(store.get_record_state('claims', 'C')['payload'])
    claim.update(claim_id='D', statement='真正相信包括聽從祂的話。')
    store.apply_plan(store.plan_package({'package_id': 'unconnected', 'claims': [claim]}))
    ports = RelationPorts('b_to_a_supports')
    result = c.run(store, ['C', 'D'], root, ports, max_new_claims=0, apply=True)
    edge = next(p for k, p in result['updates'].items() if k.startswith('claim_relations/'))
    assert result['applied'] and (edge['from_id'], edge['to_id']) == ('D', 'C')
    assert edge['reason_origin'] == 'gpt_grounded_relation_reason'
    assert ports.calls.count('gpt') == 1 and ports.calls.count('jev:relation') == 1


@pytest.mark.parametrize('fault', ['unknown_job', 'direction', 'needs_source'])
def test_relation_reason_cannot_change_scope_or_silently_approve_missing_material(group, fault):
    store, root, _ = group
    seed_relation(store)
    original = store.get_record_state('claim_relations', 'R')
    def reply(request):
        assert 'relation_reason_jobs' in request['input']
        job = request['input']['relation_reason_jobs'][0]
        reason = {'job_id': job['job_id'], 'action': 'write', 'reason': '有據的理由。'}
        if fault == 'unknown_job': reason['job_id'] = 'UNKNOWN'
        if fault == 'direction': reason['from_id'] = 'C'
        if fault == 'needs_source': reason['action'] = 'needs_source'
        return {'reasons': [reason]}
    ports = RelationPorts('b_to_a_supports', reason='no')
    ports.proposal = reply
    if fault == 'needs_source':
        result = c.run(store, ['C'], root, ports, max_new_claims=0, apply=True)
        assert result['status'] == 'needs_attention' and not result['applied']
    else:
        with pytest.raises(ValueError):
            c.run(store, ['C'], root, ports, max_new_claims=0, apply=True)
    assert store.get_record_state('claim_relations', 'R') == original


def test_no_relation_is_retired_without_content_rewrite(group):
    store, root, _ = group
    seed_relation(store)
    row = deepcopy(store.get_record_state('claims', 'C')['payload'])
    records, _ = e.load(store, ['E'])
    row['occurrences'] = c.anchors(['E'], records, 'S')
    store.apply_plan(store.plan_package({'package_id': 'already-bound', 'claims': [row]}))
    result = c.run(store, ['C'], root, RelationPorts('none'), max_new_claims=0, apply=True)
    assert result['applied'] and store.get_record_state('claim_relations', 'R')['retired']
    assert result['updates'] == {}


def test_neighbours_other_edges_are_not_reviewed_or_pinned(group):
    store, root, _ = group
    seed_relation(store)
    store.apply_plan(store.plan_package({'package_id': 'second-hop', 'claims': [{
        'claim_id': 'Z', 'statement': '另一個主題。', 'claim_type': 'exegesis',
        'occurrences': [{'source_id': 'S', 'anchors': []}]}], 'claim_relations': [{
        'claim_relation_id': 'R2', 'from_id': 'D', 'to_id': 'Z', 'relation_type': 'supports', 'reason': 'old'}]}))
    def unrelated():
        p = deepcopy(store.get_record_state('claim_relations', 'R2')['payload'])
        p['reason'] = 'unrelated concurrent change'
        store.apply_plan(store.plan_package({'package_id': 'unrelated', 'claim_relations': [p]}))
    ports = RelationPorts('b_to_a_supports', during_relation=unrelated)
    assert c.run(store, ['C'], root, ports, max_new_claims=0, apply=True)['applied']
    before = json.loads((root/'before.json').read_text())['records']
    assert 'claim_relations/R2' not in before and 'claims/Z' not in before
    assert ports.calls.count('jev:relation') == 1


def test_explicit_relation_constraint_is_not_overridden(group):
    store, root, _ = group
    seed_relation(store)
    store.apply_plan(store.plan_package({'package_id': 'forbidden', 'claim_relation_constraints': [{
        'constraint_id': 'X', 'source_id': 'D', 'target_id': 'C',
        'forbidden_relation_types': ['supports'], 'reason': 'Only parallel context is established.'}]}))
    result = c.run(store, ['C'], root, RelationPorts('b_to_a_supports'), max_new_claims=0, apply=True)
    assert result['status'] == 'needs_attention' and not result['applied']
    assert result['pending'][0]['reason'] == 'explicit_relation_constraint'


@pytest.mark.parametrize(('task', 'usage', 'timeout', 'error'), [
    ('evidence_fidelity_repair', {'output_tokens': 2500}, 120, None),
    ('evidence_fidelity_repair', {'output_tokens': 2501}, 120, 'exceeded output token'),
    ('evidence_fidelity_repair', None, 120, 'no output token receipt'),
    ('argument_completeness', {'output_tokens': 3000}, None, None),
])
def test_flash_evidence_limits_keep_raw_provider_receipt(tmp_path, task, usage, timeout, error):
    from types import SimpleNamespace
    from backend.pipeline import claim_refresh, evidence_refresh

    options = {}

    def reply(*args, **kwargs):
        options.update(kwargs)
        return {'items': []}

    gpt = SimpleNamespace(backend='codex_subscription', model='gpt-6-sol',
                          reasoning_effort='medium', last_usage=usage,
                          last_raw_response={'items': []}, generate_json=reply)
    ports = claim_refresh.Models(gpt, tmp_path)
    ports.evidence_fidelity_limits = (120, 2500)
    request = {'system': 'Check the evidence', 'input': {'task': task}, 'schema': {}}
    if error:
        with pytest.raises(ValueError, match=error):
            ports.repair(request)
    else:
        assert ports.repair(request) == {'items': []}
    assert options.get('timeout_seconds') == timeout
    receipt = tmp_path / 'gpt' / evidence_refresh.digest(request) / 'provider.json'
    assert json.loads(receipt.read_text())['usage'] == usage
