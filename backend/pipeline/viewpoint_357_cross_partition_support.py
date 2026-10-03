"""Source-bound g010 cross-partition proposal and future-resolution contract.

Preparation is read-only. Explicit --apply-and-audit requires two exact independent
reviews, a verified backup, unchanged Registry/source pins and exact readback before
independent audit. It never resumes production or manufactures theological identity.
"""
from copy import deepcopy
import argparse
import fcntl
import json
import subprocess
from pathlib import Path
from pydantic import ConfigDict

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore, stored_operation_payload
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
PURPOSE_LINK = 'VCL-d6f273ad93fd0d88cb00'
COMPOSITE_VIEWPOINT = 'CV-d03679c0b155aea26660'
COMPOSITE_LINKS = {'VCL-d432f73b8199bfec5a2b','VCL-e2f60e9be9e3224715e8'}
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


def repair_purpose_locator(link, claim):
    """Preserve the purpose connective as an exact original span, never prose editing."""
    if (link['viewpoint_claim_link_id'], link['claim_id'], link['revision'],
        link['pinned_claim_revision'], link['link_type'], link['effective_state']) != (
        PURPOSE_LINK, 'DK-61d92ac2a5fb-P03-CL003', 1, claim['revision'], 'equivalent_component', 'active'
    ) or claim['claim_id'] != link['claim_id']:
        raise ValueError('purpose link identity/revision drift')
    text = claim['statement']
    expected = '耶穌接受約翰的洗，不是因為祂需要悔改，而是為了盡諸般的義並與人類站在一起。'
    if text != expected or link['component_locator']['claim_sha256'] != semantic_record_sha(claim):
        raise ValueError('purpose Claim text/SHA drift')
    old = link['component_locator']['canonical_spans']
    pieces = ['耶穌接受約翰的洗', '與人類站在一起。']
    old_expected = [{'start_char':text.index(part),'end_char':text.index(part)+len(part),'exact_text':part} for part in pieces]
    if old != old_expected or link['component_locator']['statement_component'] != ''.join(pieces):
        raise ValueError('purpose old locator drift')
    pieces.insert(1, '為了')
    after = deepcopy(link)
    after.update(revision=2, component_locator={'claim_sha256':semantic_record_sha(claim),
        'statement_component':''.join(pieces), 'canonical_spans':[
            {'start_char':text.index(part),'end_char':text.index(part)+len(part),'exact_text':part} for part in pieces]})
    return after


def add_major_qualifier(link,claim):
    """Restore major-meaning qualifier without turning the denied meaning positive."""
    if (link['viewpoint_claim_link_id'],link['revision'],link['claim_id'],link['pinned_claim_revision']) != (
        'VCL-8457e53525f0eb1f35c3',1,claim['claim_id'],claim['revision']):
        raise ValueError('major qualifier identity drift')
    text=claim['statement']
    if text!='「義」主要不是指沒有罪，而是好關係以及促進好關係的行為。' or link['component_locator']['claim_sha256']!=semantic_record_sha(claim):
        raise ValueError('major qualifier text/SHA drift')
    parts=['「義」主要','是好關係以及促進好關係的行為。']
    after=deepcopy(link)
    after.update(revision=2,component_locator={'claim_sha256':semantic_record_sha(claim),'statement_component':''.join(parts),
        'canonical_spans':[{'start_char':text.index(part),'end_char':text.index(part)+len(part),'exact_text':part} for part in parts]})
    return after


def mark_proposed(link, action, amendment_sha):
    """Candidate metadata is never an active/approved Registry record."""
    prior_decision = link['decision_id']
    seed = {'link': link, 'action': action, 'amendment': amendment_sha}
    link['decision_id'] = 'VID-'+sha256_json(seed)[:20]
    link['review_status'] = 'candidate'
    if action != 'retire':
        link['effective_state'] = 'invalidated'
    return {'identity_decision_id': link['decision_id'], 'status': 'candidate_pending_exact_postimage_review',
            'action': action, 'claim_id': link['claim_id'], 'viewpoint_id': link['viewpoint_id'],
            'link_id': link['viewpoint_claim_link_id'], 'link_type': link['link_type'],
            'supersedes_decision_id':prior_decision if link['revision']>1 else None,
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
    negative.update(revision=2, component_locator={'claim_sha256':semantic_record_sha(claim),
        'statement_component':text,'canonical_spans':[{'start_char':start,'end_char':start+len(text),'exact_text':text}]})
    negative['occurrence_refs'] = [occurrence_ref(claim, negative['evidence_bindings'])]
    negative_head = states['canonical_viewpoints', negative['viewpoint_id']]
    if negative_head['current_revision_id'] != negative['validated_against_viewpoint_revision_id']:
        raise ValueError('negative viewpoint drift')
    adjacent_denial = deepcopy(states['viewpoint_claim_links', ADJACENT_DENIAL_LINK])
    if adjacent_denial['revision'] != 1 or adjacent_denial['link_type'] != 'qualifies' or adjacent_denial['effective_state'] != 'active':
        raise ValueError('adjacent denial revision drift')
    adjacent_denial.update(revision=2, effective_state='retired')
    purpose = repair_purpose_locator(states['viewpoint_claim_links', PURPOSE_LINK], states['claims','DK-61d92ac2a5fb-P03-CL003'])
    repentance = deepcopy(states['viewpoint_claim_links','VCL-3136719f25186ec0d844'])
    if repentance['revision']!=1 or repentance['link_type']!='qualifies' or repentance['effective_state']!='active':
        raise ValueError('repentance denial drift')
    repentance.update(revision=2,effective_state='retired')
    union_full = deepcopy(states['viewpoint_claim_links','VCL-2035576d71896837f065'])
    union_support = deepcopy(states['viewpoint_claim_links','VCL-c4aa850e9213ecefdd2f'])
    for link in [union_full,union_support]:
        own = states['claims',link['claim_id']]
        if link['revision']!=1 or link['effective_state']!='active' or link['component_locator']['statement_component']!=own['statement']:
            raise ValueError('whole-component union drift')
        link.update(revision=2,component_locator=None)
    union_full['link_type']='equivalent_full'
    # Narrower witnessed union supports, not equivalent to additional purpose /
    # recognition/redemption conditions. No new Claim or CVP is manufactured.
    union_support['link_type']='supports'
    john_support = deepcopy(states['viewpoint_claim_links','VCL-f53234e06cbb15219cb8'])
    john_claim = states['claims',john_support['claim_id']]
    if john_support['revision']!=1 or john_support['link_type']!='supports' or john_support['viewpoint_id']!=EXEGESIS:
        raise ValueError('John support drift')
    john_evidence = exact_evidence(states,john_claim,packet['sources'],[john_claim['claim_id'].replace('CL004','E'+s) for s in ['019','020','021']])
    john_support.update(revision=2,evidence_bindings=sorted(
        [{'evidence_step_id':e['step']['evidence_step_id'],'source_fragment_id':e['fragment']['fragment_id']} for e in john_evidence],
        key=lambda b:(b['evidence_step_id'],b['source_fragment_id'])))
    john_support['occurrence_refs']=[occurrence_ref(john_claim,john_support['evidence_bindings'])]
    purpose['link_type']='supports'
    union_component=deepcopy(states['viewpoint_claim_links','VCL-cab54eff0119ad55a8b2'])
    if union_component['revision']!=1 or union_component['link_type']!='equivalent_component':
        raise ValueError('adjacent union identity drift')
    union_component.update(revision=2,link_type='supports')
    major=add_major_qualifier(states['viewpoint_claim_links','VCL-8457e53525f0eb1f35c3'],states['claims','DK-b4223cee7ee7-P02-CL002'])
    composite_members=[]
    for lid in sorted(COMPOSITE_LINKS):
        link=deepcopy(states['viewpoint_claim_links',lid])
        if link['revision']!=1 or link['viewpoint_id']!=COMPOSITE_VIEWPOINT or link['link_type']!='equivalent_component':
            raise ValueError('composite source identity drift')
        link.update(revision=2,link_type='supports')
        composite_members.append(link)
    decision_plans = [mark_proposed(link, action, amendment['artifact_sha256']) for link, action in
                      [(first_after,'revalidate_with_type_change'),(retired,'retire'),(qualifies,'retire'),(support,'create'),
                       (negative,'revalidate'),(adjacent_denial,'retire'),(purpose,'revalidate_with_type_change'),(repentance,'retire'),
                       (union_full,'revalidate_with_type_change'),(union_support,'revalidate_with_type_change'),(john_support,'revalidate'),
                       (union_component,'revalidate_with_type_change'),(major,'revalidate')]
                      +[(link,'revalidate_with_type_change') for link in composite_members]]
    target_members = [p for (c, _), p in states.items() if c == 'viewpoint_claim_links' and p.get('viewpoint_id') == EXEGESIS and p.get('effective_state') == 'active']
    packet.update(schema_version='wkp357_cross_partition_support_review.v1', classification_amendment=amendment,
                  target_exegesis_revision=target_revision, target_exegesis_links=target_members,
                  target_exegesis_claims=[states['claims', cid] for cid in sorted({p['claim_id'] for p in target_members})],
                  adjacent_negative_revision=states['viewpoint_revisions', negative_head['current_revision_id']],
                  proposed_links=[first_after, retired, qualifies, support, negative, adjacent_denial, purpose,repentance,union_full,union_support,john_support,union_component,major]+composite_members, proposed_decisions=decision_plans,
                  composite_identity={'viewpoint_id':COMPOSITE_VIEWPOINT,'owner_direction':'应该是合在一起的，教授总是强调这个',
                    'core_and_signature_unchanged':True,'no_split':True,'joint_link_ids':sorted(COMPOSITE_LINKS),
                    'joint_claim_ids':sorted(link['claim_id'] for link in composite_members),
                    'identity_basis':'Joint source-grounded proposition, not each partial Claim asserted equivalent to the complete conjunction. Preserve original relationship/behaviour formulations and disclose promote/maintain ambiguity. Actual dual review must verify the conjunction is professed, not invented.',
                    'decision_requires_joint_candidate_and_both_support_members':True,
                    'does_not_approve_other_content_or_all_links':True},
                  excluded_support_evidence={'E001':'否定错误解释由独立的否定命题连接保留，不作为约翰施洗的支持依据。'},
                  occurrence_seed_contract='viewpoint_batch_changeset: claim_id, claim_revision_sha256, sorted selected evidence pairs',
                  authorization='Owner: 继续 after complete-g010 correction plan; 好，这个要改 for purpose locator; latest complaint authorizes completing existing repair rather than idle promises. Resolve all g010 links/dependencies within existing connection scope. No Claim/Evidence/source/CVP prose edits. Explicit apply requires actual dual pass, backup, dry-run, exact readback and audit; no production continuation.',
                  owner_source_ambiguity={'claim_id':'DK-61d92ac2a5fb-P03-CL002','link_id':'VCL-d432f73b8199bfec5a2b',
                    'direction':'User: 教授没有讲清促进、维持的区别；指出原文表述差异，不据此自行断定相同/不同，不作为单独返工理由。',
                    'not_a_new_professor_distinction':True,'semantic_equality_not_proven_by_this_note':True},
                  scope_resolution={'negative_link':'Whole Claim is theological, but this exact negative component is witnessed only as the Matthew3:15 question/answer. Preserve local equivalent_component with exact locator: no invented general-to-local reasoning and no orphan CVP. One full Claim partition remains salvation; component identity is not a second partition owner or new grouping assignment. Review this distinction rather than infer local component scope from the whole Claim routing label.',
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
    composite=packet.get('composite_identity')
    if composite:
        joint_links=[p for p in packet['proposed_links'] if p['viewpoint_claim_link_id'] in composite['joint_link_ids']]
        if (len(joint_links)!=2 or {p['claim_id'] for p in joint_links}!=set(composite['joint_claim_ids'])
            or any(p['viewpoint_id']!=composite['viewpoint_id'] or p['link_type']!='supports' for p in joint_links)
            or not composite['no_split'] or not composite['core_and_signature_unchanged']):
            raise ValueError('incomplete or altered composite identity basis')
    joint_id='VID-'+sha256_json({'joint':composite,'report':sha256_json(report)})[:20] if composite else None
    joint_created=False
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
        joint=bool(composite and link['viewpoint_claim_link_id'] in composite['joint_link_ids'])
        if joint:
            candidate.update(candidate_claim_ids=composite['joint_claim_ids'],generation_fingerprint=sha256_json(composite))
            identity.update(claims=candidate['candidate_claim_ids'],generation_fingerprint=candidate['generation_fingerprint'])
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
        if joint:
            decision.update(identity_decision_id=joint_id,
                claim_link_decisions=[{'claim_id':cid,'link_type':'supports'} for cid in composite['joint_claim_ids']],
                reason='Owner directed keeping one composite viewpoint. Exact joint source-grounded proposition independently reviewed; partial Claims support jointly, not individual full equivalence.')
            link['decision_id']=joint_id
        link.update(review_status='system_approved', effective_state=plan['intended_effective_state_after_review'])
        package['viewpoint_claim_links'].append(link)
        if not joint or not joint_created:
            package['viewpoint_identity_candidates'].append(candidate)
            package['viewpoint_identity_decisions'].append(decision)
        if joint: joint_created=True
    return package


def verify_review_artifacts(packet, report, root):
    """Bind approval to the actual retained responses; no label-only approvals."""
    expected = []
    for label, backend, model, review in zip(['gpt-review','opus-review'],['codex','claude'],
                                            ['gpt-6.1-sol','claude-opus-5-5'],report['reviews']):
        raw = read_artifact(root/('raw-'+label+'.json'))
        body = {k:v for k,v in raw.items() if k!='artifact_sha256'}
        if (raw['artifact_sha256']!=sha256_json(body) or raw['request_payload_sha256']!=sha256_json(packet)
            or raw['model_id']!=model or raw['backend']!=backend
            or raw['response_sha256']!=sha256_json(raw['response']) or raw['response']!=review):
            raise ValueError('raw independent review proof drift')
        expected.append(raw['artifact_sha256'])
    if expected!=report['accepted_call_artifact_shas']:
        raise ValueError('accepted independent review identity drift')


def apply_reviewed_correction(store, packet, report, before, package, root):
    """Fresh backup and exact whole-Registry comparison; never regenerate models."""
    import re
    from backend.pipeline.viewpoint_cvp_recovery_executor import binding
    verify_review_artifacts(packet,report,root)
    if (root/'apply-intent.json').exists() or (root/'apply-receipt.json').exists():
        raise ValueError('existing apply attempt; no replay')
    label='before-g010-closure-'+sha256_json(str(root))[:24]
    backup_run=subprocess.run(['scripts/backup-registry.sh',label],cwd=PROJECT,check=True,capture_output=True,text=True)
    match=re.search(r'backup-registry: wrote (.+\.dump) \(',backup_run.stdout)
    if not match: raise ValueError('backup not reported')
    backup=Path(match.group(1)).resolve()
    catalog=subprocess.run(['pg_restore','--list',str(backup)],check=True,capture_output=True,text=True)
    if not catalog.stdout.strip(): raise ValueError('empty backup catalog')
    write_immutable(root/'backup.json',binding(backup))
    with exclusive_cvp_run_lock({'global_lock_path':str(BASE/'registry-cvp.lock')}):
        live=snapshot(store)
        if fingerprint(live,before['collections'])!=before['pre_fingerprint_sha256']:
            raise ValueError('Registry drift before apply')
        for source in packet['sources']:
            if file_sha256(Path(source['path']))!=source['file_sha256']:
                raise ValueError('physical source drift before apply')
        verify_review_artifacts(packet,report,root)
        if approved_package(live,packet,report,root)!=package:
            raise ValueError('reviewed package reconstruction drift')
        replay=store.plan_package(package,source_kind='wkp357_cross_partition_support_correction')
        if json.loads(json.dumps(replay.as_dict()))!=read_artifact(root/'dry-run-plan.json'):
            raise ValueError('dry-run/apply mismatch')
        if binding(backup)!=read_artifact(root/'backup.json'):
            raise ValueError('backup SHA drift')
        intent={'review_report_sha256':sha256_json(report),'packet_sha256':sha256_json(packet),
                'backup':binding(backup),'allowed_collections':sorted(k for k in package if k!='package_id')}
        write_immutable(root/'apply-intent.json',intent)
        expected_heads={p['viewpoint_id']:p['validated_against_viewpoint_revision_id'] for p in package['viewpoint_claim_links']}
        receipt=store.apply_plan(replay,metadata={'reviewed_g010_closure_correction':intent},
            expected_registry_fingerprint_sha256=before['pre_fingerprint_sha256'],registry_collections=before['collections'],
            expected_current_viewpoint_revisions=expected_heads)
        write_immutable(root/'apply-receipt.json',receipt)
        expected=deepcopy(live)
        for op in replay.operations: expected[op.collection,op.object_id]=stored_operation_payload(op)
        post=snapshot(store)
        if set(post)!=set(expected) or fingerprint(post,before['collections'])!=fingerprint(expected,before['collections']):
            raise ValueError('complete exact Registry readback mismatch; applied receipt retained')
        for source in packet['sources']:
            if file_sha256(Path(source['path']))!=source['file_sha256']:
                raise ValueError('physical source drift after apply')
        result={'status':'applied_readback_verified','change_set_id':replay.change_set_id,
                'database_mutations':len(replay.operations),'claim_source_cvp_text_mutations':0,'production_resumed':False}
        write_immutable(root/'verified-result.json',result)
    return result


REVIEW_PROMPT = '''核对完整来源和所有实际拟修订连接，每个targets的Claim各一个verdict。
第一条改为完整Claim等价，保留受洗/连合/代负罪债条件。第二条撤销与一般义定义的错误等价，另建完整神学Claim supports 具体“约翰施洗促进自己与神好关系”的释经CVP；不是一般义CVP的supports。
读取目标释经CVP、来源原文、现有所有相邻连接和关系。原文是否确实把该神学前提用于解释约翰施洗？支持须保留具体行动限定，不得仅凭主题相似。检查仍保留的相邻qualifies等关系是否也需修订；若有问题必须指出，不为续跑默许。
本轮一并撤销错误相邻qualifies，补上原Claim自身E003/E006的具体施洗证据，否定词义连接保留原文顿号而非拼成“审判灭罪”。请核对补充的否定CVP正文/signature，以及每项proposed_decisions撤销或重新验证依据。
所有拟修订记录当前都是candidate，非retired记录invalidated，不伪装已批准/active。intended_effective_state_after_review仅在真实双模型审核、确定性校验、备份、ChangeSet、回读后才可生效。occurrence是整组选中证据的canonical batch seed，不是一条Evidence必有一个OCC。
Claim、Evidence、来源、CVP正文不改；只改明确列出的十五条连接。VCL-d6f273ad93fd0d88cb00补上「為了」原span并改supports；相邻cab54同类窄连合也改supports。8457补「義」主要原span及肯定部分，不把否定变肯定。两条否定别种解释的qualifies撤销。CL006整句等价改equivalent_full；CL007仅明确连合，缺更广认同/救赎目的，以完整Claim supports而不是硬补等价。John supports补自己E019/E020并保留E021，不能用耶稣关系冒充约翰关系。
用户已明确裁定CV-d036的关系本身和行为应保持同一个复合观点，教授总强调这个；不拆CVP，不改正文/signature。两条部分来源改为共同支持，使用一个联合身份candidate/decision绑定两Claim与当前CVP及精确来源，不虚称任一局部Claim等同整个合取命题。实际审核仍须检查联合完整主张确实出自教授，不凭人工指令发明来源；保持促进/维持原措辞差异披露。新decision实际双模型批准才能生效，不伪装用户审批全部连接。
否定成分撤回前一轮错误supports方案：全Claim归神学不能推出它所有成分都非局部释经。这里的否定原文只在太3:15问答出现，其精确成分与现有局部否定CVP保留equivalent_component。一个Claim只有一个partition，成分的既有身份连接不是第二次分组，不产生第二个owner。不虚构一般到局部推理，不掏空CVP；审核必须判断该成分的原文范围，不因路由标签推翻实际语义。
owner_source_ambiguity记录用户对促进/维持的明确方向：教授未讲清区别，保留原文差异并披露不明确，不替他制造区别。不借该说明断言新的严格等价。若除此之外有真实身份问题仍指出。分类投影消费/未来神学CVP属于后续执行衔接；本次只检查精确连接后像，不声称已闭合生产或允许重分组。新建create与既有类型变更revalidate_with_type_change区分；supersedes_decision_id明确旧实际决定，先前未写候选仅是历史提案。
必须检查group_closure内全部11个CVP的现有成员、当前正文/signature、相邻连接和依赖。每个targets Claim各一verdict；checked_link_ids与checked_dependency_ids必须完整且不重复列出实际检查的required IDs。发现任何未修的真实连接、范围或结构问题放入closure_problems，不能默许下一组。唯一partition是Claim的分类，不代表支持关系不能跨区。未来神学身份仅pending，不能当作已生成或已审核。review不授权写库；approved只有无任何problems才true。'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--amendment', type=Path, required=True)
    parser.add_argument('--review', action='store_true')
    parser.add_argument('--prior-review-root', type=Path)
    parser.add_argument('--apply-and-audit',action='store_true')
    args = parser.parse_args()
    if args.apply_and_audit and not args.review:
        raise ValueError('apply requires actual formal review in this run')
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
                    if len(plan.operations) != 43 or any(op.collection not in package for op in plan.operations):
                        raise ValueError('unexpected correction operation scope')
                    write_immutable(root/'effective-package.json', package)
                    write_immutable(root/'dry-run-plan.json', json.loads(json.dumps(plan.as_dict())))
                    result.update(status='reviewed_dry_run_pass_not_applied', planned_operations=len(plan.operations))
            write_derived(root/'progress.json', result)
            if approved and args.apply_and_audit:
                result=apply_reviewed_correction(store,packet,report,before,package,root)
                write_derived(root/'progress.json',result|{'status':'independent_audit'})
                from backend.pipeline.viewpoint_cvp_recovery_executor import run_logged, load_campaign, verify_group_audit
                cfg,schedule,upstream,groups=load_campaign(CAMPAIGN)
                state=read_artifact(CAMPAIGN/'progress.json')
                audit_dir=root/'independent-audit'
                run_logged([str(PROJECT/'backend/.venv/bin/python'),'scripts/audit-library.py','--scope','all','--layers','1,2,3,4',
                    '--claim-ids',','.join(groups[state['group_key']]['claim_ids']),
                    '--viewpoint-ids',','.join(packet['group_closure']['viewpoint_ids']),
                    '--workers','2','--model','gemini-3.8-flash','--out',str(audit_dir),'--no-status-file'],root,'independent-audit')
                proof=verify_group_audit(CAMPAIGN,cfg,schedule,upstream,groups,audit_dir/'audit.json',root/'verified-group')
                result.update(status='applied_readback_independent_audit_pass',completion_proof=str(root/'verified-group/completion.json'))
                write_derived(root/'progress.json',result)
        print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        import sys
        if '--output-root' in sys.argv:
            candidate=Path(sys.argv[sys.argv.index('--output-root')+1]).resolve()
            if candidate.parent==BASE and (candidate/'packet.json').exists():
                write_derived(candidate/'failure.json',{'error':f'{type(exc).__name__}: {exc}',
                    'apply_receipt_exists':(candidate/'apply-receipt.json').exists(),
                    'apply_intent_exists':(candidate/'apply-intent.json').exists(),
                    'automatic_retry':False,'production_resumed':False})
                write_derived(candidate/'progress.json',{'status':'stopped','error':str(exc),
                    'writes_require_receipt_inspection':(candidate/'apply-intent.json').exists(),
                    'production_resumed':False})
        raise
