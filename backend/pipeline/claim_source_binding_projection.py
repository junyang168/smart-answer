"""Derive binding metadata from exact current fragments, never eligibility."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

CONTRACT = 'claim_source_binding_projection_v1'
FIELD = 'saved_source_binding_metadata_repair'
FRAGMENT_FIELDS = {'source_binding_status', 'deleted_in_source_sha256'}


def enabled(materials):
    if not materials: return False
    if materials.get(FIELD): return True
    from backend.pipeline import claim_management as cm
    authority = json.loads(cm.source.verify_file(cm.Request.model_validate(materials['request']).authorization).read_text())
    return authority.get('source_binding_projection') == CONTRACT


def bound(fragment, sources):
    snapshot = sources.get(fragment.get('source_id'), {})
    text = snapshot.get('paragraphs', {}).get(fragment.get('paragraph_key'), {}).get('text', '')
    quote = fragment.get('verbatim_excerpt')
    digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
    return bool(quote and quote in text and fragment.get('anchor_state') == 'source_version_bound'
        and fragment.get('source_sha256') == snapshot.get('body_sha256')
        and fragment.get('paragraph_text_sha256') == digest(text)
        and fragment.get('verbatim_excerpt_sha256') == digest(quote))


def evidence_bound(evidence, rows, sources):
    ids = set(evidence.get('source_fragment_ids') or [])
    if evidence.get('source_fragment_id'): ids.add(evidence['source_fragment_id'])
    return bool(ids) and all(bound(rows.get('source_fragments/' + fid, {}), sources) for fid in ids)


def canonical_fields(key, payload, rows, sources):
    if key.startswith('source_fragments/') and bound(payload, sources):
        return {'source_binding_status': 'source_version_bound', 'deleted_in_source_sha256': None}
    if key.startswith('evidence_steps/') and evidence_bound(payload, rows, sources):
        return {'source_binding_status': 'source_version_bound'}
    return {}


def project(candidate, receipt, materials, *, force=False):
    from backend.pipeline import claim_management as cm
    if not (force or enabled(materials)):
        return candidate, receipt
    result = candidate.model_copy(deep=True)
    rows = {k:v['payload'] for k,v in materials['before'].items()}
    rows.update(cm.source.index(result.records))
    actions = {d.record_key:d.action for d in result.dispositions}
    permissions = {p['collection']+'/'+p['object_id'] for p in materials['unit_request']['permissions']}
    present = {r.key for r in result.records}
    # First review also calibrates an unchanged, writable record when its
    # deletion marker contradicts an already exact current binding. Pending
    # review flags alone do not create extra writes.
    for key, state in sorted(materials['before'].items()):
        payload = rows[key]
        if (actions.get(key) != 'retain' or key not in permissions or state.get('retired')
                or payload.get('review_status') in cm.HELD
                or not (payload.get('source_binding_status') in {'source_deleted','partially_deleted','source_text_missing'}
                        or payload.get('deleted_in_source_sha256'))
                or not canonical_fields(key, payload, rows, materials['sources'])):
            continue
        if key not in present:
            col, oid = cm.source.split_key(key)
            result.records.append(cm.source.Row(collection=col,object_id=oid,payload=deepcopy(payload)))
            present.add(key)
        own = payload if key.startswith('source_fragments/') else rows['source_fragments/'+(
            payload.get('source_fragment_id') or payload['source_fragment_ids'][0])]
        disposition = next(d for d in result.dispositions if d.record_key == key)
        disposition.action = 'update'
        disposition.reason = 'Program-only calibration of contradictory deletion metadata from the exact current source; no teaching or eligibility change.'
        disposition.citations = [cm.Citation(source_id=own['source_id'],paragraph_key=own['paragraph_key'],exact_quote=own['verbatim_excerpt'])]
        actions[key] = 'update'
    corrections = []
    for row in sorted(result.records, key=lambda r: not r.key.startswith('source_fragments/')):
        if actions[row.key] not in {'create', 'update'}: continue
        values = canonical_fields(row.key, row.payload, rows, materials['sources'])
        for field, value in values.items():
            if field not in row.payload or row.payload[field] == value: continue
            corrections.append(dict(record_key=row.key, field=field, before=deepcopy(row.payload[field]), after=value))
            if value is None: row.payload.pop(field)
            else: row.payload[field] = value
        rows[row.key] = row.payload
    if corrections:
        receipt = {**receipt, 'candidate_sha256':result.digest, 'source_binding_status_corrections':corrections}
    return result, receipt


def round_repair(parent, ordinal):
    from backend.pipeline.cvp_sql_commit import check_binding
    from backend.pipeline.source_reconciliation_runner import body, read
    selected = parent.get(FIELD)
    if selected is None or selected['next_round'] != ordinal: return None
    check_binding(selected['proof'])
    proof = body(read(Path(selected['proof']['path'])))
    if (proof['contract'] != CONTRACT or proof['request'] != parent['request']
            or proof['next_round'] != ordinal or proof['new_budget'] is not False
            or proof['review_approval_reused'] is not False):
        raise ValueError('saved source-binding repair changed its original action or budget')
    return selected


def initial_candidate(materials):
    from backend.pipeline import claim_management as cm
    from backend.pipeline.cvp_sql_commit import check_binding
    from backend.pipeline.source_reconciliation_runner import body, read
    selected = materials[FIELD]; check_binding(selected['proof'])
    proof = body(read(Path(selected['proof']['path'])))
    for name in ('original_materials', 'original_candidate', 'original_normalization'):
        check_binding(proof[name])
    original = body(read(Path(proof['original_materials']['path'])))
    if (materials['request'] != proof['request'] or materials['round_number'] != proof['next_round']
            or materials['unit_request'] != original['unit_request']
            or materials['before'] != original['before'] or materials['sources'] != original['sources']):
        raise ValueError('saved source-binding repair changed source, authority or content basis')
    old = cm.Candidate.model_validate(body(read(Path(proof['original_candidate']['path']))))
    receipt = body(read(Path(proof['original_normalization']['path'])))
    fixed, receipt = project(old, receipt, original, force=True)
    if fixed.digest != proof['corrected_candidate_sha256'] or receipt['source_binding_status_corrections'] != proof['corrections']:
        raise ValueError('saved source-binding repair changed beyond canonical binding metadata')
    return fixed, receipt


def resume_saved_metadata(store, round_id, *, fixed_commit, test_evidence):
    """Reserve no new budget; send a mechanically corrected saved GPT to Opus.

    The accepted prior report stays unchanged in its archive. Its disagreement
    is not turned into approval; a fresh full Opus review is still mandatory.
    """
    from backend.pipeline import claim_management as cm
    from backend.pipeline.cvp_sql_actions import validate_claim_stage
    from backend.pipeline.cvp_sql_commit import binding, check_binding, pins
    from backend.pipeline.source_reconciliation_runner import body, read, write
    from backend.api.canonical_repository.postgres_store import POSTGRES_APPLY_ADVISORY_LOCK_KEY, canonical_json
    check_binding(test_evidence)
    test = json.loads(Path(test_evidence['path']).read_text())
    if test.get('failed') != 0 or test.get('passed', 0) < 1 or test.get('fixed_commit') != fixed_commit:
        raise ValueError('binding metadata repair requires passing fixed-code tests')
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (POSTGRES_APPLY_ADVISORY_LOCK_KEY,))
        value = cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s FOR UPDATE', (round_id,)).fetchone()[0]
        parent = cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s FOR UPDATE', (value['parent_run_id'],)).fetchone()[0]
        if parent.get(FIELD):
            return parent[FIELD]
        if (parent['result'].get('reason') != 'disagreement' or value.get('outcome') != 'disagreement'
                or value.get('change_set_ids') or parent.get('created_keys') or parent.get('continued_by')
                or list(parent['latest_rounds'].values()) != [round_id]
                or set(value.get('stage_results', {})) != {'candidate', 'opus'}
                or any(v['state'] not in {'completed','planned','cancelled'} for v in parent['call_reservations'].values())
                or any(parent['call_reservations'].get(round_id+':'+role,{}).get('state') != 'completed' for role in ('gpt','opus'))):
            raise ValueError('binding metadata repair requires its exact accepted uncommitted model stages')
        old_root = Path(value.get('recovery_root', Path(parent['execution_root']) / round_id))
        materials = value['materials']; policy = cm.Policy.model_validate(materials['policy'])
        review_packet = body(read(old_root/'opus.packet.json'))
        raw = validate_claim_stage(old_root, 'opus', policy, review_packet)['response']
        validate_claim_stage(old_root, 'gpt', policy, cm.model_packet(materials))
        if raw.get('findings') or not raw.get('binding_assessments') or any(not a['sufficient'] for a in raw['binding_assessments']):
            raise ValueError('saved metadata repair cannot bypass a content or evidence rejection')
        persistent = [r for r in raw['prior_findings'] if r['disposition'] != 'resolved']
        if not persistent or any('source_binding_status' not in r['reason'] for r in persistent):
            raise ValueError('remaining review issue is not the proven binding metadata defect')
        request = cm.Request.model_validate(parent['request'])
        _, sources = cm.load_authority(request, source_documents=cm.source_documents(materials['before']),
            human_source_decisions=materials.get('human_source_decisions'))
        if sources != materials['sources']:
            raise ValueError('saved metadata repair source drift')
        store._assert_transaction_read_pins(cursor, pins({cm.source.split_key(k):v for k,v in materials['transaction_before'].items()}))
        old = cm.Candidate.model_validate(value['candidate'])
        original_receipt = body(read(old_root/'candidate-normalization.json'))
        fixed, receipt = project(old, original_receipt, materials, force=True)
        corrections = receipt.get('source_binding_status_corrections', [])
        affected = {c['record_key'] for c in corrections}
        if not corrections or any(f['target_key'] not in affected for f in parent['findings'].values()):
            raise ValueError('saved metadata repair does not cover the remaining findings')
        cm.validate_candidate(request, materials['before'], fixed, sources,
            created_keys=materials['created_keys'], original_replacements=materials['replacements'],
            metadata_projection=materials.get('program_metadata_projection'))
        if (sum(v['role']=='opus' and v['state'] != 'cancelled' for v in parent['call_reservations'].values()) >= policy.budget.opus_calls
                or sum(bool(v) for v in parent['revision_rounds'].values()) >= policy.budget.max_revisions):
            raise ValueError('original budget cannot fund the saved metadata review')
        root = old_root/'binding-status-repair-v1'; root.mkdir(exist_ok=True)
        write(root/'original-round.json', value); write(root/'original-parent.json', parent)
        record = dict(contract=CONTRACT, request=parent['request'], next_round=value['round_number']+1,
            unit_id=value['unit_id'], original_materials=binding(old_root/'materials.json'),
            original_candidate=binding(old_root/'candidate.json'), original_normalization=binding(old_root/'candidate-normalization.json'),
            original_round=binding(root/'original-round.json'), original_parent=binding(root/'original-parent.json'),
            original_gpt=binding(old_root/'gpt.raw.json'), original_opus=binding(old_root/'opus.raw.json'),
            corrected_candidate_sha256=fixed.digest, corrections=corrections,
            fixed_commit=fixed_commit, test_evidence=test_evidence,
            new_budget=False, review_approval_reused=False, new_gpt_calls=0)
        write(root/'repair.json',record)
        selected = dict(proof=binding(root/'repair.json'), next_round=record['next_round'])
        value.update(outcome='revision_required', binding_metadata_recovery=selected)
        parent.update(audit_contract=cm.audit_contract(root,policy), result={'status':'running'})
        parent[FIELD] = selected
        for rid, metadata in ((round_id,value),(value['parent_run_id'],parent)):
            cursor.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb,status='running',finished_at=NULL,heartbeat_at=now() WHERE run_id=%s",(canonical_json(metadata),rid))
        return selected
