from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.pipeline import source_reconciliation as c
from backend.pipeline import viewpoint_completed_predecessors as m
from backend.pipeline import viewpoint_applied_source_epochs as epochs
from backend.pipeline.source_reconciliation_runner import write, binding, body, read


def scenario(tmp_path,monkeypatch):
    source=tmp_path/'effective-package.json';write(source,{'exact':'package'})
    payload={'claim_id':'C','revision':2,'statement':'original own source'}
    plan={'operations':[
        dict(collection='claims',object_id='C',operation='update',before_revision=2,before_sha256=c.record_content_sha(payload)),
        dict(collection='claims',object_id='N',operation='create',before_revision=None,before_sha256=None)]}
    write(tmp_path/'dry-run-plan.json',plan)
    snapshot=tmp_path/'before.json';write(snapshot,{'before_records':[
        dict(collection='claims',object_id='C',payload=payload),
        dict(collection='claims',object_id='unrelated',payload={'claim_id':'unrelated','revision':1})]})
    snap=binding(snapshot);anchor=tmp_path/'compiler-anchor.json'
    write(anchor,dict(kind='completed_source_predecessor_compilation',status='complete',source=binding(source),
        plan=binding(tmp_path/'dry-run-plan.json'),bindings=[snap],semantic_approval_granted=False,model_calls=0,Registry_writes=0))
    projection=m.compiled.compile_projection(binding(anchor),snapshot,['claims/C','claims/N'],tmp_path/'projection.json',field='before_records')['projection']
    declaration=tmp_path/'input.json'
    write(declaration,dict(schema_version=m.SCHEMA,source=binding(source),completion=binding(anchor),snapshot=snap,aliases=[snap],projection=projection))
    monkeypatch.setattr(epochs,'read_pinned_versions',lambda pins,store:{('claims','C'):deepcopy(payload)})
    return binding(declaration),binding(source),snap,plan


def test_routine_reads_small_cut_and_every_exact_sql_before_not_old_snapshot(tmp_path,monkeypatch):
    bound,source,snapshot,plan=scenario(tmp_path,monkeypatch)
    # Archive is not a runtime input after an explicit copied readset. No
    # filesystem stat/hash/parse or expensive fallback is allowed on this path.
    original=m.c.verify_file
    def verify(pin):
        assert pin!=snapshot
        return original(pin)
    monkeypatch.setattr(m.c,'verify_file',verify)
    with m.predecessor_inputs([bound]):
        with pytest.raises(ValueError,match='lacks actual SQL'):
            m.verify_archived_refusal(snapshot,source)
        m.authenticate_snapshot(snapshot,source,plan,object())
        m.verify_archived_refusal(snapshot,source)
    assert m._scope.get() is None


def test_no_declaration_preserves_original_physical_guard(tmp_path,monkeypatch):
    _,source,snapshot,plan=scenario(tmp_path,monkeypatch)
    (tmp_path/'before.json').write_text('{}')
    with pytest.raises(ValueError):m.authenticate_snapshot(snapshot,source,plan,object())
    with pytest.raises(ValueError):m.verify_archived_refusal(snapshot,source)


@pytest.mark.parametrize('kind',['source','snapshot','anchor_source','anchor_plan','approval','sql_row','missing_sql','create_present','projection_file','projection_code','population','alias','duplicate'])
def test_input_drift_never_waives_sql_or_recompiles(tmp_path,monkeypatch,kind):
    bound,source,snapshot,plan=scenario(tmp_path,monkeypatch)
    spec=body(read(Path(bound['path'])))
    if kind=='source':source={**source,'file_sha256':'0'*64}
    elif kind=='snapshot':snapshot={**snapshot,'file_sha256':'0'*64}
    elif kind in {'anchor_source','anchor_plan','approval'}:
        anchor=body(read(Path(spec['completion']['path'])))
        if kind=='approval':anchor['semantic_approval_granted']=True
        else:anchor[kind.removeprefix('anchor_')]['file_sha256']='0'*64
        write(Path(spec['completion']['path']),anchor,immutable=False);spec['completion']=binding(Path(spec['completion']['path']))
    elif kind in {'sql_row','missing_sql'}:
        monkeypatch.setattr(epochs,'read_pinned_versions',lambda pins,store:{} if kind=='missing_sql' else {('claims','C'):{'claim_id':'C','revision':2,'statement':'drift'}})
    elif kind in {'create_present','projection_code','population'}:
        value=body(read(Path(spec['projection']['path'])))
        if kind=='create_present':
            value['rows'].append(dict(collection='claims',object_id='N',payload={'claim_id':'N','revision':1}));value['absent_keys']=[]
        elif kind=='population':value['rows']=[]
        else:value['compiler_bindings'][0]['file_sha256']='0'*64
        write(Path(spec['projection']['path']),value,immutable=False);spec['projection']=binding(Path(spec['projection']['path']))
    elif kind=='projection_file':(tmp_path/'projection.json').write_text('{}')
    elif kind=='alias':spec['aliases'].append({**snapshot,'path':str(tmp_path/'another-before.json'),'file_sha256':'0'*64})
    write(Path(bound['path']),spec,immutable=False);bound=binding(Path(bound['path']))
    declarations=[bound,bound] if kind=='duplicate' else [bound]
    with pytest.raises((ValueError,FileNotFoundError)):
        with m.predecessor_inputs(declarations):m.authenticate_snapshot(snapshot,source,plan,object())
    assert m._scope.get() is None


def test_other_refusal_files_and_unknown_source_always_get_physical_sha(tmp_path,monkeypatch):
    bound,source,snapshot,plan=scenario(tmp_path,monkeypatch)
    other=tmp_path/'raw-refusal.json';write(other,{'original':'refusal'});pin=binding(other)
    other.write_text('{}')
    with m.predecessor_inputs([bound]):
        m.authenticate_snapshot(snapshot,source,plan,object())
        with pytest.raises(ValueError):m.verify_archived_refusal(pin,source)
        with pytest.raises(ValueError):m.verify_archived_refusal(snapshot,{**source,'file_sha256':'0'*64})


@pytest.mark.parametrize('change',['duplicate','unpin_create','unsupported','empty'])
def test_complete_plan_population_is_required(tmp_path,monkeypatch,change):
    bound,source,snapshot,plan=scenario(tmp_path,monkeypatch)
    if change=='duplicate':plan['operations'].append(deepcopy(plan['operations'][0]))
    elif change=='unpin_create':plan['operations'][1]['before_revision']=1
    elif change=='unsupported':plan['operations'][0]['operation']='delete'
    else:plan['operations']=[]
    with pytest.raises(ValueError):m.plan_readset(plan,object())


@pytest.mark.parametrize('raw_plan',[True,False])
def test_explicit_compiler_uses_complete_archive_then_no_overwrite(tmp_path,monkeypatch,raw_plan):
    _,source,snapshot,plan=scenario(tmp_path,monkeypatch)
    if raw_plan:(tmp_path/'dry-run-plan.json').write_text(json.dumps(plan))
    calls=[]
    def actual_guard(bound,store,**kw):
        assert bound==source
        m.c.verify_file(snapshot);calls.append('full original physical/SQL guard')
        return {'source_repair_application':{'before_binding':snapshot}}
    monkeypatch.setattr(epochs,'consume_applied_source_package',actual_guard)
    output=tmp_path/'new-maintenance'
    bound=m.compile_source_predecessor(source,output,object())
    result=body(read(output/'compilation-result.json'))
    assert result['whole_operation_population']==2
    assert result['retained_before_versions']==1 and result['create_absences']==1
    assert result['exact_SQL_predecessor_equality'] is True
    assert result['production_resume_authorized'] is False
    assert calls==['full original physical/SQL guard']
    with m.predecessor_inputs([bound]):m.authenticate_snapshot(snapshot,source,plan,object())
    with pytest.raises(ValueError,match='new absolute'):
        m.compile_source_predecessor(source,output,object())


def test_bounded_routine_cannot_start_compiler(tmp_path,monkeypatch):
    from backend.pipeline.viewpoint_validation_context import ValidationContext,ROUTINE_VALIDATION_LIMITS
    _,source,_,_=scenario(tmp_path,monkeypatch)
    monkeypatch.setattr(epochs,'consume_applied_source_package',lambda *a,**k:pytest.fail('routine cannot invoke legacy authentication'))
    with ValidationContext('routine',limits=ROUTINE_VALIDATION_LIMITS):
        with pytest.raises(ValueError):m.compile_source_predecessor(source,tmp_path/'forbidden',object())
    assert not (tmp_path/'forbidden').exists()
