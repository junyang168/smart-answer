from copy import deepcopy
import pytest
from backend.pipeline import cvp_exact_human_audit_retention as h
from backend.api.canonical_repository.postgres_store import sha256_json


def direction():
    item={'item_id':'4:V:C','claim':{'claim_id':'C'},'viewpoint':{'viewpoint_id':'V'},
        'connections':[{'link_type':'qualifies','viewpoint_claim_link_id':'L','decision_id':'D',
            'viewpoint_id':'V','validated_against_viewpoint_revision_id':'R'}],
        'evidence':[],'sources':[],'audit_finding':{'viewpoint_id':'V','claim_ids_in_question':['C'],'issue':'different_proposition'}}
    pins=[{'collection':c,'object_id':o,'pin':[1,'a'*64,False]} for c,o in sorted(h.required_keys([item]))]
    return seal({'schema_version':h.DIRECTION,'authorized_by':'owner','owner_direction':'retain exact contextual qualification',
        'decision':'reject_objection_retain_exact_current_link','master_data_mutations':0,'additional_model_calls':0,
        'audit':{'path':'audit'},'items':[item],'read_pins':pins})


def seal(d):
    d.pop('artifact_sha256',None);d['artifact_sha256']=sha256_json(d);return d


def test_exact_direction_grants_no_write_or_retry():
    d=direction();before=deepcopy(d);h.validate_direction(d);assert d==before
    assert h.target(d['items'][0])==h.target({**d['items'][0],'audit_finding':{'different':'reason'}})


@pytest.mark.parametrize('field,value',[('schema_version','foreign'),('authorized_by','model'),('owner_direction',''),
    ('decision','approve_new_content'),('master_data_mutations',1),('additional_model_calls',1),('items',[])])
def test_foreign_or_broader_authority_rejected(field,value):
    d=direction();d[field]=value
    with pytest.raises(ValueError):h.validate_direction(seal(d))


@pytest.mark.parametrize('problem',['missing','duplicate','retired','foreign','seal','layer','type','multiple','empty'])
def test_dependency_and_population_counterexamples(problem):
    d=direction()
    if problem=='missing':d['read_pins'].pop()
    if problem=='duplicate':d['read_pins'][-1]=d['read_pins'][0]
    if problem=='retired':d['read_pins'][0]['pin'][2]=True
    if problem=='foreign':d['read_pins'][0]['object_id']='foreign'
    if problem=='layer':d['items'][0]['item_id']='3:C:C'
    if problem=='type':d['items'][0]['connections'][0]['link_type']='unreviewed'
    if problem=='multiple':d['items'][0]['connections']*=2
    if problem=='empty':d['items'][0]['connections']=[]
    seal(d)
    if problem=='seal':d['owner_direction']='changed'
    with pytest.raises(ValueError):h.validate_direction(d)


def test_partial_human_route_keeps_unrelated_item(monkeypatch):
    packet={'items':[{'item_id':'4:V:C'},{'item_id':'4:V:OTHER'}]}
    monkeypatch.setattr(h,'human_items',lambda *a:({'4:V:C'},[]))
    new,ids=h.partition({},packet,None,None)
    assert new['items']==[{'item_id':'4:V:OTHER'}] and len(packet['items'])==2
    assert ids=={'4:V:C'}
