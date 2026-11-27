"""A projection defect reuses the exact GPT answer, never the rejected review."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from backend.pipeline import claim_management as cm,claim_management_batch as batch,claim_management_intake as intake
from backend.pipeline import claim_quote_projection as quotes
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_commit import binding
from backend.pipeline.source_reconciliation_runner import body,read
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres
from backend.tests.test_claim_management_intake import claims,pass_review
from backend.tests.test_claim_metadata_projection import DurableSyntheticPorts
from backend.tests.test_claim_quote_span import seed_source


@pytest.mark.parametrize('fault',[None,'new_fragment','round','source','candidate','budget','unknown'])
def test_saved_gpt_highlight_repair_requires_exact_readset_and_new_full_review(claims,monkeypatch,fault):
 store,root=claims;literal=seed_source(store);wrong=literal.replace('原文的證據','原文證據')
 prepared=intake.prepare(['C'],root,instruction='Refresh C completely',store=store)
 request,execution=next(iter(batch.load(prepared['manifest']['path'],root/'batch',store=store)[1].values()))
 cite={'source_id':'S','paragraph_key':'S0001','exact_quote':literal}
 def finding(reason):return {'target_key':'claims/C','category':'mapping','component':'occurrences',
  'reason':reason,'acceptance_condition':'The bound highlight must match its exact source fragment.',
  'citations':[cite],'affected_target_keys':['claims/C'],'affected_write_keys':[],
  'impact_reason':'own Claim source quotation'}
 def review(packet):
  result=pass_review(packet)
  rows={r['collection']+'/'+r['object_id']:r['payload'] for r in packet['candidate']['records']}
  if not rows:result['findings']=[finding('Preserve complete source teaching.')]
  else:
   fragment=next(v for k,v in rows.items()if k.startswith('source_fragments/'));anchor=rows['claims/C']['occurrences'][0]['anchors'][0]
   result['prior_findings']=[{'finding_id':fid,'disposition':'resolved' if anchor['proposed_highlight']['text']==fragment['verbatim_excerpt'] else 'persistent',
    'reason':'The full candidate is reviewed; highlight and fragment must agree.','citations':[cite],
    'affected_target_keys':['claims/C'],'affected_write_keys':['claims/C'],
    'impact_reason':'own quotation consistency'}for fid in packet['unresolved']]
  return result
 def proposal(packet):
  fields={'statement':literal,'occurrences':[{'source_id':'S','anchors':[{
   'evidence_id':'EC','source_fragment_id':'FC','paragraph_key':'S0001',
   'proposed_highlight':{'text':wrong}}]}]}
  edits=[('claims/C',fields),('source_fragments/FC',{'verbatim_excerpt':wrong})];creations=[]
  if fault=='new_fragment':
   fields['occurrences'][0]['anchors'][0]['source_fragment_id']='NEW-F'
   edits=[('claims/C',fields),('evidence_steps/EC',{'source_fragment_ids':['NEW-F']})]
   creations=[{'collection':'source_fragments','object_id':'NEW-F','fields_json':json.dumps({
    'source_id':'S','paragraph_key':'S0001','verbatim_excerpt':wrong}),'reason':'new exact fragment','citations':[cite]}]
  return {'changes':[{'record_key':key,'fields_json':json.dumps(value),'reason':'exact own teaching','citations':[cite]}
   for key,value in edits],
   'creations':creations,'retirements':[],'findings':[],'finding_resolutions':[
    {'finding_id':fid,'disposition':'resolved','reason':'complete source precision','citations':[cite],
     'affected_target_keys':['claims/C'],'affected_write_keys':['claims/C'],'impact_reason':'own source'}for fid in packet['unresolved']]}
 original_project=quotes.project
 def old_projection(candidate,sources,**kwargs):
  fixed,changes=original_project(candidate,sources,**kwargs);value=fixed.model_dump(mode='json')
  for c in changes:
   if 'source_fragment_id' in c:
    node=value
    for part in c['path'][:-1]:node=node[part]
    node[c['path'][-1]]=c['before']
  return cm.Candidate.model_validate(value),[c for c in changes if 'source_fragment_id'not in c]
 ports=DurableSyntheticPorts(store,execution,opus=review,proposal=proposal)
 with monkeypatch.context()as patch:
  patch.setattr(quotes,'project',old_projection)
  failed=execute_claim_management(store,request,root=execution,ports=ports)
 assert failed['reason']=='disagreement',failed
 metadata=lambda x:{k:v for k,v in x.items()if k not in {'run_id','status','error'}}
 parent=metadata(store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id)))
 rid=next(iter(parent['latest_rounds'].values()));value=metadata(store.get_cvp_group(rid))
 if fault=='round':value['candidate']['records'][0]['payload']['statement']='unrelated teaching'
 snapshot=root/'saved-round.json';snapshot.write_text(json.dumps(value))
 policy=cm.Policy.model_validate(parent['policy']);policy.policy_id+='-saved-highlight';policy.initial_candidate='existing'
 policy.budget.gpt_calls=1;policy.budget.opus_calls=1;policy.budget.max_revisions=1
 if fault=='budget':policy.budget.opus_calls=cm.Policy.model_validate(parent['policy']).budget.opus_calls-1
 policy_file=root/'repair-policy.json';policy_file.write_text(policy.model_dump_json())
 req=request.model_copy(deep=True);req.predecessor_action_id=request.action_id;req.action_id+='-saved-highlight';req.policy=cm.source.FileBinding.model_validate(binding(policy_file))
 auth=json.loads(Path(request.authorization.path).read_text())
 auth.update(request=req.model_dump(mode='json',exclude={'authorization','policy'}),policy=req.policy.model_dump(mode='json'))
 from backend.api.canonical_repository.claim_identity_allocation import reserve
 recovery=root/'highlight-repair'
 auth['claim_creation_allocations']={'S':reserve(store,action_id=req.action_id,source_id='S',target_id='C',root=recovery)}
 if fault=='unknown':next(v for v in parent['call_reservations'].values()if v['role']=='opus')['state']='started'
 auth[quotes.REPAIR]={'predecessor_action_id':request.action_id,'prior_metadata_sha256':cm.sha256_json(parent),
   'round':binding(snapshot),'authorization_quote':'Fix the proven mechanical projection defect and reuse the saved GPT response.'}
 auth_file=root/'repair-authority.json';auth_file.write_text(json.dumps(auth));req.authorization=cm.source.FileBinding.model_validate(binding(auth_file))
 if fault in {'round','unknown','budget'}:
  with pytest.raises(ValueError):cm.continuation_budget(parent,req,policy)
  return
 account=cm.continuation_budget(parent,req,policy)
 assert account['spent_generations']=={'gpt':1,'opus':2,'gemini':0}
 assert not account[quotes.REPAIR]['new_budget'] and not account[quotes.REPAIR]['review_approval_reused']
 if fault=='candidate':
  changed=deepcopy(value);changed['candidate']['records'][0]['payload']['statement']='external SQL round drift'
  with store.connect()as conn:conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s',(json.dumps(changed),rid))
 if fault=='source':
  changed=deepcopy(store.get_record_state('claims','C')['payload']);changed['statement']='external content drift'
  store.apply_plan(store.plan_package({'package_id':'external-change','claims':[changed]}))
 second=DurableSyntheticPorts(store,recovery,opus=review,
   proposal=lambda _:pytest.fail('mechanical correction must not invoke GPT again'))
 raw=binding(execution/rid/'gpt.raw.json');slots=deepcopy(parent['call_reservations'])
 if fault in {'candidate','source'}:
  with pytest.raises(Exception,match='drift|changed|original'):
   execute_claim_management(store,req,root=recovery,ports=second)
  assert second.calls==[]
  return
 done=execute_claim_management(store,req,root=recovery,ports=second)
 assert done['status']=='completed',done
 assert second.calls==['opus'] and ports.calls==['opus','gpt','opus']
 assert binding(execution/rid/'gpt.raw.json')==raw
 assert store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))['call_reservations']==slots
 assert store.get_record_state('claims','C')['payload']['occurrences'][0]['anchors'][0]['proposed_highlight']['text']==literal
 assert store.claim_management_qualification('C')['ready']


def test_initial_review_already_sees_both_calibrated_quote_and_duplicate_highlight(claims):
 store,root=claims;literal=seed_source(store);wrong=literal.replace('原文的證據','原文證據')
 fragment=deepcopy(store.get_record_state('source_fragments','FC')['payload']);fragment['verbatim_excerpt']=wrong
 claim=deepcopy(store.get_record_state('claims','C')['payload']);claim['occurrences']=[{'source_id':'S','anchors':[{
  'source_fragment_id':'FC','evidence_id':'EC','paragraph_key':'S0001','proposed_highlight':{'text':wrong}}]}]
 store.apply_plan(store.plan_package({'package_id':'old-duplicate-quotes','claims':[claim],'source_fragments':[fragment]}))
 prepared=intake.prepare(['C'],root,instruction='Review the complete Claim and own source.',store=store)
 request,execution=next(iter(batch.load(prepared['manifest']['path'],root/'batch',store=store)[1].values()))
 def review(packet):
  rows={r['collection']+'/'+r['object_id']:r['payload']for r in packet['candidate']['records']}
  assert rows['source_fragments/FC']['verbatim_excerpt']==literal
  assert rows['claims/C']['occurrences'][0]['anchors'][0]['proposed_highlight']['text']==literal
  return pass_review(packet)
 ports=DurableSyntheticPorts(store,execution,opus=review,proposal=lambda _:pytest.fail('no semantic finding requires GPT'))
 result=execute_claim_management(store,request,root=execution,ports=ports)
 assert result['status']=='completed',result
 assert ports.calls==['opus']
 assert store.get_record_state('claims','C')['payload']['occurrences'][0]['anchors'][0]['proposed_highlight']['text']==literal
