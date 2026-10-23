"""One owner-corrected transcript: exact materialization and fresh dual review.

This is deliberately not an extraction rerun, a historical approval override,
or a general transcript editor. No apply/resume entry point is exposed here:
reviewed material must also pass the source-version consumer and store gates.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import sha256_json, semantic_record_sha
from backend.api.canonical_repository.viewpoint_production_safety import file_sha256
from backend.pipeline.source_projection import project_script
from backend.pipeline.viewpoint_authorized_source_repair_357 import Review, snapshot, fingerprint, review_call
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable, subscription_client, stable_decided_at

BASE = Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357')
IMPACT = BASE / 'g041-owner-transcript-correction-impact-20261005-v1'
VERSION = 'wang_g041_owner_transcript_reconciliation_v1'
SOURCE = 'SRC-2017_NYSC_2-5be3c2739375'
REPORT_SHA = 'a2c05af841d6ad4d76092a3a48da605915ffceb26119e3aa57247dee91484c5f'
PROPOSAL_SHA = 'bc3f9aaccdecbd6e77fdc24799d90d7bf209e19855057f6a7a5740461bbfba0e'
LINK = 'VCL-c9213899c86aa0654a06'
FIRST_ROOT = BASE / 'g041-owner-transcript-reconciliation-20261005-v1'
RUNTIME_RECEIPT = BASE / 'g040-sealed-withdrawn-lineage-repair-20261005-v1/continuation/continuation-receipt.json'
PROJECT = Path(__file__).resolve().parents[2]
PROMPT = '''独立审核负责人重新听录音后亲自更正的一份逐字稿所产生的精确来源版本迁移。负责人更正不是AI批准；旧异议、旧审核和旧版本必须保留。
完整新旧自身原文都在包中。更正涉及听众1、听众2、教授答的归属，以及她/他的代词、离婚书用词和被动语态引号。只判断来源忠实，不评价教授神学对错，不用其他讲道替本篇补证。
逐项检查5处新引文、2处Evidence的speaker/statement、2条Claim的引文定位，以及CL004既有qualifies连接在新Claim版本上的精确重新绑定。不得改Claim statement、CVP正文、连接类型/端点/成分边界或其他证据；旧两模型批准不等于新版本批准。
changed_paragraph_claims的7条Claim必须每条恰好一个verdict。各Claim的全部当前Evidence、相关连接及目标CVP完整后像都要检查；归属矛盾、范围扩大、必要充分混淆或任何额外修改需求均拒绝。尤其CL005句子“他自己没有犯奸淫…”在新原文是教授回答，不是听众的待确认问题，但不得因此忽略整句的上下文限定。
机械证明绑定其余六段完整文字与坐标未变、596片段全部精确定位、其他记录不变；不将旧metadata的reviewed_by解释成新审核，不解除任何human hold，不把范围外Claim加入973成员。
只有给定精确后像全部忠于新原文及当前具体观点时approved=true且problems=[]。任何一个拒绝都停止整包，不部分写入，不循环审核直到通过。'''


def text_sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def script(value):
    return value if isinstance(value, list) else value['script']


def frozen_inputs():
    for name, expected in [('impact-report.json', REPORT_SHA), ('reconciliation-scope-proposal.json', PROPOSAL_SHA)]:
        if file_sha256(IMPACT / name) != expected:
            raise ValueError('owner impact/proposal physical drift')
    return read_artifact(IMPACT / 'impact-report.json'), read_artifact(IMPACT / 'reconciliation-scope-proposal.json')


def verify_source(report):
    versions = report['source_versions']
    pub, rev = Path(versions['published_path']), Path(versions['review_path'])
    if file_sha256(pub) != versions['new_file_sha256'] or file_sha256(rev) != versions['new_review_file_sha256']:
        raise ValueError('corrected source physical drift')
    projection = project_script(script(read_artifact(pub)))
    reviewed = project_script(script(read_artifact(rev)))
    texts = [r['text'] for r in projection.spoken_rows]
    if texts != [r['text'] for r in reviewed.spoken_rows] or len(texts) != 7:
        raise ValueError('published/review source denominator or text drift')
    if projection.body_sha256 != versions['new_body_sha256'] or projection.spoken_text_sha256 != versions['new_spoken_text_sha256']:
        raise ValueError('corrected source projection drift')
    if texts[0] != report['new_S0001']:
        raise ValueError('corrected dialogue differs from owner frozen version')
    scope = report['frozen_scope']
    manifest_path = Path(scope['manifest_path'])
    if file_sha256(manifest_path) != scope['manifest_file_sha256']:
        raise ValueError('accepted membership physical drift')
    manifest = read_artifact(manifest_path)
    if manifest['artifact_sha256'] != scope['manifest_artifact_sha256'] or len(manifest['claim_packets']) != 973 or len(manifest['groups']) != 156:
        raise ValueError('accepted membership denominator drift')
    matches = [s for s in manifest['sources'] if s['source_id'] == SOURCE]
    if len(matches) != 1:
        raise ValueError('source membership not unique')
    original = matches[0]['paragraphs']
    # Frozen grouping retains complete old spoken paragraphs, not old physical
    # JSON bytes. Never describe this as verification of an unavailable file.
    old_texts = [p['text'] for p in original]
    if len(old_texts) != 7 or old_texts[0] != report['old_S0001']:
        raise ValueError('historical complete spoken source drift')
    for n, (old, new, pin) in enumerate(zip(old_texts, texts, versions['paragraphs']), 1):
        if pin['paragraph_key'] != f'S{n:04}' or text_sha(old) != pin['old_text_sha256'] or text_sha(new) != pin['new_text_sha256'] or (n != 1 and old != new):
            raise ValueError('unapproved source paragraph change')
    return projection, old_texts


def materialize(states, report, proposal, root):
    projection, original = verify_source(report)
    versions = report['source_versions']
    before_source = states['source_documents', SOURCE]
    if before_source != versions['registry_source'] or sha256_json(before_source) != versions['registry_source_payload_sha256']:
        raise ValueError('source Registry predecessor drift')
    fragments = {o:p for (c,o),p in states.items() if c == 'source_fragments' and p.get('source_id') == SOURCE}
    inventory = report['all_source_fragment_inventory']
    if len(fragments) != 596 or len(inventory) != 596 or set(fragments) != {r[0] for r in inventory}:
        raise ValueError('full fragment denominator drift')
    for oid, revision, digest, _ in inventory:
        if fragments[oid]['revision'] != revision or sha256_json(fragments[oid]) != digest:
            raise ValueError('fragment Registry predecessor drift')
    for kind, collection in [('changed_paragraph_claims','claims'), ('changed_paragraph_evidence','evidence_steps'), ('active_links','viewpoint_claim_links')]:
        for row in report[kind]:
            if states[collection, row['object_id']] != row['payload'] or sha256_json(row['payload']) != row['payload_sha256']:
                raise ValueError('semantic dependency predecessor drift')
    claim_ids = {r['object_id'] for r in report['changed_paragraph_claims']}
    actual_links = {o for (c,o),p in states.items() if c == 'viewpoint_claim_links' and p.get('claim_id') in claim_ids and p.get('effective_state') == 'active'}
    if actual_links != {r['object_id'] for r in report['active_links']}:
        raise ValueError('active link denominator drift')
    # Reject new pinned downstream consumers instead of silently invalidating them.
    forbidden = {'argument_routes','argument_route_revisions','argument_route_attestations','claim_concept_units','viewpoint_resolution_ledgers','product_dependencies'}
    changed_claims = {r['claim_id'] for r in proposal['proposed_claim_anchor_metadata_changes']}
    if any(any(cid in json.dumps(p) for cid in changed_claims) for (c,_),p in states.items() if c in forbidden):
        raise ValueError('unplanned pinned Claim consumer')
    source = deepcopy(before_source)
    source.update(revision=before_source['revision']+1, source_sha256=projection.body_sha256,
        source_body_sha256=projection.body_sha256, anchor_binding_sha256=projection.body_sha256,
        source_file_sha256=versions['new_file_sha256'], source_text_sha256=projection.spoken_text_sha256,
        editorial_structure_sha256=projection.editorial_structure_sha256,
        editorial_topology_sha256=projection.editorial_topology_sha256)
    quote_changes = {r['fragment_id']:r for r in proposal['proposed_exact_quote_changes']}
    after_fragments = []
    for oid, before in sorted(fragments.items()):
        n = int(before['paragraph_key'][1:])-1
        old_text, new_row = original[n], projection.spoken_rows[n]
        quote = before['verbatim_excerpt']
        if before['source_sha256'] != versions['old_body_sha256'] or before['paragraph_text_sha256'] != text_sha(old_text) or quote not in old_text or text_sha(quote) != before['verbatim_excerpt_sha256']:
            raise ValueError('fragment old exact-source binding drift')
        for field, row_field in [('source_segment_index','index'), ('media_time','start_time'), ('media_end_time','end_time')]:
            if before.get(field) is not None and new_row.get(row_field) != before[field]:
                raise ValueError('fragment coordinate changed')
        if oid in quote_changes:
            spec = quote_changes[oid]
            if quote != spec['before_quote'] or sha256_json(before) != spec['before_payload_sha256']:
                raise ValueError('changed quote predecessor drift')
            quote = spec['proposed_after_quote']
        if not quote or quote not in new_row['text']:
            raise ValueError('fragment lacks exact new-source quote')
        after = deepcopy(before)
        after.update(revision=before['revision']+1, source_sha256=projection.body_sha256,
            paragraph_text_sha256=text_sha(new_row['text']), verbatim_excerpt=quote,
            verbatim_excerpt_sha256=text_sha(quote))
        after_fragments.append(after)
    evidence = []
    for spec in proposal['proposed_evidence_changes']:
        old = states['evidence_steps', spec['evidence_step_id']]
        if sha256_json(old) != spec['before_payload_sha256']:
            raise ValueError('Evidence predecessor drift')
        new = deepcopy(old); new.update(spec['changed_fields']); new['revision'] += 1
        evidence.append(new)
    claims = []
    for spec in proposal['proposed_claim_anchor_metadata_changes']:
        old = states['claims', spec['claim_id']]
        new = deepcopy(old); anchors = [a for o in new['occurrences'] for a in o['anchors'] if a['evidence_id'] == spec['evidence_id']]
        if len(anchors) != 1 or anchors[0]['proposed_highlight']['text'] != spec['quote'] or old['review_status'] != 'ai_consensus_reviewed':
            raise ValueError('Claim anchor predecessor/hold drift')
        anchors[0]['proposed_highlight']['text'] = spec['proposed_after_quote']
        new.update(revision=old['revision']+1, reviewed_at=stable_decided_at(root),
            reviewed_by='gpt-6.1-sol+claude-opus-5-5',
            review_note=f'Owner transcript anchor-only reconciliation; fresh dual review {root}/review-report.json; statement unchanged, old review retained.')
        claims.append(new)
    link = deepcopy(states['viewpoint_claim_links', LINK])
    changed = next(c for c in claims if c['claim_id'] == link['claim_id'])
    link.update(revision=link['revision']+1, pinned_claim_revision=changed['revision'])
    link['component_locator']['claim_sha256'] = semantic_record_sha(changed)
    package = {'package_id':'G041-OWNER-TRANSCRIPT-'+sha256_json({'root':str(root),'proposal':PROPOSAL_SHA})[:20],
        'source_documents':[source], 'source_fragments':after_fragments, 'evidence_steps':evidence,
        'claims':claims, 'viewpoint_claim_links':[link]}
    viewpoints = {l['payload']['viewpoint_id'] for l in report['active_links']}
    packet = {'schema_version':VERSION, 'approval_status':'not_human_approved',
        'owner_authorization':'owner corrected transcript, requested continue; no Claim/CVP body changes',
        'scope_proposal_binding':{'path':str(IMPACT/'reconciliation-scope-proposal.json'),'file_sha256':PROPOSAL_SHA},
        'impact_binding':{'path':str(IMPACT/'impact-report.json'),'file_sha256':REPORT_SHA},
        'source_versions':versions, 'complete_original':original,
        'complete_corrected':[r['text'] for r in projection.spoken_rows],
        'semantic_after':{k:v for k,v in package.items() if k not in {'source_fragments','package_id'}},
        'changed_quote_postimages':[f for f in after_fragments if f['fragment_id'] in quote_changes],
        'changed_paragraph_claims':[r['payload'] for r in report['changed_paragraph_claims']],
        'changed_paragraph_evidence':[r['payload'] for r in report['changed_paragraph_evidence']],
        'active_links':[r['payload'] for r in report['active_links']],
        'target_viewpoints':[{'identity':states['canonical_viewpoints',v],
            'revision':states['viewpoint_revisions',states['canonical_viewpoints',v]['current_revision_id']]} for v in sorted(viewpoints)],
        'mechanical_proof':{'source_documents':1,'source_fragments':596,'changed_quotes':5,
            'unchanged_paragraphs':6,'unchanged_coordinates_verified':True,
            'all_after_fragments_sha256':sha256_json(after_fragments),
            'old_spoken_source_from':'physical SHA-bound accepted grouping; not old raw file verification',
            'claim_statement_changes':0,'cvp_body_changes':0,'physical_source_writes':0,
            'frozen_claims':973,'frozen_groups':156,'human_holds_released':0},
        'draft_package_sha256':sha256_json(package)}
    return package, packet


def adapter(provider, model):
    from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
    result = StructuredJsonReviewerAdapter(client=subscription_client(provider,model,'high'),
        prompt=PROMPT,response_model=Review,schema_name=VERSION,
        compact_payload=True,shared_text_payload=True,shared_record_payload=True)
    result.max_request_bytes = 500000
    return result


def canonical_runtime():
    """Reuse the exact producer runtime proof; never diagnose another identity."""
    from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
    from backend.pipeline.codex_subscription_preflight import verify_runtime
    recorded = read_artifact(RUNTIME_RECEIPT)['codex_runtime']
    identity = recorded['identity']; executable = Path(identity['cli']['path'])
    proof_path = Path(recorded['proof_path']); proof = read_artifact(proof_path)
    if proof['identity'] != identity or file_sha256(executable) != identity['cli']['file_sha256']:
        raise ValueError('canonical producer runtime proof/physical identity drift')
    client = CodexSubscriptionClient(model='gpt-6.1-sol',reasoning_effort='high',executable=str(executable))
    client._verify_chatgpt_login()
    auth = Path(client.environment.get('CODEX_HOME',str(Path.home()/'.codex')))/'auth.json'
    actual = {'cli':client.cli_identity,'model':client.model,'backend':client.backend,
        'reasoning_effort':client.reasoning_effort,'login_state_sha256':file_sha256(auth)}
    if actual != identity:
        raise ValueError('canonical runtime/login changed; no new diagnostic fallback')
    reused = verify_runtime(client,proof_path.parent)
    if reused['proof_path'] != str(proof_path) or not reused['reused'] or reused['new_model_calls'] != 0:
        raise ValueError('canonical runtime must reuse prior successful proof')
    os.environ['CODEX_EXECUTABLE'] = str(executable)
    return reused


def zero_call_predecessor():
    """One mechanical continuation, retaining the spent reservation and failure."""
    failure = read_artifact(FIRST_ROOT/'failure.json')
    failed = read_artifact(FIRST_ROOT/'failed-review-gpt.json')
    reservation = read_artifact(IMPACT/'owner-transcript-reconciliation-start.json')
    packet = read_artifact(FIRST_ROOT/'packet.json')
    if (failure.get('error') != 'Codex executable unavailable: /Users/junyang/.vscode/extensions/openai.chatgpt-26.818.41705-darwin-arm64/bin/macos-aarch64/codex'
            or failed.get('error') != failure['error'] or failed.get('raw_response') is not None
            or failed.get('packet_sha256') != sha256_json(packet)
            or reservation.get('output_root') != str(FIRST_ROOT)
            or reservation.get('packet_sha256') != sha256_json(packet)
            or reservation.get('proposal_sha256') != PROPOSAL_SHA
            or any((FIRST_ROOT/n).exists() for n in ('raw-gpt.json','raw-opus.json','apply-intent.json','apply-receipt.json','review-report.json'))):
        raise ValueError('runtime continuation requires exact zero-inference predecessor')
    return [{'path':str(FIRST_ROOT/n),'file_sha256':file_sha256(FIRST_ROOT/n)}
        for n in ('failure.json','failed-review-gpt.json','packet.json','before.json')]+[
        {'path':str(IMPACT/'owner-transcript-reconciliation-start.json'),
         'file_sha256':file_sha256(IMPACT/'owner-transcript-reconciliation-start.json')}]


def require_approval(response, packet):
    parsed = Review.model_validate(response)
    ids = {c['claim_id'] for c in packet['changed_paragraph_claims']}
    if len(parsed.verdicts) != len(ids) or {v.claim_id for v in parsed.verdicts} != ids or any(not v.approved or v.problems for v in parsed.verdicts):
        raise ValueError('fresh source review rejected or incomplete; no automatic repeat')
    return parsed.model_dump(mode='json')


def run(root, review=False, mechanical_runtime_continuation=False):
    from backend.pipeline.cvp_retired_runtime import reject_legacy_execution
    reject_legacy_execution()
    if subprocess.check_output(['git','status','--porcelain'],cwd=PROJECT,text=True).strip():
        raise ValueError('commit bounded implementation before source review')
    load_dotenv(PROJECT/'.env')
    runtime = canonical_runtime()
    predecessor_bindings = zero_call_predecessor() if mechanical_runtime_continuation else []
    if mechanical_runtime_continuation and root != BASE/'g041-owner-transcript-reconciliation-runtime-20261005-v2':
        raise ValueError('one distinct mechanical runtime continuation root required')
    report, proposal = frozen_inputs()
    states = snapshot(PostgresKnowledgeStore()); collections = sorted({c for c,_ in states})
    package, packet = materialize(states, report, proposal, root)
    pre = fingerprint(states, collections)
    proof = {'collections':collections,'pre_fingerprint_sha256':pre,
        'before_records':[{'collection':c,'object_id':o,'payload':p} for (c,o),p in sorted(states.items())]}
    write_immutable(root/'before.json',proof); write_immutable(root/'draft-package.json',package)
    write_immutable(root/'packet.json',packet)
    write_immutable(root/'canonical-runtime.json',runtime)
    preflights = {}
    for label,provider,model in [('gpt','codex','gpt-6.1-sol'),('opus','claude','claude-opus-5-5')]:
        a = adapter(provider, model); size = a.request_bytes(packet)
        if size > 500000: raise ValueError('source review exceeds frozen request ceiling')
        preflights[label] = {'model':model,'backend':a.backend,'request_bytes':size,
            'request_encoding_proof':a.request_encoding_proof(packet)}
    write_immutable(root/'preflight.json',{'packet_sha256':sha256_json(packet),'draft_package_sha256':sha256_json(package),
        'requests':preflights,'registry_writes':0,'models_called':0})
    if not review:
        print(json.dumps({'status':'preflight_passed_no_models_no_writes','requests':{k:v['request_bytes'] for k,v in preflights.items()}}));return
    reservation = (FIRST_ROOT/'mechanical-runtime-continuation.json' if mechanical_runtime_continuation
        else IMPACT/'owner-transcript-reconciliation-start.json')
    if reservation.exists():
        raise ValueError('source review already reserved; preserve raw artifacts, no repeat')
    authority = {'schema_version':VERSION,'output_root':str(root),'packet_sha256':sha256_json(packet),
        'proposal_sha256':PROPOSAL_SHA,'human_content_approval':False,'fresh_reviews_per_provider':1,
        'zero_call_predecessor_bindings':predecessor_bindings,'runtime_proof':runtime,
        'old_failure_and_budget_preserved':True}
    write_immutable(reservation,authority);write_immutable(root/'owner-authorization.json',authority)
    reviews = {}
    for label,provider,model in [('gpt','codex','gpt-6.1-sol'),('opus','claude','claude-opus-5-5')]:
        a = adapter(provider,model)
        write_immutable(root/f'progress-{label}.json',{'status':'reviewing','master_data_mutations':0})
        raw,_,_ = review_call(a,packet,root,label)
        reviews[label] = require_approval(raw,packet)
        if fingerprint(snapshot(PostgresKnowledgeStore()),collections) != pre:
            raise ValueError('Registry changed during bounded review')
        verify_source(report)
    write_immutable(root/'review-report.json',{'status':'dual_review_approved_not_applied',
        'packet_sha256':sha256_json(packet),'reviews':reviews,'master_data_mutations':0})
    print('fresh complete dual review approved; store/source-version consumer and audit gates still required')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root',type=Path,required=True)
    parser.add_argument('--review',action='store_true')
    parser.add_argument('--mechanical-runtime-continuation',action='store_true')
    args=parser.parse_args();root=args.output_root.resolve()
    if root.parent != BASE or not root.name.startswith('g041-owner-transcript-reconciliation-'):
        raise ValueError('independent owner source repair root required')
    root.mkdir(parents=True,exist_ok=True)
    with (BASE/'immediate-recovery-20261002-v1/recovery.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:run(root,args.review,args.mechanical_runtime_continuation)
        except Exception as exc:
            write_immutable(root/'failure.json',{'error':str(exc),'master_data_mutations':0,'automatic_repeat':False})
            raise


if __name__ == '__main__':main()
