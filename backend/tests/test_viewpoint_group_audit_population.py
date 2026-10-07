from copy import deepcopy
import json
import pytest
from backend.pipeline.viewpoint_group_audit_population import group_audit_targets
from backend.api.canonical_repository.viewpoint_foundation import sha256_json


@pytest.fixture
def proof(tmp_path):
    package={'viewpoint_claim_links':[
        {'viewpoint_claim_link_id':'retained','viewpoint_id':'CV-old','effective_state':'active'},
        {'viewpoint_claim_link_id':'new','viewpoint_id':'CV-new','effective_state':'active'}]}
    plan={'source_sha256':sha256_json(package),'change_set_id':'KCS-test','fingerprint_sha256':'plan',
          'operations':[{'collection':'viewpoint_claim_links','operation':'create','payload':package['viewpoint_claim_links'][1]}]}
    receipt={'readback_status':'verified','cvp_changeset_id':'KCS-test','cvp_changeset_sha256':'plan',
        'committed_viewpoint_revisions':[{'viewpoint_id':'CV-old','viewpoint_revision_id':'CVR-old'},
            {'viewpoint_id':'CV-new','viewpoint_revision_id':'CVR-new'}]}
    receipt['artifact_sha256']=sha256_json(receipt)
    return tmp_path,package,plan,receipt


def save(proof):
    root,package,plan,receipt=proof
    for name,value in [('cvp-change-package.json',package),('cvp-change-plan.json',plan),('cvp-readback-receipt.json',receipt)]:
        (root/name).write_text(json.dumps(value))
    return root/'cvp-change-plan.json'


def test_retained_member_is_in_the_actual_full_audit_denominator(proof):
    assert group_audit_targets(save(proof))==['CV-new','CV-old']


@pytest.mark.parametrize('fault',['package','omitted_retained_readback','wrong_kcs','wrong_plan','unverified','receipt_sha','duplicate','foreign_written'])
def test_no_unbound_or_partial_full_audit(proof,fault):
    root,package,plan,receipt=proof
    if fault=='package':package['viewpoint_claim_links'][0]['viewpoint_id']='foreign'
    elif fault=='omitted_retained_readback':receipt['committed_viewpoint_revisions']=receipt['committed_viewpoint_revisions'][1:]
    elif fault=='wrong_kcs':receipt['cvp_changeset_id']='other'
    elif fault=='wrong_plan':receipt['cvp_changeset_sha256']='other'
    elif fault=='unverified':receipt['readback_status']='pending'
    elif fault=='receipt_sha':receipt['artifact_sha256']='bad'
    elif fault=='duplicate':
        package['viewpoint_claim_links'].append(deepcopy(package['viewpoint_claim_links'][0]));plan['source_sha256']=sha256_json(package)
    else:plan['operations'][0]['payload']={**plan['operations'][0]['payload'],'viewpoint_id':'foreign'}
    if fault!='receipt_sha':receipt['artifact_sha256']=sha256_json({k:v for k,v in receipt.items() if k!='artifact_sha256'})
    with pytest.raises(ValueError):group_audit_targets(save(proof))
