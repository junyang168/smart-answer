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


@pytest.mark.parametrize('action',['needs_connection','needs_source'])
def test_only_connection_findings_resume_into_argument_stage(candidate,tmp_path,monkeypatch,action):
    package,_=candidate
    path=tmp_path/'candidate.json';path.write_text(json.dumps(package))
    class HandoffPorts(FlowPorts):
        def repair(self,request):
            if request['input']['task']=='claim_support':
                self.calls.append(('gpt',request))
                return {'items':[{'job_id':job['job_id'],'action':action,'reason':'核對缺項。'}
                                 for job in request['input']['jobs']]}
            return super().repair(request)
    original=flow.arguments.run
    def interrupted(*args,**kwargs):
        raise RuntimeError('interrupted before argument review')
    monkeypatch.setattr(flow.arguments,'run',interrupted)
    root=tmp_path/'handoff'
    ports=HandoffPorts(choices={'support':'partial'})
    if action=='needs_source':
        stopped=flow.run_candidate(path,root,ports)
        assert stopped['status']=='needs_attention' and stopped['pending'][0]['task']=='statements'
        return
    with pytest.raises(RuntimeError,match='interrupted before argument review'):
        flow.run_candidate(path,root,ports)
    progress=flow.read(root/'flash/claim-progress.json')
    assert progress['active']['task']=='arguments'
    statement_path=next((root/'flash/claims').glob('*/statements/result.json'))
    checkpoint=flow.read(statement_path)
    original_verification=flow.read(statement_path.with_name('verification.json'))
    assert checkpoint['argument_handoffs']==original_verification['pending']
    assert checkpoint['statement_verification_sha256']==flow.e.digest(original_verification)
    monkeypatch.setattr(flow.arguments,'run',original)
    monkeypatch.setattr(flow.statements,'run',lambda *a,**kw:pytest.fail('statement stage ran again'))
    resumed=flow.run_candidate(path,root,HandoffPorts(choices={'candidate':'none'}))
    assert resumed['status']=='completed'
    assert next(iter(resumed['stages']['claims'].values()))['arguments']['resolved_statement_handoffs']==original_verification['pending']


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


@pytest.mark.parametrize('changed',[False,True,'retired'])
def test_source_export_resolves_external_claim_identity_without_expanding_review(candidate,tmp_path,monkeypatch,changed):
    package,_=candidate;local=package['claims'][0]['claim_id']
    external={**deepcopy(package['claims'][0]),'claim_id':'EXTERNAL-REFERENCE',
              'occurrences':[{'transcript_id':'legacy-transcript','anchors':[]}]}
    package['claims'].append(external)
    for row in package['evidence_steps']:
        if row['evidence_step_id'] in external['evidence_step_ids']:
            row.setdefault('produced_claim_ids',[]).append('EXTERNAL-REFERENCE')
    path=tmp_path/'candidate.json';path.write_text(json.dumps(package));ports=FlowPorts()
    result=flow.run_candidate(path,tmp_path/'flow',ports)
    assert result['status']=='completed'
    assert set(result['external_reference_pins'])=={'claims/EXTERNAL-REFERENCE'}
    plan=flow.read(tmp_path/'flow/flash/grouping/shared-groups.json')
    assert plan['target_claim_ids']==[local]
    final=flow.read(result['package']['path'])
    assert 'EXTERNAL-REFERENCE' not in {p['claim_id'] for p in final['claims']}
    assert final['flash_application']['external_reference_pins']==result['external_reference_pins']
    # Embedded declarations never let an ordinary package bypass closure.
    with pytest.raises(flow.KnowledgePackageMergeError,match='unknown produced claims'):
        flow.validate_merged_package(final,native_relations=True)
    with workspace.open_candidate(package,tmp_path/'flow/workspace') as store:
        expected=normalize_package(package)['claims']['EXTERNAL-REFERENCE']
        assert store.get_record_state('claims','EXTERNAL-REFERENCE')['payload']==expected
        if changed=='retired':
            # Deliberate private-DB fault injection: a referenced node must
            # never be accepted merely because a historical pin exists.
            with store.connect() as conn:
                conn.execute("UPDATE wang_knowledge.objects SET retired_at=now() WHERE collection='claims' AND object_id='EXTERNAL-REFERENCE'")
            records,_=flow.snapshot(store,package['source_documents'][0]['source_id'])
            with pytest.raises(flow.KnowledgePackageMergeError,match='unknown produced claims'):
                flow.export(store,package['source_documents'][0]['source_id'],tmp_path/'retired-export',records,None,{},[],0,'candidate_workspace')
    replay=FlowPorts()
    if changed:
        if changed is True:
            pins=deepcopy(result['external_reference_pins']);pins['claims/EXTERNAL-REFERENCE']['revision']+=1
            monkeypatch.setattr(flow,'external_reference_pins',lambda *a:pins)
        with pytest.raises(ValueError,match='external references changed'):
            flow.run_candidate(path,tmp_path/'flow',replay)
    else:assert flow.run_candidate(path,tmp_path/'flow',replay)==result
    assert not replay.calls


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
    assert [len(batch) for batch in plan['groups'][0]['sequential_claim_batches']]==[6,5]
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


@pytest.mark.parametrize('referenced',[False,True])
def test_canonical_export_preserves_superseded_source_fragment_history(candidate,tmp_path,monkeypatch,referenced):
    package,_=candidate
    with workspace.open_candidate(package,tmp_path/'private') as store:
        sid=package['source_documents'][0]['source_id']
        records,sources=flow.snapshot(store,sid)
        historical={**deepcopy(package['source_fragments'][0]),'fragment_id':'OLD-FRAGMENT',
                    'source_sha256':'0'*64,'anchor_state':'replaced_by_precise_fragments'}
        records['source_fragments/OLD-FRAGMENT']={'payload':historical,'content_sha256':'historical',
                                                  'revision':1,'retired':False}
        if referenced:
            evidence=next(row for key,row in records.items() if key.startswith('evidence_steps/'))
            evidence['payload']['source_fragment_ids'].append('OLD-FRAGMENT')
        monkeypatch.setattr(flow,'snapshot',lambda *_:(records,sources))
        args=(store,sid,tmp_path/'export',records,None,{},[],0,'canonical_refresh')
        if referenced:
            with pytest.raises(flow.KnowledgePackageMergeError,match='fragment source generation'):
                flow.export(*args)
        else:
            result=flow.export(*args)
            output=flow.read(result['package']['path'])
            assert result['status']=='completed'
            assert output['flash_application']['historical_source_fragments']==[historical]
            assert historical not in output['source_fragments']


def test_completed_output_drift_is_rejected_without_new_calls(candidate,tmp_path):
    package,_=candidate;path=tmp_path/'candidate.json';path.write_text(json.dumps(package))
    result=flow.run_candidate(path,tmp_path/'flow',FlowPorts())
    __import__('pathlib').Path(result['package']['path']).write_text('{}')
    p=FlowPorts()
    with pytest.raises(ValueError,match='output package changed'):flow.run_candidate(path,tmp_path/'flow',p)
    assert not p.calls


@pytest.mark.parametrize('legacy',[False,True])
def test_pending_evidence_checkpoint_does_not_repeat_completed_source_layers(candidate,tmp_path,monkeypatch,legacy):
    package,_=candidate;path=tmp_path/'candidate.json';path.write_text(json.dumps(package))
    original=flow.eo.run
    def paused(*args,**kwargs):
        result=original(*args,**kwargs)
        return {**result,'status':'needs_attention','pending':[{'layer':'handoff','reason':'test checkpoint'}]}
    monkeypatch.setattr(flow.eo,'run',paused)
    first=flow.run_candidate(path,tmp_path/'flow',FlowPorts())
    assert first['status']=='needs_attention'
    if legacy:(tmp_path/'flow/flash/source-layer-input.json').unlink()
    monkeypatch.setattr(flow.eo,'run',original)
    monkeypatch.setattr(flow.source,'run',lambda *a,**k:pytest.fail('completed source verification repeated'))
    monkeypatch.setattr(flow.coverage,'run',lambda *a,**k:pytest.fail('completed source coverage repeated'))
    result=flow.run_candidate(path,tmp_path/'flow',FlowPorts())
    assert result['status']=='completed',result.get('pending')


@pytest.mark.parametrize('tamper_review',[False,True])
def test_pending_evidence_allows_index_progress_but_keeps_review_receipts_strict(candidate,tmp_path,monkeypatch,tamper_review):
    package,_=candidate;path=tmp_path/'candidate.json';path.write_text(json.dumps(package))
    original=flow.eo.run
    def pending(*args,**kwargs):
        value=original(*args,**kwargs)
        value={**value,'status':'needs_attention','affected_prior_group_ids':[value['groups'][0]['group_id']]}
        return flow.e.publish(args[3],value)
    monkeypatch.setattr(flow.eo,'run',pending)
    first=flow.run_candidate(path,tmp_path/'resume',FlowPorts())
    assert first['pending'][0]['layer']=='evidence'
    root=tmp_path/'resume/flash'
    old=first['stages']['evidence']
    # A lawful same-root subset continuation updates the progress index, with
    # no knowledge write and no replacement of any per-group judgment.
    flow.e.publish(root/'evidence',{**old,'status':'completed','affected_prior_group_ids':[]})
    frozen=root/'evidence/reports'/(flow.e.digest(old)+'.json')
    assert flow.read(frozen)==old
    if tamper_review:
        review=next((root/'evidence/groups').glob('*/*/result.json'));review.write_text('{}')
    monkeypatch.setattr(flow.eo,'run',original)
    monkeypatch.setattr(flow.source,'run',lambda *a,**k:pytest.fail('source repeated'))
    monkeypatch.setattr(flow.coverage,'run',lambda *a,**k:pytest.fail('coverage repeated'))
    if tamper_review:
        with pytest.raises(ValueError,match='stage evidence changed'):
            flow.run_candidate(path,tmp_path/'resume',FlowPorts())
    else:
        result=flow.run_candidate(path,tmp_path/'resume',FlowPorts())
        assert result['status']=='completed',result.get('pending')


@pytest.mark.parametrize('bad',[False,True])
def test_legacy_pending_index_uses_only_its_exact_embedded_original(tmp_path,bad):
    old={'status':'needs_attention','affected_prior_group_ids':['G']}
    index=tmp_path/'evidence/result.json';flow.e.save(index,{'status':'completed'})
    saved={'pending':[{'layer':'evidence'}],'stages':{'evidence':old},
        'evidence_refs':[{'path':str(index),'sha256':flow.e.digest(old)}]}
    if bad:saved['stages']['evidence']={'status':'different'}
    if bad:
        with pytest.raises(ValueError,match='stage evidence changed'):flow.recovery.verify_pending_refs(tmp_path,saved)
    else:
        flow.recovery.verify_pending_refs(tmp_path,saved)
        assert flow.read(tmp_path/'evidence/reports'/(flow.e.digest(old)+'.json'))==old


def test_material_only_source_gets_real_missing_claim_disposition(candidate,tmp_path):
    package,_=candidate;package['claims']=[]
    for p in package['evidence_steps']:p['produced_claim_ids']=[]
    for p in package['questions']:p['answer_claim_ids']=[]
    path=tmp_path/'candidate.json';path.write_text(json.dumps(package));ports=FlowPorts()
    result=flow.run_candidate(path,tmp_path/'flow',ports)
    assert result['status']=='completed',result.get('pending')
    assert any(role=='gpt' and r['input']['task']=='missing_group_claims' for role,r in ports.calls)
    assert flow.read(result['package']['path'])['claims']==[]


@pytest.mark.parametrize('fault',[None,'database','receipt','legacy'])
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
    elif fault=='legacy':
        lower=flow.read(root/'claim-layer-input.json')
        for key in ('checkpoint_sha256','before_sha256'):lower.pop(key,None)
        (root/'claim-layer-input.json').write_text(json.dumps(lower))
        progress=flow.read(root/'claim-progress.json');progress['lower_sha256']=flow.e.digest(lower)
        (root/'claim-progress.json').write_text(json.dumps(progress))
        (root/'source-layer-input.json').unlink()
    monkeypatch.setattr(flow.relations,'run',original)
    for module in (flow.source,flow.coverage,flow.eo,flow.statements,flow.arguments):
        monkeypatch.setattr(module,'run',lambda *a,**k:pytest.fail('completed layer/stage repeated'))
    if fault in {'database','receipt'}:
        with pytest.raises(ValueError,match='checkpoint input changed|stage evidence changed|outside write'):
            flow.run_candidate(path,tmp_path/'resume',FlowPorts())
    else:
        result=flow.run_candidate(path,tmp_path/'resume',FlowPorts())
        assert result['status']=='completed'


def test_source_handoff_batches_keep_group_boundaries_and_existing_singletons(tmp_path):
    def finding(n):return {'object_key':f'evidence_steps/E{n}','layer':'source','reason':'缺原文。'}
    first=[finding(n) for n in range(6)]
    second=[finding(n) for n in range(6,8)]
    result={'groups':[{'group_id':'G1','pending':first},{'group_id':'G2','pending':second}]}
    batches=list(flow.source_handoff_batches(result,tmp_path))
    assert [len(items) for items,path in batches]==[4,2,2]
    assert [p['object_key'] for items,path in batches for p in items]==[p['object_key'] for p in first+second]
    legacy=tmp_path/flow.e.digest(first[0]['object_key']);legacy.mkdir()
    (legacy/'intent.json').write_text('{}')
    batches=list(flow.source_handoff_batches(result,tmp_path))
    assert batches[0]==([first[0]],legacy)
    assert [len(items) for items,path in batches]==[1,4,1,2]
    assert list(flow.source_handoff_batches({'groups':[{'pending':list(reversed(first))},{'pending':second}]},tmp_path))==batches


def test_unresolved_prepared_source_is_not_sent_for_another_gpt(candidate,tmp_path,monkeypatch):
    package,_=candidate;path=tmp_path/'candidate.json';path.write_text(json.dumps(package))
    monkeypatch.setattr(flow.eo,'run',lambda *a,**k:{'status':'needs_attention','groups':[{'pending':[
        {'layer':'source','object_key':'evidence_steps/E','reason':'候選仍不足。','source_completion_attempted':True}]}]})
    monkeypatch.setattr(flow.source,'repair_bindings',lambda *a,**k:pytest.fail('same source gap sent to GPT twice'))
    result=flow.run_candidate(path,tmp_path/'flow',FlowPorts())
    assert result['status']=='needs_attention'
    assert result['pending'][0]['findings'][0]['source_completion_attempted']

@pytest.mark.parametrize('status,applied,recover',[
    ('verified',False,True),('applied',True,False),('needs_attention',False,False)])
def test_saved_source_preview_finishes_before_lower_review(candidate,tmp_path,monkeypatch,status,applied,recover):
    package,_=candidate;path=tmp_path/'candidate.json';path.write_text(json.dumps(package))
    handoff=tmp_path/'flow/flash/source-handoffs/prepared'
    flow.e.save(handoff/'intent.json',{'findings':[{'object_key':'evidence_steps/E','layer':'source'}],'models':{}})
    flow.e.save(handoff/'result.json',{'status':status,'applied':applied,'pending':[] if status!='needs_attention' else ['unresolved']})
    calls=[]
    def resume(store,sid,findings,root,ports,*,apply):
        assert apply and root==handoff
        calls.append('saved-preview')
        return {'status':'applied','applied':True,'pending':[]}
    monkeypatch.setattr(flow.source,'repair_bindings',resume)
    result=flow.run_candidate(path,tmp_path/'flow',FlowPorts())
    assert result['status']=='completed'
    assert calls==(['saved-preview'] if recover else [])


def test_confirmed_citation_is_attached_before_evidence_verification(candidate,tmp_path,monkeypatch):
    package,_=candidate;target=package['evidence_steps'][0];target['scripture_refs']=[]
    path=tmp_path/'candidate.json';path.write_text(json.dumps(package));original=flow.coverage.run
    def coverage_result(*a,**kw):
        result=deepcopy(original(*a,**kw))
        fragment=package['source_fragments'][0]
        result['findings']=[{'job_id':'test','findings':[{'verdict':'missing_scripture_reference',
            'reference':'馬太福音16:18','quote':fragment['verbatim_excerpt'],'paragraph_key':fragment['paragraph_key'],
            'existing_record_keys':['evidence_steps/'+target['evidence_step_id']]}]}]
        return result
    monkeypatch.setattr(flow.coverage,'run',coverage_result)
    lower=flow.eo.run
    def check_before_lower(store,*a,**kw):
        assert store.get_record_state('evidence_steps',target['evidence_step_id'])['payload']['scripture_refs']==['馬太福音 16:18']
        return lower(store,*a,**kw)
    monkeypatch.setattr(flow.eo,'run',check_before_lower)
    result=flow.run_candidate(path,tmp_path/'flow',FlowPorts())
    assert result['status']=='completed',result.get('pending')
    assert result['stages']['scripture_bindings']['applied']


@pytest.mark.parametrize('boundary',['source','evidence','claims'])
def test_exception_after_completed_layer_resumes_without_repeating_it(candidate,tmp_path,monkeypatch,boundary):
    package,_=candidate;path=tmp_path/'candidate.json';path.write_text(json.dumps(package))
    root=tmp_path/'resume'
    # Fail at the next layer's entry, before it can publish a flow result.
    owner,name={'source':(flow.eo,'run'),'evidence':(flow,'claim_tasks'),'claims':(flow,'export')}[boundary]
    original=getattr(owner,name)
    def crash(*a,**k):raise RuntimeError('injected next-layer failure')
    monkeypatch.setattr(owner,name,crash)
    with pytest.raises(RuntimeError,match='injected next-layer failure'):
        flow.run_candidate(path,root,FlowPorts())
    assert not (root/'flash/result.json').exists()
    assert (root/'flash/source-layer-input.json').exists()
    if boundary!='source':assert (root/'flash/claim-layer-input.json').exists()
    if boundary=='claims':assert flow.read(root/'flash/claim-progress.json')['active'] is None
    monkeypatch.setattr(owner,name,original)
    completed=[flow.source,flow.coverage]
    if boundary!='source':completed.append(flow.eo)
    if boundary=='claims':completed.extend([flow.statements,flow.arguments,flow.relations,flow.missing])
    for module in completed:
        monkeypatch.setattr(module,'run',lambda *a,**k:pytest.fail('completed layer/task repeated'))
    result=flow.run_candidate(path,root,FlowPorts())
    assert result['status']=='completed',result.get('pending')


@pytest.mark.parametrize('fault',['checkpoint','original_input','receipt','source','database','orphan_cursor','model','scope'])
def test_source_checkpoint_refuses_changed_inputs_before_new_calls(candidate,tmp_path,monkeypatch,fault):
    package,source_path=candidate;path=tmp_path/'candidate.json';path.write_text(json.dumps(package))
    root=tmp_path/'resume';original=flow.eo.run
    def crash(*a,**k):raise RuntimeError('lower layer interrupted')
    monkeypatch.setattr(flow.eo,'run',crash)
    with pytest.raises(RuntimeError):flow.run_candidate(path,root,FlowPorts())
    checkpoint=root/'flash/source-layer-input.json';saved=flow.read(checkpoint)
    if fault=='checkpoint':
        saved['stages']['source']['status']='changed';checkpoint.write_text(json.dumps(saved))
    elif fault=='original_input':(root/'flash/before.json').write_text('{}')
    elif fault=='receipt':__import__('pathlib').Path(saved['refs'][0]['path']).write_text('{}')
    elif fault=='source':source_path.write_text(source_path.read_text()+' ')
    elif fault=='orphan_cursor':(root/'flash/claim-progress.json').write_text('{}')
    elif fault=='database':
        with workspace.open_candidate(package,root/'workspace') as store:
            cid=package['claims'][0]['claim_id'];p=store.get_record_state('claims',cid)['payload'];p['statement']='外部改動。'
            store.apply_plan(store.plan_package({'package_id':'outside-change','claims':[p]}))
    monkeypatch.setattr(flow.eo,'run',original)
    ports=FlowPorts();options={}
    if fault=='model':ports.identity={**ports.identity,'gpt':{'backend':'changed','model':'changed'}}
    if fault=='scope':options['plan']=flow.read(root/'flash/shared-groups.json')
    with pytest.raises((ValueError,PostgresKnowledgeStoreError)):
        flow.run_candidate(path,root,ports,**options)
    assert not ports.calls


def test_source_checkpoint_accepts_only_receipted_partial_lower_commits(candidate,tmp_path,monkeypatch):
    package,_=candidate;path=tmp_path/'candidate.json';path.write_text(json.dumps(package))
    root=tmp_path/'resume';original=flow.eo.run
    def committed_then_crashed(store,sid,plan,folder,*a,**kw):
        # Exercise the real SQL commit and readback path, then lose the caller's
        # result. This write is private test data, not a production correction.
        records,sources=flow.snapshot(store,sid)
        key=next(k for k in records if k.startswith('evidence_steps/'))
        payload=deepcopy(records[key]['payload']);payload['statement']+='（隔離測試修訂）'
        work=folder/'interrupted-group'
        flow.e.save(work/'before.json',{'records':records,'sources':sources})
        flow.e.save(work/'verification.json',{'version':'test_partial_evidence','status':'verified',
            'updates':{key:payload},'new_fragments':{},'pending':[]})
        assert flow.e.commit_verified(store,work)['applied']
        raise RuntimeError('SQL committed before caller failed')
    monkeypatch.setattr(flow.eo,'run',committed_then_crashed)
    with pytest.raises(RuntimeError,match='SQL committed'):
        flow.run_candidate(path,root,FlowPorts())
    monkeypatch.setattr(flow.eo,'run',original)
    for module in (flow.source,flow.coverage):
        monkeypatch.setattr(module,'run',lambda *a,**k:pytest.fail('source repeated after owned SQL write'))
    result=flow.run_candidate(path,root,FlowPorts())
    assert result['status']=='completed',result.get('pending')
    assert len(result['changes']['updated'])==1


def test_checkpoint_must_be_saved_before_entering_next_layer(candidate,tmp_path,monkeypatch):
    package,_=candidate;path=tmp_path/'candidate.json';path.write_text(json.dumps(package))
    from pathlib import Path
    replace=Path.replace
    def interrupted_replace(self,target):
        if Path(target).name=='source-layer-input.json':raise OSError('checkpoint disk failure')
        return replace(self,target)
    monkeypatch.setattr(Path,'replace',interrupted_replace)
    monkeypatch.setattr(flow.eo,'run',lambda *a,**k:pytest.fail('entered lower layer without durable source checkpoint'))
    with pytest.raises(OSError,match='checkpoint disk failure'):
        flow.run_candidate(path,tmp_path/'resume',FlowPorts())
    assert not (tmp_path/'resume/flash/source-layer-input.json').exists()


def test_subscription_flash_routes_models_by_layer(candidate, tmp_path, monkeypatch):
    from backend.pipeline import claim_refresh, codex_subscription_client

    class Client:
        def __init__(self, **kwargs):
            self.model = kwargs['model']

    class Ports(FlowPorts):
        def __init__(self, client, root, *, capacity):
            super().__init__()
            self.identity = {**self.identity, 'gpt': {'model': client.model}}
            self.capacity = capacity
            self.gpt = client

    monkeypatch.setattr(codex_subscription_client, 'CodexSubscriptionClient', Client)
    monkeypatch.setattr(claim_refresh, 'Models', Ports)
    capacity = object()
    normal, evidence = flow.subscription_ports(tmp_path, capacity)
    assert normal.capacity is evidence.capacity is capacity
    package, _ = candidate
    path = tmp_path / 'candidate.json'
    path.write_text(json.dumps(package))

    observed = {}
    for module, name in [(flow.coverage, 'source'), (flow.eo, 'evidence'), (flow.statements, 'claim')]:
        original = module.run

        def record(*args, _name=name, _original=original, **kwargs):
            ports = args[4] if _name == 'evidence' else args[-1]
            observed[_name] = ports.identity['gpt']['model']
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, 'run', record)

    root = tmp_path / 'flow'
    result = flow.run_candidate(path, root, normal, evidence_ports=evidence)
    assert result['status'] == 'completed'
    assert observed == {'source': 'gpt-6.1-sol', 'evidence': 'gpt-6-sol', 'claim': 'gpt-6.1-sol'}
    normal.calls.clear()
    evidence.calls.clear()
    observed.clear()
    assert flow.run_candidate(path, root, normal, evidence_ports=evidence) == result
    assert not observed and not normal.calls and not evidence.calls


def test_legacy_evidence_keeps_saved_identity_and_routes_new_calls(tmp_path):
    from types import SimpleNamespace

    normal = SimpleNamespace(identity={'gpt': {'model': 'gpt-6.1-sol'}, 'jev': {}})
    evidence = SimpleNamespace(identity={'gpt': {'model': 'gpt-6-sol'}, 'jev': {}})
    root = tmp_path / 'evidence'
    root.mkdir()
    (root / 'intent.json').write_text(json.dumps({'models': normal.identity}))
    assert flow.bind_evidence_ports(tmp_path, normal, evidence) is evidence
    assert evidence.identity == normal.identity
    assert evidence.new_gpt_identity == {'model': 'gpt-6-sol'}
    assert flow.bind_evidence_ports(tmp_path, normal, evidence).new_gpt_identity == {'model': 'gpt-6-sol'}


@pytest.mark.parametrize(('result', 'expected'), [
    ({'status': 'completed', 'pending': []}, 0),
    ({'status': 'needs_attention', 'pending': [
        {'layer': 'claims', 'group_id': 'FG008', 'task': 'arguments',
         'findings': [{'object_key': 'claims/P06-CL009'}]},
    ]}, 2),
    ({'status': 'needs_attention', 'pending': []}, 2),
])
def test_flash_cli_reports_lecture_completion_separately_from_process_exit(
        tmp_path, monkeypatch, capsys, result, expected):
    import sys
    from backend.pipeline import claim_model_capacity

    capacity = tmp_path / 'capacity.json'
    capacity.write_text('{}')
    monkeypatch.setattr(claim_model_capacity, 'ClaimModelCapacity', lambda config: object())
    monkeypatch.setattr(flow, 'subscription_ports', lambda *args: (object(), object()))
    monkeypatch.setattr(flow, 'run_candidate', lambda *args, **kwargs: result)
    monkeypatch.setattr(sys, 'argv', ['claim_flash_flow', '--root', str(tmp_path),
                                    '--capacity-file', str(capacity), '--candidate', 'candidate.json'])
    assert flow.main() == expected
    assert json.loads(capsys.readouterr().out) == result
