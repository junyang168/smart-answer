from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
import pytest
from backend.pipeline import source_permission_recovery as r
from backend.pipeline.cvp_sql_flow import binding
from backend.pipeline.source_reconciliation_runner import write,read,body


def fixture(tmp_path,monkeypatch):
    frozen={'manifest':{'source_id':'S','evidence_binding_repair':{'exact':True},
        'permissions':[{'create':True,'collection':'source_fragments','parent_id':'S'}]},'sql_scope':{'run_id':'R'}}
    write(tmp_path/'scoped-materials.json',frozen)
    for stage in ('proposal','initial_review','correction'):
        write(tmp_path/(stage+'.raw.json'),{'response':{'original':stage}})
        write(tmp_path/(stage+'.packet.json'),{'full':'own source'})
    d={'schema_version':r.VERSION,'authorized_by':'owner_engineering','authorization_quote':'fixed code input',
       'human_content_approval':False,'new_proposal_calls':1,'additional_corrections':0,
       'review_stage':'original_unused_final_review','action_root':str(tmp_path),'run_id':'R',
       'materials':binding(tmp_path/'scoped-materials.json'),
       'original_leaves':{stage:binding(tmp_path/(stage+'.raw.json')) for stage in ('proposal','initial_review','correction')}}
    write(tmp_path/'direction.json',d);write(tmp_path/r.NAME,{'authorization':binding(tmp_path/'direction.json')})
    monkeypatch.setattr(r,'validate_raw',lambda root,stage,pkt:{'response':{'original':stage},'artifact_sha256':stage})
    monkeypatch.setattr(r,'build_proposal_packet',lambda f:{'full':'own source'})
    from backend.pipeline import source_reconciliation as c
    monkeypatch.setattr(c,'guard_files',lambda f:None)
    return frozen,d


def test_input_repair_preserves_three_original_answers_and_scope(tmp_path,monkeypatch):
    f,d=fixture(tmp_path,monkeypatch);before={p:p.read_bytes() for p in tmp_path.iterdir()}
    pkt=r.packet(tmp_path,f)
    receipt=pkt['permission_input_recovery']
    assert receipt['human_content_approval'] is False
    assert receipt['new_object_count_limit'] is None
    assert [x['stage'] for x in receipt['original_answers']]==['proposal','initial_review','correction']
    assert all(p.read_bytes()==b for p,b in before.items())


@pytest.mark.parametrize('fault',['calls','correction','scope','parent','no_parent','leaf','material','final'])
def test_fixed_parent_recovery_negative_gates(tmp_path,monkeypatch,fault):
    f,d=fixture(tmp_path,monkeypatch)
    if fault=='calls':d['new_proposal_calls']=2
    elif fault=='correction':d['additional_corrections']=1
    elif fault=='scope':d['action_root']='/foreign'
    elif fault=='parent':f['manifest']['permissions'][0]['parent_id']='foreign'
    elif fault=='no_parent':f['manifest']['permissions'][0].pop('parent_id')
    elif fault=='leaf':d['original_leaves']['proposal']['file_sha256']='f'*64
    elif fault=='material':f['manifest']['source_id']='changed'
    elif fault=='final':write(tmp_path/'final_review.reservation.json',{'intent':True})
    write(tmp_path/'direction.json',d,immutable=False)
    # A changed direction does not get hidden behind an old physical binding.
    write(tmp_path/r.NAME,{'authorization':binding(tmp_path/'direction.json')},immutable=False)
    with pytest.raises(ValueError):r.packet(tmp_path,f)


def test_adapter_identity_isolated_old_contract_untouched():
    original=SimpleNamespace(_prompt='old',_base_prompt='old',generation_config_sha256='old')
    assert r.adapter(original,{}) is original
    updated=r.adapter(original,{'permission_input_recovery':{'exact':True}})
    assert original._prompt=='old' and updated._prompt!='old'
    assert updated.generation_config_sha256!='old'
