"""Pure capacity planning for complete Claim work units.

A probe constructs the *real* scoped adapter and packet for each available
phase. Measuring serialized material alone misses prompts and dynamic schemas.
No call is dispatched here. A future review packet cannot be measured before
its candidate exists; callers must repeat the check before every dispatch.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from backend.api.canonical_repository.viewpoint_foundation import sha256_json

VERSION = 'wang_cvp_management_capacity_v1'


@dataclass(frozen=True)
class RequestProbe:
    phase: str
    adapter: Any
    packet: Mapping[str, Any]


def measure_request(probe: RequestProbe) -> dict:
    """Measure exactly the adapter's actual structured request, without a call."""
    if probe.phase not in {'proposal', 'correction', 'review'}:
        raise ValueError('unknown CVP capacity phase')
    size = probe.adapter.request_bytes(probe.packet)
    if type(size) is not int or size < 0:
        raise ValueError('adapter returned an invalid request byte count')
    return {'phase': probe.phase, 'request_bytes': size,
            'packet_sha256': sha256_json(probe.packet),
            'generation_config_sha256': probe.adapter.generation_config_sha256}


def dependency_clusters(claim_ids: Sequence[str], *,
                        candidates_by_claim: Mapping[str, Sequence[str]],
                        mutable_dependencies_by_claim: Mapping[str, Sequence[Sequence[str]]]) -> list[dict]:
    """Join complete Claims sharing any mutable (collection, object_id).

    Readonly recall candidates and source documents do not join clusters. The
    caller must include the full proposed mutation closure, including a shared
    head, member link or relation even when it is absent from recall results.
    Candidate lists are opaque, ordered, complete lists: never trim them here.
    """
    ids = list(claim_ids)
    if not ids or any(not isinstance(cid, str) or not cid for cid in ids) or len(set(ids)) != len(ids):
        raise ValueError('capacity requires unique nonempty original Claims')
    if set(candidates_by_claim) != set(ids) or set(mutable_dependencies_by_claim) != set(ids):
        raise ValueError('capacity maps must cover the exact original Claim denominator')
    roots = {cid: cid for cid in ids}
    def find(cid):
        while roots[cid] != cid:
            roots[cid] = roots[roots[cid]]
            cid = roots[cid]
        return cid
    owners = {}
    dependencies = {}
    for cid in ids:
        candidates = candidates_by_claim[cid]
        if isinstance(candidates, (str, bytes)) or any(not isinstance(v, str) or not v for v in candidates):
            raise ValueError('candidate list must contain complete viewpoint IDs')
        if len(set(candidates)) != len(candidates):
            raise ValueError('duplicate candidate viewpoint')
        keys = set()
        for key in mutable_dependencies_by_claim[cid]:
            if isinstance(key, (str, bytes)) or len(key) != 2 or any(not isinstance(v, str) or not v for v in key):
                raise ValueError('mutable dependencies require collection/object_id pairs')
            key = tuple(key)
            keys.add(key)
            if key in owners:
                roots[find(cid)] = find(owners[key])
            else:
                owners[key] = cid
        dependencies[cid] = keys
    grouped = {}
    for cid in ids:
        grouped.setdefault(find(cid), []).append(cid)
    return [{'claim_ids': members,
             'candidates_by_claim': {cid: list(candidates_by_claim[cid]) for cid in members},
             'mutable_dependencies': [list(key) for key in sorted(set().union(*(dependencies[cid] for cid in members)))]}
            for members in grouped.values()]


def plan_work_units(claim_ids: Sequence[str], *, parent_group_key: str,
                    candidates_by_claim: Mapping[str, Sequence[str]],
                    mutable_dependencies_by_claim: Mapping[str, Sequence[Sequence[str]]],
                    max_request_bytes: int,
                    probe: Callable[[tuple[str, ...]], Sequence[RequestProbe]],
                    required_phases: Sequence[str] = ('proposal',)) -> dict:
    """Deterministic first-fit packing with exact, rebuilt packet measurements.

    Whole dependency clusters are the smallest units. Each oversized cluster
    becomes an explicit resource_hold; other clusters can continue. The plan
    preserves every original Claim exactly once. Missing required phase probes
    fail closed, rather than being treated as a zero-size review request.
    """
    if type(max_request_bytes) is not int or max_request_bytes <= 0:
        raise ValueError('positive request byte budget required')
    required = list(required_phases)
    if not required or len(set(required)) != len(required) or not set(required) <= {'proposal', 'correction', 'review'}:
        raise ValueError('explicit unique capacity phases required')
    clusters = dependency_clusters(claim_ids, candidates_by_claim=candidates_by_claim,
                                   mutable_dependencies_by_claim=mutable_dependencies_by_claim)
    original = list(claim_ids)
    order = {cid: i for i, cid in enumerate(original)}
    measured = {}
    def evaluate(ids):
        ids = tuple(sorted(ids, key=order.__getitem__))
        if ids not in measured:
            probes = list(probe(ids))
            if len({p.phase for p in probes}) != len(probes):
                raise ValueError('duplicate phase probe')
            rows = [measure_request(p) for p in probes]
            if not set(required) <= {r['phase'] for r in rows}:
                raise ValueError('actual required phase packet is not available')
            measured[ids] = rows
        return ids, measured[ids]
    def fits(rows):
        return all(r['request_bytes'] <= max_request_bytes for r in rows)
    bins, holds = [], []
    for cluster in clusters:
        ids, rows = evaluate(cluster['claim_ids'])
        if not fits(rows):
            holds.append((ids, rows))
            continue
        for i, (members, _) in enumerate(bins):
            combined, combined_rows = evaluate((*members, *ids))
            if fits(combined_rows):
                bins[i] = (combined, combined_rows)
                break
        else:
            bins.append((ids, rows))
    units = []
    for status, groups in [('ready', bins), ('resource_hold', holds)]:
        for ids, rows in groups:
            unit = {'claim_ids': list(ids), 'status': status, 'measurements': rows,
                    'candidates_by_claim': {cid: list(candidates_by_claim[cid]) for cid in ids},
                    'cluster_indices': [i for i,c in enumerate(clusters) if set(c['claim_ids']) <= set(ids)]}
            unit['group_key'] = parent_group_key + ':capacity:' + sha256_json({
                'parent_group_key': parent_group_key, 'claim_ids': unit['claim_ids']})[:20]
            if status == 'resource_hold':
                unit['reason'] = 'indivisible complete Claim dependency cluster exceeds request budget'
            units.append(unit)
    units.sort(key=lambda u: min(order[cid] for cid in u['claim_ids']))
    result = {'schema_version': VERSION, 'parent_group_key': parent_group_key,
              'original_claim_ids': original, 'max_request_bytes': max_request_bytes,
              'required_phases': required, 'clusters': clusters, 'units': units,
              'review_capacity_pending': 'review' not in required,
              'denominator': {'original_claim_count': len(original),
                  'ready_claim_count': sum(len(u['claim_ids']) for u in units if u['status'] == 'ready'),
                  'held_claim_count': sum(len(u['claim_ids']) for u in units if u['status'] == 'resource_hold')}}
    result['artifact_sha256'] = sha256_json(result)
    return deepcopy(result)


def validate_work_units(plan: Mapping[str, Any], *, claim_ids: Sequence[str],
                        parent_group_key: str,
                        candidates_by_claim: Mapping[str, Sequence[str]],
                        mutable_dependencies_by_claim: Mapping[str, Sequence[Sequence[str]]],
                        max_request_bytes: int) -> None:
    """Validate subdivision coverage before registering children in parent.units.

    This authenticates the manifest, not model approval or current SQL pins.
    The dispatcher must still measure its actual adapter immediately before
    the existing call reservation and enforce the parent's remaining budget.
    """
    content = {k: v for k, v in plan.items() if k != 'artifact_sha256'}
    if plan.get('artifact_sha256') != sha256_json(content) or plan.get('schema_version') != VERSION:
        raise ValueError('invalid capacity manifest binding')
    if (plan['original_claim_ids'] != list(claim_ids) or plan['parent_group_key'] != parent_group_key
            or plan['max_request_bytes'] != max_request_bytes):
        raise ValueError('capacity changed original group or budget')
    clusters = dependency_clusters(claim_ids, candidates_by_claim=candidates_by_claim,
                                   mutable_dependencies_by_claim=mutable_dependencies_by_claim)
    if plan['clusters'] != clusters:
        raise ValueError('capacity changed complete candidates or mutation dependencies')
    seen = []
    keys = set()
    for unit in plan['units']:
        ids = unit['claim_ids']
        if not ids or len(set(ids)) != len(ids) or any(cid not in claim_ids for cid in ids):
            raise ValueError('invalid work unit Claim coverage')
        expected_key = parent_group_key + ':capacity:' + sha256_json({
            'parent_group_key': parent_group_key, 'claim_ids': ids})[:20]
        if unit['group_key'] != expected_key or expected_key in keys:
            raise ValueError('invalid work unit identity')
        keys.add(expected_key)
        if unit['candidates_by_claim'] != {cid: list(candidates_by_claim[cid]) for cid in ids}:
            raise ValueError('capacity dropped a Claim candidate')
        expected = [i for i,c in enumerate(clusters) if set(c['claim_ids']) <= set(ids)]
        if (unit['cluster_indices'] != expected or
                set(ids) != {cid for i in expected for cid in clusters[i]['claim_ids']}):
            raise ValueError('capacity split a mutable dependency cluster')
        rows = unit['measurements']
        phases = [r['phase'] for r in rows]
        if len(set(phases)) != len(phases) or not set(plan['required_phases']) <= set(phases):
            raise ValueError('capacity lacks actual required phase measurements')
        if any(type(r['request_bytes']) is not int or r['request_bytes'] < 0 for r in rows):
            raise ValueError('invalid measured request size')
        fits = all(r['request_bytes'] <= max_request_bytes for r in rows)
        if unit['status'] != ('ready' if fits else 'resource_hold'):
            raise ValueError('capacity status differs from measured requests')
        if not fits and len(expected) != 1:
            raise ValueError('resource hold must describe one indivisible cluster')
        seen.extend(ids)
    if len(seen) != len(claim_ids) or set(seen) != set(claim_ids):
        raise ValueError('capacity changed original Claim denominator')
    counts = {'original_claim_count': len(claim_ids),
              'ready_claim_count': sum(len(u['claim_ids']) for u in plan['units'] if u['status']=='ready'),
              'held_claim_count': sum(len(u['claim_ids']) for u in plan['units'] if u['status']=='resource_hold')}
    if plan['denominator'] != counts or plan['review_capacity_pending'] != ('review' not in plan['required_phases']):
        raise ValueError('capacity changed completion or pending review disclosure')
