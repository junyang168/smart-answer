"""Bounded existing-link withdrawals with independently reviewed replacement.

One common SQL transaction preserves the old payloads and supplies replacement
coverage before withdrawing its only link. No Claim/source/CVP text changes.
"""
from copy import deepcopy
from pathlib import Path
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.api.canonical_repository.postgres_store import record_content_sha, canonical_json, sha256_json
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.pipeline import source_reconciliation as content
from backend.pipeline.source_reconciliation_runner import Runner, Stop, body, read, write, stage_adapter
from backend.pipeline.viewpoint_authorized_source_repair_357 import Review
from backend.pipeline.viewpoint_evidence_source_context import load_context_source
from backend.pipeline.viewpoint_resolution_runtime import subscription_client, read_artifact
from backend.pipeline.viewpoint_semantic_contract import semantic_prompt

SCHEMA = 'wang_cvp_sql_link_replacement_v1'
DIRECTION = 'wang_cvp_link_replacement_direction_v1'
WITHDRAWAL_DIRECTION = 'wang_cvp_reviewed_link_withdrawal_direction_v1'
WITHDRAWAL_PROMPT = '''读取指定 Claim 完整自身正文、完整原证据、原生成判定、原审计与 GPT 判读，以及全部当前连接/CVR/成员。
负责人要求修复明列连接的问题，不是批准撤回后像，也不是推翻原上下文判定。
本次仅判断明列 supports/qualifies 连接能否逻辑撤回。若完整语境不能明确证明撤回正确，返回 unresolved，不能默认通过。
撤回仅改变 SQL retired 状态，旧 payload 完整保留；Claim 仍须有其他当前已批准等价覆盖，原目标仍须有其他当前等价成员。
不得修改 Claim/Evidence/fragment/source/CVP/core/scope/端点/其他连接，不解除 hold，不改旧审核或异议。
这是新管理后像提案，不是再次判读原异议；后续独立 Opus 必须审核完整撤回后像及保留覆盖。'''
PROMPT = '''读取指定 Claim 完整自身正文、完整原证据、原生成判定、原审计与 GPT 判读，比较新旧完整 CVR 及所有成员。
负责人授权撤回明列旧连接并优先匹配候选既有 CVP，不是批准新的连接类型或内容。
只为 replacement_link_id 对应 Claim 提出一个既有新目标及准确 link_type：equivalent_full/equivalent_component/supports/qualifies。
解释此 Claim 的同一完整成分在自身语境中的实际意思及对目标的具体作用；不能借别的 Claim 或正文另一独立结论补摘要。
旧目标的原判定和新异议都保留。不得修改 Claim/Evidence/fragment/source/CVP/core/scope/其他连接，不解除 hold。
component_locator/evidence_bindings 仅复用该 Claim 原连接的完整成分和自身绑定；不新增、删改引用或手算 SHA。
如候选不适合，返回 no_existing_target 或 unresolved，不能默认通过或把正常缺覆盖 defer。
本阶段只是提议；下一独立 Opus 必须审核全部撤回和替代完整后像，未通过整笔不写。'''


class Choice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    claim_id: str
    diagnosis: Literal['different_target', 'no_existing_target', 'unresolved']
    viewpoint_id: str | None = None
    link_type: Literal['supports', 'qualifies', 'equivalent_full', 'equivalent_component'] | None = None
    reason: str = Field(min_length=1)
    old_target_reason: str = Field(min_length=1)


class Response(BaseModel):
    model_config = ConfigDict(extra='forbid')
    selection: Choice


class WithdrawalChoice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    claim_id: str
    diagnosis: Literal['withdraw_only', 'unresolved']
    reason: str = Field(min_length=1)
    old_target_reason: str = Field(min_length=1)


class WithdrawalResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    selection: WithdrawalChoice


def adapter(stage, *, withdrawal_only=False):
    if stage not in {'proposal', 'initial_review', 'correction', 'final_review'}:
        raise ValueError('replacement uses the existing single-correction review boundary')
    proposal = stage in {'proposal', 'correction'}
    base = WITHDRAWAL_PROMPT if withdrawal_only else PROMPT
    prompt = base if proposal else base + '''\n独立审核 after_link 及全部 withdrawal_links 的完整后像，不重新提议。
逐项核查保留 Claim/source/原目标其他成员、原正确连接、选择的具体语义作用、完整自身来源和 current CVR。
before 中非批准/hold 的旧其他成员只读 impact_only，不得到新批准也不能解除。
after_link 暂留旧 decision_id 仅作显示，提交时程序绑定本轮回答产生新 VIC/VID；旧连接 payload 原样历史退役。
每个被撤回连接的 Claim 恰好一个 verdict，包含新连接语义；全部成立才 approved=true 且 problems=[]。'''
    obj = StructuredJsonReviewerAdapter(client=subscription_client(
        'codex' if proposal else 'claude', 'gpt-6.1-sol' if proposal else 'claude-opus-5-5', 'high'),
        prompt=semantic_prompt(prompt), response_model=(WithdrawalResponse if withdrawal_only else Response) if proposal else Review,
        schema_name=SCHEMA + ('_withdrawal' if withdrawal_only else '') + '_' + stage, compact_payload=True,
        shared_text_payload=True, shared_record_payload=True)
    obj.max_request_bytes = 500000
    return obj


def production_ports(*, withdrawal_only=False):
    from backend.pipeline.source_reconciliation_runtime import ProductionPorts
    class Ports(ProductionPorts):
        def adapter(self, stage): return adapter(stage, withdrawal_only=withdrawal_only)
    return Ports()


def validate_direction(d, m):
    withdrawal_only = m.get('operation_mode') == 'withdrawal_only'
    expected = {'schema_version': WITHDRAWAL_DIRECTION if withdrawal_only else DIRECTION, 'run_id': m['run_id'],
        'campaign_sha256': m['campaign_sha256'],
        'withdrawal_links': [{'link_id': p['viewpoint_claim_link_id'], 'sha256': sha256_json(p)} for p in m['withdrawal_links']],
        'replacement_link_id': m['replacement_link_id'], 'candidate_viewpoint_ids': m['candidate_viewpoint_ids'],
        'original_reports': m['original_reports'], 'authorized_by': 'owner',
        'human_content_approval': False, 'automatic_future_repairs_authorized': False}
    if any(d.get(k) != v for k, v in expected.items()) or not d.get('authorization_quote'):
        raise ValueError('replacement exact owner scope drift')
    if withdrawal_only and d.get('operation_mode') != 'withdrawal_only':
        raise ValueError('withdrawal mode authority missing')
    old = m['withdrawal_links']
    ids = [p['viewpoint_claim_link_id'] for p in old]
    if (not old or len(ids) != len(set(ids)) or len({p['claim_id'] for p in old}) != len(old)
            or m['replacement_link_id'] not in ids or (not withdrawal_only and not m['candidate_viewpoint_ids'])
            or len(set(m['candidate_viewpoint_ids'])) != len(m['candidate_viewpoint_ids'])):
        raise ValueError('replacement requires distinct exact withdrawals and one covered Claim')
    if withdrawal_only and (m['candidate_viewpoint_ids'] or len(ids) != 1):
        raise ValueError('withdrawal must be one exact link without replacement targets')
    states = content.index(m['records'])
    from backend.pipeline.cvp_link_target_selection import _approved
    def retained_coverage(link):
        if (link.get('effective_state') != 'active' or link.get('link_type') not in
                {'equivalent_full', 'equivalent_component'} or link['viewpoint_claim_link_id'] in ids):
            return False
        try:
            claim = states[content.key('claims', link['claim_id'])]
            head = states[content.key('canonical_viewpoints', link['viewpoint_id'])]
            revision = states[content.key('viewpoint_revisions', head['current_revision_id'])]
            for item in (link, claim, head, revision): _approved(item)
            return (link['pinned_claim_revision'] == claim['revision'] and
                link['validated_against_viewpoint_revision_id'] == head['current_revision_id'] and
                revision['viewpoint_id'] == head['viewpoint_id'])
        except (KeyError, ValueError):
            return False
    for p in old:
        claim = states[content.key('claims', p['claim_id'])]
        if (p.get('effective_state') != 'active' or p['link_type'] not in {'supports', 'qualifies'}
                or p.get('supporting_relation_ids') or p['pinned_claim_revision'] != claim['revision']):
            raise ValueError('replacement cannot withdraw equivalent, held or relation-bound links')
        for item in (p, claim):
            _approved(item)
        own_head = states[content.key('canonical_viewpoints', p['viewpoint_id'])]
        own_revision = states[content.key('viewpoint_revisions', own_head['current_revision_id'])]
        _approved(own_head); _approved(own_revision)
        if own_revision['viewpoint_id'] != own_head['viewpoint_id']:
            raise ValueError('withdrawal old CVR is foreign')
        if p['validated_against_viewpoint_revision_id'] != own_head['current_revision_id']:
            raise ValueError('withdrawal current CVR drift')
        equivalent = [v for k, v in states.items() if k.startswith('viewpoint_claim_links/')
            and v['viewpoint_id'] == p['viewpoint_id'] and v['claim_id'] != p['claim_id'] and retained_coverage(v)]
        if not equivalent: raise ValueError('withdrawal would orphan old CVP')
        if withdrawal_only or p['viewpoint_claim_link_id'] != m['replacement_link_id']:
            if not any(v['claim_id'] == p['claim_id'] and retained_coverage(v)
                for k, v in states.items() if k.startswith('viewpoint_claim_links/')):
                raise ValueError('withdrawal would leave another Claim without correct coverage')


def prepare_material(store, run_id, authorization):
    from backend.pipeline.cvp_sql_flow import check_binding, pins, pin_rows
    from backend.pipeline.cvp_link_target_selection import _approved
    from backend.pipeline.viewpoint_adjudication_history import validated_history
    check_binding(authorization); d = read_artifact(authorization['path'])
    group = store.get_cvp_group(run_id)
    if not group or group['campaign_sha256'] != d['campaign_sha256']:
        raise ValueError('replacement foreign group/campaign')
    for report in d['original_reports']: check_binding(report)
    packet, findings = validated_history(Path(d['adjudication_root']))
    old = [store.get_record('viewpoint_claim_links', r['link_id']) for r in d['withdrawal_links']]
    if any(p is None for p in old): raise ValueError('withdrawal absent/retired')
    for p in old:
        matches = [i for i in packet['items'] if p in i.get('connections', [])]
        if (len(matches) != 1 or findings[matches[0]['item_id']]['verdict'] != 'audit_objection_upheld'
                or store.get_record('claims', p['claim_id']) != matches[0]['claim']
                or p['viewpoint_id'] not in group['reviewed']['evidence']['viewpoint_ids']):
            raise ValueError('replacement not bound to exact original upheld/current affected graph')
    records = store.read_cvp_context([p['claim_id'] for p in old],
        viewpoint_ids=sorted({p['viewpoint_id'] for p in old} | set(d['candidate_viewpoint_ids'])))
    states = {}
    for k, row in records.items():
        if row['retired']: continue
        p = row['payload']
        if row['revision'] != p['revision'] or record_content_sha(p) != row['content_sha256']:
            raise ValueError('current replacement SQL payload/revision/SHA drift')
        states[k] = p
    targets = []
    for vid in d['candidate_viewpoint_ids']:
        h = states['canonical_viewpoints', vid]; r = states['viewpoint_revisions', h['current_revision_id']]
        _approved(h); _approved(r)
        if vid in {p['viewpoint_id'] for p in old} or r['viewpoint_id'] != vid:
            raise ValueError('replacement must compare a different exact current target')
        members = sorted([p for (col, _), p in states.items() if col == 'viewpoint_claim_links'
                   and p['viewpoint_id'] == vid and p.get('effective_state') == 'active'],
                   key=lambda p: p['viewpoint_claim_link_id'])
        if not members: raise ValueError('target lacks actual members')
        for link in members:
            claim = states['claims', link['claim_id']]; _approved(link); _approved(claim)
            if link['pinned_claim_revision'] != claim['revision'] or link['validated_against_viewpoint_revision_id'] != r['viewpoint_revision_id']:
                raise ValueError('target membership drift')
        targets.append({'viewpoint': h, 'current_revision': r, 'links': members})
    sources = []
    for (col, _), doc in sorted(states.items()):
        if col == 'source_documents':
            path, physical, projection = load_context_source(doc)
            sources.append({'source_id': doc['source_id'], 'path': str(path), 'file_sha256': physical,
                            'body_sha256': projection.body_sha256, 'paragraphs': deepcopy(projection.spoken_rows)})
    m = {'run_id': run_id, 'campaign_sha256': group['campaign_sha256'], 'authorization': authorization,
        'withdrawal_links': old, 'replacement_link_id': d['replacement_link_id'],
        'candidate_viewpoint_ids': d['candidate_viewpoint_ids'], 'targets': targets, 'sources': sources,
        'original_reports': d['original_reports'], 'adjudication_root': d['adjudication_root'],
        'original_model_responses': [{'binding': b, 'model_id': r['model_id'],
            'backend': r['backend'], 'response': r['response'], 'response_sha256': r['response_sha256']}
            for b in d['original_reports'] for r in [read_artifact(b['path'])]
            if 'model_id' in r and 'response' in r],
        'records': [{'collection': col, 'object_id': oid, 'payload': p} for (col, oid), p in sorted(states.items())],
        'read_pins': pin_rows(pins(records)),
        'retained_unapproved_members': [{'collection': col, 'object_id': oid, 'impact_only': True}
            for (col, oid), p in sorted(states.items()) if col == 'claims' and p.get('review_status') in content.HUMAN_HOLDS]}
    if d.get('schema_version') == WITHDRAWAL_DIRECTION:
        m['operation_mode'] = d.get('operation_mode')
    if not sources: raise ValueError('replacement lacks complete own sources')
    validate_direction(d, m)
    return json.loads(canonical_json(m))


def postimage(m, response):
    if m.get('operation_mode') == 'withdrawal_only':
        choice = WithdrawalResponse.model_validate(response).selection
        if choice.claim_id != m['withdrawal_links'][0]['claim_id']:
            raise ValueError('withdrawal foreign Claim')
        if choice.diagnosis != 'withdraw_only':
            raise Stop('withdrawal independently unresolved; no write/retry')
        return None
    choice = Response.model_validate(response).selection
    old = next(p for p in m['withdrawal_links'] if p['viewpoint_claim_link_id'] == m['replacement_link_id'])
    if choice.claim_id != old['claim_id']: raise ValueError('replacement foreign Claim')
    if choice.diagnosis != 'different_target':
        raise Stop('replacement requires normal new_viewpoint coverage or unresolved-source handoff; no write/retry')
    if choice.viewpoint_id not in m['candidate_viewpoint_ids'] or choice.link_type is None:
        raise ValueError('replacement target/type outside direction')
    target = next(t for t in m['targets'] if t['viewpoint']['viewpoint_id'] == choice.viewpoint_id)
    if any(p['claim_id'] == old['claim_id'] for p in target['links']):
        raise ValueError('replacement duplicates existing membership')
    new = deepcopy(old)
    new.update(revision=1, viewpoint_id=choice.viewpoint_id, link_type=choice.link_type,
               validated_against_viewpoint_revision_id=target['viewpoint']['current_revision_id'])
    if choice.link_type == 'equivalent_full': new['component_locator'] = None
    new['viewpoint_claim_link_id'] = 'VCL-' + sha256_json({'contract': SCHEMA, 'old': sha256_json(old),
        'target': choice.viewpoint_id, 'type': choice.link_type})[:20]
    return new


def review_packet(m, raw):
    packet = {'schema_version': SCHEMA, 'materials': m, 'proposal': raw['response'],
            'after_link': postimage(m, raw['response']), 'withdrawal_links': m['withdrawal_links'],
            'provenance_receipt': 'Fresh candidate/decision bind actual new GPT and Opus; old judgments unchanged.'}
    if m.get('operation_mode') == 'withdrawal_only':
        packet['withdrawal_storage_postimages'] = [{'collection': 'viewpoint_claim_links',
            'object_id': p['viewpoint_claim_link_id'], 'payload': p, 'payload_unchanged': True,
            'storage_revision': p['revision'] + 1, 'retired': True} for p in m['withdrawal_links']]
        packet['provenance_receipt'] = 'Only logical withdrawal after real independent approval; old payload/decisions/verdicts remain unchanged. No new Claim/CVP/link/identity rows.'
    return packet


def approved(m, raw):
    review = Review.model_validate(raw['response'])
    wanted = sorted(p['claim_id'] for p in m['withdrawal_links'])
    if sorted(v.claim_id for v in review.verdicts) != wanted or any(not v.approved or v.problems for v in review.verdicts):
        raise Stop('replacement exact postimages independently rejected; no second correction')


def correction_packet(m, proposal, review):
    return {'schema_version': SCHEMA, 'materials': m, 'proposal': proposal['response'],
            'initial_review': review['response'],
            'prior_artifacts': {'proposal': proposal['artifact_sha256'], 'review': review['artifact_sha256']},
            'correction_boundary': 'One complete corrected selection only; never alter Claim/source/CVP or old judgments.'}


def effective_saved_review(root, m):
    """Consume the original stages, at most one correction, without calls."""
    proposal = saved_raw(root, 'proposal', m)
    review = saved_raw(root, 'initial_review', review_packet(m, proposal))
    try:
        approved(m, review)
    except Stop:
        proposal = saved_raw(root, 'correction', correction_packet(m, proposal, review))
        review = saved_raw(root, 'final_review', review_packet(m, proposal))
        approved(m, review)
    else:
        if any((root / (stage + suffix)).exists() for stage in ('correction', 'final_review')
               for suffix in ('.raw.json', '.reservation.json', '.call-intent.json')):
            raise ValueError('approved initial review cannot acquire a second review budget')
    return proposal, review


def saved_raw(root, stage, packet, *, adapter_factory=None):
    # Runner seals canonical key order before encoding. Reproduce that exact
    # request, including shape dictionaries; do not authenticate a new wire.
    packet = json.loads(canonical_json(packet))
    raw = read(root / (stage + '.raw.json'))
    m = packet.get('materials', packet)
    factory = adapter_factory or ((lambda s: adapter(s, withdrawal_only=True)) if m.get('operation_mode') == 'withdrawal_only' else adapter)
    model = stage_adapter(factory(stage), packet, root / (stage + '.raw.json'), saved=raw)
    # Authenticate this contract's actual adapter, never relabel an old review.
    from backend.pipeline.source_reconciliation_runner import request_limit
    proof = model.request_encoding_proof(packet)
    expected = {'model_id': model.model_id, 'backend': model.backend, 'prompt_sha256': model.prompt_sha256,
        'generation_config_sha256': model.generation_config_sha256, 'request_payload_sha256': sha256_json(packet),
        'request_encoding_proof': proof, 'request_bytes': model.request_bytes(packet), 'stage': stage, 'automatic_retry': False}
    limit, budget = request_limit(model, root / (stage + '.raw.json'), saved=raw)
    if budget is not None: expected['request_budget'] = budget
    if expected != body(read(root / (stage + '.reservation.json'))) or any(raw.get(k) != v for k, v in expected.items()) or expected['request_bytes'] > limit:
        raise ValueError('replacement saved request/reservation/model drift')
    from backend.pipeline.viewpoint_resolution_runtime import bound_model_response
    if (not proof.get('exact_roundtrip_verified') or raw['response_sha256'] != sha256_json(raw['response'])
            or raw.get('provider_raw_sha256') != sha256_json(raw.get('provider_raw'))):
        raise ValueError('replacement saved raw/codec drift')
    from datetime import datetime
    identity = raw.get('runtime_identity')
    if (datetime.fromisoformat(raw['completed_at']).utcoffset() is None or
            (identity is not None and (identity.get('model_id') != model.model_id
                or identity.get('backend') != model.backend or identity.get('reasoning_effort') != 'high'
                or not identity.get('file_sha256')))):
        raise ValueError('replacement actual runtime/time identity drift')
    bound_model_response(model, packet, raw['response'], root / (stage + '.raw.json'), persist=False)
    return raw


def compile_package(m, proposal, review, root):
    from backend.pipeline.cvp_link_retarget import fresh_identity_records
    approved(m, review); link = postimage(m, proposal['response'])
    states = content.index(m['records'])
    old = next(p for p in m['withdrawal_links'] if p['viewpoint_claim_link_id'] == m['replacement_link_id'])
    seed = sha256_json({'contract': SCHEMA, 'materials': sha256_json(m),
                       'proposal': proposal['artifact_sha256'], 'review': review['artifact_sha256']})
    if m.get('operation_mode') == 'withdrawal_only':
        return {'package_id': 'CVP-LINK-WITHDRAW-' + seed[:24],
            'viewpoint_claim_links': [], 'viewpoint_identity_candidates': [], 'viewpoint_identity_decisions': [],
            'source_repair_application': {'schema_version': SCHEMA, 'run_root': str(Path(root).resolve()), 'materials_sha256': sha256_json(m)}}
    target = next(t for t in m['targets'] if t['viewpoint']['viewpoint_id'] == link['viewpoint_id'])
    candidate, decision = fresh_identity_records(states, old, link,
        target['current_revision']['proposition_signature'], proposal, review, seed=seed, policy=SCHEMA)
    link['decision_id'] = decision['identity_decision_id']
    return {'package_id': 'CVP-LINK-REPLACE-' + seed[:24], 'viewpoint_claim_links': [link],
        'viewpoint_identity_candidates': [candidate], 'viewpoint_identity_decisions': [decision],
        'source_repair_application': {'schema_version': SCHEMA, 'run_root': str(Path(root).resolve()), 'materials_sha256': sha256_json(m)}}


def validate_package(package):
    from backend.pipeline.cvp_sql_flow import check_binding
    app = package['source_repair_application']; root = Path(app['run_root'])
    if app['schema_version'] != SCHEMA or not root.is_absolute(): raise ValueError('replacement authority missing')
    m = body(read(root / 'materials.json'))
    if app['materials_sha256'] != sha256_json(m): raise ValueError('replacement material seal drift')
    check_binding(m['authorization']); validate_direction(read_artifact(m['authorization']['path']), m)
    for b in m['original_reports']: check_binding(b)
    for s in m['sources']: check_binding({'path': s['path'], 'file_sha256': s['file_sha256']})
    proposal, review = effective_saved_review(root, m)
    if package != compile_package(m, proposal, review, root): raise ValueError('replacement exact approved delta drift')
    return tuple(('viewpoint_claim_links', p['viewpoint_claim_link_id']) for p in m['withdrawal_links'])


def execute(store, run_id, *, authorization, root, ports):
    from backend.pipeline.cvp_sql_flow import check_binding, binding, decoded_pins, commit_package, verified_group_backup
    from backend.pipeline.cvp_sql_actions import initialize_subscription_runtime, resume_bound_action
    check_binding(authorization); d = read_artifact(authorization['path'])
    identity = {'contract': SCHEMA, 'withdrawals': d['withdrawal_links'], 'replacement': d['replacement_link_id']}
    if d.get('schema_version') == WITHDRAWAL_DIRECTION:
        identity['operation_mode'] = d.get('operation_mode')
    aid = 'CVPA-' + sha256_json(identity)
    root = Path(root) / aid; store.bind_cvp_execution_root(run_id, identity=aid, root=root)
    previous = store.get_cvp_action(run_id, aid)
    if previous and previous['authorization'] != authorization: raise ValueError('replacement direction/root cannot reset once budget')
    resumed = resume_bound_action(store, run_id, aid)
    if resumed: return resumed
    m = previous['materials'] if previous else prepare_material(store, run_id, authorization)
    validate_direction(d, m)
    action = store.prepare_cvp_action(run_id, action_id=aid, kind='owner_link_retarget', authorization=authorization, materials=m)
    write(root / 'materials.json', m); expected = decoded_pins(m['read_pins'])
    def guard():
        for s in m['sources']: check_binding({'path': s['path'], 'file_sha256': s['file_sha256']})
        with store.connect() as conn, conn.cursor() as cursor: store._assert_transaction_read_pins(cursor, expected)
    guard(); initialize_subscription_runtime(ports)
    runner = Runner(root, ports); runner.root = root
    proposal = runner.call('proposal', m); post = review_packet(m, proposal); guard()
    review = runner.call('initial_review', post); guard()
    proposal_stage, review_stage = 'proposal', 'initial_review'
    try:
        approved(m, review)
    except Stop:
        proposal = runner.call('correction', correction_packet(m, proposal, review)); guard()
        review = runner.call('final_review', review_packet(m, proposal)); approved(m, review); guard()
        proposal_stage, review_stage = 'correction', 'final_review'
    package = compile_package(m, proposal, review, root); validate_package(package)
    plan = store.plan_package(package, source_kind=SCHEMA, transactional=True)
    wanted = {('retire', 'viewpoint_claim_links', p['viewpoint_claim_link_id']) for p in m['withdrawal_links']}
    for col in ('viewpoint_claim_links', 'viewpoint_identity_candidates', 'viewpoint_identity_decisions'):
        field = {'viewpoint_claim_links': 'viewpoint_claim_link_id', 'viewpoint_identity_candidates': 'identity_candidate_id', 'viewpoint_identity_decisions': 'identity_decision_id'}[col]
        wanted.update(('create', col, p[field]) for p in package[col])
    if len(plan.operations) != len(wanted) or {(o.operation, o.collection, o.object_id) for o in plan.operations} != wanted:
        raise ValueError('replacement plan contains an extra update/retirement/write')
    evidence = store.get_cvp_group(run_id)['reviewed']['evidence']
    return commit_package(store, run_id, package=package,
        approval={'gpt': binding(root / (proposal_stage + '.raw.json')),
                  'opus': binding(root / (review_stage + '.raw.json'))},
        authorization=authorization, backup=verified_group_backup(root, package, ports),
        sources=[{'path': s['path'], 'file_sha256': s['file_sha256']} for s in m['sources']], read_pins=expected,
        reason='owner bounded withdrawal and independently reviewed replacement; original judgments preserved',
        actor='owner-authorized-pipeline', source_kind=SCHEMA,
        viewpoint_ids=sorted(set(evidence['viewpoint_ids']) | set(m['candidate_viewpoint_ids'])),
        disposition=evidence.get('disposition'), action=action)
