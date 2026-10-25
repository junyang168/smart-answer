"""Exact reviewed Claim -> absent machine-anchor withdrawal -> locator.

Two fixed contiguous transforms, not a recursive approval inheritance walker.
Claim words and retained anchors/Evidence stay exact; unavailable Evidence may
only lose qualification. No new review, original cleanup execution or write.
"""
from copy import deepcopy
from backend.api.canonical_repository.postgres_store import record_content_sha,sha256_json
from backend.pipeline.cvp_sql_retained_evidence_locator_authority import require,exact_operation,sealed_plan

def reviewed_reverse_projection(store,eid,cid,approved,review_kcs,expected):
    """One already-committed claim-only reverse-index projection, no new pair."""
    from backend.pipeline.cvp_sql_pair_review_authority import pair_history,KIND
    with store.connect() as c:
        reviewed=c.execute("SELECT revision,content_sha256,payload FROM wang_knowledge.object_versions WHERE collection='evidence_steps' AND object_id=%s AND recorded_at<=(SELECT recorded_at FROM wang_knowledge.object_versions WHERE collection='claims' AND object_id=%s AND revision=%s) ORDER BY revision DESC LIMIT 1",(eid,cid,approved['revision'])).fetchone()
        step=c.execute("SELECT v.change_set_id,v.content_sha256,v.payload,c.status,c.source_kind FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c USING(change_set_id) WHERE v.collection='evidence_steps' AND v.object_id=%s AND v.revision=%s",(eid,expected['revision'])).fetchone()
    require(reviewed and reviewed[1]==record_content_sha(reviewed[2]) and step
        and step[1]==record_content_sha(expected) and step[2]==expected and step[3]=='applied',
        'reviewed Evidence generation/history drift')
    if reviewed[2]==expected:return {'change_set_id':step[0],'revision':expected['revision'],'content_sha256':step[1]}
    require(step[4]==KIND and reviewed[0]+1==expected['revision'],'retained Evidence not a direct reverse projection')
    history=pair_history(store,step[0],cid);meta=history['metadata']['claim_evidence_reciprocity_repair'];auth=meta['authorization']
    require(auth['schema_version']=='wang_claim_evidence_pair_repair_authorization_v1'
        and auth['policy']=='downstream_claim_projection_with_independent_pair_review_v1'
        and auth['artifact_sha256']==meta['pair_adjudication_authorization_sha256']==sha256_json({k:v for k,v in auth.items() if k!='artifact_sha256'})
        and history['source_sha256']==sha256_json({'schema_version':'wang_claim_evidence_pair_repair_plan_v1',
            'audit_artifact_sha256':auth['audit_artifact_sha256'],'authorization_artifact_sha256':auth['artifact_sha256'],
            'actions_sha256':sha256_json(auth['actions'])}), 'reverse projection authorization drift')
    actions=[a for a in auth['actions'] if a['evidence_step_id']==eid]
    own=[a for a in actions if a['claim_id']==cid]
    require(len(own)==1 and own[0]['claim_revision']==approved['revision']
        and own[0]['claim_content_sha256']==record_content_sha(approved),'reverse projection foreign Claim')
    require(actions and all(a['mismatch_type']=='claim_only' and a['effective_decision']=='include'
        and a['decision_basis']=='project_existing_claim_binding_to_reverse_index'
        and a['evidence_revision']==reviewed[0] and a['evidence_content_sha256']==reviewed[1] for a in actions),
        'not exact existing forward bindings')
    moved={**reviewed[2],'revision':expected['revision'],
        'produced_claim_ids':sorted(set(reviewed[2].get('produced_claim_ids',[]))|{a['claim_id'] for a in actions})}
    require(moved==expected,'reverse projection changed Evidence meaning')
    with store.connect() as c:
        operations=c.execute("SELECT operation,before_revision,after_revision,before_sha256,after_sha256 FROM wang_knowledge.change_operations WHERE change_set_id=%s AND collection='evidence_steps' AND object_id=%s",(step[0],eid)).fetchall()
    require(operations==[('update',reviewed[0],expected['revision'],reviewed[1],record_content_sha(expected))],
        'reverse projection not exact committed operation')
    return {'change_set_id':step[0],'revision':expected['revision'],'content_sha256':step[1],
        'authorization_sha256':auth['artifact_sha256']}

def withdrawn_postimage(before, after, index):
    expected=deepcopy(before);removed=[];canonical=[]
    for occurrence in expected.get('occurrences',[]):
        local=occurrence.get('local_source_evidence_ids',[])
        ids=occurrence.get('canonical_evidence_step_ids',[])
        require(len(local)==len(ids) and len(local)==len(set(local)),'ambiguous local Evidence identity')
        mapping=dict(zip(local,ids));kept=[]
        for anchor in occurrence.get('anchors',[]):
            text=anchor.get('proposed_highlight',{}).get('text')
            require(text,'empty reviewed anchor')
            if any(text in row.text for row in index.rows):kept.append(anchor)
            else:
                removed.append(anchor)
                eid=mapping.get(anchor.get('evidence_id'),anchor.get('evidence_id'))
                require(eid in before.get('evidence_step_ids',[]),'removed anchor has foreign Evidence')
                if eid not in canonical:canonical.append(eid)
        require(kept,'withdrawal removed all own anchors')
        occurrence['anchors']=kept
    require(removed,'no absent machine anchor')
    # Some absent anchors never had eligible Evidence. Do not promote or change
    # another bucket; reproduce only the actual eligibility withdrawal.
    qualified=before.get('eligible_evidence_step_ids',[])
    moved=[i for i in canonical if i in qualified]
    expected['eligible_evidence_step_ids']=[i for i in qualified if i not in moved]
    withheld=list(before.get('withheld_evidence_step_ids',[]))
    expected['withheld_evidence_step_ids']=withheld+[i for i in moved if i not in withheld]
    expected['revision']=after['revision']
    require(expected==after,'anchor withdrawal changed reviewed Claim meaning/other fields')
    require(expected['eligible_evidence_step_ids'],'no retained eligible Evidence')
    return removed,moved

def authority(store,cid,previous,prior,source,index,locator_operations):
    require(prior['source_kind']=='wkp368_absent_machine_anchor_cleanup','wrong absent-anchor transform')
    targets=[o for o in prior['operations'] if o['collection']=='claims' and o['object_id']==cid]
    require(len(targets)==1,'missing/ambiguous anchor withdrawal target')
    target=targets[0];approved=target['before'];after=previous[2]
    require(approved['revision']+1==after['revision'] and approved['review_status']==after['review_status']=='ai_consensus_reviewed',
            'noncontiguous/unapproved anchor withdrawal')
    exact_operation(prior,cid,approved,after)
    with store.connect() as c:
        version=c.execute("SELECT change_set_id,content_sha256,payload FROM wang_knowledge.object_versions WHERE collection='claims' AND object_id=%s AND revision=%s",(cid,approved['revision'])).fetchall()
        events=c.execute("SELECT review_event_id,reviewer_kind,decision,artifact FROM wang_knowledge.review_events WHERE collection='claims' AND object_id=%s AND object_revision=%s",(cid,approved['revision'])).fetchall()
    require(len(version)==1 and version[0][1:]==(record_content_sha(approved),approved),'original reviewed SQL version drift')
    original=store.get_change_history(version[0][0])
    require(original and original['status']=='applied' and original['source_kind']=='review_decision'
        and len(events)==1 and events[0][1] in {'ai','human','system'} and events[0][2]=='ai_consensus_reviewed'
        and events[0][3] and events[0][0]==original['metadata'].get('review_event_id')
        and events[0][3].get('change_set_id',version[0][0])==version[0][0],'original exact SQL review missing')
    op=[o for o in original['operations'] if o['collection']=='claims' and o['object_id']==cid]
    require(len(op)==1 and op[0]['after']==approved and op[0]['after_sha256']==version[0][1], 'review committed postimage differs')
    metadata=prior['metadata']
    document,report_binding=sealed_plan({**prior,'metadata':{**metadata,
        'source_plan_path':metadata['dry_run_report_path'],'source_plan_file_sha256':metadata['dry_run_report_file_sha256']}})
    require(document['schema_version']=='wang_wkp368_absent_machine_anchor_cleanup_dry_run_v1'
        and document['artifact_sha256']==metadata['dry_run_report_content_sha256'],'wrong withdrawal report schema/seal')
    matches=[s for s in document['source_proofs'] if s['source_id']==source['source_id']]
    require(len(matches)==1 and matches[0]['path']==source['source_path']
        and matches[0]['file_sha256']==source['source_file_sha256']
        and matches[0]['body_sha256']==source['source_body_sha256'],'withdrawal physical source changed')
    require(all(o.get('transcript_id')==source['transcript_id'] for o in approved['occurrences']), 'foreign occurrence')
    removed,moved=withdrawn_postimage(approved,after,index)
    proofs=[p for p in document['claim_bad_anchor_proofs'] if p['claim_id']==cid]
    require(len(removed)==1 and len(proofs)==1 and proofs[0]['removed_anchor_sha256']==sha256_json(removed[0])
        and proofs[0]['withheld_evidence_step_ids_added']==moved
        and proofs[0]['remaining_anchor_count']==sum(len(o['anchors']) for o in after['occurrences']),
        'withdrawal retained/withheld population drift')
    prior_ops={(o['collection'],o['object_id']):o for o in prior['operations']}
    locator_ops={(o['collection'],o['object_id']):o for o in locator_operations}
    dependency=[]
    from backend.pipeline.source_contract_cleanup import migrate_source_fragment
    for eid in after['evidence_step_ids']:
        live=store.get_record_state('evidence_steps',eid)
        require(live and not live['retired'] and record_content_sha(live['payload'])==live['content_sha256']
            and cid in live['payload'].get('produced_claim_ids',[]),'missing/foreign retained Evidence')
        ev=prior_ops.get(('evidence_steps',eid))
        if eid in moved:
            require(ev and ev['operation']=='update','withheld Evidence lacks committed withdrawal')
            expected={k:v for k,v in ev['before'].items() if k not in {'source_fragment_id','source_fragment_ids'}}
            expected.update(revision=ev['after_revision'],anchor_quality='missing',support_eligibility='withheld_missing_anchor')
            require(expected==ev['after']==live['payload'] and live['revision']==ev['after_revision'],
                    'unavailable Evidence was not an exact qualification-only downgrade')
            reviewed_reverse_projection(store,eid,cid,approved,version[0][0],ev['before'])
            fids=set(ev['before'].get('source_fragment_ids',[]))
            if ev['before'].get('source_fragment_id'):fids.add(ev['before']['source_fragment_id'])
            require(fids,'missing withdrawn fragments')
            for fid in fids:
                f=prior_ops.get(('source_fragments',fid));now=store.get_record_state('source_fragments',fid)
                require(f and f['operation']=='retire' and now and now['retired']
                    and now['revision']==f['after_revision'] and now['content_sha256']==f['after_sha256']
                    and f['before']['source_id']==source['source_id']
                    and not any(f['before']['verbatim_excerpt'] in r.text for r in index.rows),
                    'withdrawn fragment is not exact absent own-source history')
                dependency.append({'collection':'source_fragments','object_id':fid,
                    'pin':[now['revision'],now['content_sha256'],True]})
        else:
            require(ev is None and eid not in moved,'unexpected retained Evidence mutation')
            if eid in after['eligible_evidence_step_ids']:
                reviewed_reverse_projection(store,eid,cid,approved,version[0][0],live['payload'])
                require(live['payload'].get('support_eligibility') in {'eligible','eligible_candidate'},'retained Evidence held')
                fids=set(live['payload'].get('source_fragment_ids',[]))
                if live['payload'].get('source_fragment_id'):fids.add(live['payload']['source_fragment_id'])
                require(fids,'retained Evidence unbound')
                for fid in sorted(fids):
                    f=store.get_record_state('source_fragments',fid);lop=locator_ops.get(('source_fragments',fid))
                    require(f and not f['retired'] and f['payload']['source_id']==source['source_id'],'foreign/retired own fragment')
                    if lop:
                        transformed,resolution=migrate_source_fragment(lop['before'],index,source_sha256=source['source_body_sha256'])
                        require(transformed and resolution.status=='resolved'
                            and dict(transformed,revision=lop['after_revision'])==lop['after']==f['payload'],
                            'retained fragment locator differs')
                    require(f['payload']['verbatim_excerpt'] and any(f['payload']['verbatim_excerpt'] in r.text for r in index.rows),
                        'retained own quote absent')
                    dependency.append({'collection':'source_fragments','object_id':fid,
                        'pin':[f['revision'],f['content_sha256'],False]})
            else:
                require((eid in after['withheld_evidence_step_ids'] and live['payload'].get('support_eligibility','').startswith('withheld'))
                    or (eid in after.get('context_evidence_step_ids',[]) and live['payload'].get('support_eligibility') in {'contextual_only','context_only'}),
                    'noneligible Evidence acquired eligibility')
        dependency.append({'collection':'evidence_steps','object_id':eid,
            'pin':[live['revision'],live['content_sha256'],False]})
    return {'review_event_id':events[0][0],'reviewer_kind':events[0][1],'decision':events[0][2],
        'review_artifact_sha256':sha256_json(events[0][3]),'reviewed_revision':approved['revision'],
        'review_authority_kind':'exact_review_absent_machine_anchor_withdrawal',
        'withdrawal_change_set_id':prior['change_set_id'],'withdrawal_report':report_binding,
        'current_dependency_pins':dependency,'model_calls':0,'master_data_mutations':0}
