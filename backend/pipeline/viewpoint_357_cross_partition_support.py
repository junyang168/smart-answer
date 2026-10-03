"""Source-bound g010 cross-partition proposal and future-resolution contract.

This preparatory runner never writes Registry data or resumes production. Pending
theological identity must not be manufactured from a topic name or a partial link.
"""
from copy import deepcopy
import argparse
import fcntl
import json
from pathlib import Path
from pydantic import ConfigDict

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha, sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import file_sha256
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.pipeline.viewpoint_authorized_source_repair_357 import snapshot, Review, review_call
from backend.pipeline.viewpoint_357_g010_link_revision import BASE, PROJECT, prepare
from backend.pipeline.viewpoint_resolution_runtime import subscription_client, write_immutable, write_derived, read_artifact, stable_decided_at
from backend.pipeline.viewpoint_authorized_source_repair_357 import fingerprint
from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock

CLAIM = 'DK-d7a3359c50c895c48b09-P08-CL001'
OLD_LINK = 'VCL-84a15e86c11ea67ac44c'
EXEGESIS = 'CV-fa68184befde30dfbb8e'
FIRST_LINK = 'VCL-3c7053f2c9c443e38b79'
QUALIFIES_LINK = 'VCL-36e9fc3812d574144d5f'
NEGATIVE_LINK = 'VCL-a6bfc37011c50b2c61be'
ADJACENT_DENIAL_LINK = 'VCL-0cb2c47000de7df6364e'
CAMPAIGN = BASE/'matthew-full-20261002-v7-recovery-5cace1dfce87-recovery-7dce4372f79f-recovery-40104d43ce05-recovery-0e53c144d586-recovery-001343224f1e-recovery-a826fedccf5c'


class WholeGroupReview(Review):
    model_config = ConfigDict(extra='forbid')
    checked_link_ids: list[str]
    checked_dependency_ids: list[str]
    closure_problems: list[str]


def whole_group_context(states, packet):
    """One bounded closure: every g010 member and incident dependency, no topic search."""
    from backend.pipeline.viewpoint_evidence_source_context import load_context_source
    plans = list((CAMPAIGN/'g010/production/run/batches').glob('*/cvp-change-plan.json'))
    if len(plans) != 1:
        raise ValueError('one original g010 plan required')
    plan = read_artifact(plans[0])
    if plan['change_set_id'] != 'KCS-7684227135299cf96b6c':
        raise ValueError('wrong g010 applied identity')
    viewpoints = {op['payload']['viewpoint_id'] for op in plan['operations'] if op['collection']=='viewpoint_claim_links'}
    if len(viewpoints) != 11:
        raise ValueError('g010 viewpoint population drift')
    links = [deepcopy(p) for (c, _), p in states.items() if c=='viewpoint_claim_links'
             and p.get('effective_state')=='active' and p['viewpoint_id'] in viewpoints]
    claim_ids = {p['claim_id'] for p in links}
    # Include all active neighbours of those Claims, not just links in g010.
    links = sorted([deepcopy(p) for (c, _), p in states.items() if c=='viewpoint_claim_links'
                    and p.get('effective_state')=='active' and (p['viewpoint_id'] in viewpoints or p['claim_id'] in claim_ids)],
                   key=lambda p:p['viewpoint_claim_link_id'])
    endpoints = viewpoints | {p['viewpoint_id'] for p in links}
    revisions = [deepcopy(states['viewpoint_revisions', states['canonical_viewpoints', vid]['current_revision_id']]) for vid in sorted(endpoints)]
    dependencies = []
    for (collection, key), payload in sorted(states.items()):
        if collection not in {'viewpoint_relations','viewpoint_structures','viewpoint_structure_revisions'}:
            continue
        if any(vid in json.dumps(payload) for vid in endpoints):
            dependencies.append({'collection':collection,'id':key,'unchanged_postimage':deepcopy(payload)})
    sources = {s['source_id']:s for s in packet['sources']}
    all_steps = {eid for cid in claim_ids for eid in states['claims',cid]['evidence_step_ids']}
    for eid in sorted(all_steps):
        for fid in states['evidence_steps',eid]['source_fragment_ids']:
            sid = states['source_fragments',fid]['source_id']
            if sid not in sources:
                path, sha, projection = load_context_source(states['source_documents',sid])
                sources[sid] = {'source_id':sid,'path':str(path),'file_sha256':sha,'body_sha256':projection.body_sha256,
                                'complete_original':[r['text'] for r in projection.spoken_rows]}
    packet['sources'] = [sources[sid] for sid in sorted(sources)]
    existing = {t['claim']['claim_id']:t for t in packet['targets']}
    for cid in sorted(claim_ids):
        claim = states['claims',cid]
        evidence = exact_evidence(states,claim,packet['sources'],claim['evidence_step_ids'])
        if cid in existing:
            existing[cid]['available_evidence'] = evidence
        else:
            existing[cid] = {'claim':deepcopy(claim),'available_evidence':evidence,
                             'adjacent_links':[p for p in links if p['claim_id']==cid]}
    packet['targets'] = [existing[cid] for cid in sorted(existing)]
    proposed = {p['viewpoint_claim_link_id']:p for p in packet['proposed_links']}
    # Lossless table references avoid repeating identical full records. All
    # semantic fields remain in current_links/proposed_links; this is not a
    # content projection or truncation.
    link_table = {p['viewpoint_claim_link_id']:p for p in links}
    for target in packet['targets']:
        if 'adjacent_links' in target:
            for link in target['adjacent_links']:
                if link_table.get(link['viewpoint_claim_link_id']) != link:
                    raise ValueError('neighbour link table mismatch')
            target['adjacent_link_ids'] = [p['viewpoint_claim_link_id'] for p in target.pop('adjacent_links')]
        if 'link' in target:
            if link_table.get(target['link']['viewpoint_claim_link_id']) != target['link']:
                raise ValueError('original target link table mismatch')
            target['original_link_id'] = target.pop('link')['viewpoint_claim_link_id']
    packet['group_closure'] = {'original_plan_binding':{'path':str(plans[0]),'file_sha256':file_sha256(plans[0])},
                             'viewpoint_ids':sorted(viewpoints),'current_revisions':revisions,
                             'current_links':links,'proposed_effective_link_refs':[
                                 {'link_id':key,'record_table':'proposed_links' if key in proposed else 'group_closure.current_links'}
                                 for key in sorted(set(proposed)|set(link_table))],
                             'dependencies':dependencies,'required_link_ids':sorted(set(proposed)|{p['viewpoint_claim_link_id'] for p in links}),
                             'required_dependency_ids':sorted(d['id'] for d in dependencies),
                             'coverage':'all 11 original g010 CVPs, all active members, all incident Claim neighbours and viewpoint dependencies'}
    return packet


def validate_closure_review(review, packet):
    closure = packet['group_closure']
    for field, expected in [('checked_link_ids',closure['required_link_ids']),('checked_dependency_ids',closure['required_dependency_ids'])]:
        if len(review[field]) != len(set(review[field])) or set(review[field]) != set(expected):
            raise ValueError('incomplete or duplicate whole-group review coverage: '+field)
    return not review['closure_problems']


def exact_evidence(states, claim, packet_sources, step_ids):
    """Expand actual Claim-owned steps, including previously unlinked evidence."""
    sources = {s['source_id']: s for s in packet_sources}
    rows = []
    for eid in sorted(step_ids):
        if eid not in claim['evidence_step_ids']:
            raise ValueError('foreign evidence step')
        step = states['evidence_steps', eid]
        if claim['claim_id'] not in step['produced_claim_ids']:
            raise ValueError('non-reciprocal Claim evidence')
        for fid in sorted(step['source_fragment_ids']):
            fragment = states['source_fragments', fid]
            source = sources[fragment['source_id']]
            if file_sha256(Path(source['path'])) != source['file_sha256']:
                raise ValueError('physical source drift')
            paragraph = source['complete_original'][int(fragment['paragraph_key'][1:])-1]
            if not fragment['verbatim_excerpt'] or fragment['verbatim_excerpt'] not in paragraph:
                raise ValueError('evidence excerpt is not verbatim in pinned paragraph')
            rows.append({'step': deepcopy(step), 'fragment': deepcopy(fragment)})
    return rows


def occurrence_ref(claim, bindings):
    """Same occurrence seed as viewpoint_batch_changeset (not one ref/step)."""
    seed = {'claim_id': claim['claim_id'], 'claim_revision_sha256': semantic_record_sha(claim),
            'evidence': sorted({(b['evidence_step_id'], b['source_fragment_id']) for b in bindings})}
    return 'OCC-'+sha256_json(seed)[:20]


def mark_proposed(link, action, amendment_sha):
    """Candidate metadata is never an active/approved Registry record."""
    seed = {'link': link, 'action': action, 'amendment': amendment_sha}
    link['decision_id'] = 'VID-'+sha256_json(seed)[:20]
    link['review_status'] = 'candidate'
    if action != 'retire':
        link['effective_state'] = 'invalidated'
    return {'identity_decision_id': link['decision_id'], 'status': 'candidate_pending_exact_postimage_review',
            'action': action, 'claim_id': link['claim_id'], 'viewpoint_id': link['viewpoint_id'],
            'link_id': link['viewpoint_claim_link_id'], 'link_type': link['link_type'],
            'intended_effective_state_after_review': 'retired' if action == 'retire' else 'active',
            'prior_decision_preserved': True, 'approval_basis_after_review': 'dual_model_consensus'}


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
    first_after['occurrence_refs'] = [occurrence_ref(first['claim'], first_after['evidence_bindings'])]
    retired = deepcopy(target['link'])
    retired.update(revision=retired['revision'] + 1, effective_state='retired')
    qualifies = deepcopy(states['viewpoint_claim_links', QUALIFIES_LINK])
    if qualifies['revision'] != 1 or qualifies['claim_id'] != CLAIM or qualifies['effective_state'] != 'active':
        raise ValueError('adjacent qualifies revision drift')
    qualifies.update(revision=2, effective_state='retired')
    support_steps = [CLAIM.replace('CL001', 'E'+suffix) for suffix in ['002','003','005','006']]
    evidence = exact_evidence(states, claim, packet['sources'], support_steps)
    target['available_evidence'] = exact_evidence(states, claim, packet['sources'], claim['evidence_step_ids'])
    support = deepcopy(target['link'])
    support.update(viewpoint_claim_link_id='VCL-'+sha256_json({'claim': CLAIM, 'viewpoint': EXEGESIS, 'role': 'supports', 'amendment': amendment['artifact_sha256']})[:20],
                   revision=1, viewpoint_id=EXEGESIS, validated_against_viewpoint_revision_id=target_revision['viewpoint_revision_id'],
                   link_type='supports', component_locator=None)
    support['evidence_bindings'] = sorted(
        [{'evidence_step_id': e['step']['evidence_step_id'], 'source_fragment_id': e['fragment']['fragment_id']}
         for e in evidence], key=lambda b: (b['evidence_step_id'], b['source_fragment_id']))
    support['occurrence_refs'] = [occurrence_ref(claim, support['evidence_bindings'])]
    negative = deepcopy(states['viewpoint_claim_links', NEGATIVE_LINK])
    if negative['revision'] != 1 or negative['claim_id'] != CLAIM or negative['effective_state'] != 'active':
        raise ValueError('negative component revision drift')
    text = '「義」不是審判、滅罪'
    if claim['statement'].count(text) != 1:
        raise ValueError('negative component is not a unique exact substring')
    start = claim['statement'].index(text)
    negative.update(revision=2, link_type='supports', component_locator={'claim_sha256':semantic_record_sha(claim),
        'statement_component':text,'canonical_spans':[{'start_char':start,'end_char':start+len(text),'exact_text':text}]})
    negative['occurrence_refs'] = [occurrence_ref(claim, negative['evidence_bindings'])]
    negative_head = states['canonical_viewpoints', negative['viewpoint_id']]
    if negative_head['current_revision_id'] != negative['validated_against_viewpoint_revision_id']:
        raise ValueError('negative viewpoint drift')
    adjacent_denial = deepcopy(states['viewpoint_claim_links', ADJACENT_DENIAL_LINK])
    if adjacent_denial['revision'] != 1 or adjacent_denial['link_type'] != 'qualifies' or adjacent_denial['effective_state'] != 'active':
        raise ValueError('adjacent denial revision drift')
    adjacent_denial.update(revision=2, effective_state='retired')
    decision_plans = [mark_proposed(link, action, amendment['artifact_sha256']) for link, action in
                      [(first_after,'revalidate'),(retired,'retire'),(qualifies,'retire'),(support,'supports'),(negative,'supports'),(adjacent_denial,'retire')]]
    target_members = [p for (c, _), p in states.items() if c == 'viewpoint_claim_links' and p.get('viewpoint_id') == EXEGESIS and p.get('effective_state') == 'active']
    packet.update(schema_version='wkp357_cross_partition_support_review.v1', classification_amendment=amendment,
                  target_exegesis_revision=target_revision, target_exegesis_links=target_members,
                  target_exegesis_claims=[states['claims', cid] for cid in sorted({p['claim_id'] for p in target_members})],
                  adjacent_negative_revision=states['viewpoint_revisions', negative_head['current_revision_id']],
                  proposed_links=[first_after, retired, qualifies, support, negative, adjacent_denial], proposed_decisions=decision_plans,
                  excluded_support_evidence={'E001':'否定错误解释由独立的否定命题连接保留，不作为约翰施洗的支持依据。'},
                  occurrence_seed_contract='viewpoint_batch_changeset: claim_id, claim_revision_sha256, sorted selected evidence pairs',
                  authorization='Owner: 继续 after complete-g010 correction plan; inspect all g010 links and dependencies, resolve both final findings and adjacent denial. No Claim/Evidence/source/CVP prose edits. Candidate proposal only, no production continuation.',
                  scope_resolution={'negative_link':'General theological negative component supports its explicitly witnessed Matthew3:13-15 application; not equivalent identity. Unique partition ownership is not a ban on cross-partition support.',
                                    'adjacent_denial':'Retire qualifies: denial of a competing meaning is not a condition on the affirmative definition. Preserve original Claim, opposed position, and all other memberships; do not fabricate a new negative CVP.'})
    packet = whole_group_context(states, packet)
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


def approved_package(states, packet, report, root):
    """Seal only the exact reviewed semantics; actual approval is never copied."""
    if not report['approved'] or report['packet_sha256'] != sha256_json(packet):
        raise ValueError('unapproved or different correction packet')
    if len(set(report['accepted_call_artifact_shas'])) != 2:
        raise ValueError('two actual independent review calls required')
    expected_claims = {t['claim']['claim_id'] for t in packet['targets']}
    if len(report['reviews']) != 2 or any(
        len(r['verdicts']) != len(expected_claims) or {v['claim_id'] for v in r['verdicts']} != expected_claims
        or any(not v['approved'] or v['problems'] for v in r['verdicts']) for r in report['reviews']
    ):
        raise ValueError('unresolved content findings')
    if 'group_closure' in packet and any(not validate_closure_review(r,packet) for r in report['reviews']):
        raise ValueError('unresolved whole-group findings')
    package = {'package_id':'G010-CROSS-PARTITION-'+sha256_json(report)[:20],
               'viewpoint_claim_links':[], 'viewpoint_identity_candidates':[], 'viewpoint_identity_decisions':[]}
    plans = {p['link_id']:p for p in packet['proposed_decisions']}
    for proposed in packet['proposed_links']:
        link = deepcopy(proposed); plan = plans[link['viewpoint_claim_link_id']]
        original = states.get(('viewpoint_claim_links', link['viewpoint_claim_link_id']), states['viewpoint_claim_links', OLD_LINK])
        template_decision = states['viewpoint_identity_decisions', original['decision_id']]
        candidate = deepcopy(states['viewpoint_identity_candidates', template_decision['identity_candidate_id']])
        candidate.update(revision=1, candidate_claim_ids=[link['claim_id']], candidate_viewpoint_ids=[link['viewpoint_id']],
                         proposed_action='match_existing', generation_fingerprint=sha256_json(plan), seed_relation_ids=[])
        identity = {'claims':candidate['candidate_claim_ids'], 'viewpoints':candidate['candidate_viewpoint_ids'],
                    'relations':[], 'action':'match_existing', 'blockers':candidate.get('blocker_codes',[]),
                    'coverage_snapshot_id':candidate.get('coverage_snapshot_id'), 'generation_fingerprint':candidate['generation_fingerprint']}
        if candidate.get('scope_manifest_sha256'): identity['scope_manifest_sha256'] = candidate['scope_manifest_sha256']
        candidate['identity_candidate_id'] = 'VIC-'+sha256_json(identity)[:20]
        decision = deepcopy(template_decision)
        decision.update(revision=1, identity_decision_id=link['decision_id'], identity_candidate_id=candidate['identity_candidate_id'],
                        decision='reject_match' if plan['action']=='retire' else 'match_existing', resolved_viewpoint_id=link['viewpoint_id'],
                        claim_link_decisions=[{'claim_id':link['claim_id'], 'link_type':link['link_type']}],
                        reason='Owner-authorized correction: '+plan['action']+'; exact postimage independently reviewed; historical decision preserved.',
                        input_sha256=sha256_json(packet), review_artifact_sha256=sha256_json(report),
                        semantic_call_artifact_sha256s=sorted(report['accepted_call_artifact_shas']),
                        policy_version='wkp357_cross_partition_support_revision_v2', reviewer_id='g010_source_bound_correction',
                        reviewer_model_ids=['claude-opus-5-5','gpt-6.1-sol'], reviewer_kind='system',
                        approval_basis='dual_model_consensus', review_status='system_approved', created_at=stable_decided_at(root))
        link.update(review_status='system_approved', effective_state=plan['intended_effective_state_after_review'])
        package['viewpoint_claim_links'].append(link)
        package['viewpoint_identity_candidates'].append(candidate)
        package['viewpoint_identity_decisions'].append(decision)
    return package


REVIEW_PROMPT = '''核对完整来源和所有实际拟修订连接，每个targets的Claim各一个verdict。
第一条改为完整Claim等价，保留受洗/连合/代负罪债条件。第二条撤销与一般义定义的错误等价，另建完整神学Claim supports 具体“约翰施洗促进自己与神好关系”的释经CVP；不是一般义CVP的supports。
读取目标释经CVP、来源原文、现有所有相邻连接和关系。原文是否确实把该神学前提用于解释约翰施洗？支持须保留具体行动限定，不得仅凭主题相似。检查仍保留的相邻qualifies等关系是否也需修订；若有问题必须指出，不为续跑默许。
本轮一并撤销错误相邻qualifies，补上原Claim自身E003/E006的具体施洗证据，否定词义连接保留原文顿号而非拼成“审判灭罪”。请核对补充的否定CVP正文/signature，以及每项proposed_decisions撤销或重新验证依据。
所有拟修订记录当前都是candidate，非retired记录invalidated，不伪装已批准/active。intended_effective_state_after_review仅在真实双模型审核、确定性校验、备份、ChangeSet、回读后才可生效。occurrence是整组选中证据的canonical batch seed，不是一条Evidence必有一个OCC。
Claim、Evidence、来源、CVP正文不改；只改明确列出的六条连接。否定成分不再等价，而是神学前提supports原文明确讲到的太3:13-15局部否定应用；不可仅为过审核随意降级，请检查该原文论证是否真的成立。相邻VCL-0cb2c47000de7df6364e撤销：否定另一解释不是限定肯定定义，不制造尚未做的神学CVP。
必须检查group_closure内全部11个CVP的现有成员、当前正文/signature、相邻连接和依赖。每个targets Claim各一verdict；checked_link_ids与checked_dependency_ids必须完整且不重复列出实际检查的required IDs。发现任何未修的真实连接、范围或结构问题放入closure_problems，不能默许下一组。唯一partition是Claim的分类，不代表支持关系不能跨区。未来神学身份仅pending，不能当作已生成或已审核。review不授权写库；approved只有无任何problems才true。'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--amendment', type=Path, required=True)
    parser.add_argument('--review', action='store_true')
    parser.add_argument('--prior-review-root', type=Path)
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
        if args.prior_review_root:
            prior = args.prior_review_root.resolve()
            if prior.parent != BASE:
                raise ValueError('prior review must be independent #357 root')
            packet['prior_review_findings'] = {label:read_artifact(prior/(label+'.json')) for label in ['gpt-review','opus-review']}
            packet['prior_review_packet_sha256'] = sha256_json(read_artifact(prior/'packet.json'))
            packet['prior_review_file_bindings'] = [{'path':str(prior/name), 'file_sha256':file_sha256(prior/name)}
                                                   for name in ['packet.json','gpt-review.json','opus-review.json']]
            packet['correction_round'] = 1
        root.mkdir()
        collections = sorted({c for c, _ in states})
        before = {'collections':collections, 'pre_fingerprint_sha256':fingerprint(states,collections)}
        write_immutable(root/'before.json', before)
        write_immutable(root/'classification-projection.json', role_projection(ledger, manifest, amendment))
        write_immutable(root/'packet.json', packet); write_immutable(root/'pending-connection.json', contract)
        result = {'status': 'prepared_not_applied', 'database_mutations': 0, 'production_resumed': False}
        write_derived(root/'progress.json', result)
        if args.review:
            for label, backend, model in [('gpt-review', 'codex', 'gpt-6.1-sol'), ('opus-review', 'claude', 'claude-opus-5-5')]:
                write_derived(root/'progress.json', result | {'status': label})
                adapter = StructuredJsonReviewerAdapter(client=subscription_client(backend, model, 'high'), prompt=REVIEW_PROMPT, response_model=WholeGroupReview, schema_name='cross_partition_whole_group_review', compact_payload=True)
                raw, _, _ = review_call(adapter, packet, root, label)
                review = WholeGroupReview.model_validate(raw).model_dump(mode='json')
                validate_closure_review(review,packet)
                expected = {t['claim']['claim_id'] for t in packet['targets']}
                if len(review['verdicts']) != len(expected) or {v['claim_id'] for v in review['verdicts']} != expected:
                    raise ValueError('review population mismatch')
                write_immutable(root/(label+'.json'), review)
            reviews = [json.loads((root/(name+'.json')).read_text()) for name in ['gpt-review','opus-review']]
            approved = all(v['approved'] and not v['problems'] for r in reviews for v in r['verdicts']) and all(validate_closure_review(r,packet) for r in reviews)
            report = {'approved':approved, 'packet_sha256':sha256_json(packet), 'reviews':reviews,
                      'accepted_call_artifact_shas':[read_artifact(root/('raw-'+label+'.json'))['artifact_sha256'] for label in ['gpt-review','opus-review']],
                      'human_content_approval':False, 'database_mutations':0}
            write_immutable(root/'review-report.json', report)
            result['status'] = 'dual_review_pass_not_applied' if approved else 'content_findings_no_apply'
            if approved:
                with exclusive_cvp_run_lock({'global_lock_path':str(BASE/'registry-cvp.lock')}):
                    store = PostgresKnowledgeStore(); live = snapshot(store)
                    if fingerprint(live, collections) != before['pre_fingerprint_sha256']:
                        raise ValueError('Registry changed during correction review')
                    for source in packet['sources']:
                        if file_sha256(Path(source['path'])) != source['file_sha256']:
                            raise ValueError('source changed during correction review')
                    package = approved_package(live, packet, report, root)
                    plan = store.plan_package(package, source_kind='wkp357_cross_partition_support_correction')
                    if len(plan.operations) != 18 or any(op.collection not in package for op in plan.operations):
                        raise ValueError('unexpected correction operation scope')
                    write_immutable(root/'effective-package.json', package)
                    write_immutable(root/'dry-run-plan.json', json.loads(json.dumps(plan.as_dict())))
                    result.update(status='reviewed_dry_run_pass_not_applied', planned_operations=len(plan.operations))
            write_derived(root/'progress.json', result)
        print(json.dumps(result))


if __name__ == '__main__':
    main()
