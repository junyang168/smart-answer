"""Multiple human decisions are data, not per-group code or mutable slots."""
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import pytest
from backend.pipeline import cvp_retained_review_separation as flow
from backend.pipeline.cvp_sql_flow import binding


@pytest.fixture
def setup(tmp_path):
    def save(group, **patch):
        d={'schema_version':'wang_cvp_human_retained_content_disposition_v1',
            'campaign_sha256':'campaign','group_id':group,'human_exact_disposition':True,
            'owner_reply':'保留，不改','decision':'retain_existing_contextual_equivalence_unchanged',
            'registry_operations':[],'additional_model_calls_authorized':0,
            'group_completion_granted':False,'other_pending_impacts_resolved':False,
            'items':[{'link_id':'L-'+group}]}
        d.update(patch)
        p=tmp_path/(group+str(len(list(tmp_path.glob('*.json'))))+'.json')
        p.write_text(json.dumps(d));return binding(p)
    root=tmp_path/'execution';root.mkdir()
    store=SimpleNamespace(get_cvp_group=lambda g:{'campaign_sha256':'campaign','sequence':int(g[1:])})
    def persist(bs):flow.persist_human_bindings(root,bs,campaign='campaign',store=store,limit=107)
    return root,save,persist


def test_legacy_and_new_group_recovery_preserves_original_bytes(setup):
    root,save,persist=setup;a=save('G94');b=save('G107')
    legacy=root/flow.HUMAN_NAME;legacy.write_text(json.dumps({'human_disposition':a}));before=legacy.read_bytes()
    persist([a,b]);persist([b,a])
    assert legacy.read_bytes()==before
    assert flow.human_binding_for(root/'G94'/'normal-review')==a
    assert flow.human_binding_for(root/'G107'/'normal-review')==b
    assert flow.human_binding_for(root/'G108'/'normal-review') is None
    assert len(list((root/flow.HUMAN_ADDITIONS).glob('*.json')))==1
    with pytest.raises(ValueError):persist([b])


@pytest.mark.parametrize('fault',['duplicate','same-group','foreign','out-of-bound','write','retry','complete','pending','blank','duplicate-link'])
def test_entire_set_validated_before_any_registration(setup,fault):
    root,save,persist=setup;a=save('G94');b=save('G107');bs=[a,b]
    if fault=='duplicate':bs=[a,a]
    elif fault=='same-group':bs=[a,save('G94')]
    elif fault=='foreign':bs=[a,save('G107',campaign_sha256='foreign')]
    elif fault=='out-of-bound':bs=[a,save('G108')]
    else:
        changes={'write':{'registry_operations':['write']},'retry':{'additional_model_calls_authorized':1},
            'complete':{'group_completion_granted':True},'pending':{'other_pending_impacts_resolved':True},
            'blank':{'owner_reply':''},'duplicate-link':{'items':[{'link_id':'L'},{'link_id':'L'}]}}
        bs=[a,save('G107',**changes[fault])]
    with pytest.raises(ValueError):persist(bs)
    assert not list(root.rglob('*.json'))


def test_added_direction_cannot_disappear_on_recovery(setup):
    root,save,persist=setup;a=save('G94');b=save('G107');persist([a,b])
    with pytest.raises(ValueError):persist([a])
    with pytest.raises(ValueError):persist([b])


def test_physical_direction_drift_fails_before_selection(setup):
    root,save,persist=setup;a=save('G94');persist([a])
    Path(a['path']).write_text('{}')
    with pytest.raises(ValueError):flow.human_binding_for(root/'G94'/'normal-review')


def test_disjoint_partial_judgments_merge_as_data_with_original_proofs(setup):
    root,save,persist=setup;a=save('G107');b=save('G107',items=[{'link_id':'OTHER'}])
    persist([a,b])
    bound=flow.human_binding_for(root/'G107'/'normal-review')
    d=json.loads(Path(bound['path']).read_text())
    assert {i['link_id'] for i in d['items']}=={'L-G107','OTHER'}
    assert d['source_decision_bindings']==sorted([a,b],key=lambda x:x['path'])
    assert d['registry_operations']==[] and d['group_completion_granted'] is False
    with pytest.raises(ValueError):persist([a])
    Path(b['path']).write_text('{}')
    with pytest.raises(ValueError):flow.human_binding_for(root/'G107'/'normal-review')


def test_partial_decisions_cannot_mix_final_review_rounds(setup):
    root,save,persist=setup;a=save('G107',complete_context_final_raw_file_sha256='A')
    b=save('G107',items=[{'link_id':'OTHER'}],complete_context_final_raw_file_sha256='B')
    with pytest.raises(ValueError):persist([a,b])
    assert not list(root.rglob('*.json'))
