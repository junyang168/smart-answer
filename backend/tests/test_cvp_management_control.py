"""Independent quality restrictions and human intake do not reopen operations."""
from copy import deepcopy
import hashlib

import pytest

from backend.api.canonical_repository import cvp_management as domain
from backend.api.canonical_repository import cvp_management_store as persistence
from backend.pipeline import cvp_management_control as control
from backend.pipeline import cvp_management_runner as runner
from backend.pipeline.viewpoint_resolution_runtime import write_immutable
from test_cvp_management_runner import setup,postgres_store,_temporary_postgres,parent


def bound(path):
    return {'path':str(path),'file_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def audit_manifest(store,request,root):
    head=store.read_cvp_heads()[0];vid=head['viewpoint_id']
    row=store.get_record_state('canonical_viewpoints',vid)
    raw={'findings':[{'reason':'此觀點仍有一項範圍限定需要核查。'}]}
    report=root/'external-independent-report.json';write_immutable(report,raw)
    observation={'schema_version':domain.VERSION+'_quality_observation','audit_id':'quality-1',
        'report_binding':bound(report),'raw_report':raw,
        'reader':{'model_id':'independent-reader','participating_model_ids':['gpt-6.1-sol','claude-opus-5-5'],
            'code_path':'scripts/audit-library.py','participating_code_paths':['backend/pipeline/cvp_materials.py'],
            'source_mode':'direct_physical_originals','imports_pipeline_readers':False},
        'cohort_pins':[{'collection':'canonical_viewpoints','object_id':vid,'revision':row['revision'],
                        'content_sha256':row['content_sha256']}],
        'sample_keys':['canonical_viewpoints/'+vid],
        'findings':[{'ref':'f1','category':'coverage','target_keys':['canonical_viewpoints/'+vid],
                     'report_path':['findings',0,'reason']}],'cohort_description':'Explicit isolated fixture cohort'}
    authorization=root/'audit-direction.json'
    write_immutable(authorization,{'schema_version':domain.VERSION+'_audit_direction','authorized_by':'owner',
        'action_id':request['action_id'],'observation':observation})
    return {'schema_version':domain.VERSION+'_control','operation':'independent_audit',
        'parent_run_id':parent(store)['run_id'],'observation':observation,'authorization':bound(authorization)}


def test_registered_independent_objection_blocks_only_current_use_and_preserves_completed_operation(setup):
    store,request,root,clients,source=setup
    completed=runner.run(store,request,root=root,execute=True,clients=clients)
    assert completed['status']=='completed',parent(store)
    counts={r:len(c.requests) for r,c in clients.items()}
    manifest=audit_manifest(store,request,root)
    preview=control.run(store,manifest)
    assert parent(store)['independent_audit']['status']=='not_performed'
    registration=control.run(store,manifest,execute=True)
    assert registration==preview
    assert parent(store)['result']==completed
    assert parent(store)['independent_audit']['status']=='findings'
    target=manifest['observation']['sample_keys'][0]
    usage={'schema_version':domain.VERSION+'_control','operation':'usage_admission',
        'parent_run_id':parent(store)['run_id'],'target_keys':[target],'purpose':'existing_use'}
    assert control.run(store,usage)['status']=='blocked'
    assert counts=={r:len(c.requests) for r,c in clients.items()}
    # An identical audit intake is idempotent and cannot erase the objection.
    assert control.run(store,manifest,execute=True)==registration
    assert len(parent(store)['quality_findings'])==1
    original=store.get_record_state(*target.split('/',1))
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.objects SET revision=revision+1 WHERE collection=%s AND object_id=%s",target.split('/',1))
    assert control.run(store,usage)['status']=='blocked'


def test_audit_intake_rejects_forged_report_unsupported_reader_and_stale_cohort(setup):
    store,request,root,clients,source=setup
    assert runner.run(store,request,root=root,execute=True,clients=clients)['status']=='completed'
    manifest=audit_manifest(store,request,root)
    forged=deepcopy(manifest);forged['observation']['raw_report']['findings'][0]['reason']='虛構的另一個判斷。'
    with pytest.raises(ValueError,match='physical report'):control.run(store,forged,execute=True)
    forged=deepcopy(manifest);forged['observation']['reader']['model_id']='claude-opus-5-5'
    with pytest.raises(ValueError,match='independent quality audit'):control.run(store,forged,execute=True)
    # No new task/call slot or business row is created by a rejected intake.
    assert parent(store)['quality_findings']==[]
    assert len(parent(store)['call_reservations'])==2


def test_formal_downstream_use_requires_separate_scope_even_after_operation_completed(setup):
    store,request,root,clients,source=setup
    assert runner.run(store,request,root=root,execute=True,clients=clients)['status']=='completed'
    vid=store.read_cvp_heads()[0]['viewpoint_id']
    manifest={'schema_version':domain.VERSION+'_control','operation':'usage_admission',
        'parent_run_id':parent(store)['run_id'],'target_keys':['canonical_viewpoints/'+vid],'purpose':'formal_downstream'}
    result=control.run(store,manifest)
    assert result['status']=='blocked' and 'scope_quality_admission_required' in result['blocked'][0]['reasons'][0]
    with pytest.raises(ValueError,match='read-only'):control.run(store,manifest,execute=True)
    assert parent(store)['result']['status']=='completed'


def test_link_quality_objection_restricts_its_actual_viewpoint_dependency(setup):
    store,request,root,clients,_=setup
    assert runner.run(store,request,root=root,execute=True,clients=clients)['status']=='completed'
    manifest=audit_manifest(store,request,root)
    (collection,lid),row=next(iter(store.read_cvp_links(claim_ids=['C1']).items()))
    target=collection+'/'+lid
    observation=manifest['observation']
    observation['cohort_pins']=[{'collection':collection,'object_id':lid,'revision':row['revision'],'content_sha256':row['content_sha256']}]
    observation['sample_keys']=[target]
    observation['findings'][0]['target_keys']=[target]
    path=root/'link-audit-direction.json'
    write_immutable(path,{'schema_version':domain.VERSION+'_audit_direction','authorized_by':'owner',
                        'action_id':request['action_id'],'observation':observation})
    manifest['authorization']=bound(path)
    control.run(store,manifest,execute=True)
    vid=store.read_cvp_heads()[0]['viewpoint_id']
    usage={'schema_version':domain.VERSION+'_control','operation':'usage_admission',
        'parent_run_id':parent(store)['run_id'],'target_keys':['canonical_viewpoints/'+vid],'purpose':'existing_use'}
    assert control.run(store,usage)['status']=='blocked'
