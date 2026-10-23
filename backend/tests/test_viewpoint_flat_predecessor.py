from copy import deepcopy
from pathlib import Path

import pytest

from backend.pipeline import viewpoint_compiled_readset as p
from backend.pipeline.source_reconciliation_runner import binding, body, read, write
from backend.pipeline.viewpoint_validation_context import (
    ValidationContext, ROUTINE_VALIDATION_LIMITS, ValidationBudgetExceeded,
)


KEYS=['claims/C','claims/new']


def projected(tmp_path,name,revision,*,created=False,unrelated=0):
    root=tmp_path/name
    snapshot=root/'before.json'; complete=root/'complete.json'; target=root/'projection.json'
    rows=[dict(collection='claims',object_id='C',payload={
        'claim_id':'C','revision':revision,'statement':'Own 中文',
        'review_status':'human_review_required'})]
    if created:rows.append(dict(collection='claims',object_id='new',payload={
        'claim_id':'new','revision':1,'statement':'Retained own claim'}))
    rows += [dict(collection='claims',object_id=f'U{i}',payload={
        'claim_id':f'U{i}','revision':1}) for i in range(unrelated)]
    write(snapshot,dict(records=rows))
    write(complete,dict(status='complete',bindings=[binding(snapshot)]))
    result=p.compile_projection(binding(complete),snapshot,KEYS,target)
    return dict(projection=result['projection'],completion=binding(complete),
                snapshot=binding(snapshot),field='records')


def chain(tmp_path,count=2,unrelated=0):
    states=[projected(tmp_path,f'epoch-{i}',i+1,created=i>0,unrelated=unrelated)
            for i in range(count+1)]
    transitions=[dict(before=a,after=b) for a,b in zip(states,states[1:])]
    return transitions,tmp_path/'flat.json'


def consume(receipt,transitions,states):
    return p.consume_predecessor_map(receipt['projection'],KEYS,states,
        chain_sha256=receipt['chain_sha256'],first_before=transitions[0]['before'],
        last_after=transitions[-1]['after'])


def test_flat_projection_restores_exact_rows_and_created_absence_preserving_hold(tmp_path):
    transitions,target=chain(tmp_path)
    result=p.compile_predecessor_map(transitions,KEYS,target)
    latest=p._projected_state(transitions[-1]['after'],KEYS)
    original=deepcopy(latest)
    value=consume(result,transitions,latest)
    assert latest==original
    assert value['rows']['claims/C']['revision']==1
    assert value['rows']['claims/C']['review_status']=='human_review_required'
    assert value['rows']['claims/new'] is None
    assert value['approval_status']=='projection_only_not_admission'
    assert not result['production_resume_authorized']


@pytest.mark.parametrize('kind',['gap','order','repeat','cycle','denominator','spec'])
def test_invalid_chain_never_publishes(tmp_path,kind):
    transitions,target=chain(tmp_path)
    if kind=='gap':transitions[1]['before']=transitions[0]['before']
    elif kind=='order':transitions.reverse()
    elif kind=='repeat':transitions.append(deepcopy(transitions[-1]))
    elif kind=='cycle':transitions[1]['after']=transitions[0]['before']
    elif kind=='denominator':transitions[0]['before']['field']='different'
    else:transitions[0]['after']['supplied_approved']=True
    with pytest.raises(ValueError):p.compile_predecessor_map(transitions,KEYS,target)
    assert not target.exists()


@pytest.mark.parametrize('kind',['current','hold','missing','extra','anchor','chain','runtime','compiler','approval','population','physical'])
def test_consumer_rejects_drift_without_opening_old_artifacts(tmp_path,monkeypatch,kind):
    transitions,target=chain(tmp_path)
    result=p.compile_predecessor_map(transitions,KEYS,target)
    latest=p._projected_state(transitions[-1]['after'],KEYS)
    if kind=='current':latest['claims/C']['revision']=99
    elif kind=='hold':latest['claims/C']['review_status']='approved'
    elif kind=='missing':latest.pop('claims/C')
    elif kind=='extra':latest['claims/unrelated']={}
    elif kind=='anchor':transitions[0]['before']['completion']['file_sha256']='0'*64
    elif kind=='chain':result['chain_sha256']='0'*64
    elif kind=='runtime':monkeypatch.setattr(p,'runtime_identity',lambda:{'python':'changed'})
    elif kind=='physical':target.write_text('{}')
    else:
        value=body(read(target))
        if kind=='compiler':value['compiler_bindings'][0]['file_sha256']='0'*64
        elif kind=='approval':value['semantic_approval_granted']=True
        else:value['oldest'].pop('claims/new')
        write(target,value,immutable=False);result['projection']=binding(target)
    def no_rebuild(*args,**kwargs):pytest.fail('consumer attempted historical compilation')
    monkeypatch.setattr(p,'compile_projection',no_rebuild)
    monkeypatch.setattr(p,'compile_predecessor_map',no_rebuild)
    with pytest.raises(ValueError):consume(result,transitions,latest)


def test_compilation_is_not_routine_fallback_and_cannot_overwrite(tmp_path):
    transitions,target=chain(tmp_path)
    with ValidationContext('routine',limits=ROUTINE_VALIDATION_LIMITS):
        with pytest.raises(ValidationBudgetExceeded):p.compile_predecessor_map(transitions,KEYS,target)
    result=p.compile_predecessor_map(transitions,KEYS,target)
    original=target.read_bytes()
    with pytest.raises(ValueError):p.compile_predecessor_map(transitions,KEYS,target)
    assert target.read_bytes()==original
    missing={**result,'projection':{**result['projection'],'path':str(tmp_path/'missing.json')}}
    with pytest.raises(FileNotFoundError):consume(missing,transitions,p._projected_state(transitions[-1]['after'],KEYS))


@pytest.mark.parametrize('limits',[p.CompileLimits(bytes=10),p.CompileLimits(seconds=1e-12),p.CompileLimits(value_bytes=10)])
def test_explicit_compilation_resource_failure_leaves_no_output(tmp_path,limits):
    transitions,target=chain(tmp_path)
    with pytest.raises(ValueError,match='budget|scalability'):
        p.compile_predecessor_map(transitions,KEYS,target,limits=limits)
    assert not target.exists()


@pytest.mark.parametrize('scale',[1,10,100])
def test_cold_flat_consumer_reads_no_H_chain_or_N_snapshot(tmp_path,monkeypatch,scale):
    # Scale H and unrelated N together. Compilation is an explicit migration;
    # this test measures only consumption, not whole production acceptance.
    transitions,target=chain(tmp_path,count=scale,unrelated=scale)
    result=p.compile_predecessor_map(transitions,KEYS,target)
    latest=p._projected_state(transitions[-1]['after'],KEYS)
    original=Path.open
    def guarded(self,*args,**kwargs):
        if self.is_relative_to(tmp_path) and self!=target:
            pytest.fail('flat consumer opened archived chain/snapshot/projection')
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'open',guarded)
    with ValidationContext('cold flat consumer',limits=ROUTINE_VALIDATION_LIMITS) as ctx:
        value=consume(result,transitions,latest)
    assert value['rows']['claims/C']['revision']==1
    assert ctx.metrics['json_parses']==1 and ctx.metrics['proof_runs']==1
    assert ctx.metrics['file_io_bytes']==2*sum(code.stat().st_size for code in p.CODE)+3*target.stat().st_size
    assert set(body(read(target)))=={
        'schema_version','approval_status','semantic_approval_granted','readset_keys',
        'chain_sha256','first_before','last_after','oldest','latest',
        'compiler_bindings','compiler_runtime','model_calls','registry_writes'}
