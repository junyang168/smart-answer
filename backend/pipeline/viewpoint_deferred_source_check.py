"""Bounded, read-only source investigation before escalating deferred components.

This is not another CVP correction round. It produces a source-repair handoff,
never grants citation authority, changes corpus data, or marks a group complete.
"""
from __future__ import annotations
import argparse
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict
from dotenv import load_dotenv
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_resolution import ReviewClaim, StructuredJsonReviewerAdapter
from backend.pipeline.viewpoint_evidence_source_context import load_context_source, verify_source_context
from backend.pipeline.viewpoint_resolution_runtime import call_model, read_artifact, write_immutable, subscription_client

class Finding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item_id: str
    diagnosis: Literal['existing_evidence_sufficient', 'source_support_without_bound_evidence', 'source_insufficient', 'needs_human_judgment']
    evidence_step_ids: list[str]
    paragraph_key: str
    exact_quote: str
    reason: str

class Findings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    findings: list[Finding]

PROMPT = '''你只核查 CVP 暂缓成分的来源，不生成 CVP，也不修改 Claim。
每个 item 恰好返回一个 finding。独立阅读完整 Claim、该 Claim 自己的证据和原文段落。
existing_evidence_sufficient：现有逐字证据已经支持该成分；可以用同段上下文消解指代，但不能把新的实质命题偷当成旧引文。
source_support_without_bound_evidence：教授在原文讲过，但支持它的文字没有收入本 Claim 的证据；需源证据修复，绝不直接通过 CVP。
source_insufficient：提供的原文也不足以支持该成分。needs_human_judgment：仍不确定。
source_paragraphs 是诊断线索，不是新增引用权威。不能借其他 Claim 的证据。
引用只能逐字来自此 item 的 evidence 或 source_paragraphs。如果有引用，必须给同 item 的段落 key。
如果判 existing_evidence_sufficient，引用须来自列出的本 Claim evidence_step_ids 的原引文。
必须保留“相对”等模态/限定。不判断教授的神学/词义解释是否正确。'''

def build_packet(packet, disposition, store):
    claims = {c['claim_id']: c for c in packet['claims']}
    items = []
    for row in disposition['claims']:
        raw = claims[row['claim_id']]
        deferred = [(i, c) for i, c in enumerate(row['components']) if c['disposition'] == 'deferred']
        if not deferred:
            continue
        claim = ReviewClaim.model_validate(raw)
        current = store.get_record_state('claims', claim.claim_id)
        if not current or current['retired'] or current['payload']['revision'] != claim.pinned_claim_revision:
            raise ValueError('deferred Claim revision drift')
        # Same semantic pin as the production packet, not merely matching text.
        from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha
        if semantic_record_sha(current['payload']) != claim.claim_revision_sha256:
            raise ValueError('deferred Claim SHA drift')
        source = store.get_record_state('source_documents', claim.source_id)
        if not source or source['retired']:
            raise ValueError('source missing or retired')
        loaded = load_context_source(source['payload'])
        paragraphs = {}
        for evidence in claim.evidence:
            step = store.get_record_state('evidence_steps', evidence.evidence_step_id)
            fragment = store.get_record_state('source_fragments', evidence.source_fragment_id)
            if (not step or step['retired'] or not fragment or fragment['retired']
                    or step['payload']['statement'] != evidence.evidence_statement
                    or fragment['payload']['verbatim_excerpt'] != evidence.verbatim_excerpt
                    or fragment['payload']['source_id'] != claim.source_id
                    or fragment['payload']['paragraph_key'] != evidence.paragraph_key):
                raise ValueError('deferred evidence/fragment drift')
            if evidence.source_context:
                verify_source_context(evidence, source['payload'], loaded)
                index = int(evidence.paragraph_key[1:]) - 1
                # Adjacent physical paragraphs are diagnostic only. No corpus
                # search and no foreign Claim/evidence membership is added.
                for j in range(max(0, index - 1), min(len(loaded[2].spoken_rows), index + 2)):
                    key = f'S{j + 1:04d}'
                    text = str(loaded[2].spoken_rows[j].get('text') or '')
                    paragraphs[key] = {'paragraph_key':key,'text':text,'text_sha256':sha256_json(text)}
        for i, component in deferred:
            for span in component['spans']:
                if claim.statement[span['start_char']:span['end_char']] != span['exact_text']:
                    raise ValueError('deferred span drift')
            items.append({'item_id': f'{claim.claim_id}:{i}', 'claim_id':claim.claim_id,
                'claim_revision_sha256':claim.claim_revision_sha256,'statement':claim.statement,
                'component':component,'evidence':[e.model_dump(mode='json') for e in claim.evidence],
                'source_binding':{'path':str(loaded[0]),'file_sha256':loaded[1],'body_sha256':loaded[2].body_sha256},
                'source_paragraphs':[paragraphs[k] for k in sorted(paragraphs)]})
    body={'schema_version':'wang_deferred_source_investigation_v1','items':items,
          'input_packet_sha256':sha256_json(packet),'input_disposition_sha256':sha256_json(disposition),
          'source_context_grants_citation_authority':False,'master_data_mutations':0}
    return body | {'artifact_sha256':sha256_json(body)}

def validate_findings(raw, packet):
    result=Findings.model_validate(raw)
    index={i['item_id']:i for i in packet['items']}
    if len(result.findings)!=len(index) or {f.item_id for f in result.findings}!=set(index):
        raise ValueError('source findings must cover each deferred component exactly once')
    for f in result.findings:
        item=index[f.item_id]
        evidence={e['evidence_step_id']:e for e in item['evidence']}
        if not set(f.evidence_step_ids)<=set(evidence):
            raise ValueError('foreign evidence in source diagnosis')
        paragraphs={p['paragraph_key']:p['text'] for p in item['source_paragraphs']}
        if f.exact_quote and (not f.paragraph_key or f.exact_quote not in paragraphs.get(f.paragraph_key,'')):
            raise ValueError('source diagnosis quote is not exact in its bound paragraph')
        if f.diagnosis=='existing_evidence_sufficient':
            if not f.exact_quote or not f.evidence_step_ids or not any(
                f.exact_quote in evidence[eid]['verbatim_excerpt'] and evidence[eid]['paragraph_key']==f.paragraph_key
                for eid in f.evidence_step_ids):
                raise ValueError('context cannot silently become bound evidence')
        if f.diagnosis=='source_support_without_bound_evidence' and not f.exact_quote:
            raise ValueError('missing source support quotation')
    return {f.item_id:f.model_dump(mode='json') for f in result.findings}

def run(packet_path, disposition_path, output, store, adapters=None):
    packet=build_packet(read_artifact(packet_path), read_artifact(disposition_path), store)
    write_immutable(output/'packet.json',packet)
    if not packet['items']:
        raise ValueError('no deferred components to investigate')
    if adapters is None:
        adapters=[StructuredJsonReviewerAdapter(client=subscription_client(provider,model,'high'),
            prompt=PROMPT,response_model=Findings,schema_name='wang_deferred_source_findings_v1')
            for provider,model in (('codex','gpt-6.1-sol'),('claude','claude-opus-5-5'))]
    reviews=[]
    try:
        for label,adapter in zip(('gpt','opus'),adapters,strict=True):
            adapter.max_request_bytes=200_000
            raw,_,_=call_model(adapter,packet,output/f'raw-{label}.json')
            # Both models see exactly the same source, not each other's answer.
            reviews.append(validate_findings(raw,packet))
        actions=[]
        repair_proposals=[]
        for item in packet['items']:
            left,right=(r[item['item_id']] for r in reviews)
            agreed=left['diagnosis']==right['diagnosis']
            action={'existing_evidence_sufficient':'reviewed_cvp_repair_required',
                    'source_support_without_bound_evidence':'claim_evidence_repair_authorization_required',
                    'source_insufficient':'claim_source_review_required',
                    'needs_human_judgment':'human_judgment_required'}.get(left['diagnosis']) if agreed else 'human_judgment_required'
            actions.append({'item_id':item['item_id'],'models_agree':agreed,'next_action':action,'gpt':left,'opus':right})
            if action=='claim_evidence_repair_authorization_required':
                paragraphs={p['paragraph_key']:p for p in item['source_paragraphs']}
                quotations=[]
                for finding in (left,right):
                    paragraph=paragraphs[finding['paragraph_key']]
                    start=paragraph['text'].index(finding['exact_quote'])
                    quotation={'paragraph_key':finding['paragraph_key'],
                        'paragraph_text_sha256':paragraph['text_sha256'],
                        'start_char':start,'end_char':start+len(finding['exact_quote']),
                        'exact_quote':finding['exact_quote']}
                    if quotation not in quotations:
                        quotations.append(quotation)
                repair_proposals.append({'claim_id':item['claim_id'],
                    'claim_revision_sha256':item['claim_revision_sha256'],
                    'original_statement':item['statement'],'source_binding':item['source_binding'],
                    'candidate_source_quotations':quotations,
                    'review_constraints':[left['reason'],right['reason']],
                    'status':'proposal_only_requires_authorized_corpus_repair',
                    'claim_or_evidence_writes_executed':False})
        report={'schema_version':'wang_deferred_source_handoff_v1','status':'investigated_not_resolved',
                'packet_sha256':packet['artifact_sha256'],'actions':actions,'group_complete':False,
                'master_data_mutations':0,'maximum_model_calls':2,
                'repair_proposals':repair_proposals}
        report['artifact_sha256']=sha256_json(report)
        write_immutable(output/'handoff.json',report)
        return report
    except Exception as exc:
        # Keep the source packet and raw returned answers; never skip the item
        # or turn validation failure into an additional unbounded model call.
        client = getattr(adapter, '_client', None)
        transport = getattr(client, 'last_raw_response', None)
        if transport is not None:
            write_immutable(output/f'failed-transport-{label}.json',
                {'model_id':adapter.model_id,'raw_response':transport,'error':str(exc)})
        write_immutable(output/'failure.json',{'status':'failed','error':str(exc),'packet_sha256':packet['artifact_sha256']})
        raise

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('packet','disposition','output-root'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2]/'.env')
    report=run(args.packet,args.disposition,args.output_root,PostgresKnowledgeStore())
    print(__import__('json').dumps(report,ensure_ascii=False))

if __name__=='__main__': main()
