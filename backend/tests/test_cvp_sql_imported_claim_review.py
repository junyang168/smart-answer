"""An archived pass must cover the exact committed graph, not merely its ID."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from backend.api.canonical_repository.postgres_store import _normalize_records, record_content_sha, sha256_json
from backend.pipeline.corpus_ai_review import AI_REVIEW_VERSION, apply_risk_routing, reviewer_fingerprint
from backend.pipeline.corpus_ai_review_runner import _normalize_claim_layer
from backend.pipeline.cvp_sql_imported_claim_review import imported_passing_review


def save(path, value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False))
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def imported_case(tmp_path):
    source_path=tmp_path/'source.json'
    source_sha=save(source_path,{'script':[{'text':'The earlier stage ends here.','index':1}]})
    source={'source_id':'SRC-test','source_type':'sermon_transcript','transcript_id':'sermon-test',
        'source_path':str(source_path),'source_file_sha256':source_sha}
    package={'source_documents':[source],
        'claims':[{'claim_id':'CL-test','title':'The earlier stage ends here.',
            'claim_type':'reasoning_conclusion','attribution':'professor','evidence_step_ids':['E-test']}],
        'evidence_steps':[{'evidence_step_id':'E-test','statement':'Earlier prophets stop here.',
            'produced_claim_ids':['CL-test'],'source_fragment_ids':['FR-test'],'support_eligibility':'eligible'}],
        'source_fragments':[{'fragment_id':'FR-test','source_id':'SRC-test',
            'verbatim_excerpt':'The earlier stage ends here.'}]}
    package_path=tmp_path/'cross-section'/'sample.cross-section.json'
    package_sha=save(package_path,package)
    origin={'input_mode':'curated_claim_layer','projection_version':'wang_claim_layer_review_projection_v3',
        'package_sha256':package_sha,'transcript_sha256':{'SRC-test':source_sha},'transcript_id':'sermon-test'}
    source_fingerprint=sha256_json(origin)
    reviewer=reviewer_fingerprint(source_extraction_fingerprint=source_fingerprint,prompt='historical exact prompt',
        model_id='actual-historical-reviewer',max_output_tokens=64000,backend='claude-subscription')
    reviewer.update(provider='anthropic',generated_at='2026-09-02T00:00:00Z')
    row={'claim_id':'CL-test','decision':'pass','issues':[],'proposed_statement':'',
        'proposed_claim_kind':'','proposed_route_type':'unchanged','rationale':'Exact own source.',
        'confidence':'high','human_review_reason':''}
    review={'schema_version':AI_REVIEW_VERSION,'source':{**origin,'package_path':str(package_path),
        'transcript_paths':{'SRC-test':str(source_path)},'source_candidate_fingerprint':source_fingerprint},
        'reviewer':reviewer,'reviewed_claims':_normalize_claim_layer(package)['candidate_claims'],
        'spot_check_percent':0,**apply_risk_routing({'claim_reviews':[row]},
            reviewer_fingerprint_sha256=reviewer['fingerprint_sha256'],spot_check_percent=0)}
    review_path=tmp_path/'reviews'/'sample.independent-review.json'
    save(review_path,review)
    normalized,_=_normalize_records(package)
    operations=[]
    for col in ('claims','evidence_steps','source_fragments'):
        for oid,payload in normalized[col].items():
            after=dict(payload,revision=3)
            if col=='claims':
                after.update(review_status='ai_consensus_reviewed',review_note='OLD DIFFERENT CONTENT REVIEW',
                    reviewed_by='old reviewer',reviewed_at='old time')
            operations.append({'operation':'update','collection':col,'object_id':oid,
                'after':after,'after_revision':3,'after_sha256':record_content_sha(after)})
    claim=operations[0]['after']
    previous=('KCS-import',record_content_sha(claim),claim)
    history={'status':'applied','source_kind':'knowledge_package',
        'metadata':{'input_path':str(tmp_path/'reviewed'/'sample.reviewed-candidate.json')},'operations':operations}
    return previous,history,source,review_path,package_path


def test_exact_passing_input_authenticates_committed_import_not_old_note(imported_case):
    previous,history,source,path,package_path=imported_case
    before=deepcopy((previous,history))
    proof=imported_passing_review('CL-test',previous,history,source)
    assert proof['review_event_id'] is None  # no invented SQL review event
    assert proof['review_authority_kind']=='exact_passing_input_and_committed_import_graph'
    assert proof['review_binding']['file_sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
    assert proof['qualification_file_bytes']<32*1024**2
    assert proof['unrelated_findings_released'] is False
    assert (previous,history)==before


@pytest.mark.parametrize('mutation',[
    'uncommitted','wrong_source_kind','missing_input_location','missing_review','old_text',
    'changed_evidence','missing_fragment','fragment_foreign_source','target_sha','duplicate_operation',
    'review_snapshot','review_fingerprint','request_fingerprint','source_changed','foreign_source_path',
    'source_identity','invalid_seal','spot_check','conflicting_review','ambiguous_pass',
])
def test_imported_authority_rejects_unbound_stale_changed_or_held(imported_case,mutation):
    previous,history,source,path,package_path=imported_case
    review=json.loads(path.read_text())
    if mutation=='uncommitted':history['status']='pending'
    elif mutation=='wrong_source_kind':history['source_kind']='anything_else'
    elif mutation=='missing_input_location':history['metadata']={}
    elif mutation=='missing_review':path.unlink()
    elif mutation=='old_text':previous[2]['statement']='The old unrelated humility claim.'
    elif mutation=='changed_evidence':
        history['operations'][1]['after']['statement']='Stronger different statement.'
        history['operations'][1]['after_sha256']=record_content_sha(history['operations'][1]['after'])
    elif mutation=='missing_fragment':history['operations'].pop()
    elif mutation=='fragment_foreign_source':
        history['operations'][-1]['after']['source_id']='foreign'
        history['operations'][-1]['after_sha256']=record_content_sha(history['operations'][-1]['after'])
    elif mutation=='target_sha':history['operations'][0]['after_sha256']='f'*64
    elif mutation=='duplicate_operation':history['operations'].append(history['operations'][0])
    elif mutation=='review_snapshot':review['reviewed_claims'][0]['statement']='Different snapshot'
    elif mutation=='review_fingerprint':review['reviewer']['review_model_id']='different model'
    elif mutation=='request_fingerprint':review['source']['source_candidate_fingerprint']='f'*64
    elif mutation=='source_changed':save(source['source_path'],{'script':[{'text':'Changed source.'}]})
    elif mutation=='foreign_source_path':review['source']['transcript_paths']['SRC-test']=str(path)
    elif mutation=='source_identity':source['transcript_id']='foreign sermon'
    elif mutation=='invalid_seal':review['reviewer']['artifact_sha256']='f'*64
    elif mutation=='spot_check':
        review['spot_check_percent']=100
        review.update(apply_risk_routing({'claim_reviews':review['claim_reviews']},
            reviewer_fingerprint_sha256=review['reviewer']['fingerprint_sha256'],spot_check_percent=100))
    elif mutation in {'conflicting_review','ambiguous_pass'}:
        other=deepcopy(review)
        other['claim_reviews'][0]['rationale']='Another actual review.'
        if mutation=='conflicting_review':
            other['claim_reviews'][0].update(decision='human_review_required',
                issues=[{'issue_type':'evidence_insufficient','severity':'high','explanation':'Unclear',
                    'affected_anchor_indexes':[],'duplicate_of_claim_id':''}],human_review_reason='Unclear source')
            other.update(apply_risk_routing({'claim_reviews':other['claim_reviews']},
                reviewer_fingerprint_sha256=other['reviewer']['fingerprint_sha256'],spot_check_percent=0))
        save(path.parent/'review-generations'/'sample.independent-review.another.json',other)
    if path.exists():save(path,review)
    with pytest.raises(ValueError):imported_passing_review('CL-test',previous,history,source)


def test_identical_canonical_and_archive_do_not_create_second_approval(imported_case):
    previous,history,source,path,_=imported_case
    save(path.parent/'review-generations'/'sample.independent-review.identical.json',json.loads(path.read_text()))
    assert imported_passing_review('CL-test',previous,history,source)['review_event_id'] is None


def test_large_generation_is_rejected_before_reading(imported_case):
    previous,history,source,path,_=imported_case
    with path.open('wb') as f:f.truncate(8*1024**2+1)
    with pytest.raises(ValueError,match='bounded review input'):
        imported_passing_review('CL-test',previous,history,source)


@pytest.mark.parametrize('change', ['old_same_id_text','human_hold'])
def test_valid_review_of_old_text_or_held_input_is_not_current_authority(imported_case,change):
    previous,history,source,path,package_path=imported_case
    package=json.loads(package_path.read_text());review=json.loads(path.read_text())
    if change=='old_same_id_text':package['claims'][0]['title']='Old humility content, same ID.'
    else:package['claims'][0]['review_status']='human_hold'
    review['source']['package_sha256']=save(package_path,package)
    origin=review['source']
    identity={k:origin[k] for k in ('input_mode','projection_version','package_sha256',
        'transcript_sha256','transcript_id')}
    fingerprint=sha256_json(identity)
    origin['source_candidate_fingerprint']=fingerprint
    review['reviewer']['source_extraction_fingerprint']=fingerprint
    reviewer_identity={k:v for k,v in review['reviewer'].items()
        if k not in {'provider','generated_at','fingerprint_sha256','artifact_sha256'}}
    review['reviewer']['fingerprint_sha256']=sha256_json(reviewer_identity)
    review['reviewed_claims']=_normalize_claim_layer(package)['candidate_claims']
    review.update(apply_risk_routing({'claim_reviews':review['claim_reviews']},
        reviewer_fingerprint_sha256=review['reviewer']['fingerprint_sha256'],spot_check_percent=0))
    save(path,review)
    message='missing/ambiguous exact imported review' if change=='old_same_id_text' else 'real hold'
    with pytest.raises(ValueError,match=message):
        imported_passing_review('CL-test',previous,history,source)
