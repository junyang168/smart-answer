from copy import deepcopy
from pathlib import Path

import pytest

from backend.pipeline import viewpoint_compiled_readset as p
from backend.pipeline.source_reconciliation_runner import write, binding, read, body
from backend.pipeline.viewpoint_validation_context import ValidationContext, ROUTINE_VALIDATION_LIMITS


def scenario(tmp_path, *, unrelated=0, history=0, packed=False, monkeypatch=None):
    root=tmp_path/'source-reconciliation'
    source=root/'before.json'
    rows=[dict(collection='claims',object_id='C',payload={
        'claim_id':'C','statement':'Own 中文 "quoted" \\ 源 [x] {y}', 'revision':2,'n':1.2345})]
    rows += [dict(collection='claims',object_id=f'U{i}',payload={
        'claim_id':f'U{i}','statement':'Unrelated text '*10,'revision':1}) for i in range(unrelated)]
    if packed:
        from backend.pipeline import immutable_proof_reuse
        monkeypatch.setattr(immutable_proof_reuse,'MIN_SNAPSHOT_BYTES',1)
    write(source,dict(records=rows,extra={'omitted':[[{'s':'"}\\{'}]]*5},headers={'same':1}))
    snapshot=binding(source)
    completion=root/'complete.json'
    write(completion,dict(status='complete',bindings=[snapshot],unrelated_history=list(range(history))))
    complete=binding(completion);target=tmp_path/'projection.json'
    return source,snapshot,complete,target,rows


@pytest.mark.parametrize('chunk', [1,2,7,128,65536])
def test_streaming_utf8_escaping_and_numbers_are_exact(tmp_path,chunk):
    source,snapshot,complete,target,rows=scenario(tmp_path,unrelated=3)
    receipt=p.compile_projection(complete,source,['claims/C','claims/missing'],target,
        sections=['headers'],chunk_bytes=chunk)
    projection=p.consume_projection(receipt['projection'],['claims/missing','claims/C'],
        completion_binding=complete,snapshot_binding=snapshot,sections=['headers'])
    assert projection['rows']=={'claims/C':rows[0]['payload']}
    assert projection['absent_keys']==['claims/missing']
    assert projection['sections']=={'headers':{'same':1}}
    assert projection['approval_status']=='projection_only_not_admission'
    assert receipt['snapshot_rows_scanned']==4 and receipt['input_bytes']==source.stat().st_size
    assert not receipt['production_resume_authorized']


def test_shared_exact_snapshot_streamed_not_restored_in_full(tmp_path,monkeypatch):
    source,snapshot,complete,target,rows=scenario(tmp_path,unrelated=20,packed=True,monkeypatch=monkeypatch)
    receipt=p.compile_projection(complete,source,['claims/C'],target)
    value=p.consume_projection(receipt['projection'],['claims/C'],completion_binding=complete,snapshot_binding=snapshot)
    assert value['rows']['claims/C']==rows[0]['payload']
    assert receipt['snapshot_rows_scanned']==21
    assert receipt['input_bytes']>source.stat().st_size


@pytest.mark.parametrize('kind',['snapshot','blob','completion'])
def test_input_drift_never_publishes_projection(tmp_path,monkeypatch,kind):
    source,snapshot,complete,target,_=scenario(tmp_path,unrelated=3,packed=True,monkeypatch=monkeypatch)
    if kind=='snapshot':source.write_text('{}')
    elif kind=='completion':Path(complete['path']).write_text('{}')
    else:next((source.parent/'immutable-snapshots').glob('*.rows.json')).write_text('[]')
    with pytest.raises(ValueError):p.compile_projection(complete,source,['claims/C'],target)
    assert not target.exists()


def test_no_overwrite_or_automatic_recompilation(tmp_path):
    source,snapshot,complete,target,_=scenario(tmp_path)
    receipt=p.compile_projection(complete,source,['claims/C'],target)
    original=target.read_bytes()
    with pytest.raises(ValueError,match='already exists'):
        p.compile_projection(complete,source,['claims/C'],target)
    missing={**receipt['projection'],'path':str(tmp_path/'missing.json')}
    with pytest.raises(FileNotFoundError):
        p.consume_projection(missing,['claims/C'],completion_binding=complete,snapshot_binding=snapshot)
    assert target.read_bytes()==original


@pytest.mark.parametrize('kind',['keys','snapshot','completion','field','section'])
def test_consumer_rejects_declared_boundary_drift(tmp_path,kind):
    source,snapshot,complete,target,_=scenario(tmp_path)
    receipt=p.compile_projection(complete,source,['claims/C'],target)
    options=dict(completion_binding=complete,snapshot_binding=snapshot)
    keys=['claims/C']
    if kind=='keys':keys=['claims/other']
    elif kind=='snapshot':options['snapshot_binding']={**snapshot,'file_sha256':'0'*64}
    elif kind=='completion':options['completion_binding']={**complete,'file_sha256':'0'*64}
    elif kind=='field':options['field']='before_records'
    else:options['sections']=['different']
    with pytest.raises(ValueError,match='denominator drift'):
        p.consume_projection(receipt['projection'],keys,**options)


@pytest.mark.parametrize('kind',['approval','row','absent','compiler','seal','physical'])
def test_consumer_rejects_tampering_without_approval_or_rebuild(tmp_path,kind):
    source,snapshot,complete,target,_=scenario(tmp_path)
    receipt=p.compile_projection(complete,source,['claims/C','claims/missing'],target)
    value=body(read(target))
    if kind=='approval':value['semantic_approval_granted']=True
    elif kind=='row':value['rows'].append(deepcopy(value['rows'][0]))
    elif kind=='absent':value['absent_keys']=['claims/C']
    elif kind=='compiler':value['compiler_bindings'][0]['file_sha256']='0'*64
    elif kind=='seal':value['artifact_sha256']='0'*64
    if kind=='physical':target.write_text('{}')
    else:
        write(target,value,immutable=False)
        receipt['projection']=binding(target)
    with pytest.raises(ValueError):
        p.consume_projection(receipt['projection'],['claims/C','claims/missing'],
                             completion_binding=complete,snapshot_binding=snapshot)


@pytest.mark.parametrize('kind',['io','time','value'])
def test_compilation_is_budgeted_explicit_maintenance(tmp_path,kind):
    source,snapshot,complete,target,_=scenario(tmp_path,unrelated=20)
    kwargs={'bytes':10} if kind=='io' else {'seconds':1e-12} if kind=='time' else {'value_bytes':10}
    with pytest.raises(ValueError,match='budget'):
        p.compile_projection(complete,source,['claims/C'],target,limits=p.CompileLimits(**kwargs))
    assert not target.exists()


@pytest.mark.parametrize('payload',['null','{}','[{}]'])
def test_bad_row_field_cannot_become_false_absence_proof(tmp_path,payload):
    source=tmp_path/'before.json';source.write_text('{"records":'+payload+'}')
    snapshot=binding(source);complete=tmp_path/'complete.json'
    write(complete,{'status':'complete','bindings':[snapshot]})
    with pytest.raises(ValueError):
        p.compile_projection(binding(complete),source,['claims/C'],tmp_path/'projection.json')


def test_duplicate_selected_row_and_missing_section_reject(tmp_path):
    source,snapshot,complete,target,rows=scenario(tmp_path)
    write(source,{'records':rows+rows},immutable=False)
    write(Path(complete['path']),{'status':'complete','bindings':[binding(source)]},immutable=False)
    with pytest.raises(ValueError,match='duplicate selected'):
        p.compile_projection(binding(Path(complete['path'])),source,['claims/C'],target)
    assert not target.exists()


@pytest.mark.parametrize('scale',[1,10,100])
def test_cold_projection_consumption_never_opens_N_or_H_history(tmp_path,monkeypatch,scale):
    source,snapshot,complete,target,rows=scenario(tmp_path,unrelated=20*scale,history=100*scale)
    receipt=p.compile_projection(complete,source,['claims/C'],target)
    original=Path.open
    denied={source,Path(complete['path'])}
    def guarded(self,*args,**kwargs):
        if self in denied:pytest.fail('routine projection tried to reopen N/H historical inputs')
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'open',guarded)
    with ValidationContext('cold projection',limits=ROUTINE_VALIDATION_LIMITS) as ctx:
        actual=p.consume_projection(receipt['projection'],['claims/C'],completion_binding=complete,snapshot_binding=snapshot)
    assert actual['rows']=={'claims/C':rows[0]['payload']}
    assert ctx.metrics['json_parses']==1 and ctx.metrics['proof_runs']==1
    # Full current compiler/schema code pins plus the ONE small projection,
    # independent of the size of either archived snapshot or completion.
    expected=2*sum(code.stat().st_size for code in p.CODE)+3*target.stat().st_size
    assert ctx.metrics['file_io_bytes']==expected


def test_exact_scoped_snapshot_never_loads_all_active_objects():
    from backend.pipeline.source_reconciliation_runtime import ProductionPorts
    from backend.api.canonical_repository.postgres_store import record_content_sha
    payload={'claim_id':'C','revision':2,'review_status':'human_review_required'}
    class DB:
        calls=[]
        def connect(self):return self
        def cursor(self):return self
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,query,params=None):self.calls.append((query,params))
        def fetchall(self):return [('claims','C',2,record_content_sha(payload),payload,None)]
    db=DB();ports=ProductionPorts(db)
    result=ports.scoped_snapshot(['claims/C'],absent_keys=['claims/missing'])
    assert result['claims/C']['review_status']=='human_review_required'
    assert len(db.calls)==2 and 'unnest' in db.calls[-1][0]
    assert db.calls[-1][1]==(['claims','claims'],['C','missing'])
    assert 'WHERE retired_at IS NULL' not in db.calls[-1][0]


@pytest.mark.parametrize('kind',['missing','sha','revision','retired','unexpected','duplicate','negative'])
def test_scoped_current_observation_is_fresh_and_fail_closed(kind):
    from backend.pipeline.source_reconciliation_runtime import ProductionPorts
    from backend.api.canonical_repository.postgres_store import record_content_sha
    payload={'claim_id':'C','revision':2}
    row=['claims','C',2,record_content_sha(payload),payload,None]
    if kind=='sha':row[3]='0'*64
    elif kind=='revision':row[2]=3
    elif kind=='retired':row[5]='retired'
    elif kind=='unexpected':row[1]='foreign'
    elif kind=='negative':row[1]='missing'
    class DB:
        def connect(self):return self
        def cursor(self):return self
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,*a):pass
        def fetchall(self):return [] if kind=='missing' else [row,row] if kind=='duplicate' else [row]
    with pytest.raises(ValueError):
        ProductionPorts(DB()).scoped_snapshot(['claims/C'],absent_keys=['claims/missing'])


def test_compiler_code_and_runtime_changes_invalidate_projection(tmp_path,monkeypatch):
    source,snapshot,complete,target,_=scenario(tmp_path)
    receipt=p.compile_projection(complete,source,['claims/C'],target)
    monkeypatch.setattr(p,'runtime_identity',lambda:{'python':'different','pydantic':'different'})
    with pytest.raises(ValueError,match='runtime identity drift'):
        p.consume_projection(receipt['projection'],['claims/C'],completion_binding=complete,snapshot_binding=snapshot)


def test_missing_requested_section_never_publishes(tmp_path):
    source,snapshot,complete,target,_=scenario(tmp_path)
    with pytest.raises(ValueError,match='fields missing'):
        p.compile_projection(complete,source,['claims/C'],target,sections=['missing'])
    assert not target.exists()


def test_scoped_database_observation_is_never_cached():
    from backend.pipeline.source_reconciliation_runtime import ProductionPorts
    from backend.api.canonical_repository.postgres_store import record_content_sha
    class DB:
        revision=1
        queries=0
        def connect(self):return self
        def cursor(self):return self
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,query,params=None):
            if params:self.queries+=1
        def fetchall(self):
            payload={'claim_id':'C','revision':self.revision}
            return [('claims','C',self.revision,record_content_sha(payload),payload,None)]
    db=DB();ports=ProductionPorts(db)
    assert ports.scoped_snapshot(['claims/C'])['claims/C']['revision']==1
    db.revision=2
    assert ports.scoped_snapshot(['claims/C'])['claims/C']['revision']==2
    assert db.queries==2


@pytest.mark.parametrize('chunk',[-1,0,True,1024**2+1])
def test_unbounded_streaming_chunk_cannot_defeat_memory_budget(tmp_path,chunk):
    source,snapshot,complete,target,_=scenario(tmp_path)
    with pytest.raises(ValueError,match='streaming chunk'):
        p.compile_projection(complete,source,['claims/C'],target,chunk_bytes=chunk)
    assert not target.exists()
