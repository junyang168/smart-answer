import json
from types import SimpleNamespace

import pytest

from backend.pipeline import viewpoint_resolution_runtime as runtime


def authorization(tmp_path, **changes):
    path=tmp_path/'owner.json'
    body={'schema_version':'cvp_sql_request_limit_authorization.v1','campaign_sha256':'campaign',
          'max_request_bytes':600000,'authorized_by':'owner','owner_direction':'600K'}
    body.update(changes)
    path.write_text(json.dumps(body))
    return path


def test_scoped_limit_preserves_adapter_and_resets(tmp_path):
    root=tmp_path/'execution';a=SimpleNamespace(max_request_bytes=500000)
    auth=authorization(tmp_path)
    with runtime.authorized_request_limit(root,'campaign',auth):
        limit,proof=runtime.effective_request_limit(a,root/'raw.json')
        assert limit==600000 and proof['campaign_sha256']=='campaign'
        assert a.max_request_bytes==500000
        with pytest.raises(ValueError,match='outside'):
            runtime.effective_request_limit(a,tmp_path/'other.json')
    assert runtime.effective_request_limit(a,root/'raw.json')==(500000,None)
    with pytest.raises(ValueError,match='explicit'):
        with runtime.authorized_request_limit(root,'campaign'):pass


@pytest.mark.parametrize('changes',[{'campaign_sha256':'foreign'}, {'max_request_bytes':True},
    {'max_request_bytes':0},{'authorized_by':'model'},{'owner_direction':''}])
def test_invalid_authority(tmp_path,changes):
    with pytest.raises(ValueError,match='invalid'):
        with runtime.authorized_request_limit(tmp_path/'execution','campaign',authorization(tmp_path,**changes)):pass


def test_changed_receipt_rejected_before_call(tmp_path):
    root=tmp_path/'execution';auth=authorization(tmp_path)
    with runtime.authorized_request_limit(root,'campaign',auth):
        auth.write_text(auth.read_text()+' ')
        with pytest.raises(ValueError,match='drift'):
            runtime.effective_request_limit(SimpleNamespace(max_request_bytes=500000),root/'raw.json')


class Adapter:
    model_id='fake'
    backend='fake-subscription'
    prompt_sha256='prompt'
    generation_config_sha256='config'
    max_request_bytes=500000
    calls=0

    def request_bytes(self,payload):return payload['bytes']
    def generate(self,payload):
        self.calls+=1
        return {'approved':True}


def test_budget_changes_no_request_or_cached_identity_and_still_rejects_over_limit(tmp_path):
    root=tmp_path/'execution';root.mkdir();a=Adapter();auth=authorization(tmp_path)
    legacy=root/'legacy.json'
    runtime.call_model(a,{'bytes':499999},legacy)
    old=legacy.read_bytes()
    with pytest.raises(ValueError,match='500000'):
        runtime.call_model(a,{'bytes':511625},root/'raw.json')
    assert not (root/'raw.call-intent.json').exists()
    with runtime.authorized_request_limit(root,'campaign',auth):
        assert runtime.call_model(a,{'bytes':499999},legacy)[1]==0
        assert legacy.read_bytes()==old
        assert runtime.call_model(a,{'bytes':511625},root/'raw.json')[1]==1
        assert runtime.call_model(a,{'bytes':511625},root/'raw.json')[1]==0
        runtime.call_model(a,{'bytes':600000},root/'exact.json')
        with pytest.raises(ValueError,match='600000'):
            runtime.call_model(a,{'bytes':600001},root/'over.json')
        assert not (root/'over.call-intent.json').exists()
    assert a.calls==3


def test_unknown_intent_cannot_be_reissued_with_larger_budget(tmp_path):
    root=tmp_path/'execution';root.mkdir();a=Adapter();auth=authorization(tmp_path)
    (root/'raw.call-intent.json').write_text('{}')
    with runtime.authorized_request_limit(root,'campaign',auth):
        with pytest.raises(ValueError,match='outcome unknown'):
            runtime.call_model(a,{'bytes':511625},root/'raw.json')
    assert a.calls==0 and not (root/'raw.request-budget.json').exists()


def test_temporary_run_amendment_preserves_original_and_other_runs(tmp_path):
    from backend.pipeline.cvp_sql_flow import binding
    root=tmp_path/'execution';auth=authorization(tmp_path)
    with runtime.authorized_request_limit(root,'campaign',auth):pass
    old=(root/'request-limit-binding.json').read_bytes()
    run='CVPG-'+'a'*64
    amended=tmp_path/'temporary.json'
    body=json.loads(auth.read_text())
    body.update(max_request_bytes=700000, supersedes_authorization=binding(auth), temporary_run_id=run)
    amended.write_text(json.dumps(body))
    a=SimpleNamespace(max_request_bytes=500000)
    with runtime.authorized_request_limit(root,'campaign',amended):
        assert runtime.effective_request_limit(a,root/run/'raw.json')[0]==700000
        assert runtime.effective_request_limit(a,root/('CVPG-'+'b'*64)/'raw.json')[0]==600000
    assert (root/'request-limit-binding.json').read_bytes()==old
    with runtime.authorized_request_limit(root,'campaign',auth):
        assert runtime.effective_request_limit(a,root/run/'raw.json')[0]==600000
    body['supersedes_authorization']['file_sha256']='f'*64
    amended.write_text(json.dumps(body))
    with pytest.raises(ValueError,match='predecessor'):
        with runtime.authorized_request_limit(root,'campaign',amended):pass


def temporary_source_budget(tmp_path):
    from backend.pipeline.cvp_sql_flow import binding
    root=tmp_path/'execution';auth=authorization(tmp_path)
    with runtime.authorized_request_limit(root,'campaign',auth):pass
    run='CVPG-'+'a'*64
    amended=tmp_path/'temporary.json'
    body=json.loads(auth.read_text())
    body.update(max_request_bytes=700000,supersedes_authorization=binding(auth),temporary_run_id=run)
    amended.write_text(json.dumps(body))
    return root,auth,amended,run


def test_source_temporary_budget_all_stages_and_saved_reuse(tmp_path):
    from backend.pipeline.source_reconciliation_runner import request_limit
    root,auth,amended,run=temporary_source_budget(tmp_path)
    a=SimpleNamespace(max_request_bytes=500000)
    original=(root/'request-limit-binding.json').read_bytes()
    with runtime.authorized_request_limit(root,'campaign',amended):
        for stage in ('proposal','initial_review','correction','final_review'):
            path=root/run/'claim-issues'/'action'/(stage+'.raw.json')
            limit,budget=request_limit(a,path)
            assert limit==700000
            assert request_limit(a,root/('CVPG-'+'b'*64)/'action'/(stage+'.raw.json'))[0]==600000
    with runtime.authorized_request_limit(root,'campaign',auth):
        assert request_limit(a,path)[0]==600000
        assert request_limit(a,path,saved={'request_budget':budget})==(700000,budget)
    assert (root/'request-limit-binding.json').read_bytes()==original


@pytest.mark.parametrize('change',['missing_amendment','changed_base','changed_run','changed_owner','foreign_root'])
def test_source_temporary_budget_proof_drift_refused(tmp_path,change):
    from backend.pipeline.source_reconciliation_runner import request_limit
    root,auth,amended,run=temporary_source_budget(tmp_path)
    a=SimpleNamespace(max_request_bytes=500000);path=root/run/'action'/'proposal.raw.json'
    with runtime.authorized_request_limit(root,'campaign',amended):
        _,budget=request_limit(a,path)
    proof=root/('request-limit-amendment-'+budget['authorization']['file_sha256']+'.json')
    if change=='missing_amendment':proof.unlink()
    elif change=='changed_base':
        base=json.loads((root/'request-limit-binding.json').read_text());base['max_request_bytes']=800000
        (root/'request-limit-binding.json').write_text(json.dumps(base))
    elif change=='changed_run':budget=dict(budget,temporary_run_id='CVPG-'+'b'*64)
    elif change=='changed_owner':amended.write_text(amended.read_text()+' ')
    else:path=tmp_path/'foreign'/'proposal.raw.json'
    with pytest.raises(ValueError):request_limit(a,path,saved={'request_budget':budget})
