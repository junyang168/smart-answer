from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json

import pytest

from backend.api.canonical_repository.postgres_store import record_content_sha,sha256_json
from backend.pipeline.cvp_sql_flow import current_claim_authority
from backend.pipeline.knowledge_source import markdown_source_document
from backend.pipeline.source_contract_cleanup import build_projection,build_source_contract_cleanup_plan,migrate_claim_occurrence_anchors
from backend.pipeline.viewpoint_resolution_runtime import write_immutable


@pytest.fixture
def authority_case(tmp_path):
    source_path=tmp_path/'source.md'
    source_path.write_text('# AI title\n\nFirst paragraph.\n\n## AI subtitle\n\nSecond paragraph with exact evidence.\n')
    source={'source_id':'SRC-test','transcript_id':'SRC-test','source_type':'notes_manuscript','source_path':str(source_path)}
    script,raw,_=markdown_source_document(source)
    index,projection=build_projection(script['script'])
    physical=next(r.physical_ordinal for r in index.rows if 'exact evidence' in r.text)
    before={'claim_id':'CL-test','revision':2,'statement':'Second paragraph has evidence.',
        'review_status':'ai_consensus_reviewed','evidence_step_ids':['E-test'],
        'occurrences':[{'source_id':'SRC-test','transcript_id':'SRC-test','anchors':[
            {'evidence_id':'E-test','paragraph_key':f'S{physical:04d}','proposed_highlight':{'text':'exact evidence'}}]}]}
    migrated,findings=migrate_claim_occurrence_anchors(before,index,source_id='SRC-test',transcript_id='SRC-test')
    assert migrated and findings
    previous=('KCS-original',record_content_sha(before),before)
    plan=build_source_contract_cleanup_plan(package_id='synthetic',current={('claims','CL-test'):
        {'payload':before,'revision':2,'content_sha256':previous[1]}},replacements={('claims','CL-test'):migrated})
    after=dict(plan.operations[0].payload,revision=3)
    source.update(source_file_sha256=hashlib.sha256(raw).hexdigest(),source_body_sha256=projection.body_sha256)
    path=tmp_path/'plan.json'
    document={'schema_version':'wang_source_contract_cleanup_source_plan_v1','source':source,'plan':plan.as_dict()}
    document['artifact_sha256']=sha256_json(document)
    write_immutable(path,document)
    actual=[]
    for op in plan.as_dict()['operations']:
        actual.append({**{k:v for k,v in op.items() if k!='payload'},'before':before,'after':after})
    history={'status':'applied','source_kind':plan.source_kind,'source_sha256':plan.source_sha256,
        'fingerprint_sha256':plan.fingerprint_sha256,'operations':actual,
        'metadata':{'source_plan_path':str(path),'source_plan_file_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}}

    class Result:
        def __init__(self,value):self.value=value
        def fetchone(self):return self.value
        def fetchall(self):return self.value

    class Store:
        def __init__(self):
            self.row={'payload':after,'revision':3,'content_sha256':record_content_sha(after),'retired':False}
            self.current=(plan.change_set_id,self.row['content_sha256'],after)
            self.previous=previous
            self.current_events=[]
            self.previous_events=[('REV-original','ai','ai_consensus_reviewed',{'change_set_id':'KCS-original'})]
            self.history=history
            self.prior={'status':'applied','metadata':{'review_event_id':'REV-original'},
                'operations':[{'collection':'claims','object_id':'CL-test','after_revision':2,
                    'after_sha256':previous[1],'after':before}]}
            self.calls=[]
        @contextmanager
        def connect(self):yield self
        def claim_management_qualification(self,cid):
            # These fixtures exercise untouched historical review authorities.
            return None
        def execute(self,sql,params):
            self.calls.append(('SQL',params))
            if 'object_versions' in sql:return Result(self.current if params[1]==3 else self.previous)
            return Result(self.current_events if params[1]==3 else self.previous_events)
        def get_change_history(self,cid):
            self.calls.append(('HISTORY',cid))
            return self.history if cid==plan.change_set_id else self.prior

    return Store(),path,source_path


def test_exact_coordinate_step_consumes_real_predecessor_without_new_approval(authority_case):
    s,_,_=authority_case
    result=current_claim_authority(s,'CL-test',s.row)
    assert result['review_event_id']=='REV-original'
    assert result['reviewed_revision']==2 and result['revision']==3
    assert result['authority_kind']=='exact_sql_applied_locator_postimage'
    assert result['model_calls']==result['master_data_mutations']==0
    assert len(s.calls)==6  # point queries and two exact ChangeSets, never list_records


@pytest.mark.parametrize('qualification', [
    {'ready':False,'reason':'formal_review_findings'},
    {'ready':True,'revision':2,'content_sha256':'a'*64},
])
def test_v2_current_failure_or_stale_proof_cannot_fall_back_to_historical_pass(authority_case,qualification):
    store,_,_=authority_case
    store.claim_management_qualification=lambda cid:qualification
    with pytest.raises(ValueError,match='exact independent qualification'):
        current_claim_authority(store,'CL-test',store.row)
    assert store.calls == []


@pytest.mark.parametrize('mutation',[
    'missing_review','duplicate_review','empty_artifact','wrong_kind','uncommitted',
    'prior_uncommitted','current_sha','previous_sha','version_content','plan_fingerprint',
    'operation_sha','missing_operation','physical_source','physical_plan','semantic_change',
    'foreign_review','unbound_review','prior_postimage',
])
def test_locator_authority_rejects_missing_ambiguous_or_changed_proof(authority_case,mutation):
    s,path,source_path=authority_case
    if mutation=='missing_review':s.previous_events=[]
    elif mutation=='duplicate_review':s.previous_events*=2
    elif mutation=='empty_artifact':s.previous_events=[('REV-original','ai','ai_consensus_reviewed',{})]
    elif mutation=='wrong_kind':s.history['source_kind']='anything_else'
    elif mutation=='uncommitted':s.history['status']='pending'
    elif mutation=='prior_uncommitted':s.prior['status']='pending'
    elif mutation=='foreign_review':s.previous_events=[('REV-original','ai','ai_consensus_reviewed',{'change_set_id':'KCS-foreign'})]
    elif mutation=='unbound_review':s.prior['metadata']={}
    elif mutation=='prior_postimage':s.prior['operations'][0]['after_sha256']='b'*64
    elif mutation=='current_sha':s.row['content_sha256']='a'*64
    elif mutation=='previous_sha':s.previous=(s.previous[0],'a'*64,s.previous[2])
    elif mutation=='version_content':s.row={**s.row,'payload':dict(s.row['payload'],statement='stronger')}
    elif mutation=='plan_fingerprint':s.history['fingerprint_sha256']='a'*64
    elif mutation=='operation_sha':s.history['operations'][0]['before_sha256']='a'*64
    elif mutation=='missing_operation':s.history['operations']=[]
    elif mutation=='physical_source':source_path.write_text('different source')
    elif mutation=='physical_plan':path.write_text('{}')
    elif mutation=='semantic_change':
        # Even a resealed, applied-looking plan is not permission to carry an
        # old review over new content. Full deterministic postimage must match.
        s.row['payload']['statement']='Changed semantic statement'
        s.row['content_sha256']=record_content_sha(s.row['payload'])
        s.current=(s.current[0],s.row['content_sha256'],s.row['payload'])
        s.history['operations'][0]['after_sha256']=s.row['content_sha256']
        doc=json.loads(path.read_text());doc.pop('artifact_sha256')
        doc['plan']['operations'][0]['payload']['statement']='Changed semantic statement'
        doc['plan']['operations'][0]['after_sha256']=s.row['content_sha256']
        doc['artifact_sha256']=sha256_json(doc);path.write_text(json.dumps(doc))
        s.history['metadata']['source_plan_file_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):current_claim_authority(s,'CL-test',s.row)


def test_ambiguous_current_review_does_not_use_predecessor_fallback(authority_case):
    s,_,_=authority_case;s.current_events=s.previous_events*2
    with pytest.raises(ValueError,match='missing/ambiguous'):current_claim_authority(s,'CL-test',s.row)
    assert len(s.calls)==1


def test_real_hold_never_uses_coordinate_history(authority_case):
    s,_,_=authority_case;s.row['payload']['review_status']='human_hold'
    with pytest.raises(ValueError,match='held'):current_claim_authority(s,'CL-test',s.row)
    assert not s.calls


@pytest.mark.parametrize('fault', [None, 'no_proof', 'not_ready', 'stale_revision', 'stale_sha', 'missing_marker',
                                 'false_marker', 'string_marker', 'human_hold', 'withheld', 'rejected', 'superseded', 'retired'])
def test_legacy_sampling_requires_exact_current_qualification_and_explicit_replacement(authority_case, fault):
    s, _, _ = authority_case
    s.row['payload']['review_status'] = 'human_review_required'
    q = {'ready': True, 'revision': s.row['revision'], 'content_sha256': s.row['content_sha256'],
         'legacy_ai_spot_check_replaced_by_current_review': True, 'audit': {'file_sha256': 'a'*64}}
    if fault == 'no_proof': q = None
    elif fault == 'not_ready': q['ready'] = False
    elif fault == 'stale_revision': q['revision'] -= 1
    elif fault == 'stale_sha': q['content_sha256'] = 'b'*64
    elif fault == 'missing_marker': q.pop('legacy_ai_spot_check_replaced_by_current_review')
    elif fault == 'false_marker': q['legacy_ai_spot_check_replaced_by_current_review'] = False
    elif fault == 'string_marker': q['legacy_ai_spot_check_replaced_by_current_review'] = 'true'
    elif fault == 'retired': s.row['retired'] = True
    elif fault in {'human_hold', 'rejected', 'superseded'}: s.row['payload']['review_status'] = fault
    elif fault == 'withheld': s.row['payload']['review_status'] = 'withheld_unreviewed'
    s.claim_management_qualification = lambda cid: q
    before = deepcopy(s.row)
    if fault is None:
        result = current_claim_authority(s, 'CL-test', s.row)
        assert result['qualification'] == q
    else:
        with pytest.raises(ValueError): current_claim_authority(s, 'CL-test', s.row)
    assert s.row == before and not s.calls


def test_sermon_json_locator_step_uses_actual_body_projection(authority_case,tmp_path):
    s,path,source_path=authority_case
    source=json.loads(path.read_text())['source']
    script,_,_=markdown_source_document(source)
    json_path=tmp_path/'sermon.json';json_path.write_text(json.dumps(script))
    document=json.loads(path.read_text());document.pop('artifact_sha256')
    document['source'].update(source_type='sermon_transcript',source_path=str(json_path),
        source_file_sha256=hashlib.sha256(json_path.read_bytes()).hexdigest())
    document['artifact_sha256']=sha256_json(document);path.write_text(json.dumps(document))
    s.history['metadata']['source_plan_file_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    result=current_claim_authority(s,'CL-test',s.row)
    assert result['source']['path']==str(json_path)
    assert result['model_calls']==result['master_data_mutations']==0


def duplicate_case(authority_case, tmp_path):
    s,path,_=authority_case
    document=json.loads(path.read_text());source=document['source']
    script,_,_=markdown_source_document(source)
    json_path=tmp_path/'duplicate-sermon.json';json_path.write_text(json.dumps(script))
    source.update(source_type='sermon_transcript',source_path=str(json_path),
        source_file_sha256=hashlib.sha256(json_path.read_bytes()).hexdigest())
    before=s.previous[2]
    plan=build_source_contract_cleanup_plan(package_id='synthetic-duplicate',
        source_kind='wkp368_source_duplicate_cleanup',current={('claims','CL-test'):
            {'payload':before,'revision':2,'content_sha256':s.previous[1]}},
        replacements={('claims','CL-test'):{**s.row['payload'],'revision':2}})
    s.current=(plan.change_set_id,s.row['content_sha256'],s.row['payload'])
    s.get_change_history=lambda cid:s.history if cid==s.current[0] else s.prior
    s.history.update(source_kind=plan.source_kind,source_sha256=plan.source_sha256,
                     fingerprint_sha256=plan.fingerprint_sha256)
    document={'schema_version':'wang_source_duplicate_cleanup_plan_v1','plan':plan.as_dict(),
        'groups':[{'alias_source_id':'ALIAS','canonical_source_id':'SRC-test','transcript_id':'SRC-test',
                   'coordinate_ready':True,'coordinate_blockers':{'claims':[],'fragments':[],
                                                               'human_settled_claim_ids':[]}}],
        'frozen_sources':[{k:v for k,v in source.items() if k not in {'source_id','source_type'}}]}
    def save():
        document.pop('artifact_sha256',None);document['artifact_sha256']=sha256_json(document)
        path.write_text(json.dumps(document));s.history['metadata']['source_plan_file_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    save()
    return s,document,save,json_path


def test_duplicate_source_transaction_exact_locator_only_branch(authority_case,tmp_path):
    s,_,_,_=duplicate_case(authority_case,tmp_path)
    result=current_claim_authority(s,'CL-test',s.row)
    assert result['review_event_id']=='REV-original'
    assert result['reviewed_revision']==2 and result['revision']==3
    assert result['model_calls']==result['master_data_mutations']==0


@pytest.mark.parametrize('problem',['no_review','duplicate_group','missing_source','duplicate_source',
    'not_ready','blocker','alias_reassignment','changed_source','wrong_transcript','changed_postimage'])
def test_duplicate_transaction_cannot_expand_coordinate_authority(authority_case,tmp_path,problem):
    s,doc,save,source_path=duplicate_case(authority_case,tmp_path)
    if problem=='no_review':s.previous_events=[]
    elif problem=='duplicate_group':doc['groups']*=2
    elif problem=='missing_source':doc['frozen_sources']=[]
    elif problem=='duplicate_source':doc['frozen_sources']*=2
    elif problem=='not_ready':doc['groups'][0]['coordinate_ready']=False
    elif problem=='blocker':doc['groups'][0]['coordinate_blockers']['claims']=['unresolved']
    elif problem=='alias_reassignment':doc['groups'][0]['alias_source_id']='SRC-test'
    elif problem=='wrong_transcript':doc['frozen_sources'][0]['transcript_id']='foreign'
    elif problem=='changed_source':source_path.write_text('{"script":[]}')
    elif problem=='changed_postimage':s.history['operations'][0]['after']['occurrences'][0]['anchors'][0]['paragraph_key']='S9999'
    save()
    with pytest.raises(ValueError):current_claim_authority(s,'CL-test',s.row)


@pytest.mark.parametrize('problem',[None,'held','foreign','missing','retired','fragment_sha',
    'fragment_postimage','fragment_source','ambiguous_index','quote_changed'])
@pytest.mark.parametrize('kind',['wkp368_source_duplicate_cleanup','wkp368_source_contract_cleanup'])
def test_duplicate_short_quote_requires_exact_own_committed_fragment(authority_case,tmp_path,problem,kind):
    from backend.pipeline.source_contract_cleanup import migrate_source_fragment
    s,doc,save,source_path=duplicate_case(authority_case,tmp_path)
    script=json.loads(source_path.read_text())
    last=deepcopy(script['script'][-1])
    script['script'].extend([{**last,'index':999,'text':'Additional body.'},
                            {**last,'index':1000,'text':'Another exact evidence occurrence.'}])
    source_path.write_text(json.dumps(script))
    index,projection=build_projection(script['script'])
    source=doc['frozen_sources'][0];source.update(source_file_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
        source_body_sha256=projection.body_sha256)
    before_fragment={'fragment_id':'F','source_id':'SRC-test','revision':1,
        'paragraph_key':'S0004','source_segment_index':index.by_locator['S0002'].source_segment_index,
        'verbatim_excerpt':'exact evidence'}
    moved,res=migrate_source_fragment(before_fragment,index,source_sha256=projection.body_sha256,attested_locator='S0002')
    assert moved and res.status=='resolved'
    plan=build_source_contract_cleanup_plan(package_id='synthetic-duplicate-ambiguous',
        source_kind=kind,
        current={('claims','CL-test'):{'payload':s.previous[2],'revision':2,'content_sha256':s.previous[1]},
                 ('source_fragments','F'):{'payload':before_fragment,'revision':1,
                                          'content_sha256':record_content_sha(before_fragment)}},
        replacements={('claims','CL-test'):{**s.row['payload'],'revision':2},('source_fragments','F'):moved})
    after_fragment=dict(moved,revision=2)
    step={'evidence_step_id':'E-test','support_eligibility':'eligible_candidate',
          'produced_claim_ids':['CL-test'],'source_fragment_ids':['F']}
    states={('evidence_steps','E-test'):{'payload':step,'revision':1,'content_sha256':record_content_sha(step),'retired':False},
            ('source_fragments','F'):{'payload':after_fragment,'revision':2,
                                    'content_sha256':record_content_sha(after_fragment),'retired':False}}
    s.current=(plan.change_set_id,s.row['content_sha256'],s.row['payload'])
    s.history.update(source_kind=plan.source_kind,source_sha256=plan.source_sha256,fingerprint_sha256=plan.fingerprint_sha256)
    s.history['operations']=[{**{k:v for k,v in op.items() if k!='payload'},
        'before':s.previous[2] if op['collection']=='claims' else before_fragment,
        'after':s.row['payload'] if op['collection']=='claims' else after_fragment} for op in plan.as_dict()['operations']]
    doc['plan']=plan.as_dict()
    if kind=='wkp368_source_contract_cleanup':
        doc['schema_version']='wang_source_contract_cleanup_source_plan_v1'
        doc['source']={**source,'source_id':'SRC-test','source_type':'sermon_transcript'}
    s.get_record_state=lambda col,oid:deepcopy(states.get((col,oid)))
    if problem=='held':step['support_eligibility']='context_only'
    elif problem=='foreign':step['produced_claim_ids']=['OTHER']
    elif problem=='missing':states.pop(('source_fragments','F'))
    elif problem=='retired':states['source_fragments','F']['retired']=True
    elif problem=='fragment_sha':states['source_fragments','F']['content_sha256']='wrong'
    elif problem=='fragment_postimage':after_fragment['paragraph_key']='S0004'
    elif problem=='fragment_source':before_fragment['source_id']='ALIAS'
    elif problem=='ambiguous_index':before_fragment['source_segment_index']='missing'
    elif problem=='quote_changed':before_fragment['verbatim_excerpt']='changed quote'
    if problem in {'held','foreign'}:states['evidence_steps','E-test']['content_sha256']=record_content_sha(step)
    save()
    if problem:
        with pytest.raises(ValueError):current_claim_authority(s,'CL-test',s.row)
    else:
        result=current_claim_authority(s,'CL-test',s.row)
        assert result['review_event_id']=='REV-original' and result['master_data_mutations']==0


def test_conflicting_predecessor_decision_is_not_hidden_by_pass_filter(authority_case):
    s,_,_=authority_case
    s.previous_events=[('REV-rejected','ai','rejected',{'change_set_id':'KCS-original'})]
    with pytest.raises(ValueError,match='ambiguous predecessor review'):
        current_claim_authority(s,'CL-test',s.row)


@pytest.mark.parametrize('change',[None,'missing','changed','hold','retired','sha'])
def test_imported_pass_requires_current_dependency_postimages(authority_case,monkeypatch,change):
    from backend.pipeline import cvp_sql_imported_claim_review as imported
    s,_,_=authority_case
    s.previous_events=[];s.prior['source_kind']='knowledge_package'
    step={'evidence_step_id':'E-test','revision':2,'statement':'Exact evidence.',
        'review_status':'candidate','support_eligibility':'eligible'}
    sha=record_content_sha(step)
    s.prior['operations'].append({'collection':'evidence_steps','object_id':'E-test',
        'operation':'update','after':step,'after_revision':2,'after_sha256':sha})
    proof={'review_event_id':None,'reviewer_kind':'ai','decision':'ai_consensus_reviewed',
        'review_artifact_sha256':'a'*64,'imported_dependency_pins':[
            {'collection':'evidence_steps','object_id':'E-test','revision':2,'content_sha256':sha}]}
    monkeypatch.setattr(imported,'imported_passing_review',lambda *a:deepcopy(proof))
    live={'payload':deepcopy(step),'revision':2,'content_sha256':sha,'retired':False}
    if change=='changed':live['payload']['statement']='Changed evidence.'
    elif change=='hold':live['payload']['support_eligibility']='context_only'
    elif change=='retired':live['retired']=True
    elif change=='sha':live['content_sha256']='f'*64
    s.get_record_state=lambda *key:None if change=='missing' else live
    if change is not None:
        with pytest.raises(ValueError,match='current reviewed dependency differs'):
            current_claim_authority(s,'CL-test',s.row)
    else:
        result=current_claim_authority(s,'CL-test',s.row)
        assert result['review_event_id'] is None
        assert result['current_dependency_pins']==proof['imported_dependency_pins']
        assert result['model_calls']==result['master_data_mutations']==0
