from copy import deepcopy
import json
from types import SimpleNamespace
import pytest
from backend.tests.test_viewpoint_historical_review_bridge import inputs
from backend.pipeline.viewpoint_historical_review_bridge import _sealed_adjudicated_bridge
from backend.api.canonical_repository.viewpoint_production_safety import CvpProductionBlocked
import backend.api.canonical_repository.viewpoint_source_attestation as authority


def arguments(tmp_path, monkeypatch):
    args=inputs(tmp_path); args['current_claim']=args['versions'][0]['payload']; args['versions']=args['versions'][:1]
    package=json.loads((tmp_path/'input.json').read_text()); review=json.loads((tmp_path/'review.json').read_text())
    review['claim_reviews'][0]['decision']='changes_suggested'
    resolution={'reviewer_id':'claude+gpt','independent_review_decision':'changes_suggested','adjudication_status':'auto_applied'}
    item={'payload':package,'review_payload':review,'adjudication_payload':{'results':[{'claim_id':'C1','status':'auto_applied'}]},
          'application':{'artifact_sha256':'sealed'},'review_input_sha256':args['review_input_binding']['file_sha256']}
    for key,name in [('path','input.json'),('review_path','review.json'),('adjudication_path','input.json'),('overrides_path','input.json')]:
        item[key]=tmp_path/name
    args.update(item=item,resolution=resolution,sealed_lineage_binding={'path':'/authenticated-chain','file_sha256':'chain'})
    monkeypatch.setattr(authority,'build_source_eligibility_artifact',lambda **kwargs:SimpleNamespace(exceptions=[],attestations=[1],model_dump=lambda **kwargs:{'status':'modern-authority-verified'}))
    return args


def test_adjudicated_proof_does_not_claim_initial_independent_pass(tmp_path,monkeypatch):
    proof=_sealed_adjudicated_bridge(**arguments(tmp_path,monkeypatch))
    assert proof['authority_kind']=='sealed_adjudicated_final_candidate_not_initial_review_pass'
    assert proof['independent_review_decision']=='changes_suggested'
    assert proof['adjudication_status']=='auto_applied'
    assert proof['master_data_mutations']==proof['model_calls_executed']==0


@pytest.mark.parametrize('mutation',['statement','evidence','fragment','history','human_hold','rejected_authority'])
def test_adjudicated_bridge_cannot_bypass_source_or_human_gates(tmp_path,monkeypatch,mutation):
    args=arguments(tmp_path,monkeypatch)
    if mutation=='statement': args['current_claim']['statement']='changed'
    elif mutation=='evidence': args['current_evidence']['E1']['statement']='changed'
    elif mutation=='fragment': args['current_fragments']['F1']['verbatim_excerpt']='changed'
    elif mutation=='history': args['versions'][0]['content_sha256']='wrong'
    elif mutation=='human_hold': args['item']['review_payload']['claim_reviews'][0]['spot_check_selected']=True
    else: monkeypatch.setattr(authority,'build_source_eligibility_artifact',lambda **kwargs:SimpleNamespace(exceptions=['unapplied_review_change'],attestations=[]))
    with pytest.raises(CvpProductionBlocked): _sealed_adjudicated_bridge(**args)
