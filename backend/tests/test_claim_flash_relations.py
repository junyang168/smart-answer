"""Group Claim relations: bounded scope, real SQL, source reuse and recovery."""
from copy import deepcopy
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
    def __init__(self, proposed=(), **kwargs): super().__init__(**kwargs); self.proposed=list(proposed)
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
