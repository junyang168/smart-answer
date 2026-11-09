"""Missing Claim discovery for one completed group; only new nodes are checked."""
from copy import deepcopy
import fcntl
import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from backend.pipeline import claim_flash_common as f, claim_flash_relations as relations
from backend.pipeline import claim_flash_statements as statements, claim_flash_pairs as pairs
from backend.pipeline import claim_update_flash as flash, claim_refresh as c, evidence_refresh as e

VERSION='claim_flash_missing_v1'
PROMPT=(flash.PROMPT+'只檢查本組是否有教授已論證、值得獨立引用卻尚未由Claim承載的重要判斷。'
 '原文、已驗證材料及既有Claim均為依據，不重審或改寫它們。先查同篇既有Claim與本次候選，避免重複。'
 '不是每個判斷句都要建卡；若教授只是說這句話支持上層，沒有另外給此判斷論據，保留Evidence。'
 '另有經文、觀察、例子或推理支持才考慮new_claim；不以至少兩條Evidence作數量門檻。'
 'reason指出不同於重述本句的教授論據；背景、例子細節、未展開預告、設問和口頭強調不升級。'
 'represented須給既有claim_id；retain_evidence可保留承重論據而不建卡。'
 '需要修改既有Claim用needs_update，缺已驗證材料用needs_material，缺來源用needs_source。'
 '只用提供的support_keys和fragment_ids；新Claim的claim_id留空，程式分配ID。'
 'Claim與Evidence可同時承載同一判斷，但原話重述只證明教授說過，不算另外的論據。'
 'answer_question_ids只選直接回答的已提供問題。可輸出空candidates，不能為湊數碎片化。')

class Candidate(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    action:Literal['represented','retain_evidence','new_claim','needs_update','needs_material','needs_source']
    claim_id:str
    statement:str=Field(min_length=1)
    support_keys:list[str]
    fragment_ids:list[str]
    scripture_refs:list[str]
    answer_question_ids:list[str]
    reason:str=Field(min_length=1)

class Discovery(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    candidates:list[Candidate]
    reason:str=Field(min_length=1)


def consume(store,root,records,sources):
    root=Path(root);result=json.loads((root/'result.json').read_text());before=json.loads((root/'before.json').read_text())
    if (result.get('version')!=relations.VERSION or result.get('completion_scope')!='group_claim_relations_only'
            or result.get('pending') or result.get('status') not in {'verified','applied'} or before['sources']!=sources):
        raise ValueError('completed group Claim relations required')
    if result['updates'] or result.get('retiring_keys'):
        intent=json.loads((root/'commit-intent.json').read_text());history=store.get_change_history(intent['change_set_id'])
        if (not result.get('applied') or not history or history['status']!='applied'
                or history['source_kind']!=relations.VERSION or history['fingerprint_sha256']!=intent['fingerprint_sha256']):
            raise ValueError('Claim relation SQL proof differs')
    e.readback(store,result)
    read=set(result['updates'])
    for key,row in before['records'].items():
        if key in result.get('retiring_keys',[]):continue
        read.add(key)
        if key in result['updates']:continue
        now=records.get(key)
        if not now or any(now[k]!=row[k] for k in ['revision','content_sha256','retired']):
            raise ValueError('completed group input changed: '+key)
    return read


def finish(store,root):
    # New identities, reciprocal owners and answer aliases use the existing commit.
    if (root/'verification.json').exists():return statements.finish(store,root)
    return flash.commit_group(store,root)


def run(store,source_id,relation_root,root,ports,*,apply=False,max_new_claims=None):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True);relation_root=Path(relation_root).resolve()
    previous=json.loads((relation_root/'intent.json').read_text());ids=previous['claim_ids']
    group=json.loads((Path(previous['argument_root'])/'intent.json').read_text())['group']
    room=10-len(ids) if max_new_claims is None else max_new_claims
    if not ids or len(set(ids))!=len(ids) or type(room) is not int or not 0<=room<=10-len(ids):
        raise ValueError('one existing group with bounded creation space required')
    with (root/'run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);pin=f.bind_source(store,source_id,root)
        e.save(root/'intent.json',{'version':VERSION,'source':pin,'claim_ids':ids,'group':group,
            'relation_root':str(relation_root),'max_new_claims':room,'models':ports.identity,
            'policy_sha256':e.digest([PROMPT,Discovery.model_json_schema()])})
        if (root/'draft.json').exists():return finish(store,root) if apply else json.loads((root/'draft.json').read_text())
        records,sources=f.load_source(store,source_id)
        if previous['source']!=pin:raise ValueError('missing Claim source differs from completed group')
        read=consume(store,relation_root,records,sources);original=deepcopy(records)
        pool={k for k in group['lower_record_keys'] if k.startswith(('evidence_steps/','observations/'))}
        if not pool<=read:raise ValueError('group lower materials lack completed proof')
        near=set(group['paragraph_keys'])
        fragments={k.split('/',1)[1]:r['payload'] for k,r in records.items() if k.startswith('source_fragments/') and r['payload']['paragraph_key'] in near}
        for key in pool:
            for fid in e.evidence_fragment_ids(records[key]['payload']):fragments[fid]=records['source_fragments/'+fid]['payload']
        read.update('source_fragments/'+fid for fid in fragments)
        peers={k:r['payload'] for k,r in records.items() if k.startswith('claims/')}
        source_index={source_id:{k.split('/',1)[1]:{'statement':p['statement'],'attribution':p.get('attribution')} for k,p in peers.items()
            if p.get('review_status') not in {'superseded','retired'} and not p.get('superseded_by')}}
        # Inactive explicit source members are identity references, never writable.
        aliases=c.source_aliases(records,source_id)
        with store.connect() as conn:
            conn.execute('SET TRANSACTION READ ONLY')
            rows=conn.execute("SELECT object_id,revision,content_sha256,payload,retired_at IS NOT NULL FROM wang_knowledge.objects WHERE collection='claims' AND retired_at IS NOT NULL AND payload @> ANY(%s::jsonb[])",
                ([json.dumps({'occurrences':[{'source_id':a}]}) for a in aliases],)).fetchall()
        for oid,rev,sha,p,retired in rows:
            key='claims/'+oid;records[key]={'revision':rev,'content_sha256':sha,'payload':p,'retired':retired};peers[key]=p
        read.update(peers)
        questions={k:r['payload'] for k,r in records.items() if k.startswith('questions/') and flash.paragraph_keys(k,records)&near}
        read.update(questions)
        for key in questions:read.update('source_fragments/'+fid for fid in e.evidence_fragment_ids(records[key]['payload']))
        shared={'group_id':group['group_id'], 'remaining_creation_space':room,
            'source_paragraphs':{pk:sources[source_id]['paragraphs'][pk]['text'] for pk in group['paragraph_keys']},
            'source_fragments':{fid:{'paragraph_key':p['paragraph_key'],'quote':p['verbatim_excerpt']} for fid,p in fragments.items()},
            'verified_material':{k:{name:p for name,p in records[k]['payload'].items() if name in {'statement','speaker','stance','scripture_refs','source_fragment_id','source_fragment_ids'}} for k in sorted(pool)},
            'existing_claims':{k:{'statement':p['statement'],'attribution':p.get('attribution'),'retired':records[k]['retired'],
                'review_status':p.get('review_status'),'superseded_by':p.get('superseded_by')} for k,p in peers.items()},
            'questions':{k.split('/',1)[1]:{'text':p['text'],'answer_claim_ids':p.get('answer_claim_ids',[])} for k,p in questions.items()}}
        proposed=[];pending=[];dispositions=[];links=[];members=[]
        for _,raw in f.gpt(root/'discovery',ports,'missing_group_claims',[{'job_id':group['group_id']}],Discovery.model_json_schema(),PROMPT,shared=shared,semantic_review=False):
            discovery=Discovery.model_validate(raw)
            for item in discovery.candidates:
                data=item.model_dump();dispositions.append(data)
                if (len(item.support_keys)!=len(set(item.support_keys)) or not set(item.support_keys)<=pool
                        or not set(item.fragment_ids)<=set(fragments) or not set(item.answer_question_ids)<={k.split('/',1)[1] for k in questions}):
                    raise ValueError('missing Claim uses unprovided material or Question')
                if item.action in {'represented','needs_update'}:
                    key='claims/'+item.claim_id
                    if key not in peers:raise ValueError('missing Claim named unprovided existing Claim')
                    if records[key]['retired'] or peers[key].get('review_status') in {'superseded','retired'} or peers[key].get('superseded_by'):
                        pending.append({'candidate':data,'reason':'inactive_identity_requires_handoff'})
                    elif item.action=='needs_update':pending.append({'candidate':data,'reason':'existing_claim_update_required'})
                    continue
                if item.claim_id:raise ValueError('new Claim ID must be allocated by program')
                if item.action=='retain_evidence':continue
                if item.action!='new_claim':pending.append({'candidate':data,'reason':item.action});continue
                if not item.support_keys or not item.fragment_ids:
                    pending.append({'candidate':data,'reason':'new_claim_missing_verified_material'});continue
                if not set(item.fragment_ids)<=set(fid for k in item.support_keys for fid in e.evidence_fragment_ids(records[k]['payload'])):
                    raise ValueError('new Claim fragments are not bound to selected supports')
                existing=next((k for k,p in peers.items() if c.normalized(p['statement'])==c.normalized(item.statement)),None)
                if existing:
                    if records[existing].get('retired') or peers[existing].get('review_status') in {'superseded','retired'} or peers[existing].get('superseded_by'):
                        pending.append({'candidate':data,'reason':'inactive_identity_requires_handoff'})
                    continue
                if len(proposed)>=room:pending.append({'candidate':data,'reason':'creation_space_exceeded'});continue
                alias='new:'+str(len(proposed));key='claims/'+alias
                candidate={'statement':item.statement,'scripture_refs':item.scripture_refs,'reason':item.reason,
                    'evidence_step_ids':[k.split('/',1)[1] for k in item.support_keys if k.startswith('evidence_steps/')],
                    '_observation_ids':[k.split('/',1)[1] for k in item.support_keys if k.startswith('observations/')]}
                proposed.append(candidate);members.append(key)
                records[key]={'payload':{'claim_id':alias,**candidate,'attribution':'professor','occurrences':[{'source_id':source_id,'anchors':[]}]},'retired':False}
                peers[key]=records[key]['payload']
                for oid in candidate['_observation_ids']:
                    rid='candidate-'+e.digest([oid,alias])[:24]
                    records['knowledge_relations/'+rid]={'payload':{'from_id':oid,'to_id':alias,'relation_type':'supports'}}
                if item.answer_question_ids:links.append({'claim':alias,'question_ids':item.answer_question_ids})
        checked=statements.review(records,sources,members,source_id,root/'new-statements',ports)
        records=f.apply_overlay(records,checked['updates']);pending+=checked['pending']
        reviewed_material={k:flash.claim_material(k,records,sources,source_id) for k in members}
        implicit=[{'a':'evidence_steps/'+eid,'b':key} for key in members for eid in records[key]['payload']['evidence_step_ids']]
        obs=[{'a':'observations/'+oid,'b':key} for key in members for oid in records[key]['payload']['_observation_ids']]
        attachment=pairs.review(records,sources,[],obs,root/'new-attachments',ports,kind='attachment',implicit=implicit,adjudicate_proposed_none=True)
        pending+=attachment['pending'];new_lower=attachment['new_relations']
        for removal in attachment['implicit_changes']:
            key=removal['b'];eid=removal['a'].split('/',1)[1]
            if key in members:records[key]['payload']['evidence_step_ids']=[x for x in records[key]['payload']['evidence_step_ids'] if x!=eid]
        for key in list(records):
            if key.startswith('knowledge_relations/') and records[key]['payload']['to_id'] in {k.split('/',1)[1] for k in members}:
                records.pop(key)
        for rel in new_lower:
            records['knowledge_relations/candidate-'+e.digest(rel)[:24]]={'payload':rel}
        affected=[k for k in members if flash.claim_material(k,records,sources,source_id)!=reviewed_material[k]
                  and not any(x.get('object_key')==k for x in pending)]
        if affected:
            postcheck=statements.review(records,sources,affected,source_id,root/'final-support',ports,tasks=['support','scripture'])
            records=f.apply_overlay(records,postcheck['updates']);pending+=postcheck['pending'];checked['final_support']=postcheck
            # A text change here would stale already-reviewed pairs. Keep it as
            # a handoff rather than write those pairs against different text.
            if postcheck['updates']:pending.append({'reason':'final_statement_change_requires_pair_handoff'})
        for index,key in enumerate(members):
            p=records[key]['payload'];proposed[index].update({k:p[k] for k in ['statement','scripture_refs','evidence_step_ids']})
            proposed[index]['_observation_ids']=[r['from_id'] for r in new_lower if r['to_id']==p['claim_id'] and 'observations/'+r['from_id'] in pool and r['relation_type'] in {'supports','explains','corroborates'}]
            if not p['evidence_step_ids'] and not proposed[index]['_observation_ids']:pending.append({'object_key':key,'reason':'new_claim_has_no_final_support'})
        new_pairs,p,discoveries=flash.discover(records,sources,members,set(members)|{'claims/'+cid for cid in ids},root/'new-relations',ports,task='missing_claim_relations') if members else ([],[],[])
        pending+=p
        relationships=pairs.review(records,sources,[],new_pairs,root/'new-claim-pairs',ports,kind='claim',adjudicate_proposed_none=True)
        pending+=relationships['pending']
        selected_questions={qid for link in links for qid in link['question_ids']}
        question_records={k:r for k,r in records.items() if not k.startswith('questions/') or k.split('/',1)[1] in selected_questions}
        question_updates,q_pending=flash.question_links(question_records,sources,members,set(),links,root/'new-answers',ports) if links else ({},[])
        pending+=q_pending
        for key in question_updates:
            for cid in original[key]['payload'].get('answer_claim_ids',[]):
                rk='claims/'+cid
                if rk not in original:raise ValueError('Question reference Claim was not loaded')
                read.add(rk)
        draft={'version':c.VERSION,'execution_mode':VERSION,'source_id':source_id,'claim_ids':ids,'status':'needs_attention' if pending else 'verified',
            'updates':question_updates,'new_claim_candidates':proposed,'new_relation_specs':relationships['new_relations'],
            'new_knowledge_relation_specs':new_lower,'retiring_keys':[],'question_links':[],'pending':pending,'applied':False,
            'dispositions':dispositions,'statement_checks':checked,'attachment_checks':attachment['outcomes'],
            'claim_relation_discovery':discoveries,'claim_relation_checks':relationships['outcomes'],
            'completion_scope':'group_missing_claims_only','claim_qualification':'not_granted'}
        draft['pending']+=flash.new_claim_support_findings(draft,records)
        if draft['pending']:draft['status']='needs_attention'
        if proposed or question_updates:draft['source_claim_index']=source_index
        e.save(root/'before.json',{'records':{k:records[k] if k not in original else original[k] for k in read},'sources':sources})
        e.save(root/'discovery.json',{'dispositions':dispositions})
        e.save(root/'draft.json',draft)
        return finish(store,root) if apply else e.publish(root,draft)


def main():
    import argparse
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
    from backend.pipeline.claim_model_capacity import ClaimModelCapacity
    parser=argparse.ArgumentParser(description='Missing Claims in one completed group, without old-layer review')
    for name in ['source-id','relation-root','capacity-file','root']:parser.add_argument('--'+name,required=True)
    parser.add_argument('--max-new-claims',type=int);parser.add_argument('--database-url');parser.add_argument('--codex-executable');parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();load_dotenv('.env')
    client=CodexSubscriptionClient(model='gpt-6.1-sol',reasoning_effort='medium',max_output_tokens=4000,executable=args.codex_executable)
    ports=c.Models(client,Path(args.root),capacity=ClaimModelCapacity(json.loads(Path(args.capacity_file).read_text())))
    result=run(PostgresKnowledgeStore(args.database_url),args.source_id,args.relation_root,Path(args.root),ports,apply=args.apply,max_new_claims=args.max_new_claims)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
