"""A committed route resumes exact readback without another model or apply."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.knowledge_models import ArgumentRouteAttestationRecord
from backend.api.canonical_repository.postgres_store import ChangeOperation,ChangeSetPlan,record_content_sha
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_route_cvp_queue import CvpRouteQueue,make_cvp_route_job
from backend.pipeline import viewpoint_route_cvp_worker as worker


def attestation():
    return ArgumentRouteAttestationRecord.model_validate({
        'argument_route_attestation_id':'A1','argument_route_id':'R1','validated_against_route_revision_id':'RR1',
        'source_id':'S1','source_revision_sha256':'s1-sha','claim_ids':['C1'],'occurrence_ref_id':'O1',
        'step_bindings':[{'route_step_key':'conclusion','claim_component_keys':['CCK-1'],
            'evidence_step_ids':['E1'],'source_fragment_ids':['F1'],'attestation_status':'attested'}],
        'terminal_claim_link_id':'L1','completeness':'full','review_artifact_sha256':'review-sha',
        'review_status':'system_approved'}).model_dump(mode='json')


@pytest.fixture
def saved(tmp_path,monkeypatch):
    # Actual provider receipt authentication is shared with the runtime; these
    # fixtures isolate committed SQL/history and exact current readback.
    monkeypatch.setattr(worker,'authenticate_saved_route_review',lambda *args:None)
    actual=attestation();raw=deepcopy(actual);raw.pop('support_scope_note')
    package={'argument_route_attestations':[raw]}
    op=ChangeOperation(operation='create',collection='argument_route_attestations',object_id='A1',
        before_revision=None,before_sha256=None,after_revision=1,after_sha256=record_content_sha(actual),payload=actual)
    plan=ChangeSetPlan(change_set_id='KCS-1',fingerprint_sha256='plan-sha',package_id='P1',
        source_kind='argument_route_resolution_v2',source_sha256=sha256_json(package),
        operations=(op,),unchanged=0,ignored_keys=())
    def write(name,value,seal=None):
        if seal:value=value | {seal:sha256_json(value)}
        (tmp_path/name).write_text(json.dumps(value))
        return value
    material=write('cvp-route-materials.json',{'viewpoint_id':'V1','record_pins':[
        {'collection':'claims','object_id':'C1','revision':1,'content_sha256':'claim-sha'}],
        'physical_sources':[],'links':[],'existing_routes':[],'existing_attestations':[]},'artifact_sha256')
    packet=write('route-packet.json',{'scope_label':'current V1'},'packet_sha256')
    review={'route_evidence_packet_sha256':packet['packet_sha256'],'reason':'actual final pass'}
    run=write('route-cvp-run.json',{'route_packet_sha256':packet['packet_sha256'],
        'review_sha256':sha256_json(review),'rounds':[{'round':1,'outcome':'pass'}]},'artifact_sha256')
    write('route-review-001.json',{'review_sha256':run['review_sha256'],'review':review,'validation':{'outcome':'pass'}})
    write('route-change-package.json',package);write('route-change-plan.json',plan.as_dict())
    history={'status':'applied','source_kind':plan.source_kind,'source_sha256':plan.source_sha256,
        'fingerprint_sha256':plan.fingerprint_sha256,'metadata':{'route_review_sha256':run['review_sha256'],
        'route_material_sha256':material['artifact_sha256']}}
    rows={('argument_route_attestations','A1'):actual};frozen=[]
    store=SimpleNamespace(get_change_history=lambda _:history,
        get_record=lambda col,oid:rows.get((col,oid)),verify_current_read_pins=lambda pins:frozen.append(pins),
        read_cvp_links=lambda **kw:{},read_cvp_route_records=lambda _:rows)
    return store,tmp_path,history,rows,frozen,material


def test_typed_defaults_and_saved_applied_history_finish_without_models_or_apply(saved,monkeypatch):
    store,root,_,_,frozen,material=saved
    def forbidden(*a,**kw):raise AssertionError('must only read the already committed result')
    monkeypatch.setattr(worker,'prepare_cvp_route_materials',forbidden)
    monkeypatch.setattr(worker,'run_cvp_route_scope',forbidden)
    result=worker.process_cvp_route(store=store,viewpoint_id='V1',output_dir=root,
        proposer=None,reviewer=None,reconsiderer=None,max_correction_rounds=4,apply=True)
    assert result['status']=='applied_readback_verified'
    assert result['model_calls']==result['semantic_writes']==0
    assert frozen==[{('claims','C1'):(1,'claim-sha',False)}]
    assert worker.resume_applied_cvp_route(store,'V1',root,material_sha256=material['artifact_sha256'])==result
    with pytest.raises(ValueError,match='original approval'):
        worker.resume_applied_cvp_route(store,'V1',root,material_sha256='different-material')


@pytest.mark.parametrize('defect',['approval','history','postimage','links','routes'])
def test_recovery_rejects_wrong_approval_history_and_current_scope(saved,defect):
    store,root,history,rows,_,_=saved
    if defect=='approval':
        p=root/'route-review-001.json';v=json.loads(p.read_text());v['review']['reason']='altered review';p.write_text(json.dumps(v))
    if defect=='history':history['fingerprint_sha256']='different-plan'
    if defect=='postimage':rows['argument_route_attestations','A1']['support_scope_note']='external content change'
    if defect=='links':store.read_cvp_links=lambda **kw:{('viewpoint_claim_links','new-link'):{'payload':{'effective_state':'active'}}}
    if defect=='routes':rows['argument_route_attestations','external']={}
    with pytest.raises(ValueError):worker.resume_applied_cvp_route(store,'V1',root)


def test_unapplied_plan_cannot_claim_saved_approval_as_applied(saved):
    store,root,history,_,frozen,_=saved;history['status']='planned'
    assert worker.resume_applied_cvp_route(store,'V1',root) is None
    assert frozen==[]


def test_exact_job_recovery_cannot_retry_another_exception(tmp_path):
    q=CvpRouteQueue(tmp_path);jobs=[make_cvp_route_job(v,'material','policy') for v in ['V1','V2']]
    for j in jobs:
        q.enqueue(j);claimed,epoch=q.claim(worker_id='initial',policy_sha256='policy',lease_seconds=30,job_id=j['job_id'])
        assert claimed==j
        q.finish(j['job_id'],worker_id='initial',epoch=epoch,status='exception',detail='preserved old result')
    chosen,epoch=q.claim(worker_id='recovery',policy_sha256='policy',lease_seconds=30,
        retry_exceptions=True,job_id=jobs[1]['job_id'])
    assert chosen==jobs[1]
    assert json.loads((q.states/(jobs[0]['job_id']+'.json')).read_text())['status']=='exception'


def test_saved_retired_route_head_is_history_not_an_extra_active_route(saved):
    store,root,_,rows,_,_=saved
    rows['argument_routes','old-route']={'payload':{'route_status':'retired'}}
    assert worker.resume_applied_cvp_route(store,'V1',root)['status']=='applied_readback_verified'
    rows['argument_routes','old-route']['payload']['route_status']='active'
    with pytest.raises(ValueError,match='route population changed'):
        worker.resume_applied_cvp_route(store,'V1',root)
