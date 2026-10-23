from copy import deepcopy

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_owner_evidence_annotation import (
    VERSION, PROMPT, validate_package, validate_postimage, verify_files,
)


def packet():
    before={'evidence_step_id':'E1','revision':1,'review_status':'candidate',
        'support_eligibility':'withheld_unreviewed','statement':'original statement',
        'source_fragment_ids':['F1'],'scripture_refs':['reference'], 'produced_claim_ids':['C1']}
    annotation={'attribution':'editorial','not_a_professor_quote':True,'audio_verified':False,
        'note':'owner correction','source_binding':{'sha':'source pin'}}
    auth={'action':'authorize_exact_evidence_erratum','owner_message':'同意','evidence_step_id':'E1','annotation':annotation}
    after=deepcopy(before)|{'revision':2,'review_status':'ai_consensus_reviewed',
        'support_eligibility':'eligible_with_label','editorial_annotations':[annotation]}
    return {'owner_authorization':auth,'before_evidence':before,'after_evidence':after,
        'claim':{'claim_id':'C1','evidence_step_ids':['E1']}}


def package():
    p=packet(); body={'package_id':'repair','evidence_steps':[p['after_evidence']]}
    p['package_sha256']=sha256_json(body)
    raws=[]
    for backend,model in [('codex_subscription','gpt-6.1-sol'),('claude_subscription','claude-opus-5-5')]:
        response={'evidence_step_id':'E1','approved':True,'reason':'supported','problems':[]}
        import hashlib
        raw={'backend':backend,'model_id':model,'request_payload_sha256':sha256_json(p),
            'prompt_sha256':sha256_json({'prompt': PROMPT}),
            'response':response,'response_sha256':sha256_json(response)}
        raws.append(raw|{'artifact_sha256':sha256_json(raw)})
    return body|{'source_repair_application':{'schema_version':VERSION,'review_packet':p,'review_artifacts':raws}}


def test_exact_append_allowed_and_reviewed_contract_valid():
    validate_postimage(packet())
    validate_package(package())


def test_raw_text_hash_is_not_runtime_prompt_identity():
    import hashlib
    pkg = package()
    raw = pkg['source_repair_application']['review_artifacts'][0]
    raw['prompt_sha256'] = hashlib.sha256(PROMPT.encode()).hexdigest()
    raw['artifact_sha256'] = sha256_json({k:v for k,v in raw.items() if k != 'artifact_sha256'})
    with pytest.raises(ValueError, match='binding mismatch'):
        validate_package(pkg)


@pytest.mark.parametrize('field,value',[
    ('statement','rewritten quote'),('source_fragment_ids',['foreign']),
    ('scripture_refs',['changed range']),('produced_claim_ids',['foreign']),
    ('revision',3),('review_status','human_approved'),('support_eligibility','eligible'),
])
def test_unapproved_postimage_change_rejected(field,value):
    p=packet(); p['after_evidence'][field]=value
    with pytest.raises(ValueError): validate_postimage(p)


@pytest.mark.parametrize('field,value',[
    ('attribution','professor'),('not_a_professor_quote',False),('audio_verified',True),
])
def test_owner_annotation_cannot_impersonate_verified_professor(field,value):
    p=packet(); p['owner_authorization']['annotation'][field]=value
    with pytest.raises(ValueError): validate_postimage(p)


def test_old_annotation_may_not_be_removed():
    p=packet(); p['before_evidence']['editorial_annotations']=[{'note':'old'}]
    with pytest.raises(ValueError): validate_postimage(p)


def test_foreign_claim_membership_rejected():
    p=packet(); p['claim']['evidence_step_ids']=[]
    with pytest.raises(ValueError): validate_postimage(p)


@pytest.mark.parametrize('mode',['missing_review','wrong_backend','rejection','problems','raw_sha','request_sha','response_sha','foreign_id','postimage','extra_collection'])
def test_false_review_authority_rejected(mode):
    pkg=package(); app=pkg['source_repair_application']; raw=app['review_artifacts'][0]
    if mode=='missing_review': app['review_artifacts'].pop()
    elif mode=='wrong_backend': raw['backend']='api'
    elif mode=='rejection': raw['response']['approved']=False
    elif mode=='problems': raw['response']['problems']=['issue']
    elif mode=='raw_sha': raw['artifact_sha256']='bad'
    elif mode=='request_sha': raw['request_payload_sha256']='bad'
    elif mode=='response_sha': raw['response_sha256']='bad'
    elif mode=='foreign_id': raw['response']['evidence_step_id']='foreign'
    elif mode=='postimage': pkg['evidence_steps'][0]['statement']='changed'
    elif mode=='extra_collection': pkg['claims']=[]
    with pytest.raises(ValueError): validate_package(pkg)


def test_changed_physical_mother_rejected(tmp_path):
    import hashlib
    p=tmp_path/'mother'; p.write_text('source')
    binding={'path':str(p),'file_sha256':hashlib.sha256(b'source').hexdigest()}
    verify_files({'physical_bindings':[binding]})
    p.write_text('changed')
    with pytest.raises(ValueError): verify_files({'physical_bindings':[binding]})


def test_store_invokes_narrow_contract():
    from backend.api.canonical_repository.reviewed_candidate_contract import validate_store_package_authorization
    validate_store_package_authorization(package())
    p=package(); p['source_repair_application']['review_artifacts']=[]
    with pytest.raises(ValueError): validate_store_package_authorization(p)
