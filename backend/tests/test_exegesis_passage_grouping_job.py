import copy
import pytest
from backend.pipeline.exegesis_passage_grouping_job import load_manifest,validate_groups
from backend.pipeline.exegesis_grouping_transport import write_new
from backend.pipeline.viewpoint_passage_grouping_preflight import plan_reviewed_passage_unit


def test_only_reviewed_first_layer_authorizes_second_layer(tmp_path):
    m=dict(schema_version='wang_exegesis_passage_units_v1',layer_1_semantic_passed=True,layer_2_executed=False,
        claim_packets=[dict(claim_id='a'),dict(claim_id='b')],units=[dict(unit_id='p001',passage_key='Matt.16.28-Matt.17.8',claim_ids=['a','b'])])
    write_new(tmp_path/'good.json',m)
    assert load_manifest(tmp_path/'good.json')['units']==m['units']
    for name,override in [('unreviewed',{'layer_1_semantic_passed':False}),('executed',{'layer_2_executed':True}),('duplicated',{'units':[m['units'][0],m['units'][0]]})]:
        write_new(tmp_path/(name+'.json'),m|override)
        with pytest.raises(ValueError):load_manifest(tmp_path/(name+'.json'))


def test_small_passage_cannot_be_mechanically_split():
    unit=dict(unit_id='p001',claim_ids=['a','b'])
    answer=plan_reviewed_passage_unit(unit_id='p001',claim_ids=['a','b'],batch_size=20).model_dump(mode='json')
    validate_groups(answer,unit)
    invalid=copy.deepcopy(answer);invalid['groups'][0]['claim_ids']=['a'];invalid['groups'].append(dict(invalid['groups'][0],group_key='other',claim_ids=['b']))
    with pytest.raises(ValueError,match='single whole group'):validate_groups(invalid,unit)


@pytest.mark.parametrize('bad',['missing','duplicate','foreign','ceiling','group-key'])
def test_model_groups_must_cover_whole_passage_with_ceiling(bad):
    ids=[str(i) for i in range(21)];u=dict(unit_id='p001',claim_ids=ids)
    groups=[dict(group_key='one',claim_ids=ids[:11],rationale='first argument'),dict(group_key='two',claim_ids=ids[11:],rationale='second argument')]
    if bad=='missing':groups[0]['claim_ids'].pop()
    if bad=='duplicate':groups[1]['claim_ids'].append('0')
    if bad=='foreign':groups[0]['claim_ids'][0]='outside'
    if bad=='ceiling':groups=[dict(group_key='one',claim_ids=ids,rationale='too many')]
    if bad=='group-key':groups[1]['group_key']='one'
    with pytest.raises(ValueError):validate_groups(dict(groups=groups),u)


def test_direct_preparation_is_reused_only_with_exact_manifest_and_members(tmp_path):
    from backend.pipeline.exegesis_passage_grouping_job import load_direct_preparation
    unit=dict(unit_id='p001',claim_ids=['a','b'])
    manifest=dict(artifact_sha256='bound-l1',units=[unit])
    grouping=plan_reviewed_passage_unit(unit_id='p001',claim_ids=['a','b'],batch_size=20).model_dump(mode='json')
    artifact=write_new(tmp_path/'p001.json',dict(input_manifest_sha256='bound-l1',unit_id='p001',grouping=grouping))
    preparation=write_new(tmp_path/'preparation.json',dict(input_manifest_sha256='bound-l1',completed_direct_units=1,completed_direct_claims=2))
    answers,sha=load_direct_preparation(tmp_path,manifest)
    assert answers['p001']==artifact and sha==preparation['artifact_sha256']
    with pytest.raises(ValueError,match='another L1'):load_direct_preparation(tmp_path,dict(manifest,artifact_sha256='different'))
    with pytest.raises(ValueError):load_direct_preparation(tmp_path,dict(manifest,units=[dict(unit,claim_ids=['a'])]))


def test_split_runner_pins_scope_in_payload_and_schema(tmp_path,monkeypatch):
    from backend.pipeline.viewpoint_passage_grouping_sample_runner import split_reviewed_unit
    from backend.pipeline import exegesis_grouping_transport
    ids=[str(i) for i in range(21)]
    def call(**kwargs):
        assert kwargs['payload']['scope_label']=='p009'
        assert kwargs['schema']['properties']['scope_label']['const']=='p009'
        return dict(schema_version='wang_canonical_viewpoint_claim_grouping_v1',scope_label='p009',groups=[dict(group_key='first',claim_ids=ids[:12],rationale='first route'),dict(group_key='second',claim_ids=ids[12:],rationale='second route')])
    monkeypatch.setattr(exegesis_grouping_transport,'call',call)
    result=split_reviewed_unit(unit_id='p009',payload=dict(claims=[dict(claim_id=i) for i in ids]),provider='claude',model='claude-opus-5-5',effort='high',directory=tmp_path/'call',max_request_bytes=2500000)
    assert result.scope_label=='p009'


def test_scope_recovery_preserves_groups_and_rejects_different_unit_binding(tmp_path):
    from backend.pipeline.exegesis_passage_grouping_job import recover_scope_labels
    from backend.pipeline.exegesis_intelligent_grouping_job import checked
    ids=[str(i) for i in range(21)]
    unit=dict(unit_id='p009',passage_key='Matt.3.13-Matt.3.17',claim_ids=ids)
    manifest=dict(artifact_sha256='l1',units=[unit])
    write_new(tmp_path/'config.json',dict(input_manifest_sha256='l1',model='claude-opus-5-5',provider='claude'))
    directory=tmp_path/'groups'/'p009';directory.mkdir(parents=True)
    request=write_new(directory/'request.json',dict(model='claude-opus-5-5',provider='claude',payload=dict(reviewed_unit=unit,input_manifest_sha256='l1',claims=[dict(claim_id=i) for i in ids])))
    groups=[dict(group_key='one',claim_ids=ids[:12],rationale='first route'),dict(group_key='two',claim_ids=ids[12:],rationale='second route')]
    raw=write_new(directory/'response.json',dict(request_sha256=request['artifact_sha256'],response=dict(schema_version='wang_canonical_viewpoint_claim_grouping_v1',scope_label='Matt.3.13-Matt.3.17',groups=groups)))
    write_new(tmp_path/'failure-p009.json',dict(error='batch resolution failed: grouping is for scope Matt.3.13-Matt.3.17, not p009'))
    recovered,_=recover_scope_labels(tmp_path,manifest,'claude-opus-5-5')
    assert recovered['p009']['grouping']['groups']==groups
    assert checked(directory/'response.json')==raw
    with pytest.raises(ValueError,match='unit binding'):
        recover_scope_labels(tmp_path,dict(manifest,units=[dict(unit,passage_key='Matt.5.1-Matt.5.12')]),'claude-opus-5-5')
