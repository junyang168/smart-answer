from copy import deepcopy
import pytest
from backend.api.canonical_repository.reviewed_candidate_contract import validate_store_package_authorization, ConsensusApplicationError
from backend.api.canonical_repository.viewpoint_foundation import sha256_json, semantic_record_sha


def fixture():
    old={'claim_id':'C1','revision':1,'statement':'魂不是靈魂。','review_status':'ai_consensus_reviewed',
         'evidence_step_ids':['E1','E2'],'scripture_refs':['Luke12:20','Matt2:20'],'occurrences':[]}
    new=deepcopy(old); new.update(revision=2,statement='在路12:20中，'+old['statement'])
    before={'viewpoint_claim_link_id':'L1','claim_id':'C1','revision':2,'pinned_claim_revision':1,
            'link_type':'equivalent_component','decision_id':'historical','evidence_bindings':[{'evidence_step_id':'E1'}],
            'component_locator':{}}
    link=deepcopy(before); link.update(revision=3,pinned_claim_revision=2,component_locator={
        'claim_sha256':semantic_record_sha(new),'statement_component':new['statement'],
        'canonical_spans':[{'start_char':0,'end_char':len(new['statement']),'exact_text':new['statement']}]})
    body={'package_id':'repair','claims':[new],'viewpoint_claim_links':[link]}
    packet={'before_claim':old,'before_link':before,'qualifier_prefix':'在路12:20中，',
            'proposed_package':deepcopy(body),'package_sha256':sha256_json(body),'after_claim_semantic_sha256':semantic_record_sha(new)}
    p=body|{'source_repair_application':{'schema_version':'wang_claim_qualifier_repair_v2','user_authorization':'owner agreed',
        'approval_status':'not_human_approved','review_packet':packet,'review_artifacts':[]}}
    reseal(p); return p


def reseal(p):
    app=p['source_repair_application']; packet=app['review_packet']
    body={k:v for k,v in p.items() if k!='source_repair_application'}
    packet.update(proposed_package=deepcopy(body),package_sha256=sha256_json(body))
    app['review_artifacts']=[]
    for backend in ('codex_subscription','claude_subscription'):
        response={'verdicts':[{'claim_id':'C1','approved':True,'problems':[]}]}
        raw={'backend':backend,'response':response,'response_sha256':sha256_json(response),'request_payload_sha256':sha256_json(packet)}
        app['review_artifacts'].append(raw|{'artifact_sha256':sha256_json(raw)})


def test_exact_qualifier_and_same_identity_pass():
    validate_store_package_authorization(fixture())


@pytest.mark.parametrize('mutate',[
    lambda p:p['claims'][0].update(statement='other theology'),
    lambda p:p['claims'][0]['evidence_step_ids'].pop(),
    lambda p:p['claims'][0]['scripture_refs'].pop(),
    lambda p:p['claims'][0].update(review_status='human_approved'),
    lambda p:p['viewpoint_claim_links'][0].update(link_type='supports'),
    lambda p:p['viewpoint_claim_links'][0].update(decision_id='unreviewed'),
    lambda p:p['viewpoint_claim_links'][0]['evidence_bindings'].clear(),
    lambda p:p['viewpoint_claim_links'][0].update(pinned_claim_revision=1),
    lambda p:p['viewpoint_claim_links'][0]['component_locator']['canonical_spans'][0].update(end_char=1),
    lambda p:p.update(evidence_steps=[]),
])
def test_resealed_semantic_escape_still_fails(mutate):
    p=fixture(); mutate(p); reseal(p)
    with pytest.raises(ConsensusApplicationError): validate_store_package_authorization(p)


@pytest.mark.parametrize('mutate',[
    lambda p:p['source_repair_application']['review_artifacts'].pop(),
    lambda p:p['source_repair_application']['review_artifacts'][1].update(backend='codex_subscription'),
    lambda p:p['source_repair_application']['review_artifacts'][0].update(request_payload_sha256='old'),
    lambda p:p['source_repair_application']['review_artifacts'][0]['response']['verdicts'][0].update(approved=False),
    lambda p:p['source_repair_application'].update(approval_status='human_approved'),
    lambda p:p['claims'][0].update(statement='changed after review'),
])
def test_missing_stale_or_fake_approval_fails(mutate):
    p=fixture(); mutate(p)
    with pytest.raises(ConsensusApplicationError): validate_store_package_authorization(p)
