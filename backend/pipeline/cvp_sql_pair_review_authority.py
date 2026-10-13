"""Consume one committed pair-addition approval before a locator-only step.

This is a fixed two-step contract, not an ancestor walker. A unique SQL Claim
review approves the unchanged meaning; the sealed, committed #364 independent
pair decisions approve ONLY added forward bindings. The outer locator validator
still proves its complete current postimage and physical source. No snapshots,
new review events, model calls or Registry writes are produced here.
"""
from backend.api.canonical_repository.postgres_store import canonical_json, record_content_sha, sha256_json

KIND = 'wkp364_claim_evidence_pair_adjudication'


def pair_history(store, change_set_id, cid):
    """Read bounded approval metadata, never the embedded old whole-store guard."""
    with store.connect() as conn:
        row = conn.execute("""SELECT status,source_kind,fingerprint_sha256,source_sha256,
            jsonb_build_object('claim_evidence_reciprocity_repair',jsonb_build_object(
              'pair_adjudication_authorization_sha256',metadata->'claim_evidence_reciprocity_repair'->'pair_adjudication_authorization_sha256',
              'authorization',metadata->'claim_evidence_reciprocity_repair'->'store_guard'->'expected_pair_adjudication_authorization'))
            FROM wang_knowledge.change_sets WHERE change_set_id=%s AND source_kind=%s""",
            (change_set_id,KIND)).fetchone()
        if not row:
            return None
        ops = conn.execute("""SELECT operation,before_revision,after_revision,before_sha256,after_sha256
            FROM wang_knowledge.change_operations WHERE change_set_id=%s AND collection='claims' AND object_id=%s""",
            (change_set_id,cid)).fetchall()
        operations = []
        for op in ops:
            versions = conn.execute("""SELECT revision,payload FROM wang_knowledge.object_versions
                WHERE collection='claims' AND object_id=%s AND revision IN (%s,%s)""",
                (cid,op[1],op[2])).fetchall()
            values = dict(versions)
            operations.append(dict(zip(('operation','before_revision','after_revision','before_sha256','after_sha256'),op),
                collection='claims',object_id=cid,before=values.get(op[1]),after=values.get(op[2])))
    return dict(zip(('status','source_kind','fingerprint_sha256','source_sha256','metadata'),row),operations=operations)


def pair_review(store, cid, previous, history, source, index, locator_operations):
    from backend.pipeline.source_contract_cleanup import migrate_source_fragment

    def require(value, reason):
        if not value:
            raise ValueError('pair Claim review authority failed: '+cid+': '+reason)

    require(history['source_kind']==KIND and history['status']=='applied', 'uncommitted/foreign pair step')
    meta = history['metadata']['claim_evidence_reciprocity_repair']
    auth = meta.get('authorization') or {}
    require(len(canonical_json(auth).encode())<=2*1024**2, 'bounded authorization exceeded')
    require(auth.get('schema_version')=='wang_claim_evidence_pair_repair_authorization_v1'
            and auth.get('policy')=='downstream_claim_projection_with_independent_pair_review_v1'
            and auth.get('artifact_sha256')==meta.get('pair_adjudication_authorization_sha256')
            ==sha256_json({k:v for k,v in auth.items() if k!='artifact_sha256'}), 'pair authorization seal drift')
    require(history.get('source_sha256')==sha256_json({
        'schema_version':'wang_claim_evidence_pair_repair_plan_v1',
        'audit_artifact_sha256':auth.get('audit_artifact_sha256'),
        'authorization_artifact_sha256':auth['artifact_sha256'],
        'actions_sha256':sha256_json(auth.get('actions'))}), 'pair source/authorization binding differs')
    require(previous[0]=='KCS-'+history.get('fingerprint_sha256','')[:20], 'pair commit identity differs')
    operations = history['operations']
    require(len(operations)==1, 'missing/ambiguous Claim operation')
    op = operations[0]; before,after = op.get('before'),op.get('after')
    require(before and after and op['operation']=='update'
            and op['after_revision']==op['before_revision']+1==after['revision']
            and op['before_revision']==before['revision']
            and op['before_sha256']==record_content_sha(before)
            and op['after_sha256']==previous[1]==record_content_sha(after) and after==previous[2], 'pair version drift')
    require(before.get('review_status')==after.get('review_status')=='ai_consensus_reviewed'
            and {k:v for k,v in before.items() if k not in {'revision','evidence_step_ids'}}
            =={k:v for k,v in after.items() if k not in {'revision','evidence_step_ids'}}, 'pair changed Claim meaning/status')
    original = before.get('evidence_step_ids',[]); additions = after.get('evidence_step_ids',[])[len(original):]
    require(original and additions and len(set(original+additions))==len(original+additions)
            and after['evidence_step_ids']==original+sorted(additions), 'not exact append-only bindings')
    actions = [a for a in auth.get('actions',[]) if a.get('claim_id')==cid]
    require(len(actions)==len(additions) and {a.get('evidence_step_id') for a in actions}==set(additions)
            and len({a.get('pair_id') for a in actions})==len(actions), 'pair decision coverage differs')
    for a in actions:
        require(a.get('mismatch_type')=='evidence_only' and a.get('reviewed_decision')==a.get('effective_decision')=='include'
                and a.get('decision_basis')=='independent_model_consensus_include'
                and a.get('claim_review_status')=='ai_consensus_reviewed'
                and a.get('claim_revision')==before['revision'] and a.get('claim_content_sha256')==op['before_sha256'],
                'pair decision unresolved/foreign')
    with store.connect() as conn:
        base = conn.execute("""SELECT change_set_id,content_sha256,payload,recorded_at
            FROM wang_knowledge.object_versions WHERE collection='claims' AND object_id=%s AND revision=%s""",
            (cid,before['revision'])).fetchone()
        committed = conn.execute("""SELECT status,metadata->>'review_event_id' FROM wang_knowledge.change_sets
            WHERE change_set_id=%s""",(base[0],)).fetchone() if base else None
        events = conn.execute("""SELECT review_event_id,reviewer_kind,decision,artifact
            FROM wang_knowledge.review_events WHERE collection='claims' AND object_id=%s AND object_revision=%s""",
            (cid,before['revision'])).fetchall()
        pair_time = conn.execute("""SELECT recorded_at FROM wang_knowledge.object_versions
            WHERE collection='claims' AND object_id=%s AND revision=%s""",(cid,after['revision'])).fetchone()
    require(base and base[1]==op['before_sha256'] and base[2]==before and committed and committed[0]=='applied'
            and len(events)==1 and events[0][0]==committed[1] and events[0][1] in {'ai','human','system'}
            and events[0][2]=='ai_consensus_reviewed' and events[0][3]
            and events[0][3].get('change_set_id')==base[0] and pair_time, 'exact pre-pair SQL review missing')
    # Each Evidence and fragment must be the exact committed generation at the
    # pair decision, unchanged except this outer, proven locator-only operation.
    locator = {(o['collection'],o['object_id']):o for o in locator_operations}
    pins = []
    def dependency(collection, oid, *, previously_reviewed=False):
        with store.connect() as conn:
            old = conn.execute("""SELECT revision,content_sha256,payload FROM wang_knowledge.object_versions
                WHERE collection=%s AND object_id=%s AND recorded_at<=%s ORDER BY revision DESC LIMIT 1""",
                (collection,oid,pair_time[0])).fetchone()
        require(old and old[1]==record_content_sha(old[2]), 'missing exact pair dependency')
        if previously_reviewed:
            with store.connect() as conn:
                reviewed = conn.execute("""SELECT revision,content_sha256,payload FROM wang_knowledge.object_versions
                    WHERE collection=%s AND object_id=%s AND recorded_at<=%s ORDER BY revision DESC LIMIT 1""",
                    (collection,oid,base[3])).fetchone()
            require(reviewed==old, 'original reviewed dependency changed before pair step')
        expected = old[2]
        move = locator.get((collection,oid))
        if move:
            require(collection=='source_fragments' and move['operation']=='update'
                    and move['before_revision']==old[0] and move['before_sha256']==old[1] and move['before']==expected,
                    'dependency is not a direct locator step')
            migrated,resolution = migrate_source_fragment(expected,index,source_sha256=source['source_body_sha256'])
            require(migrated and resolution.status=='resolved'
                    and canonical_json(dict(migrated,revision=move['after_revision']))==canonical_json(move['after']),
                    'dependency locator postimage differs')
            expected = move['after']
        live = store.get_record_state(collection,oid)
        require(live and not live.get('retired') and live['revision']==expected['revision']
                and live['content_sha256']==record_content_sha(expected)==record_content_sha(live['payload'])
                and live['payload']==expected, 'current dependency drift/retirement')
        pins.append({'collection':collection,'object_id':oid,'revision':live['revision'],'content_sha256':live['content_sha256']})
        return old,live['payload']
    action_by_evidence = {a['evidence_step_id']:a for a in actions}
    for eid in after['evidence_step_ids']:
        old,evidence = dependency('evidence_steps',eid,previously_reviewed=eid in original)
        require(evidence.get('support_eligibility') in {'eligible','eligible_candidate'}
                and cid in evidence.get('produced_claim_ids',[]), 'own Evidence held or not reciprocal')
        if eid in action_by_evidence:
            a = action_by_evidence[eid]
            require((a.get('evidence_revision'),a.get('evidence_content_sha256'))==(old[0],old[1]), 'approved Evidence pin differs')
        fragments = set(evidence.get('source_fragment_ids') or [])
        if evidence.get('source_fragment_id'):fragments.add(evidence['source_fragment_id'])
        require(fragments, 'own Evidence lacks fragments')
        for fid in sorted(fragments):
            old,fragment = dependency('source_fragments',fid,previously_reviewed=eid in original)
            require(old[2].get('source_sha256') in {source.get('source_file_sha256'),source['source_body_sha256']}
                    and old[2].get('source_sha256') and old[2].get('anchor_state')=='source_version_bound'
                    and fragment.get('source_id')==source['source_id']
                    and fragment.get('review_status') not in {'human_hold','human_review_required','withheld_unreviewed','rejected','superseded'},
                    'foreign/changed/held Evidence source')
    return {'review_event_id':events[0][0],'reviewer_kind':events[0][1],'decision':events[0][2],
        'review_artifact_sha256':sha256_json(events[0][3]),'pair_change_set_id':previous[0],
        'pair_authorization_sha256':auth['artifact_sha256'],'pre_pair_reviewed_revision':before['revision'],
        'current_dependency_pins':pins}
