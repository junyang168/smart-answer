"""Direct index metadata proofs from the actual committed CVP2 approval.

The already completed GPT proposal and Opus review approve these summaries.
This module authenticates those exact saved calls; it never runs another review,
reads current source files, changes knowledge records, or follows old index roots.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from backend.api.canonical_repository import cvp_management as domain
from backend.api.canonical_repository.cvp_management_authority import validate_evidence
from backend.api.canonical_repository.cvp_recall import seal, verify
from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha, sha256_json
from backend.pipeline import cvp_management_models as models
from backend.pipeline.viewpoint_resolution_runtime import (
    model_request_identity, read_artifact, read_saved_model_response, response_adapter, write_immutable,
)

VERSION = 'wang_cvp_committed_summary_proof_v1'


def _authenticated_evidence(evidence, proposal_cache, review_cache, *, proposer=None, reviewer=None):
    package = validate_evidence(evidence)
    request = domain.Request.model_validate(evidence['request'])
    bundle = evidence['bundle']
    packets = [domain.proposal_packet(request, bundle, previous=evidence['previous'],
                                     prior_findings=evidence['prior_findings']),
               domain.review_packet(request, bundle, evidence['candidate'],
                                    prior_findings=evidence['prior_findings'])]
    for cache, packet, roles, client, name, key in [
        (proposal_cache, packets[0], ['proposal', 'correction'], proposer, 'proposal_raw', 'proposal_artifact_sha256'),
        (review_cache, packets[1], ['review', 'initial_review', 'final_review'], reviewer, 'review_raw', 'review_artifact_sha256')]:
        cache = Path(cache)
        if not cache.is_absolute():
            raise ValueError('committed summary requires absolute original call cache paths')
        saved = read_artifact(cache)
        choices = []
        for role in roles:
            if role == 'correction' and evidence['previous'] is None:
                continue
            adapter = models.adapter(role, request, bundle, packet, client=client)
            expected = model_request_identity(response_adapter(adapter, saved), packet)
            if all(saved.get(field) == value for field, value in expected.items()):
                choices.append((role, adapter))
        if len(choices) != 1:
            raise ValueError('committed summary has no exact authentic role/config/material call')
        role, adapter = choices[0]
        raw = read_saved_model_response(adapter, packet, cache)
        parsed = adapter._response_model.model_validate(raw).model_dump(mode='json')
        parsed = models.validate_response(role, parsed)
        if (raw != evidence[name] or parsed != models.validate_response(role, evidence[name])
                or saved.get('artifact_sha256') != evidence['provenance'][key]):
            raise ValueError('committed summary model evidence differs from its actual saved raw')
    return package


def _approved_cards(evidence, package, heads, *, only_current_subset=False):
    rows = {(row['collection'], row['object_id']): (row['payload'], row['revision'])
            for row in evidence['bundle']['records']}
    for col, values in package.items():
        if col not in domain.KNOWLEDGE_COLLECTIONS:
            continue
        id_field = domain.KNOWLEDGE_COLLECTIONS[col][1]
        for value in values:
            rows[col, value[id_field]] = value, value['revision']
    live = {head['viewpoint_id']: head for head in heads}
    if len(live) != len(heads):
        raise ValueError('committed summary current head list has duplicate viewpoints')
    cards = []
    for summary in evidence['candidate']['summaries']:
        ref = evidence['candidate']['references'][summary['ref']]
        vid = ref['object_id']
        if only_current_subset and vid not in live:
            continue
        view, vrev = rows['canonical_viewpoints', vid]
        if view['identity_status'] != 'active':
            continue
        revision, rrev = rows['viewpoint_revisions', view['current_revision_id']]
        # The generic SQL importer materializes optional null fields in old
        # packages. Direct historical proofs used the pre-import payload;
        # accept exactly those two deterministic representations.
        normalized = domain.KNOWLEDGE_COLLECTIONS['canonical_viewpoints'][0].model_validate(view).model_dump(mode='json')
        variants = [{'viewpoint_id': vid, 'viewpoint_revision_id': view['current_revision_id'],
            'head_sha256': sha256_json({'viewpoint': [vrev, semantic_record_sha(payload)],
                                       'revision': [rrev, semantic_record_sha(revision)]}),
            **{field: revision[field] for field in ('core_proposition', 'scope', 'proposition_signature')}}
            for payload in (view, normalized)]
        if live.get(vid) not in variants:
            raise ValueError('committed summary is bound to a stale or different current head')
        cards.append({**live[vid], 'summary': summary['summary']})
    return cards


def write_committed_summary_proofs(evidence, proposal_cache, review_cache, heads, output_dir):
    """Authenticate actual raw, bind final committed head and save direct proofs.

    ``heads`` must be actual current SQL heads from the caller's readback, not
    simulated pre-commit heads. Source material is read from the saved bundle;
    no current physical source is reparsed to re-review approved metadata.
    """
    package = _authenticated_evidence(evidence, proposal_cache, review_cache)
    cards = _approved_cards(evidence, package, heads)
    if not cards:
        return []
    root = Path(output_dir).resolve()
    evidence_path = root / 'committed-evidence.json'
    write_immutable(evidence_path, evidence)
    binding = {'path': str(evidence_path), 'file_sha256': hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
               'payload_sha256': sha256_json(evidence)}
    proofs = []
    for card in cards:
        proof = seal({'schema_version': VERSION, 'heads': [{k: v for k, v in card.items() if k != 'summary'}],
            'summary': card['summary'], 'evidence': binding,
            'package_sha256': sha256_json(package),
            'candidate_sha256': evidence['candidate']['artifact_sha256'],
            'approval_sha256': evidence['approval']['artifact_sha256'],
            'proposal_cache': str(Path(proposal_cache).resolve()), 'review_cache': str(Path(review_cache).resolve())})
        write_immutable(root / ('summary-' + sha256_json(card['viewpoint_id'])[:24] + '.json'), proof)
        proofs.append(proof)
    return proofs


def authenticate_committed_summary_proof(proof, *, proposer=None, reviewer=None):
    """Read one flat proof and its direct original calls, with no model calls."""
    verify(proof, VERSION)
    binding = proof['evidence']
    path = Path(binding['path'])
    if not path.is_absolute() or hashlib.sha256(path.read_bytes()).hexdigest() != binding['file_sha256']:
        raise ValueError('committed summary saved evidence file drift')
    evidence = read_artifact(path)
    if sha256_json(evidence) != binding['payload_sha256']:
        raise ValueError('committed summary saved evidence content drift')
    package = _authenticated_evidence(evidence, proof['proposal_cache'], proof['review_cache'],
                                      proposer=proposer, reviewer=reviewer)
    if (sha256_json(package) != proof['package_sha256']
            or evidence['candidate']['artifact_sha256'] != proof['candidate_sha256']
            or evidence['approval']['artifact_sha256'] != proof['approval_sha256']):
        raise ValueError('committed summary reviewed afterimage binding differs')
    cards = _approved_cards(evidence, package, proof['heads'], only_current_subset=True)
    matches = [card for card in cards if card == {**proof['heads'][0], 'summary': proof['summary']}]
    if len(proof['heads']) != 1 or len(matches) != 1:
        raise ValueError('committed summary differs from its exact approved metadata')
    return matches
