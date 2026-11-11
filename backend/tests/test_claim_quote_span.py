"""Literal source restoration happens before review and survives revision/resume."""
from copy import deepcopy
from pathlib import Path
import json

import pytest

from backend.pipeline import claim_management as cm, claim_model_output as output
from backend.pipeline import claim_management_intake as intake, claim_management_batch as batch
from backend.pipeline.claim_quote_projection import source_excerpt, SPAN_CONTRACT, NEAR_CONTRACT, project_review
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_commit import binding
from backend.pipeline.source_reconciliation_runner import body, read
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_intake import claims, pass_review
from backend.tests.test_claim_metadata_projection import DurableSyntheticPorts

REAL_QUOTES = json.loads((Path(__file__).parent/'fixtures/claim_quote_span_2018_revision455.json').read_text())


@pytest.mark.parametrize('old,new', [('joint_update_v8', 'joint_update_v9'), ('bounded_unit_v1', 'bounded_unit_v2')])
def test_new_policy_only_enables_quote_restoration_without_changing_models_or_budget(old, new):
    directory = intake.DEFAULT_POLICY.parent
    before = json.loads((directory/('wang_claim_management_'+old+'.json')).read_text())
    after = json.loads((directory/('wang_claim_management_'+new+'.json')).read_text())
    assert after.pop('source_quote_projection') == SPAN_CONTRACT
    after['policy_id'] = before['policy_id']
    assert after == before


@pytest.mark.parametrize('case', REAL_QUOTES, ids=lambda c:c['original_call'][4:12]+'-'+c['paragraph_key']+'-'+str(len(c['returned_quote'])))
def test_all_saved_real_quote_variants_restore_exact_source(case):
    assert source_excerpt(case['returned_quote'], case['paragraph'], contract=SPAN_CONTRACT) == case['expected_literal']


def test_frozen_near_contract_keeps_its_original_partial_sentence_limits():
    assert sum(source_excerpt(c['returned_quote'], c['paragraph'], contract=NEAR_CONTRACT) != c['returned_quote']
               for c in REAL_QUOTES) == 5


@pytest.mark.parametrize('paragraph', [
    '教授說這一段解釋是完全不同的意思。教授說這一段解釋是完全相近的意思。',
    '教授說這一段解釋是完全不同的意思。教授說這一段解釋是完全不同的意思。',
    '另一個主題，這裡沒有教授所說的那個句子，也沒有相同證據。',
])
def test_ambiguous_repeated_or_different_sentences_are_not_guessed(paragraph):
    quote = '教授說這一段解釋是完全相同的意思。'
    assert source_excerpt(quote, paragraph, contract=SPAN_CONTRACT) == quote


def test_only_the_bound_paragraph_and_typed_quote_are_corrected():
    c = REAL_QUOTES[0]
    sources = {'S': {'paragraphs': {'P': {'text': c['paragraph']}, 'OTHER': {'text': '這裡是另一段原文。'}}}}
    quote = {'source_id': 'S', 'paragraph_key': 'OTHER', 'exact_quote': c['returned_quote']}
    raw = {'findings': [{'reason': c['returned_quote'], 'citations': [quote]}], 'prior_findings': [],
           'binding_assessments': [{'claim_key': 'claims/C', 'sufficient': False}]}
    fixed, changes = project_review(raw, sources, contract=SPAN_CONTRACT)
    assert fixed == raw and not changes  # No paragraph/source search or reassignment.
    raw['findings'][0]['citations'][0]['paragraph_key'] = 'P'
    fixed, changes = project_review(raw, sources, contract=SPAN_CONTRACT)
    assert len(changes) == 1
    assert fixed['findings'][0]['reason'] == raw['findings'][0]['reason']
    assert fixed['binding_assessments'] == raw['binding_assessments']


def seed_source(store, real_case=None):
    literal = real_case['expected_literal'] if real_case else '這項判斷必須根據原文的證據來理解，不能只看表面的說法。'
    document = deepcopy(store.get_record_state('source_documents', 'S')['payload'])
    file = Path(document['source_path'])
    text = real_case['paragraph'] if real_case else '教授接著說明：'+literal
    file.write_text(json.dumps({'script': [{'text': text}]}))
    document['source_file_sha256'] = binding(file)['file_sha256']
    fragment = dict(store.get_record_state('source_fragments', 'FC')['payload'], verbatim_excerpt=literal)
    store.apply_plan(store.plan_package({'package_id': 'literal-span-source',
                                        'source_documents': [document], 'source_fragments': [fragment]}))
    return literal


@pytest.mark.parametrize('interrupt', [False, True])
@pytest.mark.parametrize('omit_calibrated_quote', [False, True])
def test_pre_review_restoration_survives_two_revisions_and_saved_candidate_resume(claims, monkeypatch, interrupt, omit_calibrated_quote):
    store, root = claims
    literal = seed_source(store)
    wrong = [literal.replace('原文的證據', '原文證據'), literal.replace('表面的說法', '表面說法')]
    fragment = dict(store.get_record_state('source_fragments', 'FC')['payload'], verbatim_excerpt=wrong[0])
    store.apply_plan(store.plan_package({'package_id': 'existing-quote-needs-preflight', 'source_fragments': [fragment]}))
    # This test explicitly exercises previous-candidate propagation. The newer
    # database-retry contract intentionally omits that field and is tested separately.
    policy = json.loads(intake.DEFAULT_POLICY.read_text())
    policy.pop('retry_context_contract', None)
    policy_file = root/'previous-candidate-policy.json'
    policy_file.parent.mkdir(parents=True, exist_ok=True)
    policy_file.write_text(json.dumps(policy))
    prepared = intake.prepare(['C'], root, instruction='Refresh C with its own complete evidence.', store=store, policy=policy_file)
    request, execution = next(iter(batch.load(prepared['manifest']['path'], root/'batch', store=store)[1].values()))
    assert cm.Policy.model_validate_json(Path(request.policy.path).read_text()).source_quote_projection == SPAN_CONTRACT
    untouched = store.get_record_state('claims', 'D')
    states = {'gpt': 0, 'opus': 0, 'interrupted': False}
    def citation(text):
        return {'source_id': 'S', 'paragraph_key': 'S0001', 'exact_quote': text}
    def review(packet):
        number = states['opus']; states['opus'] += 1
        result = pass_review(packet)
        assert next(r['payload']['verbatim_excerpt'] for r in packet['candidate']['records']
                    if r['collection'] == 'source_fragments') == literal
        assert all(c['exact_quote'] == literal for d in packet['candidate']['dispositions'] for c in d['citations'])
        assert store.get_record_state('source_fragments', 'FC')['payload']['verbatim_excerpt'] == wrong[0]
        # Every genuine content issue is independent of the quote correction.
        if number < 2:
            result['findings'] = [{'target_key': 'claims/C', 'component': 'statement.'+str(number),
                'category': 'content', 'reason': 'Add the source distinction.' if not number else 'Keep the source limitation too.',
                'acceptance_condition': 'Preserve the actual complete teaching.', 'citations': [citation(wrong[0])],
                'affected_target_keys': ['claims/C'], 'affected_write_keys': ['claims/C'] if number else [],
                'impact_reason': 'A substantive target issue, not a quotation-format finding.'}]
        result['prior_findings'] = [{'finding_id': fid, 'disposition': 'resolved', 'reason': 'Content distinction preserved.',
            'citations': [citation(wrong[0])], 'affected_target_keys': ['claims/C'],
            'affected_write_keys': ['claims/C'], 'impact_reason': 'Target wording corrected.'} for fid in packet['unresolved']]
        return result
    def propose(packet):
        number = states['gpt']; states['gpt'] += 1
        assert all(c['exact_quote'] == literal for f in packet['unresolved'].values() for c in f['citations'])
        assert next(r['payload']['verbatim_excerpt'] for r in packet['previous_candidate']['records']
                    if r['collection'] == 'source_fragments') == literal
        statement = '應依據原文證據理解教授主張。' if not number else '應依據原文證據理解教授主張，不能只看表面的說法。'
        edits = [('claims/C', {'statement': statement})]
        if not omit_calibrated_quote:
            edits.append(('source_fragments/FC', {'verbatim_excerpt': wrong[number]}))
        return {'changes': [{'record_key': key, 'fields_json': json.dumps(fields),
                'reason': 'Preserve the source teaching.', 'citations': [citation(wrong[number])]} for key, fields in edits],
            'creations': [], 'retirements': [], 'findings': [], 'finding_resolutions': [
                {'finding_id': fid, 'disposition': 'resolved', 'reason': 'Preserve the requested distinction.',
                 'citations': [citation(wrong[number])], 'affected_target_keys': ['claims/C'],
                 'affected_write_keys': ['claims/C'], 'impact_reason': 'Full target precision.'} for fid in packet['unresolved']]}
    ports = DurableSyntheticPorts(store, execution, opus=review, proposal=propose)
    record = store.record_claim_management_stage
    def stop_before_review(rid, receipt):
        saved = record(rid, receipt)
        if interrupt and not states['interrupted'] and states['gpt'] == 1 and body(read(Path(receipt['path'])))['stage'] == 'candidate':
            states['interrupted'] = True
            raise RuntimeError('synthetic controller exit after corrected candidate, before Opus')
        return saved
    monkeypatch.setattr(store, 'record_claim_management_stage', stop_before_review)
    result = execute_claim_management(store, request, root=execution, ports=ports)
    if interrupt:
        assert result['reason'] == 'engineering_required', result
        assert ports.calls == ['opus', 'gpt']
        result = execute_claim_management(store, request, root=execution, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['opus', 'gpt', 'opus', 'gpt', 'opus']
    assert states['gpt'] == 2 and states['opus'] == 3
    parent = store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
    assert all(s['state'] == 'completed' and 'rejection' not in s for s in parent['call_reservations'].values())
    gpt_rounds = {s['round_id'] for s in parent['call_reservations'].values() if s['role'] == 'gpt'}
    for rid in gpt_rounds:
        raw = body(read(execution/rid/'gpt.raw.json'))['response']
        receipt = body(read(execution/rid/'candidate-normalization.json'))
        assert receipt['model_response_sha256'] == cm.sha256_json(raw)
        assert len(receipt['source_quote_corrections']) >= 3
        if omit_calibrated_quote:
            assert receipt['inherited_initial_quote_preflight']['record_keys'] == ['source_fragments/FC']
        assert any(w in json.dumps(raw, ensure_ascii=False) for w in wrong)
    assert store.get_record_state('source_fragments', 'FC')['payload']['verbatim_excerpt'] == literal
    assert store.get_record_state('claims', 'D') == untouched
    assert store.claim_management_qualification('C')['ready']
    calls = list(ports.calls)
    assert execute_claim_management(store, request, root=execution, ports=ports)['status'] == 'completed'
    assert ports.calls == calls  # Completion/replay does not generate another review.


def test_a_corrected_quote_does_not_approve_an_unsupported_claim(claims):
    store, root = claims
    literal = seed_source(store)
    wrong = literal.replace('原文的證據', '原文證據')
    policy = cm.Policy.model_validate_json(intake.DEFAULT_POLICY.read_text())
    policy.initial_candidate = 'proposal'
    policy.budget.max_revisions = 0
    policy.budget.gpt_calls = policy.budget.opus_calls = 1
    policy_file = root.parent/'one-review-policy.json'; policy_file.write_text(policy.model_dump_json())
    prepared = intake.prepare(['C'], root, instruction='Refresh C without overriding content findings.', policy=policy_file, store=store)
    request, execution = next(iter(batch.load(prepared['manifest']['path'], root/'batch', store=store)[1].values()))
    before = store.get_record_state('claims', 'C')
    cite = {'source_id': 'S', 'paragraph_key': 'S0001', 'exact_quote': wrong}
    def propose(packet):
        return {'changes': [{'record_key': key, 'fields_json': json.dumps(fields), 'reason': 'Synthetic proposal.',
                             'citations': [cite]} for key, fields in [
            ('claims/C', {'statement': '只看表面就能理解教授主張。'}),
            ('source_fragments/FC', {'verbatim_excerpt': wrong})]],
            'creations': [], 'retirements': [], 'findings': [], 'finding_resolutions': []}
    def review(packet):
        rows = {r['collection']+'/'+r['object_id']: r['payload'] for r in packet['candidate']['records']}
        assert rows['source_fragments/FC']['verbatim_excerpt'] == literal
        assert rows['claims/C']['statement'] == '只看表面就能理解教授主張。'
        result = pass_review(packet)
        result['findings'] = [{'target_key': 'claims/C', 'component': 'statement', 'category': 'content',
            'reason': 'The statement contradicts the corrected actual source.', 'acceptance_condition': 'Preserve the limitation.',
            'citations': [cite], 'affected_target_keys': ['claims/C'], 'affected_write_keys': ['claims/C'],
            'impact_reason': 'Own target is unsupported even though its quotation is now exact.'}]
        return result
    ports = DurableSyntheticPorts(store, execution, proposal=propose, opus=review)
    result = execute_claim_management(store, request, root=execution, ports=ports)
    assert result['status'] == 'paused' and result['reason'] == 'budget_exhausted', result
    assert ports.calls == ['gpt', 'opus']
    assert store.get_record_state('claims', 'C') == before
    parent = store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
    assert parent['findings']
    assert not store.get_cvp_group(next(iter(parent['latest_rounds'].values())))['change_set_ids']


def test_default_upgrade_does_not_rewrite_saved_intake_or_unit_policy(claims):
    from backend.pipeline import claim_review_units
    store, root = claims
    old = intake.DEFAULT_POLICY.parent/'wang_claim_management_joint_update_v8.json'
    prepared = intake.prepare(['C'], root, instruction='Keep this frozen policy.', policy=old, store=store)
    original = (root/'policy.json').read_bytes()
    assert intake.prepare(['C'], root, instruction='Keep this frozen policy.', store=store) == prepared
    assert (root/'policy.json').read_bytes() == original
    assert json.loads(original).get('source_quote_projection') is None
    # This unit is a separate frozen no-call preparation, retaining its old
    # configuration when the caller uses its default on a later invocation.
    unit = root.parent/'old-unit'
    first = claim_review_units.prepare(['D'], unit, instruction='Keep this unit policy.',
        policy=old.parent/'wang_claim_management_bounded_unit_v1.json', store=store)
    old_bytes = (unit/'unit-policy.json').read_bytes()
    assert claim_review_units.prepare(['D'], unit, instruction='Keep this unit policy.', store=store) == first
    assert (unit/'unit-policy.json').read_bytes() == old_bytes


@pytest.mark.parametrize('bad_quote', [None, [], {}])
def test_malformed_quote_stays_for_the_typed_validator(bad_quote):
    raw = {'findings': [{'citations': [{'source_id': 'S', 'paragraph_key': 'P', 'exact_quote': bad_quote}]}]}
    fixed, changes = project_review(raw, {'S': {'paragraphs': {'P': {'text': '原文。'}}}}, contract=SPAN_CONTRACT)
    assert fixed == raw and not changes


@pytest.mark.parametrize('quote,paragraph', [
    ('教授說這一段解釋是完全相同的意思。', '教授說這一段解釋是完全不同的意思。'),
    ('教授在這裡強調的原因是主要來自這一段經文。', '教授在這裡強調的原因是來自這一段經文。'),
    ('教授引用馬太福音5:16解釋天上的父。', '教授引用馬太福音5:14解釋天上的父。'),
    ('教授用希臘文 huios 說明這個稱呼的意思。', '教授用希臘文 theos 說明這個稱呼的意思。'),
    ('教授說這些不是神的話，而那些是神的話。', '教授說這些是神的話，而那些不是神的話。'),
])
def test_similarity_does_not_erase_negation_scope_scripture_or_technical_terms(quote, paragraph):
    assert source_excerpt(quote, paragraph, contract=SPAN_CONTRACT) == quote


@pytest.mark.parametrize('interrupt', [False, True])
@pytest.mark.parametrize('real_case', REAL_QUOTES, ids=lambda c:c['original_call'][4:12]+'-'+str(len(c['returned_quote'])))
def test_first_opus_sees_calibrated_existing_quote_and_only_target_support_is_written(claims, monkeypatch, interrupt, real_case):
    store, root = claims
    literal = seed_source(store, real_case)
    wrong = real_case['returned_quote']
    fragments = [dict(store.get_record_state('source_fragments', fid)['payload'], verbatim_excerpt=wrong)
                 for fid in ['FC', 'FD']]
    store.apply_plan(store.plan_package({'package_id': 'first-review-quote-preflight', 'source_fragments': fragments,
        'claim_relations': [{'claim_relation_id': 'CD', 'from_id': 'C', 'to_id': 'D',
                             'relation_type': 'supports', 'reason': 'Neighbour reference.'}],
        'knowledge_relations': [{'relation_id': 'BACKGROUND', 'from_id': 'ED', 'to_id': 'EC',
                                 'relation_type': 'contextualizes', 'reason': 'Reference-only background.'}]}))
    prepared = intake.prepare(['C'], root, instruction='Calibrate own literal evidence before the first review.', store=store,
        policy=intake.DEFAULT_POLICY.parent/'wang_claim_management_bounded_unit_v2.json', groups=[['C']])
    request, execution = next(iter(batch.load(prepared['manifest']['path'], root/'batch', store=store)[1].values()))
    # Spare permission on a neighbour's private quote is not a maintenance root.
    assert any(p.key == 'source_fragments/FD' and 'verbatim_excerpt' in p.fields for p in request.permissions)
    untouched = {key: store.get_record_state(*key.split('/')) for key in ['claims/C', 'claims/D', 'source_fragments/FD']}
    def review(packet):
        assert store.get_record_state('source_fragments', 'FC')['payload']['verbatim_excerpt'] == wrong
        assert 'source_fragments/FD' in packet['before']
        assert [(r['collection'], r['object_id'], r['payload']['verbatim_excerpt'])
                for r in packet['candidate']['records']] == [('source_fragments', 'FC', literal)]
        assert 'source_fragments/FD' not in {d['record_key'] for d in packet['candidate']['dispositions'] if d['action'] != 'retain'}
        return pass_review(packet)
    ports = DurableSyntheticPorts(store, execution, opus=review)
    record = store.record_claim_management_stage
    stopped = []
    def before_first_review(rid, receipt):
        result = record(rid, receipt)
        if interrupt and not stopped and body(read(Path(receipt['path'])))['stage'] == 'candidate':
            stopped.append(rid)
            raise RuntimeError('synthetic stop after initial calibration and before first Opus')
        return result
    monkeypatch.setattr(store, 'record_claim_management_stage', before_first_review)
    result = execute_claim_management(store, request, root=execution, ports=ports)
    if interrupt:
        assert result['reason'] == 'engineering_required', result
        assert ports.calls == []
        assert store.get_record_state('source_fragments', 'FC')['payload']['verbatim_excerpt'] == wrong
        result = execute_claim_management(store, request, root=execution, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['opus']  # No GPT was needed for a mechanical preflight.
    parent = store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
    rid = next(iter(parent['latest_rounds'].values()))
    receipt = body(read(execution/rid/'candidate-normalization.json'))
    assert receipt['initial_quote_preflight']['record_keys'] == ['source_fragments/FC']
    assert receipt['source_quote_corrections'][0]['before'] == wrong
    assert receipt['source_quote_corrections'][0]['after'] == literal
    assert store.get_record_state('source_fragments', 'FC')['payload']['verbatim_excerpt'] == literal
    assert {key: store.get_record_state(*key.split('/')) for key in untouched} == untouched
    assert store.claim_management_qualification('C')['ready']


@pytest.mark.parametrize('write_permission', [False, True])
def test_initial_calibration_never_bypasses_permission_or_review_approval(claims, write_permission):
    from backend.tests.test_claim_management_human_source_follow_up import authorize
    store, root = claims
    literal = seed_source(store); wrong = literal.replace('原文的證據', '原文證據')
    fragment = dict(store.get_record_state('source_fragments', 'FC')['payload'], verbatim_excerpt=wrong)
    store.apply_plan(store.plan_package({'package_id': 'preflight-rejection-before-commit', 'source_fragments': [fragment]}))
    policy = cm.Policy.model_validate_json(intake.DEFAULT_POLICY.read_text())
    policy.budget.max_revisions = policy.budget.gpt_calls = 0; policy.budget.opus_calls = 1
    p = root.parent/'first-review-only-policy.json'; p.write_text(policy.model_dump_json())
    prepared = intake.prepare(['C'], root, instruction='Calibrate within permission and require review.', policy=p, store=store)
    request, execution = next(iter(batch.load(prepared['manifest']['path'], root/'batch', store=store)[1].values()))
    if not write_permission:
        request.permissions = [p.model_copy(update={'fields': [f for f in p.fields if f != 'verbatim_excerpt']})
                               if p.key == 'source_fragments/FC' else p for p in request.permissions]
        a = json.loads(cm.source.verify_file(request.authorization).read_text())
        authorize(request, {k:v for k,v in a.items() if k not in {'request', 'policy'}})
    before = store.get_record_state('source_fragments', 'FC')
    def review(packet):
        rows = {r['collection']+'/'+r['object_id']:r['payload'] for r in packet['candidate']['records']}
        assert ('source_fragments/FC' in rows) == write_permission
        if write_permission:assert rows['source_fragments/FC']['verbatim_excerpt'] == literal
        result = pass_review(packet)
        result['binding_assessments'][0]['sufficient'] = False
        result['findings'] = [{'target_key': 'claims/C', 'component': 'statement', 'category': 'content',
            'reason': 'Target still needs a substantive decision.', 'acceptance_condition': 'Preserve the source meaning.',
            'citations': [{'source_id':'S','paragraph_key':'S0001','exact_quote':literal}],
            'affected_target_keys':['claims/C'], 'affected_write_keys':[], 'impact_reason':'Target content remains unresolved.'}]
        return result
    ports = DurableSyntheticPorts(store, execution, opus=review)
    result = execute_claim_management(store, request, root=execution, ports=ports)
    assert result['status'] == 'paused' and result['reason'] == 'budget_exhausted', result
    assert ports.calls == ['opus'] and store.get_record_state('source_fragments', 'FC') == before


def test_initial_calibrated_pass_remains_reference_during_another_targets_revision(claims):
    store, root = claims
    literal = seed_source(store); wrong = literal.replace('原文的證據', '原文證據')
    fragment = dict(store.get_record_state('source_fragments', 'FD')['payload'], verbatim_excerpt=wrong)
    d = dict(store.get_record_state('claims', 'D')['payload'], statement='教授要求按原文證據理解。')
    store.apply_plan(store.plan_package({'package_id':'calibrated-initial-pass', 'claims':[d], 'source_fragments':[fragment]}))
    prepared = intake.prepare(['C','D'], root, instruction='Calibrate both before first review and retain genuine passes.',
        policy=intake.DEFAULT_POLICY.parent/'wang_claim_management_bounded_unit_v2.json', groups=[['C','D']], store=store)
    request, execution = next(iter(batch.load(prepared['manifest']['path'], root/'batch', store=store)[1].values()))
    cite = {'source_id':'S','paragraph_key':'S0001','exact_quote':literal}
    def review(packet):
        assert next(r['payload']['verbatim_excerpt']for r in packet['candidate']['records']
                    if r['object_id']=='FD') == literal
        result = pass_review(packet)
        if not packet['round_number']:
            assert set(packet['own_evidence_bindings']) == {'claims/C','claims/D'}
            result['findings'] = [{'target_key':'claims/C','component':'statement','category':'content',
                'reason':'C is unsupported.','acceptance_condition':'Use the professor actual teaching.','citations':[cite],
                'affected_target_keys':['claims/C'],'affected_write_keys':[],'impact_reason':'C only; D basis is complete.'}]
        else:
            assert packet['initial_review_references']['claim_keys'] == ['claims/D']
            assert set(packet['own_evidence_bindings']) == {'claims/C'}
            result['prior_findings'] = [{'finding_id':fid,'disposition':'resolved','reason':'Actual teaching preserved.',
                'citations':[cite],'affected_target_keys':['claims/C'],'affected_write_keys':['claims/C'],
                'impact_reason':'C source meaning restored.'} for fid in packet['unresolved']]
        return result
    def propose(packet):
        assert next(r['payload']['verbatim_excerpt']for r in packet['previous_candidate']['records']
                    if r['object_id']=='FD') == literal
        return {'changes':[{'record_key':'claims/C','fields_json':json.dumps({'statement':'教授要求按原文證據理解，不能只看表面。'}),
            'reason':'Actual teaching.','citations':[cite]}], 'creations':[],'retirements':[],'findings':[],
            'finding_resolutions':[{'finding_id':fid,'disposition':'resolved','reason':'Actual teaching.', 'citations':[cite],
                'affected_target_keys':['claims/C'],'affected_write_keys':['claims/C'],'impact_reason':'C source distinction.'}
                for fid in packet['unresolved']]}
    ports = DurableSyntheticPorts(store, execution, opus=review, proposal=propose)
    result = execute_claim_management(store, request, root=execution, ports=ports)
    assert result['status']=='completed',result
    assert ports.calls == ['opus','gpt','opus']
    assert store.claim_management_qualification('C')['ready'] and store.claim_management_qualification('D')['ready']
