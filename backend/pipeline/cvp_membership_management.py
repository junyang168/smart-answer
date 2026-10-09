"""Routine membership revalidation intake; never a semantic approval.

The old Claim action cannot repair a different domain by pretending unresolved
means retain. Preserve that action/budget and hand its exact finding to CVP
management. Membership proposals still need an independent review and the
shared SQL executor; this intake grants no write or retry permission.
"""
from backend.pipeline import source_reconciliation as c
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from copy import deepcopy
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

VERSION = 'wang_cvp_membership_management_v1'
PACKAGE_SCHEMA = 'wang_cvp_membership_sql_v1'
POLICY = Path(__file__).with_name('policies')/'wang_cvp_membership_management_v1.json'


class Member(BaseModel):
    model_config = ConfigDict(extra='forbid')
    viewpoint_id: str
    claim_id: str
    statement_component: str = Field(min_length=1)
    evidence_bindings: list[dict[str, str]] = Field(min_length=1)
    reason: str = Field(min_length=1)


class MembershipProposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    decision: Literal['propose_members', 'unresolved']
    members: list[Member]
    reason: str = Field(min_length=1)


PROPOSAL_PROMPT = '''常规 CVP current membership revalidation，不是重审原失败 Claim 提案。
读完整当前 CVP/CVR、所有当前成员/支持、退役历史 receipt，以及每个候选 Claim 的完整自身正文和实际可用 Evidence。
只在同核心、完整 signature/scope/归属/模态不变时，为每个目标提出一个真实同身份的 current Claim 成分成员。
statement_component 必须是候选 Claim statement 内唯一、连续、逐字成分，充分表达当前 CVP；自身正文可解释口语省略，不能替摘要补另一个独立结论。
只选 material.candidates 中的 Claim 和其精确 evidence_bindings。支持论据不等于等价，不把 current supports 升为 equivalent，不复活退役 Claim/VIC，不改 Claim/Evidence/source/CVP/原组成员。
若没有合法同核心成员，decision=unresolved，members=[]，说明原文的实质缺口或差异；不为了续跑强造成员，不判断教授神学对错。
必要时一次纠正，之后不能再纠正。只输出 schema 的完整 JSON，不回填 SHA。'''
REVIEW_PROMPT = '''独立审核常规 CVP 成员重验的完整后像，不把原拒绝改为通过。
逐字核查所有目标完整 CVR/core/signature/scope、所有当前成员、候选完整自身正文与 evidence、原退役状态。
new_links 展示每条新 equivalent_component 的精确 Claim/版本/成分/自身证据；同时完整核查新 VIC/VID 的 provenance_contract。
只允许新增该同核心成员和本轮 fresh identity provenance；所有现有 supports、Claim/Evidence/source/CVP/core/signature/scope/关系必须不变。
每个 member.claim_id 一个 verdict，任一不成立 approved=false 并说明实质差异；不能只同主题或局部支持。全部成立才批准，不为续跑批准。'''


def model_adapter(stage):
    from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
    from backend.pipeline.viewpoint_authorized_source_repair_357 import Review
    from backend.pipeline.viewpoint_resolution_runtime import subscription_client
    from backend.pipeline.viewpoint_semantic_contract import semantic_prompt
    reviewer = stage in {'initial_review', 'final_review'}
    obj = StructuredJsonReviewerAdapter(
        client=subscription_client('claude' if reviewer else 'codex',
            'claude-opus-5-5' if reviewer else 'gpt-6.1-sol', 'high'),
        prompt=semantic_prompt(REVIEW_PROMPT if reviewer else PROPOSAL_PROMPT),
        response_model=Review if reviewer else MembershipProposal,
        schema_name=PACKAGE_SCHEMA, compact_payload=True, shared_text_payload=True, shared_record_payload=True)
    obj.max_request_bytes = 500000
    return obj


def policy_binding():
    from backend.pipeline.cvp_sql_flow import binding
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    policy = read_artifact(POLICY)
    expected = {'schema_version': VERSION, 'human_content_approval': False,
        'automatic_member_revalidation': True, 'preserve_core_signature_and_scope': True,
        'preserve_claims_evidence_sources': True, 'preserve_campaign_members_and_order': True,
        'revive_retired_members': False, 'supports_is_equivalent_without_review': False,
        'proposal_model': 'gpt-6.1-sol', 'independent_review_model': 'claude-opus-5-5',
        'correction_limit': 1, 'unknown_call_retry': False}
    if any(policy.get(k) != v for k,v in expected.items()) or not policy.get('authorization_quote'):
        raise ValueError('routine membership policy drift')
    return binding(POLICY)


def prepare_material(store, frozen, handoff):
    """Indexed exact current original readset; no future-group admission."""
    from backend.pipeline.cvp_sql_flow import current_claim_authority, decoded_pins
    from backend.pipeline.viewpoint_evidence_source_context import attach_source_context, load_context_source
    from backend.api.canonical_repository.viewpoint_resolution import compile_review_claim
    from backend.api.canonical_repository.knowledge_models import ClaimRecord, EvidenceStepRecord, SourceFragmentRecord
    from backend.api.canonical_repository.models import Citation
    import time
    started=time.perf_counter()
    c.guard_files(frozen)
    with store.connect() as conn, conn.cursor() as cursor:
        store._assert_transaction_read_pins(cursor, decoded_pins(frozen['sql_scope']['read_pins']))
    states = c.index(frozen['before_records'])
    retired = {c.key(p['collection'],p['object_id']) for p in frozen['sql_scope']['read_pins'] if p['pin'][2]}
    evidence = {c.split_key(k)[1]:EvidenceStepRecord.model_validate(v) for k,v in states.items()
                if k.startswith('evidence_steps/') and k not in retired}
    fragments = {c.split_key(k)[1]:SourceFragmentRecord.model_validate(v) for k,v in states.items()
                 if k.startswith('source_fragments/') and k not in retired}
    candidates = []; sources = {}; excluded = []
    # Only Claims already supplied as complete original/forward context.
    permitted_sources = {frozen['manifest']['source_id']}
    permitted_sources.update(s['source_id'] for s in frozen.get('context_sources', []))
    for key, payload in sorted(states.items()):
        if not key.startswith('claims/') or key in retired:
            continue
        claim = ClaimRecord.model_validate(payload)
        own = set()
        for eid in claim.evidence_step_ids:
            if eid in evidence:
                e = evidence[eid]
                own.update(fragments[f].source_id for f in set(e.source_fragment_ids or []) |
                           ({e.source_fragment_id} if e.source_fragment_id else set()) if f in fragments)
        if len(own) != 1 or not own <= permitted_sources:
            continue
        sid = next(iter(own)); doc = states[c.key('source_documents',sid)]
        current = store.get_record_state('claims',claim.claim_id)
        try:
            authority = current_claim_authority(store, claim.claim_id, current)
        except ValueError as exc:
            excluded.append({'claim_id':claim.claim_id,'reason':str(exc)})
            continue  # An unqualified read-only candidate is not a writable member.
        compiled, findings = compile_review_claim(claim=claim,evidence_index=evidence,fragment_index=fragments,
            citation_index={c.split_key(k)[1]:Citation.model_validate(v) for k,v in states.items() if k.startswith('citations/')},
            coverage_source_sha256={sid:doc['source_body_sha256']},attestation_sha=sha256_json(authority))
        if compiled is None or findings:
            continue  # Not qualified for membership; never upgrade Evidence here.
        loaded = load_context_source(doc)
        sources[sid] = {'path':str(loaded[0]),'file_sha256':loaded[1]}
        candidates.append({'claim':payload, 'projection':attach_source_context(compiled,doc).model_dump(mode='json'),
                           'authority':authority})
    if not candidates:
        raise ValueError('membership has no complete qualified current Claim/source candidate')
    material={'schema_version':VERSION, 'policy':policy_binding(), 'handoff':handoff,
            'source_materials_sha256':sha256_json(frozen), 'run_id':frozen['sql_scope']['run_id'],
            'before_records':frozen['before_records'], 'read_pins':frozen['sql_scope']['read_pins'],
            'candidates':candidates, 'excluded_candidates':excluded, 'sources':list(sources.values())}
    from backend.api.canonical_repository.postgres_store import canonical_json
    if time.perf_counter()-started>5 or len(canonical_json(material).encode())>64*1024**2:
        raise ValueError('membership preparation exceeds shared mechanical budget; no whole-history fallback')
    return material


def new_links(material, response):
    from backend.api.canonical_repository.knowledge_models import ViewpointClaimLinkRecord
    from backend.api.canonical_repository.postgres_store import record_content_sha
    from backend.pipeline.source_reconciliation_runner import Stop
    proposal = MembershipProposal.model_validate(response)
    targets = {t['viewpoint_id']:t for t in material['handoff']['targets']}
    if proposal.decision != 'propose_members':
        raise Stop('routine membership has substantive unresolved source/identity; no retry')
    if len(proposal.members) != len(targets) or {m.viewpoint_id for m in proposal.members} != set(targets):
        raise ValueError('membership must cover exactly the orphaned target identities')
    states = c.index(material['before_records']); result = []
    candidates = {p['claim']['claim_id']:p for p in material['candidates']}
    for member in proposal.members:
        if member.claim_id not in candidates:
            raise ValueError('foreign/unqualified membership Claim')
        candidate = candidates[member.claim_id]; claim = candidate['claim']; text = claim['statement']
        if text.count(member.statement_component) != 1:
            raise ValueError('membership requires a unique exact nonempty statement component')
        if any(k.startswith('viewpoint_claim_links/') and p['claim_id']==member.claim_id
               and p['viewpoint_id']==member.viewpoint_id and p.get('effective_state','active')=='active'
               for k,p in states.items()):
            raise ValueError('cannot relabel an existing support or duplicate membership')
        # Pair qualification comes from the complete source-validated projection.
        available = {(e['evidence_step_id'],e['source_fragment_id']) for e in candidate['projection']['evidence']
                     if e.get('valid_for_identity_review') is True
                     and e.get('support_eligibility') in {'eligible','eligible_with_label','eligible_candidate'}}
        pairs = [(b.get('evidence_step_id'),b.get('source_fragment_id')) for b in member.evidence_bindings]
        if (any(set(b) != {'evidence_step_id','source_fragment_id'} for b in member.evidence_bindings)
                or len(pairs)!=len(set(pairs)) or not set(pairs)<=available):
            raise ValueError('membership lacks exact qualified own Evidence/fragment pair')
        start = text.index(member.statement_component)
        locator = {'statement_component':member.statement_component,'claim_sha256':record_content_sha(claim),
                   'canonical_spans':[{'start_char':start,'end_char':start+len(member.statement_component),'exact_text':member.statement_component}]}
        seed = {'contract':PACKAGE_SCHEMA, 'viewpoint':member.viewpoint_id,
                'revision':targets[member.viewpoint_id]['viewpoint_revision_id'], 'claim':member.claim_id,
                'claim_sha256':record_content_sha(claim), 'locator':locator}
        result.append(ViewpointClaimLinkRecord(viewpoint_claim_link_id='VCL-'+sha256_json(seed)[:20],
            viewpoint_id=member.viewpoint_id,validated_against_viewpoint_revision_id=seed['revision'],
            claim_id=member.claim_id,pinned_claim_revision=claim['revision'],link_type='equivalent_component',
            component_locator=locator,evidence_bindings=[{'evidence_step_id':a,'source_fragment_id':b} for a,b in sorted(pairs)],
            occurrence_refs=[],decision_id='VID-pending-independent-membership-review',review_status='system_approved').model_dump(mode='json'))
    return result


def review_packet(material, response):
    return {'schema_version':PACKAGE_SCHEMA,'materials':material,'proposal':response,
            'new_links':new_links(material,response), 'provenance_contract':{
                'fresh_candidate_and_decision_per_link':True,'pending_decision_is_display_only':True,
                'reviewed_new_records':['viewpoint_claim_links','viewpoint_identity_candidates','viewpoint_identity_decisions'],
                'existing_record_changes':0,'original_rejection_preserved':True}}


def approved(packet, raw):
    from backend.pipeline.viewpoint_authorized_source_repair_357 import Review
    verdicts = Review.model_validate(raw['response']).verdicts
    required = [m['claim_id'] for m in packet['proposal']['members']]
    actual = [v.claim_id for v in verdicts]
    if len(actual)!=len(set(actual)) or set(actual)!=set(required):
        raise ValueError('membership independent review omitted/duplicated a complete member')
    return all(v.approved and not v.problems for v in verdicts)


def compile_package(material, proposal, review, root):
    from backend.api.canonical_repository.knowledge_models import ViewpointIdentityCandidateRecord, ViewpointIdentityDecisionRecord
    packet = review_packet(material,proposal['response'])
    if not approved(packet,review):
        raise ValueError('membership rejection cannot authorize a package')
    states = c.index(material['before_records']); links = packet['new_links']; candidates=[]; decisions=[]
    for link, member in zip(links,proposal['response']['members'],strict=True):
        seed = sha256_json({'contract':PACKAGE_SCHEMA,'material':sha256_json(material),
            'proposal':proposal['artifact_sha256'],'review':review['artifact_sha256'],'link':link})
        candidate = ViewpointIdentityCandidateRecord(identity_candidate_id='VIC-'+seed[:20],
            candidate_claim_ids=[link['claim_id']],candidate_viewpoint_ids=[link['viewpoint_id']],proposed_action='match_existing',
            proposed_proposition_signature=states[c.key('viewpoint_revisions',link['validated_against_viewpoint_revision_id'])]['proposition_signature'],
            scope_manifest_sha256=sha256_json(material),generation_fingerprint=seed).model_dump(mode='json')
        # Candidate IDs follow the same foundation identity compiler as all other domains.
        identity={'claims':candidate['candidate_claim_ids'],'viewpoints':candidate['candidate_viewpoint_ids'],
                  'relations':[],'action':'match_existing','blockers':[],
                  'coverage_snapshot_id':None,'scope_manifest_sha256':candidate['scope_manifest_sha256'],
                  'generation_fingerprint':seed}
        candidate['identity_candidate_id']='VIC-'+sha256_json(identity)[:20]
        decision = ViewpointIdentityDecisionRecord(identity_decision_id='VID-'+seed[:20],
            identity_candidate_id=candidate['identity_candidate_id'],decision='match_existing',resolved_viewpoint_id=link['viewpoint_id'],
            claim_link_decisions=[{'claim_id':link['claim_id'],'link_type':'equivalent_component'}],reviewer_kind='system',
            reviewer_id=PACKAGE_SCHEMA,approval_basis='dual_model_consensus',reason=member['reason'],input_sha256=seed,
            review_artifact_sha256=review['artifact_sha256'],policy_version=VERSION,
            reviewer_model_ids=['claude-opus-5-5','gpt-6.1-sol'],
            semantic_call_artifact_sha256s=sorted([proposal['artifact_sha256'],review['artifact_sha256']]),
            created_at=review['completed_at'],review_status='system_approved').model_dump(mode='json')
        link['decision_id']=decision['identity_decision_id']; candidates.append(candidate); decisions.append(decision)
    return {'package_id':'CVP-MEMBERSHIP-'+sha256_json({'material':material,'review':review['artifact_sha256']})[:24],
            'viewpoint_claim_links':links,'viewpoint_identity_candidates':candidates,'viewpoint_identity_decisions':decisions,
            'source_repair_application':{'schema_version':PACKAGE_SCHEMA,'run_root':str(Path(root).resolve()),
                'materials_sha256':sha256_json(material),'terminal_stage':review['stage']}}


def saved_raw(root, stage, packet):
    from datetime import datetime
    import json
    from backend.api.canonical_repository.postgres_store import canonical_json
    from backend.pipeline.source_reconciliation_runner import read, body, stage_adapter, request_limit
    from backend.pipeline.viewpoint_resolution_runtime import bound_model_response
    packet=json.loads(canonical_json(packet))
    raw=read(root/(stage+'.raw.json')); adapter=stage_adapter(model_adapter(stage),packet,root/(stage+'.raw.json'),saved=raw)
    proof=adapter.request_encoding_proof(packet)
    expected={'model_id':adapter.model_id,'backend':adapter.backend,'prompt_sha256':adapter.prompt_sha256,
        'generation_config_sha256':adapter.generation_config_sha256,'request_payload_sha256':sha256_json(packet),
        'request_encoding_proof':proof,'request_bytes':adapter.request_bytes(packet),'stage':stage,'automatic_retry':False}
    limit,budget=request_limit(adapter,root/(stage+'.raw.json'),saved=raw)
    if budget is not None:expected['request_budget']=budget
    if (expected!=body(read(root/(stage+'.reservation.json'))) or any(raw.get(k)!=v for k,v in expected.items())
            or expected['request_bytes']>limit or not proof.get('exact_roundtrip_verified')
            or proof['semantic_payload_sha256']!=sha256_json(packet) or proof['restored_payload_sha256']!=sha256_json(packet)
            or raw['response_sha256']!=sha256_json(raw['response']) or raw.get('provider_raw_sha256')!=sha256_json(raw.get('provider_raw'))
            or datetime.fromisoformat(raw['completed_at']).utcoffset() is None):
        raise ValueError('membership actual raw/request/once/encoding drift')
    identity=raw.get('runtime_identity')
    if identity is not None and (identity.get('model_id')!=adapter.model_id or identity.get('backend')!=adapter.backend
            or identity.get('reasoning_effort')!='high' or not identity.get('file_sha256')):
        raise ValueError('membership actual subscription identity drift')
    bound_model_response(adapter,packet,raw['response'],root/(stage+'.raw.json'),persist=False)
    return raw


def validate_package(package):
    from backend.pipeline.source_reconciliation_runner import read,body
    from backend.pipeline.cvp_sql_flow import check_binding
    app=package['source_repair_application']; root=Path(app['run_root']); material=body(read(root/'materials.json'))
    if app['schema_version']!=PACKAGE_SCHEMA or not root.is_absolute() or sha256_json(material)!=app['materials_sha256']:
        raise ValueError('membership authority/material drift')
    check_binding(material['policy'])
    if material['policy']!=policy_binding():raise ValueError('membership policy substitution')
    for source in material['sources']:check_binding(source)
    first=saved_raw(root,'proposal',material); packet=review_packet(material,first['response'])
    initial=saved_raw(root,'initial_review',packet)
    proposal=first; terminal=initial
    if app['terminal_stage']=='final_review':
        if approved(packet,initial):raise ValueError('membership correction without initial disagreement')
        correction_packet={**material,'initial_proposal':first['response'],'Opus_review':initial['response'],
                           'correction_round':1,'another_correction_allowed':False}
        proposal=saved_raw(root,'correction',correction_packet)
        terminal=saved_raw(root,'final_review',review_packet(material,proposal['response']))
    elif app['terminal_stage']!='initial_review':raise ValueError('membership nonterminal approval')
    if package!=compile_package(material,proposal,terminal,root):
        raise ValueError('membership differs from exactly independently approved records')


def execute(stages, frozen, handoff):
    """New bounded management domain; original correction is never replayed."""
    from backend.pipeline.source_reconciliation_runner import Runner,read,write,body,Stop
    from backend.pipeline.cvp_sql_flow import binding,check_binding,decoded_pins
    from backend.pipeline.source_reconciliation_runtime import ProductionPorts
    store=stages.store
    stable={'kind':VERSION,'targets':[(t['viewpoint_id'],t['viewpoint_revision_id']) for t in handoff['targets']]}
    action_id='CVPA-'+sha256_json(stable); root=stages.root.parent/'membership'/action_id
    run_id=frozen['sql_scope']['run_id']; store.bind_cvp_execution_root(run_id,identity=action_id,root=root)
    store.reserve_cvp_membership_identity(identity=sha256_json(stable),parent_run_id=run_id,
                                          root=root,handoff_id=handoff['handoff_id'])
    previous=store.get_cvp_action(run_id,action_id)
    material=previous['materials'] if previous else prepare_material(store,frozen,handoff)
    if material['handoff']!=handoff or material['source_materials_sha256']!=sha256_json(frozen):
        raise ValueError('same membership identity cannot change parent/root or reset budgets')
    store.prepare_cvp_action(run_id,action_id=action_id,kind='cvp_membership_revalidation',
                             authorization=material['policy'],materials=material)
    write(root/'materials.json',material)
    def guard():
        c.guard_files(frozen)
        for source in material['sources']:check_binding(source)
        with store.connect() as conn,conn.cursor() as cursor:
            store._assert_transaction_read_pins(cursor,decoded_pins(material['read_pins']))
    class Ports(ProductionPorts):
        def adapter(self,stage):return model_adapter(stage)
    ports=Ports() if isinstance(stages.ports,ProductionPorts) else stages.ports
    runner=Runner(root,ports);runner.root=root
    guard();proposal=runner.call('proposal',material)
    packet=review_packet(material,proposal['response']);guard();review=runner.call('initial_review',packet)
    if not approved(packet,review):
        correction_packet={**material,'initial_proposal':proposal['response'],'Opus_review':review['response'],
                           'correction_round':1,'another_correction_allowed':False}
        guard();proposal=runner.call('correction',correction_packet)
        packet=review_packet(material,proposal['response']);guard();review=runner.call('final_review',packet)
        if not approved(packet,review):raise Stop('membership terminal substantive disagreement; no second correction')
    guard();package=compile_package(material,proposal,review,root);validate_package(package)
    write(root/'approved-package.json',package)
    return {'schema_version':VERSION,'handoff_id':handoff['handoff_id'],'package':binding(root/'approved-package.json'),
            'management_action_id':action_id,'status':'independently_reviewed_pending_joint_transaction',
            'old_unresolved_is_not_pass':True,'original_correction_budget_exhausted':True}


def resolved_proposal(root, frozen, proposal, correction, *, receipt=None):
    """Explicit domain resolution, not a new model verdict or silent retain."""
    from backend.pipeline.source_reconciliation_runner import read,body
    from backend.pipeline.cvp_sql_flow import check_binding
    handoff=retirement_handoff(frozen,proposal,correction)
    if handoff is None:raise ValueError('membership resolution has no exact original retirement handoff')
    receipt=receipt or body(read(Path(root)/'membership-resolution.json'))
    if (receipt['schema_version']!=VERSION or receipt['handoff_id']!=handoff['handoff_id']
            or receipt['status']!='independently_reviewed_pending_joint_transaction'
            or receipt['old_unresolved_is_not_pass'] is not True or receipt['original_correction_budget_exhausted'] is not True):
        raise ValueError('membership resolution receipt drift')
    check_binding(receipt['package']); package=body(read(Path(receipt['package']['path'])));validate_package(package)
    material=body(read(Path(package['source_repair_application']['run_root'])/'materials.json'))
    if (material['handoff']!=handoff or material['source_materials_sha256']!=sha256_json(frozen)
            or material.get('before_records')!=frozen['before_records']
            or material.get('read_pins')!=frozen['sql_scope']['read_pins']):
        raise ValueError('foreign membership resolution cannot clear unresolved')
    result=proposal.model_dump()
    resolved_keys=set()
    for d in result['dispositions']:
        if d['action']=='unresolved':
            resolved_keys.add(d['record_key'])
            d.update(action='retain',reason='Program domain resolution: unchanged core/CVR with independently reviewed new current member; '
                'pending the same joint SQL transaction. Original GPT unresolved retained. Proof '+receipt['package']['file_sha256'])
    for impact in result.get('cvp_impacts',[]):
        if impact['record_key'] in resolved_keys:
            if impact['action']!='needs_direction':raise ValueError('membership cannot replace a different original impact')
            impact.update(action='retain',reason='Program membership resolution, independently reviewed pending joint transaction: '+receipt['package']['file_sha256'])
    return type(proposal).model_validate(result), package, receipt


def joint_review_packet(root, frozen, proposal, correction):
    from backend.pipeline.source_review_input_repair import add_receipt
    resolved,package,receipt=resolved_proposal(root,frozen,proposal,correction)
    packet=add_receipt(frozen,correction['response'],c.review_packet(frozen,resolved))
    packet['membership_resolution']=receipt
    packet['management_postimages']={k:v for k,v in package.items() if k not in {'package_id','source_repair_application'}}
    packet['joint_transaction_contract']='Review the complete joint postimage, including the independently reviewed new membership; '
    packet['joint_transaction_contract']+='if any current CVP/core/identity or original update remains unresolved, reject. No old rejection was changed into pass.'
    return resolved,packet


def append_joint_package(package, root, frozen, proposal, correction):
    _,membership,_=resolved_proposal(root,frozen,proposal,correction)
    result=deepcopy(package)
    for col in ('viewpoint_claim_links','viewpoint_identity_candidates','viewpoint_identity_decisions'):
        result.setdefault(col,[]).extend(membership[col])
    return result


def retirement_handoff(frozen, proposal, correction):
    if not frozen.get('claim_issue_context', {}).get('scope_amendment'):
        return None
    unresolved = {d.record_key for d in proposal.dispositions if d.action == 'unresolved'}
    if not unresolved or any(c.split_key(k)[0] not in
            {'canonical_viewpoints', 'viewpoint_revisions'} for k in unresolved):
        return None
    # This is a domain transfer, not permission to bypass missing graph/source,
    # an unknown consumer, or any original proposal validator.
    if not frozen.get('claim_issue_context', {}).get('scope_amendment'):
        return None
    c.validate_proposal(frozen, proposal, review_only=True)
    states = c.index(frozen['before_records'])
    retired = {(p['collection'], p['object_id']) for p in
               frozen.get('sql_scope', {}).get('read_pins', []) if p['pin'][2] is True}
    targets = []
    covered = set()
    for k in sorted(unresolved):
        if not k.startswith('canonical_viewpoints/'):
            continue
        head = states[k]
        rk = c.key('viewpoint_revisions', head['current_revision_id'])
        revision = states.get(rk)
        candidate = head.get('created_from_candidate_id')
        if (rk not in unresolved or revision is None or revision['viewpoint_id'] != head['viewpoint_id']
                or head.get('identity_status') not in {'active', 'provisional'}
                or head.get('review_status') != 'system_approved'
                or revision.get('review_status') != 'system_approved'
                or not candidate or ('viewpoint_identity_candidates', candidate) not in retired):
            return None
        links = [p for key,p in states.items() if key.startswith('viewpoint_claim_links/')
                 and p['viewpoint_id'] == head['viewpoint_id']
                 and p.get('effective_state', 'active') == 'active']
        if any(p['link_type'] in {'equivalent_full', 'equivalent_component'} for p in links):
            return None  # A different finding, not the retired-membership intake.
        targets.append({'viewpoint_id': head['viewpoint_id'],
                        'viewpoint_revision_id': head['current_revision_id'],
                        'retired_creation_candidate_id': candidate,
                        'current_link_ids': sorted(p['viewpoint_claim_link_id'] for p in links)})
        covered |= {k, rk}
    if covered != unresolved:
        return None
    return {'schema_version': VERSION,
            'handoff_id': sha256_json({'update_id': frozen['update_id'], 'targets': targets}),
            'source_update_id': frozen['update_id'], 'targets': targets,
            'original_correction_artifact_sha256': correction.get('artifact_sha256'),
            'original_response_sha256': sha256_json(correction['response']),
            'original_proposal_sha256': sha256_json(proposal.model_dump()),
            'original_correction_budget_exhausted': True,
            'next_action': 'current_membership_revalidation',
            'waiting_for': 'system', 'write_authority': False,
            'old_unresolved_is_not_pass': True,
            'retired_members_must_not_be_revived': True,
            'supports_is_not_equivalent': True,
            'preserve_core_claims_sources_campaign': True}
