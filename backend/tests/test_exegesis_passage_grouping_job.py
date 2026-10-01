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
