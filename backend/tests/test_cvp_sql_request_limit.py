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
