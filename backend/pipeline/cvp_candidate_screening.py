"""Opus selection of recalled CVP detail when an actual model request is too large.

The original recall remains immutable. This stage only narrows comparison
material; it neither decides identity nor changes knowledge records.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Literal

from pydantic import Field

from backend.api.canonical_repository import cvp_management as domain
from backend.api.canonical_repository.cvp_recall import combine_recall_candidates, seal
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.pipeline.cvp_management_models import adapter as management_adapter
from backend.pipeline.cvp_materials import prepare_materials
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable


PROMPT = """你负责在 CVP 模型请求超限时筛选已召回的旧 CVP 细节。完整阅读这一条 Claim 的判断、证据及相关原文小节，以及逐个候选 CVP 的命题、summary、有效成员、证据及相关原文小节。只保留与该 Claim 的具体判断最相关、可能属于同一观点或具有承重身份边界的旧 CVP。相同人物、经文、词语、讲道或一般主题不足以保留；仅作为背景或后续推论的候选也要排除。比较谓词、对象、范围、条件及论证作用，不按标题词重合判断。没有固定保留数，零个也可以。无法确定是否应排除时保留并简述疑点。已有有效连接必须保留。对输入每个 CVP ID 恰好返回一次 retain 或 exclude 及简短理由；不抄原文，不生成、连接或批准 CVP，不判断神学真伪。"""


class Decision(domain.Contract):
    cvp_id: str
    decision: Literal['retain', 'exclude']
    reason: str = Field(min_length=1)


class Selection(domain.Contract):
    decisions: list[Decision]


def _payload(bundle, claim_id, viewpoint_ids):
    records = {(r['collection'], r['object_id']): r['payload'] for r in bundle['records']}
    refs = bundle['references']
    packets_by_ref = {s['source_ref']: s for s in bundle['material']['sources']}
    if not set(refs['source_documents'].values()) <= set(packets_by_ref):
        raise ValueError('detail screening lacks a relevant original section')
    source_packets = {sid: packets_by_ref[ref] for sid, ref in refs['source_documents'].items()}
    sections = {}

    def support(cid):
        claim = records['claims', cid]
        evidence = []
        for eid in claim.get('evidence_step_ids', []):
            step = records.get(('evidence_steps', eid))
            if step is None:
                raise ValueError('detail screening lacks a member evidence step')
            fragments = []
            fids = step.get('source_fragment_ids') or ([step['source_fragment_id']] if step.get('source_fragment_id') else [])
            for fid in fids:
                fragment = records.get(('source_fragments', fid))
                if fragment is None:
                    raise ValueError('detail screening lacks a source fragment')
                sid, key = fragment['source_id'], fragment.get('paragraph_key')
                fragments.append({'source_id': sid, 'paragraph_key': key,
                                  'verbatim_excerpt': fragment.get('verbatim_excerpt')})
                matching = []
                for section in source_packets.get(sid, {}).get('sections', []):
                    if any(p['paragraph_key'] == key for p in section['paragraphs']):
                        matching.append(section)
                        sections[sid + ':' + section['section_ref']] = {
                            'source_id': sid, 'heading': section.get('title'),
                            'paragraphs': [{'paragraph_key': p['paragraph_key'], 'text': p['row'].get('text', '')}
                                           for p in section['paragraphs']]}
                if not matching:
                    raise ValueError('detail screening lacks the relevant original section')
            evidence.append({'statement': step.get('statement'),
                             'discourse_role': step.get('discourse_role'), 'excerpts': fragments})
        fields = ('statement', 'conditions', 'limitations', 'qualifications', 'modality',
                  'polarity', 'population_scope', 'scripture_refs', 'claim_type', 'attribution')
        return {'claim_id': cid, **{field: claim.get(field) for field in fields}, 'evidence': evidence}

    target = support(claim_id)
    views = {v['viewpoint']['viewpoint_id']: v for v in bundle['material']['viewpoints']}
    links = bundle['material']['links']
    claim_ids = {ref: cid for cid, ref in refs['claims'].items()}
    candidates = []
    for vid in viewpoint_ids:
        view = views[vid]
        member_ids = sorted({claim_ids[link['claim_ref']] for link in links
                             if link['cvp_ref'] == view['cvp_ref'] and
                             link['content'].get('effective_state') == 'active'})
        candidates.append({'cvp_id': vid, 'statement': view['head']['core_proposition'],
                           'summary': (view.get('contextual_summary') or {}).get('text')
                                      if view.get('summary_status') == 'current' else None,
                           'summary_status': view.get('summary_status'),
                           'scope': view['head'].get('scope'),
                           'members': [support(cid) for cid in member_ids]})
    return {'schema_version': 'wang_cvp_detail_screening_v1', 'claim': target,
            'candidates': candidates, 'source_sections': list(sections.values())}


def with_screening(bundle, original_recall, decisions):
    if not decisions:
        return bundle
    value = deepcopy(bundle)
    prior = value['material'].get('screening')
    if prior and prior['original_recall_sha256'] != original_recall['artifact_sha256']:
        value['material']['initial_screening'] = prior
    value['material']['screening'] = {
        'original_recall_sha256': original_recall['artifact_sha256'],
        'excluded': [{'claim_id': cid, 'cvp_id': row['cvp_id'], 'reason': row['reason'],
                      'decision_artifact_sha256': proof['artifact_sha256']}
                     for cid, proof in decisions.items() for row in proof['decisions']
                     if row['decision'] == 'exclude']}
    return seal({k: v for k, v in value.items() if k != 'artifact_sha256'})


def screen_to_fit(store, preflight, group_key, recall, request, *, proposer, reviewer,
                  screening_root: Path, call):
    """Narrow initial cards only after the actual first proposal exceeds budget."""
    original = {row['claim_id']: set(row['viewpoint_ids']) for row in recall['claim_candidates']}
    selected = deepcopy(original)
    plan = next(p['plan'] for p in preflight['recall_plans'] if p['group_key'] == group_key)
    existing = preflight['existing_links']
    decisions = {}
    screening_root = Path(screening_root)

    def prepared(*, all_support=False):
        narrowed = combine_recall_candidates(plan, selected, existing_links=existing)
        vids = {vid for row in narrowed['claim_candidates'] for vid in row['viewpoint_ids']}
        links = store.read_cvp_links(viewpoint_ids=sorted(vids))
        members = {row['payload']['claim_id'] for row in links.values()
                   if row['payload'].get('effective_state') == 'active'}
        material = prepare_materials(store, preflight, group_key, narrowed,
            member_support_ids=members if all_support else (),
            maintenance_viewpoint_ids=request.maintenance_viewpoint_ids,
            maintenance_record_keys=[(p.collection, p.object_id) for p in request.permissions],
            summary_root=request.input.get('contextual_summary_root'))
        return narrowed, with_screening(material, recall, decisions)

    narrowed, material = prepared()
    def size():
        packet = domain.proposal_packet(request, material)
        return management_adapter('proposal', request, material, packet, client=proposer).request_bytes(packet)
    measured = size()
    for cid in sorted(original, key=lambda c: (-len(original[c]), c)):
        if measured <= request.budget.max_request_bytes:
            break
        if len(original[cid]) < 2:
            continue
        # Screening itself sees full member evidence even though the first
        # proposal is measured against its ordinary compact material.
        _, detailed = prepared(all_support=True)
        payload = _payload(detailed, cid, sorted(original[cid]))
        model = StructuredJsonReviewerAdapter(client=reviewer, prompt=PROMPT,
            response_model=Selection, schema_name='wang_cvp_detail_screening_v1', compact_payload=True)
        model.max_request_bytes = request.budget.max_request_bytes
        path = screening_root / (sha256_json(cid)[:16] + '.json')
        raw, _, _ = call(model, payload, path)
        answer = Selection.model_validate(raw)
        if (len(answer.decisions) != len(original[cid]) or
                {d.cvp_id for d in answer.decisions} != original[cid]):
            raise ValueError('CVP detail screening omitted, repeated or invented a recalled ID')
        if any(not d.reason.strip() for d in answer.decisions):
            raise ValueError('CVP detail screening lacks a concrete reason')
        active = {l['viewpoint_id'] for l in existing
                  if l.get('claim_id') == cid and l.get('effective_state') == 'active'}
        if any(d.decision == 'exclude' and d.cvp_id in active for d in answer.decisions):
            raise ValueError('CVP detail screening excluded an existing active connection')
        selected[cid] = {d.cvp_id for d in answer.decisions if d.decision == 'retain'}
        proof = seal({'schema_version': 'wang_cvp_detail_screening_decision_v1',
            'original_recall_sha256': recall['artifact_sha256'], 'claim_id': cid,
            'material_sha256': detailed['artifact_sha256'],
            'response_artifact_sha256': read_artifact(path)['artifact_sha256'],
            'decisions': [d.model_dump(mode='json') for d in answer.decisions]})
        write_immutable(screening_root / (sha256_json(cid)[:16] + '-decision.json'), proof)
        decisions[cid] = proof
        narrowed, material = prepared()
        measured = size()
    if measured > request.budget.max_request_bytes:
        raise ValueError(f'complete screened material exceeds request budget: {measured} bytes')
    return narrowed, material, measured


def screen_expansion_to_fit(store, preflight, group_key, recall, request, *, reviewer,
                            screening_root: Path, call, measure, applicable_claim_ids):
    """Screen detail only after a preliminary action makes expansion too large.

    ``measure`` constructs and measures the actual next proposal and review
    packets. It keeps the original recall/cards in the bound material; the
    narrowed recall selects only which read-only member detail to load.
    """
    original = {row['claim_id']: set(row['viewpoint_ids']) for row in recall['claim_candidates']}
    selected = deepcopy(original)
    plan = next(p['plan'] for p in preflight['recall_plans'] if p['group_key'] == group_key)
    existing = preflight['existing_links']
    decisions = {}
    all_ids = {vid for ids in original.values() for vid in ids}
    links = store.read_cvp_links(viewpoint_ids=sorted(all_ids))
    members = {row['payload']['claim_id'] for row in links.values()
               if row['payload'].get('effective_state') == 'active'}
    detailed = prepare_materials(store, preflight, group_key, recall,
        member_support_ids=members,
        maintenance_viewpoint_ids=request.maintenance_viewpoint_ids,
        maintenance_record_keys=[(p.collection, p.object_id) for p in request.permissions],
        summary_root=request.input.get('contextual_summary_root'))
    screening_root = Path(screening_root)

    def narrowed():
        return combine_recall_candidates(plan, selected, existing_links=existing)

    measured = measure(narrowed(), decisions)
    for cid in sorted(applicable_claim_ids, key=lambda c: (-len(original[c]), c)):
        if measured <= request.budget.max_request_bytes:
            break
        if len(original[cid]) < 2:
            continue
        payload = _payload(detailed, cid, sorted(original[cid]))
        model = StructuredJsonReviewerAdapter(client=reviewer, prompt=PROMPT,
            response_model=Selection, schema_name='wang_cvp_detail_screening_v1', compact_payload=True)
        model.max_request_bytes = request.budget.max_request_bytes
        path = screening_root / (sha256_json(cid)[:16] + '.json')
        raw, _, _ = call(model, payload, path)
        answer = Selection.model_validate(raw)
        if (len(answer.decisions) != len(original[cid]) or
                {d.cvp_id for d in answer.decisions} != original[cid]):
            raise ValueError('CVP detail screening omitted, repeated or invented a recalled ID')
        if any(not d.reason.strip() for d in answer.decisions):
            raise ValueError('CVP detail screening lacks a concrete reason')
        active = {l['viewpoint_id'] for l in existing
                  if l.get('claim_id') == cid and l.get('effective_state') == 'active'}
        if any(d.decision == 'exclude' and d.cvp_id in active for d in answer.decisions):
            raise ValueError('CVP detail screening excluded an existing active connection')
        selected[cid] = {d.cvp_id for d in answer.decisions if d.decision == 'retain'}
        proof = seal({'schema_version': 'wang_cvp_detail_screening_decision_v1',
            'original_recall_sha256': recall['artifact_sha256'], 'claim_id': cid,
            'material_sha256': detailed['artifact_sha256'],
            'response_artifact_sha256': read_artifact(path)['artifact_sha256'],
            'decisions': [d.model_dump(mode='json') for d in answer.decisions]})
        write_immutable(screening_root / (sha256_json(cid)[:16] + '-decision.json'), proof)
        decisions[cid] = proof
        measured = measure(narrowed(), decisions)
    if measured > request.budget.max_request_bytes:
        raise ValueError(f'complete screened material exceeds request budget: {measured} bytes')
    return narrowed(), decisions, measured


def effective_recall(recall, bundle, preflight, group_key):
    """Keep the full durable recall; omit screened IDs only for detail loading."""
    screening = bundle['material'].get('screening') or {}
    excluded = screening.get('excluded', [])
    if not excluded:
        return recall
    if screening['original_recall_sha256'] != recall['artifact_sha256']:
        # Initial-card screening already produced this narrowed recall. Its
        # exclusions remain proved on the bundle but need no second removal.
        if bundle.get('recall_sha256') == recall['artifact_sha256']:
            return recall
        raise ValueError('CVP detail screening belongs to different recall')
    selected = {row['claim_id']: set(row['viewpoint_ids']) for row in recall['claim_candidates']}
    for row in excluded:
        if row['cvp_id'] not in selected[row['claim_id']]:
            raise ValueError('CVP detail screening excludes a foreign recall ID')
        selected[row['claim_id']].remove(row['cvp_id'])
    plan = next(p['plan'] for p in preflight['recall_plans'] if p['group_key'] == group_key)
    return combine_recall_candidates(plan, selected, existing_links=preflight['existing_links'])
