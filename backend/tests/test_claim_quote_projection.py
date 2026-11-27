"""Literal source restoration preserves decisions and binding identities."""
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


@pytest.mark.parametrize('projection', [CONTRACT, 'source_literal_span_v2'])
def test_saved_punctuation_rejection_reuses_gpt_and_only_original_unused_opus(claims, projection):
 store,root=claims
 prepared=intake.prepare(['C'],root,instruction='Refresh the complete Claim',store=store,
                         policy=intake.DEFAULT_POLICY.parent/'wang_claim_management_joint_update_v8.json')
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
  'human_content_approval':False,'source_quote_projection':projection,
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

from backend.pipeline.claim_quote_projection import NEAR_CONTRACT,project_review

@pytest.mark.parametrize('quote,source,expected',[
 ('所以你們是誰？你們的身體是誰？你們的身體不是你們肉體，你們整個的人的。看到沒有？',
  '所以你們是誰？你們的身體是誰？你們的身體不是你們肉體，你們整個的。看到沒有？這種經文很多。',
  '所以你們是誰？你們的身體是誰？你們的身體不是你們肉體，你們整個的。看到沒有？'),
 ('聽眾問：那能不能理解成說靈魂得救，身體還沒有得贖？教授：不是，那個說法錯了。',
  '聽眾問：那能不能理解成說靈魂得救，身體還沒有得贖？教授：不是，那個說法錯。',
  '聽眾問：那能不能理解成說靈魂得救，身體還沒有得贖？教授：不是，那個說法錯。'),
 ('教授長篇解釋這一段聖經原文的意思與我們的看法。',
  '完全不相干的另外一個問題，沒有引用的相同句子。','教授長篇解釋這一段聖經原文的意思與我們的看法。'),
 ('教授說這一段解釋是完全相同的意思。',
  '教授說這一段解釋是完全不同的意思。教授說這一段解釋是完全相近的意思。',
  '教授說這一段解釋是完全相同的意思。'),
])
def test_near_source_quote_is_unique_literal_source_copy(quote,source,expected):
 assert source_excerpt(quote,source,contract=NEAR_CONTRACT)==expected


def test_review_quote_projection_never_changes_verdict_or_finding_reason():
 text='所以你們是誰？你們的身體是誰？你們的身體不是你們肉體，你們整個的。看到沒有？這種經文很多。'
 wrong='所以你們是誰？你們的身體是誰？你們的身體不是你們肉體，你們整個的人的。看到沒有？'
 raw={'complete_review':True,'binding_assessments':[{'claim_key':'claims/C','sufficient':False}],
      'findings':[{'reason':'Claim wording is unsupported; keep this finding.',
                   'citations':[{'source_id':'S','paragraph_key':'S1','exact_quote':wrong}]}],
      'prior_findings':[]}
 fixed,changes=project_review(raw,{'S':{'paragraphs':{'S1':{'text':text}}}},contract=NEAR_CONTRACT)
 assert raw['findings'][0]['citations'][0]['exact_quote']==wrong
 assert len(changes)==1
 assert fixed['binding_assessments']==raw['binding_assessments']
 assert fixed['findings'][0]['reason']==raw['findings'][0]['reason']
 assert fixed['findings'][0]['citations'][0]['exact_quote'] in text


@pytest.mark.parametrize('projection', [NEAR_CONTRACT, 'source_literal_span_v2'])
def test_unaccepted_opus_near_quote_keeps_findings_and_reuses_original_review(claims, projection):
 from backend.pipeline.claim_review_recovery import recover_returned_review,VERSION
 from backend.pipeline.source_reconciliation_runner import body,read
 store,root=claims
 doc=deepcopy(store.get_record_state('source_documents','S')['payload']);file=Path(doc['source_path'])
 text='甲是假设，不是断言。这里特别强调的是原句的假设性质，不是陈述实际已经发生的事实。'
 wrong=text.replace('假设性质','一个假设性质')
 file.write_text(json.dumps({'script':[{'text':text}]}));doc['source_file_sha256']=binding(file)['file_sha256']
 store.apply_plan(store.plan_package({'package_id':'long-source','source_documents':[doc]}))
 prepared=intake.prepare(['C'],root,instruction='Refresh complete own evidence',store=store,
                         policy=intake.DEFAULT_POLICY.parent/'wang_claim_management_joint_update_v8.json')
 request,execution=next(iter(batch.load(prepared['manifest']['path'],root/'batch',store=store)[1].values()))
 cite={'source_id':'S','paragraph_key':'S0001','exact_quote':text}
 def review(packet):
  result=pass_review(packet)
  if not packet['candidate']['records']:
   result['binding_assessments'][0]['sufficient']=False
   result['findings']=[{'target_key':'claims/C','component':'statement','category':'mapping',
    'reason':'Keep the distinction between a hypothesis and an assertion.','acceptance_condition':'full precision',
    'citations':[{**cite,'exact_quote':wrong}],'affected_target_keys':['claims/C'],
    'affected_write_keys':[],'impact_reason':'own source judgment'}]
  result['prior_findings']=[{'finding_id':fid,'disposition':'resolved','reason':'own source precise',
   'citations':[cite],'affected_target_keys':['claims/C'],'affected_write_keys':['claims/C'],
   'impact_reason':'complete hypothesis wording'} for fid in packet['unresolved']]
  return result
 def proposal(packet):
  assert packet['policy'].get('source_quote_projection') is None  # original policy never rewritten
  return {'changes':[
   {'record_key':'claims/C','fields_json':json.dumps({'statement':'甲是假设，不是断言。'}),'reason':'precise','citations':[cite]},
   {'record_key':'source_fragments/FC','fields_json':json.dumps({'verbatim_excerpt':wrong}),
    'reason':'complete source quotation','citations':[{**cite,'exact_quote':wrong}]}],
   'creations':[],'retirements':[],'findings':[],'finding_resolutions':[
    {'finding_id':fid,'disposition':'resolved','reason':'source precise','citations':[cite],
     'affected_target_keys':['claims/C'],'affected_write_keys':['claims/C'],'impact_reason':'source wording'}
    for fid in packet['unresolved']]}
 ports=DurableSyntheticPorts(store,execution,opus=review,proposal=proposal)
 failed=execute_claim_management(store,request,root=execution,ports=ports)
 assert failed['detail']=='citation is not an exact quote from the complete own source',failed
 parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id));rid=next(iter(parent['latest_rounds'].values()));old=store.get_cvp_group(rid)
 original=binding(execution/rid/'opus.raw.json');slots=deepcopy(parent['call_reservations'])
 direction={'schema_version':VERSION,'round_id':rid,'authorized_by':'owner','authorization_quote':'Copy the uniquely matching bound source instead of repeating a model call',
  'human_content_approval':False,'record_unaccepted_result':True,'source_quote_projection':projection,
  'prior_metadata_sha256':cm.sha256_json({k:v for k,v in old.items()if k not in {'run_id','status','error'}}),
  'original_raw':original}
 path=root/'review-quote-authority.json';path.write_text(json.dumps(direction));authority=binding(path)
 restored=recover_returned_review(store,rid,authorization=authority,unrecorded=True)
 assert store.get_cvp_group(parent['run_id'])['call_reservations']==slots
 proof=body(read(Path(restored)/'engineering-recovery.json'))
 assert not proof['returned_review_passed'] and len(proof['source_quote_corrections'])==1
 result=execute_claim_management(store,request,root=execution,ports=ports)
 assert result['status']=='completed',result
 assert ports.calls==['opus','gpt','opus']
 assert binding(execution/rid/'opus.raw.json')==original
 reviewed=store.get_cvp_group(rid)
 assert not reviewed['opus_approved'] and len(reviewed['findings'])==1
 assert len(reviewed['source_quote_corrections']['opus']['corrections'])==1
 assert store.get_record_state('source_fragments','FC')['payload']['verbatim_excerpt']==text
 assert store.claim_management_qualification('C')['ready']


@pytest.mark.parametrize('mismatch', [None, 'fragment', 'paragraph', 'highlight', 'retained', 'legacy'])
def test_corrected_fragment_only_propagates_to_identical_proposed_bound_highlight(case, mismatch):
 import hashlib
 from backend.pipeline.claim_quote_projection import SPAN_CONTRACT
 _,before,candidate,sources=deepcopy(case)
 old='禱告說求神拯救我們。後來啊，變成祝福的禱告。後來呢，又成爲歡呼，歡呼他們所要歡迎進來的這些人。'
 new=old.replace('又成爲歡呼','又成爲他們所說的歡呼')
 sources['S']['paragraphs']['S0001']['text']=new
 fragment={**before['source_fragments/F']['payload'],'verbatim_excerpt':old}
 anchor={'source_fragment_id':'F','evidence_id':'E','paragraph_key':'S0001',
         'speaker':'professor','stance':'asserted','proposed_highlight':{'status':'proposed','text':old},
         'verbatim_excerpt_sha256':hashlib.sha256(old.encode()).hexdigest()}
 if mismatch=='fragment':anchor['source_fragment_id']='OTHER'
 if mismatch=='paragraph':anchor['paragraph_key']='S0002'
 if mismatch=='highlight':anchor['proposed_highlight']['text']='不同的原文高亮'
 claim={**before['claims/C']['payload'],'occurrences':[{'source_id':'S','anchors':[anchor]}]}
 candidate.records=[cm.source.Row(collection='source_fragments',object_id='F',payload=fragment),
                    cm.source.Row(collection='claims',object_id='C',payload=claim)]
 for d in candidate.dispositions:
  if d.record_key in {'claims/C','source_fragments/F'}:d.action='update'
 if mismatch=='retained':next(d for d in candidate.dispositions if d.record_key=='claims/C').action='retain'
 original=candidate.model_dump(mode='json')
 fixed,changes=project(candidate,sources,contract=CONTRACT if mismatch=='legacy' else SPAN_CONTRACT)
 assert candidate.model_dump(mode='json')==original
 updated=fixed.records[1].payload['occurrences'][0]['anchors'][0]
 expected=deepcopy(anchor)
 if mismatch is None:
  expected['proposed_highlight']['text']=new
  expected['verbatim_excerpt_sha256']=hashlib.sha256(new.encode()).hexdigest()
  assert len([c for c in changes if c['path'][1]==1])==2
 assert updated==expected
 assert fixed.records[1].payload['statement']==claim['statement']
 assert fixed.records[1].payload['evidence_step_ids']==claim['evidence_step_ids']
