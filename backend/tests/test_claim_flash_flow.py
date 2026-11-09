from copy import deepcopy
import json
import pytest

from backend.pipeline import claim_creation_flash as creation, claim_flash_flow as flow
from backend.pipeline import claim_candidate_workspace as workspace
from backend.api.canonical_repository.postgres_store import normalize_package, PostgresKnowledgeStoreError
from backend.tests.test_claim_creation_flash import Ports as CreationPorts, groups
from backend.tests.test_detailed_knowledge_extraction import _transcript
from backend.tests.test_claim_flash_missing import Ports as MissingPorts
from backend.tests.test_claim_flash_evidence import Ports as EvidencePorts
from backend.tests.test_claim_flash_relations import Ports as RelationPorts


class FlowPorts(MissingPorts):
    def repair(self,request):
        task=request['input']['task']
        if task=='source_preservation_inventory':
            self.calls.append(('gpt',request))
            return {'items':[{'job_id':j['job_id'],'units':[],'citations':[]} for j in request['input']['jobs']]}
        if task=='evidence_fidelity_repair':
            self.calls.append(('gpt',request))
            return {'items':[{'job_id':j['job_id'],'action':'keep','reason':'測試保持原值。'} for j in request['input']['jobs']]}
        if task=='missing_group_claim_relations':return RelationPorts().repair(request)
        return super().repair(request)


@pytest.fixture
def candidate(tmp_path):
    src=_transcript();path=tmp_path/'source.json';path.write_text(json.dumps(src))
    result=creation.run('T',path,tmp_path/'creation',CreationPorts(src),groups=groups())
    return json.loads((tmp_path/'creation'/'candidate.json').read_text()),path


def test_candidate_loader_keeps_provenance_and_canonical_guard(candidate,tmp_path):
    package,_=candidate
    with workspace.open_candidate(package,tmp_path/'private') as store:
        assert len(store.compile_package()['claims'])==1
        with pytest.raises(PostgresKnowledgeStoreError,match='reviewed candidate'):
            store.plan_package(package)
        before=store.get_record_state('claims',package['claims'][0]['claim_id'])
    assert json.loads((tmp_path/'private'/'candidate-input.json').read_text())==package
    assert json.loads((tmp_path/'private'/'result.json').read_text())['stopped']
    with workspace.open_candidate(package,tmp_path/'private') as store:
        assert store.get_record_state('claims',package['claims'][0]['claim_id'])==before


def test_complete_layered_candidate_and_zero_model_replay(candidate,tmp_path):
    package,_=candidate;path=tmp_path/'candidate.json';path.write_text(json.dumps(package));ports=FlowPorts()
    result=flow.run_candidate(path,tmp_path/'flow',ports)
    assert result['status']=='completed', result.get('pending')
    final=json.loads(__import__('pathlib').Path(result['package']['path']).read_text())
    assert final['extraction']==package['extraction']
    assert final['flash_application']['completion']=='completed'
    assert result['production_writes']==0 and not result['canonical_committed']
    assert result['legacy_qualification']=='not_granted'
    # Output contains unchanged material, complete owners and exact saved SQL rows.
    with workspace.open_candidate(package,tmp_path/'flow'/'workspace') as store:
        for col,values in normalize_package(final).items():
            for oid,payload in values.items():
                assert store.get_record_state(col,oid)['payload']==payload
    replay=FlowPorts();again=flow.run_candidate(path,tmp_path/'flow',replay)
    assert again==result and not replay.calls


def test_indivisible_source_group_uses_two_upper_batches_and_one_lower_review(candidate,tmp_path):
    package,_=candidate
    original=package['claims'][0]
    package['claims']=[deepcopy(original)]+[{**deepcopy(original),'claim_id':original['claim_id']+'-test-'+str(n)} for n in range(1,11)]
    for record in package['evidence_steps']:
        if original['claim_id'] in record.get('produced_claim_ids',[]):
            record['produced_claim_ids']=[p['claim_id'] for p in package['claims']]
    for record in package.get('questions',[]):
        if original['claim_id'] in record.get('answer_claim_ids',[]):
            record['answer_claim_ids']=[p['claim_id'] for p in package['claims']]
    path=tmp_path/'many.json';path.write_text(json.dumps(package))
    result=flow.run_candidate(path,tmp_path/'many-flow',FlowPorts())
    assert result['status']=='completed',result.get('pending')
    plan=flow.read(tmp_path/'many-flow/flash/shared-groups.json')
    assert len(plan['groups'])==1
    assert [len(batch) for batch in plan['groups'][0]['sequential_claim_batches']]==[8,3]
    assert set(result['stages']['claims'])=={'FG001-batch-1','FG001-batch-2'}
    assert len(result['stages']['evidence']['groups'])==1


def test_export_pending_is_not_approval_and_tracks_real_changes(candidate,tmp_path):
    package,_=candidate
    with workspace.open_candidate(package,tmp_path/'private') as store:
        sid=package['source_documents'][0]['source_id'];before,_=flow.snapshot(store,sid)
        cid=package['claims'][0]['claim_id'];p=store.get_record_state('claims',cid)['payload'];p['statement']='修改後。'
        store.apply_plan(store.plan_package({'package_id':'changed','claims':[p]}))
        root=tmp_path/'out';root.mkdir()
        result=flow.export(store,sid,root,before,package,{},[{'layer':'claims','reason':'待查'}],__import__('time').monotonic(),'candidate_workspace')
        assert result['status']=='needs_attention' and result['changes']['updated']==['claims/'+cid]
        final=flow.read(result['package']['path']);assert final['claims'][0]['statement']=='修改後。'
        assert final['source_fragments']==[v['payload'] for k,v in sorted(before.items()) if k.startswith('source_fragments/')]


def test_completed_output_drift_is_rejected_without_new_calls(candidate,tmp_path):
    package,_=candidate;path=tmp_path/'candidate.json';path.write_text(json.dumps(package))
    result=flow.run_candidate(path,tmp_path/'flow',FlowPorts())
    __import__('pathlib').Path(result['package']['path']).write_text('{}')
    p=FlowPorts()
    with pytest.raises(ValueError,match='output package changed'):flow.run_candidate(path,tmp_path/'flow',p)
    assert not p.calls


def test_pending_evidence_checkpoint_does_not_repeat_completed_source_layers(candidate,tmp_path,monkeypatch):
    package,_=candidate;path=tmp_path/'candidate.json';path.write_text(json.dumps(package))
    original=flow.eo.run
    def paused(*args,**kwargs):
        result=original(*args,**kwargs)
        return {**result,'status':'needs_attention','pending':[{'layer':'handoff','reason':'test checkpoint'}]}
    monkeypatch.setattr(flow.eo,'run',paused)
    first=flow.run_candidate(path,tmp_path/'flow',FlowPorts())
    assert first['status']=='needs_attention'
    monkeypatch.setattr(flow.eo,'run',original)
    monkeypatch.setattr(flow.source,'run',lambda *a,**k:pytest.fail('completed source verification repeated'))
    monkeypatch.setattr(flow.coverage,'run',lambda *a,**k:pytest.fail('completed source coverage repeated'))
    result=flow.run_candidate(path,tmp_path/'flow',FlowPorts())
    assert result['status']=='completed',result.get('pending')


def test_material_only_source_gets_real_missing_claim_disposition(candidate,tmp_path):
    package,_=candidate;package['claims']=[]
    for p in package['evidence_steps']:p['produced_claim_ids']=[]
    for p in package['questions']:p['answer_claim_ids']=[]
    path=tmp_path/'candidate.json';path.write_text(json.dumps(package));ports=FlowPorts()
    result=flow.run_candidate(path,tmp_path/'flow',ports)
    assert result['status']=='completed',result.get('pending')
    assert any(role=='gpt' and r['input']['task']=='missing_group_claims' for role,r in ports.calls)
    assert flow.read(result['package']['path'])['claims']==[]


@pytest.mark.parametrize('fault',[None,'database','receipt'])
def test_claim_checkpoint_skips_completed_stages_and_refuses_drift(candidate,tmp_path,monkeypatch,fault):
    package,_=candidate;path=tmp_path/'candidate.json';path.write_text(json.dumps(package))
    original=flow.relations.run
    def paused(*args,**kwargs):
        result=original(*args,**kwargs)
        return {**result,'status':'needs_attention','pending':[{'reason':'one later relation needs continuation'}]}
    monkeypatch.setattr(flow.relations,'run',paused)
    first=flow.run_candidate(path,tmp_path/'resume',FlowPorts())
    assert first['status']=='needs_attention' and first['pending'][0]['layer']=='claims'
    root=tmp_path/'resume/flash'
    if fault=='database':
        with workspace.open_candidate(package,tmp_path/'resume/workspace') as store:
            cid=package['claims'][0]['claim_id'];p=store.get_record_state('claims',cid)['payload'];p['statement']='外部改動。'
            store.apply_plan(store.plan_package({'package_id':'outside-change','claims':[p]}))
    elif fault=='receipt':
        stage=next((root/'claims').glob('*/statements/result.json'));stage.write_text('{}')
    monkeypatch.setattr(flow.relations,'run',original)
    for module in (flow.source,flow.coverage,flow.eo,flow.statements,flow.arguments):
        monkeypatch.setattr(module,'run',lambda *a,**k:pytest.fail('completed layer/stage repeated'))
    if fault:
        with pytest.raises(ValueError,match='checkpoint input changed|stage evidence changed'):
            flow.run_candidate(path,tmp_path/'resume',FlowPorts())
    else:
        result=flow.run_candidate(path,tmp_path/'resume',FlowPorts())
        assert result['status']=='completed'
