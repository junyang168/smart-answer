"""Extend only transaction pins around an unchanged, already reviewed Claim plan.

No candidate, content approval, original materials or model slots are rewritten.
"""
from copy import deepcopy
from pathlib import Path
import json

from backend.api.canonical_repository.postgres_store import canonical_json, sha256_json, POSTGRES_APPLY_ADVISORY_LOCK_KEY
from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_actions import validate_claim_management_package
from backend.pipeline.cvp_sql_flow import check_binding, decoded_pins, pins, pin_rows
from backend.pipeline.source_reconciliation_runner import write

VERSION = 'wang_claim_transaction_readset_recovery_v1'


def extend_reviewed_readset(store, run_id, *, authorization):
    check_binding(authorization)
    direction = json.loads(Path(authorization['path']).read_text())
    if (direction.get('schema_version') != VERSION or direction.get('run_id') != run_id
            or direction.get('authorized_by') != 'owner' or not direction.get('authorization_quote')
            or direction.get('human_content_approval') is not False):
        raise ValueError('exact owner direction required for reviewed readset recovery')
    saved = store.get_cvp_group(run_id)
    if not saved or saved.get('kind') != 'claim_management_round':
        raise ValueError('not a reviewed Claim round')
    package = saved['reviewed']['evidence']['package']['payload']
    validate_claim_management_package(package)
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (POSTGRES_APPLY_ADVISORY_LOCK_KEY,))
        value = cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s FOR UPDATE', (run_id,)).fetchone()[0]
        history = value.setdefault('transaction_readset_recoveries', [])
        if history and history[-1]['authorization'] == authorization:
            return history[-1]['new_reviewed_sha256']
        if (value['checkpoint'] != 'reviewed' or value.get('change_set_ids') or not value.get('opus_approved')
                or direction.get('reviewed_sha256') != value['reviewed_sha256']
                or sha256_json(value['reviewed']) != value['reviewed_sha256']
                or value['reviewed']['plan']['source_kind'] != cm.VERSION):
            raise ValueError('readset recovery requires exact uncommitted approved Claim plan')
        original = deepcopy(value['reviewed'])
        old_pins = decoded_pins(original['evidence']['read_pins'])
        observed = store._assert_transaction_read_pins(cursor, old_pins)
        roots = [tuple(k) for k in original['evidence']['claim_management_scope']['consumer_roots']]
        affected = store._affected_cvp_context(conn, roots)
        new_pins = {**old_pins, **pins(affected)}
        if any(new_pins[k] != v for k,v in old_pins.items()):
            raise ValueError('original reviewed read pin changed; cannot refresh stale content approval')
        expanded = store._assert_transaction_read_pins(cursor, new_pins)
        materials = value['materials']
        request = cm.Request.model_validate(materials['unit_request'])
        candidate = cm.Candidate.model_validate(value['candidate'])
        states = {cm.source.key(*k):cm.model_state(v) for k,v in expanded.items()}
        if (cm.audit_keys(request, states, candidate) != cm.audit_keys(request, materials['before'], candidate)
                or any(states.get(k) != v for k,v in materials['before'].items())):
            raise ValueError('added consumers change the content review denominator or facts; fresh review required')
        revised = deepcopy(original)
        revised['evidence']['read_pins'] = pin_rows(new_pins)
        digest = sha256_json(revised)
        record = {'authorization':authorization, 'old_reviewed_sha256':value['reviewed_sha256'],
            'old_reviewed':original, 'new_reviewed_sha256':digest,
            'added_read_keys':[list(k) for k in sorted(set(new_pins)-set(old_pins))],
            'candidate_unchanged':True, 'model_approval_unchanged':True, 'model_calls':0}
        write(Path(materials['audit_contract']['instructions']['path']).parent / ('readset-recovery-'+digest+'.json'), record)
        history.append(record)
        value.update(reviewed=revised,reviewed_sha256=digest)
        cursor.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb,heartbeat_at=now(),error_message=NULL WHERE run_id=%s',
            (canonical_json(value),run_id))
    return digest
