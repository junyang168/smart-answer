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
RETAINED_BINDING_DEFECT = 'retained_own_source_binding_false_completion_v1'


def repair_extension(previous, request, policy, authority):
    """One existing-run engineering pair for a proven, completed no-write defect.

    This cannot replay committed data or turn the invalid old vote into approval.
    The ordinary successor independently reviews a newly proposed full postimage.
    """
    repair = authority.get('own_source_binding_repair')
    if repair is None:
        return None
    import hashlib
    from backend.pipeline.claim_metadata_projection import repair_defects
    from backend.pipeline.cvp_sql_commit import binding, check_binding
    from backend.pipeline.source_reconciliation_runner import body, read
    old = cm.Request.model_validate(previous['request'])
    original = cm.Policy.model_validate(previous['policy'])
    fields = {'defect', 'predecessor_action_id', 'prior_metadata_sha256', 'round_id',
              'fixed_commit', 'fixed_parent', 'implementation', 'test_evidence', 'saved_inputs'}
    if (not isinstance(repair, dict) or set(repair) != fields
            or repair['defect'] != RETAINED_BINDING_DEFECT
            or RETAINED_BINDING_DEFECT in repair_defects(previous)
            or repair['predecessor_action_id'] != old.action_id
            or repair['prior_metadata_sha256'] != cm.sha256_json(previous)
            or previous['result']['status'] != 'completed'
            or previous.get('created_keys') or previous.get('replacements')
            or len(previous['latest_rounds']) != 1
            or repair['round_id'] not in previous['latest_rounds'].values()
            or request.model_dump(exclude={'action_id','predecessor_action_id','authorization','policy'})
                != old.model_dump(exclude={'action_id','predecessor_action_id','authorization','policy'})
            or policy.model_dump(exclude={'policy_id','initial_candidate','budget'})
                != original.model_dump(exclude={'policy_id','initial_candidate','budget'})
            or policy.initial_candidate != 'proposal' or policy.independent_audit_required
            or policy.budget.gpt_calls != 1 or policy.budget.opus_calls != 1
            or policy.budget.gemini_calls or policy.budget.max_revisions != 1
            or any(not isinstance(repair[k], str) or len(repair[k]) != 40
                   or any(c not in '0123456789abcdef' for c in repair[k])
                   for k in ('fixed_commit','fixed_parent'))):
        raise ValueError('invalid one-time retained source binding engineering repair')
    root = Path(previous['execution_root']) / repair['round_id']
    names = ['materials','candidate','opus.raw','readback','package']
    if any(s['round_id'] == repair['round_id'] and s['role'] == 'gpt' and s['state'] == 'completed'
           for s in previous['call_reservations'].values()):
        names.append('gpt.raw')
    expected_inputs = {name: binding(root / (name + '.json')) for name in names}
    if repair['saved_inputs'] != expected_inputs:
        raise ValueError('retained binding repair saved input drift')
    for bound in expected_inputs.values():
        check_binding(bound)
    material = body(read(root / 'materials.json'))
    candidate = cm.Candidate.model_validate(body(read(root / 'candidate.json')))
    if (candidate.records or any(d.action != 'retain' for d in candidate.dispositions)
            or material['before'] != previous['original_goal']['original_objects']
            or material['unit_request']['target_keys'] != old.target_keys
            or not enabled(material['policy'])):
        raise ValueError('retained binding repair requires unchanged SQL before and an all-retain candidate')
    rows = {k:s['payload'] for k,s in material['before'].items() if not s['retired']}
    try:
        assert_current_fragments(rows, material['sources'], old.target_keys)
    except ValueError:
        pass
    else:
        raise ValueError('retained binding repair requires a reproducible actual source binding defect')
    paths = ('backend/pipeline/claim_evidence_review.py','backend/pipeline/claim_model_output.py',
             'backend/pipeline/claim_management.py','backend/pipeline/claim_review_reuse.py',
             'backend/api/canonical_repository/postgres_store.py')
    repo = Path(__file__).resolve().parents[2]
    implementation = {p:hashlib.sha256((repo/p).read_bytes()).hexdigest() for p in paths}
    report = json.loads(cm.source.verify_file(cm.source.FileBinding.model_validate(repair['test_evidence'])).read_text())
    if (repair['implementation'] != implementation or report.get('implementation') != implementation
            or report.get('failed') != 0 or not report.get('passed',0) > 0
            or report.get('defect') != RETAINED_BINDING_DEFECT):
        raise ValueError('retained binding repair requires passing fixed-implementation tests')
    return deepcopy(repair)


def freeze_initial_review_reuse(store, request, *, initial_round_id=None):
    """An explicit successor may reuse individual passes, never an operation pass."""
    from backend.pipeline.cvp_sql_commit import binding, check_binding
    direction = json.loads(cm.source.verify_file(request.authorization).read_text())
    rid = initial_round_id or direction.get('reuse_initial_review_round')
    origin = request.action_id if initial_round_id else request.predecessor_action_id
    lineage = []
    expected_child = request.action_id
    if rid is None:
        if direction.get('reuse_initial_claim_assessments') is not True:
            return None
        eligible, visited = [], set()
        origin = request.action_id
        while origin:
            if origin in visited:
                raise ValueError('initial review continuation cycle')
            visited.add(origin)
            parent = store.get_cvp_group('CMA-' + cm.sha256_json(origin))
            if not parent:
                if origin != request.action_id:
                    raise ValueError('initial review predecessor is missing')
                origin = request.predecessor_action_id
                continue
            prior_request = cm.Request.model_validate(parent['request'])
            if (prior_request.target_keys != request.target_keys
                    or prior_request.source_bindings != request.source_bindings
                    or prior_request.operation != 'Update'
                    or parent.get('continued_by') not in {None, 'CMA-' + cm.sha256_json(expected_child)}):
                raise ValueError('initial review continuation changed its exact source/targets or forked')
            for slot in parent.get('call_reservations', {}).values():
                if slot['role'] != 'opus' or slot['state'] != 'completed':
                    continue
                prior = store.get_cvp_group(slot['round_id'])
                if (prior['materials']['round_number'] == 0 and prior['materials']['roles'] == ['opus']
                        and prior.get('candidate') and reusable_initial_candidate(prior['materials'],
                            cm.Candidate.model_validate(prior['candidate']))):
                    eligible.append(slot['round_id'])
            if eligible:
                break
            if origin != request.action_id:
                authority = json.loads(cm.source.verify_file(prior_request.authorization).read_text())
                if authority['request'] != prior_request.model_dump(mode='json', exclude={'authorization', 'policy'}):
                    raise ValueError('initial review predecessor authority drift')
                lineage.append(prior_request.authorization.model_dump(mode='json'))
            expected_child = origin
            origin = prior_request.predecessor_action_id
        if not eligible:
            return None
        if len(eligible) != 1:
            raise ValueError('initial reuse requires one fixed original review unit')
        rid = eligible[0]
    previous = store.get_cvp_group('CMA-' + cm.sha256_json(origin or ''))
    old = store.get_cvp_group(rid)
    if (request.operation != 'Update' or not previous or not old or old.get('kind') != 'claim_management_round'
            or old.get('parent_run_id') != previous['run_id']
            or previous.get('continued_by') not in {None, 'CMA-' + cm.sha256_json(expected_child)}
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
    if lineage:
        proof['predecessor_authorizations'] = lineage
    material, candidate, _ = _initial_review(proof)
    if material != old['materials'] or candidate.model_dump(mode='json') != old['candidate']:
        raise ValueError('initial assessment reuse SQL and saved review disagree')
    return proof


def reusable_initial_candidate(materials, candidate):
    """Old unchanged frames, or exactly proved new initial quote calibration."""
    if not candidate.records and all(d.action == 'retain' for d in candidate.dispositions):
        return True
    from backend.pipeline.claim_quote_projection import SPAN_CONTRACT, initial_candidate
    if materials['policy'].get('source_quote_projection') != SPAN_CONTRACT:
        return False
    expected, receipt = initial_candidate(materials)
    return bool(receipt.get('initial_quote_preflight') and expected == candidate)


def _saved_initial_stage(root, material, packet):
    """Authenticate a historical call with its own frozen prompt bytes.

    The sidecar restores SHA-bound resources only. It cannot change a packet,
    schema, reservation, answer, budget or vote; ordinary wire validation and
    all present-day content checks still run.
    """
    from backend.pipeline.cvp_sql_actions import validate_claim_stage
    policy=cm.Policy.model_validate(material['policy'])
    path=root/'historical-role-prompts.json'
    if not path.exists():return validate_claim_stage(root,'opus',policy,packet)
    from backend.pipeline.cvp_sql_commit import binding,check_binding
    from backend.pipeline.source_reconciliation_runner import validate_bound_call
    from backend.pipeline.source_reconciliation_runtime import claim_management_model_adapter
    value=json.loads(path.read_text());prompts={}
    if (set(value)!={'contract','round_id','raw','prompts'}
            or value['contract']!='claim_historical_role_prompts_v1'
            or value['round_id']!=material['round_id']
            or value['raw']!=binding(root/'opus.raw.json')):
        raise ValueError('historical initial prompt restoration identity drift')
    for name,bound in value['prompts'].items():
        expected=material['audit_contract']['implementation'].get('backend/pipeline/prompts/'+name)
        if not expected or '/' in name or bound['file_sha256']!=expected:
            raise ValueError('historical prompt differs from the original frozen contract')
        check_binding(bound);prompts[name]=Path(bound['path']).read_text()
    return validate_bound_call(root,'opus',packet,
        claim_management_model_adapter('opus',policy,saved_prompts=prompts))


def _initial_review(proof):
    from backend.pipeline.cvp_sql_commit import check_binding
    from backend.pipeline.source_reconciliation_runner import body, read
    from backend.pipeline.cvp_sql_actions import claim_management_review_packet, validate_claim_stage
    from backend.pipeline.claim_model_output import review as expand_review
    if set(proof) - {'predecessor_authorizations'} != {'contract', 'review_action_id', 'round_id', 'receipt', 'materials', 'candidate', 'raw'} or proof['contract'] != INITIAL_REUSE:
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
            or not reusable_initial_candidate(material, candidate)):
        raise ValueError('initial reuse requires the exact unchanged or program-calibrated existing-candidate review')
    root = Path(proof['materials']['path']).parent
    if Path(proof['raw']['path']) != root / 'opus.raw.json':
        raise ValueError('initial assessment raw belongs to a different review')
    packet = claim_management_review_packet(material, candidate)
    raw = _saved_initial_stage(root, material, packet)
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
    old, initial, (wire, review) = _initial_review(proof)
    request = cm.Request.model_validate(materials['unit_request'])
    origin = request.predecessor_action_id
    visited = set()
    for bound in proof.get('predecessor_authorizations', []):
        authority = json.loads(cm.source.verify_file(cm.source.FileBinding.model_validate(bound)).read_text())
        prior = authority.get('request', {})
        if (not origin or origin in visited or prior.get('action_id') != origin
                or prior.get('target_keys') != request.target_keys
                or prior.get('source_bindings') != request.model_dump(mode='json')['source_bindings']
                or prior.get('operation') != 'Update'):
            raise ValueError('initial review continuation authority chain drift')
        visited.add(origin)
        origin = prior.get('predecessor_action_id')
    allowed = {origin} if proof.get('predecessor_authorizations') else {request.action_id, request.predecessor_action_id}
    if (proof['review_action_id'] not in allowed
            or request.target_keys != old['unit_request']['target_keys']
            or request.model_dump(mode='json')['source_bindings'] != old['unit_request']['source_bindings']
            or {sid: {k:v for k,v in value.items() if k != 'human_source_decisions'} for sid,value in materials['sources'].items()}
                != {sid: {k:v for k,v in value.items() if k != 'human_source_decisions'} for sid,value in old['sources'].items()}):
        raise ValueError('initial assessment reuse changed its original targets or source evidence')
    if materials['sources'] != old['sources']:
        # A newly recorded human clarification invalidates the cached passes,
        # not the lawful successor. Review every target on the current facts.
        return {}
    approved = {v.record_key for v in review.verdicts if v.approved and not v.problems}
    original = deepcopy(old['before'])
    original.update({row.key: {'payload': row.payload, 'retired': False} for row in initial.records})
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
        from backend.pipeline.claim_direct_scope import owner_context_enabled
        if owner_context_enabled(request):
            original_own = cm.Request.model_validate(old['unit_request']).model_copy(
                deep=True, update={'target_keys': [key], 'permissions': []})
            left = set(scope(original_own, original, include_write_context=False)['record_keys'])
            right = set(scope(own, current, include_write_context=False)['record_keys'])
        else:
            # Frozen older wires declared their fresh assessment set using
            # this exact projection; never reinterpret a saved approval.
            left = set(scope(own, original)['record_keys'])
            right = set(scope(own, current)['record_keys'])
        if left != right or not left <= approved:
            continue
        if any(f['target_key'] in left or key in (f.get('affected_target_keys') or []) for f in issues):
            continue
        if any(original[k]['retired'] != current[k]['retired']
               or {n:v for n,v in original[k]['payload'].items() if n != 'revision'}
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
    if packet['policy'].get('review_unit_contract'):
        from backend.pipeline.claim_review_units import review_capacity, CAPACITY_EXCEPTION
        maximum = review_capacity(materials, candidate, packet['own_evidence_bindings'])
        if len(packet['own_evidence_bindings']) > maximum:
            raise ValueError('active review unit exceeds ten Claims; split the unit before review')
        if maximum != 10:
            packet.setdefault('program_owned_fields', {})[CAPACITY_EXCEPTION] = {
                'authorization': materials[CAPACITY_EXCEPTION], 'maximum_claims': maximum,
                'instruction': 'Owner authorized this exact saved group to exceed the default capacity. '
                    'This approves capacity only, not the statements or modifications. Independently assess every active Claim '
                    'and its own evidence normally. No extra model calls, permissions or source paragraphs are granted.'}
    return packet


def assert_current_fragments(rows, sources, claim_keys):
    """Check actual own bindings, including retains; previous drafts prove nothing.

    A source-wide hash may change outside this Claim's evidence. The exact
    paragraph and literal excerpt must still exist in the bound current source.
    Neighbours' private evidence is intentionally outside this check.
    """
    for key in claim_keys:
        own = cm.own_source_ids(rows[key], rows)
        for eid in rows[key].get('evidence_step_ids', []):
            step = rows[cm.source.key('evidence_steps', eid)]
            fragments = set(step.get('source_fragment_ids') or [])
            if step.get('source_fragment_id'):
                fragments.add(step['source_fragment_id'])
            for fid in fragments:
                fragment = rows.get(cm.source.key('source_fragments', fid), {})
                sid = fragment.get('source_id')
                paragraph = sources.get(sid, {}).get('paragraphs', {}).get(str(fragment.get('paragraph_key')), {})
                excerpt = fragment.get('verbatim_excerpt')
                if (sid not in own or not isinstance(excerpt, str) or not excerpt.strip()
                        or excerpt not in paragraph.get('text', '')):
                    raise ValueError('own Evidence lacks an exact current source paragraph/excerpt: '
                                     + key + ' -> ' + fid)


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
            # A failing review must still reach GPT with its concrete repair
            # findings. Only an otherwise-positive own-basis assessment may
            # establish a passing candidate; it cannot approve stale retains.
            bound = {a.claim_key} | set(steps) | {f for e in steps.values() for f in e['fragment_keys']}
            if not rejected & bound:
                assert_current_fragments(rows, materials['sources'], [a.claim_key])
        else:
            bound = set(steps) | {f for step in steps.values() for f in step['fragment_keys']}
            # A binding objection belongs on the exact bound excerpt too.
            # Unrelated observations, Questions and neighbour excerpts do not
            # explain a gap in this Claim's own Evidence.
            if a.claim_key not in rejected and not rejected & bound:
                raise ValueError('insufficient own Evidence requires a concrete Claim finding')
