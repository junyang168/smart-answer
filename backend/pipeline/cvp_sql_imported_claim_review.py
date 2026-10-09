"""Authenticate one imported, unchanged passing Claim against committed SQL.

Legacy package ingest could retain review metadata from an older semantic
version without recording the new independent review event. That metadata is
NOT authority. Consume the exact independent input and the actual imported
Claim/Evidence/fragment postimages instead. No whole-Registry snapshot,
adjudication replay, ancestor walk, new review event, or content mutation.

This narrow branch cannot consume patched/merged recipients: every substantive
field of the named Claim and its evidence graph must equal the review input.
Other Claims and unresolved adjudications in that package gain no authority.
"""
import hashlib
import json
from pathlib import Path

from backend.api.canonical_repository.postgres_store import (
    _normalize_records, record_content_sha, sha256_json,
)

_REVIEW_FIELDS = {'revision','review_status','review_note','reviewed_by','reviewed_at'}
_MAX_BYTES = 32 * 1024**2


def imported_passing_review(cid, previous, history, source):
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

    require(history.get('status')=='applied' and history.get('source_kind')=='knowledge_package',
            'predecessor lacks committed package import')
    require(previous[1]==record_content_sha(previous[2]) and
            previous[2].get('review_status')=='ai_consensus_reviewed', 'predecessor is not eligible')
    named = Path(history.get('metadata',{}).get('input_path',''))
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
        if not claim or meaning(claim)!=meaning(previous[2]):
            continue  # old same-ID text is not a review of this imported version
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
        evidence_ids = claim.get('evidence_step_ids',[])
        require(evidence_ids and len(evidence_ids)==len(set(evidence_ids)), 'missing exact evidence')
        graph = {('claims',cid):claim}
        for eid in evidence_ids:
            step = normalized.get('evidence_steps',{}).get(eid)
            require(step and cid in step.get('produced_claim_ids',[]), 'evidence ownership missing')
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
        require(set(origin.get('transcript_paths',{}))=={sid}
                and origin['transcript_paths'][sid]==source['source_path'], 'foreign review source path')
        source_raw,_ = read(source['source_path'],source['source_file_sha256'])
        payload = json.loads(source_raw) if source['source_type']=='sermon_transcript' else markdown_source_document(source)[0]
        payload = {'script':payload} if isinstance(payload,list) else payload
        require(review_source_binding_mode(origin.get('transcript_sha256',{}).get(sid,''),
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
    require(len(matches)==1, 'missing/ambiguous exact imported review')
    return {**next(iter(matches.values())),'qualification_file_bytes':bytes_read}


def graph_as_rows(graph):
    return [{'collection':col,'object_id':oid,'payload':payload}
            for (col,oid),payload in sorted(graph.items())]
