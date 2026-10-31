"""Preserve existing target reviews across fully reviewed context changes.

Admission is explicit and transactional. Normal reads consume one direct proof;
Full transaction pins remain intact. An unchanged argument basis plus a full
current peer review can preserve an old qualification, never create one.
"""
from copy import deepcopy
from pathlib import Path


def legacy_spot_check_replaced(cursor, claim_id, state, value):
    """A fresh complete target review can replace an old AI sampling request.

    This grants no historical/human approval and never releases write holds.
    The caller must first verify the new review's current source and row pins.
    """
    if (state['payload'].get('review_status') != 'human_review_required'
            or 'claims/' + claim_id not in value['materials']['original_goal']['target_keys']
            or value['materials']['request']['operation'] != 'Update'
            or value.get('outcome') != 'completed' or not value.get('opus_approved')
            or not value.get('program_readback_complete')):
        return False
    from backend.pipeline.claim_metadata_projection import sampling_marker
    return bool(sampling_marker(cursor, claim_id, state))


def anchor_pruning(before, after):
    if {k:v for k,v in before.items() if k not in {'revision','occurrences'}} != {k:v for k,v in after.items() if k not in {'revision','occurrences'}}:
        return False
    old, new = before.get('occurrences', []), after.get('occurrences', [])
    if len(old) != len(new):return False
    removed = 0
    for a,b in zip(old,new):
        if {k:v for k,v in a.items() if k!='anchors'} != {k:v for k,v in b.items() if k!='anchors'}:return False
        aa,bb=a.get('anchors',[]),b.get('anchors',[])
        if aa and not bb:return False
        pos=0
        for anchor in aa:
            if pos<len(bb) and anchor==bb[pos]:pos+=1
        if pos!=len(bb):return False
        removed+=len(aa)-len(bb)
    return removed>0


def protected_keys(value):
    return set(value['materials']['original_goal']['target_keys']) | {
        d['record_key'] for d in value['candidate']['dispositions'] if d['action']!='retain'}


def argument_basis(targets, payloads, *, leaf_claims=()):
    """Own references and upstream premises, not downstream consumers.

    Every incident edge is protected. Only incoming edges expand the premises;
    following an outgoing support edge backwards through all its consumers
    would recreate the complete transaction readset. Unknown edge directions
    expand both ways conservatively. Build indexes once within the saved scope.
    """
    from backend.pipeline import claim_management as cm
    ids={}
    for key in payloads:
        col,oid=key.split('/',1)
        if col in cm.CLAIM_COLLECTIONS:ids.setdefault(oid,set()).add(key)
    incident={};incoming={};owners={}
    for key,payload in payloads.items():
        col=key.split('/',1)[0]
        if col in cm.RELATION_COLLECTIONS:
            ends=set()
            for field in ('from_id','to_id','source_id','target_id','from_claim_id','to_claim_id'):
                oid=payload.get(field)
                if not oid:continue
                matches=ids.get(oid,set())
                if len(matches)!=1:raise ValueError('qualification basis has missing or ambiguous endpoint')
                ends.update(matches)
            if not ends:raise ValueError('qualification basis has unsupported relation endpoints')
            destinations={payload[f] for f in ('to_id','target_id','to_claim_id') if payload.get(f)}
            directional=payload.get('relation_type') in {'supports','contextualizes'}
            destination_keys=set().union(*(ids.get(oid,set()) for oid in destinations)) if destinations and directional else ends
            for endpoint in ends:
                incident.setdefault(endpoint,set()).add(key)
            for endpoint in destination_keys:
                incoming.setdefault(endpoint,set()).update(ends)
        elif col=='evidence_steps':
            for cid in payload.get('produced_claim_ids',[]):
                owners.setdefault('claims/'+cid,set()).add(key)
    basis=set();pending=set(targets)
    while pending:
        key=pending.pop()
        if key in basis:continue
        if key not in payloads:raise ValueError('qualification argument basis missing record')
        basis.add(key)
        col=key.split('/',1)[0]
        if key in leaf_claims:
            # A direct neighbour contributes its declared Claim, never its
            # private evidence or a second Claim frontier.
            basis.update(incident.get(key,set()))
            continue
        if col not in cm.RELATION_COLLECTIONS:
            pending.update(cm.domain_dependencies(col,payloads[key],payloads)-basis)
            pending.update(owners.get(key,set())-basis)
            pending.update(incoming.get(key,set())-basis)
            # Guard outgoing relations without turning their destination's
            # other independent premises into this Claim's own premises.
            basis.update(incident.get(key,set()))
    basis.update(k for k in payloads if k.startswith('source_documents/'))
    return basis


def scoped_objections(old,peer,old_review,review,basis,protected,changed):
    """Preserve exact prior context judgments, never infer a new target impact.

    A peer's empty impact list speaks only about that peer's targets. For a
    new objection it cannot establish safety for a different original target.
    Compare content and the actual resolution literally, without fuzzy issue
    matching or treating an unchanged object as an unchanged objection.
    """
    from backend.pipeline import claim_management as cm

    def active(value,decoded):
        result=[]
        for finding in decoded['findings']:
            result.append((finding,None))
        for resolution in decoded['prior_findings']:
            if resolution['disposition']=='resolved':continue
            prior=value['materials']['unresolved'].get(resolution['finding_id'])
            if prior is None:raise ValueError('context objection has no saved prior finding')
            result.append((cm.resolution_finding(cm.FindingResolution.model_validate(resolution),
                value['materials']['unresolved']),resolution))
        return result

    def signature(finding,resolution):
        content={k:v for k,v in finding.items() if k not in {
            'affected_target_keys','affected_write_keys','impact_reason'}}
        # A changed adjudication or new supporting location is not silently
        # declared equivalent merely because it has the same issue identity.
        decision=({k:resolution[k] for k in ('disposition','reason','citations')}
                  if resolution else None)
        return cm.sha256_json({'finding':content,'resolution':decision})

    prior={signature(f,r):(f,r) for f,r in active(old,old_review)}
    peer_targets=set(peer['materials']['original_goal']['target_keys'])
    targets=set(old['materials']['original_goal']['target_keys'])
    allowed={};proof=[]
    for finding,resolution in active(peer,review):
        key=finding['target_key']
        if (finding.get('affected_target_keys')!=[] or finding.get('affected_write_keys')!=[]
                or not (finding.get('impact_reason') or '').strip()
                or key in protected|changed
                or resolution and resolution['disposition']=='reopened'):
            raise ValueError('context objection affects protected or changed scope')
        sig=signature(finding,resolution)
        previous=prior.get(sig)
        if previous is not None:
            original,_=previous
            if (original.get('affected_target_keys')!=[] or original.get('affected_write_keys')!=[]
                    or not (original.get('impact_reason') or '').strip()):
                raise ValueError('original objection lacks explicit context judgment')
            kind='unchanged_original_context'
        else:
            if key in basis or not targets<=peer_targets:
                raise ValueError('new or changed objection lacks original target impact proof')
            kind='new_context_outside_original_basis'
        allowed[key]=True
        proof.append({'record_key':key,'signature':sig,'basis':kind})
    rejected={v['record_key'] for v in review['verdicts'] if not v['approved'] or v['problems']}
    if not rejected<=set(allowed):raise ValueError('unexplained current review rejection')
    return sorted(proof,key=lambda p:(p['record_key'],p['signature']))


def reviewed_context_change(cursor,old,peer,original,fresh,current,protected,review,old_review):
    """Exact unchanged basis, exact scope, complete current content judgment."""
    from backend.pipeline import claim_management as cm
    from backend.api.canonical_repository.postgres_store import record_content_sha
    if set(original)!=set(fresh):raise ValueError('qualification context scope changed')
    changes={key for key in original if original[key]!=fresh[key]}
    if any('/'.join(key) in protected or key[0] not in cm.CLAIM_COLLECTIONS
           or original[key][2] or fresh[key][2]
           or current[key]['payload'].get('review_status') in cm.HELD|{'superseded'} for key in changes):
        raise ValueError('protected target/write/source or retirement changed')
    before={cm.source.key(*key):current[key]['payload'] for key in original}
    after=dict(before)
    ordered=sorted(changes)
    rows=cursor.execute('''SELECT v.collection,v.object_id,v.payload,v.content_sha256
        FROM wang_knowledge.object_versions v
        JOIN unnest(%s::text[],%s::text[],%s::int[]) AS n(collection,object_id,revision)
        ON (v.collection,v.object_id,v.revision)=(n.collection,n.object_id,n.revision)''',
        ([k[0] for k in ordered],[k[1] for k in ordered],[original[k][0] for k in ordered])).fetchall()
    if len(rows)!=len(changes):raise ValueError('qualification context history missing')
    for col,oid,payload,digest in rows:
        if digest!=original[col,oid][1] or record_content_sha(payload)!=digest:
            raise ValueError('qualification context history SHA drift')
        before[cm.source.key(col,oid)]=payload
    targets={k for k in protected if k.startswith('claims/')}
    from backend.pipeline import claim_direct_scope as direct
    bounded = direct.enabled(peer['materials']['unit_request'])
    leaves = set()
    if direct.enabled(old['materials']['unit_request']):
        request = cm.Request.model_validate(old['materials']['unit_request'])
        leaves = set(direct.scope(request, {k:{'payload':v} for k,v in before.items()})['neighbour_claim_keys'])
    basis=argument_basis(targets,before,leaf_claims=leaves)|argument_basis(targets,after,leaf_claims=leaves)
    if any(original[tuple(key.split('/',1))]!=fresh[tuple(key.split('/',1))] for key in basis):
        raise ValueError('original Claim argument basis changed')
    dispositions={d['record_key']:d['action'] for d in peer['candidate']['dispositions']}
    verdicts={v['record_key'] for v in review['verdicts'] if v['approved'] and not v['problems']}
    scope={cm.source.key(*key) for key in original if key[0] in cm.CLAIM_COLLECTIONS}
    objections=scoped_objections(old,peer,old_review,review,basis,protected,
        {cm.source.key(*key) for key in changes})
    exceptions={p['record_key'] for p in objections}
    retained_protected = protected & set(peer['materials']['before']) if bounded else protected
    reviewed_scope = {cm.source.key(*key) for key in changes} | retained_protected if bounded else scope
    if not reviewed_scope<=verdicts|exceptions or not reviewed_scope<=set(dispositions):
        raise ValueError('current review omitted original content scope')
    if not retained_protected<=set(dispositions) or any(dispositions[key]!='retain' for key in retained_protected):
        raise ValueError('original protected objects were not retained')
    if any(dispositions[key]!='retain' for key in basis if key in dispositions):
        raise ValueError('original argument basis was not retained')
    return sorted(cm.source.key(*key) for key in changes),sorted(basis),objections


def complete(value):
    from backend.pipeline.claim_management import Policy
    return (value.get('kind')=='claim_management_round' and value.get('outcome')=='completed'
        and value.get('program_readback_complete') and value.get('opus_approved')
        and not value.get('revisit_required')
        and not Policy.model_validate(value['materials']['policy']).independent_audit_required
        and value['materials']['original_goal']['operation']=='Update')


def round_value(cursor,rid):
    row=cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s FOR UPDATE',(rid,)).fetchone()
    if not row:raise ValueError('missing qualification review')
    return deepcopy(row[0])


def evidence(value):
    from backend.pipeline.cvp_sql_commit import check_binding
    from backend.pipeline.source_reconciliation_runner import read,body
    if not complete(value):raise ValueError('reuse requires an existing completed target review and fresh peer review')
    from backend.pipeline.source_editorial_transition import verify_current_source_binding
    for bound in value['materials']['request']['source_bindings'].values():verify_current_source_binding(bound)
    bound=value['stage_results']['readback'];check_binding(bound)
    opus=value['stage_results']['opus'];check_binding(opus)
    approval=value['reviewed']['evidence']['approval'];check_binding(approval)
    raw=body(read(Path(approval['path'])))['response']
    from backend.pipeline.claim_model_output import review
    from backend.pipeline.claim_management import Candidate, Request, validate_review, audit_keys, operation_questions
    decoded = review(raw, value['materials'], Candidate.model_validate(value['candidate']))
    if value['materials']['policy'].get('review_unit_contract'):
        m=value['materials'];request=Request.model_validate(m['unit_request']);candidate=Candidate.model_validate(value['candidate'])
        if not validate_review(candidate,decoded,audit_keys(request,m['before'],candidate),
                operation_questions(m['original_goal'],candidate),m['unresolved'],m['sources'],
                request=request,records=m['before']):
            raise ValueError('completed review retains an unresolved own-data issue')
    return bound,decoded.model_dump(mode='json')


def current_pins(store,cursor,value):
    """Cheap direct proof consumption, with no historical reconstruction."""
    from backend.pipeline.cvp_sql_commit import decoded_pins, check_binding
    from backend.api.canonical_repository.postgres_store import sha256_json
    reuse=value.get('reviewed_context_reuse')
    if not reuse:return decoded_pins(value['audited_pins'])
    core={k:v for k,v in reuse.items() if k!='event_ids'}
    if (core['original_pins_sha256']!=sha256_json(value['audited_pins'])
            or core['original_review']!=value['stage_results']['readback']
            or core['original_approval']!=value['reviewed']['evidence']['approval']
            or set(decoded_pins(core['current_pins']))!=set(decoded_pins(value['audited_pins']))):
        raise ValueError('qualification reuse original proof drift')
    if core['schema_version'] not in {'wang_claim_review_context_reuse_v1','wang_claim_review_context_reuse_v2','wang_claim_review_context_reuse_v3','wang_claim_review_context_reuse_v4'}:
        raise ValueError('unknown qualification reuse proof')
    if core['schema_version'] in {'wang_claim_review_context_reuse_v2','wang_claim_review_context_reuse_v3','wang_claim_review_context_reuse_v4'}:
        original=decoded_pins(value['audited_pins']);current=decoded_pins(core['current_pins'])
        basis=set(core['unchanged_argument_basis'])
        protected=protected_keys(value)
        if (not basis<={'/'.join(k) for k in original}
                or not {k for k in protected if k.startswith('claims/')}<=basis
                or core['protected_original_writes']!=sorted(protected-basis)
                or any(original[tuple(k.split('/',1))]!=current[tuple(k.split('/',1))] for k in basis|protected)
                or set(core['changed_context_keys'])!={'/'.join(k) for k in original if original[k]!=current[k]}):
            raise ValueError('qualification reuse argument basis drift')
    check_binding(core['original_approval'])
    peer=round_value(cursor,core['peer_round_id'])
    if not complete(peer) or peer['stage_results']['readback']!=core['peer_review']:
        raise ValueError('qualification reuse peer review invalidated')
    check_binding(core['peer_review'])
    if peer['reviewed']['evidence']['approval']!=core['peer_approval']:raise ValueError('peer approval identity drift')
    check_binding(core['peer_approval'])
    if core['schema_version']=='wang_claim_review_context_reuse_v4':
        from backend.pipeline.claim_direct_scope import enabled
        if not enabled(peer['materials']['unit_request']):
            raise ValueError('direct scope reuse contract drift')
        original=decoded_pins(value['audited_pins']);fresh=decoded_pins(peer['audited_pins'])
        updated=decoded_pins(core['current_pins'])
        missing=set(original)-set(fresh)
        changed={k for k in original if original[k]!=updated[k]}
        if (core['unchanged_original_only_keys']!=sorted('/'.join(k) for k in missing)
                or any(updated[k]!=original[k] for k in missing)
                or not changed<=set(fresh) or any(updated[k]!=fresh[k] for k in changed)):
            raise ValueError('direct scope reuse coverage drift')
        from backend.pipeline.source_editorial_transition import verify_current_source_binding
        for bound in peer['materials']['unit_request']['source_bindings'].values():
            verify_current_source_binding(bound)
        peer_current=store._assert_transaction_read_pins(cursor,fresh)
        store._assert_claim_management_scope(cursor,fresh,records=peer_current,request=peer['materials']['unit_request'],
            actual_write_keys=[d['record_key'] for d in peer['candidate']['dispositions'] if d['action']!='retain'])
        if 'human_source_decisions' in peer['materials']:
            from backend.pipeline.claim_human_source import assert_current
            assert_current(cursor,peer['materials']['unit_request']['source_bindings'],peer['materials']['human_source_decisions'])
    for key,eid in reuse['event_ids'].items():
        event=cursor.execute('SELECT artifact,decision FROM wang_knowledge.review_events WHERE review_event_id=%s',(eid,)).fetchone()
        if not event or event[0]!=core or event[1]!='review_context_revalidated':raise ValueError('qualification reuse event drift')
    if set(reuse['event_ids'])!={k for k in protected_keys(value) if k.startswith('claims/')}:
        raise ValueError('qualification reuse omitted an original Claim')
    return decoded_pins(core['current_pins'])


def admit(store,cursor,original_id,peer_id):
    """One bounded admission. Caller holds the common transaction lock."""
    from backend.pipeline.cvp_sql_commit import decoded_pins, pin_rows
    from backend.api.canonical_repository.postgres_store import record_content_sha,sha256_json,canonical_json
    old=round_value(cursor,original_id);peer=round_value(cursor,peer_id)
    original_review,old_review=evidence(old);peer_review,review=evidence(peer)
    has_objections=bool(review['findings'] or any(r['disposition']!='resolved' for r in review['prior_findings'])
        or any(not v['approved'] or v['problems'] for v in review['verdicts']))
    required=set(old['materials']['original_goal']['required_questions'])
    if not required<=set(peer['materials']['original_goal']['required_questions']) or not required<=set(review['answered_questions']):
        raise ValueError('peer review capabilities cannot support reuse')
    original=decoded_pins(old['audited_pins']);fresh=decoded_pins(peer['audited_pins'])
    from backend.pipeline.claim_direct_scope import enabled as direct_scope_enabled
    bounded=direct_scope_enabled(peer['materials']['unit_request'])
    if not bounded and not set(original)<=set(fresh):raise ValueError('peer review omitted original dependency scope')
    # Original-only evidence is still covered by the original full review:
    # verify it is exactly unchanged, instead of sending it to the peer model.
    current=store._assert_transaction_read_pins(cursor,{**original,**fresh} if bounded else fresh)
    store._assert_claim_management_scope(cursor,fresh,records={k:current[k] for k in fresh},request=peer['materials']['unit_request'],
        actual_write_keys=[d['record_key'] for d in peer['candidate']['dispositions'] if d['action']!='retain'])
    if bounded:
        for value in (old,peer):
            if 'human_source_decisions' in value['materials']:
                from backend.pipeline.claim_human_source import assert_current
                assert_current(cursor,value['materials']['unit_request']['source_bindings'],value['materials']['human_source_decisions'])
    peer_pins=fresh
    if bounded:
        fresh={k:peer_pins.get(k,pin) for k,pin in original.items()}
    protected=protected_keys(old);retained={d['record_key'] for d in peer['candidate']['dispositions'] if d['action']=='retain'}
    peer_protected = protected & set(peer['materials']['before']) if bounded else protected
    if not {k for k in peer_protected if k.startswith('claims/')}<=retained:raise ValueError('original target changed in peer operation')
    verdicts={v['record_key'] for v in review['verdicts'] if v['approved'] and not v['problems']}
    if not {k for k in peer_protected if k.startswith('claims/')}<=verdicts:raise ValueError('peer did not inspect retained target')
    updated={key:fresh[key] for key in original};changes=[];legacy=True
    for key,pin in original.items():
        if pin==fresh[key]:continue
        skey='/'.join(key)
        if (key[0]!='claims' or skey in protected or skey not in protected_keys(peer)
                or not pin or pin[2] or fresh[key][2]):
            legacy=False;break
        row=cursor.execute('SELECT payload,content_sha256 FROM wang_knowledge.object_versions WHERE collection=%s AND object_id=%s AND revision=%s',(*key,pin[0])).fetchone()
        if not row or row[1]!=pin[1] or record_content_sha(row[0])!=pin[1] or not anchor_pruning(row[0],current[key]['payload']):
            legacy=False;break
        changes.append(skey)
    basis=None;objections=[]
    if not legacy or has_objections:
        changes,basis,objections=reviewed_context_change(cursor,old,peer,original,fresh,current,protected,review,old_review)
    if not changes:raise ValueError('qualification has no context drift to revalidate')
    store._assert_claim_management_scope(cursor,updated,records={key:current[key] for key in updated},request=old['materials']['unit_request'],
        actual_write_keys=[d['record_key'] for d in old['candidate']['dispositions'] if d['action']!='retain'])
    for key in protected:
        if key.startswith('claims/') and cursor.execute("""SELECT 1 FROM wang_knowledge.pipeline_runs
            WHERE metadata->'blocked_current_claim_ids' ? %s AND started_at>
              (SELECT started_at FROM wang_knowledge.pipeline_runs WHERE run_id=%s) LIMIT 1""",(key.split('/',1)[1],original_id)).fetchone():
            raise ValueError('a later formal finding requires its own target review')
    core={'schema_version':'wang_claim_review_context_reuse_v1','original_round_id':original_id,
        'original_pins_sha256':sha256_json(old['audited_pins']),'original_review':original_review,
        'original_approval':old['reviewed']['evidence']['approval'],
        'peer_round_id':peer_id,'peer_review':peer_review,'peer_approval':peer['reviewed']['evidence']['approval'],'changed_readonly_claims':changes,'current_pins':pin_rows(updated),
        'basis':'unchanged target, sources, evidence and relations; reviewed readonly peer anchor pruning'}
    if basis is not None:
        core.update(schema_version='wang_claim_review_context_reuse_v2',
            unchanged_argument_basis=basis,protected_original_writes=sorted(protected-set(basis)),
            changed_context_keys=changes,
            basis='unchanged original target argument and writes; full current scoped content review')
        core.pop('changed_readonly_claims')
    if objections:
        core.update(schema_version='wang_claim_review_context_reuse_v3',retained_context_objections=objections)
    if bounded:
        if basis is None:
            basis=sorted('/'.join(k) for k in original if original[k]==updated[k])
        core.update(schema_version='wang_claim_review_context_reuse_v4',
            unchanged_argument_basis=basis,protected_original_writes=sorted(protected-set(basis)),
            changed_context_keys=changes,
            unchanged_original_only_keys=sorted('/'.join(k) for k in set(original)-set(peer_pins)),
            basis=('unchanged original proof plus peer-reviewed anchor pruning' if legacy and not has_objections else
                   'unchanged original argument and writes plus peer-reviewed context changes'))
        core.pop('changed_readonly_claims',None)
    events={key:'CMCTX-'+sha256_json({'proof':core,'target':key})[:32] for key in protected if key.startswith('claims/')}
    proof={**core,'event_ids':events}
    if old.get('reviewed_context_reuse')==proof:return proof
    for key,eid in events.items():
        cursor.execute("""INSERT INTO wang_knowledge.review_events
            (review_event_id,collection,object_id,object_revision,reviewer_kind,reviewer_id,decision,reason,artifact)
            VALUES (%s,'claims',%s,%s,'system','claim-management-context-reuse','review_context_revalidated',%s,%s::jsonb)
            ON CONFLICT (review_event_id) DO NOTHING""",(eid,key.split('/',1)[1],current[tuple(key.split('/',1))]['revision'],core['basis'],canonical_json(core)))
    old['reviewed_context_reuse']=proof
    cursor.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s',(canonical_json(old),original_id))
    return proof


def after_completion(store,cursor,peer_id,peer):
    """Only existing full qualifications may be retained; no bystander upgrade."""
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    if not complete(peer):return []
    readonly={d['record_key'].split('/',1)[1] for d in peer['candidate']['dispositions'] if d['action']=='retain' and d['record_key'].startswith('claims/')} - {
        k.split('/',1)[1] for k in peer['materials']['original_goal']['target_keys']}
    restored=[];seen=set()
    for cid in sorted(readonly):
        row=cursor.execute("""SELECT run_id FROM wang_knowledge.pipeline_runs
            WHERE metadata->>'kind'='claim_management_round' AND metadata->>'outcome'='completed'
              AND metadata->'materials'->'original_goal'->'target_keys' ? %s
            ORDER BY started_at DESC,run_id DESC LIMIT 1""",( 'claims/'+cid,)).fetchone()
        if not row or row[0] in seen:continue
        seen.add(row[0])
        try:proof=admit(store,cursor,row[0],peer_id)
        except (ValueError,ChangeSetConflict):continue
        restored.extend(proof['event_ids'])
    from backend.pipeline.claim_review_reuse import after_completion as reuse_context
    reuse_context(store, cursor, peer_id, peer)
    return sorted(set(restored))
