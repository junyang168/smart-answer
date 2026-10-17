"""Committed summaries reuse the real domain calls, never a second model gate."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.api.canonical_repository import cvp_management as domain
from backend.api.canonical_repository.cvp_management_authority import validate_evidence
from backend.api.canonical_repository.cvp_recall import seal
from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha, sha256_json
from backend.pipeline import cvp_management_index as subject, cvp_management_models as models
from backend.pipeline.cvp_recall import maintain_reviewed_index, load_reviewed_index
from backend.pipeline.viewpoint_resolution_runtime import call_model, read_artifact
from test_cvp_management import request, materials, proposal, passing
from test_cvp_management_models import envelope
from test_cvp_recall_stages import Client, review_client


def completed(tmp_path, monkeypatch, *, req=None, bundle=None, raw=None):
    req, bundle = req or request(), bundle or materials()
    p = Client(lambda packet: envelope('candidate', candidate=raw or proposal()))
    r = review_client()
    monkeypatch.setattr(models, 'subscription_client', lambda provider, model, effort: p if provider == 'codex' else r)
    proposal_cache = (tmp_path / 'proposal.json').resolve()
    review_cache = (tmp_path / 'review.json').resolve()
    packet = domain.proposal_packet(req, bundle)
    adapter = models.adapter('proposal', req, bundle, packet, client=p)
    proposal_raw = call_model(adapter, packet, proposal_cache)[0]
    candidate = domain.compile_candidate(req, bundle, proposal_raw['candidate'])
    r.behavior = lambda packet: envelope('review', review=passing(candidate))
    packet = domain.review_packet(req, bundle, candidate)
    adapter = models.adapter('review', req, bundle, packet, client=r)
    review_raw = call_model(adapter, packet, review_cache)[0]
    approval = domain.validate_review(req, bundle, candidate, review_raw['review'])
    evidence = {'request': req.model_dump(mode='json'), 'bundle': bundle, 'candidate': candidate,
        'approval': approval, 'proposal_raw': proposal_raw, 'review_raw': review_raw, 'previous': None,
        'prior_findings': [], 'call_keys': {'gpt': 'g', 'opus': 'o'}, 'round_id': 'R',
        'provenance': {'proposal_model': p.model, 'review_model': r.model,
            'proposal_artifact_sha256': read_artifact(proposal_cache)['artifact_sha256'],
            'review_artifact_sha256': read_artifact(review_cache)['artifact_sha256'],
            'created_at': '2026-10-17T00:00:00Z'}}
    package = validate_evidence(evidence)
    view, revision = package['canonical_viewpoints'][0], package['viewpoint_revisions'][0]
    heads = [{'viewpoint_id': view['viewpoint_id'], 'viewpoint_revision_id': view['current_revision_id'],
        'head_sha256': sha256_json({'viewpoint': [view['revision'], semantic_record_sha(view)],
                                   'revision': [revision['revision'], semantic_record_sha(revision)]}),
        **{field: revision[field] for field in ('core_proposition', 'scope', 'proposition_signature')}}]
    return evidence, proposal_cache, review_cache, heads, p, r


def test_committed_metadata_builds_index_with_zero_extra_calls_and_flat_authentication(tmp_path, monkeypatch):
    evidence, p_cache, r_cache, heads, p, r = completed(tmp_path / 'calls', monkeypatch)
    proofs = subject.write_committed_summary_proofs(evidence, p_cache, r_cache, heads, tmp_path / 'proofs')
    assert len(proofs) == 1
    before = len(p.requests), len(r.requests)
    forbidden = lambda *args: (_ for _ in ()).throw(AssertionError('no extra summary model calls'))
    index, metrics = maintain_reviewed_index(heads, proposer=p, reviewer=r, output_dir=tmp_path / 'index',
        max_request_bytes=500000, max_calls=0, approved_card_proofs=proofs, invoke=forbidden)
    assert metrics['model_calls'] == 0
    assert index['cards'][0]['summary'] == evidence['candidate']['summaries'][0]['summary']
    assert len(index['summary_proofs']) == 1
    assert load_reviewed_index(heads, proposer=p, reviewer=r, output_dir=tmp_path / 'index') == index
    # Direct evidence leaves suffice; no historical index/maintenance root walk.
    (tmp_path / 'index/index-maintenance.json').unlink()
    second, metrics = maintain_reviewed_index(heads, previous_output_dir=tmp_path / 'index',
        proposer=p, reviewer=r, output_dir=tmp_path / 'next', max_request_bytes=500000,
        max_calls=0, invoke=forbidden)
    assert second == index and metrics['model_calls'] == 0
    assert (len(p.requests), len(r.requests)) == before


@pytest.mark.parametrize('kind', ['summary', 'head', 'raw', 'missing', 'unknown'])
def test_forged_stale_unknown_and_missing_committed_summary_proofs_reject(tmp_path, monkeypatch, kind):
    evidence, p_cache, r_cache, heads, p, r = completed(tmp_path / 'calls', monkeypatch)
    proof = subject.write_committed_summary_proofs(evidence, p_cache, r_cache, heads, tmp_path / 'proofs')[0]
    if kind == 'summary':
        proof = seal({**{k: v for k, v in proof.items() if k != 'artifact_sha256'}, 'summary': '新增了教授未說的內容。'})
    elif kind == 'head':
        heads = deepcopy(heads)
        heads[0]['head_sha256'] = '0' * 64
    elif kind == 'raw':
        raw = read_artifact(r_cache)
        raw['response']['review']['novelty_approved'] = False
        raw['response_sha256'] = sha256_json(raw['response'])
        raw['artifact_sha256'] = sha256_json({k: v for k, v in raw.items() if k != 'artifact_sha256'})
        r_cache.write_text(json.dumps(raw))
    elif kind == 'missing':
        r_cache.unlink()
    else:
        r_cache.unlink()
        r_cache.with_name(r_cache.stem + '.call-failure.json').write_text('{"status":"unknown"}')
    with pytest.raises((ValueError, FileNotFoundError)):
        maintain_reviewed_index(heads, proposer=p, reviewer=r, output_dir=tmp_path / 'index',
            max_request_bytes=500000, max_calls=0, approved_card_proofs=[proof])
    assert not (tmp_path / 'index/index.json').exists()
    assert len(p.requests) == len(r.requests) == 1


def test_writer_requires_actual_final_raw_and_current_semantic_head(tmp_path, monkeypatch):
    evidence, p_cache, r_cache, heads, p, r = completed(tmp_path / 'calls', monkeypatch)
    altered = deepcopy(evidence)
    altered['provenance']['review_artifact_sha256'] = '0' * 64
    with pytest.raises(ValueError):
        subject.write_committed_summary_proofs(altered, p_cache, r_cache, heads, tmp_path / 'bad')
    altered_heads = deepcopy(heads)
    altered_heads[0]['core_proposition'] = '另一個判斷。'
    with pytest.raises(ValueError, match='stale or different'):
        subject.write_committed_summary_proofs(evidence, p_cache, r_cache, altered_heads, tmp_path / 'bad-head')
    assert not (tmp_path / 'bad-head').exists()



def test_mixed_index_reuses_domain_proof_and_only_initializes_unapproved_cards(tmp_path, monkeypatch):
    from test_cvp_recall import heads as old_heads
    evidence, p_cache, r_cache, heads, p, r = completed(tmp_path / 'calls', monkeypatch)
    proofs = subject.write_committed_summary_proofs(evidence, p_cache, r_cache, heads, tmp_path / 'proofs')
    p.behavior = r.behavior = None
    base, _ = maintain_reviewed_index(old_heads(1), proposer=p, reviewer=r, output_dir=tmp_path / 'base',
        max_request_bytes=500000, max_calls=2)
    counts = len(p.requests), len(r.requests)
    current = [*old_heads(1), *heads]
    mixed, metrics = maintain_reviewed_index(current, previous_output_dir=tmp_path / 'base',
        proposer=p, reviewer=r, output_dir=tmp_path / 'mixed', max_request_bytes=500000,
        max_calls=0, approved_card_proofs=proofs)
    assert metrics['model_calls'] == 0
    assert len(mixed['summary_proofs']) == 2
    assert (len(p.requests), len(r.requests)) == counts
    # A genuinely unseen legacy card still uses the normal two-call metadata gate.
    extra = old_heads(2)[1]
    result, metrics = maintain_reviewed_index([*current, extra], previous_output_dir=tmp_path / 'mixed',
        proposer=p, reviewer=r, output_dir=tmp_path / 'extra', max_request_bytes=500000, max_calls=2)
    assert metrics['model_calls'] == 2
    assert len(json.loads(p.requests[-1]['user_prompt'])['viewpoints']) == 1
    assert len(result['cards']) == 3


def test_multiple_summaries_share_direct_evidence_without_other_card_dependencies(tmp_path, monkeypatch):
    from backend.api.canonical_repository.cvp_claim_input import compile_input
    req = request(creation_slots={'canonical_viewpoints': 2, 'viewpoint_revisions': 2, 'viewpoint_claim_links': 2})
    bundle = deepcopy(materials())
    bundle.pop('artifact_sha256')
    claim = bundle['records'][0]['payload']
    claim['statement'] = '人子是神。人子審判。'
    bundle['records'][0]['content_sha256'] = semantic_record_sha(claim)
    bundle['claim_input'] = compile_input([claim], bundle['claim_input']['policy'], claim_refs={'C1': 'c1'})
    bundle = seal(bundle)
    raw = proposal()
    raw['components'] = [
        {'claim_ref': 'c1', 'spans': [{'text_ref': 't1', 'start_char': 0, 'end_char': 5}]},
        {'claim_ref': 'c1', 'spans': [{'text_ref': 't1', 'start_char': 5, 'end_char': 10}]}]
    raw['component_dispositions'].append({'component_ref': 'k2', 'disposition': 'connected',
        'target_ref': 'nv2', 'link_ref': 'nl2', 'reason': '另一個判斷。'})
    raw['changes'][2]['fields_json'] = json.dumps({'viewpoint_id': 'nv1', 'claim_id': 'c1', 'link_type': 'equivalent_component'})
    second_revision = json.loads(raw['changes'][1]['fields_json'])
    second_revision.update(viewpoint_id='nv2', core_proposition='人子執行審判。')
    raw['changes'].extend([{'ref': 'nv2', 'fields_json': json.dumps({'current_revision_id': 'nr2'})},
        {'ref': 'nr2', 'fields_json': json.dumps(second_revision)},
        {'ref': 'nl2', 'fields_json': json.dumps({'viewpoint_id': 'nv2', 'claim_id': 'c1', 'link_type': 'equivalent_component'})}])
    raw['summaries'].append({'ref': 'nv2', 'summary': '教授在此說明人子執行審判。'})
    evidence, p_cache, r_cache, _, p, r = completed(tmp_path / 'calls', monkeypatch, req=req, bundle=bundle, raw=raw)
    package = validate_evidence(evidence)
    heads = []
    for view in package['canonical_viewpoints']:
        revision = next(row for row in package['viewpoint_revisions'] if row['viewpoint_revision_id'] == view['current_revision_id'])
        heads.append({'viewpoint_id': view['viewpoint_id'], 'viewpoint_revision_id': view['current_revision_id'],
            'head_sha256': sha256_json({'viewpoint': [view['revision'], semantic_record_sha(view)],
                                       'revision': [revision['revision'], semantic_record_sha(revision)]}),
            **{field: revision[field] for field in ('core_proposition', 'scope', 'proposition_signature')}})
    proofs = subject.write_committed_summary_proofs(evidence, p_cache, r_cache, heads, tmp_path / 'proofs')
    assert len(proofs) == 2 and proofs[0]['evidence'] == proofs[1]['evidence']
    for proof in proofs:
        assert len(subject.authenticate_committed_summary_proof(proof, proposer=p, reviewer=r)) == 1
