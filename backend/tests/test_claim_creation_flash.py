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
                'support_hint':'但以理書人子領受永遠權柄。','existing_claim_id':None}]}
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
    source=_transcript();source['script'][0]['text']='長'*30000
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
