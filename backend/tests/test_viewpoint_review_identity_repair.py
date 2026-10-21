import hashlib
import json
import os
from copy import deepcopy
from contextvars import ContextVar
from pathlib import Path

import pytest
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline import viewpoint_review_identity_repair as repair


def fixture():
    row={'claim_id':'wrong','component_index':0,'decision':'pass','finding_codes':[],
         'correction':None,'coverage_evidence_step_ids':['E1'],'reason':'Immutable wrong ID in reason'}
    raw={'change_reviews':[row]}
    request={'review_mode':'final_effective_proposal','allows_further_correction':False,
        'packet':{'claims':[{'claim_id':'C1','evidence':[{'evidence_step_id':'E1','valid_for_identity_review':True}]}]},
        'proposal':{'claim_decisions':[{'claim_id':'C1','components':[{}]}]}}
    d={'request_payload_sha256':sha256_json(request),'original_response_sha256':sha256_json(raw),
       'edits':[{'row_index':0,'original_claim_id':'wrong','expected_claim_id':'C1','component_index':0}]}
    return d,request,raw


def test_only_named_id_changes_no_prose_parsing():
    d,p,raw=fixture();before=deepcopy(raw);fixed=repair.diagnostic(d,p,raw)
    assert raw==before and fixed['change_reviews'][0]['claim_id']=='C1'
    fixed['change_reviews'][0]['claim_id']='wrong'
    assert fixed==raw


@pytest.mark.parametrize('field',['decision','reason','coverage_evidence_step_ids','component_index','correction','claim_id','finding_codes'])
def test_returned_response_cannot_change_another_field(field):
    d,p,raw=fixture();expected=repair.diagnostic(d,p,raw);changed=deepcopy(expected)
    changed['change_reviews'][0][field]='changed'
    with pytest.raises(ValueError):repair.validate_format_response(changed,expected)


@pytest.mark.parametrize('fault',['request','response','empty','duplicate','row','bool','negative','oldid','target','same','index','reject','finding','correction','foreign_evidence','ineligible','no_evidence','missing_component','extra_claim','duplicate_claim'])
def test_strict_mapping_boundaries(fault):
    d,p,raw=fixture()
    if fault=='request':d['request_payload_sha256']='0'*64
    elif fault=='response':d['original_response_sha256']='0'*64
    elif fault=='empty':d['edits']=[]
    elif fault=='duplicate':d['edits']*=2
    elif fault=='row':d['edits'][0]['row_index']=10
    elif fault=='bool':d['edits'][0]['row_index']=True
    elif fault=='negative':d['edits'][0]['row_index']=-1
    elif fault=='oldid':d['edits'][0]['original_claim_id']='foreign'
    elif fault=='target':d['edits'][0]['expected_claim_id']='foreign'
    elif fault=='same':d['edits'][0]['expected_claim_id']='wrong'
    elif fault=='index':d['edits'][0]['component_index']=1
    elif fault=='reject':raw['change_reviews'][0]['decision']='reject'
    elif fault=='finding':raw['change_reviews'][0]['finding_codes']=['semantic']
    elif fault=='correction':raw['change_reviews'][0]['correction']='new words'
    elif fault=='foreign_evidence':raw['change_reviews'][0]['coverage_evidence_step_ids']=['foreign']
    elif fault=='ineligible':p['packet']['claims'][0]['evidence'][0]['valid_for_identity_review']=False
    elif fault=='no_evidence':raw['change_reviews'][0]['coverage_evidence_step_ids']=[]
    elif fault=='missing_component':p['proposal']['claim_decisions'][0]['components'].append({})
    elif fault=='extra_claim':p['proposal']['claim_decisions'].append({'claim_id':'extra','components':[{}]})
    else:p['packet']['claims']*=2
    if fault not in ('request','response'):
        d['request_payload_sha256']=sha256_json(p);d['original_response_sha256']=sha256_json(raw)
    with pytest.raises(ValueError):repair.diagnostic(d,p,raw)


@pytest.mark.skipif(os.getenv('WKP_REVIEW_ID_ACTUAL')!='1',reason='explicit actual saved current SQL readonly')
def test_actual_complete_compiler_fixed_parent(monkeypatch):
    from dotenv import load_dotenv
    from backend.tests.test_statement_impact_completion import ReadOnly
    from backend.pipeline import cvp_sql_flow as f,viewpoint_batch_resolution_runner as r
    from backend.pipeline import viewpoint_resolution_runtime as rt,cvp_batch_identity_integration as bi
    load_dotenv('.env');s=ReadOnly()
    rid='CVPG-33eaa47f42cfcb59b69c35a0b944763dd8adf69e744d30f91686f5ce5109c2a2'
    g=s.get_cvp_group(rid);root=Path(g['execution_roots']['group'])/'normal-review'
    path=root.parents[2]/'owner-g154-final-review-claim-id-format-repair.json'
    d=rt.read_artifact(path);seen=[];formats=[]
    def files():return {x:hashlib.sha256(x.read_bytes()).hexdigest() for x in root.rglob('*') if x.is_file()}
    before=files()
    def call(a,p,path):
        selected=rt.response_adapter(a,rt.read_artifact(path));raw=rt.read_saved_model_response(selected,p,path)
        seen.append(path.name)
        if path.name=='raw-final-review.json':
            fixed=repair.diagnostic(d,p,raw)
            outgoing=repair.format_request(d,p,raw)
            proof=selected.request_encoding_proof(outgoing)
            assert proof['exact_roundtrip_verified'] and selected.request_bytes(outgoing)<600000
            print('ACTUAL_ID_FORMAT_PREFLIGHT',selected.request_bytes(outgoing))
            assert len(d['edits'])==5 and len(p['packet']['claims'])==17
            # Synthetic ID-only return tests compilation, never production approval.
            formats.append((selected,p,raw,fixed))
            from backend.api.canonical_repository.viewpoint_response_binding import bind_response
            return bind_response(selected._response_model,fixed,p),0,0.0
        return rt.bound_model_response(selected,p,raw,path,persist=False),0,0.0
    monkeypatch.setattr(bi,'ENABLED',ContextVar('readonly',default=True));monkeypatch.setattr(r,'_call',call)
    monkeypatch.setattr(rt,'validating_saved_artifacts',lambda:True)
    for module,name in ((r,'_write_immutable'),(r,'_write_derived'),(r,'_write_current_state'),(rt,'write_immutable'),(f,'write_immutable')):
        monkeypatch.setattr(module,name,lambda *a,**k:None)
    with rt.authorized_request_limit(root.parents[1],g['campaign_sha256'],root.parents[2]/'owner-request-limit-600k.json'):
        package=f.reviewed_cvp_package(root,g['materials'],s)
    assert len(formats)==1 and seen==['raw-proposal.json','raw-consolidation.json','raw-review.json','raw-reconsideration.json','raw-final-review.json']
    assert package and files()==before and s.get_cvp_group(rid)==g
