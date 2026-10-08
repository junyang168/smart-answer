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
