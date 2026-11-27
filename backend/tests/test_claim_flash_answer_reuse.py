from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from backend.pipeline import claim_flash_answer_reuse as reuse,claim_flash_common as f,claim_flash_evidence as eo,evidence_refresh as e
from backend.tests.test_claim_flash_focused import job
from backend.tests.test_evidence_refresh import sample
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres


def fixture(tmp_path):
    operation=tmp_path/'operation';root=operation/'flow/fidelity'
    class Ports:
        identity={'gpt':{'backend':'codex_subscription','model':'test','effort':'medium'}}
        focus_evidence_findings=True
        focused_review_instruction='只查原疑點'
        def __init__(self):self.root=operation
        def repair(self,request):pytest.fail('reused answer must not dispatch another model')
    ports=Ports()
    target=eo.focused_request({'identity':ports.identity['gpt'],'system':eo.PROMPT,
        'input':{'task':'evidence_fidelity_repair','jobs':[job()]},'schema':eo.Edits.model_json_schema()})
    source={**deepcopy(target),'diagnostic_scope':{'production_admission':False}}
    target={**target,'execution_recovery':{'original_request_sha256':'old-unknown','authority_sha256':'previous'}}
    target_folder=root/'gpt'/e.digest(target);source_folder=operation/'diagnostic/gpt'/e.digest(source)
    answer={'items':[{'job_id':job()['job_id'],'action':'repair','stance':'quoted','reason':'只核對原疑點。'}]}
    def transport(request,folder,success):
        key=e.digest(request);t=operation/'gpt'/key/'transport'
        e.save(folder/'request.json',request);e.save(folder/'started.json',{'request_sha256':key})
        e.save(t/'response-schema.json',request['schema'])
        (t/'stdout.log').write_text('saved stdout');(t/'stderr.log').write_text('saved stderr')
        if success:e.save(t/'response.json',answer);e.save(folder/'response.json',answer)
        command=['codex','exec','--model','test','--config','model_reasoning_effort="medium"',
            '--output-schema',str(t/'response-schema.json'),'--output-last-message',str(t/'response.json'),'--json','-']
        e.save(t/'invocation.json',{'input_sha256':'identical-wire','executable_sha256':'identical-binary',
            'command_sha256':reuse.hashlib.sha256(json.dumps(command).encode()).hexdigest()})
        receipt={'returncode':0 if success else None,'files':{p.name:reuse.hashlib.sha256(p.read_bytes()).hexdigest() for p in t.iterdir() if p.name in {'stdout.log','stderr.log','response.json'}}}
        if not success:receipt['error_type']='TimeoutExpired'
        e.save(t/'exit.json',receipt)
        return t,receipt,command
    src,src_exit,src_cmd=transport(source,source_folder,True)
    dst,dst_exit,dst_cmd=transport(target,target_folder,False)
    authority={'contract':reuse.CONTRACT,'owner_direction':'继续跑','target_request_sha256':e.digest(target),
        'source_request_path':str(source_folder/'request.json'),'source_request_sha256':e.digest(source),
        'source_exit_sha256':e.digest(src_exit),'target_exit_sha256':e.digest(dst_exit),'response_sha256':e.digest(answer),
        'source_command':src_cmd,'target_command':dst_cmd}
    e.save(target_folder/'returned-answer-admission.json',authority)
    return SimpleNamespace(root=root,ports=ports,target=target,source=source,answer=answer,
        authority=authority,target_folder=target_folder,source_folder=source_folder,src=src,dst=dst)


@pytest.mark.parametrize('fault',[None,'no_authority','input','schema','identity','response','transport','exit','invocation','command','wrong_actual_model','actual_schema','not_timeout','failed_source','outside','accepted_target'])
def test_reuse_only_exact_successful_answer_and_preserve_unknown_call(tmp_path,fault):
    x=fixture(tmp_path)
    if fault=='no_authority':
        (x.target_folder/'returned-answer-admission.json').unlink()
        assert reuse.returned_answer(x.root,x.ports,x.target) is None
        return
    if fault in {'input','schema','identity'}:
        # Even a fresh authority binding the changed request cannot equate
        # a successful answer for another input with this exact target.
        source=deepcopy(x.source)
        if fault=='input':source['input']['jobs'][0]['record']['statement']='另一意思。'
        if fault=='schema':source['schema']['description']='changed schema'
        if fault=='identity':source['identity']['model']='another-model'
        path=x.ports.root/'changed/gpt'/e.digest(source)/'request.json';e.save(path,source)
        authority={**x.authority,'source_request_path':str(path),'source_request_sha256':e.digest(source)}
        (x.target_folder/'returned-answer-admission.json').write_text(json.dumps(authority))
    elif fault=='response':(x.source_folder/'response.json').write_text('{}')
    elif fault=='transport':(x.src/'stdout.log').write_text('modified')
    elif fault=='exit':(x.src/'exit.json').write_text('{}')
    elif fault=='invocation':
        value=reuse.read(x.src/'invocation.json');value['input_sha256']='different-wire';(x.src/'invocation.json').write_text(json.dumps(value))
    elif fault=='command':
        authority=deepcopy(x.authority);authority['source_command'][authority['source_command'].index('--model')+1]='another-model'
        (x.target_folder/'returned-answer-admission.json').write_text(json.dumps(authority))
    elif fault=='wrong_actual_model':
        authority=deepcopy(x.authority);command=authority['source_command'];command[command.index('--model')+1]='another-model'
        invocation=reuse.read(x.src/'invocation.json');invocation['command_sha256']=reuse.hashlib.sha256(json.dumps(command).encode()).hexdigest()
        (x.src/'invocation.json').write_text(json.dumps(invocation));(x.target_folder/'returned-answer-admission.json').write_text(json.dumps(authority))
    elif fault=='actual_schema':(x.src/'response-schema.json').write_text('{}')
    elif fault in {'not_timeout','failed_source'}:
        transport=x.dst if fault=='not_timeout' else x.src
        receipt=reuse.read(transport/'exit.json')
        if fault=='not_timeout':receipt['error_type']='KeyboardInterrupt'
        else:receipt['returncode']=1
        (transport/'exit.json').write_text(json.dumps(receipt))
        authority=deepcopy(x.authority);authority['target_exit_sha256' if fault=='not_timeout' else 'source_exit_sha256']=e.digest(receipt)
        (x.target_folder/'returned-answer-admission.json').write_text(json.dumps(authority))
    elif fault=='outside':
        authority={**x.authority,'source_request_path':str(tmp_path/'outside/request.json')};(x.target_folder/'returned-answer-admission.json').write_text(json.dumps(authority))
    elif fault=='accepted_target':e.save(x.target_folder/'response.json',x.answer)
    if fault:
        with pytest.raises(ValueError):reuse.returned_answer(x.root,x.ports,x.target)
        return
    assert reuse.returned_answer(x.root,x.ports,x.target)==x.answer
    assert not (x.target_folder/'response.json').exists()
    assert reuse.read(x.dst/'exit.json')['error_type']=='TimeoutExpired'
    assert reuse.read(x.target_folder/'returned-answer-proof.json')['model_calls']==0


def test_normal_flash_consumes_admitted_answer_and_still_validates_it(tmp_path):
    x=fixture(tmp_path)
    # The same ordinary request built by f.gpt selects its stopped focused key.
    target=deepcopy(x.target);target.pop('execution_recovery')
    target_folder=x.root/'gpt'/e.digest(target)
    e.save(target_folder/'request.json',target);e.save(target_folder/'started.json',{'request_sha256':e.digest(target)})
    # Copy only test receipts for this synthetic stopped operation identity.
    old=x.dst;new=x.ports.root/'gpt'/e.digest(target)/'transport'
    for name in ('stdout.log','stderr.log','response-schema.json','exit.json'):
        new.mkdir(parents=True,exist_ok=True);(new/name).write_bytes((old/name).read_bytes())
    command=list(x.authority['target_command'])
    for i,v in enumerate(command):command[i]=v.replace(str(old),str(new))
    inv=reuse.read(old/'invocation.json');inv['command_sha256']=reuse.hashlib.sha256(json.dumps(command).encode()).hexdigest();e.save(new/'invocation.json',inv)
    authority={**x.authority,'target_request_sha256':e.digest(target),'target_command':command}
    e.save(target_folder/'returned-answer-admission.json',authority)
    result=list(f.gpt(x.root,x.ports,'evidence_fidelity_repair',[job()],eo.Edits.model_json_schema(),eo.PROMPT,semantic_review=False))
    assert len(result)==1 and result[0][1]==x.answer
    eo.validate_focused_reply(target,result[0][1])
    assert not (target_folder/'response.json').exists()


@pytest.mark.parametrize('tampered',[False,True])
def test_outer_evidence_recovery_uses_verified_answer_then_normal_sql_commit(sample,tampered):
    from backend.tests.test_claim_flash_evidence import Ports as BasePorts,saved_inventory
    store,base,_=sample;records,sources=f.load_source(store,'S')
    plan={'source_id':'S','source_body_sha256':sources['S']['body_sha256'],'groups':[{
        'group_id':'G','paragraph_keys':['S0001'],'lower_record_keys':['evidence_steps/E'],'reference_record_keys':[]}]}
    previous=saved_inventory(base/'previous',sources)
    class Ports(BasePorts):
        def __init__(self):super().__init__();self.root=base/'providers';self.fail=True
        def repair(self,request):
            if request['input']['task']=='evidence_fidelity_repair':
                if self.fail:raise TimeoutError('lost reply')
                pytest.fail('completed saved adjudication must not run again')
            return super().repair(request)
    ports=Ports();root=base/'evidence'
    with pytest.raises(TimeoutError):eo.run(store,'S',plan,root,ports,previous,apply=True)
    old=next(p.parent for p in root.rglob('started.json') if not (p.parent/'response.json').exists())
    target=reuse.read(old/'request.json');source={**deepcopy(target),'diagnostic_scope':{'production_admission':False}}
    answer=BasePorts().repair(target)
    source_folder=ports.root/'diagnostic/gpt'/e.digest(source)
    def transport(request,folder,success):
        t=ports.root/'gpt'/e.digest(request)/'transport'
        e.save(folder/'request.json',request);e.save(folder/'started.json',{'request_sha256':e.digest(request)})
        e.save(t/'response-schema.json',request['schema']);(t/'stdout.log').write_text('stdout');(t/'stderr.log').write_text('stderr')
        if success:e.save(folder/'response.json',answer);e.save(t/'response.json',answer)
        command=['codex','exec','--model',request['identity']['model'],'--config','model_reasoning_effort="'+request['identity']['effort']+'"',
            '--output-schema',str(t/'response-schema.json'),'--output-last-message',str(t/'response.json'),'--json','-']
        e.save(t/'invocation.json',{'input_sha256':'same','executable_sha256':'same',
            'command_sha256':reuse.hashlib.sha256(json.dumps(command).encode()).hexdigest()})
        receipt={'returncode':0 if success else None,'files':{p.name:reuse.hashlib.sha256(p.read_bytes()).hexdigest()
            for p in t.iterdir() if p.name in {'stdout.log','stderr.log','response.json'}}}
        if not success:receipt['error_type']='TimeoutExpired'
        e.save(t/'exit.json',receipt)
        return t,receipt,command
    src,src_exit,src_cmd=transport(source,source_folder,True);dst,dst_exit,dst_cmd=transport(target,old,False)
    e.save(old/'returned-answer-admission.json',{'contract':reuse.CONTRACT,'owner_direction':'继续跑',
        'target_request_sha256':e.digest(target),'source_request_path':str(source_folder/'request.json'),
        'source_request_sha256':e.digest(source),'source_exit_sha256':e.digest(src_exit),'target_exit_sha256':e.digest(dst_exit),
        'response_sha256':e.digest(answer),'source_command':src_cmd,'target_command':dst_cmd})
    if tampered:(source_folder/'response.json').write_text('{}')
    calls=len(ports.calls);ports.fail=False
    if tampered:
        with pytest.raises(ValueError,match='saved response differs'):eo.run(store,'S',plan,root,ports,previous,apply=True)
        assert len(ports.calls)==calls
        return
    result=eo.run(store,'S',plan,root,ports,previous,apply=True)
    assert result['status']=='completed'
    assert store.get_record_state('evidence_steps','E')['payload']['statement']=='沒有聽從祂的話，顯明沒有真正相信祂。'
    assert not (old/'response.json').exists()
    assert reuse.read(dst/'exit.json')['error_type']=='TimeoutExpired'
