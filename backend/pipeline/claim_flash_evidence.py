"""Grouped Evidence/Observation refresh using verified fragments, never raw-source lookup."""
from copy import deepcopy
import fcntl
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from backend.api.canonical_repository.knowledge_models import KNOWLEDGE_COLLECTIONS
from backend.pipeline import claim_flash_common as f, claim_flash_components as components
from backend.pipeline import claim_flash_coverage as coverage, claim_flash_questions as q
from backend.pipeline import claim_flash_source as source, claim_flash_source_fill as fill
from backend.pipeline import evidence_refresh as e

VERSION = 'claim_flash_grouped_evidence_v1'
COLLECTIONS = {'evidence_steps','observations'}
FIELDS = ('statement','speaker','stance','attribution','scripture_refs')
PROMPT = (
    '只處置Evidence/Observation本身，不審Claim、EvidenceStep論證、問題答案或節點關係。'
    'sources只包含已驗證的SourceFragments；不查整篇原文、不補未掛片段作為自身支持。'
    'Jev只提供線索；GPT可修正或有據保留。檢查statement全部意思、否定、條件、程度、模態、'
    '發言者及採納立場；合理同義概括可保留，不為吹毛求疵增加限定。保存教授引用的經文及反方。'
    '經文規範化節次不要求片段逐字念出；缺上下文時保留並交接，不因局部未重述就刪除既有經文。'
    '只輸出必要欄位修改及簡短理由。keep不改記錄。缺少來源用needs_source交回原文層，'
    '不得改片段、連接、owners、審閱狀態或資格。每個job_id恰好一次。')
COVERAGE_PROMPT = PROMPT + (
    '本次只處置指定重要意思是否已保存，不全面重審參考記錄。優先enrich既有Evidence/Observation，'
    '必要才create一筆，不把口頭語、預告或重複拆成新卡，不新建Claim。'
    'enrich只准writable_keys；其他組記錄需要修改用handoff，不能為繞過範圍重複create。'
    'fragment_ids只能選所給sources，須完整覆蓋候選原話，不要求採用全部候選ID。'
    'enrich只能使用該記錄已掛載的片段；需要改來源綁定用needs_source，不繞過原文層。'
    '本組多個候選若enrich同一記錄，每項須給相同、完整保存全部相關候選的最終表述，不能互相覆蓋。'
    'keep表示已有保存或確定不必另存；需要更多原話用needs_source。')


class Edit(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    job_id: str
    action: Literal['repair','keep','needs_source']
    statement: str | None = None
    speaker: str | None = None
    stance: str | None = None
    attribution: str | None = None
    scripture_refs: list[str] | None = None
    reason: str = Field(min_length=1)


class Edits(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    items: list[Edit]


class Missing(Edit):
    action: Literal['enrich','create','keep','needs_source','handoff']
    record_key: str
    record_type: Literal['evidence_steps','observations']
    fragment_ids: list[str]


class MissingBundle(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    job_id: str
    items: list[Missing]


class MissingEdits(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    items: list[MissingBundle]


def visible(payload):
    return {k:payload[k] for k in FIELDS if k in payload}


def reference_visible(payload):
    # Position stores its judgment in title, not statement. It remains read-only.
    return {**visible(payload), **({'title':payload['title']} if 'title' in payload else {})}


def material(key, records, sources):
    payload = records[key]['payload']
    value = e.material({**payload,'evidence_step_id':key},records,sources)
    # Deliberately do not use f.value: no between-fragment expansion or nearby paragraphs.
    return {k:value[k] for k in ('fragments','original_text_excerpts')}


def fingerprint(key, payload, value):
    return e.digest({'key':key,'record':visible(payload),'fragments':value['fragments']})


def patch_for(key, payload, edit):
    if edit.action == 'keep':
        return {}
    patch = {k:getattr(edit,k) for k in FIELDS if getattr(edit,k) is not None}
    if 'statement' in patch and not patch['statement'].strip():
        raise ValueError('empty Evidence statement')
    if key.startswith('observations/'):
        patch = {k:v for k,v in patch.items() if k not in {'speaker','stance'} or k in payload}
    KNOWLEDGE_COLLECTIONS[key.split('/')[0]][0].model_validate({**payload,**patch})
    return {k:v for k,v in patch.items() if payload.get(k) != v}


def inventory(previous, sources, source_id):
    """Consume the prior source-only inventory; no fresh extraction or whole-sermon model input."""
    previous = Path(previous).resolve()
    intent = json.loads((previous/'intent.json').read_text())
    if intent.get('source_id') != source_id or intent.get('source') != sources[source_id]['file']:
        raise ValueError('coverage inventory source/version differs')
    rows = {}; paragraphs = sources[source_id]['paragraphs']
    for request_path in sorted((previous/'source-inventory'/'gpt').glob('*/request.json')):
        req = json.loads(request_path.read_text())
        if req['input']['task'] != 'source_preservation_inventory' or req['system'] != coverage.INVENTORY_PROMPT:
            raise ValueError('source inventory extraction contract differs')
        answer_path = request_path.parent/'response.json'
        if not answer_path.exists():
            raise ValueError('source inventory reply unresolved; no automatic extraction')
        answers = coverage.Inventories.model_validate_json(answer_path.read_text()).model_dump()['items']
        f.exactly(answers,[j['job_id'] for j in req['input']['jobs']],'job_id')
        for job in req['input']['jobs']:
            if any(pk not in paragraphs or text != paragraphs[pk]['text'] for pk,text in job['paragraphs'].items()):
                raise ValueError('source inventory paragraphs differ')
        for row in answers:
            job = next(j for j in req['input']['jobs'] if j['job_id']==row['job_id'])
            for unit in row['units']:
                coverage.literal(unit,{pk:paragraphs[pk] for pk in job['paragraphs']},[])
                rows[e.digest(unit)] = unit
    if not rows and not list((previous/'source-inventory'/'gpt').glob('*/response.json')):
        raise ValueError('missing source inventory; run the source stage first')
    return list(rows.values())


def decompose(root, ports, statements):
    cached = {}; needed = {}
    for text in sorted(set(statements.values())):
        alias = 'T'+e.digest(text)
        path = root/'component-cache'/(alias+'.json')
        if path.exists():cached[text]=json.loads(path.read_text())
        else:needed[alias]=text
    if needed:
        result = components.decompose(root/'components'/e.digest(needed),ports,needed)
        for alias,text in needed.items():
            cached[text]=result[alias];e.save(root/'component-cache'/(alias+'.json'),result[alias])
    return {key:deepcopy(cached[text]) for key,text in statements.items()}


def group_records(group, records, target_keys=()):
    pks = set(group['paragraph_keys'])
    pool = {k:r['payload'] for k,r in records.items() if k.startswith('source_fragments/') and not r.get('retired')
            and r['payload'].get('paragraph_key') in pks}
    for key in target_keys:
        for fid in e.evidence_fragment_ids(records[key]['payload']):
            fk='source_fragments/'+fid
            if fk in records and not records[fk].get('retired'):pool[fk]=records[fk]['payload']
    keys = set(group.get('lower_record_keys',[])) | set(group.get('reference_record_keys',[]))
    keys |= {k for k,r in records.items() if k.split('/')[0] in coverage.COLLECTIONS and
             any('source_fragments/'+fid in pool for fid in e.evidence_fragment_ids(r['payload']))}
    return pool, {k:records[k]['payload'] for k in sorted(keys) if k in records
                  and k.split('/')[0] in coverage.COLLECTIONS and not records[k].get('retired')}


def coverage_state(catalog):
    aliases={key:'R'+e.digest(key)[:8] for key in catalog}
    if len(set(aliases.values()))!=len(aliases):raise ValueError('Evidence catalogue alias collision')
    return {'records':{aliases[k]:{'statement':p.get('statement',p.get('title','')),
        'scripture_refs':p.get('scripture_refs',[]),'record_type':k.split('/')[0],'attribution':p.get('attribution'),
        **{field:p[field] for field in ('speaker','stance') if p.get(field)}} for k,p in catalog.items()}}


def run_group(store, records, sources, group, targets, units, root, ports, previous, *, apply):
    sid = next(iter(sources)); working = dict(records); updates = {}; outcomes = []; pending = []; values = {}; ready = {}
    for key in targets:
        try:
            values[key]=material(key,working,sources)
        except (KeyError,ValueError) as exc:
            pending.append({'object_key':key,'layer':'source','reason':str(exc)});continue
        fp=fingerprint(key,working[key]['payload'],values[key]); accepted=root/'accepted'/(fp+'.json')
        if accepted.exists():
            proof=json.loads(accepted.read_text());decision=json.loads(Path(proof['decision_path']).read_text())
            if proof['decision_sha256']!=e.digest(decision) or proof['fingerprint']!=fp:
                raise ValueError('Evidence acceptance proof differs')
            outcomes.append({'object_key':key,'basis':'reused_accepted_input'});continue
        path=root/'decisions'/fp/'result.json'
        if path.exists():ready[key]=json.loads(path.read_text())
    todo={k:working[k]['payload']['statement'] for k in values if k not in ready and not any(x.get('object_key')==k for x in outcomes)}
    parts=decompose(root,ports,todo);jobs=[]
    for key in todo:
        answers=components.check(root/'fidelity',ports,key.split('/')[0],visible(working[key]['payload']),values[key],parts[key],fragment_only=True)
        if components.needs_disposition(answers):
            jobs.append({'job_id':key,'record':visible(working[key]['payload']),'sources':values[key]['fragments'],
                         'components':parts[key],'jev':answers})
        else:ready[key]={'patch':{},'basis':'jev_precheck','reason':'本層逐項檢查無異議。'}
    for batch,raw in f.gpt(root/'fidelity-edits',ports,'evidence_fidelity_repair',jobs,Edits.model_json_schema(),PROMPT,semantic_review=False):
        replies=Edits.model_validate(raw).items;f.exactly([x.model_dump() for x in replies],[j['job_id'] for j in batch],'job_id')
        for edit in replies:
            if edit.action=='needs_source':
                pending.append({'object_key':edit.job_id,'layer':'source','reason':edit.reason});continue
            ready[edit.job_id]={'patch':patch_for(edit.job_id,working[edit.job_id]['payload'],edit),
                               'basis':'gpt_'+edit.action,'reason':edit.reason}
    for key,decision in ready.items():
        p=working[key]['payload'];fp=fingerprint(key,p,values[key]);path=root/'decisions'/fp/'result.json'
        e.save(path,decision)
        if decision['patch'] and p.get('review_status') in f.PROTECTED:
            pending.append({'object_key':key,'layer':'evidence','reason':'protected_record'});continue
        post={**p,**decision['patch']}
        if post!=p:updates[key]=post;working[key]={**working[key],'payload':post}
        accepted=root/'accepted'/(fingerprint(key,post,values[key])+'.json')
        proof={'fingerprint':fingerprint(key,post,values[key]),'decision_path':str(path.resolve()),'decision_sha256':e.digest(decision)}
        if not accepted.exists():e.save(accepted,proof)
        outcomes.append({'object_key':key,'basis':decision['basis'],'reason':decision['reason']})
    if pending:units=[]  # Do not spend coverage calls before the group's lower prerequisites hold.
    pool,catalog=group_records(group,working,targets);valid={}
    for key,p in pool.items():
        try:
            e.material({'evidence_step_id':key,'statement':'','source_fragment_ids':[p['fragment_id']]},working,sources)
            valid[key]=p
        except (KeyError,ValueError):pass  # Invalid fragments are never offered as new support.
    state=coverage_state(catalog)
    def coverage_fingerprint(current):
        return e.digest({'group_id':group['group_id'],'units':units,'state':current,
            'fragments':{k:{field:p.get(field) for field in ('fragment_id','paragraph_key','verbatim_excerpt','source_sha256')}
                         for k,p in valid.items()}})
    accepted_coverage=root/'coverage-accepted'/(coverage_fingerprint(state)+'.json')
    if accepted_coverage.exists():
        proof=json.loads(accepted_coverage.read_text());decision=json.loads(Path(proof['decision_path']).read_text())
        if proof['decision_sha256']!=e.digest(decision):raise ValueError('coverage acceptance proof differs')
        outcomes.append({'group_id':group['group_id'],'basis':'reused_coverage_postimage'})
        units=[]
    jobs=[];specs={};covered={}
    for i,unit in enumerate(units):
        token='U'+str(i)
        ids=fill.covered_by(unit,sources[sid]['paragraphs'][unit['paragraph_key']],valid)
        if not ids:
            pending.append({'unit':unit,'layer':'source','reason':'important_unit_not_covered_by_verified_fragments'});continue
        covered[token]=[k.split('/',1)[1] for k in ids];specs[token]=q.coverage_unit(unit['statement'])
    # Bounded per-question batches, sharing the same compact group catalogue.
    answers={};batch={}
    for token,spec in specs.items():
        trial={**batch,token:spec}
        if batch and len(json.dumps({'model':e.MODEL,'state':state,'questions':trial},ensure_ascii=False).encode())>f.JEV_BYTES:
            answers.update(f.jev(root/'coverage'/group['group_id'],ports,state,batch,
                reuse_roots=[Path(previous)/'coverage'/group['group_id']]));batch={}
        batch[token]=spec
    if batch:answers.update(f.jev(root/'coverage'/group['group_id'],ports,state,batch,
        reuse_roots=[Path(previous)/'coverage'/group['group_id']]))
    for i,unit in enumerate(units):
        token='U'+str(i)
        if token in answers and answers[token]!='covered':jobs.append({'job_id':token,'unit':unit,
            'required_fragment_ids':covered[token],'jev':answers[token]})
    coverage_updates={};coverage_proofs=[]
    grouped=[{'job_id':group['group_id'],'candidates':jobs}] if jobs else []
    for batch,raw in f.gpt(root/'coverage-edits',ports,'evidence_coverage_repair',grouped,MissingEdits.model_json_schema(),COVERAGE_PROMPT,
        shared={'records':{k:reference_visible(p) for k,p in catalog.items()},'writable_keys':targets,
                'sources':{p['fragment_id']:{'paragraph_key':p['paragraph_key'],'quote':p['verbatim_excerpt']} for p in valid.values()}},semantic_review=False):
        bundles=MissingEdits.model_validate(raw).items;f.exactly([x.model_dump() for x in bundles],[j['job_id'] for j in batch],'job_id')
        edits=bundles[0].items;f.exactly([x.model_dump() for x in edits],[j['job_id'] for j in jobs],'job_id')
        for edit in edits:
            if edit.action=='keep':outcomes.append({'unit_id':edit.job_id,'basis':'gpt_keep','reason':edit.reason});continue
            if edit.action in {'needs_source','handoff'}:
                pending.append({'unit_id':edit.job_id,'layer':'source' if edit.action=='needs_source' else 'handoff','reason':edit.reason});continue
            job=next(j for j in jobs if j['job_id']==edit.job_id)
            chosen={k:p for k,p in valid.items() if p['fragment_id'] in edit.fragment_ids}
            if (not edit.fragment_ids or not set(edit.fragment_ids)<={p['fragment_id'] for p in valid.values()}
                    or edit.action=='create' and not fill.covered_by(job['unit'],sources[sid]['paragraphs'][job['unit']['paragraph_key']],chosen)):
                raise ValueError('coverage edit lacks confirmed or borrows unprovided fragments')
            if edit.action=='enrich':
                key=edit.record_key
                if key not in targets or key.split('/')[0]!=edit.record_type:raise ValueError('coverage attempted other-group write')
                p=working[key]['payload']
                if p.get('review_status') in f.PROTECTED:raise ValueError('protected Evidence coverage record')
                if not set(edit.fragment_ids)<=set(e.evidence_fragment_ids(p)):
                    pending.append({'unit_id':edit.job_id,'layer':'source','reason':'existing_record_needs_source_binding_change'});continue
                # Enrichment retains the complete existing binding. The source
                # stage already verified the inventory's literal span; GPT
                # selects support for the meaning, not every oral bridge in
                # that span. Do not require it to re-quote the inventory here.
            else:
                if not edit.statement or not edit.statement.strip():raise ValueError('new Evidence needs statement')
                oid=('E' if edit.record_type=='evidence_steps' else 'O')+'-FLE-'+e.digest([sid,edit.statement,sorted(edit.fragment_ids)])[:24]
                key=edit.record_type+'/'+oid
                p={('evidence_step_id' if edit.record_type=='evidence_steps' else 'observation_id'):oid,
                   'statement':edit.statement,'review_status':'candidate','visibility':'internal'}
                existing=next((k for k,r in working.items() if k.startswith(edit.record_type+'/') and not r.get('retired')
                    and r['payload'].get('statement')==edit.statement and set(e.evidence_fragment_ids(r['payload']))==set(edit.fragment_ids)),None)
                if existing:outcomes.append({'unit_id':edit.job_id,'basis':'existing_record_reused','object_key':existing});continue
                if key in working:raise ValueError('new Evidence identity occupied')
            post={**p,**patch_for(key,p,edit)}
            if edit.action=='create':post.update(source_fragment_id=None,source_fragment_ids=list(dict.fromkeys(edit.fragment_ids)))
            KNOWLEDGE_COLLECTIONS[edit.record_type][0].model_validate(post)
            if key in coverage_updates and post!=coverage_updates[key]:raise ValueError('coverage edits conflict on the same Evidence')
            coverage_updates[key]=post
            updates[key]=post;working[key]={**working.get(key,{}),'payload':post,'retired':False}
            value=material(key,working,sources);fp=fingerprint(key,post,value);path=root/'coverage-decisions'/coverage_fingerprint(state)/(e.digest(job)+'.json')
            decision={'postimage':visible(post),'fragment_ids':e.evidence_fragment_ids(post),'reason':edit.reason};e.save(path,decision)
            accepted=root/'accepted'/(fp+'.json')
            coverage_proofs.append((accepted,{'fingerprint':fp,'decision_path':str(path.resolve()),'decision_sha256':e.digest(decision)}))
            outcomes.append({'unit_id':edit.job_id,'basis':'gpt_'+edit.action,'object_key':key,'reason':edit.reason})
    if units and not pending:
        for accepted,proof in coverage_proofs:
            if not accepted.exists():e.save(accepted,proof)
        post_state=coverage_state(group_records(group,working,targets)[1])
        path=root/'coverage-decisions'/coverage_fingerprint(state)/'accepted-outcome.json'
        decision={'unit_outcomes':[x for x in outcomes if 'unit_id' in x], 'jev':answers,
                  'postimage_fingerprint':coverage_fingerprint(post_state)}
        e.save(path,decision)
        accepted=root/'coverage-accepted'/(coverage_fingerprint(post_state)+'.json')
        if not accepted.exists():e.save(accepted,{'decision_path':str(path.resolve()),'decision_sha256':e.digest(decision)})
    read_keys=set(targets)|set(catalog)|set(valid)|{'source_documents/'+sid}
    for key in targets:
        read_keys.update('source_fragments/'+fid for fid in e.evidence_fragment_ids(records[key]['payload']))
    frozen={k:records[k] for k in read_keys if k in records}
    stage=root/'groups'/group['group_id']/e.digest({k:(v.get('revision'),v.get('content_sha256'),v['payload']) for k,v in frozen.items()})
    result={'version':VERSION,'status':'needs_attention' if pending else 'verified','source_id':sid,'group_id':group['group_id'],
        'updates':{k:p for k,p in updates.items() if k not in records or p!=records[k]['payload']},'new_fragments':{},
        'outcomes':outcomes,'pending':pending,'applied':False,'claim_qualification':'not_granted',
        'commit_reason':'Grouped fragment-bound Evidence/Observation dispositions only'}
    if any(k.split('/')[0] not in COLLECTIONS for k in result['updates']):raise ValueError('Evidence layer write scope expanded')
    f.source_unchanged(sources);e.save(stage/'before.json',{'records':frozen,'sources':sources});e.save(stage/'verification.json',result)
    return e.commit_verified(store,stage) if apply else e.publish(stage,result)


def run(store, source_id, plan, root, ports, previous, *, group_ids=None, apply=False):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    with (root/'run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        pin=f.bind_source(store,source_id,root);records,sources=f.load_source(store,source_id,visual_context=True)
        source.current_source(records,sources,source_id);coverage.validate_groups(plan,source_id,sources[source_id])
        if pin!={'source_id':source_id,**sources[source_id]['file']}:raise ValueError('Evidence source census drift')
        selected=group_ids or [g['group_id'] for g in plan['groups']]
        if len(selected)!=len(set(selected)) or not set(selected)<={g['group_id'] for g in plan['groups']}:raise ValueError('invalid Evidence group selection')
        saved_intent=json.loads((root/'intent.json').read_text()) if (root/'intent.json').exists() else None
        authorized=saved_intent['selected'] if saved_intent else selected
        if not set(selected)<=set(authorized):raise ValueError('Evidence group selection expands frozen scope')
        e.save(root/'intent.json',{'version':VERSION,'source':pin,'groups':plan,'selected':authorized,'models':ports.identity,
            'gpt_output_limit':getattr(getattr(ports,'gpt',None),'max_output_tokens',None),
            'policy':e.digest([PROMPT,COVERAGE_PROMPT,components.PROMPT,Edits.model_json_schema(),MissingEdits.model_json_schema()]),
            'previous':str(Path(previous).resolve())})
        for started in root.rglob('started.json'):
            if not (started.parent/'response.json').exists():
                raise ValueError('Evidence call has no saved reply; inspect original transport before continuing')
        # Complete the original receipt after a post-commit crash, even when
        # the current postimage already has a reusable semantic decision.
        for intent in sorted((root/'groups').glob('*/*/commit-intent.json')):
            if (intent.parent/'commit.json').exists():continue
            saved=json.loads((intent.parent/'verification.json').read_text())
            if saved.get('version')!=VERSION or saved.get('new_fragments') or any(k.split('/')[0] not in COLLECTIONS for k in saved['updates']):
                raise ValueError('Evidence recovery write scope differs')
            recovered=e.commit_verified(store,intent.parent)
            if recovered.get('applied'):
                for key in recovered['updates']:records[key]=store.get_record_state(*key.split('/',1))
        units=inventory(previous,sources,source_id);owners={}
        for g in plan['groups']:
            for key in g.get('lower_record_keys',[]):
                if key.split('/')[0] in COLLECTIONS:
                    if key in owners:raise ValueError('Evidence has duplicate primary groups')
                    owners[key]=g['group_id']
        pk_group={pk:g['group_id'] for g in plan['groups'] for pk in g['paragraph_keys']}
        for key,r in records.items():
            if key.split('/')[0] not in COLLECTIONS or key in owners:continue
            pks=[records.get('source_fragments/'+fid,{}).get('payload',{}).get('paragraph_key') for fid in e.evidence_fragment_ids(r['payload'])]
            if any(pk in pk_group for pk in pks):owners[key]=next(pk_group[pk] for pk in pks if pk in pk_group)
            else:raise ValueError('unlocated Evidence must return to source layer: '+key)
        # Track direct readers of actual write keys, not a recursive graph audit.
        readers={};finished=set()
        for group in plan['groups']:
            for path in (root/'groups'/group['group_id']).glob('*/result.json'):
                saved=json.loads(path.read_text())
                if saved.get('status')=='applied' or saved.get('status')=='verified' and not saved.get('updates'):
                    finished.add(group['group_id'])
        for group in plan['groups']:
            primary=[k for k,gid in owners.items() if gid==group['group_id'] and k in records]
            for key in group_records(group,records,primary)[1]:readers.setdefault(key,set()).add(group['group_id'])
        results=[]
        affected=set(json.loads((root/'result.json').read_text()).get('affected_prior_group_ids',[])) if (root/'result.json').exists() else set()
        for group in plan['groups']:
            if group['group_id'] not in selected:continue
            targets=sorted(k for k,gid in owners.items() if gid==group['group_id'] and k in records)
            result=run_group(store,records,sources,group,targets,[u for u in units if u['paragraph_key'] in group['paragraph_keys']],root,ports,previous,apply=apply)
            results.append(result)
            if result.get('applied'):
                for key in result['updates']:
                    if key in records and visible(records[key]['payload'])!=visible(result['updates'][key]):
                        affected.update((readers.get(key,set())&finished)-{group['group_id']})
                    records[key]=store.get_record_state(*key.split('/',1))
            elif result['updates'] or result['pending']:break  # No later group consumes an unapplied draft.
            if not result['pending']:finished.add(group['group_id']);affected.discard(group['group_id'])
        return e.publish(root,{'version':VERSION,'source_id':source_id,'groups':results,
            'requested_group_ids':selected,'authorized_group_ids':authorized,'affected_prior_group_ids':sorted(affected),
            'status':'completed' if not affected and len(results)==len(selected) and all(not g['pending'] for g in results) and (apply or not any(g['updates'] for g in results)) else 'needs_attention',
            'claim_qualification':'not_granted'})
