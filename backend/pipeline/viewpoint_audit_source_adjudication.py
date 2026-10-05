"""Read-only, bounded original-source adjudication of independent audit disputes.

The independent report is immutable. A finding is a repair/escalation proposal,
not permission to write corpus data or to complete the group.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.pipeline.viewpoint_evidence_source_context import load_context_source
from backend.pipeline.viewpoint_resolution_runtime import call_model, read_artifact, write_immutable, subscription_client


class Finding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item_id: str
    verdict: Literal['audit_objection_upheld', 'audit_objection_rejected', 'unresolved']
    source_id: str
    paragraph_key: str
    exact_quote: str
    reason: str


class Findings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    findings: list[Finding]


PROMPT = '''只读原文仲裁：逐项判断独立审计异议是否成立，不生成修改，不评判教授神学对错。
每个 item 返回一次。阅读完整 Claim、实际 CVP、现行连接及其自己绑定的原文。
component 必须在完整 Claim 和自己原文中理解，不要求截取片段脱离上下文独立成句。
允许原文消解「王」「僕人」等指代；不允许借其他 Claim 的证据扩展实质命题。
仍须检查真值条件、限定、等价与支持的区别；不可为了通过而自动改为 supports。
分别判断 audit_objection_upheld / audit_objection_rejected / unresolved。
前两者必须引用本 item 原文逐字引文及段落 key，并明确回应原异议。
context 只是诊断材料，不授予新增引用权威。结果只是提案，不是审计通过或写库批准。'''


def disputes(audit):
    from backend.pipeline.viewpoint_g035_temporary_audit import validate as validate_exception
    validate_exception(audit)
    result = []
    for layer, key in (('3', 'claim_id'), ('4', 'viewpoint_id')):
        report = audit.get('layers', {}).get(layer, {})
        rows = report.get('results', [])
        found = [r for r in rows if r.get('verdict') == 'disputed']
        if found and (not report.get('complete') or report.get('judged') != len(rows)
                      or report.get('disputed') != len(found)
                      or any(report.get(k, 0) for k in ('model_errors', 'blocked', 'no_source_text'))):
            raise ValueError('incomplete audit cannot enter semantic adjudication')
        for row in found:
            result.append((layer, key, row))
    return result


def validate_population(audit, claim_ids, viewpoint_ids):
    from backend.pipeline.viewpoint_g035_temporary_audit import validate as validate_exception
    validate_exception(audit)
    for layer, key, ids in (('3', 'claim_id', claim_ids), ('4', 'viewpoint_id', viewpoint_ids)):
        report = audit['layers'][layer]
        rows = report['results']
        if (not report.get('complete') or report.get('judged') != len(ids) or
                len(rows) != len(ids) or {r[key] for r in rows} != set(ids) or
                any(report.get(k, 0) for k in ('model_errors', 'blocked', 'no_source_text'))):
            raise ValueError('incomplete audit population cannot enter adjudication')


def validate_own_source_identity(source, own_sources, store):
    sid = source['source_id']
    if sid in own_sources:
        return
    # Legacy sermon occurrences use the registered transcript key, not the
    # SourceDocument object ID. Titles/paths never establish source authority.
    alias = source.get('transcript_id')
    matches = []
    for candidate in store.list_records('source_documents'):
        if not alias or candidate.get('transcript_id') != alias:
            continue
        state = store.get_record_state('source_documents', candidate['source_id'])
        if state and not state['retired']:
            matches.append(state['payload']['source_id'])
    if (source.get('source_type') != 'sermon_transcript' or
            alias not in own_sources or matches != [sid]):
        raise ValueError('foreign source')


def build_packet(audit_path, store):
    audit = read_artifact(audit_path)
    links = store.list_records('viewpoint_claim_links')

    def active(collection, identity):
        state = store.get_record_state(collection, identity)
        if not state or state['retired']:
            raise ValueError(f'missing active {collection}: {identity}')
        return state['payload']

    items = []
    for layer, key, row in disputes(audit):
        viewpoint = active('canonical_viewpoints', row[key]) if layer == '4' else None
        if viewpoint and viewpoint['current_revision_id'] != row['revision_id']:
            raise ValueError('audited viewpoint revision drift')
        ids = row.get('claim_ids_in_question') if layer == '4' else [row['claim_id']]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError('audit must identify exact disputed Claims')
        for cid in ids:
            claim = active('claims', cid)
            own_links = [l for l in links if l['claim_id'] == cid and
                         l.get('effective_state') == 'active' and viewpoint and
                         l['viewpoint_id'] == row['viewpoint_id']]
            if viewpoint and not own_links:
                raise ValueError('disputed Claim has no active target connection')
            sources, evidence = {}, []
            bindings = [b for l in own_links for b in l['evidence_bindings']]
            if layer == '3':
                bindings = []
                for eid in claim['evidence_step_ids']:
                    step = active('evidence_steps', eid)
                    fids = set(step.get('source_fragment_ids', []))
                    if step.get('source_fragment_id'):
                        fids.add(step['source_fragment_id'])
                    bindings.extend({'evidence_step_id': eid, 'source_fragment_id': fid}
                                    for fid in sorted(fids))
            if not bindings:
                raise ValueError('no own evidence bindings')
            for link in own_links:
                if (link['pinned_claim_revision'] != claim['revision'] or
                        link['validated_against_viewpoint_revision_id'] != row['revision_id']):
                    raise ValueError('connection revision drift')
                for span in (link.get('component_locator') or {}).get('canonical_spans', []):
                    if claim['statement'][span['start_char']:span['end_char']] != span['exact_text']:
                        raise ValueError('component span drift')
            own_sources = {o['source_id'] for o in claim['occurrences']}
            for b in bindings:
                if b['evidence_step_id'] not in claim['evidence_step_ids']:
                    raise ValueError('foreign evidence')
                step = active('evidence_steps', b['evidence_step_id'])
                fids = set(step.get('source_fragment_ids', [])) | {step.get('source_fragment_id')}
                if b['source_fragment_id'] not in fids:
                    raise ValueError('fragment not bound to own EvidenceStep')
                fragment = active('source_fragments', b['source_fragment_id'])
                sid = fragment['source_id']
                source = active('source_documents', sid)
                validate_own_source_identity(source, own_sources, store)
                if sid not in sources:
                    path, file_sha, projection = load_context_source(source)
                    sources[sid] = {'source_id': sid, 'path': str(path), 'file_sha256': file_sha,
                                    'body_sha256': projection.body_sha256,
                                    'paragraphs': [{'paragraph_key': f'S{i+1:04d}', 'text': str(p.get('text') or '')}
                                                   for i, p in enumerate(projection.spoken_rows)]}
                paragraphs = {p['paragraph_key']: p['text'] for p in sources[sid]['paragraphs']}
                if not fragment['verbatim_excerpt'] or fragment['verbatim_excerpt'] not in paragraphs.get(fragment['paragraph_key'], ''):
                    raise ValueError('physical evidence quote drift')
                evidence.append({'step': step, 'fragment': fragment})
            items.append({'item_id': f'{layer}:{row[key]}:{cid}', 'audit_finding': row,
                          'claim': claim, 'viewpoint': viewpoint, 'connections': own_links,
                          'evidence': evidence, 'sources': list(sources.values())})
    if len({i['item_id'] for i in items}) != len(items):
        raise ValueError('duplicate audit adjudication item')
    body = {'schema_version': 'wang_audit_source_adjudication_packet_v1', 'items': items,
            'audit_binding': {'path': str(audit_path.resolve()),
                              'file_sha256': hashlib.sha256(audit_path.read_bytes()).hexdigest()},
            'master_data_mutations': 0, 'context_grants_citation_authority': False}
    return body | {'artifact_sha256': sha256_json(body)}


def validate_findings(raw, packet):
    findings = Findings.model_validate(raw).findings
    index = {i['item_id']: i for i in packet['items']}
    if len(findings) != len(index) or {f.item_id for f in findings} != set(index):
        raise ValueError('adjudication must cover disputed items exactly once')
    for f in findings:
        if not f.reason.strip():
            raise ValueError('missing adjudication reason')
        paragraphs = {(s['source_id'], p['paragraph_key']): p['text']
                      for s in index[f.item_id]['sources'] for p in s['paragraphs']}
        if f.verdict != 'unresolved' and not f.exact_quote:
            raise ValueError('decided finding requires original quotation')
        if f.exact_quote and f.exact_quote not in paragraphs.get((f.source_id, f.paragraph_key), ''):
            raise ValueError('adjudication quotation is not exact in own original')
    return {f.item_id: f.model_dump(mode='json') for f in findings}


def source_review_adapter(client, cache):
    """Compact new requests; retain authenticated legacy cache format only.

    Selecting a serializer is not cache approval: call_model still verifies
    every model, backend, prompt, request, response and artifact SHA pin.
    Unknown cached configurations fail there, never fall back to a new call.
    """
    legacy = StructuredJsonReviewerAdapter(client=client, prompt=PROMPT,
        response_model=Findings, schema_name='wang_audit_source_findings_v1')
    compact = legacy.compact_copy()
    if cache.exists() and read_artifact(cache).get('generation_config_sha256') == legacy.generation_config_sha256:
        return legacy
    return compact


def run(audit_path, output, store, adapters=None, *, final_gpt=False):
    packet = build_packet(audit_path, store)
    write_immutable(output / 'packet.json', packet)
    if not packet['items']:
        raise ValueError('no semantic audit disputes')
    providers = [('codex', 'gpt-6.1-sol')]
    if not final_gpt:
        providers.append(('claude', 'claude-opus-5-5'))
    if adapters is None:
        adapters = [source_review_adapter(subscription_client(provider, model, 'high'),
                    output / f'raw-{label}.json')
                    for label, (provider, model) in zip(('gpt', 'opus'), providers)]
    try:
        reviews = []
        labels = ('gpt',) if final_gpt else ('gpt', 'opus')
        for label, adapter in zip(labels, adapters, strict=True):
            adapter.max_request_bytes = 500_000
            raw, _, _ = call_model(adapter, packet, output / f'raw-{label}.json')
            reviews.append(validate_findings(raw, packet))
        if build_packet(audit_path, store) != packet:
            raise ValueError('source or Registry changed during adjudication')
        actions = []
        for item in packet['items']:
            left = reviews[0][item['item_id']]
            right = reviews[-1][item['item_id']]
            verdict = left['verdict'] if left['verdict'] == right['verdict'] else 'unresolved'
            action = {'audit_objection_upheld': 'authorized_content_repair_required',
                      'audit_objection_rejected': 'independent_resolution_required',
                      'unresolved': 'owner_judgment_required'}[verdict]
            row = {'item_id': item['item_id'], 'verdict': verdict,
                   'next_action': action, 'gpt': left}
            if not final_gpt:
                row['opus'] = right
            actions.append(row)
        report = {'schema_version': 'wang_audit_source_adjudication_handoff_v1',
                  'packet_sha256': packet['artifact_sha256'], 'actions': actions,
                  'group_complete': False, 'master_data_mutations': 0,
                  'maximum_model_calls': 1 if final_gpt else 2}
        report['artifact_sha256'] = sha256_json(report)
        write_immutable(output / ('gpt-final-handoff.json' if final_gpt else 'handoff.json'), report)
        return report
    except Exception as exc:
        transport = getattr(getattr(adapter, '_client', None), 'last_raw_response', None)
        if transport is not None:
            write_immutable(output / f'failed-transport-{label}.json', {'raw_response': transport, 'error': str(exc)})
        write_immutable(output / 'failure.json', {'error': str(exc), 'packet_sha256': packet['artifact_sha256']})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--final-gpt', action='store_true')
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2] / '.env')
    run(args.audit, args.output_root, PostgresKnowledgeStore(), final_gpt=args.final_gpt)


if __name__ == '__main__':
    main()
