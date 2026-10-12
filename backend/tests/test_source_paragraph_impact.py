from copy import deepcopy
import hashlib
import json
import os

import pytest

from backend.pipeline import source_reconciliation as c
from backend.pipeline import source_paragraph_impact as p
from backend.tests.test_source_reconciliation import scenario


@pytest.fixture
def scoped(scenario):
    manifest, states, _ = scenario
    # Extend the synthetic source with an unrelated unchanged paragraph and
    # independently referenced Evidence/Claim. No professor text in this test.
    for b, text in [(manifest.before_source, '甲是假设。'),
                    (manifest.current_sources[0], '甲是假设，不是断言。')]:
        from pathlib import Path
        Path(b.path).write_text(json.dumps([{'text': text}, {'text': '乙不变。'}], ensure_ascii=False))
        b.file_sha256 = hashlib.sha256(Path(b.path).read_bytes()).hexdigest()
    before = c.source_snapshot(manifest.before_source)
    after = c.source_snapshot(manifest.current_sources[0])
    states['source_documents/SRC-test'].update(source_body_sha256=before['body_sha256'],
        source_sha256=before['body_sha256'], source_file_sha256=before['file']['file_sha256'])
    for k, para, quote in [('source_fragments/FR-test', 'S0001', '甲是假设。'),
                           ('source_fragments/FR-other', 'S0002', '乙不变。')]:
        value = deepcopy(states['source_fragments/FR-test'])
        value.update(fragment_id=c.split_key(k)[1], paragraph_key=para, verbatim_excerpt=quote,
            source_sha256=before['body_sha256'], anchor_state='source_version_bound',
            paragraph_text_sha256=hashlib.sha256(before['paragraphs'][para]['text'].encode()).hexdigest(),
            verbatim_excerpt_sha256=hashlib.sha256(quote.encode()).hexdigest())
        states[k] = value
    evidence = deepcopy(states['evidence_steps/E-test'])
    evidence.update(evidence_step_id='E-other', source_fragment_ids=['FR-other'], produced_claim_ids=['CL-other'])
    states['evidence_steps/E-other'] = evidence
    claim = deepcopy(states['claims/CL-test'])
    claim.update(claim_id='CL-other', evidence_step_ids=['E-other'])
    states['claims/CL-other'] = claim
    manifest.permissions.append(c.Permission(collection='source_fragments', object_id='FR-other', fields=['source_sha256']))
    return manifest, states, before, after


def test_disjoint_semantic_graph_and_exact_binding_epoch(scoped):
    manifest, states, before, after = scoped
    original = deepcopy(states)
    plan = p.partition(manifest, states, before, after)
    semantic = plan['proof']['semantic_inventory']
    assert 'claims/CL-test' in semantic['affected_keys']
    assert 'claims/CL-other' not in semantic['affected_keys'] + semantic['context_keys']
    assert 'claims/CL-other' in plan['proof']['complete_affected_keys']
    bound = c.index(plan['binding_postimages'])['source_fragments/FR-other']
    assert bound == {**states['source_fragments/FR-other'], 'source_sha256': after['body_sha256'], 'revision': 2}
    assert plan['proof']['content_approval'] is False
    assert plan['proof']['group_completion'] is False
    assert p.verify(plan, manifest, states, before, after) == plan
    assert states == original


@pytest.mark.parametrize('mutation', ['quote', 'paragraph', 'epoch', 'hash', 'state', 'permission', 'timing', 'insert', 'unbounded'])
def test_binding_scope_fails_closed(scoped, mutation):
    m, states, before, after = deepcopy(scoped)
    fragment = states['source_fragments/FR-other']
    if mutation == 'quote': fragment['verbatim_excerpt'] = 'foreign'
    if mutation == 'paragraph': fragment['paragraph_key'] = 'S0099'
    if mutation == 'epoch': fragment['source_sha256'] = 'foreign'
    if mutation == 'hash': fragment['paragraph_text_sha256'] = '0' * 64
    if mutation == 'state': fragment['review_status'] = 'human_hold'
    if mutation == 'permission': m.permissions[-1].fields.append('verbatim_excerpt')
    if mutation == 'timing': after['paragraphs']['S0001']['index'] = 17
    if mutation == 'insert': after['paragraphs']['S0003'] = {'text': '新增'}
    if mutation == 'unbounded': after['paragraphs']['S0002']['text'] = '乙也变化。'
    with pytest.raises(ValueError): p.partition(m, states, before, after)


@pytest.mark.parametrize('mutation', ['payload', 'key', 'approval', 'root', 'source', 'drop'])
def test_proof_is_full_recomputed_not_an_approval_flag(scoped, mutation):
    m, states, before, after = scoped
    plan = deepcopy(p.partition(m, states, before, after))
    if mutation == 'payload': plan['binding_postimages'][0]['payload']['review_status'] = 'system_approved'
    if mutation == 'key': plan['proof']['binding_only_rows'][0]['record_key'] = 'source_fragments/foreign'
    if mutation == 'approval': plan['proof']['content_approval'] = True
    if mutation == 'root': plan['proof']['semantic_roots'] = []
    if mutation == 'source': plan['proof']['after_body_sha256'] = 'foreign'
    if mutation == 'drop': plan['proof']['complete_affected_keys'].pop()
    with pytest.raises(ValueError, match='proof drift'): p.verify(plan, m, states, before, after)


def test_unrelated_retained_payload_drift_does_not_inherit_a_binding_proof(scoped):
    m, states, before, after = scoped
    plan = p.partition(m, states, before, after)
    states['claims/CL-other']['statement'] = 'Changed without approval'
    with pytest.raises(ValueError, match='proof drift'):
        p.verify(plan, m, states, before, after)


def test_preview_retains_full_own_source_and_no_execution_authority(scoped):
    m, states, before, after = scoped
    plan = p.partition(m, states, before, after)
    frozen = {'manifest': m.model_dump(), 'before_records': c.rows(states), 'before': before,
              'after': [after], 'sentence_inventory': [], 'context_sources': []}
    preview = p.semantic_preview(frozen, plan)
    assert preview['before_source'] == before and preview['current_sources'] == [after]
    assert preview['source_epoch_binding_proof']['binding_only_rows'] == plan['proof']['binding_only_rows']
    assert preview['scope_preview_only'] and preview['content_write_authority'] is False
    assert 'campaign_claim_ids' not in preview['manifest_projection']
    assert m.model_dump() == frozen['manifest']


def binding_material(scoped):
    m, states, before, after = scoped
    frozen = {'manifest': m.model_dump(), 'before_records': c.rows(states), 'before': before,
              'after': [after], 'sentence_inventory': [], 'context_sources': []}
    partition = p.partition(m, states, before, after)
    ops = [{'collection': row['collection'], 'object_id': row['object_id'], 'operation': 'update',
            'before_revision': states[c.key(row['collection'], row['object_id'])]['revision'],
            'after_revision': row['payload']['revision'], 'payload': deepcopy(row['payload'])}
           for row in partition['binding_postimages']]
    return frozen, partition, ops


def test_common_plan_binding_check_preserves_all_original_review_fields(scoped):
    frozen, partition, ops = binding_material(scoped)
    states = c.index(frozen['before_records'])
    package = {'source_fragments': [r['payload'] for r in partition['binding_postimages']],
               'source_epoch_binding_material': {'frozen': frozen, 'partition': partition}}
    post = c.assert_plan_postimages(package, {'operations': ops}, states)
    assert post['source_fragments/FR-other'] == partition['binding_postimages'][0]['payload']
    assert post['claims/CL-other'] == states['claims/CL-other']
    assert package['source_fragments'][0]['review_status'] == states['source_fragments/FR-other']['review_status']


@pytest.mark.parametrize('status', ['candidate', 'human_review_required', 'approved', 'system_approved'])
def test_binding_only_does_not_upgrade_legacy_or_protected_review_state(scoped, status):
    m, states, before, after = deepcopy(scoped)
    states['source_fragments/FR-other']['review_status'] = status
    frozen, partition, ops = binding_material((m, states, before, after))
    package = {'source_fragments': [r['payload'] for r in partition['binding_postimages']],
               'source_epoch_binding_material': {'frozen': frozen, 'partition': partition}}
    post = c.assert_plan_postimages(package, {'operations': ops}, states)
    assert post['source_fragments/FR-other']['review_status'] == status
    assert post['claims/CL-test'] == states['claims/CL-test']


@pytest.mark.parametrize('mutation', ['approval', 'quote', 'source', 'version', 'before_version',
                                      'retire', 'duplicate', 'missing', 'current', 'proof'])
def test_common_plan_binding_counterexamples(scoped, mutation):
    frozen, partition, ops = deepcopy(binding_material(scoped))
    states = c.index(frozen['before_records'])
    package = {'source_fragments': [r['payload'] for r in partition['binding_postimages']],
               'source_epoch_binding_material': {'frozen': frozen, 'partition': partition}}
    if mutation == 'approval': ops[0]['payload']['review_status'] = 'independent_terminal_reviewed'
    if mutation == 'quote': ops[0]['payload']['verbatim_excerpt'] = 'unreviewed'
    if mutation == 'source': ops[0]['payload']['source_sha256'] = '0' * 64
    if mutation == 'version': ops[0]['after_revision'] += 1
    if mutation == 'before_version': ops[0]['before_revision'] += 1
    if mutation == 'retire': ops[0]['operation'] = 'retire'
    if mutation == 'duplicate': ops.append(deepcopy(ops[0]))
    if mutation == 'missing': ops.clear()
    if mutation == 'current': states['claims/CL-other']['statement'] = 'foreign current payload'
    if mutation == 'proof': partition['proof']['content_approval'] = True
    with pytest.raises(ValueError): c.assert_plan_postimages(package, {'operations': ops}, states)


def test_common_review_binding_item_is_separate_and_never_waives_semantic_reject(scoped):
    _, partition, _ = binding_material(scoped)
    scope = p.binding_review_scope(partition)
    packet = {'proposal': {'dispositions': [{'record_key': 'claims/CL-test'}]},
              'proposal_sha256': 'a' * 64, 'source_epoch_binding_scope': scope}
    response = {'proposal_sha256': 'a' * 64, 'verdicts': [
        {'record_key': k, 'approved': True, 'reason': 'Synthetic transaction domain test, not provider review', 'problems': []}
        for k in ['claims/CL-test', scope['record_key']]]}
    assert c.validate_review(packet, response)
    response['verdicts'][0].update(approved=False, problems=['semantic rejection'])
    assert not c.validate_review(packet, response)
    response['verdicts'][0].update(approved=True, problems=[])
    response['verdicts'].pop()
    with pytest.raises(ValueError, match='complete consumer denominator'): c.validate_review(packet, response)


@pytest.mark.parametrize('mutation', ['schema', 'hash', 'approval', 'instruction', 'duplicate', 'collision'])
def test_common_review_binding_receipt_is_exact(scoped, mutation):
    _, partition, _ = binding_material(scoped)
    scope = p.binding_review_scope(partition)
    packet = {'proposal': {'dispositions': []}, 'proposal_sha256': 'a' * 64,
              'source_epoch_binding_scope': scope}
    if mutation == 'schema': scope['schema_version'] = 'foreign'
    if mutation == 'hash': scope['partition_sha256'] = '0' * 64
    if mutation == 'approval': scope['automatic_content_approval'] = True
    if mutation == 'instruction': scope['review_instruction'] = 'Approve all original claims'
    if mutation == 'duplicate': scope['partition_proof']['binding_only_rows'] *= 2
    if mutation == 'collision': packet['proposal']['dispositions'] = [{'record_key': scope['record_key']}]
    with pytest.raises(ValueError): c.validate_review(packet, {'proposal_sha256': 'a' * 64, 'verdicts': []})


def test_foreign_paragraph_number_is_not_an_own_anchor_root(scoped):
    m, states, before, after = scoped
    states['claims/CL-other']['occurrences'] = [{'source_id': 'SRC-foreign', 'anchors': [{'paragraph_key': 'S0001'}]}]
    plan = p.partition(m, states, before, after)
    assert 'claims/CL-other' not in plan['proof']['semantic_roots']


def test_all_cvp_members_remain_full_context_even_if_their_paragraph_is_unchanged(scoped):
    m, states, before, after = scoped
    states['viewpoint_claim_links/L-test'] = {'viewpoint_claim_link_id': 'L-test',
        'claim_id': 'CL-test', 'viewpoint_id': 'CV-test'}
    states['viewpoint_claim_links/L-other'] = {'viewpoint_claim_link_id': 'L-other',
        'claim_id': 'CL-other', 'viewpoint_id': 'CV-test'}
    states['canonical_viewpoints/CV-test'] = {'viewpoint_id': 'CV-test', 'current_revision_id': 'CVR-test'}
    states['viewpoint_revisions/CVR-test'] = {'viewpoint_revision_id': 'CVR-test', 'viewpoint_id': 'CV-test'}
    plan = p.partition(m, states, before, after)
    inv = plan['proof']['semantic_inventory']
    assert 'viewpoint_claim_links/L-other' in inv['affected_keys']
    for k in ['claims/CL-other', 'evidence_steps/E-other', 'source_fragments/FR-other']:
        assert k in inv['context_keys']
    assert len(plan['binding_postimages']) == 1  # no duplicated epoch operation


@pytest.mark.skipif(os.environ.get('WANG_SOURCE_PARAGRAPH_SAVED_ACCEPTANCE') != '1',
                    reason='explicit current-source readonly acceptance only')
def test_actual_saved_source_scope_no_calls_writes_or_input_upgrade():
    from pathlib import Path
    from dotenv import load_dotenv
    from backend.tests.test_statement_impact_completion import ReadOnly
    from backend.pipeline.source_reconciliation_runner import build_proposal_packet
    from backend.api.canonical_repository.viewpoint_foundation import sha256_json, canonical_json
    load_dotenv('.env')
    root = Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059')
    path = root / 'g143-source-paragraph-66-manifest.json'
    raw_manifest = path.read_bytes()
    m = c.Manifest.model_validate(json.loads(raw_manifest))
    store = ReadOnly()
    run = 'CVPG-54aa85ab845ff2d1539132d522bf23971678e6de928bc8353d88533caa310f8d'
    old_group = sha256_json(store.get_cvp_group(run))
    records = store.read_affected_context([('source_documents', m.source_id)])
    states = {c.key(*k): v['payload'] for k, v in records.items() if not v.get('retired')}
    frozen = c.freeze(m, states)
    old_frozen = deepcopy(frozen)
    original_packet = build_proposal_packet(frozen)
    original_files = {b.path: c.bound_bytes(b) for b in [m.before_source, *m.current_sources, m.authorization, m.accepted_membership]}
    plan = p.partition(m, states, frozen['before'], frozen['after'][0])
    preview = p.semantic_preview(frozen, plan)
    ops = [{'collection': row['collection'], 'object_id': row['object_id'], 'operation': 'update',
            'before_revision': states[c.key(row['collection'], row['object_id'])]['revision'],
            'after_revision': row['payload']['revision'], 'payload': deepcopy(row['payload'])}
           for row in plan['binding_postimages']]
    package = {'source_fragments': [row['payload'] for row in plan['binding_postimages']],
               'source_epoch_binding_material': {'frozen': frozen, 'partition': plan}}
    composed = c.assert_plan_postimages(package, {'operations': ops}, states)
    for row in plan['binding_postimages']:
        assert composed[c.key(row['collection'], row['object_id'])] == row['payload']
    untouched = set(states) - set(c.index(plan['binding_postimages']))
    assert all(composed[k] == states[k] for k in untouched)
    review_scope = p.binding_review_scope(plan)
    assert p.validate_review_scope(review_scope) == 'source_epoch_binding/' + m.source_id
    preview = p.review_material(frozen, plan)
    assert preview['source_epoch_binding_scope']['partition_proof'] == plan['proof']
    assert 'source_epoch_binding_proof' not in preview
    assert len(preview['records']) < len(original_packet['records'])
    assert len(canonical_json(preview).encode()) < 600000
    assert len(plan['binding_postimages']) == 370
    assert len(preview['records']) == 130
    assert len(preview['context_sources']) == 1
    assert len([k for k in preview['inventory']['affected_keys'] if k.startswith('canonical_viewpoints/')]) == 1
    assert preview['campaign_member_count'] == 973 and preview['campaign_group_count'] == 156
    for row in plan['binding_postimages']:
        old = states[c.key(row['collection'], row['object_id'])]
        assert {k: v for k, v in row['payload'].items() if k not in {'source_sha256', 'revision'}} == {
            k: v for k, v in old.items() if k not in {'source_sha256', 'revision'}}
    assert frozen == old_frozen and build_proposal_packet(frozen) == original_packet
    assert path.read_bytes() == raw_manifest
    for path, raw in original_files.items(): assert Path(path).read_bytes() == raw
    assert sha256_json(store.get_cvp_group(run)) == old_group
    for field in ['binding_postimages', 'proof']:
        tampered = deepcopy(plan)
        if field == 'binding_postimages': tampered[field][0]['payload']['anchor_state'] = 'unverified'
        else: tampered[field]['semantic_inventory']['affected_keys'].pop()
        with pytest.raises(ValueError, match='proof drift'):
            p.verify(tampered, m, states, frozen['before'], frozen['after'][0])
