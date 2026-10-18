from copy import deepcopy

import pytest

from backend.api.canonical_repository.cvp_recall import (
    STATEMENT_SCOPE_PROFILE, build_statement_index, recall_plan, seal,
)
from backend.pipeline.cvp_recall import (
    refresh_recall, recall_adapter, _authenticate_recall_root,
    RECALL_PROMPT, STATEMENT_SCOPE_RECALL_PROMPT,
)
from backend.pipeline.cvp_recall_preparation import prepare_recall
from test_cvp_recall import heads, claims
from test_cvp_recall_stages import Client
from test_cvp_recall_preparation import Store, manifest


def test_complete_heads_project_only_statement_and_scripture_scope():
    hs=heads(750);hs[0]['scope']['scripture_scope']=['馬太福音24:26-28']
    ix=build_statement_index(hs);client=Client()
    plan=recall_plan(ix,claims(2),max_request_bytes=500000,
        measure=lambda p:recall_adapter(client,p,max_request_bytes=500000).request_bytes(p))
    assert len(plan['shards'])==1
    payload=plan['shards'][0]
    assert payload['input_profile']==STATEMENT_SCOPE_PROFILE
    assert len(payload['viewpoints'])==750
    assert all(set(r)=={'cvp_ref','core_proposition','scripture_scope'} for r in payload['viewpoints'])
    assert payload['viewpoints'][0]['scripture_scope']==['馬太福音24:26-28']
    assert '不要求覆盖整条Claim' in STATEMENT_SCOPE_RECALL_PROMPT
    assert '不要求覆盖整条Claim' not in RECALL_PROMPT
    with pytest.raises(ValueError,match='capacity'):
        recall_plan(ix,claims(2),max_request_bytes=1)


def test_new_preflight_needs_no_summary_but_preserves_legacy_admission():
    store=Store(2)
    assert prepare_recall(store,manifest(store))['status']=='index_required'
    result=prepare_recall(store,manifest(store,recall_profile=STATEMENT_SCOPE_PROFILE))
    assert result['status']=='prepared' and result['model_calls']==0
    assert result['recall_plans'][0]['plan']['shards'][0]['input_profile']==STATEMENT_SCOPE_PROFILE
    with pytest.raises(ValueError,match='profile'):
        prepare_recall(store,manifest(store,recall_profile='unknown'))
    store.quals['C0']['ready']=False
    with pytest.raises(ValueError,match='qualification'):
        prepare_recall(store,manifest(store,recall_profile=STATEMENT_SCOPE_PROFILE))


def test_statement_scope_incremental_recall_replay_and_changed_scope(tmp_path):
    client=Client();hs=heads(3);ix=build_statement_index(hs)
    result,metrics=refresh_recall(ix,claims(2),client=client,output_dir=tmp_path/'first',
        max_request_bytes=100000,max_calls=1)
    assert metrics['model_calls']==1
    same,metrics=refresh_recall(ix,claims(2),client=client,output_dir=tmp_path/'same',
        previous_output_dir=tmp_path/'first',max_request_bytes=100000,max_calls=0)
    assert metrics['model_calls']==0 and same['claim_candidates']==result['claim_candidates']
    changed=deepcopy(hs);changed[0]['scope']['scripture_scope']=['約翰福音5:27']
    current=build_statement_index(changed)
    refreshed,metrics=refresh_recall(current,claims(2),client=client,output_dir=tmp_path/'changed',
        previous_output_dir=tmp_path/'same',max_request_bytes=100000,max_calls=1)
    assert metrics['model_calls']==1 and metrics['reused_cards']==2
    assert metrics['changed_viewpoint_ids']==[hs[0]['viewpoint_id']]
    auth,_=_authenticate_recall_root(tmp_path/'changed',client=client,previous_index=current)
    assert auth['index']==current
    replay,metrics=refresh_recall(current,claims(2),client=client,output_dir=tmp_path/'changed',
        previous_output_dir=tmp_path/'same',max_request_bytes=100000,max_calls=0)
    assert replay==refreshed and metrics['model_calls']==0
