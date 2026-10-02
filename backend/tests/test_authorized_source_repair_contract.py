from copy import deepcopy
import hashlib
import pytest
from backend.api.canonical_repository.reviewed_candidate_contract import (
    ConsensusApplicationError, validate_store_package_authorization,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json


def fixture_package():
    old={'claim_id':'C1','claim_type':'interpretive_judgment','revision':3,'statement':'old','review_status':'ai_consensus_reviewed',
         'evidence_step_ids':['E1'],'occurrences':[{'source_id':'S1','anchors':[{'evidence_id':'E1'}]}]}
    new=deepcopy(old); new.update(revision=4,statement='new')
    new['evidence_step_ids'].append('E2'); new['occurrences'][0]['anchors'].append({'evidence_id':'E2'})
    step={'evidence_step_id':'E2','produced_claim_ids':['C1'],'source_fragment_ids':['F2']}
    frag={'fragment_id':'F2','source_id':'S1','source_sha256':'body','verbatim_excerpt':'new',
          'verbatim_excerpt_sha256':hashlib.sha256(b'new').hexdigest(),
          'paragraph_text_sha256':hashlib.sha256(b'new context').hexdigest()}
    package={'package_id':'test','claims':[new],'evidence_steps':[step],
             'source_fragments':[frag],'viewpoint_claim_links':[]}
    packet={'package_sha256':sha256_json(package),'source_body_sha256':'body',
            'items':[{'before':old,'after':new,'new_evidence_step':step,'new_fragment':frag,
                      'source_paragraph':'new context','updated_links':[]}]}
    artifacts=[]
    for backend in ('codex_subscription','claude_subscription'):
        response={'verdicts':[{'claim_id':'C1','approved':True,'problems':[]}]}
        raw={'backend':backend,'response':response,'response_sha256':sha256_json(response),
             'request_payload_sha256':sha256_json(packet)}
        artifacts.append(raw|{'artifact_sha256':sha256_json(raw)})
    package['source_repair_application']={'schema_version':'wang_authorized_source_repair_v1',
        'user_authorization':'explicit scoped approval','approval_status':'not_human_approved',
        'review_packet':packet,'review_artifacts':artifacts}
    return package


def test_valid_fresh_repair_authority():
    validate_store_package_authorization(fixture_package())


@pytest.mark.parametrize('mutation',[
    lambda p:p['claims'][0].update(statement='tampered'),
    lambda p:p.update(canonical_viewpoints=[{'statement':'unauthorized'}]),
    lambda p:p['source_repair_application'].update(user_authorization=''),
    lambda p:p['source_repair_application'].update(approval_status='human_approved'),
    lambda p:p['source_repair_application']['review_artifacts'].pop(),
    lambda p:p['source_repair_application']['review_artifacts'][1].update(backend='codex_subscription'),
    lambda p:p['source_repair_application']['review_artifacts'][0]['response']['verdicts'][0].update(approved=False),
    lambda p:p['source_repair_application']['review_artifacts'][0].update(request_payload_sha256='stale'),
    lambda p:p['source_repair_application']['review_packet']['items'][0]['before'].update(revision=1),
])
def test_rejects_tampering_and_missing_authority(mutation):
    package=fixture_package(); mutation(package)
    with pytest.raises(ConsensusApplicationError):
        validate_store_package_authorization(package)


def test_existing_claim_authority_not_relaxed():
    with pytest.raises(ConsensusApplicationError,match='sealed final'):
        validate_store_package_authorization({'claims':[{'review_status':'ai_consensus_reviewed'}]})
