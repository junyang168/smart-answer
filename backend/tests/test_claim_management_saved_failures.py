"""Real rejected inputs exercise v2 contracts, never become new approvals.

Set WKP435_SAVED_FAILURE_ROOT to the original campaign directory. These tests
only read archives; no production SQL, content models, or old-budget extension.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from backend.pipeline import claim_management as cm
from backend.pipeline import source_reconciliation as source
from backend.pipeline.source_reconciliation_runner import read, body
from backend.api.canonical_repository.postgres_store import record_content_sha

CATALOG = json.loads((Path(__file__).parent / 'fixtures/claim_management/saved_failures_v2.json').read_text())['cases']


@pytest.fixture(params=CATALOG, ids=lambda c: c['category'] + '-' + c['stage'])
def saved(request):
    directory = os.environ.get('WKP435_SAVED_FAILURE_ROOT')
    if not directory:
        pytest.skip('explicit original rejected-input archive required')
    case = request.param
    root = Path(directory) / case['root']
    original = {}
    for name, digest in case['bindings'].items():
        path = root / name
        original[path] = path.read_bytes()
        assert hashlib.sha256(original[path]).hexdigest() == digest
    frozen = body(read(root / 'scoped-materials.json'))
    raw = read(root / (case['stage'] + '.raw.json'))
    packet = body(read(root / (case['stage'] + '.packet.json')))
    yield case, frozen, raw, packet
    assert {path: path.read_bytes() for path in original} == original


def states(frozen):
    pins = {source.key(r['collection'], r['object_id']): r['pin'] for r in frozen['sql_scope']['read_pins']}
    return {key: {'payload': payload, 'revision': pins[key][0], 'content_sha256': pins[key][1],
                  'retired': pins[key][2]} for key, payload in source.index(frozen['before_records']).items()}


def contract(frozen, before, *, operation='Review', permissions=(), target=None):
    target = target or next(k for k in before if k.startswith('claims/'))
    manifest = frozen['manifest']
    return cm.Request(action_id='isolated-readonly-rejected-input-test', operation=operation, allowed_operations=[operation],
        target_keys=[target], permissions=list(permissions),
        source_bindings={manifest['source_id']: frozen['after'][0]['file']},
        authorization=manifest['authorization'], policy=frozen['claim_issue_context']['policy'])


def test_all_rejected_leaves_enter_as_actual_existing_candidates_without_fabricated_origin(saved):
    case, frozen, raw, packet = saved
    assert raw['model_id'] == 'claude-opus-5-5' and raw['backend'] == 'claude_subscription'
    assert raw['response_sha256'] == cm.sha256_json(raw['response'])
    rejected = [v['record_key'] for v in raw['response']['verdicts'] if not v['approved'] or v['problems']]
    assert rejected == case['rejected_keys']
    before = states(frozen)
    original = deepcopy(before)
    candidate = cm.existing_candidate(before)
    request = contract(frozen, before)
    post, writes = cm.validate_candidate(request, before, candidate, {})
    assert not writes and post == {k: v['payload'] for k, v in before.items()}
    assert candidate.records == [] and before == original
    assert set(d.record_key for d in candidate.dispositions) == set(before)
    # Older rejected calls retain their actual old authority and limits. They
    # cannot be consumed as permission or as a new v2 qualification.
    with pytest.raises(ValueError):
        cm.load_authority(request)


def test_rejected_existing_scope_cannot_drop_readonly_dependencies(saved):
    _, frozen, _, _ = saved
    before = states(frozen)
    request = contract(frozen, before)
    candidate = cm.existing_candidate(before)
    assert len(before) > 1
    candidate.dispositions.pop()
    with pytest.raises(ValueError, match='dependency disposition'):
        cm.validate_candidate(request, before, candidate, {})


def test_real_shared_owner_does_not_become_writable_during_cleanup(saved):
    case, frozen, raw, _ = saved
    if case['category'] != 'shared_owners_and_permission':
        pytest.skip('specific shared-owner counterexample')
    before = states(frozen)
    rejected = [k for k in case['rejected_keys'] if k.startswith('claims/')]
    assert len(rejected) >= 2
    target, other = rejected[:2]
    request = contract(frozen, before, operation='Update', target=target, permissions=[source.Permission(
        collection='claims', object_id=target.split('/',1)[1], fields=['statement'])])
    own = {frozen['manifest']['source_id']: frozen['after'][0]}
    paragraph_key, paragraph = next(iter(frozen['after'][0]['paragraphs'].items()))
    candidate = cm.existing_candidate(before)
    candidate.dispositions = [d for d in candidate.dispositions if d.record_key != other] + [cm.ObjectDisposition(
        record_key=other, action='update', reason='unauthorized test mutation', citations=[cm.Citation(
            source_id=frozen['manifest']['source_id'], paragraph_key=paragraph_key, exact_quote=paragraph['text'])])]
    candidate.records = [source.Row(collection='claims', object_id=other.split('/',1)[1],
        payload={**before[other]['payload'], 'statement': 'unauthorized test mutation'})]
    with pytest.raises(ValueError, match='read-only owner|protected'):
        cm.validate_candidate(request, before, candidate, own)


def test_real_final_hash_rejection_is_a_verified_metadata_projection_without_rewriting_words(saved):
    case, frozen, raw, packet = saved
    if case['category'] != 'mechanical_hash_and_context_only' or case['stage'] != 'final_review':
        pytest.skip('specific final hash counterexample')
    rows = [source.Row.model_validate(row) for row in packet['proposal']['records'] if row['collection'] == 'source_fragments']
    assert len(rows) == 1
    source_id = frozen['manifest']['source_id']
    payload = rows[0].payload
    citation = cm.Citation(source_id=source_id, paragraph_key=payload['paragraph_key'], exact_quote=payload['verbatim_excerpt'])
    sources = {source_id: frozen['after'][0]}
    cm.validate_citations([citation], sources)
    candidate = cm.Candidate(records=rows, dispositions=[cm.ObjectDisposition(record_key=rows[0].key,
        action='create', reason='Test mechanical projection of the original rejected fragment', citations=[citation])])
    original = candidate.model_dump(mode='json')
    normalized, receipt = cm.normalize_candidate(original, sources)
    before, after = original['records'][0]['payload'], normalized.records[0].payload
    assert after['verbatim_excerpt_sha256'] == hashlib.sha256(after['verbatim_excerpt'].encode()).hexdigest()
    assert after['paragraph_text_sha256'] == hashlib.sha256(sources[source_id]['paragraphs'][payload['paragraph_key']]['text'].encode()).hexdigest()
    assert after['source_sha256'] == sources[source_id]['body_sha256']
    assert {k: v for k, v in before.items() if k not in cm.FRAGMENT_HASH_FIELDS} == {
        k: v for k, v in after.items() if k not in cm.FRAGMENT_HASH_FIELDS}
    assert candidate.model_dump(mode='json') == original
    assert receipt['reader_visible_text_unchanged'] and receipt['semantic_decisions_unchanged']
    assert set(c['field'] for c in receipt['changes']) <= cm.FRAGMENT_HASH_FIELDS
    assert any(not v['approved'] for v in raw['response']['verdicts'])


def test_real_source_ambiguity_is_human_content_work_not_a_hash_or_schema_decision(saved):
    case, frozen, raw, _ = saved
    if case['category'] != 'source_uncertainty':
        pytest.skip('specific original text ambiguity')
    verdict = next(v for v in raw['response']['verdicts'] if v['record_key'].startswith('claims/') and not v['approved'])
    assert 'source_unresolved' in ' '.join(verdict['problems'])
    paragraph = frozen['after'][0]['paragraphs']['S0066']
    finding = cm.Finding(target_key=verdict['record_key'], component='statement', category='source_uncertain',
        reason=verdict['reason'], acceptance_condition='Resolve the original transcription and passage-range ambiguity',
        human_question='Confirm the uncertain passage numbers in the original source', citations=[cm.Citation(
            source_id=frozen['manifest']['source_id'], paragraph_key='S0066', exact_quote=paragraph['text'])])
    sources = {frozen['manifest']['source_id']: frozen['after'][0]}
    cm.validate_citations(finding.citations, sources)
    identity = finding.identity('original-rejected-action')
    renamed = finding.model_copy(deep=True)
    renamed.reason += ' (different wording)'
    assert renamed.identity('original-rejected-action') == identity
    assert cm.failure_reason({identity: finding.model_dump(mode='json')}) == 'human_required'
    engineering = finding.model_copy(update={'category': 'engineering', 'human_question': None})
    assert cm.failure_reason({identity: engineering.model_dump(mode='json')}) == 'engineering_required'
