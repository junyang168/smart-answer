"""A spent no-report API call gets one authenticated, bounded continuation."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.pipeline import claim_management as cm
from backend.pipeline.claim_anthropic_api import ClaimAnthropicAPI, consumed_output_limit
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.source_reconciliation_runner import body,read,stage_adapter
from backend.pipeline.source_reconciliation_runtime import claim_management_model_adapter
from backend.pipeline.subscription_process import digest_file
from backend.pipeline.cvp_sql_commit import binding
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres
from backend.tests.test_claim_management_execution_postgres import operation_request,seed_claims,SyntheticPorts
from backend.tests.test_claim_owner_call_extension import authorize


def overwrite(path,value):
    # Deliberately tamper only with isolated test artifacts.
    path.write_text(json.dumps(value))


@pytest.fixture
def exhausted(action,request,monkeypatch):
    store=action[0];seed_claims(store,['C'])
    drift=getattr(request,'param',None)
    if drift:
        from backend.api.canonical_repository.postgres_store import record_content_sha
        payload={'viewpoint_id':'CONSUMER','revision':1}
        with store.connect() as conn:
            conn.execute("INSERT INTO wang_knowledge.objects (collection,object_id,revision,content_sha256,payload) VALUES ('canonical_viewpoints','CONSUMER',1,%s,%s::jsonb)",
                (record_content_sha(payload),json.dumps(payload)))
        read_context=store.read_claim_context
        def complete_context(*args,**kwargs):
            rows=read_context(*args,**kwargs)
            rows['canonical_viewpoints','CONSUMER']=store.get_record_state('canonical_viewpoints','CONSUMER')
            return rows
        monkeypatch.setattr(store,'read_claim_context',complete_context)
    old,root=operation_request(action,'Update',['C'])
    policy=cm.Policy.model_validate_json((cm.DEFAULT_POLICY.parent/'wang_claim_management_joint_update_v3.json').read_text())
    policy.models['opus']['backend']='anthropic_api'
    policy.api_spend={'root':str(root/'billing'),'limit_usd':100}
    policy.initial_candidate='existing';policy.budget.gpt_calls=1;policy.budget.opus_calls=2
    policy.budget.max_revisions=1;policy.budget.max_output_tokens=32000
    authorize(old,policy,root.with_suffix('.policy.json'))
    def fail(packet):
        stage=next(root.glob('CMR-*'));intent=body(read(stage/'opus.reservation.json'))
        adapter=stage_adapter(claim_management_model_adapter('opus',policy),packet,stage/'opus.raw.json',saved=intent)
        class Stream:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def __iter__(self):return iter([])
            def get_final_message(self):
                return SimpleNamespace(model_dump=lambda **kw:{'model':'claude-opus-5-5','stop_reason':'max_tokens',
                    'content':[{'type':'thinking','thinking':'','signature':'unchanged'}],
                    'usage':{'input_tokens':1000,'output_tokens':32000}})
        client=ClaimAnthropicAPI(spend_root=policy.api_spend['root'],
            client=SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw:Stream())))
        client.call_artifacts=stage/'opus.transport'
        return client.generate_json(adapter._prompt,json.dumps(packet),{'schema':{'type':'object'}})
    ports=SyntheticPorts(store,root,opus=fail)
    assert execute_claim_management(store,old,root=root,ports=ports)['reason']=='model_failed'
    previous=store.get_cvp_group('CMA-'+cm.sha256_json(old.action_id))
    previous={k:v for k,v in previous.items() if k not in {'run_id','status','error'}}
    if drift:
        with store.connect() as conn:
            conn.execute("UPDATE wang_knowledge.objects SET revision=2 WHERE collection='canonical_viewpoints' AND object_id='CONSUMER'")
    if drift=='target':
        payload=store.get_record_state('claims','C')['payload'];payload['statement']='changed outside the task'
        store.apply_plan(store.plan_package({'package_id':'target-drift','claims':[payload]}))
    new,next_root=operation_request(action,'Update',['C']);new.predecessor_action_id=old.action_id
    new.permissions=old.permissions
    follow=policy.model_copy(deep=True);follow.budget.max_output_tokens=64000
    states=store.read_claim_context([('claims','C')])
    extension={'basis':'api_output_limit','predecessor_action_id':old.action_id,
        'prior_metadata_sha256':cm.sha256_json(previous),'budget':follow.budget.model_dump(mode='json'),
        'authorization_quote':'工程修复后大模型调动应该自动增加一次',
        'current_objects_sha256':cm.sha256_json({cm.source.key(*k):cm.model_state(v) for k,v in states.items()}),
        'related_action_metadata_sha256':{}}
    authorize(new,follow,next_root.with_suffix('.policy.json'),extension)
    return store,old,root,previous,new,next_root,follow,extension


@pytest.mark.parametrize('exhausted',[None,'consumer'],indirect=True)
def test_spent_output_limit_continues_once_without_repeating_completed_work(exhausted):
    store,old,root,previous,new,next_root,policy,extension=exhausted
    before=store.get_record_state('claims','C')
    proof=cm.continuation_budget(previous,new,policy)
    assert proof['spent_generations']['opus']==1 and proof['remaining_generations']['opus']==1
    assert proof['api_output_limit_recovery'] and not proof['preserved_unknown_calls']
    ports=SyntheticPorts(store,next_root,opus=lambda packet:{'complete_review':True,'findings':[],'prior_findings':[]})
    result=execute_claim_management(store,new,root=next_root,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['opus'] and store.get_record_state('claims','C')==before
    assert store.claim_management_qualification('C')['ready']
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(old.action_id))
    assert parent['call_reservations']==previous['call_reservations']
    assert execute_claim_management(store,new,root=next_root,ports=ports)==result
    assert ports.calls==['opus']


@pytest.mark.parametrize('exhausted',['target'],indirect=True)
def test_current_transaction_binding_cannot_hide_real_claim_change(exhausted):
    store,_,_,_,new,next_root,_,_=exhausted
    ports=SyntheticPorts(store,next_root)
    with pytest.raises((ValueError,RuntimeError),match='drift|changed'):
        execute_claim_management(store,new,root=next_root,ports=ports)
    assert not ports.calls


@pytest.mark.parametrize('defect',['receipt','response_hash','partial_text','not_max_tokens','ledger','prompt','payload'])
def test_output_limit_is_not_unknown_partial_or_unbound_input(exhausted,defect):
    _,_,root,previous,_,_,_,_=exhausted
    key,slot=next((k,v) for k,v in previous['call_reservations'].items() if v['state']=='started')
    transport=root/slot['round_id']/'opus.transport'
    if defect=='receipt':(transport/'exit.json').unlink()
    elif defect=='response_hash':(transport/'response.json').write_text('{}')
    elif defect in {'partial_text','not_max_tokens'}:
        p=transport/'response.json';value=json.loads(p.read_text())
        if defect=='partial_text':value['content'].append({'type':'text','text':'{"findings":'})
        else:value['stop_reason']='end_turn'
        overwrite(p,value);receipt=json.loads((transport/'exit.json').read_text())
        receipt['files']['response.json']=digest_file(p);overwrite(transport/'exit.json',receipt)
    else:
        ledgerpath=Path(previous['policy']['api_spend']['root'])/'spend.json'
        ledger=json.loads(ledgerpath.read_text());entry=ledger['calls'][str(transport.resolve())]
        if defect=='ledger':entry['usage']={}
        else:
            p=transport/'request.json';wire=json.loads(p.read_text())
            if defect=='prompt':wire['system']='Different prompt'
            else:wire['messages'][0]['content']='{}'
            overwrite(p,wire)
            import hashlib
            sha=hashlib.sha256(json.dumps(wire,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            overwrite(transport/'invocation.json',{'backend':'anthropic_api','request_sha256':sha})
            entry['request_sha256']=sha
        overwrite(ledgerpath,ledger)
    with pytest.raises((ValueError,FileNotFoundError)):
        consumed_output_limit(previous,key,slot)


@pytest.mark.parametrize('defect',['no_authority','too_many_calls','too_much_output','change_effort','repeat'])
def test_resource_recovery_cannot_reset_policy_or_loop(exhausted,defect):
    _,_,_,previous,new,next_root,policy,extension=exhausted
    if defect=='no_authority':
        p=Path(new.authorization.path);auth=json.loads(p.read_text());auth.pop('owner_call_extension')
        overwrite(p,auth);new.authorization=cm.source.FileBinding.model_validate(binding(p))
    else:
        if defect=='too_many_calls':policy.budget.opus_calls=3
        elif defect=='too_much_output':policy.budget.max_output_tokens=128000
        elif defect=='change_effort':policy.models['opus']['effort']='medium'
        else:
            previous['continuation']={'api_output_limit_recovery':{'prior':'spent'}}
            extension['prior_metadata_sha256']=cm.sha256_json(previous)
        extension['budget']=policy.budget.model_dump(mode='json')
        authorize(new,policy,next_root.with_suffix('.policy.json'),extension)
    with pytest.raises(ValueError):cm.continuation_budget(previous,new,policy)
