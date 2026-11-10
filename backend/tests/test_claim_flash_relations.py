"""Group Claim relations: bounded scope, real SQL, source reuse and recovery."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from backend.pipeline import claim_flash_relations as stage, claim_flash_arguments as arguments
from backend.pipeline import claim_flash_statements as statements, claim_flash_common as f
from backend.pipeline import claim_flash_evidence as eo
from backend.pipeline import evidence_refresh as e
from backend.tests.test_claim_flash_arguments import Ports as ArgumentPorts
from backend.tests.test_claim_flash_evidence import Ports as EvidencePorts, saved_inventory
from backend.tests.test_claim_update_flash import Ports as BasePorts
from backend.tests.test_evidence_refresh import sample
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres


class Ports(BasePorts):
    def __init__(self, proposed=(), **kwargs):
        super().__init__(**kwargs)
        self.proposed=[{'relation_type':'supports','direction':'a_to_b',**p} for p in proposed]
    def repair(self, request):
        self.calls.append(('gpt', request)); task=request['input']['task']
        assert task in {'missing_group_claim_relations', 'pair_claim'}
        if task=='missing_group_claim_relations':
            return {'items':[{'job_id':j['job_id'], 'pairs':self.proposed, 'unresolved_keys':[],
                              'reason':'只檢查本組Claim間關係。'} for j in request['input']['jobs']]}
        return {'items':[{'job_id':j['job_id'], 'action':'set', 'relation_type':'supports',
                         'direction':'a_to_b' if j['a']=='claims/C' else 'b_to_a',
                         'reason':'前項判斷是教授推到後項的依據。'} for j in request['input']['jobs']]}


@pytest.fixture
def ready(sample,tmp_path):
    store,root,_=sample
    ev=store.get_record_state('evidence_steps','E')['payload']; ev['statement']='沒有聽從祂的話，顯明沒有真正相信祂。'; ev['produced_claim_ids']=['C','D']
    first=store.get_record_state('claims','C')['payload']; first['statement']=ev['statement']
    second={**deepcopy(first),'claim_id':'D','statement':'相信與聽從在教授的論證中相關。'}
    store.apply_plan(store.plan_package({'package_id':'relations-fixture','claims':[first,second],'evidence_steps':[ev]}))
    records,sources=f.load_source(store,'S')
    plan={'source_id':'S','source_body_sha256':sources['S']['body_sha256'],'groups':[{
        'group_id':'G','title':'one','paragraph_keys':list(sources['S']['paragraphs']),
        'claim_ids':['C','D'],'lower_record_keys':['evidence_steps/E'],'reference_record_keys':[]}]}
    eroot=root/'evidence'; sroot=root/'statements'; aroot=root/'arguments'
    assert eo.run(store,'S',plan,eroot,EvidencePorts(),saved_inventory(tmp_path/'prior',sources),apply=True)['status']=='completed'
    assert statements.run(store,'S',['C','D'],eroot,sroot,BasePorts(),apply=True)['status']=='verified'
    assert arguments.run(store,'S',plan,'G',eroot,sroot,aroot,ArgumentPorts(),apply=True)['status']=='verified'
    return store,root,aroot


def edge(store, **overrides):
    p={'claim_relation_id':'R','from_id':'D','to_id':'C','relation_type':'supports','reason':'原理由。',**overrides}
    store.apply_plan(store.plan_package({'package_id':'fixture-edge-'+p['claim_relation_id'],'claim_relations':[p]}))


def test_missing_relation_and_zero_model_replay(ready):
    store,root,aroot=ready; before={c:store.get_record_state('claims',c) for c in ['C','D']}
    p=Ports([{'a':'claims/C','b':'claims/D','reason':'補回教授的推理。'}])
    result=stage.run(store,'S',aroot,root/'relations',p,apply=True)
    assert result['status']=='applied' and len(result['updates'])==1
    rel=next(iter(result['updates'].values())); assert (rel['from_id'],rel['to_id'],rel['relation_type'])==('C','D','supports')
    assert before=={c:store.get_record_state('claims',c) for c in ['C','D']}
    assert all(set(r['questions'])<={'relation','reason_valid'} for role,r in p.calls if role=='jev')
    replay=Ports(); assert stage.run(store,'S',aroot,root/'relations',replay,apply=True)['status']=='applied'
    assert replay.calls==[]


def test_argument_claim_candidate_is_handed_to_independent_relation_task(ready):
    store,root,original=ready
    intent=json.loads((original/'intent.json').read_text());group=intent['group']
    ar=root/'argument-handoff';argports=ArgumentPorts([{'a':'claims/C','b':'claims/D','fragment_ids':['F'],'reason':'候選論證承接。'}])
    plan={'source_id':'S','source_body_sha256':f.load_source(store,'S')[1]['S']['body_sha256'],'groups':[group]}
    result=arguments.run(store,'S',plan,group['group_id'],intent['evidence_root'],intent['statement_root'],ar,argports,apply=True)
    assert result['status']=='verified' and len(result['claim_pair_handoffs'])==1
    assert not result['updates'] and not any(req['input']['task']=='pair_claim' for role,req in argports.calls if role=='gpt')
    ports=Ports();final=stage.run(store,'S',ar,root/'relation-handoff',ports,apply=True)
    assert final['status']=='applied' and len(final['updates'])==1
    assert final['consumed_argument_claim_handoffs']==result['claim_pair_handoffs']
    assert not any(req['input']['task']=='pair_claim' for role,req in ports.calls if role=='gpt')
    assert not any(role=='jev' for role,req in ports.calls)
    discovery=next(req for role,req in ports.calls if role=='gpt')
    assert discovery['input']['completed_connections'][0]['reason']==result['claim_pair_handoffs'][0]['reason']


def test_conflicting_complete_proposals_never_partially_commit(ready):
    store,root,aroot=ready
    ports=Ports([{'a':'claims/C','b':'claims/D','reason':'第一個完整判定。'},
                 {'a':'claims/C','b':'claims/D','direction':'b_to_a','reason':'相矛盾的完整判定。'}])
    result=stage.run(store,'S',aroot,root/'conflicting',ports,apply=True)
    assert result['status']=='needs_attention'
    assert result['pending'][0]['reason']=='conflicting_completed_relation_proposals'
    assert not (root/'conflicting'/'commit-intent.json').exists()
    assert not any(role=='jev' or req['input']['task']=='pair_claim' for role,req in ports.calls)


def test_postreply_source_record_drift_still_blocks_completed_relation_commit(ready):
    store,root,aroot=ready
    class Drift(Ports):
        def repair(self,req):
            raw=super().repair(req)
            p=store.get_record_state('source_fragments','F')['payload'];p['verbatim_excerpt_sha256']='changed'
            store.apply_plan(store.plan_package({'package_id':'after-reply-drift','source_fragments':[p]}))
            return raw
    ports=Drift([{'a':'claims/C','b':'claims/D','reason':'有據的完整判定。'}])
    with pytest.raises(ValueError,match='inputs changed'):stage.run(store,'S',aroot,root/'postreply-drift',ports,apply=True)
    assert not (root/'postreply-drift'/'commit.json').exists()


def test_wrong_direction_repaired(ready):
    store,root,aroot=ready; edge(store)
    result=stage.run(store,'S',aroot,root/'relations',Ports(choices={'relation':'b_to_a_supports'}),apply=True)
    assert result['status']=='applied'
    assert store.get_record_state('claim_relations','R')['payload']['from_id']=='C'
    assert store.get_record_state('claim_relations','R')['payload']['to_id']=='D'


def test_no_missing_relation_is_noop(ready):
    store,root,aroot=ready
    result=stage.run(store,'S',aroot,root/'relations',Ports(),apply=True)
    assert result['status']=='verified' and not result['updates']
    assert not (root/'relations'/'commit.json').exists()


def test_relation_retirement_replay_and_commit_crash(ready,monkeypatch):
    store,root,aroot=ready; edge(store); apply=store.apply_plan
    def crash(*a,**kw): apply(*a,**kw); raise RuntimeError('after SQL')
    monkeypatch.setattr(store,'apply_plan',crash)
    with pytest.raises(RuntimeError,match='after SQL'):
        stage.run(store,'S',aroot,root/'relations',Ports(choices={'relation':'none'}),apply=True)
    monkeypatch.setattr(store,'apply_plan',lambda *a,**kw:pytest.fail('duplicate SQL'))
    replay=Ports(); result=stage.run(store,'S',aroot,root/'relations',replay,apply=True)
    assert result['status']=='applied' and store.get_record_state('claim_relations','R')['retired']
    assert replay.calls==[]


def test_protected_relation_not_retired(ready):
    store,root,aroot=ready; edge(store,review_status='human_approved')
    result=stage.run(store,'S',aroot,root/'relations',Ports(choices={'relation':'none'}),apply=True)
    assert result['status']=='needs_attention' and result['pending'][0]['reason']=='protected_relation'
    assert not store.get_record_state('claim_relations','R')['retired']


def test_forbidden_relation_cannot_be_added(ready):
    store,root,aroot=ready
    store.apply_plan(store.plan_package({'package_id':'forbidden','claim_relation_constraints':[{
        'constraint_id':'N','source_id':'C','target_id':'D','forbidden_relation_types':['supports'],'reason':'禁止這條邊。'}]}))
    result=stage.run(store,'S',aroot,root/'relations',Ports([{'a':'claims/C','b':'claims/D','reason':'候選。'}]),apply=True)
    assert result['status']=='needs_attention' and result['pending'][0]['reason']=='explicit_relation_constraint'
    assert not result['updates']


@pytest.mark.parametrize('defect',['outside','self','lower'])
def test_discovery_cannot_expand_scope(ready,defect):
    store,root,aroot=ready
    other={'outside':'claims/X','self':'claims/C','lower':'evidence_steps/E'}[defect]
    with pytest.raises(ValueError,match='exceeded group'):
        stage.run(store,'S',aroot,root/'relations',Ports([{'a':'claims/C','b':other,'reason':'不合法。'}]),apply=True)
    assert not (root/'relations'/'commit-intent.json').exists()


def test_lower_drift_rejects_before_models(ready):
    store,root,aroot=ready; ev=store.get_record_state('evidence_steps','E')['payload']; ev['statement']='改變過的材料。'
    store.apply_plan(store.plan_package({'package_id':'drift','evidence_steps':[ev]})); ports=Ports()
    with pytest.raises(ValueError,match='input changed'): stage.run(store,'S',aroot,root/'relations',ports,apply=True)
    assert ports.calls==[]


def test_unresolved_discovery_does_not_write(ready):
    store,root,aroot=ready
    class Pending(Ports):
        def repair(self,request):
            raw=super().repair(request)
            if request['input']['task']=='missing_group_claim_relations':raw['items'][0]['unresolved_keys']=['claims/C']
            return raw
    result=stage.run(store,'S',aroot,root/'relations',Pending(),apply=True)
    assert result['status']=='needs_attention' and result['pending'][0]['object_key']=='claims/C'
    assert not (root/'relations'/'commit.json').exists()


def test_completed_relation_input_drift_not_hidden_on_replay(ready):
    store,root,aroot=ready
    stage.run(store,'S',aroot,root/'relations',Ports(),apply=True)
    p=store.get_record_state('claims','C')['payload'];p['statement']='不同的主張。'
    store.apply_plan(store.plan_package({'package_id':'after-completion-drift','claims':[p]})); ports=Ports()
    with pytest.raises(ValueError,match='inputs changed'):stage.run(store,'S',aroot,root/'relations',ports,apply=True)
    assert ports.calls==[]


@pytest.mark.parametrize('protected',[False,True])
def test_exact_retired_identity_is_pinned_and_human_protection_preserved(ready,protected):
    store,root,aroot=ready
    oid='CR-FLA-'+e.digest(['S','C','D','supports'])[:24]
    edge(store,claim_relation_id=oid,from_id='C',to_id='D',review_status='human_approved' if protected else 'candidate')
    store.apply_plan(store.plan_package({'package_id':'retire-exact-identity'},retiring_keys=[('claim_relations',oid)]))
    ports=Ports([{'a':'claims/C','b':'claims/D','reason':'本次完整材料支持這條直接邊。'}])
    result=stage.run(store,'S',aroot,root/'relations',ports,apply=True)
    assert result['status']=='needs_attention'
    assert result['pending'][0]['reason']==('protected_retired_relation' if protected else 'retired_relation_requires_restore')
    assert store.get_record_state('claim_relations',oid)['retired']
    assert not result['updates'] and not (root/'relations'/'commit-intent.json').exists()
    before=json.loads((root/'relations'/'before.json').read_text())
    assert before['records']['claim_relations/'+oid]['retired']
    replay=Ports();assert stage.run(store,'S',aroot,root/'relations',replay,apply=True)['status']=='needs_attention'
    assert replay.calls==[]


def test_cross_group_edges_have_one_later_owner_including_sequential_batches():
    groups=[{'claim_ids':['A','B'],'sequential_claim_batches':[['A'],['B']]},{'claim_ids':['C']}]
    records={}
    for oid,a,b in [('AB','A','B'),('AC','A','C'),('CA','C','A'),('BC','B','C'),('AX','A','X')]:
        records['claim_relations/'+oid]={'payload':{'from_id':a,'to_id':b},'retired':False}
    assert stage.owned_edges(records,['A'],groups)=={}
    assert set(stage.owned_edges(records,['B'],groups))=={'claim_relations/AB'}
    assert set(stage.owned_edges(records,['C'],groups))=={'claim_relations/AC','claim_relations/CA','claim_relations/BC'}
    assert stage.owned_edges(records,['A','B'])=={'claim_relations/AB':{'from_id':'A','to_id':'B'}}
    with pytest.raises(ValueError,match='task differs'):stage.owned_edges(records,['B','C'],groups)


def test_cross_group_endpoint_is_readonly_not_a_discovery_target(ready):
    store,root,aroot=ready
    third={**deepcopy(store.get_record_state('claims','C')['payload']),'claim_id':'X'}
    store.apply_plan(store.plan_package({'package_id':'cross-group-endpoint','claims':[third]}))
    edge(store,from_id='X',to_id='C')
    arg=json.loads((aroot/'intent.json').read_text());ep=Path(arg['evidence_root'])/'intent.json'
    plan=json.loads(ep.read_text());plan['groups']['groups'].insert(0,{'group_id':'EARLIER','claim_ids':['X']})
    ep.write_text(json.dumps(plan))  # This fixture's frozen plan predates the new relation task.
    before=store.get_record_state('claims','X')
    ports=Ports(choices={'relation':'none'})
    result=stage.run(store,'S',aroot,root/'cross-group',ports,apply=True)
    assert result['readonly_endpoint_keys']==['claims/X']
    assert result['reviewed_existing_relation_keys']==['claim_relations/R']
    assert store.get_record_state('claims','X')==before
    discovery=next(req for role,req in ports.calls if role=='gpt' and req['input']['task']=='missing_group_claim_relations')
    assert set(discovery['input']['claims'])=={'claims/C','claims/D'}
    assert not discovery['input']['existing_edges']
    frozen=json.loads((root/'cross-group/before.json').read_text())
    assert 'claims/X' in frozen['records'] and 'evidence_steps/E' in frozen['records']
    assert store.get_record_state('claim_relations','R')['retired']


def test_invalid_completed_type_repairs_only_that_pair_without_jev(ready):
    from backend.pipeline import claim_flash_pairs as pairs
    store,root,_=ready
    third={**deepcopy(store.get_record_state('claims','C')['payload']),'claim_id':'X'}
    store.apply_plan(store.plan_package({'package_id':'invalid-type-third','claims':[third]}))
    records,sources=f.load_source(store,'S');ports=Ports()
    result=pairs.review(records,sources,[],[],root/'invalid-type',ports,kind='claim',repair_invalid_completed=True,completed_proposals=[
        {'a':'claims/C','b':'claims/D','relation_type':'applies','direction':'a_to_b','reason':'原判定的理由。'},
        {'a':'claims/D','b':'claims/X','relation_type':'supports','direction':'a_to_b','reason':'另一條已完成判定。'}])
    assert not result['pending'] and len(result['new_relations'])==2
    assert len(ports.calls)==1
    role,request=ports.calls[0];assert role=='gpt' and request['input']['task']=='pair_claim'
    assert len(request['input']['jobs'])==1
    job=request['input']['jobs'][0]
    assert {job['a'],job['b']}=={'claims/C','claims/D'}
    assert 'a_to_b_applies' not in job['allowed_choices']
    assert job['candidate_reason']=='原判定的理由。'
    assert result['new_relations'][0]['reason']=='另一條已完成判定。'


def test_new_claim_discovery_schema_excludes_other_domain_types():
    legacy=stage.discovery_schema()['$defs']['CompletedProposal']['properties']['relation_type']['enum']
    current=stage.discovery_schema('claim_relation_types_v1')['$defs']['CompletedProposal']['properties']['relation_type']['enum']
    assert 'applies' in legacy and 'answers' in legacy
    assert set(current)==set(legacy)-{'applies','answers'}
