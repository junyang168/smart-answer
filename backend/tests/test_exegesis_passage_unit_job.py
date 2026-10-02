import pytest
from backend.pipeline.exegesis_passage_unit_job import normalize, plan_schema, primary_fits


def plan(ids):
    return dict(units=[dict(passage_key='Matt.16.28-Matt.17.8',rationale='promise and fulfillment',needs_context=False,context_reason='')],
        assignments={i:dict(unit_index=0) for i in ids})


def test_whole_book_schema_requires_every_member_and_l1_has_no_twenty_limit():
    ids=[f'c{i:04d}' for i in range(973)]
    schema=plan_schema(ids)
    assert schema['properties']['assignments']['required']==ids
    assert not schema['properties']['assignments']['additionalProperties']
    assert len(normalize(plan(ids),ids)['units'][0]['claim_ids'])==973


@pytest.mark.parametrize('bad', ['missing','foreign','invalid-index','empty-unit','reversed','cross-book'])
def test_complete_membership_and_range_guards(bad):
    p=plan(['a','b'])
    if bad=='missing':p['assignments'].pop('a')
    if bad=='foreign':p['assignments']['z']=dict(unit_index=0)
    if bad=='invalid-index':p['assignments']['a']['unit_index']=3
    if bad=='empty-unit':p['units'].append(dict(p['units'][0]))
    if bad=='reversed':p['units'][0]['passage_key']='Matt.17.8-Matt.16.28'
    if bad=='cross-book':p['units'][0]['passage_key']='Matt.28-Luke.1'
    with pytest.raises(ValueError):normalize(p,['a','b'])


def test_primary_containment_keeps_chapter_only_but_catches_truncated_structural_claim():
    assert primary_fits('Matt.2','Matt.2.16-Matt.2.18')
    assert primary_fits('Matt.16.28','Matt.16.28-Matt.17.8')
    assert not primary_fits('Matt.5.17-Matt.7.29','Matt.5.17-Matt.7.12')
    assert not primary_fits('Matt.5-Matt.7','Matt.5-Matt.7.12')
    assert not primary_fits('Luke.12.20','Matt.2.19-Matt.2.23')


def test_background_stage_accepts_cli_path_and_reuses_bound_response(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from pathlib import Path
    from backend.pipeline import exegesis_passage_unit_job as module
    from backend.pipeline.exegesis_grouping_transport import write_new
    args=SimpleNamespace(codex_executable=Path('/fake/codex'),model='gpt-6.1-sol',code_sha='code',max_gpt_bytes=2500000,timeout=1)
    calls=[]
    def fake_call(**kwargs):
        calls.append(kwargs);kwargs['directory'].mkdir()
        write_new(kwargs['directory']/'response.json',dict(response={'ok':True}))
        return {'ok':True}
    monkeypatch.setattr(module,'call',fake_call)
    payload={'claims':[{'id':'a'}]}
    assert module.model_stage(tmp_path,'generation','prompt',payload,{'type':'object'},args)=={'ok':True}
    assert module.model_stage(tmp_path,'generation','prompt',payload,{'type':'object'},args)=={'ok':True}
    assert len(calls)==1
    assert calls[0]['model']=='gpt-6.1-sol' and calls[0]['effort']=='high'
    with pytest.raises(ValueError,match='cached artifact differs'):
        module.model_stage(tmp_path,'generation','changed prompt',payload,{'type':'object'},args)


def test_reuse_completed_whole_book_plan_preserves_every_boundary_and_member():
    from backend.pipeline.exegesis_passage_unit_job import import_proposal
    seed=dict(units=[dict(unit_id='u700',passage_key='Matt.16.28-Matt.17.8',rationale='existing reason',needs_context=False,context_reason='',claim_ids=['C1','C2'])])
    converted=import_proposal(seed,{'a':'C1','b':'C2'})
    assert converted['units']==[dict(seed['units'][0],claim_ids=['a','b'])]
    assert converted['assignments']=={'a':dict(unit_index=0),'b':dict(unit_index=0)}
    assert seed['units'][0]['claim_ids']==['C1','C2']
    with pytest.raises(ValueError):import_proposal(seed,{'a':'C1','b':'C2','c':'C3'})


def test_l1_correction_cannot_request_or_rewrite_primary():
    schema=plan_schema(['a'],{'u001':{}})
    assert 'primary' not in schema['$defs']['assignment']['properties']
    response=plan(['a']);response['assignments']['a']['primary']='Matt.16.19'
    with pytest.raises(ValueError,match='cannot rewrite primary'):normalize(response,['a'])
