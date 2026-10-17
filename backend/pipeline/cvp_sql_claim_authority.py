"""Bounded SQL authority for an already-applied, non-semantic locator step."""
from pathlib import Path

from backend.api.canonical_repository.postgres_store import (
    canonical_json, record_content_sha, sha256_json,
)


def coordinate_authority(store, cid, row):
    from backend.pipeline.cvp_sql_commit import check_binding, plan_from_document
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    from backend.api.canonical_repository.postgres_store import validate_change_set_plan_integrity
    from backend.pipeline.knowledge_source import markdown_source_document
    from backend.pipeline.source_contract_cleanup import build_projection, migrate_claim_occurrence_anchors

    def require(condition, reason):
        if not condition:
            raise ValueError('current Claim locator review authority failed: ' + cid + ': ' + reason)

    require(not row.get('retired') and row['revision'] > 1, 'no eligible predecessor')
    with store.connect() as conn:
        current = conn.execute("""SELECT change_set_id,content_sha256,payload FROM wang_knowledge.object_versions
            WHERE collection='claims' AND object_id=%s AND revision=%s""", (cid,row['revision'])).fetchone()
        previous = conn.execute("""SELECT change_set_id,content_sha256,payload FROM wang_knowledge.object_versions
            WHERE collection='claims' AND object_id=%s AND revision=%s""", (cid,row['revision']-1)).fetchone()
        events = conn.execute("""SELECT review_event_id,reviewer_kind,decision,artifact
            FROM wang_knowledge.review_events WHERE collection='claims' AND object_id=%s AND object_revision=%s
            ORDER BY review_event_id""", (cid,row['revision']-1)).fetchall()
    require(current and previous, 'missing exact SQL versions')
    require(current[1] == row['content_sha256'] == record_content_sha(current[2])
            and canonical_json(current[2]) == canonical_json(row['payload'])
            and previous[1] == record_content_sha(previous[2]), 'version payload/SHA drift')
    require(len(events) <= 1 and (not events or
            events[0][1] in {'ai','human','system'} and events[0][3]
            and events[0][2]==previous[2].get('review_status')), 'missing/ambiguous predecessor review')
    history = store.get_change_history(current[0])
    require(history and history['status'] == 'applied'
            and history['source_kind'] in {'wkp368_source_contract_cleanup','wkp368_source_duplicate_cleanup'},
            'not an applied locator cleanup')
    prior = None
    if not events:
        from backend.pipeline.cvp_sql_pair_review_authority import pair_history
        prior = pair_history(store, previous[0], cid)
    prior = prior or store.get_change_history(previous[0])
    require(prior and prior['status'] == 'applied', 'predecessor not committed')
    reviewed = [a for a in prior['operations'] if a['collection']=='claims' and a['object_id']==cid]
    review_ids = set(prior['metadata'].get('review_event_ids', []))
    review_ids.add(prior['metadata'].get('review_event_id'))
    require(len(reviewed)==1 and reviewed[0]['after_revision']==row['revision']-1
            and reviewed[0]['after_sha256']==previous[1] and reviewed[0]['after']==previous[2],
            'review is not bound to exact predecessor commit')
    if events:
        require(events[0][0] in review_ids
                and events[0][3].get('change_set_id',previous[0])==previous[0],
                'review is not bound to exact predecessor commit')
    metadata = history['metadata']
    plan_binding = {'path':metadata['source_plan_path'],'file_sha256':metadata['source_plan_file_sha256']}
    require(Path(plan_binding['path']).stat().st_size <= 2*1024**2, 'bounded source plan exceeded')
    check_binding(plan_binding)
    document = read_artifact(plan_binding['path'])
    duplicate = history['source_kind'] == 'wkp368_source_duplicate_cleanup'
    require(document.get('schema_version') == ('wang_source_duplicate_cleanup_plan_v1' if duplicate
            else 'wang_source_contract_cleanup_source_plan_v1')
            and document.get('artifact_sha256') == sha256_json({k:v for k,v in document.items() if k!='artifact_sha256'}),
            'missing exact source plan seal/schema')
    plan = plan_from_document(document['plan'])
    validate_change_set_plan_integrity(plan)
    require((plan.change_set_id,plan.fingerprint_sha256,plan.source_sha256,plan.source_kind) ==
            (current[0],history['fingerprint_sha256'],history['source_sha256'],history['source_kind']), 'plan/commit identity drift')
    planned = plan.as_dict()['operations']
    actual = history['operations']
    fields = ('operation','collection','object_id','before_revision','after_revision','before_sha256','after_sha256')
    require(len(planned) == len(actual) and all(all(p[k] == a[k] for k in fields)
            and canonical_json(dict(p['payload'],revision=p['after_revision'])) == canonical_json(a['after'])
            for p,a in zip(planned,actual)), 'complete applied operation binding drift')
    targets = [a for a in actual if a['collection']=='claims' and a['object_id']==cid]
    require(len(targets)==1, 'ambiguous target operation')
    target = targets[0]
    require(target['operation']=='update' and target['before_revision']==row['revision']-1
            and target['after_revision']==row['revision'] and target['before_sha256']==previous[1]
            and target['after_sha256']==current[1] and target['before']==previous[2]
            and target['after']==current[2], 'target versions not exact')
    if duplicate:
        # This historical duplicate-source transaction also moved ordinary
        # canonical-source Claim coordinates. Consume ONLY that locator step;
        # alias reassignment or source identity changes are not this authority.
        require(events, 'duplicate cleanup requires exact predecessor SQL review')
        occurrences = previous[2].get('occurrences', [])
        transcripts = {o.get('transcript_id') for o in occurrences}
        require(len(transcripts)==1 and None not in transcripts, 'no unique own transcript')
        groups = [g for g in document.get('groups',[]) if g.get('transcript_id') in transcripts]
        require(len(groups)==1 and groups[0].get('coordinate_ready') is True
                and not any(groups[0].get('coordinate_blockers',{}).values()),
                'duplicate cleanup coordinates not independently ready')
        group = groups[0]
        require(all(o.get('source_id') != group.get('alias_source_id') for o in occurrences),
                'source alias reassignment needs separate authority')
        materials = [m for m in document.get('frozen_sources',[]) if m.get('transcript_id') in transcripts]
        require(len(materials)==1, 'missing/ambiguous duplicate cleanup source')
        source = dict(materials[0],source_id=group['canonical_source_id'],source_type='sermon_transcript')
    else:
        source = document['source']
    require(source['source_type'] in {'notes_manuscript','sermon_transcript'}
            and Path(source['source_path']).is_absolute(), 'unsupported source')
    source_binding = {'path':source['source_path'],'file_sha256':source['source_file_sha256']}
    from backend.pipeline.source_editorial_transition import verify_current_source_binding
    # Keep historical review provenance stable; the verifier separately
    # proves the live file differs only by inserted editorial headings.
    verify_current_source_binding(source_binding)
    if source['source_type']=='notes_manuscript':
        script,_,_ = markdown_source_document(source)
    else:
        import json
        script = json.loads(Path(source['source_path']).read_bytes())
        script = {'script':script} if isinstance(script,list) else script
    index,projection = build_projection(script['script'])
    require(projection.body_sha256 == source['source_body_sha256'], 'physical body drift')
    locator_evidence_pins = []
    migrated,findings = migrate_claim_occurrence_anchors(previous[2], index,
        source_id=source['source_id'],transcript_id=source['transcript_id'])
    if migrated is None and findings and all(
            f['status'] in {'changed','coordinate_conflict','ambiguous_exact_text'} for f in findings):
        # Both historical cleanup kinds used the same pure locator compiler.
        # Later locator resolvers reject a short quote present in two body
        # rows. Resolve ONLY through that anchor's own already-committed
        # Evidence/fragment graph, never the desired Claim postimage or a hint
        # supplied by its current paragraph_key.
        from backend.pipeline.source_contract_cleanup import migrate_source_fragment
        import re
        locators = {}
        applied = {(op['collection'],op['object_id']):op for op in actual}
        for finding in findings:
            if finding['status']=='changed':continue
            match = re.fullmatch(r'occurrences\[(\d+)\]\.anchors\[(\d+)\]\.paragraph_key',finding['path'])
            require(match, 'ambiguous locator finding path')
            anchor = previous[2]['occurrences'][int(match[1])]['anchors'][int(match[2])]
            eid = anchor.get('evidence_id')
            require(eid in previous[2].get('evidence_step_ids',[]), 'anchor evidence is not owned by reviewed Claim')
            step = store.get_record_state('evidence_steps',eid)
            require(step and not step['retired'] and record_content_sha(step['payload'])==step['content_sha256']
                    and step['payload'].get('support_eligibility') in {'eligible','eligible_candidate'}
                    and cid in step['payload'].get('produced_claim_ids',[]) , 'own evidence unavailable or held')
            locator_evidence_pins.append({'collection':'evidence_steps','object_id':eid,
                'revision':step['revision'],'content_sha256':step['content_sha256']})
            fragment_ids = set(step['payload'].get('source_fragment_ids') or [])
            if step['payload'].get('source_fragment_id'):fragment_ids.add(step['payload']['source_fragment_id'])
            require(fragment_ids, 'own evidence lacks bound fragments')
            candidates = set()
            text = anchor.get('proposed_highlight',{}).get('text')
            require(text, 'empty ambiguous anchor text')
            for fid in sorted(fragment_ids):
                fragment = store.get_record_state('source_fragments',fid)
                op = applied.get(('source_fragments',fid))
                require(fragment and not fragment['retired'] and op and op['operation']=='update'
                        and fragment['revision']==op['after_revision']
                        and fragment['content_sha256']==op['after_sha256']==record_content_sha(fragment['payload'])
                        and canonical_json(fragment['payload'])==canonical_json(op['after'])
                        and op['before'].get('source_id')==op['after'].get('source_id')==source['source_id'],
                        'own fragment is not the exact canonical-source committed coordinate step')
                original_fragment = op['before']
                own_rows = [r for r in index.by_source_segment_index.get(
                    str(original_fragment.get('source_segment_index')),())
                    if original_fragment.get('verbatim_excerpt')
                    and original_fragment['verbatim_excerpt'] in r.text]
                require(len(own_rows)==1, 'original fragment source index is missing/ambiguous')
                moved,resolution = migrate_source_fragment(original_fragment,index,
                    source_sha256=projection.body_sha256,attested_locator=own_rows[0].locator)
                require(moved and resolution.status=='resolved'
                        and canonical_json(dict(moved,revision=op['after_revision']))==canonical_json(op['after']),
                        'own fragment full deterministic postimage differs')
                locator_evidence_pins.append({'collection':'source_fragments','object_id':fid,
                    'revision':fragment['revision'],'content_sha256':fragment['content_sha256'],
                    'original_source_segment_index':original_fragment['source_segment_index']})
                locator = fragment['payload'].get('paragraph_key')
                body = index.by_locator.get(locator)
                if body and text in body.text:candidates.add(locator)
            require(len(candidates)==1, 'own evidence cannot uniquely disambiguate anchor')
            locator = next(iter(candidates))
            require(eid not in locators or locators[eid]==locator, 'own evidence locator conflict')
            locators[eid] = locator
        migrated,findings = migrate_claim_occurrence_anchors(previous[2],index,
            source_id=source['source_id'],transcript_id=source['transcript_id'],
            anchor_locator_by_evidence_id=locators)
    require(migrated and findings and all(f['status']=='changed' for f in findings)
            and canonical_json(dict(migrated,revision=row['revision'])) == canonical_json(current[2]),
            'full deterministic locator postimage differs')
    if events:
        review_authority = {'review_event_id':events[0][0],'reviewer_kind':events[0][1],
            'decision':events[0][2],'review_artifact_sha256':sha256_json(events[0][3])}
    elif prior.get('source_kind') == 'wkp364_claim_evidence_pair_adjudication':
        from backend.pipeline.cvp_sql_pair_review_authority import pair_review
        review_authority = pair_review(store,cid,previous,prior,source,index,actual)
    elif prior.get('source_kind') == 'wkp368_absent_machine_anchor_cleanup':
        from backend.pipeline.cvp_sql_absent_anchor_authority import authority as absent_anchor_authority
        review_authority=absent_anchor_authority(store,cid,previous,prior,source,index,actual)
    else:
        from backend.pipeline.cvp_sql_imported_claim_review import imported_passing_review
        from backend.pipeline.source_contract_cleanup import migrate_source_fragment
        review_authority = (imported_passing_review(cid, previous, prior, source,store=store)
            if prior.get('source_kind')=='wkp353_relation_id_incident_recovery'
            else imported_passing_review(cid, previous, prior, source))
        # The archived pass covers its exact imported graph, not any later
        # Evidence mutation. Read only these IDs; allow only this same proven
        # deterministic fragment-coordinate step, never another history walk.
        prior_ops = {(a['collection'],a['object_id']):a for a in prior['operations']}
        locator_ops = {(a['collection'],a['object_id']):a for a in actual}
        current_pins = []
        recovered_posts={(p['collection'],p['object_id']):p['payload']
            for p in review_authority.get('imported_dependency_postimages',[])}
        for pin in review_authority['imported_dependency_pins']:
            key = (pin['collection'],pin['object_id'])
            original = recovered_posts[key] if key in recovered_posts else prior_ops[key]['after']
            expected = original
            if key in locator_ops:
                op = locator_ops[key]
                require(key[0]=='source_fragments' and op['operation']=='update'
                        and op['before_revision']==pin['revision']
                        and op['before_sha256']==pin['content_sha256'] and op['before']==original,
                        'imported dependency has an unproved modification')
                moved,resolution = migrate_source_fragment(original,index,source_sha256=projection.body_sha256)
                require(moved and resolution.status=='resolved'
                        and canonical_json(dict(moved,revision=op['after_revision']))==canonical_json(op['after']),
                        'fragment locator postimage differs')
                expected = op['after']
            live = store.get_record_state(*key)
            require(live and not live.get('retired') and live['revision']==expected['revision']
                    and live['content_sha256']==record_content_sha(expected)==record_content_sha(live['payload'])
                    and canonical_json(live['payload'])==canonical_json(expected),
                    'current reviewed dependency differs')
            current_pins.append({'collection':key[0],'object_id':key[1],
                'revision':live['revision'],'content_sha256':live['content_sha256']})
        review_authority['current_dependency_pins'] = current_pins
    return {**review_authority,'claim_id':cid,'revision':row['revision'],'content_sha256':row['content_sha256'],
        'reviewed_revision':row['revision']-1,
        'authority_kind':'exact_sql_applied_locator_postimage','coordinate_change_set_id':current[0],
        'coordinate_plan':plan_binding,'source':source_binding,'model_calls':0,'master_data_mutations':0,
        **({'locator_evidence_pins':locator_evidence_pins} if locator_evidence_pins else {})}


def current_claim_authority(store, cid, row):
    """Consume an actual revision-bound SQL review, not an inherited pass bit."""
    claim = row["payload"]
    if row.get("retired") or claim.get("review_status") in {"human_hold","withheld_unreviewed","rejected","superseded"}:
        raise ValueError("current Claim held or not reviewed: " + cid)
    qualification = store.claim_management_qualification(cid)
    if qualification is not None:
        if (not qualification['ready']
                or [qualification.get('revision'), qualification.get('content_sha256')]
                   != [row['revision'], row['content_sha256']]):
            raise ValueError('current Claim requires exact independent qualification: ' + cid)
        if (claim.get('review_status') == 'human_review_required'
                and qualification.get('legacy_ai_spot_check_replaced_by_current_review') is not True):
            raise ValueError('current Claim held or not reviewed: ' + cid)
        return {'claim_id': cid, 'revision': row['revision'], 'content_sha256': row['content_sha256'],
                'qualification': qualification, 'reviewer_kind': 'ai',
                'decision': 'independent_current_qualified', 'review_artifact_sha256': qualification['audit']['file_sha256']}
    if claim.get('review_status') == 'human_review_required':
        raise ValueError('current Claim held or not reviewed: ' + cid)
    with store.connect() as conn:
        events = conn.execute("""SELECT review_event_id,reviewer_kind,decision,artifact
                                 FROM wang_knowledge.review_events
                                 WHERE collection='claims' AND object_id=%s AND object_revision=%s
                                   AND decision=%s ORDER BY review_event_id""",
                              (cid,row["revision"],claim.get("review_status"))).fetchall()
    if not events:
        from backend.pipeline.cvp_sql_retained_evidence_locator_authority import authority as fixed_authority
        fixed=fixed_authority(store,cid,row)
        if fixed is not None:return fixed
        return coordinate_predecessor_authority(store, cid, row)
    if len(events) != 1 or events[0][1] not in {"ai","human","system"} or not events[0][3]:
        raise ValueError("current Claim has missing/ambiguous exact SQL review: " + cid)
    return {"claim_id":cid,"revision":row["revision"],"content_sha256":row["content_sha256"],
            "review_event_id":events[0][0],"reviewer_kind":events[0][1],"decision":events[0][2],
            "review_artifact_sha256":sha256_json(events[0][3])}


def coordinate_predecessor_authority(store, cid, row):
    """Carry one actual SQL review over an exact applied locator-only step.

    No new review event, status mutation, history-chain replay or legacy runner.
    The immediate predecessor must have its own unique review, either a bound
    SQL event or an exact passing imported-input/SQL-graph proof. Inherited
    review notes grant no authority. The original bounded plan, actual SQL
    operation and physical body must all agree, and the original deterministic
    locator compiler must reproduce the full row. Never walk older versions.
    """
    from backend.pipeline.cvp_sql_claim_authority import coordinate_authority
    return coordinate_authority(store, cid, row)
