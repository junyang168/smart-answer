"""Read-only authority for a closed legacy relation-only Claim review.

Never upgrades a Claim status or original-group qualification. The old review,
exact accepted exclusion, committed import and one locator step must agree.
"""
import hashlib
import json
from pathlib import Path

from backend.api.canonical_repository.postgres_store import _normalize_records, record_content_sha, sha256_json


def require(condition, reason):
    if not condition:
        raise ValueError('retained relation-only Claim proof: '+reason)


def relation_only_result(review, outcome, cid):
    reviews=[r for r in review['claim_reviews'] if r['claim_id']==cid]
    results=[r for r in outcome['results'] if r['claim_id']==cid]
    require(len(reviews)==len(results)==1, 'missing/ambiguous review')
    r=reviews[0];result=results[0];answer=result.get('openai',{})
    require(r['decision']=='changes_suggested' and r['issues']
        and all(i['issue_type']=='relation_error' for i in r['issues'])
        and not r.get('human_review_reason') and not r.get('spot_check_selected'), 'real content hold')
    patch=answer.get('patch',{})
    require(result.get('status')=='auto_applied' and answer.get('decision')=='accept'
        and result.get('claude_reconsideration') is None, 'unresolved adjudication')
    excluded=patch.get('excluded_claim_relation_ids',[])
    require(excluded and len(excluded)==len(set(excluded))
        and patch.get('route_type')=='unchanged'
        and all(not v for k,v in patch.items() if k not in {'route_type','excluded_claim_relation_ids'}),
        'not solely accepted relation exclusion')
    return excluded


def authority(store, item, audit, row):
    from backend.pipeline.cvp_sql_claim_authority import coordinate_authority
    from backend.pipeline.cvp_sql_flow import check_binding
    from backend.pipeline.corpus_ai_review_runner import _normalize_claim_layer, _review_artifact_sha256
    from backend.pipeline.corpus_ai_review import validate_review_response, apply_risk_routing
    from backend.pipeline.corpus_ai_adjudication_runner import _transcript_segments, _adjudication_artifact_sha256
    from backend.pipeline.corpus_ai_adjudication import validate_openai_adjudication, compile_outcome
    cid=item['claim']['claim_id']
    require(row['payload'].get('review_status')=='human_review_required' and not row.get('retired')
        and cid not in audit['meta']['registry_read_scope']['claim_ids'], 'not read-only retained context')
    coordinate=coordinate_authority(store,cid,row)
    require(coordinate['decision']=='human_review_required', 'wrong predecessor status')
    with store.connect() as conn:
        prior=conn.execute("SELECT change_set_id,payload FROM wang_knowledge.object_versions WHERE collection='claims' AND object_id=%s AND revision=%s",
            (cid,row['revision']-1)).fetchone()
    history=store.get_change_history(prior[0])
    ops=[o for o in history['operations'] if o['collection']=='claims' and o['object_id']==cid]
    require(history['status']=='applied' and len(ops)==1 and ops[0]['before'], 'missing committed review')
    op=ops[0];before=op['before']
    ignored={'revision','review_status','review_note','reviewed_by','reviewed_at'}
    require({k:v for k,v in before.items() if k not in ignored}==
        {k:v for k,v in prior[1].items() if k not in ignored}, 'review changed content')
    with store.connect() as conn:
        origin=conn.execute("SELECT change_set_id FROM wang_knowledge.object_versions WHERE collection='claims' AND object_id=%s AND revision=%s",
            (cid,op['before_revision'])).fetchone()
    imported=store.get_change_history(origin[0])
    require(imported['status']=='applied' and imported['source_kind']=='knowledge_package', 'foreign import')
    path=Path(imported['metadata']['input_path']);stem=path.name.removesuffix('.reviewed-candidate.json')
    require(path.is_absolute() and path.name.endswith('.reviewed-candidate.json'), 'unknown import input')
    bindings=[]
    def read(p, expected=None):
        p=Path(p);require(p.is_absolute() and p.stat().st_size<=8*1024**2,'unbounded exact leaf')
        raw=p.read_bytes();sha=hashlib.sha256(raw).hexdigest()
        require(expected is None or sha==expected,'physical SHA drift')
        bindings.append({'path':str(p),'file_sha256':sha});return json.loads(raw)
    package=read(path)
    require(sha256_json(package)==imported['source_sha256'], 'committed package SHA drift')
    review=read(path.parent.parent/'reviews'/(stem+'.independent-review.json'))
    require(('sha256:'+bindings[-1]['file_sha256'][:12]) in prior[1].get('review_note',''),
        'committed review physical identity drift')
    outcome=read(path.parent.parent/'adjudications'/(stem+'.ai-adjudication.json'))
    survey_package=read(review['source']['package_path'],review['source']['package_sha256'])
    survey=_normalize_claim_layer(survey_package)
    require(review['schema_version']=='wang_corpus_independent_review_v1'
        and review['reviewed_claims']==survey['candidate_claims'], 'original review input drift')
    validate_review_response(review,survey)
    reviewer=review['reviewer'];rf=reviewer['fingerprint_sha256']
    identity={k:v for k,v in reviewer.items() if k not in {'fingerprint_sha256','generated_at','artifact_sha256','provider'}}
    require(sha256_json(identity)==rf and reviewer.get('provider')=='anthropic', 'original review identity drift')
    if reviewer.get('artifact_sha256') is not None:
        require(reviewer['artifact_sha256']==_review_artifact_sha256(review),'original review seal drift')
    routed=apply_risk_routing({'claim_reviews':review['claim_reviews']},reviewer_fingerprint_sha256=rf,
        spot_check_percent=review['spot_check_percent'])
    require(all(review.get(k)==v for k,v in routed.items()), 'original review routing drift')
    segments={}
    for sid,p in review['source']['transcript_paths'].items():
        source=next((s for s in item['sources'] if s['source_id']==sid),None)
        require(source and source['path']==p,'foreign source')
        check_binding({'path':p,'file_sha256':source['file_sha256']})
        raw=Path(p).read_bytes();payload=json.loads(raw)
        from backend.pipeline.legacy_candidate_reconciliation import review_source_binding_mode
        require(review_source_binding_mode(review['source']['transcript_sha256'][sid],raw,payload) is not None,
            'original source changed')
        transcript=next(s['transcript_id'] for s in survey_package['source_documents'] if s['source_id']==sid)
        segments[transcript]=_transcript_segments(payload)
    fingerprint=before.get('ai_adjudication',{}).get('fingerprint')
    ai=outcome['adjudicator']
    ai_identity={k:v for k,v in ai.items() if k not in {'fingerprint_sha256','generated_at','artifact_sha256'}}
    require(fingerprint and ai['fingerprint_sha256']==fingerprint==sha256_json(ai_identity)
        and ai['review_fingerprint']==rf and outcome['source']=={'package_path':review['source']['package_path'],
            'review_path':str(path.parent.parent/'reviews'/(stem+'.independent-review.json'))}, 'adjudication identity drift')
    if ai.get('artifact_sha256') is not None:
        require(ai['artifact_sha256']==_adjudication_artifact_sha256(outcome),'adjudication seal drift')
    reviews=[r for r in review['claim_reviews'] if r['claim_id']==cid]
    answers=[r for r in outcome['openai_adjudication']['adjudications'] if r['claim_id']==cid]
    require(len(reviews)==len(answers)==1, 'ambiguous exact adjudication')
    scoped={**outcome['openai_adjudication'],'adjudications':answers}
    validate_openai_adjudication(scoped,reviews=reviews,
        claims_by_id={c['claim_id']:c for c in survey['candidate_claims'] if c['claim_id']==cid},
        transcript_segments=segments)
    compiled=compile_outcome(scoped,None,reviews=reviews)
    actual=[r for r in outcome['results'] if r['claim_id']==cid]
    require(actual==compiled['results'], 'exact adjudication outcome drift')
    excluded=relation_only_result(review,outcome,cid)
    consensus=package.get('consensus_application',{})
    require(consensus.get('adjudication_fingerprint')==fingerprint and cid in consensus.get('applied_claim_ids',[])
        and set(excluded)<=set(consensus.get('removed_claim_relation_ids',[])), 'import did not apply exclusion')
    normalized=_normalize_records(package)[0]['claims'].get(cid)
    require(normalized and record_content_sha(normalized)==record_content_sha(before), 'imported Claim differs')
    for rid in excluded:
        require(not any(r.get('claim_relation_id')==rid or r.get('relation_id')==rid for r in package.get('claim_relations',[])),
            'excluded relation still in package')
        with store.connect() as conn:
            require(not conn.execute('SELECT 1 FROM wang_knowledge.objects WHERE object_id=%s AND retired_at IS NULL',(rid,)).fetchone(),
                'excluded relation remains current')
    body={'schema_version':'wang_retained_closed_relation_only_claim_v1','claim_id':cid,
        'claim_revision':row['revision'],'claim_sha256':row['content_sha256'],
        'original_status_unchanged':'human_review_required','coordinate_authority':coordinate,
        'imported_change_set_id':origin[0],'excluded_relation_ids':excluded,'bindings':bindings,
        'master_data_mutations':0,'new_model_calls':0}
    body['artifact_sha256']=sha256_json(body)
    return body


def verify(proof):
    from backend.pipeline.cvp_sql_flow import check_binding
    require(proof.get('schema_version')=='wang_retained_closed_relation_only_claim_v1'
        and proof.get('artifact_sha256')==sha256_json({k:v for k,v in proof.items() if k!='artifact_sha256'})
        and proof.get('master_data_mutations')==proof.get('new_model_calls')==0, 'receipt drift')
    for b in proof['bindings']:check_binding(b)
    for key in ('coordinate_plan','source'):check_binding(proof['coordinate_authority'][key])
