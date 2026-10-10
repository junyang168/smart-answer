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


def test_invalid_first_direction_is_not_persisted(tmp_path):
    import json
    from backend.pipeline.cvp_sql_flow import binding
    d=direction();d['additional_model_calls']=1;seal(d)
    p=tmp_path/'invalid.json';p.write_text(json.dumps(d))
    out=tmp_path/'execution';out.mkdir()
    with pytest.raises(ValueError):h.persist_bindings(out,[binding(p)])
    assert not list(out.rglob('*.json'))


def multi_direction():
    d=direction();i=d['items'][0]
    i['connections'].append({**i['connections'][0],'viewpoint_claim_link_id':'CONTEXT',
        'decision_id':'OTHER','link_type':'equivalent_component'})
    i['retained_link_ids']=['L'];d['exact_finding_only']=True
    d['read_pins']=[{'collection':c,'object_id':o,'pin':[1,'a'*64,False]}
        for c,o in sorted(h.required_keys(d['items']))]
    return seal(d)


def test_selected_link_keeps_full_other_connection_context():
    d=multi_direction();before=deepcopy(d);h.validate_direction(d)
    assert d==before and ('viewpoint_claim_links','CONTEXT') in h.required_keys(d['items'])


@pytest.mark.parametrize('problem',['unselected','foreign','broad','missing_context_pin','duplicate','changed_finding_scope'])
def test_selected_authority_counterexamples(problem):
    d=multi_direction();i=d['items'][0]
    if problem=='unselected':i.pop('retained_link_ids')
    if problem=='foreign':i['retained_link_ids']=['FOREIGN']
    if problem=='broad':i['retained_link_ids']=['L','CONTEXT']
    if problem=='missing_context_pin':d['read_pins']=[p for p in d['read_pins'] if p['object_id']!='CONTEXT']
    if problem=='duplicate':i['connections'].append(deepcopy(i['connections'][0]))
    if problem=='changed_finding_scope':d.pop('exact_finding_only')
    with pytest.raises(ValueError):h.validate_direction(seal(d))


def test_selected_exact_finding_cannot_expand_to_new_concern():
    d=multi_direction();item=deepcopy(d['items'][0]);item.pop('retained_link_ids')
    item['audit_finding']={**item['audit_finding'],'reason':'new unrelated objection'}
    with pytest.raises(ValueError,match='outside exact authorized finding'):
        h.verify_item(item,d,{})


@pytest.mark.parametrize('fault',['target','authority_seal','not_human','no_commit','run'])
def test_accepted_postimage_is_not_a_broad_audit_waiver(fault,monkeypatch):
    d=multi_direction();item=deepcopy(d['items'][0]);item.pop('retained_link_ids')
    accepted=deepcopy(item['connections'][0]);accepted['viewpoint_id']='v';item['connections'][0]['viewpoint_id']='v'
    d['items'][0]['connections'][0]['viewpoint_id']='v'
    d.update(run_id='r',campaign_sha256='c',accepted_postimage_authority={'path':'authority'},applied_change_set_id='KCS')
    authority=seal({'schema_version':'wang_cvp_link_representation_human_postimage_v1',
       'authorized_by':'owner','human_content_approval':True,'run_id':'r','campaign_sha256':'c','accepted_after_link':accepted})
    if fault=='target':authority['accepted_after_link']['viewpoint_id']='foreign';seal(authority)
    if fault=='authority_seal':authority['artifact_sha256']='wrong'
    if fault=='not_human':authority['human_content_approval']=False;seal(authority)
    if fault=='no_commit':d.pop('applied_change_set_id')
    if fault=='run':authority['run_id']='foreign';seal(authority)
    seal(d)
    from backend.pipeline import cvp_sql_flow
    monkeypatch.setattr(cvp_sql_flow,'check_binding',lambda _:None)
    monkeypatch.setattr(h,'read_artifact',lambda _:authority)
    with pytest.raises(ValueError):h.verify_item(item,d,{})


def test_append_binding_preserves_original_and_requires_all_flags(tmp_path,monkeypatch):
    from backend.pipeline import cvp_sql_flow
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable,read_artifact
    monkeypatch.setattr(cvp_sql_flow,'check_binding',lambda b:None)
    a=tmp_path/'a.json';b=tmp_path/'b.json'
    write_immutable(a,direction());write_immutable(b,multi_direction())
    ba={'path':str(a),'file_sha256':'a'*64};bb={'path':str(b),'file_sha256':'b'*64}
    h.persist_bindings(tmp_path,[ba]);original=(tmp_path/h.NAME).read_bytes()
    h.persist_bindings(tmp_path,[ba,bb]);h.persist_bindings(tmp_path,[ba,bb])
    assert (tmp_path/h.NAME).read_bytes()==original
    assert h.direction_bindings(tmp_path)==[ba,bb]
    assert len(list((tmp_path/h.ADDITIONS).glob('*.json')))==1
    for invalid in ([bb],[ba],[],[ba,bb,bb],[{**ba,'file_sha256':'changed'},bb]):
        with pytest.raises(ValueError):h.check_explicit_bindings(tmp_path,invalid)


def test_invalid_append_cannot_publish_new_binding(tmp_path,monkeypatch):
    from backend.pipeline import cvp_sql_flow
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    monkeypatch.setattr(cvp_sql_flow,'check_binding',lambda b:None)
    a=tmp_path/'a.json';b=tmp_path/'b.json'
    write_immutable(a,direction());d=multi_direction();d['additional_model_calls']=1
    write_immutable(b,seal(d))
    ba={'path':str(a),'file_sha256':'a'*64};bb={'path':str(b),'file_sha256':'b'*64}
    h.persist_bindings(tmp_path,[ba])
    with pytest.raises(ValueError):h.persist_bindings(tmp_path,[ba,bb])
    assert h.direction_bindings(tmp_path)==[ba]
