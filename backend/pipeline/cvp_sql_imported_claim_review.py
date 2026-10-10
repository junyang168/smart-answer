"""Authenticate one imported, unchanged passing Claim against committed SQL.

Legacy package ingest could retain review metadata from an older semantic
version without recording the new independent review event. That metadata is
NOT authority. Consume the exact independent input and the actual imported
Claim/Evidence/fragment postimages instead. No whole-Registry snapshot,
adjudication replay, ancestor walk, new review event, or content mutation.

Ordinary imports cannot consume patched/merged recipients. The separate fixed
namespace-recovery bridge may consume an exactly replayed historical recipient,
including the full original adjudication and application. Other Claims and
unresolved adjudications in that package gain no authority.
"""
import hashlib
import json
from pathlib import Path

from backend.api.canonical_repository.postgres_store import (
    _normalize_records, record_content_sha, sha256_json,
)

_REVIEW_FIELDS = {'revision','review_status','review_note','reviewed_by','reviewed_at'}
_MAX_BYTES = 32 * 1024**2


def imported_passing_review(cid, previous, history, source, *, store=None):
    from backend.pipeline.corpus_ai_review import (
        AI_REVIEW_VERSION, apply_risk_routing, validate_review_response,
    )
    from backend.pipeline.corpus_ai_review_runner import _normalize_claim_layer, _review_artifact_sha256
    from backend.pipeline.legacy_candidate_reconciliation import review_source_binding_mode
    from backend.pipeline.knowledge_source import markdown_source_document

    def require(condition, reason):
        if not condition:
            raise ValueError('imported Claim review authority failed: '+cid+': '+reason)

    bytes_read = 0
    bindings = {}

    def read(path, expected=None):
        nonlocal bytes_read
        path = Path(path)
        require(path.is_absolute() and path.is_file(), 'missing exact artifact')
        size = path.stat().st_size
        require(size <= 8*1024**2 and bytes_read+size <= _MAX_BYTES, 'bounded review input exceeded')
        raw = path.read_bytes(); bytes_read += len(raw)
        digest = hashlib.sha256(raw).hexdigest()
        require(expected is None or digest==expected, 'physical artifact SHA drift')
        bindings[str(path)] = {'path':str(path),'file_sha256':digest}
        return raw, digest

    def bound_input(review):
        # Count and bound every actual read; never delegate discovery to an
        # unmetered scan or look outside this exact input's generations.
        origin = review.get('source',{})
        path = Path(origin.get('package_path',''))
        expected = origin.get('package_sha256')
        if not path.is_absolute() or not expected:
            return None
        if path.is_file():
            raw,digest = read(path)
            if digest==expected:
                return path,raw,digest
        paths = sorted((path.parent/'generations').glob(path.stem+'.*.json'))
        require(len(paths)<=64, 'unbounded input generations')
        found = []
        for generation in paths:
            raw,digest = read(generation)
            if digest==expected:
                found.append((generation,raw,digest))
        return found[0] if len(found)==1 else None

    recovered=None
    if history.get('source_kind')=='wkp353_relation_id_incident_recovery' and store is not None:
        from backend.pipeline.cvp_sql_namespace_recovery_authority import recovery_graph
        recovered=recovery_graph(store,cid,previous,history,source)
    require(history.get('status')=='applied' and (history.get('source_kind')=='knowledge_package' or recovered),
            'predecessor lacks committed package import')
    require(previous[1]==record_content_sha(previous[2]) and
            previous[2].get('review_status')=='ai_consensus_reviewed', 'predecessor is not eligible')
    named = Path(recovered[0] if recovered else history.get('metadata',{}).get('input_path',''))
    require(named.is_absolute() and named.name.endswith('.reviewed-candidate.json'), 'missing exact import location')
    stem = named.name.removesuffix('.reviewed-candidate.json')
    directory = named.parent.parent/'reviews'
    paths = set(directory.glob(stem+'.independent-review.json'))
    paths.update((directory/'review-generations').glob(stem+'.independent-review.*.json'))
    require(0 < len(paths) <= 64, 'missing or unbounded review generations')
    operations = {}
    for op in history['operations']:
        key = (op['collection'],op['object_id'])
        require(key not in operations, 'duplicate committed operation')
        operations[key] = op
    if recovered:operations=recovered[1]
    target = operations.get(('claims',cid))
    require(target and target['operation'] in {'create','update'}
            and target['after']==previous[2] and target['after_sha256']==previous[1]
            and target['after_revision']==previous[2]['revision'], 'exact imported postimage missing')

    def meaning(payload, *, claim=True):
        ignored = _REVIEW_FIELDS if claim else {'revision'}
        return {k:v for k,v in payload.items() if k not in ignored}

    matches = {}
    seen = set()
    for path in sorted(paths):
        raw, digest = read(path)
        if digest in seen:
            continue  # canonical file and immutable generation with identical bytes
        seen.add(digest)
        review = json.loads(raw)
        package_input = bound_input(review)
        if package_input is None:
            # A broken generation cannot be used to prove current authority.
            # Its own recorded snapshot still reveals a conflicting target.
            rows = [r for r in review.get('reviewed_claims',[]) if r.get('claim_id')==cid]
            require(not rows or rows[0].get('statement',rows[0].get('title'))!=previous[2]['statement'],
                    'matching review input is missing')
            continue
        package_path,package_raw,package_sha = package_input
        package = json.loads(package_raw)
        normalized,_ = _normalize_records(package)
        claim = normalized.get('claims',{}).get(cid)
        merge_proof=None
        if claim and meaning(claim)!=meaning(previous[2]) and recovered:
            # A recipient's pass approves its original proposition, not later
            # added anchors. Consume the full, SHA-pinned original application
            # in memory only, with every contributing donor independently
            # authenticated as an actually applied consensus merge.
            reviewed_raw,_=read(named)
            reviewed=json.loads(reviewed_raw)
            application=reviewed.get('consensus_application',{})
            donors={donor for donor,keeper in application.get('merged_claim_ids',{}).items() if keeper==cid}
            if donors:
                from backend.pipeline.legacy_candidate_reconciliation import inspect_legacy_bundle,_related
                from backend.pipeline.legacy_auto_applied_review_reconciliation import verify_historical_replay
                stem=named.name.removesuffix('.reviewed-candidate.json')
                for suffix in ('.ai-adjudication.json','.adjudication.json'):
                    leaf=_related(named,suffix)
                    if leaf.is_file():read(leaf)
                read(named.parent.parent/'overrides'/(stem+'.consensus-overrides.json'))
                bundle=inspect_legacy_bundle(named,validation_claim_ids=donors)
                require(bundle.get('reason') is None and bundle.get('scoped_adjudication_structurally_validated')
                    and bundle['package_sha256']==package_sha
                    and bundle['review_sha256']==digest
                    and all(bundle['adjudication_results'].get(d,{}).get('status')=='auto_applied' for d in donors),
                    'merge donor lacks exact applied independent adjudication')
                for leaf in bundle['review_transcript_paths'].values():read(leaf)
                require(set(bundle['review_transcript_paths'])=={source['source_id']},'foreign merge source population')
                # This pure helper loads only SHA-checked historical compiler
                # code; no old runner, migration, admission or SQL mutation.
                merge_proof=verify_historical_replay(named,validation_claim_ids=donors)
                require(merge_proof and merge_proof['historical_replay_sha256']==sha256_json(reviewed)
                    ==recovered[3]['input_canonical_sha256'],'full historical merge application differs')
                from backend.pipeline.relation_id_namespace import migrate_legacy_cross_section_relation_ids
                effective,_=migrate_legacy_cross_section_relation_ids(reviewed)
                normalized,_=_normalize_records(effective)
                merged=normalized.get('claims',{}).get(cid)
                require(merged and meaning(merged)==meaning(previous[2]),'merged recipient differs from committed SQL')
                graph_claim=merged
            else:continue
        elif not claim or meaning(claim)!=meaning(previous[2]):
            continue  # old same-ID text is not a review of this imported version
        else:graph_claim=claim
        require(claim.get('review_status') not in {'human_hold','human_review_required',
                'withheld_unreviewed','rejected','superseded'}, 'review input has a real hold')
        survey = _normalize_claim_layer(package)
        require(review.get('schema_version')==AI_REVIEW_VERSION
                and review.get('reviewed_claims')==survey['candidate_claims'], 'review snapshot/schema drift')
        validate_review_response(review,survey)
        reviewer = review.get('reviewer',{})
        identity = {k:v for k,v in reviewer.items()
                    if k not in {'fingerprint_sha256','generated_at','artifact_sha256','provider'}}
        fingerprint = reviewer.get('fingerprint_sha256')
        require(reviewer.get('provider')=='anthropic' and fingerprint==sha256_json(identity)
                and all(reviewer.get(k) for k in ('review_model_id','review_prompt_sha256',
                    'response_schema_sha256','source_extraction_fingerprint')),
                'unsupported or invalid actual review identity')
        if reviewer.get('artifact_sha256') is not None:
            require(reviewer['artifact_sha256']==_review_artifact_sha256(review), 'review seal drift')
        origin = review.get('source',{})
        source_identity = {k:origin[k] for k in ('input_mode','projection_version','package_sha256',
            'transcript_sha256','transcript_id','review_batch') if k in origin}
        require(origin.get('input_mode')=='curated_claim_layer'
                and sha256_json(source_identity)==origin.get('source_candidate_fingerprint')
                ==reviewer['source_extraction_fingerprint'], 'review request identity drift')
        routed = apply_risk_routing({'claim_reviews':review['claim_reviews']},
            reviewer_fingerprint_sha256=fingerprint, spot_check_percent=int(review['spot_check_percent']))
        require(all(review.get(k)==v for k,v in routed.items()), 'review routing drift')
        rows = [r for r in review['claim_reviews'] if r['claim_id']==cid]
        require(len(rows)==1 and rows[0]['decision']=='pass' and not rows[0]['issues']
                and not rows[0].get('human_review_reason') and not rows[0].get('spot_check_selected')
                and rows[0].get('routing_status')=='ai_reviewed', 'matching review is unresolved or held')
        evidence_ids = graph_claim.get('evidence_step_ids',[])
        require(evidence_ids and len(evidence_ids)==len(set(evidence_ids)), 'missing exact evidence')
        graph = {('claims',cid):graph_claim}
        reverse_projection={}
        reverse_proofs={}
        for eid in evidence_ids:
            step = normalized.get('evidence_steps',{}).get(eid)
            require(step, 'evidence missing')
            if recovered and cid not in step.get('produced_claim_ids',[]):
                from backend.pipeline.cvp_sql_absent_anchor_authority import reviewed_reverse_projection
                live=store.get_record_state('evidence_steps',eid)
                require(live and not live['retired'],'reverse projected Evidence missing/retired')
                reverse_proofs['evidence_steps',eid]=reviewed_reverse_projection(store,eid,cid,previous[2],previous[0],live['payload'])
                reverse_projection['evidence_steps',eid]=live
            else:
                require(cid in step.get('produced_claim_ids',[]), 'evidence ownership missing')
            graph['evidence_steps',eid] = step
            fids = set(step.get('source_fragment_ids',[]))
            if step.get('source_fragment_id'):
                fids.add(step['source_fragment_id'])
            require(fids, 'evidence has no fragment')
            for fid in fids:
                fragment = normalized.get('source_fragments',{}).get(fid)
                require(fragment and fragment.get('source_id')==source['source_id'], 'foreign evidence fragment')
                graph['source_fragments',fid] = fragment
        for key, expected in graph.items():
            op = operations.get(key)
            require(op and op['operation'] in {'create','update'} and op.get('after')
                    and op['after_sha256']==record_content_sha(op['after'])
                    and op['after_revision']==op['after'].get('revision'), 'reviewed graph is not in exact SQL import')
            actual = _normalize_records({key[0]:[op['after']]})[0][key[0]][key[1]]
            require(meaning(actual,claim=key[0]=='claims')==meaning(expected,claim=key[0]=='claims'),
                    'reviewed graph differs from imported graph')
        sources = normalized.get('source_documents',{})
        require(set(sources)=={source['source_id']} and
                sources[source['source_id']].get('source_type')==source['source_type']
                and sources[source['source_id']].get('transcript_id')==source['transcript_id'], 'source identity mismatch')
        sid = source['source_id']
        require(set(origin.get('transcript_paths',{}))=={sid}, 'foreign review source population')
        historical_source_binding=None
        if recovered and origin['transcript_paths'][sid]!=source['source_path']:
            # The recovered package's independent review read script_review,
            # while current coordinates point to script_published. Require the
            # complete ordered raw text population to be byte-for-byte equal;
            # differing coordinates/metadata are not a content approval.
            require(source['source_type']=='sermon_transcript','namespace source path is not sermon JSON')
            historical_raw,_=read(origin['transcript_paths'][sid])
            historical=json.loads(historical_raw);historical={'script':historical} if isinstance(historical,list) else historical
            current_raw,_=read(source['source_path'],source['source_file_sha256'])
            current=json.loads(current_raw);current={'script':current} if isinstance(current,list) else current
            require([r['text'] for r in historical['script']]==[r['text'] for r in current['script']]
                and all(isinstance(r['text'],str) for r in historical['script']),
                'historical/current full own sermon text differs')
            require(review_source_binding_mode(origin.get('transcript_sha256',{}).get(sid,''),historical_raw,historical) is not None,
                'original review physical source drift')
            historical_source_binding=bindings[origin['transcript_paths'][sid]]
        else:
            require(origin['transcript_paths'][sid]==source['source_path'], 'foreign review source path')
        source_raw,_ = read(source['source_path'],source['source_file_sha256'])
        payload = json.loads(source_raw) if source['source_type']=='sermon_transcript' else markdown_source_document(source)[0]
        payload = {'script':payload} if isinstance(payload,list) else payload
        require(historical_source_binding is not None or review_source_binding_mode(origin.get('transcript_sha256',{}).get(sid,''),
                source_raw,payload) is not None, 'review physical source changed')
        matches[digest] = {'review_event_id':None,'reviewer_kind':'ai','decision':'ai_consensus_reviewed',
            'review_artifact_sha256':digest,'review_binding':bindings[str(path)],
            'review_input_binding':{'path':str(package_path),'file_sha256':package_sha},
            'actual_review_identity':identity,'reviewer_fingerprint':fingerprint,
            'imported_change_set_id':previous[0],'imported_graph_sha256':sha256_json(graph_as_rows(graph)),
            'imported_dependency_pins':[{'collection':col,'object_id':oid,
                'revision':operations[col,oid]['after_revision'],'content_sha256':operations[col,oid]['after_sha256']}
                for col,oid in sorted(graph) if col!='claims'],
            'review_authority_kind':'exact_passing_input_and_committed_import_graph',
            'unrelated_findings_released':False}
        if merge_proof:
            matches[digest]['merge_application_bindings']=[bindings[str(p)] for p in (
                named,Path(bundle['adjudication_path']),
                named.parent.parent/'overrides'/(stem+'.consensus-overrides.json'))]
        if recovered:
            for pin in matches[digest]['imported_dependency_pins']:
                live=reverse_projection.get((pin['collection'],pin['object_id']))
                if live:pin.update(revision=live['revision'],content_sha256=live['content_sha256'])
            matches[digest].update(namespace_recovery_binding=recovered[3],
                historical_source_binding=historical_source_binding,
                review_authority_kind='exact_passing_input_and_namespace_recovered_SQL_graph',
                reverse_projection_ids=[i for c,i in sorted(reverse_projection)],
                historical_merge_application_proof=merge_proof,
                reverse_projection_proofs=[{'collection':c,'object_id':i,**p} for (c,i),p in sorted(reverse_proofs.items())],
                imported_dependency_postimages=[{'collection':c,'object_id':i,'payload':reverse_projection[c,i]['payload'] if (c,i) in reverse_projection else operations[c,i]['after'],
                    'origin_change_set_id':reverse_proofs[c,i]['change_set_id'] if (c,i) in reverse_proofs else recovered[2][c,i]} for c,i in sorted(graph) if c!='claims'])
    require(len(matches)==1, 'missing/ambiguous exact imported review')
    return {**next(iter(matches.values())),'qualification_file_bytes':bytes_read}


def graph_as_rows(graph):
    return [{'collection':col,'object_id':oid,'payload':payload}
            for (col,oid),payload in sorted(graph.items())]
