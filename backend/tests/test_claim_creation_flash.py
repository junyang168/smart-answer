from copy import deepcopy
import json
from pathlib import Path

import pytest
from backend.pipeline import claim_creation_flash as creation
from backend.pipeline import detailed_knowledge_extraction as d
from backend.pipeline import detailed_knowledge_extraction_runner as x
from backend.pipeline.extraction_sections import Section
from backend.tests.test_detailed_knowledge_extraction import _transcript, _response


class Ports:
    identity={'gpt':{'backend':'test','model':'test','effort':'medium'}}
    def __init__(self,source, mutate=None):self.source=source;self.calls=[];self.mutate=mutate
    def repair(self,request):
        task=request['input']['task'];self.calls.append(task);r=_response()
        if task=='creation_claims':
            c=r['claims'][0];c={k:v for k,v in c.items() if k not in ('claim_id','evidence_step_ids','opposed_position_ids','review_status')}
            out={'candidates':[{**c,'related_ranges':[{'first':'S0001','last':'S0003'}],
                'conclusion_anchor':r['evidence_steps'][0]['anchors'][0],
                'support_hint':'但以理書人子領受永遠權柄。','existing_claim_id':None,'answers_question':'人子是誰？'}]}
        elif task=='creation_materials':
            out={k:r[k] for k in ('questions','positions','observations','evidence_steps')}
            for o in out['observations']:o.pop('argument_role')
            for q in out['questions']:q.pop('answer_claim_ids')
            for e in out['evidence_steps']:e.pop('produced_claim_ids')
            sentences=x.section_sentences(self.source,Section(1,0,3,'all'))
            out['sentence_audit']=[{'sentence_id':v.sentence_id,'status':'extracted','covered_by':[],
                'reason_code':None,'reason':'逐字來源已保存。'} for v in sentences]
            for a in out['sentence_audit']:
                pk=a['sentence_id'].split('#')[0]
                a['covered_by']=[n[creation.IDS[col]] for col in creation.COLLECTIONS[:4] for n in out[col] if any(z['segment_index']==pk for z in n['anchors'])]
        elif task=='creation_connections':
            out={'connections':[
                {'from_id':'P01-E001','to_id':'CL0001','relation_type':'supports','reason':'教授的否定判斷。'},
                {'from_id':'CL0001','to_id':'P01-Q001','relation_type':'answers','reason':'回答身份問題。'},
                {'from_id':'CL0001','to_id':'P01-POS001','relation_type':'refutes','reason':'反對只強調人性。'}]}
        elif task=='creation_grouping':
            out={'groups':groups(),'reason':'完整論證。'}
        else:raise AssertionError(task)
        if task=='creation_connections':out['observation_roles']=[{'observation_id':oid,'argument_role':'background','reason':'測試背景觀察。'} for oid in request['input']['local_observation_ids']]
        return self.mutate(task,out) if self.mutate else out


def groups():return [{'title':'人子身份','topic_key':'人子身份','paragraph_ranges':[{'first':'S0001','last':'S0003'}]}]


def compact_response(result):
    result=deepcopy(result);coverage={};excluded=[]
    for row in result.pop('sentence_audit'):
        if row['status']=='extracted':
            for rid in row['covered_by']:coverage.setdefault(rid,[]).append(row['sentence_id'])
        else:excluded.append({k:row[k] for k in ['sentence_id','reason_code','reason']})
    result.update(coverage_by_record=[{'record_id':rid,'sentence_ids':ids} for rid,ids in coverage.items()],excluded_sentences=excluded)
    return result


class CompactPorts(Ports):
    def repair(self,request):
        result=super().repair(request)
        if request['input']['task']=='creation_materials':
            assert 'sentence_audit' not in request['schema']['schema']['properties']
            assert '不從引文自動推定' in request['system']
            return compact_response(result)
        return result


class PackagePorts(Ports):
    def __init__(self,source,group_mutation=None):
        super().__init__(source);self.group_mutation=group_mutation
    def repair(self,request):
        if request['input']['task']!='creation_group_package':return super().repair(request)
        assert set(request['input']['claim_candidates'])=={'CL0001'}
        assert '不要生成Claim、論證連接或判argument_role' not in request['system']
        material_request=deepcopy(request);material_request['input']['task']='creation_materials'
        result=super().repair(material_request);self.calls[-1]='creation_group_package'
        result=compact_response(result)
        for row in result['observations']:row['argument_role']='background'
        result['connections']=[{'from_id':'E001','to_id':'CL0001','relation_type':'supports','reason':'原文支持候選。'},
            {'from_id':'CL0001','to_id':'Q001','relation_type':'answers','reason':'直接回答問題。'},
            {'from_id':'CL0001','to_id':'POS001','relation_type':'refutes','reason':'教授否定反方。'}]
        return self.group_mutation(result) if self.group_mutation else result


class SinglePassPorts(PackagePorts):
    def repair(self,request):
        assert request['input']['task']=='creation_single_pass'
        assert 'claim_candidates' not in request['input']
        assert 'Claim候選只讀' not in request['system']
        claim_request=deepcopy(request);claim_request['input']['task']='creation_claims'
        candidates=Ports.repair(self,claim_request)['candidates']
        material_request=deepcopy(request);material_request['input'].update(task='creation_group_package',claim_candidates={'CL0001':candidates[0]})
        result=PackagePorts.repair(self,material_request)
        self.calls[:]=['creation_single_pass']
        for edge in result['connections']:
            for endpoint in ('from_id','to_id'):
                if edge[endpoint]=='CL0001':edge[endpoint]='LOCAL-C'
        result['candidates']=[{**candidates[0],'claim_id':'LOCAL-C'}]
        return result


def test_single_pass_builds_claim_materials_and_full_graph_with_one_saved_call(tmp_path):
    source=_transcript();path=tmp_path/'source.json';path.write_text(json.dumps(source));root=tmp_path/'run';ports=SinglePassPorts(source)
    result=creation.run('T',path,root,ports,groups=groups(),single_pass=True)
    assert result['status']=='creation_complete' and ports.calls==['creation_single_pass']
    package=json.loads((root/'candidate.json').read_text());cid=package['claims'][0]['claim_id']
    assert package['evidence_steps'][0]['produced_claim_ids']==[cid]
    assert package['questions'][0]['answer_claim_ids']==[cid]
    raw=json.loads(next(root.glob('G001/single-pass/gpt/*/response.json')).read_text())
    assert raw['candidates'][0]['claim_id']=='LOCAL-C'
    assert raw['connections'][0]['to_id']=='LOCAL-C'
    assert not (root/'G001/claims/gpt').exists() and not (root/'G001/materials/batch-001/gpt').exists()
    assert json.loads((root/'G001/single-pass-projection.json').read_text())['claim_alias_mapping']=={'LOCAL-C':'CL0001'}
    ports.calls=[];assert creation.run('T',path,root,ports,groups=groups(),single_pass=True)==result
    assert ports.calls==[]


@pytest.mark.parametrize('fault',['alias_collision','duplicate_alias','unknown_endpoint','unsupported_claim'])
def test_single_pass_rejects_incomplete_or_ambiguous_graph(tmp_path,fault):
    source=_transcript();path=tmp_path/'source.json';path.write_text(json.dumps(source));root=tmp_path/'run'
    class Bad(SinglePassPorts):
        def repair(self,request):
            result=super().repair(request)
            if fault=='alias_collision':result['candidates'][0]['claim_id']='E001'
            if fault=='duplicate_alias':result['candidates'].append(deepcopy(result['candidates'][0]))
            if fault=='unknown_endpoint':result['connections'][0]['to_id']='UNKNOWN'
            if fault=='unsupported_claim':result['connections']=result['connections'][1:]
            return result
    with pytest.raises(ValueError):creation.run('T',path,root,Bad(source),groups=groups(),single_pass=True)
    assert not (root/'creation-result.json').exists()


def test_group_package_builds_materials_roles_and_connections_in_one_model_task(tmp_path):
    source=_transcript();path=tmp_path/'source.json';path.write_text(json.dumps(source));root=tmp_path/'run'
    ports=PackagePorts(source);result=creation.run('T',path,root,ports,groups=groups(),grouped_package=True)
    assert result['status']=='creation_complete' and ports.calls==['creation_claims','creation_group_package']
    package=json.loads((root/'candidate.json').read_text());cid=package['claims'][0]['claim_id']
    assert package['claims'][0]['evidence_step_ids']==[package['evidence_steps'][0]['evidence_step_id']]
    assert package['evidence_steps'][0]['produced_claim_ids']==[cid]
    assert package['questions'][0]['answer_claim_ids']==[cid] and package['claims'][0]['opposed_position_ids']
    assert not (root/'G001/connections').exists()
    ports.calls=[];assert creation.run('T',path,root,ports,groups=groups(),grouped_package=True)==result
    assert ports.calls==[]


@pytest.mark.parametrize('fault',['unknown_endpoint','missing_support','unconnected_load_bearing'])
def test_incomplete_group_package_cannot_become_a_complete_candidate(tmp_path,fault):
    source=_transcript();path=tmp_path/'source.json';path.write_text(json.dumps(source));root=tmp_path/'run'
    def mutate(result):
        if fault=='unknown_endpoint':result['connections'][0]['from_id']='MISSING'
        if fault=='missing_support':result['connections']=result['connections'][1:]
        if fault=='unconnected_load_bearing':result['observations'][0]['argument_role']='load_bearing'
        return result
    with pytest.raises(ValueError):creation.run('T',path,root,PackagePorts(source,mutate),groups=groups(),grouped_package=True)
    assert not (root/'creation-result.json').exists()


def test_group_package_schema_keeps_native_role_and_single_edge_authority():
    schema=creation.schemas(grouped_package=True)['materials']['schema']['properties']
    assert 'connections' in schema and 'argument_role' in schema['observations']['items']['properties']
    assert 'sentence_audit' not in schema and 'observation_roles' not in schema
    assert 'produced_claim_ids' not in schema['evidence_steps']['items']['properties']
    assert 'answer_claim_ids' not in schema['questions']['items']['properties']


def test_compact_transport_compiles_full_legacy_ledger_and_replays_without_calls(tmp_path):
    source=_transcript();path=tmp_path/'source.json';path.write_text(json.dumps(source));root=tmp_path/'run'
    ports=CompactPorts(source)
    result=creation.run('T',path,root,ports,groups=groups(),compact_audit=True)
    assert result['status']=='creation_complete'
    response=json.loads((root/'extraction-response.json').read_text())
    sentences=x.section_sentences(source,Section(1,0,3,'all'))
    d.validate_sentence_audit(response,source,sentences)
    assert [r['sentence_id'] for r in response['sentence_audit']]==[v.sentence_id for v in sentences]
    raw=next(root.glob('G001/materials/batch-001/gpt/*/response.json'))
    assert 'sentence_audit' not in json.loads(raw.read_text())
    receipt=json.loads((root/'G001/materials/batch-001/audit-expansion.json').read_text())
    assert receipt['contract']==creation.COMPACT_AUDIT
    assert all(r['covered_by'] for r in response['sentence_audit'])
    ports.calls=[];assert creation.run('T',path,root,ports,groups=groups(),compact_audit=True)==result
    assert ports.calls==[]


def test_compact_audit_preserves_multiple_supports_exclusion_and_order():
    reason='not_exegesis'
    raw={'observations':[],'coverage_by_record':[{'record_id':'O','sentence_ids':['A','B']},
        {'record_id':'EXISTING','sentence_ids':['B']}],
        'excluded_sentences':[{'sentence_id':'C','reason_code':reason,'reason':'純轉場，不是實質內容。'}]}
    before=deepcopy(raw);out=creation.expand_material_audit(raw,['C','B','A'])
    assert raw==before and [r['sentence_id'] for r in out['sentence_audit']]==['C','B','A']
    assert out['sentence_audit'][1]['covered_by']==['O','EXISTING']
    assert out['sentence_audit'][0]=={'sentence_id':'C','status':'not_extracted','covered_by':[],
        'reason_code':reason,'reason':'純轉場，不是實質內容。'}


@pytest.mark.parametrize('defect',['missing','foreign','duplicate_record','duplicate_sentence','overlap','empty_reason','unknown_reason'])
def test_bad_compact_audit_is_rejected_without_inventing_a_decision(defect):
    row={'record_id':'O','sentence_ids':['A']};reason='not_exegesis'
    result={'observations':[{'anchors':[{'segment_index':'S0001','verbatim_excerpt':'原句。'}]}],
        'coverage_by_record':[row],'excluded_sentences':[{'sentence_id':'B','reason_code':reason,'reason':'口頭語。'}]}
    if defect=='missing':result['coverage_by_record']=[]
    if defect=='foreign':row['sentence_ids']=['OTHER']
    if defect=='duplicate_record':result['coverage_by_record'].append(deepcopy(row))
    if defect=='duplicate_sentence':row['sentence_ids']=['A','A']
    if defect=='overlap':result['excluded_sentences'][0]['sentence_id']='A'
    if defect=='empty_reason':result['excluded_sentences'][0]['reason']=' '
    if defect=='unknown_reason':result['excluded_sentences'][0]['reason_code']='invented'
    with pytest.raises(ValueError):creation.expand_material_audit(result,['A','B'])


def test_compact_unknown_record_still_hits_existing_reference_guard(tmp_path):
    source=_transcript();path=tmp_path/'source.json';path.write_text(json.dumps(source))
    class WrongRecord(CompactPorts):
        def repair(self,request):
            result=super().repair(request)
            if request['input']['task']=='creation_materials':result['coverage_by_record'][0]['record_id']='MISSING'
            return result
    with pytest.raises(ValueError,match='unknown material'):
        creation.run('T',path,tmp_path/'run',WrongRecord(source),groups=groups(),compact_audit=True)


def test_audit_mode_change_cannot_silently_upgrade_a_frozen_run(tmp_path):
    source=_transcript();path=tmp_path/'source.json';path.write_text(json.dumps(source));root=tmp_path/'run'
    creation.run('T',path,root,Ports(source),groups=groups());ports=CompactPorts(source)
    with pytest.raises(ValueError):creation.run('T',path,root,ports,groups=groups(),compact_audit=True)
    assert ports.calls==[]


def test_complete_candidate_then_zero_call_replay(tmp_path):
    source=_transcript();p=tmp_path/'source.json';p.write_text(json.dumps(source));root=tmp_path/'run'
    ports=Ports(source);result=creation.run('T',p,root,ports,groups=groups())
    assert ports.calls==['creation_claims','creation_materials','creation_connections']
    assert result['status']=='creation_complete' and not result['flash_started']
    package=json.loads((root/'candidate.json').read_text())
    assert len(package['claims'])==1 and len(package['evidence_steps'])==2
    assert package['questions'][0]['answer_claim_ids']==[package['claims'][0]['claim_id']]
    c=package['claims'][0]['claim_id']
    assert package['evidence_steps'][0]['produced_claim_ids']==[c]
    assert {f['media_time'] for f in package['source_fragments']}=={1.0,8.0,16.0}
    ports.calls=[];assert creation.run('T',p,root,ports,groups=groups())==result
    assert ports.calls==[]


def test_semantic_grouping_reuses_saved_result(tmp_path):
    source=_transcript();p=tmp_path/'source.json';p.write_text(json.dumps(source));ports=Ports(source)
    creation.run('T',p,tmp_path/'run',ports)
    assert ports.calls[0]=='creation_grouping'


@pytest.mark.parametrize('defect',['timing','quote','range','connection','fields'])
def test_bad_partial_never_becomes_complete_candidate(tmp_path,defect):
    source=_transcript();p=tmp_path/'source.json';p.write_text(json.dumps(source));root=tmp_path/'run'
    def mutate(task,out):
        if task=='creation_claims':
            if defect=='timing':out['candidates'][0]['conclusion_anchor']['start_time']=99
            if defect=='range':out['candidates'][0]['related_ranges'][0]['last']='S9999'
            if defect=='fields':out['claims']=[]
        if defect=='quote' and task=='creation_materials':out['evidence_steps'][0]['anchors'][0]['verbatim_excerpt']='不存在'
        if defect=='connection' and task=='creation_connections':out['connections'][0]['to_id']='UNKNOWN'
        return out
    with pytest.raises((ValueError,d.DetailedExtractionValidationError)):
        creation.run('T',p,root,Ports(source,mutate),groups=groups())
    assert not (root/'creation-result.json').exists() and not (root/'candidate.json').exists()


def test_incomplete_grouping_fails_before_calls(tmp_path):
    source=_transcript();p=tmp_path/'source.json';p.write_text(json.dumps(source));ports=Ports(source)
    g=groups();g[0]['paragraph_ranges'][0]['last']='S0002'
    with pytest.raises(ValueError,match='cover every'):creation.run('T',p,tmp_path/'run',ports,groups=g)
    assert not ports.calls


def test_source_drift_blocks_completed_output(tmp_path):
    source=_transcript();p=tmp_path/'source.json';p.write_text(json.dumps(source))
    def mutate(task,out):p.write_text('{}');return out
    with pytest.raises(ValueError,match='source changed'):
        creation.run('T',p,tmp_path/'run',Ports(source,mutate),groups=groups())
    assert not (tmp_path/'run'/'candidate.json').exists()


def test_two_groups_reuse_claim_and_keep_global_sentence_coordinates(tmp_path):
    source=_transcript();p=tmp_path/'source.json';p.write_text(json.dumps(source))
    plans=[{'title':'判斷','topic_key':'人子','paragraph_ranges':[{'first':'S0001','last':'S0001'}]},
           {'title':'經文及問題','topic_key':'人子','paragraph_ranges':[{'first':'S0002','last':'S0003'}]}]
    class Multi(Ports):
        def repair(self,req):
            out=super().repair(req);task=req['input']['task'];gid=req['input'].get('group_id')
            if task=='creation_claims':
                if gid=='G002':out['candidates'][0]['existing_claim_id']='CL0001'
                else:out['candidates'][0]['related_ranges']=[{'first':'S0001','last':'S0002'}]
            if task=='creation_materials':
                owned=set(req['input']['owned_paragraphs'])
                for col in creation.COLLECTIONS[:4]:out[col]=[n for n in out[col] if n['anchors'][0]['segment_index'] in owned]
                out['sentence_audit']=[a for a in out['sentence_audit'] if a['sentence_id'].split('#')[0] in owned]
            if task=='creation_connections':
                out['connections']=(
                    [{'from_id':'P01-E001','to_id':'CL0001','relation_type':'supports','reason':'實際否定。'},
                     {'from_id':'CL0001','to_id':'P01-POS001','relation_type':'refutes','reason':'實際反方。'}]
                    if gid=='G001' else [{'from_id':'CL0001','to_id':'P02-Q001','relation_type':'answers','reason':'直接回答。'}])
            return out
    result=creation.run('T',p,tmp_path/'run',Multi(source),groups=plans)
    package=json.loads((tmp_path/'run'/'candidate.json').read_text())
    assert len(package['claims'])==1
    assert result['group_claim_ids']=={'G001':['CL0001'],'G002':['CL0001']}
    response=json.loads((tmp_path/'run'/'extraction-response.json').read_text())
    assert len(response['sentence_audit'])==4
    assert response['sentence_audit'][2]['sentence_id']=='S0002#003'
    assert package['questions'][0]['answer_claim_ids']==[package['claims'][0]['claim_id']]


def test_unknown_reply_is_not_resent(tmp_path):
    source=_transcript();p=tmp_path/'source.json';p.write_text(json.dumps(source))
    class Lost(Ports):
        def repair(self,request):self.calls.append(request);raise TimeoutError('unknown result')
    ports=Lost(source)
    with pytest.raises(TimeoutError):creation.run('T',p,tmp_path/'run',ports,groups=groups())
    retry=Lost(source)
    with pytest.raises(ValueError,match='no saved reply'):creation.run('T',p,tmp_path/'run',retry,groups=groups())
    assert not retry.calls


def test_payload_limit_precedes_model_call(tmp_path):
    source=_transcript();source['script'][0]['text']='長'*creation.f.GPT_BYTES
    p=tmp_path/'source.json';p.write_text(json.dumps(source));ports=Ports(source)
    with pytest.raises(ValueError,match='payload limit'):creation.run('T',p,tmp_path/'run',ports,groups=groups())
    assert not ports.calls


def test_punctuation_projection_preserves_raw_and_avoids_model_retry(tmp_path):
    source=_transcript();p=tmp_path/'source.json';p.write_text(json.dumps(source))
    def punct(task,out):
        if task=='creation_materials':out['evidence_steps'][0]['anchors'][0]['verbatim_excerpt']='我说不对！'
        return out
    ports=Ports(source,punct);root=tmp_path/'run'
    creation.run('T',p,root,ports,groups=groups())
    raw=list((root/'G001'/'materials').rglob('response.json'))[0]
    assert json.loads(raw.read_text())['evidence_steps'][0]['anchors'][0]['verbatim_excerpt']=='我说不对！'
    assert json.loads((root/'extraction-response.json').read_text())['evidence_steps'][0]['anchors'][0]['verbatim_excerpt']=='我说不对。'
    assert len(ports.calls)==3


def test_native_observation_support_and_leaf_answer_need_no_fabricated_evidence(tmp_path):
    source=_transcript();path=tmp_path/'source.json';path.write_text(json.dumps(source))
    class Native(Ports):
        def repair(self,req):
            out=super().repair(req)
            if req['input']['task']=='creation_connections':
                out['connections']=[
                    {'from_id':'P01-OBS001','to_id':'CL0001','relation_type':'supports','reason':'教授的經文觀察直接支持判斷。'},
                    {'from_id':'P01-E002','to_id':'P01-Q001','relation_type':'answers','reason':'材料直接回答問題，不另造Claim。'},
                    {'from_id':'CL0001','to_id':'P01-E001','relation_type':'contextualizes','reason':'主張交代這項反駁的背景。'}]
            return out
    root=tmp_path/'run';creation.run('T',path,root,Native(source),groups=groups())
    package=json.loads((root/'candidate.json').read_text())
    assert len(package['claims'])==1 and len(package['evidence_steps'])==2
    assert package['claims'][0]['evidence_step_ids']==[]
    assert len(package['knowledge_relations'])==3
    anchors=package['claims'][0]['occurrences'][0]['anchors']
    assert any(a.get('observation_id') for a in anchors)
    assert all(a['source_fragment_id'] in {f['fragment_id'] for f in package['source_fragments']} for a in anchors)
    from backend.api.canonical_repository.postgres_store import normalize_package
    normalize_package(package)


def test_legacy_validator_does_not_implicitly_allow_empty_claim_support():
    r=_response();r['claims'][0]['evidence_step_ids']=[];r['evidence_steps'][0]['produced_claim_ids']=[]
    with pytest.raises(d.DetailedExtractionValidationError,match='no evidence'):d.validate_response(r,_transcript())
    d.validate_response(r,_transcript(),additional_relations=[{'from_id':'OBS001','to_id':'CL001','relation_type':'supports','reason':'直接支持。'}])
    with pytest.raises(d.DetailedExtractionValidationError,match='unknown endpoint'):
        d.validate_response(r,_transcript(),additional_relations=[{'from_id':'ALIEN','to_id':'CL001','relation_type':'supports','reason':'未知。'}])


def test_native_package_validation_is_opt_in_and_still_rejects_missing_endpoints(tmp_path):
    from backend.pipeline.knowledge_package_merge import validate_merged_package, KnowledgePackageMergeError
    source=_transcript();p=tmp_path/'source.json';p.write_text(json.dumps(source));root=tmp_path/'run'
    creation.run('T',p,root,Ports(source),groups=groups())
    package=json.loads((root/'candidate.json').read_text())
    package['knowledge_relations'].append({'relation_id':'NATIVE','from_id':package['observations'][0]['observation_id'],
        'to_id':package['claims'][0]['claim_id'],'relation_type':'supports','reason':'經文支持。'})
    with pytest.raises(KnowledgePackageMergeError,match='unknown endpoints'):validate_merged_package(package)
    validate_merged_package(package,native_relations=True)
    package['knowledge_relations'][-1]['to_id']='MISSING'
    with pytest.raises(KnowledgePackageMergeError,match='unknown endpoints'):validate_merged_package(package,native_relations=True)


def test_material_task_does_not_decide_argument_role_and_connection_must_dispose_each(tmp_path):
    contracts=creation.schemas()
    assert 'argument_role' not in contracts['materials']['schema']['properties']['observations']['items']['properties']
    source=_transcript();p=tmp_path/'source.json';p.write_text(json.dumps(source))
    def omit(task,out):
        if task=='creation_connections':out['observation_roles']=[]
        return out
    with pytest.raises(ValueError,match='exactly once'):
        creation.run('T',p,tmp_path/'run',Ports(source,omit),groups=groups())
    assert not (tmp_path/'run'/'candidate.json').exists()


def test_load_bearing_native_chain_requires_actual_route_not_cycle_or_question_only():
    r=_response();r['observations'][0]['argument_role']='load_bearing'
    second=deepcopy(r['observations'][0]);second['observation_id']='OBS002';second['argument_role']='background'
    r['observations'].append(second)
    edges=[{'from_id':'OBS001','to_id':'OBS002','relation_type':'supports','reason':'第一步。'},
           {'from_id':'OBS002','to_id':'E001','relation_type':'supports','reason':'第二步。'}]
    d.validate_response(r,_transcript(),additional_relations=edges)
    edges[1]['to_id']='OBS001'
    with pytest.raises(d.DetailedExtractionValidationError,match='load_bearing'):d.validate_response(r,_transcript(),additional_relations=edges)


def test_material_can_bind_supporting_reference_without_generating_other_groups(tmp_path):
    source=_transcript();p=tmp_path/'source.json';p.write_text(json.dumps(source))
    plans=[{'title':'判斷','topic_key':'人子','paragraph_ranges':[{'first':'S0001','last':'S0001'}]},
           {'title':'論據','topic_key':'人子','paragraph_ranges':[{'first':'S0002','last':'S0003'}]}]
    class Shared(Ports):
        def repair(self,req):
            out=super().repair(req);task=req['input']['task'];gid=req['input'].get('group_id')
            if task=='creation_claims':
                if gid=='G002':out['candidates'][0]['existing_claim_id']='CL0001'
                else:out['candidates'][0]['related_ranges']=[{'first':'S0001','last':'S0002'}]
            if task=='creation_materials':
                owned=set(req['input']['owned_paragraphs'])
                for col in creation.COLLECTIONS[:4]:out[col]=[r for r in out[col] if r['anchors'][0]['segment_index'] in owned]
                out['sentence_audit']=[v for v in out['sentence_audit'] if v['sentence_id'].split('#')[0] in owned]
                if gid=='G001':
                    assert req['input']['reference_paragraphs']['S0002']==source['script'][1]['text']
                    out['evidence_steps'][0]['anchors'].append({'segment_index':'S0002','verbatim_excerpt':source['script'][1]['text'],'start_time':None,'end_time':None})
            if task=='creation_connections':
                out['connections']=([{'from_id':'P01-E001','to_id':'CL0001','relation_type':'supports','reason':'完整兩段支持。'}] if gid=='G001' else [])
            return out
    root=tmp_path/'run';creation.run('T',p,root,Shared(source),groups=plans)
    package=json.loads((root/'candidate.json').read_text())
    e=next(e for e in package['evidence_steps'] if e['produced_claim_ids'])
    fragments={f['fragment_id']:f for f in package['source_fragments']}
    assert {fragments[f]['paragraph_key'] for f in e['source_fragment_ids']}=={'S0001','S0002'}


def test_reference_material_cannot_be_silently_regenerated_without_owned_anchor(tmp_path):
    source=_transcript();p=tmp_path/'source.json';p.write_text(json.dumps(source))
    g=[{'title':'甲','topic_key':'甲','paragraph_ranges':[{'first':'S0001','last':'S0001'}]},
       {'title':'乙','topic_key':'乙','paragraph_ranges':[{'first':'S0002','last':'S0003'}]}]
    def out_of_scope(task,out):
        if task=='creation_claims':out['candidates'][0]['related_ranges']=[{'first':'S0001','last':'S0002'}]
        return out
    with pytest.raises(ValueError,match='owned primary'):
        creation.run('T',p,tmp_path/'run',Ports(source,out_of_scope),groups=g)


class StepsPorts(Ports):
    """Steps first, then one record type and statement per step; the program cuts the fragments."""
    def __init__(self,source,steps_mutation=None,records_mutation=None):
        super().__init__(source);self.steps_mutation=steps_mutation;self.records_mutation=records_mutation
    def repair(self,request):
        task=request['input']['task']
        if task=='creation_argument_steps':
            self.calls.append(task)
            assert set(request['input']['claims'])=={'CL0001'} and '先写论证，再分句子' in request['system']
            out={'steps':[
                {'step_id':'T1','line':'反方：人子只強調人性','sentence_ids':['S0001#001'],'supports':['CL0001']},
                {'step_id':'T2','line':'回答：不對','sentence_ids':['S0001#002'],'supports':['CL0001']},
                {'step_id':'T3','line':'經文事實：但以理書的人子領受永遠權柄','sentence_ids':['S0002#003'],'supports':['CL0001','T2']}],
                'claim_text':[],'other':[{'sentence_ids':['S0003#004'],'why':'聽眾提問，下段回答。'}],'digressions':[],'not_claims':[]}
            return self.steps_mutation(out) if self.steps_mutation else out
        if task=='creation_step_records':
            self.calls.append(task)
            assert '你不複製原文' in request['system'] and 'verbatim_excerpt' not in json.dumps(request['schema'])
            assert request['input']['argument_steps'][0]['text']=='有人说人子只强调人性。'
            out={'records':[
                {'step_id':'T1','kind':'position','statement':'人子只強調人性','observation_type':None,'step_type':None,'attribution':'有人','scripture_refs':[]},
                {'step_id':'T2','kind':'evidence','statement':'他說這看法不對','observation_type':None,'step_type':'answer','attribution':'','scripture_refs':[]},
                {'step_id':'T3','kind':'observation','statement':'但以理書的人子領受永遠權柄','observation_type':'scripture_text','step_type':None,'attribution':'','scripture_refs':['但7:13-14']}]}
            return self.records_mutation(out) if self.records_mutation else out
        return super().repair(request)


def run_steps(tmp_path,**kw):
    source=_transcript();path=tmp_path/'source.json';path.write_text(json.dumps(source));root=tmp_path/'run'
    ports=StepsPorts(source,**kw)
    return ports,root,(lambda:creation.run('T',path,root,ports,groups=groups(),grouped_package=True,steps_first=True))


def test_steps_first_cuts_fragments_and_links_from_the_steps(tmp_path):
    ports,root,go=run_steps(tmp_path);result=go()
    assert ports.calls==['creation_claims','creation_argument_steps','creation_step_records']
    package=json.loads((root/'candidate.json').read_text());claim=package['claims'][0]
    assert len(claim['evidence_step_ids'])==1 and claim['opposed_position_ids']
    assert package['observations'][0]['argument_role']=='load_bearing'
    text={f['fragment_id']:f for f in package['source_fragments']}
    assert all(a['source_fragment_id'] in text for a in claim['occurrences'][0]['anchors'])
    assert json.loads((root/'intent.json').read_text())['argument_steps_contract']==creation.STEPS_CONTRACT
    ports.calls=[];assert go()==result and ports.calls==[]


@pytest.mark.parametrize('fault',['sentence_twice','sentence_missing','two_comparisons','unknown_support'])
def test_bad_steps_stop_before_the_records_call(tmp_path,fault):
    def mutate(out):
        if fault=='sentence_twice':out['steps'][1]['sentence_ids'].append('S0001#001')
        if fault=='sentence_missing':out['other']=[]
        if fault=='two_comparisons':out['steps'][2]['line']='甲 ↔ 乙 → 一；丙 ↔ 丁 → 二'
        if fault=='unknown_support':out['steps'][0]['supports']=['CL9999']
        return out
    ports,root,go=run_steps(tmp_path,steps_mutation=mutate)
    with pytest.raises(ValueError):go()
    assert 'creation_step_records' not in ports.calls and not (root/'creation-result.json').exists()


def test_every_claim_needs_a_step_before_the_records_call(tmp_path):
    def mutate(out):
        for step in out['steps']:step['supports']=[s for s in step['supports'] if s!='CL0001']
        return out
    ports,root,go=run_steps(tmp_path,steps_mutation=mutate)
    with pytest.raises(ValueError,match='every Claim needs a step'):go()


@pytest.mark.parametrize('fault',['missing_record','untyped_observation','unattributed_position','only_refuted'])
def test_incomplete_records_never_become_a_candidate(tmp_path,fault):
    steps=None
    def records(out):
        if fault=='missing_record':out['records']=out['records'][:2]
        if fault=='untyped_observation':out['records'][2]['observation_type']=None
        if fault=='unattributed_position':  # a 反方 step may say 有人; any other step still needs its holder
            out['records'][2]['kind']='position';out['records'][2]['attribution']=' '
        if fault=='only_refuted':  # a Claim whose only step is the view it refutes has no Evidence
            for r in out['records'][1:]:r['kind']='position';r['attribution']='有人'
        return out
    ports,root,go=run_steps(tmp_path,records_mutation=records)
    with pytest.raises(ValueError):go()
    assert not (root/'creation-result.json').exists()


def test_repeated_words_get_a_fragment_that_names_one_place():
    source={'metadata':{'title':'t','status':'published'},'script':[
        {'index':1,'start_time':0.0,'end_time':1.0,'text':'怎麼處理？我們來看。所以很混亂。怎麼處理？'}]}
    sentences=x.section_sentences(source,Section(1,0,1,'all'));by={v.sentence_id:v for v in sentences}
    text={'S0001':source['script'][0]['text']}
    assert [a['verbatim_excerpt'] for a in creation.step_fragments(['S0001#004'],by,text)]==['所以很混亂。怎麼處理？']
    assert [a['verbatim_excerpt'] for a in creation.step_fragments(['S0001#001','S0001#002'],by,text)]==['怎麼處理？我們來看。']
    assert [a['verbatim_excerpt'] for a in creation.step_fragments(['S0001#002','S0001#004'],by,text)]==['我們來看。','所以很混亂。怎麼處理？']


def test_steps_first_requires_the_grouped_package(tmp_path):
    source=_transcript();path=tmp_path/'source.json';path.write_text(json.dumps(source))
    with pytest.raises(ValueError,match='grouped'):creation.run('T',path,tmp_path/'run',StepsPorts(source),groups=groups(),steps_first=True)


def test_a_sentence_spoken_twice_is_located_at_its_own_position():
    source={'metadata':{'title':'t','status':'published'},'script':[
        {'index':1,'start_time':0.0,'end_time':1.0,'text':'怎麼處理？我們來看。所以很混亂。怎麼處理？'}]}
    sentences=x.section_sentences(source,Section(1,0,1,'all'))
    assert [s.text for s in sentences]==['怎麼處理？','我們來看。','所以很混亂。','怎麼處理？']
    def response(excerpt):
        r={k:[] for k in ('questions','positions','observations','evidence_steps')}
        r['evidence_steps']=[{'evidence_step_id':'E1','anchors':[{'segment_index':'S0001','verbatim_excerpt':excerpt}]}]
        r['sentence_audit']=[{'sentence_id':s.sentence_id,'status':'extracted' if i==3 else 'not_extracted',
            'covered_by':['E1'] if i==3 else [],'reason_code':None if i==3 else 'structural_markup','reason':'測試。'}
            for i,s in enumerate(sentences)]
        return r
    d.validate_sentence_audit(response('所以很混亂。怎麼處理？'),source,sentences)
    with pytest.raises(Exception,match='S0001#004'):d.validate_sentence_audit(response('我們來看。'),source,sentences)


def test_his_own_words_of_a_claim_become_its_source_fragments(tmp_path):
    """2011 NYSC 二-1: his first answer to 「什麼是謙卑？」 went to claim_text and was
    then excluded as duplicate_of 「CL0001本身的話」, so it was kept nowhere and the
    slides had nothing to show while he gave it. claim_text is not Evidence, but
    it is where he says the Claim: it must be one of the Claim's SourceFragments."""
    def mutate(out):
        out['other']=[];out['claim_text']=[{'claim_id':'CL0001','sentence_ids':['S0003#004']}]
        return out
    ports,root,go=run_steps(tmp_path,steps_mutation=mutate);go()
    package=json.loads((root/'candidate.json').read_text());claim=package['claims'][0]
    fragments={f['fragment_id']:f for f in package['source_fragments']}
    cited=[fragments[a['source_fragment_id']]['verbatim_excerpt'] for o in claim['occurrences'] for a in o['anchors']]
    said=x.section_sentences(_transcript(),Section(1,0,3,'all'))
    assert next(v.text for v in said if v.sentence_id=='S0003#004') in ''.join(cited)


def test_duplicate_of_must_name_the_record_that_keeps_the_passage():
    def answer(target):
        return {'evidence_steps':[{'evidence_step_id':'ST05','anchors':[]}],
                'coverage_by_record':[{'record_id':'ST05','sentence_ids':['A']}],
                'excluded_sentences':[{'sentence_id':'B','reason_code':'duplicate_of','reason':'同一定義。',
                                       'duplicate_of_record_id':target}]}
    for target in ('CL0001',None,'ELSEWHERE'):
        with pytest.raises(ValueError,match='duplicate_of must name'):creation.expand_material_audit(answer(target),['A','B'])
    assert creation.expand_material_audit(answer('ST05'),['A','B'])['sentence_audit'][1]['reason_code']=='duplicate_of'
    assert creation.expand_material_audit(answer('CL0001'),['A','B'],{'CL0001'})['sentence_audit'][1]['status']=='not_extracted'


def test_a_digression_is_a_proposal_with_fragments_not_evidence():
    from backend.pipeline import claim_creation_flash as c
    steps = {'steps': [{'step_id': 'T1', 'line': '回答：證據一大堆', 'sentence_ids': ['S0001#003'], 'supports': ['CL0001']}],
             'claim_text': [], 'other': [],
             'digressions': [{'sentence_ids': ['S0001#001', 'S0001#002'], 'kind': 'unsupported_comment', 'why': '批評學者看經眼瞎，沒有給經文或推理'}]}
    assert c.check_steps(steps, ['S0001#001', 'S0001#002', 'S0001#003'], ['CL0001']) == {'S0001#003': 'T1'}
    bad = {**steps, 'digressions': [{**steps['digressions'][0], 'kind': 'story'}]}
    with pytest.raises(ValueError, match='題外話'):
        c.check_steps(bad, ['S0001#001', 'S0001#002', 'S0001#003'], ['CL0001'])
    from backend.pipeline.sentence_ledger_vocabulary import HUMAN_ONLY_REASONS
    assert 'digression' in HUMAN_ONLY_REASONS


def test_an_open_question_is_a_question_answered_by_its_claim_not_support():
    from backend.pipeline import claim_creation_flash as c
    steps = {'steps': [{'step_id': 'T1', 'line': '问题：还要不要遵守摩西的律法？', 'sentence_ids': ['S0001#001'], 'supports': ['CL0001']}],
             'claim_text': [], 'other': [], 'digressions': []}
    with pytest.raises(ValueError, match='every Claim needs a step'):
        c.check_steps(steps, ['S0001#001'], ['CL0001'])
    steps['steps'].append({'step_id': 'T2', 'line': '耶稣洁净了所有食物，因为……', 'sentence_ids': ['S0001#002'], 'supports': ['CL0001']})
    assert c.check_steps(steps, ['S0001#001', 'S0001#002'], ['CL0001'])


def test_a_sentence_carrying_scripture_must_be_in_an_argument_step():
    from types import SimpleNamespace as N
    from backend.pipeline import claim_creation_flash as c
    paragraphs = {'S0001': '還容易。」再看一節聖經，馬可七章十八到十九節。好。'}
    sents = [N(sentence_id='S0001#001', segment_index='S0001', text='還容易。」', char_start=0, char_end=5),
             N(sentence_id='S0001#002', segment_index='S0001', text='再看一節聖經，馬可七章十八到十九節。', char_start=5, char_end=23),
             N(sentence_id='S0001#003', segment_index='S0001', text='好。', char_start=23, char_end=25)]
    listing = {'citations': [{'paragraph_key': 'S0001', 'key': ['Mark', 7, 19], 'ref': 'Mark 7:19',
                              'quote': '」再看一節聖經，馬可七章十八到十九節。'}]}
    scripture = c.cited_sentences(listing, sents, paragraphs)
    assert scripture == {'S0001#002': ['Mark 7:19']}  # the closing 」 does not drag in the sentence before
    steps = {'steps': [{'step_id': 'T1', 'line': '可7:19 → 食物潔淨', 'sentence_ids': ['S0001#001', 'S0001#003'], 'supports': ['CL0001']}],
             'claim_text': [], 'other': [{'sentence_ids': ['S0001#002'], 'why': '過場'}], 'digressions': []}
    with pytest.raises(ValueError, match='scripture he cites'):
        c.check_steps(steps, ['S0001#001', 'S0001#002', 'S0001#003'], ['CL0001'], scripture)
    as_claim = {**steps, 'other': [], 'claim_text': [{'claim_id': 'CL0001', 'sentence_ids': ['S0001#002']}]}
    assert c.check_steps(as_claim, ['S0001#001', 'S0001#002', 'S0001#003'], ['CL0001'], scripture)


NOT_CLAIM_STEPS = {'steps': [{'step_id': 'T1', 'line': '經文事實：……', 'sentence_ids': ['S0001#001', 'S0001#002'], 'supports': ['CL0001']}],
                   'claim_text': [], 'other': [], 'digressions': [],
                   'not_claims': [{'claim_id': 'CL0002', 'kind': 'evidence', 'target': 'CL0001', 'why': '一段經文的讀法，用來證明 CL0001'}]}


def test_the_steps_pass_may_return_an_entry_that_is_not_a_claim():
    # Lecture 2: the Claim list is written before the sentence-by-sentence reading, and
    # a verse reading used as Evidence became a Claim that a step then had to support.
    from backend.pipeline import claim_creation_flash as c
    ids = ['S0001#001', 'S0001#002']
    assert c.check_steps(deepcopy(NOT_CLAIM_STEPS), ids, ['CL0001', 'CL0002'])


@pytest.mark.parametrize('fault,match', [
    ('target_gone', 'must name a Claim that stays'), ('not_own', 'first made in this group'),
    ('all_gone', 'without a Claim'), ('has_claim_text', 'has no claim_text'),
    ('still_supported', 'may support a Claim returned'), ('digression_target', 'has no target'), ('no_reason', 'needs a reason')])
def test_a_returned_claim_is_checked_before_the_records_call(fault, match):
    from backend.pipeline import claim_creation_flash as c
    steps = deepcopy(NOT_CLAIM_STEPS); row = steps['not_claims'][0]; own = ['CL0001', 'CL0002']
    if fault == 'target_gone': row['target'] = 'CL0002'
    if fault == 'not_own': own = ['CL0001']
    if fault == 'all_gone': steps['not_claims'].append({'claim_id': 'CL0001', 'kind': 'digression', 'target': '', 'why': '題外話'})
    if fault == 'has_claim_text': steps['claim_text'] = [{'claim_id': 'CL0002', 'sentence_ids': ['S0001#002']}]; steps['steps'][0]['sentence_ids'] = ['S0001#001']
    if fault == 'still_supported': steps['steps'][0]['supports'].append('CL0002')
    if fault == 'digression_target': row['kind'] = 'digression'
    if fault == 'no_reason': row['why'] = ' '
    with pytest.raises(ValueError, match=match):
        c.check_steps(steps, ['S0001#001', 'S0001#002'], ['CL0001', 'CL0002'], own_ids=own)


class ReturningPorts(StepsPorts):
    """The Claims call lists a second entry; the steps pass returns it as Evidence for the first."""
    def repair(self, request):
        task = request['input']['task']
        if task == 'creation_claims':
            out = Ports.repair(self, request)
            out['candidates'].append({**out['candidates'][0], 'statement': '但以理書七章的人子領受永遠權柄。'})
            return out
        if task == 'creation_argument_steps':
            self.calls.append(task)
            assert set(request['input']['claims']) == {'CL0001', 'CL0002'} and 'not_claims' in request['system']
            return {'steps': [
                {'step_id': 'T1', 'line': '反方：人子只強調人性', 'sentence_ids': ['S0001#001'], 'supports': ['CL0001']},
                {'step_id': 'T2', 'line': '回答：不對', 'sentence_ids': ['S0001#002'], 'supports': ['CL0001']},
                {'step_id': 'T3', 'line': '經文事實：但以理書的人子領受永遠權柄', 'sentence_ids': ['S0002#003'], 'supports': ['CL0001', 'T2']}],
                'claim_text': [], 'other': [{'sentence_ids': ['S0003#004'], 'why': '聽眾提問，下段回答。'}], 'digressions': [],
                'not_claims': [{'claim_id': 'CL0002', 'kind': 'evidence', 'target': 'CL0001', 'why': '經文讀法，用來證明 CL0001'}]}
        if task == 'creation_step_records':
            assert set(request['input']['claims']) == {'CL0001'}
        return super().repair(request)


def test_a_returned_claim_leaves_the_candidate_and_is_recorded(tmp_path):
    source = _transcript(); path = tmp_path / 'source.json'; path.write_text(json.dumps(source)); root = tmp_path / 'run'
    ports = ReturningPorts(source)
    result = creation.run('T', path, root, ports, groups=groups(), grouped_package=True, steps_first=True)
    package = json.loads((root / 'candidate.json').read_text())
    assert [c['claim_id'].rsplit('-', 1)[-1] for c in package['claims']] == ['CL0001'] and set(result['claim_candidates']) == {'CL0001'}
    returned = json.loads((root / 'not-claims.json').read_text())['returned']
    assert [(r['claim_id'], r['kind'], r['target'], r['statement']) for r in returned] == [
        ('CL0002', 'evidence', 'CL0001', '但以理書七章的人子領受永遠權柄。')]
    assert result['not_claims'] == returned


def test_a_group_of_only_a_digression_needs_no_claim():
    """Lecture 2: GPT made the Time Magazine aside its own group, with no Claim."""
    from backend.pipeline import claim_creation_flash as flash
    result = {'steps': [], 'claim_text': [], 'other': [{'sentence_ids': ['S1#2'], 'why': '過場'}],
              'digressions': [{'kind': 'unsupported_comment', 'sentence_ids': ['S1#1'], 'why': '批評記者，沒有理由'}], 'not_claims': []}
    assert flash.check_steps(result, ['S1#1', 'S1#2'], []) == {}


def test_a_test_run_picks_only_the_groups_that_own_the_named_paragraphs():
    from backend.pipeline.claim_creation_flash import selected_groups
    keys = ['S0001', 'S0002', 'S0003', 'S0004']
    groups = [{'paragraph_ranges': [{'first': 'S0001', 'last': 'S0002'}]},
              {'paragraph_ranges': [{'first': 'S0003', 'last': 'S0003'}]},
              {'paragraph_ranges': [{'first': 'S0004', 'last': 'S0004'}]}]
    assert selected_groups(groups, keys, ['S0002', 'S0003']) == {1, 2}
    import pytest
    with pytest.raises(ValueError, match='unknown paragraphs'):
        selected_groups(groups, keys, ['S0009'])


def test_grouping_prompt_keeps_a_heading_section_whole():
    import inspect
    from backend.pipeline import claim_creation_flash as c
    text = inspect.getsource(c.run)
    assert '不在一個小標題之內切開' in text and '小標題只是線索' not in text


def test_a_short_answer_to_a_question_stands_on_his_own_words_without_a_step():
    # 2017 （六）3 Q&A: 「挪亞之約是不是沒有條件？」 answered in one sentence, no reasons.
    from backend.pipeline import claim_creation_flash as c
    steps = {'steps': [{'step_id': 'T1', 'line': '问题：挪亞之約是否沒有條件？', 'sentence_ids': ['S0020#001'], 'supports': ['CL0009']}],
             'claim_text': [{'claim_id': 'CL0009', 'sentence_ids': ['S0020#002']}], 'other': [], 'digressions': []}
    assert c.check_steps(steps, ['S0020#001', 'S0020#002'], ['CL0009'])
    # Without his own words it is still refused: nothing would hold the answer.
    steps['claim_text'] = []; steps['steps'][0]['sentence_ids'] = ['S0020#001', 'S0020#002']
    with pytest.raises(ValueError, match='every Claim needs a step'):
        c.check_steps(steps, ['S0020#001', 'S0020#002'], ['CL0009'])


def test_only_a_named_short_answer_to_a_question_may_lack_evidence():
    r=_response();r['claims'][0]['evidence_step_ids']=[];r['evidence_steps'][0]['produced_claim_ids']=[]
    for q in r['questions']:q['answer_claim_ids']=[]
    with pytest.raises(d.DetailedExtractionValidationError,match='no evidence'):
        d.validate_response(r,_transcript(),answer_only_claims=['CL001'])  # no Question records it as the answer
    r['questions'][0]['answer_claim_ids']=['CL001']
    d.validate_response(r,_transcript(),answer_only_claims=['CL001'])
    with pytest.raises(d.DetailedExtractionValidationError,match='no evidence'):
        d.validate_response(r,_transcript())  # not named by the step check: still refused


def test_prompts_say_a_quoted_slide_line_is_not_his_words():
    from backend.pipeline import claim_creation_flash as c
    assert '不能作conclusion_anchor' in c.CLAIMS and '投影片文字' in c.STEPS


def test_a_sentence_id_with_the_wrong_paragraph_is_repaired_only_when_its_number_is_unambiguous():
    from backend.pipeline import claim_creation_flash as c
    ids = ['S0015#321', 'S0015#322', 'S0016#323']
    reply = {'steps': [], 'other': [], 'claim_text': [{'claim_id': 'C1', 'sentence_ids': ['S0015#321', 'S0016#322', 'S0016#323']}]}
    assert c.repair_sentence_ids(reply, ids) == [{'written': 'S0016#322', 'sentence_id': 'S0015#322'}]
    assert reply['claim_text'][0]['sentence_ids'] == ids
    # already placed elsewhere, or a number that names no sentence: left for check_steps to refuse
    twice = {'steps': [], 'other': [{'sentence_ids': ['S0015#322']}], 'claim_text': [{'claim_id': 'C1', 'sentence_ids': ['S0016#322']}]}
    assert c.repair_sentence_ids(twice, ids) == []
    unknown = {'steps': [], 'claim_text': [{'claim_id': 'C1', 'sentence_ids': ['S0016#999']}]}
    assert c.repair_sentence_ids(unknown, ids) == []


def test_prompts_say_a_point_answers_what_its_topic_makes_a_listener_ask():
    from backend.pipeline import claim_creation_flash as f
    prompt = ''.join(v for v in vars(f).values() if isinstance(v, str))
    assert '心裡自然會問的問題，statement都要用他的話回答' in prompt
    assert '分哪幾段（每段的經文範圍）、每段講什麼、段與段怎樣連接' in prompt
    assert '即使只講一次、沒有強調' in prompt


def test_prompts_say_a_top_point_names_its_parts_without_restating_them():
    from backend.pipeline import claim_creation_flash as f
    prompt = ''.join(v for v in vars(f).values() if isinstance(v, str))
    assert '不寫目錄' not in prompt  # it told the top point to drop the division (lecture 1 rerun round 1)
    assert '只有結論本身就是分段或列舉時' in prompt  # lecture 2 r8: every cover listed its topics
    assert '不寫「要從甲、乙、丙來明白」這種只列題目、沒有答案的句子' in prompt
    assert '上層的結論本身是分段或列舉時，點出各段經文範圍、各項名稱' in prompt


def test_step_prompt_sends_a_restated_step_back_to_its_step_not_the_question():
    from backend.pipeline import claim_creation_flash as f
    assert '那是那一步的重述，归入前面那一步，不归入问题' in f.STEPS
    assert '没有再念经文' in f.STEPS and '那一句仍属于问题' in f.STEPS  # round 3 moved a one-line side of the tension


def _one_step(line, attribution):
    from types import SimpleNamespace as S
    from backend.pipeline import claim_creation_flash as f
    text = '或者是從不同的角度來講，是可以有不同的結論。'
    sentences = [S(sentence_id='S1#1', segment_index=1, char_start=0, char_end=len(text))]
    steps = {'steps': [{'step_id': 'ST01', 'line': line, 'supports': [], 'sentence_ids': ['S1#1']}],
             'claim_text': [], 'other': []}
    records = [{'step_id': 'ST01', 'kind': 'position', 'statement': '從不同角度可以有不同結論', 'attribution': attribution,
                'observation_type': None, 'step_type': None, 'scripture_refs': []}]
    return f.build_step_records(steps, records, sentences, {1: text})


def test_an_unnamed_holder_of_an_opposing_view_is_recorded_as_someone():
    # lecture 6 G004: a quoted question he corrects; his words never say whose it is (twice)
    out = _one_step('反方：从不同角度可以有不同结论', '')
    assert out['positions'][0]['attribution'] == '有人'
    assert out['repairs'] == [{'record_id': 'ST01', 'attribution': '有人', 'why': 'holder not named in his words'}]
    assert _one_step('反方：……', '很多人')['positions'][0]['attribution'] == '很多人'


def test_a_position_without_holder_outside_an_opposing_step_is_still_refused():
    import pytest
    with pytest.raises(ValueError, match='position needs attribution'):
        _one_step('X，因为 Y', '')
