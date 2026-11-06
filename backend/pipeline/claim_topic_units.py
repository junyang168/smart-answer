"""Explicit disjoint topic boundaries for the existing Claim Update runner.

The Agent chooses topics. This module verifies a frozen partition; it does not
discover topics, schedule work, shorten SQL readsets or approve content.
"""
import json
from pathlib import Path

FIELD = 'disjoint_topic_unit'
CONTRACT = 'claim_disjoint_topic_units_v1'


def unit(request):
    from backend.pipeline import claim_management as cm
    bound = getattr(request, 'authorization', None)
    if bound is None or bound.file_sha256 == '0' * 64:
        return None
    authority = json.loads(cm.source.verify_file(bound).read_text())
    selected = authority.get(FIELD)
    if selected is None:
        return None
    if set(selected) != {'plan', 'unit_id'}:
        raise ValueError('topic unit requires an exact plan binding and unit ID')
    plan = json.loads(cm.source.verify_file(cm.source.FileBinding.model_validate(selected['plan'])).read_text())
    validate_plan(plan)
    matches = [u for u in plan['units'] if u['unit_id'] == selected['unit_id']]
    if len(matches) != 1:
        raise ValueError('unknown topic unit')
    result = dict(matches[0], source_id=plan['source_id'], source_file=plan['source_file'],
                  source_body_sha256=plan['source_body_sha256'])
    if (request.operation != 'Update' or set(request.source_bindings) != {plan['source_id']}
            or request.source_bindings[plan['source_id']].model_dump() != plan['source_file']
            or not set(request.target_keys) <= {'claims/' + c for c in result['claim_ids']}
            or not {p.key for p in request.permissions} <= set(result['record_keys'])):
        raise ValueError('request exceeds its frozen topic unit')
    return result


def validate_plan(plan):
    if (plan.get('schema_version') != CONTRACT or not plan.get('authorization_quote')
            or not plan.get('source_id') or not plan.get('source_body_sha256')
            or len(plan.get('units', [])) < 2):
        raise ValueError('explicit owner-authorized disjoint topic plan required')
    ids, claims, paragraphs, records = set(), set(), set(), set()
    for u in plan['units']:
        if (not u.get('unit_id') or u['unit_id'] in ids or not u.get('title')
                or not u.get('claim_ids') or not u.get('paragraph_keys') or not u.get('record_keys')):
            raise ValueError('topic units require distinct identities and complete boundaries')
        for field, seen in [('claim_ids', claims), ('paragraph_keys', paragraphs), ('record_keys', records)]:
            values = u[field]
            if len(values) != len(set(values)) or seen & set(values):
                raise ValueError('topic units overlap: ' + field)
            seen.update(values)
        if (not {'claims/' + c for c in u['claim_ids']} <= set(u['record_keys'])
                or any(k.startswith('source_documents/') or '/' not in k for k in u['record_keys'])
                or not set(u.get('legacy_fragment_keys', [])) <= set(u['record_keys'])):
            raise ValueError('topic record boundary is incomplete')
        ids.add(u['unit_id'])


def validate_preparation(selection, targets, groups, creation_count):
    """Reject overfilled or misplaced work before reserving SQL identities."""
    from backend.pipeline import claim_management as cm
    if set(selection) != {'plan', 'unit_id'}:
        raise ValueError('topic preparation requires an exact bound selection')
    plan = json.loads(cm.source.verify_file(cm.source.FileBinding.model_validate(selection['plan'])).read_text())
    validate_plan(plan)
    matches = [u for u in plan['units'] if u['unit_id'] == selection['unit_id']]
    if len(matches) != 1 or not set(targets) <= set(matches[0]['claim_ids']):
        raise ValueError('preparation targets exceed the selected topic')
    if any(len(g) + creation_count > 10 for g in groups):
        raise ValueError('topic groups must reserve review space for their allowed new Claims: targets + creation_count <= 10')


def project_sources(request, sources):
    selected = unit(request)
    if selected is None:
        return
    snapshot = sources[selected['source_id']]
    paragraphs = selected['paragraph_keys']
    if (snapshot['body_sha256'] != selected['source_body_sha256']
            or not set(paragraphs) <= set(snapshot['paragraphs'])
            or snapshot.get('visual_sources')):
        raise ValueError('topic source drift or unsupported visual boundary')
    # Keep the real physical source binding and paragraph identities. Never
    # fabricate a split sermon or renumber its source paragraphs.
    snapshot['paragraphs'] = {key: snapshot['paragraphs'][key] for key in paragraphs}
    if 'human_source_decisions' in snapshot:
        snapshot['human_source_decisions'] = human_decisions(request, snapshot['human_source_decisions'])
    verification = snapshot.get('human_source_verification', {}).get('record', {})
    if any(answer['citation']['paragraph_key'] not in paragraphs for answer in verification.get('answers', [])):
        raise ValueError('human clarification crosses a topic source boundary')
    snapshot['topic_boundary'] = {'contract': CONTRACT, 'unit_id': selected['unit_id'],
        'title': selected['title'], 'complete_topic_context': True,
        'whole_sermon_context': False}


def assert_records(request, before, candidate=None):
    from backend.pipeline import claim_management as cm
    selected = unit(request)
    if selected is None:
        return
    allowed = set(selected['record_keys']) | {'source_documents/' + selected['source_id']}
    if set(before) - allowed:
        raise ValueError('topic context expanded beyond its disjoint record boundary')
    paragraphs = set(selected['paragraph_keys'])
    legacy = set(selected.get('legacy_fragment_keys', []))
    proposed = cm.source.index(candidate.records) if candidate else {}
    for key, state in before.items():
        payload = proposed.get(key, state['payload'])
        if key.startswith('source_fragments/'):
            if payload.get('source_id') != selected['source_id']:
                raise ValueError('topic contains a foreign source fragment')
            if key not in legacy or (key in proposed and payload != state['payload']):
                if payload.get('paragraph_key') not in paragraphs:
                    raise ValueError('topic fragment lies outside its original paragraphs')
    if candidate:
        # Known records cannot be imported through a fresh candidate. Ordinary
        # creation validation still proves which new identities may be added.
        foreign = (set(proposed) & set(selected['record_keys'])) - set(before)
        if foreign:
            raise ValueError('candidate imported unreviewed topic context')
        for key, payload in proposed.items():
            if key in legacy and key in before and payload == before[key]['payload']:
                continue
            if key.startswith('source_fragments/') and (payload.get('source_id') != selected['source_id']
                    or payload.get('paragraph_key') not in paragraphs):
                raise ValueError('new topic fragment lies outside its original paragraphs')
        # A known object from a different topic must never be smuggled into a
        # new relation, owner list or question answer through an ID reference.
        plan_bound = json.loads(cm.source.verify_file(request.authorization).read_text())[FIELD]['plan']
        plan = json.loads(Path(plan_bound['path']).read_text())
        foreign_ids = {k.split('/', 1)[1] for u in plan['units'] if u['unit_id'] != selected['unit_id']
                       for k in u['record_keys']}
        if any(foreign_ids & set(cm.source.strings(p)) for p in proposed.values()):
            raise ValueError('candidate crosses a different topic unit')


def resources(request):
    selected = unit(request)
    if selected is None:
        return None
    sid = selected['source_id']
    # Read/read overlap is deliberately excluded for this owner-selected trial.
    return (set(selected['record_keys'])
            | {'claim_source_paragraph/' + sid + '/' + key for key in selected['paragraph_keys']}
            | {'claim_topic_execution/' + sid + '/' + selected['unit_id']})


def human_decisions(request, decisions):
    """An independent topic neither reads nor inherits another topic's facts."""
    if request is None:
        return decisions
    if isinstance(request, dict):
        from backend.pipeline import claim_management as cm
        request = cm.Request.model_validate(request)
    selected = unit(request)
    if selected is None:
        return decisions
    paragraphs = set(selected['paragraph_keys'])
    return [value for value in decisions if value['source_id'] == selected['source_id']
            and set(value['paragraph_sha256']) <= paragraphs]
