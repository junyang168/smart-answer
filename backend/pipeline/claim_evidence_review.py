"""Own bound Evidence sufficiency is distinct from whole-source fidelity.

A small opt-in addition to the existing Opus response, not another audit or
qualification store. Old policies and saved answers retain their exact shape.
"""
import json
from pathlib import Path
from typing import Literal

from pydantic import Field
from backend.pipeline import claim_management as cm
from backend.pipeline.claim_model_output import FindingsReview

CONTRACT = 'claim_own_evidence_v1'
QUESTIONS = {'own_evidence_coverage', 'quotation_attribution'}


class BindingAssessment(cm.source.Contract):
    claim_key: str
    claim_use: Literal['professor_judgment', 'quotation_report', 'editorial_inference', 'research_question']
    sufficient: bool
    evidence_keys: list[str]
    reason: str = Field(min_length=1, description='Check ALL material components against the actually bound excerpts; distinguish quoted content from professor interpretation.')


class BindingFindingsReview(FindingsReview):
    binding_assessments: list[BindingAssessment]


def enabled(value):
    if isinstance(value, cm.Request):
        if not Path(value.policy.path).is_file():
            return False
        value = json.loads(cm.source.verify_file(value.policy).read_text())
    return value.get('evidence_review_contract') == CONTRACT


def evidence_table(request, before, candidate=None, *, content_keys=None):
    from backend.pipeline.claim_direct_scope import scope
    rows = {k: v['payload'] for k, v in before.items() if not v['retired']}
    if candidate:
        rows.update(cm.source.index(candidate.records))
    content = content_keys if content_keys is not None else scope(request, before, candidate)['content_claim_keys']
    table = {}
    retired = {d.record_key for d in candidate.dispositions if d.action == 'retire'} if candidate else set()
    for key in content:
        if key in retired:
            continue  # Reviewed retirement reasons replace active evidence qualification.
        claim = rows[key]
        steps = {}
        for eid in claim.get('evidence_step_ids', []):
            ek = cm.source.key('evidence_steps', eid)
            e = rows[ek]
            fragments = set(e.get('source_fragment_ids') or [])
            if e.get('source_fragment_id'):
                fragments.add(e['source_fragment_id'])
            steps[ek] = {
                'reciprocal_owner': key.split('/', 1)[1] in e.get('produced_claim_ids', []),
                'support_eligibility': e.get('support_eligibility'),
                'speaker': e.get('speaker'), 'stance': e.get('stance'),
                'fragment_keys': sorted(cm.source.key('source_fragments', fid) for fid in fragments),
            }
        table[key] = steps
    return table


def add_packet(packet, candidate=None):
    if not enabled(packet.get('policy', {})):
        return packet
    # The direct-scope projection already contains all maintenance owners and
    # is recomputed for candidate postimages by the ordinary review packet.
    content = packet['claim_scope']['content_claim_keys']
    packet['own_evidence_bindings'] = evidence_table(None, packet['before'], candidate, content_keys=content)
    if packet['policy'].get('review_unit_contract') and len(packet['own_evidence_bindings']) > 10:
        raise ValueError('active review unit exceeds ten Claims; split the unit before review')
    return packet


def validate_assessments(review, materials, candidate):
    request = cm.Request.model_validate(materials['unit_request'])
    table = evidence_table(request, materials['before'], candidate)
    assessments = review.binding_assessments
    keys = [a.claim_key for a in assessments]
    if len(keys) != len(set(keys)) or set(keys) != set(table):
        raise ValueError('own Evidence assessment must cover each content Claim exactly once')
    pending = cm.review_findings(request.action_id, materials['unresolved'], candidate)
    rejected = {f.target_key for f in review.findings}
    rejected.update(pending[r.finding_id]['target_key'] for r in review.prior_findings
                    if r.disposition != 'resolved' and r.finding_id in pending)
    rows = {k: v['payload'] for k, v in materials['before'].items() if not v['retired']}
    rows.update(cm.source.index(candidate.records))
    for a in assessments:
        steps = table[a.claim_key]
        listed = set(a.evidence_keys)
        cited_steps = listed & set(steps)
        # A reviewer may explicitly name a step's already-bound excerpts as
        # well as the step. These are redundant references, not extra evidence.
        # Bare fragments and fragments from an uncited/foreign step stay out.
        cited_fragments = {f for key in cited_steps for f in steps[key]['fragment_keys']}
        if len(a.evidence_keys) != len(listed) or listed - cited_steps - cited_fragments:
            raise ValueError('own Evidence assessment borrowed non-owned Evidence or a whole paragraph')
        for key in cited_steps:
            e = steps[key]
            if not e['reciprocal_owner'] or not e['fragment_keys']:
                raise ValueError('own Evidence assessment lacks reciprocal owner or bound fragments')
            own = cm.own_source_ids(rows[a.claim_key], rows)
            if any(f not in rows or rows[f]['source_id'] not in own for f in e['fragment_keys']):
                raise ValueError('own Evidence assessment uses missing or foreign source fragments')
        if a.sufficient:
            if not cited_steps:
                raise ValueError('sufficient own Evidence assessment requires bound Evidence')
            if a.claim_use == 'professor_judgment' and not any(
                    steps[k]['support_eligibility'] in {'eligible', 'eligible_candidate'}
                    for k in cited_steps):
                raise ValueError('professor judgment cannot be supported only by context/ineligible Evidence')
        else:
            bound = set(steps) | {f for step in steps.values() for f in step['fragment_keys']}
            # A binding objection belongs on the exact bound excerpt too.
            # Unrelated observations, Questions and neighbour excerpts do not
            # explain a gap in this Claim's own Evidence.
            if a.claim_key not in rejected and not rejected & bound:
                raise ValueError('insufficient own Evidence requires a concrete Claim finding')
