"""Read-only target selection, separate from same-target link classification.

Finding an existing CVP is not permission to retarget a link. This stage builds
the full, current comparison packet; a selected target still needs an exact
postimage direction, independent Opus review and the shared SQL transaction.
No old rejected review is consumed as approval and no Registry write occurs.
"""
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.api.canonical_repository.postgres_store import record_content_sha, sha256_json
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.pipeline import source_reconciliation as content
from backend.pipeline.viewpoint_evidence_source_context import load_context_source
from backend.pipeline.viewpoint_semantic_contract import semantic_prompt

VERSION = 'wang_cvp_link_target_selection_v1'
PROMPT = '''先判断 Claim 的完整命题及论证方向，再比较原目标和所有候选的完整当前 CVR、成员、证据及自身全文。
分别判断：仅连接类型错（same_target）、目标观点错（different_target）、没有合适既有观点（no_existing_target）、材料不足（unresolved）。
不能预设只把等价改成支持；也不能因为旧目标被拒而默认新目标成立。
different_target 须选择包内既有候选；说明 Claim 哪一句以什么论证作用支持/等价于目标哪一部分，并解释旧目标为什么不成立。
不能把证据中另外一条命题借作 Claim 的结论，不能仅因同主题、相容、经文相同或名称相似而匹配。
能力/供应的论据不自动等价于受众身份辨识。新目标如包含范围条件，必须完整考虑，不忽略条件求通过。
no_existing_target 不是 defer；应交正常 new_viewpoint 路径，不创建绕过语义拒绝的观点。
本阶段只是建议，不能修改 Claim/Evidence/source/CVP/连接、不能伪造旧拒绝为通过；之后仍须精确后像独立 Opus 审核和 SQL/audit。
返回一个 selection，所有判断说明具体理由，不评价教授神学对错。'''


class Selection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    claim_id: str
    diagnosis: Literal['same_target', 'different_target', 'no_existing_target', 'unresolved']
    viewpoint_id: str | None = None
    link_type: Literal['supports', 'equivalent_full', 'equivalent_component'] | None = None
    reason: str = Field(min_length=1)
    old_target_reason: str = Field(min_length=1)


class Response(BaseModel):
    model_config = ConfigDict(extra='forbid')
    selection: Selection


def adapter(client=None):
    obj = StructuredJsonReviewerAdapter(client=client, prompt=semantic_prompt(PROMPT),
        response_model=Response, schema_name=VERSION, compact_payload=True,
        shared_text_payload=True, shared_record_payload=True)
    # The common boundary applies the owner-bound campaign budget/codec.
    obj.max_request_bytes = 500000
    return obj


def review_adapter(client=None):
    from backend.pipeline.viewpoint_authorized_source_repair_357 import Review
    obj = StructuredJsonReviewerAdapter(client=client, prompt=semantic_prompt(
        PROMPT + '\n独立审核 GPT selection，不沿用旧审核结论。完整比较目标及自身原文；'
        '每个指定 Claim 一个 verdict。仅选择及论证作用成立才 approved=true 且 problems=[]；'
        '这不是连接后像、SQL 写入或人工批准。'), response_model=Review,
        schema_name=VERSION + '_independent_review', compact_payload=True,
        shared_text_payload=True, shared_record_payload=True)
    obj.max_request_bytes = 500000
    return obj


def review_selection(store, packet, *, root, ports):
    """One GPT selection, one independent Opus review; never apply a link.

    Existing stage reservation/raw/encoding/budget logic is reused unchanged.
    A rejection or unknown reservation has no correction/retry path here.
    Candidate-list changes cannot reset the same link revision's budget.
    """
    from pathlib import Path
    from backend.pipeline.cvp_sql_actions import initialize_subscription_runtime
    from backend.pipeline.source_reconciliation_runner import Runner, Stop, write
    from backend.pipeline.viewpoint_authorized_source_repair_357 import Review
    old = packet['before_link']
    root = Path(root) / ('selection-' + sha256_json({'link_id':old['viewpoint_claim_link_id'],
                                                  'revision':old['revision']}))
    write(root/'comparison.packet.json', packet)
    runner = Runner(root, ports); runner.root = root

    def guard():
        current = build_packet(store,link_id=old['viewpoint_claim_link_id'],
            candidate_viewpoint_ids=[p['viewpoint']['viewpoint_id'] for p in packet['candidates']])
        if current != packet:
            raise ValueError('target selection current graph/source changed; no model replay')

    guard(); initialize_subscription_runtime(ports)
    raw = runner.call('proposal', packet)
    proposal = validate_selection(packet, raw['response'])
    if proposal['selection']['diagnosis'] in {'no_existing_target','unresolved'}:
        return proposal  # Not a target approval; normal coverage or source decision.
    review_packet = {'comparison':packet, 'proposal':raw['response'],
                     'proposal_artifact_sha256':raw['artifact_sha256']}
    guard()
    raw_review = runner.call('initial_review', review_packet)
    result = Review.model_validate(raw_review['response'])
    if (len(result.verdicts) != 1 or result.verdicts[0].claim_id != old['claim_id']
            or not result.verdicts[0].approved or result.verdicts[0].problems):
        raise Stop('target selection independently rejected; no automatic correction or retarget')
    guard()
    return {**proposal, 'selection_reviewed':True,
            'proposal_artifact_sha256':raw['artifact_sha256'],
            'review_artifact_sha256':raw_review['artifact_sha256']}


def production_ports():
    from backend.pipeline.source_reconciliation_runtime import ProductionPorts
    from backend.pipeline.viewpoint_resolution_runtime import subscription_client
    class TargetPorts(ProductionPorts):
        def adapter(self, stage):
            if stage == 'proposal':
                return adapter(subscription_client('codex','gpt-6.1-sol','high'))
            if stage == 'initial_review':
                return review_adapter(subscription_client('claude','claude-opus-5-5','high'))
            raise ValueError('target selection has no repeated correction/review stage')
    return TargetPorts()


def _approved(payload):
    if payload.get('review_status') in content.HUMAN_HOLDS or payload.get('review_status') not in {
        'system_approved', 'human_approved', 'approved', 'independent_terminal_reviewed',
        'ai_consensus_reviewed'}:
        raise ValueError('target selection requires approved current records; no hold promotion')


def build_packet(store, *, link_id, candidate_viewpoint_ids):
    """ID-indexed current graph plus complete member sources, never snapshots."""
    ids = list(candidate_viewpoint_ids)
    if not ids or len(ids) != len(set(ids)) or len(ids) > 16:
        raise ValueError('target selection needs 1..16 distinct exact candidate IDs')
    old = store.get_record('viewpoint_claim_links', link_id)
    if not old or old.get('effective_state') != 'active':
        raise ValueError('target selection requires an existing active link')
    _approved(old)
    if old['viewpoint_id'] in ids:
        raise ValueError('candidate list must distinguish new targets from the old target')
    records = store.read_cvp_context([old['claim_id']], viewpoint_ids=[old['viewpoint_id'], *ids])
    states = {}
    for key, row in records.items():
        if row.get('retired'):
            continue
        if row['revision'] != row['payload']['revision'] or record_content_sha(row['payload']) != row['content_sha256']:
            raise ValueError('target selection current SQL record/version/SHA drift')
        states[key] = row['payload']
    if states.get(('viewpoint_claim_links', link_id)) != old:
        raise ValueError('target selection link changed during indexed read')
    claim = states['claims', old['claim_id']]
    _approved(claim)
    if old['pinned_claim_revision'] != claim['revision']:
        raise ValueError('target selection stale Claim revision')
    comparisons = []
    for vid in [old['viewpoint_id'], *ids]:
        head = states['canonical_viewpoints', vid]
        revision = states['viewpoint_revisions', head['current_revision_id']]
        _approved(head); _approved(revision)
        if revision['viewpoint_id'] != vid:
            raise ValueError('foreign current CVR')
        links = sorted([p for (col, _), p in states.items() if col == 'viewpoint_claim_links'
                 and p['viewpoint_id'] == vid and p.get('effective_state') == 'active'],
                 key=lambda p:p['viewpoint_claim_link_id'])
        if not links:
            raise ValueError('existing target has no active members')
        members = []
        for link in links:
            _approved(link)
            member = states['claims', link['claim_id']]
            _approved(member)
            if (link['pinned_claim_revision'] != member['revision']
                    or link['validated_against_viewpoint_revision_id'] != head['current_revision_id']):
                raise ValueError('target selection stale member/target revision')
            members.append(member)
        comparisons.append({'viewpoint': head, 'current_revision': revision,
                            'links': links, 'member_claims': sorted(
                                {p['claim_id']:p for p in members}.values(),key=lambda p:p['claim_id'])})
    sources = []
    for (col, _), doc in sorted(states.items()):
        if col != 'source_documents':
            continue
        path, physical, projection = load_context_source(doc)
        sources.append({'source_id': doc['source_id'], 'path': str(path),
                        'file_sha256': physical, 'body_sha256': projection.body_sha256,
                        'paragraphs': deepcopy(projection.spoken_rows)})
    if not sources:
        raise ValueError('target selection lacks complete member own sources')
    packet = {'schema_version': VERSION, 'before_link': deepcopy(old), 'claim': deepcopy(claim),
              'old_target': comparisons[0], 'candidates': comparisons[1:], 'sources': sources,
              'context_records': [{'collection': col, 'object_id': oid, 'payload': deepcopy(p)}
                                  for (col, oid), p in sorted(states.items())],
              'registry_writes': 0, 'semantic_approval': False}
    return packet


def validate_selection(packet, response):
    """Route a proposal, not an approved postimage or a new call allowance."""
    result = Response.model_validate(response).selection
    if result.claim_id != packet['claim']['claim_id']:
        raise ValueError('foreign target selection Claim')
    old = packet['before_link']['viewpoint_id']
    candidates = {c['viewpoint']['viewpoint_id'] for c in packet['candidates']}
    if result.diagnosis in {'same_target', 'different_target'}:
        permitted = {old} if result.diagnosis == 'same_target' else candidates
        if result.viewpoint_id not in permitted or result.link_type is None:
            raise ValueError('target diagnosis/selected endpoint mismatch')
    elif result.viewpoint_id is not None or result.link_type is not None:
        raise ValueError('unresolved/no-existing selection cannot authorize an endpoint')
    return {'selection': result.model_dump(mode='json'), 'packet_sha256': sha256_json(packet),
            'next_stage': {'same_target': 'exact_link_postimage_review',
                           'different_target': 'exact_target_postimage_review',
                           'no_existing_target': 'normal_new_viewpoint',
                           'unresolved': 'source_or_scope_decision'}[result.diagnosis],
            'semantic_approval': False, 'registry_writes': 0}


def validate_group_scope(saved, packet):
    # A complete affected-CVP audit includes retained members from earlier
    # groups. They are not new members of the current original denominator.
    views = saved.get('reviewed', {}).get('evidence', {}).get('viewpoint_ids', [])
    if (packet['claim']['claim_id'] not in saved['claim_ids']
            and packet['before_link']['viewpoint_id'] not in views):
        raise ValueError('target selection is outside original or affected current group scope')
