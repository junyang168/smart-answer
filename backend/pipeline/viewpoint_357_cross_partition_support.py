"""Source-bound g010 cross-partition proposal and future-resolution contract.

This preparatory runner never writes Registry data or resumes production. Pending
theological identity must not be manufactured from a topic name or a partial link.
"""
from copy import deepcopy
import argparse
import fcntl
import json
from pathlib import Path

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha, sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import file_sha256
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.pipeline.viewpoint_authorized_source_repair_357 import snapshot, Review, review_call
from backend.pipeline.viewpoint_357_g010_link_revision import BASE, PROJECT, prepare
from backend.pipeline.viewpoint_resolution_runtime import subscription_client, write_immutable, write_derived

CLAIM = 'DK-d7a3359c50c895c48b09-P08-CL001'
OLD_LINK = 'VCL-84a15e86c11ea67ac44c'
EXEGESIS = 'CV-fa68184befde30dfbb8e'
FIRST_LINK = 'VCL-3c7053f2c9c443e38b79'


def verify_amendment(amendment):
    body = deepcopy(amendment)
    digest = body.pop('artifact_sha256')
    if sha256_json(body) != digest:
        raise ValueError('classification amendment hash mismatch')
    if (body['claim_id'], body['from_role'], body['to_role'], body['to_partition']) != (
        CLAIM, 'passage_exegesis', 'other', 'salvation'
    ):
        raise ValueError('unauthorized classification scope')
    for pin in list(body['bindings'].values()) + [body['source_binding']]:
        if file_sha256(Path(pin['path'])) != pin['file_sha256']:
            raise ValueError('classification/source binding drift')


def role_projection(ledger, manifest, amendment):
    """New projection, not an in-place rewrite of either frozen artifact."""
    if ledger['artifact_sha256'] != amendment['bindings']['role_ledger']['artifact_sha256'] or manifest['artifact_sha256'] != amendment['bindings']['partition_manifest']['artifact_sha256']:
        raise ValueError('parent artifact mismatch')
    decisions = deepcopy(ledger['decisions'])
    rows = list(decisions.values()) if isinstance(decisions, dict) else decisions
    matches = [r for r in rows if r['claim_id'] == CLAIM]
    if len(matches) != 1 or matches[0]['role'] != 'passage_exegesis' or CLAIM in manifest['owners']:
        raise ValueError('ownership mismatch or duplicate')
    row = matches[0]
    row.update(role='other', decision_basis='explicit_user_classification_direction',
               classification_amendment_sha256=amendment['artifact_sha256'])
    owners = deepcopy(manifest['owners'])
    owners[CLAIM] = {'partition': 'salvation', 'claim_revision': amendment['claim_revision'],
                     'claim_statement_sha256': amendment['statement_sha256'],
                     'decision_basis': 'explicit_user_classification_direction',
                     'classification_amendment_sha256': amendment['artifact_sha256']}
    return {'schema_version': 'wkp357_role_partition_projection.v1',
            'parent_role_ledger_sha256': ledger['artifact_sha256'],
            'parent_partition_manifest_sha256': manifest['artifact_sha256'],
            'decisions': decisions, 'owners': owners,
            'preserved_relations': deepcopy(manifest['preserved_relations']),
            'grouping_authorization': False, 'database_mutations': 0,
            'status': 'classification_projection_not_campaign_freeze'}


def build(states, amendment):
    packet = prepare(states)
    claim = states['claims', CLAIM]
    if claim['revision'] != amendment['claim_revision'] or claim['statement'] != amendment['statement']:
        raise ValueError('Claim revision drift')
    head = states['canonical_viewpoints', EXEGESIS]
    target_revision = states['viewpoint_revisions', head['current_revision_id']]
    target = next(t for t in packet['targets'] if t['claim']['claim_id'] == CLAIM)
    first = next(t for t in packet['targets'] if t['link']['viewpoint_claim_link_id'] == FIRST_LINK)
    first_after = deepcopy(first['link'])
    first_after.update(revision=first_after['revision'] + 1, link_type='equivalent_full', component_locator=None)
    first_after['evidence_bindings'] = sorted(
        [{'evidence_step_id': e['step']['evidence_step_id'], 'source_fragment_id': e['fragment']['fragment_id']}
         for e in first['available_evidence']], key=lambda b: (b['evidence_step_id'], b['source_fragment_id']))
    retired = deepcopy(target['link'])
    retired.update(revision=retired['revision'] + 1, effective_state='retired')
    support = deepcopy(target['link'])
    support.update(viewpoint_claim_link_id='VCL-'+sha256_json({'claim': CLAIM, 'viewpoint': EXEGESIS, 'role': 'supports', 'amendment': amendment['artifact_sha256']})[:20],
                   revision=1, viewpoint_id=EXEGESIS, validated_against_viewpoint_revision_id=target_revision['viewpoint_revision_id'],
                   link_type='supports', component_locator=None, decision_id='pending_independent_review')
    support['evidence_bindings'] = sorted(
        [{'evidence_step_id': e['step']['evidence_step_id'], 'source_fragment_id': e['fragment']['fragment_id']}
         for e in target['available_evidence']], key=lambda b: (b['evidence_step_id'], b['source_fragment_id']))
    target_members = [p for (c, _), p in states.items() if c == 'viewpoint_claim_links' and p.get('viewpoint_id') == EXEGESIS and p.get('effective_state') == 'active']
    packet.update(schema_version='wkp357_cross_partition_support_review.v1', classification_amendment=amendment,
                  target_exegesis_revision=target_revision, target_exegesis_links=target_members,
                  target_exegesis_claims=[states['claims', cid] for cid in sorted({p['claim_id'] for p in target_members})],
                  proposed_links=[first_after, retired, support],
                  authorization='Owner: retire false equivalence, retain complete source-bound theological Claim as support for concrete exegesis; no Claim/Evidence/source edits. Proposal only; no production continuation.')
    contract = {'schema_version': 'wkp357_pending_theological_connection.v1', 'status': 'pending_theological_identity',
                'claim_id': CLAIM, 'claim_revision': claim['revision'], 'claim_semantic_sha256': semantic_record_sha(claim),
                'classification_amendment_sha256': amendment['artifact_sha256'], 'primary_partition': 'salvation',
                'exegesis_viewpoint_id': EXEGESIS, 'exegesis_revision_id': target_revision['viewpoint_revision_id'],
                'exegesis_revision_sha256': sha256_json(target_revision),
                'proposed_support_link_id': support['viewpoint_claim_link_id'],
                'source_bindings': deepcopy(packet['sources']),
                'future_relation': {'relation_type': 'applies', 'source': 'exegesis_viewpoint', 'target': 'reviewed_theological_viewpoint'},
                'identity_requirement': 'reviewed full Claim equivalence, not topic/keyword/component matching',
                'requires_exact_proposal_review_and_independent_audit': True,
                'database_mutations': 0, 'apply_authorization': False}
    return packet, contract


def validate_future_candidate(contract, claim, exegesis_revision, theological_revision, membership):
    """Deterministic prerequisites only: semantic review/audit still required."""
    if claim['claim_id'] != contract['claim_id'] or claim['revision'] != contract['claim_revision'] or semantic_record_sha(claim) != contract['claim_semantic_sha256']:
        raise ValueError('pending Claim changed; re-review required')
    if sha256_json(exegesis_revision) != contract['exegesis_revision_sha256'] or exegesis_revision['viewpoint_revision_id'] != contract['exegesis_revision_id']:
        raise ValueError('exegesis endpoint changed; re-review required')
    if (membership.get('effective_state'), membership.get('link_type'), membership.get('component_locator'), membership.get('claim_id'), membership.get('pinned_claim_revision')) != ('active', 'equivalent_full', None, claim['claim_id'], claim['revision']):
        raise ValueError('no reviewed full Claim identity')
    if membership.get('review_status') not in {'system_approved', 'human_approved', 'approved'} or not membership.get('decision_id'):
        raise ValueError('unreviewed theological membership')
    if membership['viewpoint_id'] != theological_revision['viewpoint_id'] or membership['validated_against_viewpoint_revision_id'] != theological_revision['viewpoint_revision_id'] or theological_revision['viewpoint_id'] == contract['exegesis_viewpoint_id']:
        raise ValueError('wrong theological endpoint')
    for source in contract['source_bindings']:
        if file_sha256(Path(source['path'])) != source['file_sha256']:
            raise ValueError('physical source changed')
    return {'relation_type': 'applies', 'source_viewpoint_id': contract['exegesis_viewpoint_id'],
            'target_viewpoint_id': theological_revision['viewpoint_id'], 'supporting_claim_ids': [claim['claim_id']],
            'status': 'candidate_requires_semantic_review_and_independent_audit', 'apply_authorization': False}


REVIEW_PROMPT = '''核对完整来源和所有实际拟修订连接，每个targets的Claim各一个verdict。
第一条改为完整Claim等价，保留受洗/连合/代负罪债条件。第二条撤销与一般义定义的错误等价，另建完整神学Claim supports 具体“约翰施洗促进自己与神好关系”的释经CVP；不是一般义CVP的supports。
读取目标释经CVP、来源原文、现有所有相邻连接和关系。原文是否确实把该神学前提用于解释约翰施洗？支持须保留具体行动限定，不得仅凭主题相似。检查仍保留的相邻qualifies等关系是否也需修订；若有问题必须指出，不为续跑默许。
Claim、Evidence、来源、CVP正文和已有其他连接不改。未来神学身份仅pending，不能当作已生成或已审核。review不授权写库；approved只有无任何problems才true。'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--amendment', type=Path, required=True)
    parser.add_argument('--review', action='store_true')
    args = parser.parse_args()
    root = args.output_root.resolve()
    if PROJECT.name != 'wkp-357-worktree-per-session' or root.parent != BASE or root.exists():
        raise ValueError('fresh independent #357 root required')
    load_dotenv(PROJECT/'.env')
    with (BASE/'immediate-recovery-20261002-v1/recovery.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        from backend.pipeline.viewpoint_cvp_recovery_supervisor import remaining_producers, process_table, current_job_process_ids
        if any(p not in current_job_process_ids() for p in remaining_producers(process_table(), BASE)):
            raise ValueError('another producer active')
        amendment = json.loads(args.amendment.read_text()); verify_amendment(amendment)
        ledger = json.loads(Path(amendment['bindings']['role_ledger']['path']).read_text())
        manifest = json.loads(Path(amendment['bindings']['partition_manifest']['path']).read_text())
        states = snapshot(PostgresKnowledgeStore())
        packet, contract = build(states, amendment)
        root.mkdir()
        write_immutable(root/'classification-projection.json', role_projection(ledger, manifest, amendment))
        write_immutable(root/'packet.json', packet); write_immutable(root/'pending-connection.json', contract)
        result = {'status': 'prepared_not_applied', 'database_mutations': 0, 'production_resumed': False}
        write_derived(root/'progress.json', result)
        if args.review:
            for label, backend, model in [('gpt-review', 'codex', 'gpt-6.1-sol'), ('opus-review', 'claude', 'claude-opus-5-5')]:
                write_derived(root/'progress.json', result | {'status': label})
                adapter = StructuredJsonReviewerAdapter(client=subscription_client(backend, model, 'high'), prompt=REVIEW_PROMPT, response_model=Review, schema_name='cross_partition_support_review', compact_payload=True)
                raw, _, _ = review_call(adapter, packet, root, label)
                review = Review.model_validate(raw).model_dump(mode='json')
                expected = {t['claim']['claim_id'] for t in packet['targets']}
                if len(review['verdicts']) != len(expected) or {v['claim_id'] for v in review['verdicts']} != expected:
                    raise ValueError('review population mismatch')
                write_immutable(root/(label+'.json'), review)
            reviews = [json.loads((root/(name+'.json')).read_text()) for name in ['gpt-review','opus-review']]
            result['status'] = 'dual_review_pass_not_applied' if all(v['approved'] and not v['problems'] for r in reviews for v in r['verdicts']) else 'content_findings_no_apply'
            write_derived(root/'progress.json', result)
        print(json.dumps(result))


if __name__ == '__main__':
    main()
