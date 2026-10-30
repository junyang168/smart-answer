"""Reviewed source observations supporting a CVP without invented inference."""
from typing import Literal
from pydantic import Field, model_validator

from .knowledge_models import ViewpointObservationSupportRecord
from .viewpoint_foundation import semantic_record_sha, sha256_json
from .viewpoint_batch_resolution import (
    StrictBatchModel, ArgumentRouteProposalV2Response, ArgumentRouteReviewV2Response,
    ArgumentRouteReconsiderationV2Response, ReviewedRouteChangeV2,
    CvpReReviewExceptionV2, RouteFindingDispositionV2, route_evidence_is_usable,
)

COLLECTION = 'viewpoint_observation_supports'
CONTRACT = 'wang_cvp_observation_support_v1'
RETIREMENT_BINDING = 'wang_observation_revision_retirement_binding_v1'
INELIGIBLE_CLAIM_STATUSES = frozenset({
    'rejected', 'human_hold', 'human_review_required', 'invalidated',
    'superseded', 'retired', 'withdrawn', 'withheld_unreviewed',
})


def observation_claim_is_usable(claim):
    payload=claim.model_dump(mode='json') if hasattr(claim,'model_dump') else claim
    return payload.get('review_status') not in INELIGIBLE_CLAIM_STATUSES and not payload.get('human_hold')


class ObservationBinding(StrictBatchModel):
    claim_component_key: str = Field(pattern=r'^CCK-[0-9a-f]{64}$')
    evidence_step_id: str
    source_fragment_id: str


class ProposedObservationSupport(StrictBatchModel):
    local_observation_key: str = Field(min_length=1)
    viewpoint_revision_id: str
    source_id: str
    observation_summary: str = Field(min_length=1)
    supports: str = Field(min_length=1)
    support_scope_note: str = Field(min_length=1)
    evidence_bindings: list[ObservationBinding] = Field(min_length=1)

    @model_validator(mode='after')
    def unique_bindings(self):
        keys=[(b.claim_component_key,b.evidence_step_id,b.source_fragment_id) for b in self.evidence_bindings]
        if len(keys)!=len(set(keys)):raise ValueError('duplicate observation evidence binding')
        return self


class SourceWithoutObservation(StrictBatchModel):
    viewpoint_revision_id: str
    source_id: str
    reason: str = Field(min_length=1)

    def key(self):return self.viewpoint_revision_id+'::'+self.source_id


class ArgumentRouteProposalV3Response(ArgumentRouteProposalV2Response):
    schema_version: Literal['wang_argument_route_proposal_v3']='wang_argument_route_proposal_v3'
    observation_supports: list[ProposedObservationSupport]
    sources_without_observation: list[SourceWithoutObservation]
    retired_observation_support_ids: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def unique_observations(self):
        keys=[o.local_observation_key for o in self.observation_supports]
        if len(keys)!=len(set(keys)):raise ValueError('observation local keys must be unique')
        absent=[s.key() for s in self.sources_without_observation]
        if len(absent)!=len(set(absent)):raise ValueError('observation source dispositions must be unique')
        if self.retired_observation_support_ids!=sorted(set(self.retired_observation_support_ids)):
            raise ValueError('retired observation IDs must be sorted and unique')
        return self


class ReviewedRouteChangeV3(ReviewedRouteChangeV2):
    target_kind: Literal['route','attestation','no_route','member_source','route_retirement',
                         'observation','observation_source','observation_retirement']


class CvpReReviewExceptionV3(CvpReReviewExceptionV2):
    triggering_target_kind: Literal['route','attestation','no_route','member_source','route_retirement',
                                   'observation','observation_source','observation_retirement']


class ArgumentRouteReviewV3Response(ArgumentRouteReviewV2Response):
    schema_version: Literal['wang_argument_route_review_v3']='wang_argument_route_review_v3'
    change_reviews: list[ReviewedRouteChangeV3] = Field(min_length=1)
    cvp_re_review_exceptions: list[CvpReReviewExceptionV3] = Field(default_factory=list)


class FindingDispositionV3(RouteFindingDispositionV2):
    target_kind: Literal['route','attestation','no_route','member_source','route_retirement',
                         'observation','observation_source','observation_retirement']


class ArgumentRouteReconsiderationV3Response(ArgumentRouteReconsiderationV2Response):
    schema_version: Literal['wang_argument_route_reconsideration_v3']='wang_argument_route_reconsideration_v3'
    finding_dispositions: list[FindingDispositionV3] = Field(min_length=1)
    revised_proposal: ArgumentRouteProposalV3Response


def observation_targets(proposal):
    if not isinstance(proposal,ArgumentRouteProposalV3Response):return set()
    return ({('observation',o.local_observation_key) for o in proposal.observation_supports}
            |{('observation_source',o.key()) for o in proposal.sources_without_observation}
            |{('observation_retirement',i) for i in proposal.retired_observation_support_ids})


def observation_payload(item,packet):
    components={c['claim_component_key']:c for c in packet['claim_components']}
    claims={c['claim_id']:c for c in packet['claims']}
    revision=next(v for v in packet['approved_viewpoints'] if v['viewpoint_revision_id']==item.viewpoint_revision_id)
    bindings={}
    for b in item.evidence_bindings:
        c=claims[components[b.claim_component_key]['claim_id']]
        row={'claim_id':c['claim_id'],'pinned_claim_revision':c['pinned_claim_revision'],
             'claim_revision_sha256':c['claim_revision_sha256'],
             'evidence_step_id':b.evidence_step_id,'source_fragment_id':b.source_fragment_id}
        bindings[(c['claim_id'],b.evidence_step_id,b.source_fragment_id)]=row
    return {'viewpoint_id':revision['viewpoint_id'],
        'validated_against_viewpoint_revision_id':item.viewpoint_revision_id,
        'source_id':item.source_id,'source_revision_sha256':packet['source_revisions'][item.source_id],
        'observation_summary':item.observation_summary,'supports':item.supports,
        'support_scope_note':item.support_scope_note,
        'evidence_bindings':[bindings[k] for k in sorted(bindings)]}


def observation_id(payload):return 'VOS-'+sha256_json(payload)[:24]


def revision_retirement_binding(viewpoints, existing_supports, *, claims=(),
                                source_revisions=None, unusable_context_claims=()):
    """An observation pinned to an obsolete input cannot remain current.

    This retires an immutable version, not its semantic observation. Models
    still propose the complete current observations; independent review sees
    every resulting retirement together with that complete replacement set.
    """
    current = {v['viewpoint_id']:v['viewpoint_revision_id'] for v in viewpoints}
    if len(current) != len(viewpoints):
        raise ValueError('observation retirement needs one current revision per CVP')
    if any(o['viewpoint_id'] not in current for o in existing_supports):
        raise ValueError('observation retirement has a foreign CVP')
    claim_index = {c['claim_id']:c for c in claims}
    unavailable = {c['claim_id'] for c in unusable_context_claims}
    source_revisions = source_revisions or {}
    def obsolete(o):
        if o['validated_against_viewpoint_revision_id'] != current[o['viewpoint_id']]:
            return True
        if o['source_id'] in source_revisions and o['source_revision_sha256'] != source_revisions[o['source_id']]:
            return True
        for binding in o['evidence_bindings']:
            if binding['claim_id'] in unavailable:
                return True
            claim = claim_index.get(binding['claim_id'])
            if claim is not None and (binding['pinned_claim_revision'] != claim['pinned_claim_revision']
                    or binding['claim_revision_sha256'] != claim['claim_revision_sha256']):
                return True
        # Missing context alone is not evidence of a retired/replaced Claim.
        return False
    return {'schema_version':RETIREMENT_BINDING,
        'required_retirement_ids':sorted(o['observation_support_id'] for o in existing_supports
            if obsolete(o)),
        'note':('程序会将绑定已更换的 CVP、Claim 或来源版本，以及明确不可用 Claim 的观察记录列为技术退役，不要求模型抄写这些 ID。'
                '这不表示其观察内容应删除：仍须根据全部原文提出完整的当前观察。'
                'Opus 审核程序展开后的完整候选，包括全部旧记录、技术退役和新观察。'
                '全部绑定仍为当前有效版本的旧观察须逐项保留或明确提出退役，不能静默省略。')}


def bind_revision_retirements(proposal, packet):
    """Expand forced version bookkeeping without changing raw model content."""
    binding = packet.get('observation_retirement_binding')
    if binding is None:
        return proposal, None  # Historical packets retain their exact contract.
    expected = revision_retirement_binding(packet['approved_viewpoints'],
        packet.get('existing_observation_supports', []), claims=packet.get('claims', []),
        source_revisions=packet.get('source_revisions', {}),
        unusable_context_claims=packet.get('unusable_context_claims', []))
    if binding != expected or not isinstance(proposal, ArgumentRouteProposalV3Response):
        raise ValueError('observation retirement binding differs from current complete material')
    supplied = set(proposal.retired_observation_support_ids)
    added = sorted(set(expected['required_retirement_ids']) - supplied)
    if not added:
        return proposal, None
    bound = proposal.model_copy(update={'retired_observation_support_ids':sorted(supplied | set(added))})
    return bound, {'schema_version':RETIREMENT_BINDING,
        'route_packet_sha256':packet['packet_sha256'],
        'model_proposal_sha256':sha256_json(proposal.model_dump(mode='json')),
        'bound_proposal_sha256':sha256_json(bound.model_dump(mode='json')),
        'program_added_retirement_ids':added, 'observation_content_unchanged':True,
        'requires_independent_review':True}


def validate_observation_proposal(proposal,*,claims,components,approved_revisions,existing_supports=(),viewpoint_ids=None):
    findings=[];claimed=set();claim_index={c.claim_id:c for c in claims}
    component_index={c.claim_component_key:c for c in components}
    eligible={(c.target_viewpoint_revision_id,c.source_id) for c in components
              if c.target_viewpoint_revision_id in approved_revisions
              and c.disposition not in {'tension_existing','deferred','no_registry_assertion'}}
    eligible.update((revision,o['source_id']) for o in existing_supports for revision,vid in (viewpoint_ids or {}).items()
                    if revision in approved_revisions and vid==o['viewpoint_id'])
    for o in proposal.observation_supports:
        if (o.viewpoint_revision_id,o.source_id) not in eligible:
            findings.append(f'{o.local_observation_key}: observation has no current positive CVP source connection')
        claimed.add((o.viewpoint_revision_id,o.source_id))
        for b in o.evidence_bindings:
            component=component_index.get(b.claim_component_key)
            claim=claim_index.get(component.claim_id) if component else None
            if (claim is None or not observation_claim_is_usable(claim)
                    or claim.source_id!=o.source_id or component.source_id!=o.source_id
                    or not any(e.evidence_step_id==b.evidence_step_id and e.source_fragment_id==b.source_fragment_id
                               and e.source_id==o.source_id and route_evidence_is_usable(e) for e in claim.evidence)):
                owners=[(c.claim_id,e.evidence_step_id) for c in claims if c.source_id==o.source_id
                        for e in c.evidence if e.source_fragment_id==b.source_fragment_id]
                findings.append(f'{o.local_observation_key}: observation evidence is not an eligible source-local Claim pair; '
                    f'requested_component={b.claim_component_key}, evidence_step_id={b.evidence_step_id}, '
                    f'source_fragment_id={b.source_fragment_id}; fragment_registry_pairs={owners}')
    absent={(s.viewpoint_revision_id,s.source_id) for s in proposal.sources_without_observation}
    if claimed & absent:findings.append('source both has and lacks observations')
    if claimed|absent != eligible:findings.append('observation source coverage differs from complete positive source roster')
    old_ids={s['observation_support_id'] for s in existing_supports}
    if not set(proposal.retired_observation_support_ids)<=old_ids:
        findings.append('observation retirement is outside the current baseline')
    return findings


def compile_observation_package(proposal,packet,*,review_sha256):
    old={s['observation_support_id']:s for s in packet.get('existing_observation_supports',[])}
    keeping=set();created=[]
    for o in proposal.observation_supports:
        payload=observation_payload(o,packet);ident=observation_id(payload)
        if ident in keeping:raise ValueError('duplicate observation support content')
        keeping.add(ident)
        if ident in old:
            expected={k:old[ident][k] for k in payload}
            if expected!=payload:raise ValueError('observation identity content mismatch')
            continue
        created.append(ViewpointObservationSupportRecord.model_validate(payload | {
            'observation_support_id':ident,'review_artifact_sha256':review_sha256,
            'review_status':'system_approved'}).model_dump(mode='json'))
    retired=set(proposal.retired_observation_support_ids)
    if keeping & retired or set(old)-keeping!=retired:
        raise ValueError('complete observation set must retain or explicitly retire every old support; '
            f'missing_retirements={sorted(set(old)-keeping-retired)}; '
            f'still_kept_but_retired={sorted(keeping & retired)}; '
            f'unknown_retirements={sorted(retired-set(old))}. '
            'An edited observation has a new content identity even when its local key is unchanged; '
            'explicitly retire its old record if replacing it. Removing a draft row alone does not retire it '
            'or restore its original content. When the review asks to keep the original observation, '
            'update the changed draft key back to its original content and evidence; '
            'do not remove it or retire the original record.')
    return created,[(COLLECTION,i) for i in sorted(retired)],sorted(keeping & old.keys())


def validate_observation_graph(collections, *, current_ids=None):
    from .knowledge_models import evidence_fragment_ids
    findings=[]
    for ident,o in collections.get(COLLECTION,{}).items():
        if o.get('effective_state')!='active':continue
        # An unchanged approved record pins a historical source generation.
        # Upstream management can advance its inputs; consumers must then stop
        # using the stale observation until this CVP's worker revalidates it.
        # Every incoming observation and every runtime projection is strict.
        if current_ids is not None and ident not in current_ids:continue
        view=collections.get('canonical_viewpoints',{}).get(o['viewpoint_id'])
        if not view or view.get('current_revision_id')!=o['validated_against_viewpoint_revision_id']:
            findings.append(f'{ident}: observation CVP revision is stale or missing')
        source=collections.get('source_documents',{}).get(o['source_id'])
        if not source or source.get('source_sha256')!=o['source_revision_sha256']:
            findings.append(f'{ident}: observation source revision is stale or missing')
        for b in o['evidence_bindings']:
            claim=collections.get('claims',{}).get(b['claim_id'])
            step=collections.get('evidence_steps',{}).get(b['evidence_step_id'])
            fragment=collections.get('source_fragments',{}).get(b['source_fragment_id'])
            if (not claim or not observation_claim_is_usable(claim) or claim['revision']!=b['pinned_claim_revision']
                    or semantic_record_sha(claim)!=b['claim_revision_sha256']
                    or b['evidence_step_id'] not in claim.get('evidence_step_ids',[]) or not step
                    or b['source_fragment_id'] not in evidence_fragment_ids(step) or not fragment
                    or fragment.get('source_id')!=o['source_id']
                    or fragment.get('source_sha256')!=o['source_revision_sha256']
                    or step.get('support_eligibility') not in {'eligible','eligible_with_label','eligible_candidate'}
                    or fragment.get('anchor_state') not in {'source_version_bound','canonical_citation_bound','verified','valid'}):
                findings.append(f'{ident}: observation has stale, foreign or unusable evidence binding')
    return findings


def observation_current_findings(observation,index):
    """Check only this support's dependencies for runtime/admin freshness."""
    def dump(value):return value.model_dump(mode='json') if hasattr(value,'model_dump') else value
    o=dump(observation);scoped={COLLECTION:{o['observation_support_id']:o}}
    keys=[('canonical_viewpoints',o['viewpoint_id']),('source_documents',o['source_id'])]
    keys.extend((col,ident) for b in o['evidence_bindings'] for col,ident in (
        ('claims',b['claim_id']),('evidence_steps',b['evidence_step_id']),('source_fragments',b['source_fragment_id'])))
    for col,ident in keys:
        value=index.get(col,{}).get(ident)
        if value:scoped.setdefault(col,{})[ident]=dump(value)
    return validate_observation_graph(scoped)
