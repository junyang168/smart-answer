"""Literal source punctuation is mechanical; teaching words and bindings are not."""
from copy import deepcopy
import pytest
from backend.pipeline.claim_quote_projection import source_excerpt,project,CONTRACT,FIELD
from backend.pipeline import claim_management as cm,claim_model_output as output
from backend.tests.test_claim_management import case

@pytest.mark.parametrize('quote,source,expected',[
 ('魂這個字，來指我們整個的人。','魂這個字來指我們整個的人。','魂這個字來指我們整個的人。'),
 ('教授說靈魂，指全人。','教授說：靈魂指全人。','教授說：靈魂指全人。'),
 ('這是原文！','這是原文。','這是原文。'),
 ('人不是神。','人是神。','人不是神。'),
 ('他的意思是主要的。','他的意思是這樣的。','他的意思是主要的。'),
 ('那個說法錯了。','那個說法錯。','那個說法錯了。'),
 ('馬太5:16說。','馬太5:14說。','馬太5:16說。'),
 ('-1','1','-1'),('1.0','10','1.0'),('5:16','516','5:16'),
 ('甲，乙。','甲乙。另一處也是甲乙。','甲，乙。'),
 ('甲乙','甲，乙','甲，乙'),
 ('甲乙','甲乙','甲乙'),
 ('甲。乙！丙？丁。','甲乙丙丁。','甲乙丙丁。'),
 ('教授的話。','另一段話。','教授的話。'),
])
def test_unique_literal_punctuation_only(quote,source,expected):
 assert source_excerpt(quote,source)==expected


def test_candidate_projection_leaves_statements_endpoints_and_original_raw_unchanged(case):
 request,before,candidate,sources=deepcopy(case)
 sources['S']['body_sha256']='a'*64
 sources['S']['paragraphs']['S0001']['text']='甲是假设。乙只在特定情况下成立。'
 f=deepcopy(before['source_fragments/F']['payload']);f['verbatim_excerpt']='甲，是假设。'
 candidate.records=[cm.source.Row(collection='source_fragments',object_id='F',payload=f)]
 for d in candidate.dispositions:
  if d.record_key=='source_fragments/F':
   d.action='update';d.citations=[cm.Citation(source_id='S',paragraph_key='S0001',exact_quote='甲，是假设。')]
 original=candidate.model_dump(mode='json')
 fixed,changes=project(candidate,sources)
 assert candidate.model_dump(mode='json')==original
 assert len(changes)==2 and fixed.records[0].payload['verbatim_excerpt']=='甲是假设。'
 assert fixed.dispositions[-1].citations[0].exact_quote=='甲是假设。'
 normalized,receipt=cm.normalize_candidate(original,sources,source_quote_projection=True)
 assert normalized.records[0].payload['verbatim_excerpt']=='甲是假设。'
 assert receipt['raw_candidate_sha256']==candidate.digest
 assert receipt['source_quote_corrections']==changes
 assert not receipt['reader_visible_text_unchanged'] and receipt['semantic_decisions_unchanged']
 with pytest.raises(ValueError,match='exact complete own source'):
  cm.normalize_candidate(original,sources)  # old frozen policies keep their contract


def test_policy_default_serialization_does_not_upgrade_frozen_requests():
 p=cm.Policy.model_validate_json(cm.DEFAULT_POLICY.read_text())
 assert 'source_quote_projection' not in p.model_dump(mode='json')
 p.source_quote_projection=CONTRACT
 assert p.model_dump(mode='json')['source_quote_projection']==CONTRACT


def test_previous_epoch_citation_is_not_rewritten_against_current_source(case):
 request,before,candidate,sources=deepcopy(case)
 candidate.dispositions[0].citations=[cm.Citation(source_id='S',paragraph_key='S0001',
                                                source_epoch='before',exact_quote='甲，是假设。')]
 fixed,changes=project(candidate,sources)
 assert not changes and fixed==candidate

from pathlib import Path
import json
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres
from backend.tests.test_claim_management_intake import claims,pass_review
from backend.tests.test_claim_metadata_projection import DurableSyntheticPorts
from backend.pipeline import claim_management_intake as intake,claim_management_batch as batch
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_commit import binding
from backend.pipeline.claim_review_recovery import recover_returned_candidate,CANDIDATE_IDENTITY_VERSION


def test_saved_punctuation_rejection_reuses_gpt_and_only_original_unused_opus(claims):
 store,root=claims
 prepared=intake.prepare(['C'],root,instruction='Refresh the complete Claim',store=store)
 request,execution=next(iter(batch.load(prepared['manifest']['path'],root/'batch',store=store)[1].values()))
 cite={'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}
 def review(packet):
  result=pass_review(packet)
  if not packet['candidate']['records']:
   result['findings']=[{'target_key':'claims/C','component':'statement','category':'mapping',
    'reason':'synthetic full source precision','acceptance_condition':'preserve complete wording',
    'citations':[cite],'affected_target_keys':['claims/C'],'affected_write_keys':[],
    'impact_reason':'own Claim statement'}]
  result['prior_findings']=[{'finding_id':fid,'disposition':'resolved','reason':'source wording retained',
   'citations':[cite],'affected_target_keys':['claims/C'],'affected_write_keys':['claims/C'],
   'impact_reason':'complete original statement'} for fid in packet['unresolved']]
  return result
 def propose(packet):
  return {'changes':[
   {'record_key':'claims/C','fields_json':json.dumps({'statement':'甲是假设，不是断言。'}),'reason':'complete wording','citations':[cite]},
   {'record_key':'source_fragments/FC','fields_json':json.dumps({'verbatim_excerpt':'甲，是假设，不是断言。'}),
    'reason':'literal source','citations':[{**cite,'exact_quote':'甲，是假设，不是断言。'}]}],
   'creations':[],'retirements':[],'findings':[],'finding_resolutions':[
    {'finding_id':fid,'disposition':'resolved','reason':'complete wording','citations':[cite],
     'affected_target_keys':['claims/C'],'affected_write_keys':['claims/C'],'impact_reason':'source restored'}
    for fid in packet['unresolved']]}
 ports=DurableSyntheticPorts(store,execution,opus=review,proposal=propose)
 failed=execute_claim_management(store,request,root=execution,ports=ports)
 assert failed['reason']=='engineering_required',failed
 parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id));rid=next(iter(parent['latest_rounds'].values()))
 old=store.get_cvp_group(rid);raw=binding(execution/rid/'gpt.raw.json');calls=deepcopy(parent['call_reservations'])
 metadata=lambda value:{k:v for k,v in value.items()if k not in {'run_id','status','error'}}
 direction={'schema_version':CANDIDATE_IDENTITY_VERSION,'round_id':rid,'authorized_by':'owner',
  'authorization_quote':'Automatically restore punctuation from its unique bound source and reuse the saved response',
  'human_content_approval':False,'source_quote_projection':CONTRACT,
  'prior_metadata_sha256':cm.sha256_json(metadata(old)),
  'prior_parent_sha256':cm.sha256_json(metadata(parent)),'original_raw':raw}
 path=root/'punctuation-authority.json';path.write_text(json.dumps(direction));authority=binding(path)
 recovered=recover_returned_candidate(store,rid,authorization=authority)
 assert store.get_cvp_group(parent['run_id'])['call_reservations']==calls
 resumed=execute_claim_management(store,request,root=execution,ports=ports)
 assert resumed['status']=='completed',resumed
 assert ports.calls==['opus','gpt','opus']  # zero extra GPT, one original unused Opus
 assert binding(execution/rid/'gpt.raw.json')==raw
 assert store.claim_management_qualification('C')['ready']
 assert store.get_record_state('source_fragments','FC')['payload']['verbatim_excerpt']=='甲是假设，不是断言。'
 receipt=json.loads((Path(recovered)/'candidate-normalization.json').read_text())
 assert len(receipt['source_quote_corrections'])==2
 assert recover_returned_candidate(store,rid,authorization=authority)==recovered
