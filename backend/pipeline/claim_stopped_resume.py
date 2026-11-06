"""Resume an explicitly stopped call with remaining budget and pinned migrations.

No stopped answer is reusable approval. The closed call stays spent; only
applied, code-only source locator migrations may change the saved input.
"""
from copy import deepcopy
import json
from pathlib import Path

from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_commit import binding, check_binding
from backend.pipeline.source_reconciliation_runner import body, read
from backend.pipeline.subscription_process import digest_file


def authorize(previous, request, authority):
    proof = authority.get('owner_stopped_resume')
    if proof is None:
        return None
    fields = {'predecessor_action_id', 'prior_metadata_sha256', 'authorization_quote',
              'slot', 'stop_receipt', 'saved_files', 'current_objects_sha256', 'migrations'}
    if (not isinstance(proof, dict) or set(proof) != fields
            or proof['predecessor_action_id'] != previous['action_id']
            or proof['prior_metadata_sha256'] != cm.sha256_json(previous)
            or not proof['authorization_quote'].strip()
            or authority.get('authorized_by') != 'owner'
            or authority.get('human_content_approval') is not False
            or previous['result'].get('reason') != 'model_failed'
            or previous.get('continued_by') or previous.get('created_keys')
            or previous.get('replacements') or authority.get('owner_call_extension')
            or authority.get('human_source_verification') or authority.get('subscription_refusal_retry')
            or request.predecessor_action_id != previous['action_id']):
        raise ValueError('stopped-call resume requires exact owner authority and original paused action')
    check_binding(proof['stop_receipt'])
    stop = json.loads(Path(proof['stop_receipt']['path']).read_text())
    slot = previous['call_reservations'].get(proof['slot'], {})
    stage = Path(previous['execution_root']) / slot.get('round_id', '')
    transport = stage / 'gpt.transport'
    expected_files = {name: binding(stage / name) for name in (
        'materials.json', 'gpt.packet.json', 'gpt.reservation.json', 'gpt.failure.json',
        'gpt.transport/invocation.json', 'gpt.transport/exit.json',
        'gpt.transport/stdout.log', 'gpt.transport/stderr.log', 'gpt.transport/response-schema.json')}
    if proof['saved_files'] != expected_files:
        raise ValueError('stopped-call saved files drifted')
    intent = body(read(stage / 'gpt.reservation.json'))
    packet = body(read(stage / 'gpt.packet.json'))
    material = body(read(stage / 'materials.json'))
    failure = body(read(stage / 'gpt.failure.json'))
    receipt = json.loads((transport / 'exit.json').read_text())
    invocation = json.loads((transport / 'invocation.json').read_text())
    if (slot.get('role') != 'gpt' or slot.get('state') != 'started'
            or previous['policy']['models']['gpt']['backend'] != 'codex_subscription'
            or proof['slot'] != slot.get('round_id', '') + ':gpt'
            or stop.get('action_id') != previous['action_id']
            or stop.get('gpt_terminated_by_owner_stop') is not True
            or stop.get('gpt_answer_received') is not False
            or stop.get('gpt_started_consumption_preserved') is not True
            or stop.get('gpt_exit') != expected_files['gpt.transport/exit.json']
            or intent != slot.get('intent') or cm.sha256_json(intent) != slot.get('intent_sha256')
            or packet != cm.model_packet(material)
            or cm.sha256_json(packet) != intent['request_payload_sha256']
            or receipt.get('returncode') != -15 or receipt.get('error_type')
            or invocation.get('cwd') != str(transport)
            or receipt.get('files') != {n: digest_file(transport / n) for n in ('stdout.log', 'stderr.log')}
            or failure.get('automatic_retry') is not False
            or failure.get('transport_directory') != str(transport)
            or any(failure.get('provider_raw', {}).get(n[:-4]) != (transport / n).read_text()
                   for n in ('stdout.log', 'stderr.log'))
            or (stage / 'gpt.raw.json').exists() or (transport / 'response.json').exists()):
        raise ValueError('stopped-call resume requires a closed owner-terminated call without a complete answer')
    events = [json.loads(line) for line in (transport / 'stdout.log').read_text().splitlines() if line.strip()]
    if any(e.get('type') == 'turn.completed' or (e.get('type') == 'item.completed'
           and e.get('item', {}).get('type') == 'agent_message') for e in events):
        raise ValueError('stopped-call resume cannot discard a returned answer')
    return deepcopy(proof)


def verify_migrations(previous, current, proof, histories):
    """Replay real committed transitions, never grant semantic equivalence."""
    if proof['current_objects_sha256'] != cm.sha256_json(current):
        raise ValueError('stopped-call current input drifted')
    if set(histories) != set(proof['migrations']):
        raise ValueError('stopped-call migration set differs')
    from backend.pipeline.transcript_evidence_apply import VERSION as EVIDENCE_VERSION
    from backend.pipeline.transcript_claim_anchor_sync import VERSION as ANCHOR_VERSION
    state = deepcopy(previous['original_goal']['original_objects'])
    for cid, digest in proof['migrations'].items():
        history = histories[cid]
        if (not history or cm.sha256_json(history) != digest or history['status'] != 'applied'
                or history['source_kind'] not in {EVIDENCE_VERSION, ANCHOR_VERSION}
                or history.get('review_events')):
            raise ValueError('stopped-call handoff requires exact applied locator migration history')
        for op in history['operations']:
            key = cm.source.key(op['collection'], op['object_id'])
            if key not in state:
                continue
            old = state[key]
            if (op['operation'] != 'update' or old['retired']
                    or (old['revision'], old['content_sha256'], old['payload'])
                       != (op['before_revision'], op['before_sha256'], op['before'])):
                raise ValueError('stopped-call migration does not chain from the original input: ' + key)
            state[key] = {'revision': op['after_revision'], 'content_sha256': op['after_sha256'],
                          'payload': op['after'], 'retired': False}
    if any(current.get(k) != v for k, v in state.items()):
        raise ValueError('stopped-call input changed outside the pinned locator migrations')


def historical_replacement_keys(request, before, reachable, *, _seen=frozenset()):
    """Old grants remain dormant when all replacement slices are reachable.

    Admission alone grants no calls: the store must subsequently authenticate
    the stopped predecessor, remaining budget and complete migration chain.
    """
    authority = json.loads(cm.source.verify_file(request.authorization).read_text())
    proof = authority.get('owner_stopped_resume')
    if not proof:
        parent_bound = authority.get('predecessor_request')
        if parent_bound:
            if request.action_id in _seen:
                raise ValueError('historical replacement request cycle')
            check_binding(parent_bound)
            parent = cm.Request.model_validate_json(Path(parent_bound['path']).read_text())
            if (request.predecessor_action_id != parent.action_id
                    or parent.operation != request.operation
                    or parent.target_keys != request.target_keys
                    or parent.source_bindings != request.source_bindings
                    or parent.permissions != request.permissions
                    or required_maintenance(parent) != authority.get('required_maintenance_keys')):
                raise ValueError('historical replacement relay changed original scope')
            return historical_replacement_keys(parent, before, reachable, _seen=_seen | {request.action_id})
        return set()
    bound = proof['saved_files']['materials.json']; check_binding(bound)
    material = body(read(Path(bound['path'])))
    old = cm.Request.model_validate(material['unit_request'])
    if (old.action_id != request.predecessor_action_id
            or old.target_keys != request.target_keys or old.source_bindings != request.source_bindings
            or old.permissions != request.permissions
            or material['original_goal'].get('required_maintenance_keys')
               != authority.get('required_maintenance_keys')):
        raise ValueError('replacement references must retain the original stopped request scope')
    result = set()
    for p in request.permissions:
        if p.collection != 'source_fragments' or p.key not in before or p.key not in material['before']:
            continue
        f = before[p.key]['payload']; ids = f.get('replacement_fragment_ids') or []
        if (not before[p.key]['retired'] and f.get('anchor_state') == 'replaced_by_precise_fragments'
                and ids and all(cm.source.key('source_fragments', fid) in reachable for fid in ids)):
            result.add(p.key)
    return result


def required_maintenance(request):
    return json.loads(cm.source.verify_file(request.authorization).read_text()).get('required_maintenance_keys')
