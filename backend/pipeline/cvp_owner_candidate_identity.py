"""Owner-directed same-batch identity revision, never a rewritten model verdict.

The owner decides identity, GPT proposes the bounded effective content, and the
ordinary independent reviewer still reviews the entire batch before SQL writes.
Original calls and their once budgets remain untouched.
"""
from copy import deepcopy
from pathlib import Path

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_batch_resolution import (
    CanonicalViewpointProposalResponse, ContextualIdentityConsolidationResponse,
    IdentityConsolidationResponse, canonicalize_proposal, anchor_proposal_spans,
)
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable

SCHEMA = 'wang_owner_candidate_identity_revision_v1'
NAME = 'owner-candidate-identity-authorization.json'
RAW = 'raw-owner-identity-proposal.json'
PROOF = 'owner-identity-validation.json'


def authorize(*, root, campaign, run_id, keys, keep, owner_quote):
    """Compile the human direction from exact saved inputs, without approving content."""
    from backend.pipeline.cvp_sql_commit import binding
    packet = read_artifact(root/'batch-packet.json')
    raw = read_artifact(root/'raw-proposal.json')
    proposal, _ = canonicalize_proposal(raw['response'])
    proposal, _ = anchor_proposal_spans(proposal, claim_statements={c['claim_id']:c['statement'] for c in packet['claims']})
    proposal = CanonicalViewpointProposalResponse.model_validate(proposal).model_dump(mode='json')
    ids = candidate_claims(proposal, keys)
    claims = {c['claim_id']:c for c in packet['claims']}
    direction = {'schema_version':SCHEMA, 'campaign_sha256':campaign, 'run_id':run_id,
        'authorized_by':'owner', 'owner_quote':owner_quote,
        'decision':'authorize_same_identity_revision', 'batch_id':packet['batch_id'],
        'candidate_keys':keys, 'retained_local_key':keep, 'claim_ids':ids,
        'claim_pins':{cid:{'revision':claims[cid]['pinned_claim_revision'],
                          'sha256':claims[cid]['claim_revision_sha256']} for cid in ids},
        'saved_inputs':{n:binding(root/n) for n in ('batch-packet.json','raw-proposal.json','raw-consolidation.json')}}
    direction['artifact_sha256'] = sha256_json(direction)
    validate_direction(direction, proposal, read_artifact(root/'raw-consolidation.json')['response'], packet, root)
    return direction


def candidate_claims(proposal, keys):
    return sorted({d['claim_id'] for d in proposal['claim_decisions']
                   if any(c.get('local_new_viewpoint_key') in keys for c in d['components'])})


def validate_direction(direction, proposal, consolidation, packet, root):
    from backend.pipeline.cvp_sql_commit import binding, check_binding
    if (direction.get('schema_version') != SCHEMA or direction.get('authorized_by') != 'owner'
            or not direction.get('owner_quote') or direction.get('batch_id') != packet['batch_id']
            or direction.get('decision') != 'authorize_same_identity_revision'):
        raise ValueError('owner candidate identity direction missing or foreign')
    if direction.get('artifact_sha256') != sha256_json({k:v for k,v in direction.items() if k!='artifact_sha256'}):
        raise ValueError('owner candidate direction seal drift')
    keys = direction['candidate_keys']; keep = direction['retained_local_key']
    if len(keys) != 2 or len(set(keys)) != 2 or keep not in keys:
        raise ValueError('bounded same-identity direction needs exactly two distinct candidates')
    candidates = {x['local_key']: x for x in proposal['new_viewpoint_candidates']}
    if not set(keys) <= set(candidates) or candidate_claims(proposal, keys) != direction['claim_ids']:
        raise ValueError('owner candidate/Claim identity drift')
    parsed = ContextualIdentityConsolidationResponse.model_validate(consolidation)
    if (not parsed.unresolved_items or
            not {x.local_key for x in parsed.unresolved_items} <= set(keys)
            or {x.local_key for x in parsed.verdicts} | {x.local_key for x in parsed.unresolved_items}
                != set(candidates)):
        raise ValueError('owner direction cannot override unrelated or missing dispositions')
    keep_verdict = next((x for x in parsed.verdicts if x.local_key == keep), None)
    if keep_verdict is None or keep_verdict.verdict != 'new':
        raise ValueError('owner revision cannot change an existing Registry identity')
    for name in ('batch-packet.json', 'raw-proposal.json', 'raw-consolidation.json'):
        pin = direction['saved_inputs'][name]; check_binding(pin)
        if pin != binding(root / name):
            raise ValueError('owner candidate original model input/output location drift')
    claims = {c['claim_id']: c for c in packet['claims']}
    actual = {cid: {'revision': claims[cid]['pinned_claim_revision'],
                    'sha256': claims[cid]['claim_revision_sha256']} for cid in direction['claim_ids']}
    if actual != direction['claim_pins']:
        raise ValueError('owner candidate current Claim version drift')
    return parsed


def template(proposal, keys, keep):
    """Only mechanical references move; no professor statement is synthesized."""
    result = deepcopy(proposal)
    result['new_viewpoint_candidates'] = [x for x in result['new_viewpoint_candidates']
                                         if x['local_key'] not in keys or x['local_key'] == keep]
    for d in result['claim_decisions']:
        for c in d['components']:
            if c.get('local_new_viewpoint_key') in keys:
                c['local_new_viewpoint_key'] = keep
    relations = []
    for r in result['viewpoint_relations']:
        for field in ('source_local_key', 'target_local_key'):
            if r.get(field) in keys:
                r[field] = keep
        if r.get('source_local_key') == keep and r.get('target_local_key') == keep:
            continue  # A within-identity relation would be an invalid self-edge.
        relations.append(r)
    result['viewpoint_relations'] = relations
    for s in result['structures']:
        seen = set(); focal = []
        for f in s['focal']:
            if f.get('local_key') in keys:
                f['local_key'] = keep
                if keep in seen:
                    continue
                seen.add(keep)
            focal.append(f)
        s['focal'] = focal
    return result


def validate_bounded_proposal(original, revised, keys, keep):
    expected = template(original, keys, keep)
    actual = deepcopy(revised)
    if len(actual['new_viewpoint_candidates']) != len(expected['new_viewpoint_candidates']):
        raise ValueError('owner identity revision changed unrelated candidate count')
    for a, e in zip(actual['new_viewpoint_candidates'], expected['new_viewpoint_candidates']):
        if e['local_key'] == keep:
            if a['local_key'] != keep:
                raise ValueError('owner retained local identity drift')
            a.clear(); a.update(deepcopy(e))  # Content is new GPT proposal, not approved here.
    target_claims = set(candidate_claims(original, keys))
    if len(actual['claim_decisions']) != len(expected['claim_decisions']):
        raise ValueError('owner identity revision changed Claim membership')
    for a, e in zip(actual['claim_decisions'], expected['claim_decisions']):
        if a['claim_id'] == e['claim_id'] and e['claim_id'] in target_claims:
            for ac, ec in zip(a['components'], e['components']):
                if ec.get('local_new_viewpoint_key') == keep:
                    ac['reason'] = ec['reason']
    if len(actual['viewpoint_relations']) != len(expected['viewpoint_relations']):
        raise ValueError('owner identity revision changed relation scope')
    for a, e in zip(actual['viewpoint_relations'], expected['viewpoint_relations']):
        if keep in (e.get('source_local_key'), e.get('target_local_key')):
            a['reason'] = e['reason']
    if len(actual['structures']) != len(expected['structures']):
        raise ValueError('owner identity revision changed unrelated structures')
    for a, e in zip(actual['structures'], expected['structures']):
        if any(f.get('local_key') == keep for f in e['focal']):
            for field in ('central_synthesis', 'reason', 'unresolved_items'):
                a[field] = deepcopy(e[field])
    if actual != expected:
        raise ValueError('owner identity revision exceeded exact candidates/references/content scope')


def own_sources(packet, claim_ids):
    from backend.pipeline.viewpoint_evidence_source_context import load_context_source
    result = {}; claims = {c['claim_id']: c for c in packet['claims']}
    for cid in claim_ids:
        claim = claims[cid]
        for evidence in claim['evidence']:
            ctx = evidence.get('source_context')
            if not ctx or ctx['source_id'] != claim['source_id']:
                raise ValueError('owner identity revision needs actual own body, not a title')
            _, _, projection = load_context_source({**ctx, 'source_type': 'sermon_transcript'})
            source = {'source_id':ctx['source_id'], 'source_path':ctx['source_path'],
                      'source_file_sha256':ctx['source_file_sha256'],
                      'source_body_sha256':ctx['source_body_sha256'],
                      'paragraph_key_encoding':'S + 1-based array index, padded to at least four digits',
                      'paragraph_texts': [r['text'] for r in projection.spoken_rows]}
            if ctx['source_id'] in result and result[ctx['source_id']] != source:
                raise ValueError('owner identity own source bindings conflict')
            result[ctx['source_id']] = source
    return list(result.values())


def revision_payload(*, original, consolidation, packet, direction):
    keys, keep = direction['candidate_keys'], direction['retained_local_key']
    return {'batch_id':packet['batch_id'],
               'target_claims':[c for c in packet['claims'] if c['claim_id'] in direction['claim_ids']],
               'owner_identity_direction': direction, 'original_proposal':original,
               'original_unresolved_consolidation':consolidation,
               'own_source_bodies':own_sources(packet,direction['claim_ids']),
               'required_proposal_template':template(original,keys,keep),
               'instruction': ('Owner已判这两个候选为同一CVP，授权一次最小新方案，不是原模型pass。'
                 '返回完整标准proposal，只改保留候选的命题/signature/限定、两条Claim相关component的reason、'
                 '合并后相关relation的reason和相关structure的central_synthesis/reason/unresolved_items。'
                 '其他字段逐字照required_proposal_template，特别Claim ID、spans、Evidence/fragment、disposition，'
                 '非目标候选/结构/关系完全不变。自身全文用于核查两种措辞和神性上下文，不能改来源或丢限定。'
                 '保留两条Claim各自证据及原文全部承重限定；不能因归为一个观点而假设来源是同一录音。')}


def reviewer_direction(direction):
    """Semantic direction only; physical pins are validated and retained by code."""
    return {k:direction[k] for k in ('decision','owner_quote','candidate_keys',
                                    'retained_local_key','claim_ids','artifact_sha256')}


def revise(*, proposal, consolidation, packet, root, proposer, call):
    direction = read_artifact(root / NAME)
    original = proposal.model_dump(mode='json')
    parsed = validate_direction(direction, original, consolidation, packet, root)
    keys, keep = direction['candidate_keys'], direction['retained_local_key']
    payload = revision_payload(original=original, consolidation=consolidation, packet=packet, direction=direction)
    raw, calls, seconds = call(proposer, payload, root / RAW)
    normalized, _ = canonicalize_proposal(raw)
    normalized, _ = anchor_proposal_spans(normalized, claim_statements={c['claim_id']:c['statement'] for c in packet['claims']})
    revised = CanonicalViewpointProposalResponse.model_validate(normalized)
    validate_bounded_proposal(original, revised.model_dump(mode='json'), keys, keep)
    from backend.pipeline.cvp_sql_commit import binding
    write_immutable(root / PROOF, {'schema_version':SCHEMA, 'authorization':binding(root / NAME),
        'original_proposal':binding(root/'raw-proposal.json'),
        'original_consolidation':binding(root/'raw-consolidation.json'),
        'new_proposal':binding(root/RAW), 'effective_proposal_sha256':sha256_json(revised.model_dump(mode='json')),
        'identity_decided_by':'owner', 'content_review':'pending_independent_opus',
        'original_unresolved_preserved':True, 'claim_source_mutations':0})
    # Existing verdicts are consumed verbatim, never relabeled as a new model
    # answer. Only the authorized removed local identity no longer needs one.
    verdicts = [x for x in parsed.verdicts if x.local_key not in keys or x.local_key == keep]
    return revised, IdentityConsolidationResponse(verdicts=verdicts), calls, seconds
