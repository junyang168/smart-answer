"""Claim statement tasks over completed grouped Evidence; no lower-layer reruns."""
from copy import deepcopy
import fcntl
import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from backend.pipeline import claim_flash_common as f, claim_flash_evidence as eo
from backend.pipeline import claim_update_flash as flash, claim_flash_questions as q
from backend.pipeline import claim_refresh as c, evidence_refresh as e

VERSION='claim_flash_statements_v1'
TASKS={'statement_fidelity':('claim_fidelity','faithful'), 'support':('claim_support','supported'),
       'scripture':('claim_scripture','complete')}
PROMPT=(flash.PROMPT+'只處置本次指定的一項Claim檢查，不修改下層、連接或其他Claim。'
 '合理概括可保留，不為措辭差異改寫。repair只改statement及必要scripture_refs。'
 '若缺來源用needs_source；已有材料缺連接用needs_connection，不以刪弱主張掩蓋。'
 'keep不修改任何欄位，修訂只輸出必要差異。')

class Edit(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    job_id:str
    action:Literal['keep','repair','needs_source','needs_connection']
    statement:str|None=None
    scripture_refs:list[str]|None=None
    reason:str=Field(min_length=1)

class Edits(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    items:list[Edit]


def verify_evidence(root,keys,records,sources):
    """Consume existing judgments and group completion; never create a new lower verdict."""
    root=Path(root).resolve();intent=json.loads((root/'intent.json').read_text())
    sid=next(iter(sources));pin={'source_id':sid,**sources[sid]['file']}
    if intent.get('version')!=eo.VERSION or intent.get('source')!=pin:
        raise ValueError('Evidence proof source/version differs')
    owners={k:g['group_id'] for g in intent['groups']['groups'] for k in g['lower_record_keys'] if k.split('/')[0] in eo.COLLECTIONS}
    for p in (root/'groups').glob('*/*/commit.json'):
        for k in json.loads((p.parent/'verification.json').read_text()).get('updates',{}):owners.setdefault(k,p.parent.parent.name)
    receipts=[]
    for key in sorted(keys):
        if records[key]['payload'].get('review_status') in {'human_hold','rejected','superseded'}:
            raise ValueError('required Evidence has a current hold: '+key)
        gid=owners.get(key)
        completed=[p for p in (root/'groups'/str(gid)).glob('*/result.json')
                   if (v:=json.loads(p.read_text())).get('status') in {'verified','applied'}
                   and not v.get('pending') and (v.get('applied') or not v.get('updates'))]
        if not completed:raise ValueError('required Evidence group not completed: '+key)
        fp=eo.fingerprint(key,records[key]['payload'],eo.material(key,records,sources))
        path=root/'accepted'/(fp+'.json')
        interpretation_added=False
        if not path.exists():
            # A legacy lower judgment still proves its unchanged statement and
            # literal fragments. Adding source context to an upper task does
            # not retroactively claim the lower reviewer saw that context.
            # Never discard a confirmation that the lower review DID consume.
            pks={records['source_fragments/'+fid]['payload']['paragraph_key'] for fid in e.evidence_fragment_ids(records[key]['payload'])}
            previously_bound=any(set(entry['paragraph_sha256']) & pks for p in completed
                for entry in json.loads(p.read_text()).get('human_source_guard',{}).get('entries',[]))
            if not previously_bound:
                old=eo.material(key,records,sources,decisions=[])
                fp=eo.fingerprint(key,records[key]['payload'],old);path=root/'accepted'/(fp+'.json')
                interpretation_added=True
        if not path.exists():raise ValueError('required Evidence content has no matching accepted result: '+key)
        proof=json.loads(path.read_text());decision=json.loads(Path(proof['decision_path']).read_text())
        if proof['fingerprint']!=fp or proof['decision_sha256']!=e.digest(decision):
            raise ValueError('Evidence decision binding differs: '+key)
        receipts.append({'object_key':key,'group_id':gid,'fingerprint':fp,'proof_path':str(path),'proof_sha256':e.digest(proof),
                         **({'source_interpretation_added_at_claim_layer':True,'lower_reviewer_saw_new_confirmation':False} if interpretation_added else {})})
    return receipts


def judgment_hash(material,question):
    value=deepcopy(material)
    if question!='scripture':value['claim'].pop('scripture_refs',None)
    return e.digest(value)


def finish(store,root):
    result=json.loads((root/'verification.json').read_text());before=json.loads((root/'before.json').read_text())
    f.source_unchanged(before['sources'])
    committed=(root/'commit.json').exists()
    if (root/'commit-intent.json').exists():
        history=store.get_change_history(json.loads((root/'commit-intent.json').read_text())['change_set_id'])
        committed=committed or bool(history and history['status']=='applied')
    for key,row in before['records'].items():
        if committed and (key in result['updates'] or key in result.get('retiring_keys',[])):continue
        current=store.get_record_state(*key.split('/',1))
        if not current or (current['revision'],current['content_sha256'],current['retired'])!=(row['revision'],row['content_sha256'],row['retired']):
            raise ValueError('Claim statement inputs changed: '+key)
    if result.get('deferred_legacy_ai_commit') and not committed:
        return e.publish(root,result)
    return e.commit_verified(store,root)


def review(records,sources,targets,source_id,root,ports,*,tasks=None):
    """Review only these statements, including not-yet-committed new candidates."""
    working=deepcopy(records)
    materials={k:flash.claim_material(k,records,sources,source_id) for k in targets}
    checks=TASKS if tasks is None else {k:TASKS[k] for k in tasks}
    initial={k:f.jev(root/'precheck',ports,materials[k],{name:q.claim(with_references=bool(materials[k].get('claim_references')))[name] for name in checks}) for k in targets}
    outcomes=[];pending=[];settled={k:{} for k in targets}
    for attempt in range(3):
        for question,(task,passing) in checks.items():
            jobs=[];states={}
            for key in targets:
                if any(x['object_key']==key for x in pending):continue
                state=flash.claim_material(key,working,sources,source_id);states[key]=state
                stamp=judgment_hash(state,question)
                if settled[key].get(question)==stamp:continue
                verdict=(initial[key][question] if stamp==judgment_hash(materials[key],question)
                         else f.jev(root/'precheck',ports,state,{question:q.claim(with_references=bool(state.get('claim_references')))[question]})[question])
                if verdict!=passing:
                    jobs.append({'job_id':key,'question':question,'claim':working[key]['payload'],'material':state,'jev':verdict})
                else:
                    settled[key][question]=stamp
                    outcomes.append({'object_key':key,'task':task,'basis':'jev_precheck','verdict':verdict,'input_sha256':e.digest(state)})
            for batch,raw in f.gpt(root/'dispositions',ports,task,jobs,Edits.model_json_schema(),PROMPT,semantic_review=False):
                edits=Edits.model_validate(raw).items
                f.exactly([x.model_dump() for x in edits],[j['job_id'] for j in batch],'job_id')
                for edit in edits:
                    key=edit.job_id
                    outcomes.append({'object_key':key,'task':task,'basis':'gpt_disposition','action':edit.action,'reason':edit.reason,'input_sha256':e.digest(states[key])})
                    if edit.action in {'needs_source','needs_connection'}:
                        pending.append({'object_key':key,'layer':'source' if edit.action=='needs_source' else 'claim_connections','reason':edit.reason})
                        continue
                    if edit.action=='keep':
                        settled[key][question]=judgment_hash(states[key],question)
                        continue
                    post=deepcopy(working[key]['payload'])
                    if edit.statement is not None:
                        if not edit.statement.strip():raise ValueError('empty Claim statement')
                        post['statement']=edit.statement
                    if edit.scripture_refs is not None:post['scripture_refs']=edit.scripture_refs
                    if post!=working[key]['payload']:
                        working[key]['payload']=flash.current_claim_payload(post)
                    # GPT's disposition is final for its own task, not a Jev-vote loop.
                    settled[key][question]=judgment_hash(flash.claim_material(key,working,sources,source_id),question)
        if all(any(x['object_key']==k for x in pending) or all(settled[k].get(q)==judgment_hash(flash.claim_material(k,working,sources,source_id),q) for q in checks) for k in targets):break
    for key in targets:
        if not any(x['object_key']==key for x in pending) and any(settled[key].get(q)!=judgment_hash(flash.claim_material(key,working,sources,source_id),q) for q in checks):
            pending.append({'object_key':key,'layer':'claim_statement','reason':'bounded_task_passes_exhausted'})
    updates={k:working[k]['payload'] for k in targets if working[k]['payload']!=records[k]['payload']}
    return {'updates':updates,'pending':pending,'outcomes':outcomes}


def run(store,source_id,claim_ids,evidence_root,root,ports,*,apply=False):
    if not claim_ids or len(claim_ids)>10 or len(set(claim_ids))!=len(claim_ids):
        raise ValueError('one group of one to ten distinct Claim IDs required')
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    with (root/'run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        pin=f.bind_source(store,source_id,root)
        e.save(root/'intent.json',{'version':VERSION,'claim_ids':claim_ids,'source':pin,
            'evidence_root':str(Path(evidence_root).resolve()),'models':ports.identity,
            'max_task_passes':3,'policy_sha256':e.digest([PROMPT,q.claim(),Edits.model_json_schema(),flash.CLAIM_REFERENCE_CONTRACT])})
        if (root/'verification.json').exists():return finish(store,root) if apply else json.loads((root/'verification.json').read_text())
        plan=json.loads((Path(evidence_root)/'intent.json').read_text())['groups']
        group=next((g for g in plan['groups'] if set(claim_ids)<=set(g.get('claim_ids',[]))),None)
        if group is None:
            records,sources=f.load_source(store,source_id);c.fetch_claims(store,claim_ids)
            group={'paragraph_keys':sorted({pk for cid in claim_ids for pk in flash.paragraph_keys('claims/'+cid,records)}),
                   'lower_record_keys':['evidence_steps/'+eid for cid in claim_ids for eid in records['claims/'+cid]['payload'].get('evidence_step_ids',[])],
                   'reference_record_keys':[]}
        records,sources=f.load_group_source(store,source_id,group);c.fetch_claims(store,claim_ids)
        targets=['claims/'+cid for cid in claim_ids]
        if any(k not in records or not f.belongs_to_source(records[k]['payload'],records,source_id) for k in targets):
            raise ValueError('Claim belongs to another source')
        markers=f.legacy_ai_findings(store,records,claim_ids,ports)
        if markers:e.save(root/'legacy-ai-findings.json',markers)
        working=deepcopy(records);read=set(targets)|{'source_documents/'+source_id};materials={};needed=set()
        for key in targets:
            materials[key]=flash.claim_material(key,records,sources,source_id)
            needed.update(x['object_key'] for x in materials[key]['evidence'])
            context=materials[key].get('argument_context',{})
            needed.update(x['object_key'] for x in context.get('records',[]))
            read.update(x['object_key'] for x in context.get('relations',[]))
            for ref in materials[key].get('claim_references',[]):
                needed.update(ref['supporting_material_keys']);read.add(ref['object_key'])
                read.update('source_fragments/'+fid for fid in ref['source_fragment_ids'])
                read.update(ref['supporting_relation_keys'])
                read.update(r['object_key'] for r in ref['relations'])
        if any(not materials[k]['evidence'] for k in targets):raise ValueError('Claim has no verified lower support')
        proof=verify_evidence(evidence_root,needed,records,sources)
        read.update(needed)
        for key in needed:read.update('source_fragments/'+fid for fid in e.evidence_fragment_ids(records[key]['payload']))
        # Explicit typed attachments are part of the actual Claim material.
        for key,row in records.items():
            if key.startswith('knowledge_relations/') and row['payload']['to_id'] in claim_ids:
                read.add(key)
        for key in targets:
            for occurrence in records[key]['payload'].get('occurrences',[]):
                read.update('source_fragments/'+a['source_fragment_id'] for a in occurrence.get('anchors',[]) if a.get('source_fragment_id'))
        e.save(root/'before.json',{'records':{k:records[k] for k in read if k in records},'sources':sources})
        e.save(root/'consumed-evidence.json',proof)
        reviewed=review(records,sources,targets,source_id,root,ports)
        updates,pending,outcomes=(reviewed[k] for k in ['updates','pending','outcomes'])
        f.source_unchanged(sources)
        result={'version':VERSION,'source_id':source_id,'claim_ids':claim_ids,'status':'needs_attention' if pending else 'verified',
            'updates':updates,'new_fragments':{},'pending':pending,'outcomes':outcomes,'applied':False,
            'claim_qualification':'not_granted','completion_scope':'claim_statement_tasks_only','commit_reason':'Claim statement dispositions using completed Evidence; no lower review or relation writes'}
        if markers:result['deferred_legacy_ai_commit']=True
        e.save(root/'verification.json',result)
        return finish(store,root) if apply else e.publish(root,result)


def main():
    import argparse
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
    from backend.pipeline.claim_model_capacity import ClaimModelCapacity
    parser=argparse.ArgumentParser(description='Claim statement tasks only; consume completed Evidence groups')
    for name in ['source-id','claim-ids','evidence-root','capacity-file','root']:parser.add_argument('--'+name,required=True)
    parser.add_argument('--database-url');parser.add_argument('--codex-executable');parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();load_dotenv('.env')
    client=CodexSubscriptionClient(model='gpt-6.1-sol',reasoning_effort='medium',max_output_tokens=4000,executable=args.codex_executable)
    ports=c.Models(client,Path(args.root),capacity=ClaimModelCapacity(json.loads(Path(args.capacity_file).read_text())))
    result=run(PostgresKnowledgeStore(args.database_url),args.source_id,json.loads(Path(args.claim_ids).read_text()),
        args.evidence_root,Path(args.root),ports,apply=args.apply)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
