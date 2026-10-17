"""Pure CVP index/recall contracts. No store, files, model calls or approvals."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .viewpoint_batch_resolution import (
    ClaimGroupingResponse, DEFAULT_BATCH_SIZE, validate_grouping,
)
from .viewpoint_foundation import canonical_json, sha256_json

INDEX_VERSION = 'wang_cvp_recall_index_v1'
RECALL_VERSION = 'wang_cvp_recall_v1'


def seal(body: Mapping[str, Any]) -> dict:
    return {**body, 'artifact_sha256': sha256_json(body)}


def verify(document: Mapping[str, Any], version: str) -> None:
    if (document.get('schema_version') != version or
            document.get('artifact_sha256') != sha256_json({
                k: v for k, v in document.items() if k != 'artifact_sha256'})):
        raise ValueError('CVP recall artifact version or content changed')


def work_groups(grouping: Mapping[str, Any], claim_ids: Sequence[str]) -> list[dict]:
    """Consume existing grouping, preserving its accepted order and membership."""
    if not claim_ids or len(set(claim_ids)) != len(claim_ids):
        raise ValueError('grouping requires an exact nonempty Claim cohort')
    parsed = ClaimGroupingResponse.model_validate(grouping)
    validate_grouping(grouping=parsed, scope_label=parsed.scope_label, claim_ids=claim_ids)
    if any(len(g.claim_ids) > DEFAULT_BATCH_SIZE for g in parsed.groups):
        raise ValueError('accepted Claim group exceeds 20; refine grouping before execution')
    return [{'group_key': g.group_key, 'claim_ids': list(g.claim_ids)} for g in parsed.groups]


def build_index(heads: Sequence[Mapping[str, Any]], summaries: Sequence[Mapping[str, Any]]) -> dict:
    """Join current heads to authenticated summaries supplied by the caller.

    A checksum is integrity, not semantic approval. Summary review authentication
    belongs to the stage consuming the saved GPT/Opus responses, not this join.
    """
    expected = {h['viewpoint_id']: dict(h) for h in heads}
    provided = {s['viewpoint_id']: dict(s) for s in summaries}
    if len(expected) != len(heads) or len(provided) != len(summaries):
        raise ValueError('duplicate CVP head or summary')
    if set(expected) != set(provided):
        raise ValueError('missing or foreign CVP summary; index must cover all active heads')
    cards = []
    for vid, head in sorted(expected.items()):
        summary = provided[vid]
        if any(summary.get(k) != head[k] for k in ('viewpoint_revision_id', 'head_sha256')):
            raise ValueError('CVP summary is bound to a stale head: ' + vid)
        value = summary.get('summary')
        if not isinstance(value, str) or not value.strip() or len(value) >= 150:
            raise ValueError('CVP summary must contain fewer than 150 Unicode code points')
        cards.append({**head, 'summary': value})
    return seal({'schema_version': INDEX_VERSION, 'cards': cards})


def assert_current_index(index: Mapping[str, Any], heads: Sequence[Mapping[str, Any]]) -> None:
    verify(index, INDEX_VERSION)
    current = {h['viewpoint_id']: dict(h) for h in heads}
    actual = {c['viewpoint_id']: {k: v for k, v in c.items() if k != 'summary'}
              for c in index['cards']}
    if len(current) != len(heads) or len(actual) != len(index['cards']) or actual != current:
        raise ValueError('active CVP set or current head changed; refresh index')


def claim_cards(claims: Sequence[Mapping[str, Any]]) -> tuple[list[dict], dict[str, str]]:
    """Small, complete Claim wording for recall; never include evidence bodies."""
    if not claims or len(claims) > DEFAULT_BATCH_SIZE:
        raise ValueError('recall requires one accepted group of at most 20 Claims')
    ids = [c['claim_id'] for c in claims]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate recall Claim')
    result, refs = [], {}
    for i, row in enumerate(claims, 1):
        if not isinstance(row.get('statement'), str) or not row['statement'].strip():
            raise ValueError('recall Claim lacks its complete statement')
        ref = f'c{i}'
        refs[ref] = row['claim_id']
        # The caller supplies this projection from its version-bound input.
        # Unknown semantic fields must be explicitly added to that projection,
        # not dropped by converting it through the legacy ReviewClaim schema.
        result.append({**{k: v for k, v in row.items() if k != 'claim_id'}, 'claim_ref': ref})
    return result, refs


def recall_plan(index: Mapping[str, Any], claims: Sequence[Mapping[str, Any]], *,
                max_request_bytes: int, measure: Callable[[dict], int] | None = None) -> dict:
    """Plan every shard before invoking any model; measure the actual request."""
    verify(index, INDEX_VERSION)
    if type(max_request_bytes) is not int or max_request_bytes < 1:
        raise ValueError('positive recall request capacity required')
    measure = measure or (lambda p: len(canonical_json(p).encode('utf-8')))
    cards, claim_refs = claim_cards(claims)
    view_refs = {f'v{i}': c['viewpoint_id'] for i, c in enumerate(index['cards'], 1)}
    rows = [{'cvp_ref': ref, **{k: c[k] for k in (
        'core_proposition', 'summary', 'scope', 'proposition_signature')}}
        for ref, c in zip(view_refs, index['cards'], strict=True)]
    def payload(values):
        return {'schema_version': RECALL_VERSION, 'claims': cards, 'viewpoints': values}
    # Actual JSON request size grows with appended complete cards. Search the
    # boundary rather than repeatedly encoding every growing prefix (quadratic
    # in a large Registry). No token estimate replaces the actual encoder.
    shards, offset = [], 0
    while offset < len(rows):
        if measure(payload(rows[offset:offset+1])) > max_request_bytes:
            raise ValueError('one CVP card plus complete Claim group exceeds request capacity')
        count, trial_count, remaining = 1, 2, len(rows)-offset
        while count < remaining:
            trial_count=min(trial_count,remaining)
            if measure(payload(rows[offset:offset+trial_count])) <= max_request_bytes:
                count=trial_count;trial_count*=2
                continue
            low, high=count+1,trial_count-1
            while low<=high:
                middle=(low+high)//2
                if measure(payload(rows[offset:offset+middle])) <= max_request_bytes:
                    count=middle;low=middle+1
                else:
                    high=middle-1
            break
        shards.append(payload(rows[offset:offset+count]));offset+=count
    return seal({'schema_version': RECALL_VERSION, 'index_sha256': index['artifact_sha256'],
                 'claims_sha256': sha256_json(list(claims)), 'claim_refs': claim_refs,
                 'viewpoint_refs': view_refs, 'max_request_bytes': max_request_bytes,
                 'shards': shards})


def merge_recall(plan: Mapping[str, Any], replies: Sequence[Mapping[str, Any]], *,
                 existing_links: Sequence[Mapping[str, Any]] = ()) -> dict:
    """Exact coverage and shard-local references; no ranking or top-K cutoff."""
    verify(plan, RECALL_VERSION)
    if len(replies) != len(plan['shards']):
        raise ValueError('recall has unfinished shards')
    candidates = {cid: set() for cid in plan['claim_refs'].values()}
    for payload, reply in zip(plan['shards'], replies, strict=True):
        if set(reply) != {'claim_candidates'} or not isinstance(reply['claim_candidates'], list):
            raise ValueError('recall response must contain only candidate references')
        allowed = {v['cvp_ref'] for v in payload['viewpoints']}
        seen = set()
        for row in reply['claim_candidates']:
            if not isinstance(row, dict) or set(row) != {'claim_ref', 'cvp_refs'}:
                raise ValueError('recall row fields differ')
            cref, refs = row['claim_ref'], row['cvp_refs']
            if cref not in plan['claim_refs'] or cref in seen:
                raise ValueError('unknown or repeated recall Claim reference')
            if (not isinstance(refs, list) or any(not isinstance(v, str) for v in refs)
                    or len(set(refs)) != len(refs) or not set(refs) <= allowed):
                raise ValueError('unknown, repeated or cross-shard CVP reference')
            seen.add(cref)
            candidates[plan['claim_refs'][cref]].update(plan['viewpoint_refs'][v] for v in refs)
        if seen != set(plan['claim_refs']):
            raise ValueError('recall omitted Claims, including explicit empty results')
    valid_views = set(plan['viewpoint_refs'].values())
    for link in existing_links:
        if link.get('effective_state') != 'active' or link.get('claim_id') not in candidates:
            continue
        if link.get('viewpoint_id') not in valid_views:
            raise ValueError('active existing link has no current active CVP card')
        candidates[link['claim_id']].add(link['viewpoint_id'])
    return seal({'schema_version': RECALL_VERSION, 'plan_sha256': plan['artifact_sha256'],
                 'claim_candidates': [{'claim_id': cid, 'viewpoint_ids': sorted(vids)}
                                      for cid, vids in candidates.items()],
                 'identity_decided': False})


def changed_index_cards(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> dict:
    """Compare complete cards, including reviewed summary and exact current head."""
    verify(current, INDEX_VERSION)
    old = {}
    if previous is not None:
        verify(previous, INDEX_VERSION)
        old = {c['viewpoint_id']: c for c in previous['cards']}
        if len(old) != len(previous['cards']):
            raise ValueError('duplicate previous CVP card')
    new = {c['viewpoint_id']: c for c in current['cards']}
    if len(new) != len(current['cards']):
        raise ValueError('duplicate current CVP card')
    return {'changed': sorted(k for k in new if old.get(k) != new[k]),
            'unchanged': sorted(k for k in new if old.get(k) == new[k]),
            'retired': sorted(set(old) - set(new))}


def combine_recall_candidates(plan, model_candidates, *, existing_links=()):
    """Bind authenticated model selections and active links to the full plan."""
    verify(plan, RECALL_VERSION)
    claims = list(plan['claim_refs'].values())
    views = set(plan['viewpoint_refs'].values())
    if set(model_candidates) != set(claims):
        raise ValueError('incremental recall has incomplete Claim coverage')
    candidates = {cid:set(values) for cid,values in model_candidates.items()}
    if any(not values <= views for values in candidates.values()):
        raise ValueError('incremental recall retains a stale or foreign CVP')
    for link in existing_links:
        if link.get('effective_state') != 'active' or link.get('claim_id') not in candidates:
            continue
        if link.get('viewpoint_id') not in views:
            raise ValueError('active existing link has no current active CVP card')
        candidates[link['claim_id']].add(link['viewpoint_id'])
    return seal({'schema_version':RECALL_VERSION, 'plan_sha256':plan['artifact_sha256'],
                 'claim_candidates':[{'claim_id':cid,'viewpoint_ids':sorted(candidates[cid])} for cid in claims],
                 'identity_decided':False})
