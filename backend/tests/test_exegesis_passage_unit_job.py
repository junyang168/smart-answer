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
