"""Own bound Evidence sufficiency is distinct from whole-source fidelity.

A small opt-in addition to the existing Opus response, not another audit or
qualification store. Old policies and saved answers retain their exact shape.
"""
import json
from copy import deepcopy
from pathlib import Path
from typing import Literal

from pydantic import Field
from backend.pipeline import claim_management as cm
from backend.pipeline.claim_model_output import FindingsReview

CONTRACT = 'claim_own_evidence_v1'
QUESTIONS = {'own_evidence_coverage', 'quotation_attribution'}
INITIAL_REUSE = 'claim_initial_assessment_reuse_v1'


def freeze_initial_review_reuse(store, request, *, initial_round_id=None):
    """An explicit successor may reuse individual passes, never an operation pass."""
    from backend.pipeline.cvp_sql_commit import binding, check_binding
    direction = json.loads(cm.source.verify_file(request.authorization).read_text())
    rid = initial_round_id or direction.get('reuse_initial_review_round')
    origin = request.action_id if initial_round_id else request.predecessor_action_id
    if rid is None:
        if direction.get('reuse_initial_claim_assessments') is not True:
            return None
        origin = request.action_id
        parent = store.get_cvp_group('CMA-' + cm.sha256_json(origin))
        eligible = []
        for slot in (parent or {}).get('call_reservations', {}).values():
            if slot['role'] != 'opus' or slot['state'] != 'completed':
                continue
            prior = store.get_cvp_group(slot['round_id'])
            if (prior['materials']['round_number'] == 0 and prior['materials']['roles'] == ['opus']
                    and prior.get('candidate') and not prior['candidate']['records']):
                eligible.append(slot['round_id'])
        if not eligible:
            return None
        if len(eligible) != 1:
            raise ValueError('initial reuse requires one fixed original review unit')
        rid = eligible[0]
    previous = store.get_cvp_group('CMA-' + cm.sha256_json(origin or ''))
    old = store.get_cvp_group(rid)
    if (request.operation != 'Update' or not previous or not old or old.get('kind') != 'claim_management_round'
            or old.get('parent_run_id') != previous['run_id']
            or previous.get('continued_by') not in {None, 'CMA-' + cm.sha256_json(request.action_id)}
            or previous['request']['target_keys'] != request.target_keys
            or previous['request']['source_bindings'] != request.model_dump(mode='json')['source_bindings']
            or old['materials']['round_number'] != 0 or old['materials']['roles'] != ['opus']
            or old['materials']['request']['operation'] != 'Update'
            or not old['materials']['policy'].get('review_unit_contract')
            or old.get('change_set_ids') or 'opus' not in old.get('stage_results', {})):
        raise ValueError('initial assessment reuse requires the original accepted review of this exact predecessor')
    slot = previous['call_reservations'].get(rid + ':opus', {})
    if slot.get('state') != 'completed' or slot.get('result') != old['stage_results']['opus']:
        raise ValueError('initial assessment reuse cannot borrow an unaccepted or unknown review')
    check_binding(old['stage_results']['opus'])
    receipt = json.loads(Path(old['stage_results']['opus']['path']).read_text())
    proof = {'contract': INITIAL_REUSE, 'review_action_id': origin,
             'round_id': rid, 'receipt': old['stage_results']['opus'],
             'materials': receipt['materials'], 'candidate': receipt['candidate'],
             'raw': binding(Path(receipt['materials']['path']).parent / 'opus.raw.json')}
    material, candidate, _ = _initial_review(proof)
    if material != old['materials'] or candidate.model_dump(mode='json') != old['candidate']:
        raise ValueError('initial assessment reuse SQL and saved review disagree')
    return proof


def _initial_review(proof):
    from backend.pipeline.cvp_sql_commit import check_binding
    from backend.pipeline.source_reconciliation_runner import body, read
    from backend.pipeline.cvp_sql_actions import claim_management_review_packet, validate_claim_stage
    from backend.pipeline.claim_model_output import review as expand_review
    if set(proof) != {'contract', 'review_action_id', 'round_id', 'receipt', 'materials', 'candidate', 'raw'} or proof['contract'] != INITIAL_REUSE:
        raise ValueError('invalid initial assessment reuse proof')
    for key in ('receipt', 'materials', 'candidate', 'raw'):
        check_binding(proof[key])
    receipt = body(read(Path(proof['receipt']['path'])))
    material = body(read(Path(proof['materials']['path'])))
    candidate = cm.Candidate.model_validate(body(read(Path(proof['candidate']['path']))))
    if (receipt != {'round_id': proof['round_id'], 'stage': 'opus',
                    'materials': proof['materials'], 'candidate': proof['candidate']}
            or material['round_id'] != proof['round_id'] or material['round_number'] != 0
            or material['request']['action_id'] != proof['review_action_id']
            or material.get('initial_assessment_reuse') or material['roles'] != ['opus']
            or candidate.records or any(d.action != 'retain' for d in candidate.dispositions)):
        raise ValueError('initial reuse is limited to one unchanged existing-candidate review')
    root = Path(proof['materials']['path']).parent
    if Path(proof['raw']['path']) != root / 'opus.raw.json':
        raise ValueError('initial assessment raw belongs to a different review')
    packet = claim_management_review_packet(material, candidate)
    raw = validate_claim_stage(root, 'opus', cm.Policy.model_validate(material['policy']), packet)
    review = expand_review(raw['response'], material, candidate)
    cm.validate_review(candidate, review, packet['denominator'], packet['required_questions'],
                       material['unresolved'], material['sources'],
                       request=cm.Request.model_validate(material['unit_request']), records=material['before'])
    return material, candidate, (BindingFindingsReview.model_validate(raw['response']), review)


def reused_assessments(materials, candidate):
    """Exact own basis and direct context must survive; findings can reopen it."""
    proof = materials.get('initial_assessment_reuse')
    if not proof:
        return {}
    from backend.pipeline.claim_direct_scope import scope
    old, _, (wire, review) = _initial_review(proof)
    request = cm.Request.model_validate(materials['unit_request'])
    if (proof['review_action_id'] not in {request.action_id, request.predecessor_action_id}
            or request.target_keys != old['unit_request']['target_keys']
            or request.model_dump(mode='json')['source_bindings'] != old['unit_request']['source_bindings']
            or materials['sources'] != old['sources']):
        raise ValueError('initial assessment reuse changed its original targets or source evidence')
    approved = {v.record_key for v in review.verdicts if v.approved and not v.problems}
    current = deepcopy(materials['before'])
    current.update({row.key: {'payload': row.payload, 'retired': False} for row in candidate.records})
    retired = {d.record_key for d in candidate.dispositions if d.action == 'retire'}
    issues = list(materials['unresolved'].values()) + [f.model_dump(mode='json') for f in candidate.findings]
    reused = {}
    for assessment in wire.binding_assessments:
        key = assessment.claim_key
        if key not in request.target_keys or key in retired or not assessment.sufficient:
            continue
        own = request.model_copy(deep=True, update={'target_keys': [key], 'permissions': []})
        left = set(scope(own, old['before'])['record_keys'])
        right = set(scope(own, current)['record_keys'])
        if left != right or not left <= approved:
            continue
        if any(f['target_key'] in left or key in (f.get('affected_target_keys') or []) for f in issues):
            continue
        if any(old['before'][k]['retired'] != current[k]['retired']
               or {n:v for n,v in old['before'][k]['payload'].items() if n != 'revision'}
                  != {n:v for n,v in current[k]['payload'].items() if n != 'revision'} for k in left):
            continue
        reused[key] = assessment
    return reused


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


def add_packet(packet, candidate=None, *, materials=None):
    if not enabled(packet.get('policy', {})):
        return packet
    # The direct-scope projection already contains all maintenance owners and
    # is recomputed for candidate postimages by the ordinary review packet.
    content = packet['claim_scope']['content_claim_keys']
    packet['own_evidence_bindings'] = evidence_table(None, packet['before'], candidate, content_keys=content)
    if candidate is not None and materials is not None:
        reused = reused_assessments(materials, candidate)
        if reused:
            packet['initial_review_references'] = {
                'round_id': materials['initial_assessment_reuse']['round_id'],
                'claim_keys': sorted(reused),
                'instruction': 'These original targets already passed individual source/evidence review. '
                    'The program verified their entire own basis and direct context are unchanged. '
                    'Treat them as references, without another binding_assessment. '
                    'Report any newly discovered serious contradiction normally. '
                    'Only the remaining content_claim_keys require fresh individual assessments; '
                    'this reuse does not complete or approve the overall Update.'}
            packet['claim_scope']['content_claim_keys'] = [k for k in content if k not in reused]
            for key in reused:
                packet['own_evidence_bindings'].pop(key, None)
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
