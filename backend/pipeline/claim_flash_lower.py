"""Whole-source lower layer for Flash. No Claim-group execution or qualification."""
from copy import deepcopy
import fcntl
import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from backend.pipeline import evidence_refresh as e
from backend.pipeline import claim_flash_common as f
from backend.pipeline import claim_flash_components as components
from backend.pipeline import claim_flash_questions as jevq
from backend.pipeline import claim_flash_source_lookup as source_lookup
from backend.pipeline import claim_flash_source as source_stage
from backend.pipeline.claim_flash_source import excluded_context


class Edit(BaseModel):
    model_config=ConfigDict(extra='forbid', strict=True)
    job_id: str
    action: Literal['repair','keep','needs_source']
    statement: str=Field(min_length=1)
    speaker: str
    stance: str
    scripture_refs: list[str]
    quotes: list[e.Quote]=Field(min_length=1)
    reason: str=Field(min_length=1)
    attribution: str | None = None
    questioner: str | None = None


class Edits(BaseModel):
    model_config=ConfigDict(extra='forbid', strict=True)
    items: list[Edit]


class QuestionSourceDecision(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    job_id: str
    action: Literal['repair','retire','needs_source']
    text: str
    questioner: str
    quotes: list[e.Quote]
    reason: str=Field(min_length=1)


class QuestionSourceDecisions(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    items: list[QuestionSourceDecision]


class MissingRecord(BaseModel):
    model_config=ConfigDict(extra='forbid', strict=True)
    record_type: Literal['evidence_steps','observations']
    statement: str=Field(min_length=1)
    speaker: str
    stance: str
    scripture_refs: list[str]
    quotes: list[e.Quote]=Field(min_length=1)


class Conclusion(BaseModel):
    model_config=ConfigDict(extra='forbid', strict=True)
    statement: str=Field(min_length=1)
    support_keys: list[str]=Field(min_length=1)
    quotes: list[e.Quote]=Field(min_length=1)
    reason: str=Field(min_length=1)


class LowerConnection(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    a: str
    b: str
    reason: str=Field(min_length=1)


class CoverageItem(BaseModel):
    model_config=ConfigDict(extra='forbid', strict=True)
    job_id: str
    action: Literal['resolved','needs_source']
    new_records: list[MissingRecord]
    conclusions: list[Conclusion]
    connections: list[LowerConnection] = Field(default_factory=list)
    reason: str=Field(min_length=1)


class Coverage(BaseModel):
    model_config=ConfigDict(extra='forbid', strict=True)
    items: list[CoverageItem]


PROMPT=('這是 Claim Update Flash 全來源下層，只處置指定工作，不重審 Claim。'
 'Jev 是線索，GPT 最終語義決定可覆蓋它。記錄教授的教導，不判神學對錯，不補外部前提。'
 'Evidence 保存論據與步驟；Observation 保存觀察；原文可共同支持一項，不要求逐字摘要。'
 '保留關鍵經文、否定、條件、範圍、因果和說話者角色，反方與設問不能冒充教授斷言。'
 '口頭強調、無意義短句和重複措辭不是新論據。不因有判斷就強制另建 Claim。'
 '每個 job_id 恰好一次，只輸出指定 schema 和簡短理由，quotes 必須逐字出自提供的段落。')

REPAIR_PROMPT=(PROMPT+
    '修正表述或來源用 repair；異議不成立 keep；無法解答 needs_source。不得修改 Question 答案連線。'
    'source_lookup 是同講候選查找，不是支持成立或不存在的證明；historical_quotes 只是歷史定位線索。'
    '核對候選完整上下文，可多段共同支持；不因局部缺據就刪除教授實際講過的論據。'
    'binding_requires_repair 時不能 keep 舊失效綁定，須以現版逐字原文 repair，否則 needs_source。')


def record_postimage(key, record, edit, material, records, sources):
    col=key.split('/')[0]
    text_field='text' if col=='questions' else 'title' if col=='position_nodes' else 'statement'
    if edit.action=='keep':
        # Action is authoritative. Echo fields and selected excerpts are not patches;
        # the complete original material was already physically checked before review.
        fields={'statement':record.get(text_field),'speaker':record.get('speaker'),
                'stance':record.get('stance'),'scripture_refs':record.get('scripture_refs',[]),
                'attribution':record.get('attribution'),'questioner':record.get('questioner')}
        ignored=[name for name,old in fields.items() if getattr(edit,name) is not None and getattr(edit,name)!=old]
        return record,{},[{'action':'keep','ignored_echo_fields':ignored,'original_bindings_retained':True}]
    # Reuse the existing exact quote projection, fragment identity and physical binding builder.
    synthetic={**record, 'evidence_step_id':key, 'statement':record.get(text_field, ''),
               'speaker':record.get('speaker') or 'professor', 'stance':record.get('stance') or 'asserted'}
    post,fragments,projection=e.repair_postimage(synthetic, {
        'evidence_id':key, 'action':'repair', 'statement':edit.statement,
        'speaker':edit.speaker, 'stance':edit.stance,
        'quotes':[q.model_dump() for q in edit.quotes], 'reason':edit.reason}, material, records, sources)
    result=deepcopy(record)
    result.update({text_field:edit.statement,'source_fragment_id':None,
                   'source_fragment_ids':post['source_fragment_ids']})
    if result.get('source_binding_status') in {'source_deleted','partially_deleted'}:
        result['source_binding_status']='needs_claim_refresh'
    if col=='evidence_steps':
        result.update(speaker=edit.speaker, stance=edit.stance)
    if col in {'evidence_steps','observations'}:
        result['scripture_refs']=edit.scripture_refs
    if edit.attribution is not None:
        result['attribution']=edit.attribution
    if col=='questions' and edit.questioner is not None:
        result['questioner']=edit.questioner
    from backend.api.canonical_repository.knowledge_models import KNOWLEDGE_COLLECTIONS
    result=KNOWLEDGE_COLLECTIONS[col][0].model_validate(result).model_dump(mode='json')
    return result,fragments,projection


def paragraph_records(pk, records, excluded=()):
    return {k:f.projected(r) for k,r in records.items() if k.split('/')[0] in f.LOWER
            and k not in excluded
            and any(records.get('source_fragments/'+fid,{}).get('payload',{}).get('paragraph_key')==pk
                    for fid in e.evidence_fragment_ids(r['payload']))}


def recover_deleted_questions(records,sources,pending,root,ports):
    """A deleted-source Question needs a complete current source, not a guessed anchor."""
    sid,source=next(iter(sources.items()));jobs=[]
    for item in pending:
        key=item.get('object_key','')
        if not key.startswith('questions/') or key not in records:continue
        p=records[key]['payload'];fids=e.evidence_fragment_ids(p)
        fragments=[records.get('source_fragments/'+fid,{}).get('payload',{}) for fid in fids]
        if (p.get('review_status') in f.PROTECTED or not fragments or
                any(v.get('source_id')!=sid or v.get('anchor_state')!='source_deleted'
                    or v.get('deleted_in_source_sha256')!=source['body_sha256'] for v in fragments)):continue
        jobs.append({'job_id':key,'question':f.projected(records[key]),
            'historical_quotes':[v['verbatim_excerpt'] for v in fragments],
            'previous_disposition':item['reason']})
    if not jobs:return {},{},[],[],set()
    current={pk:p['text'] for pk,p in source['paragraphs'].items()}
    updates={};generated={};retiring=[];outcomes=[];resolved=set()
    try:
        replies=f.gpt(root,ports,'deleted_question_source',jobs,QuestionSourceDecisions.model_json_schema(),
            PROMPT+'本次只處置來源遷移已明確標記原引文刪除、局部查找仍未解決的 Question。'
            'current_source_paragraphs 是完整現版講道。核對同一語境、所問內容與發問者，不能借別處泛泛的設問。'
            '現版仍有相同提問可 repair，給忠實 text 與逐字 quotes；全部原文已無此提問可 retire。'
            '陳述同一主題不等於教授問過問題，不把陳述改造成提問。仍不確定 needs_source。'
            'retire 只退役這個舊問題，保留歷史，不修改任何 Claim 或其他層。',
            shared={'current_source_paragraphs':current,'current_source_sha256':source['body_sha256']})
        for batch,raw in replies:
            decisions=QuestionSourceDecisions.model_validate(raw).items
            f.exactly([d.model_dump() for d in decisions],[j['job_id'] for j in batch],'job_id')
            for decision in decisions:
                key=decision.job_id;p=records[key]['payload']
                if decision.action=='needs_source':continue
                if decision.action=='retire':
                    oid=key.split('/',1)[1]
                    edges=[k for k,r in records.items() if k.startswith(('knowledge_relations/','claim_relations/'))
                        and oid in {r['payload']['from_id'],r['payload']['to_id']}]
                    if any(records[k]['payload'].get('review_status') in f.PROTECTED for k in edges):continue
                    retiring.extend([key]+edges)
                else:
                    if not decision.quotes or not decision.text.strip():raise ValueError('Question source repair needs text and quotes')
                    edit=Edit(job_id=key,action='repair',statement=decision.text,questioner=decision.questioner,
                        speaker='professor',stance='asserted',scripture_refs=p.get('scripture_refs',[]),
                        quotes=decision.quotes,reason=decision.reason)
                    material={'nearby_paragraphs':[{'source_id':sid,'paragraph_key':pk,**v}
                        for pk,v in source['paragraphs'].items()]}
                    post,frags,_=record_postimage(key,p,edit,material,records,sources)
                    updates[key]=post;generated.update(frags)
                resolved.add(key)
                outcomes.append({'object_key':key,'basis':'complete_current_source_question_disposition',
                    'action':decision.action,'reason':decision.reason,'source_sha256':source['body_sha256']})
    except ValueError as exc:
        # Absence cannot be inferred from a truncated sermon. Oversized complete
        # input stays pending; all other contract/source errors remain visible.
        if 'one complete GPT job exceeds Flash payload limit' not in str(exc):raise
    return updates,generated,retiring,outcomes,resolved


def run(store, source_id, root, ports, *, apply=False):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    with (root/'run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        f.bind_source(store,source_id,root)
        e.save(root/'intent.json',{'version':f.VERSION,'stage':'lower','source_id':source_id,
            'gpt_review_contract':f.REVIEW_CONTRACT,'component_contract':components.CONTRACT,
            'jev_contract':f.JEV_CONTRACT,'material_contract':f.MATERIAL_CONTRACT,
            'source_lookup_contract':source_lookup.CONTRACT,'context_contract':'unowned_dialogue_context_v1','models':ports.identity})
        if (root/'verification.json').exists():
            result=json.loads((root/'verification.json').read_text())
            if apply and (root/'commit.json').exists():
                verify(root,*f.load_source(store,source_id))
                return f.resume_committed(store,root,result)
            if (result['status']=='needs_attention' and not result.get('question_source_contract')
                    and not (root/'commit-intent.json').exists()
                    and any(p.get('object_key','').startswith('questions/') for p in result['pending'])):
                # New complete-source recovery can finish an older, unapplied
                # pending result. Preserve it verbatim and reuse exact raw calls.
                e.save(root/'superseded'/('verification-'+e.digest(result)+'.json'),result)
                (root/'verification.json').unlink()
            else:return e.commit_verified(store,root) if apply else result
        records,sources=f.load_source(store,source_id)
        e.save(root/'before.json',{'records':records,'sources':sources})
        prepared=source_stage.prepare(records,sources)
        working=prepared['working'];updates=prepared['updates'];generated=prepared['generated']
        outcomes=prepared['outcomes'];pending=prepared['pending'];jobs=[]
        materials=prepared['materials'];calibration=prepared['calibration']
        mechanical=prepared['mechanical'];excluded=prepared['excluded']
        e.save(root/'source-calibration.json',mechanical)
        e.save(root/'source-assembly.json',{key:v['source_assembly'] for key,v in materials.items() if v.get('source_assembly')})
        parts=components.decompose(root,ports,{key:working[key]['payload']['statement'] for key in materials
            if key.split('/')[0] in {'evidence_steps','observations'}})
        for key in sorted(materials):
            row=working[key]
            v=materials[key]
            answers=({'source_calibration_required':calibration[key]} if key in calibration else
                     components.check(root,ports,key.split('/')[0],f.projected(row),v,parts.get(key)))
            if not components.needs_disposition(answers):
                outcomes.append({'object_key':key,'basis':'jev_precheck'})
            else:
                jobs.append({'job_id':key,'record':f.projected(row),'material':v,'verdict':answers,
                             'components':parts.get(key)})
        lookup_jobs=[j for j in jobs if j['job_id'] in calibration or source_lookup.needs_lookup(j['verdict'])]
        enriched=source_lookup.lookup(lookup_jobs,sources,root/'source-lookup',ports)
        for job in jobs:
            if job['job_id'] in enriched:job['material']=enriched[job['job_id']]
        for batch,raw in f.gpt(root,ports,'lower_repair',jobs,Edits.model_json_schema(),REPAIR_PROMPT):
            edits=Edits.model_validate(raw).items
            f.exactly([x.model_dump() for x in edits],[j['job_id'] for j in batch],'job_id')
            byid={j['job_id']:j for j in batch}
            for edit in edits:
                key=edit.job_id
                allowed={p['paragraph_key'] for p in byid[key]['material']['nearby_paragraphs']}
                if edit.action=='repair' and any(q.paragraph_key not in allowed for q in edit.quotes):
                    # Do not broaden a record's source authority just because the
                    # model borrowed another job's passage. Correct this item once.
                    correction={**deepcopy(byid[key]),'rejected_answer':edit.model_dump(),
                        'contract_error':'引文引用了本項未提供的段落；不能借用同批其他工作的原文。只用本項材料重新處置。'}
                    schema=Edits.model_json_schema()
                    schema['$defs']['Quote']['properties']['paragraph_key']['enum']=sorted(allowed)
                    recovery=root/'quote-contract'/e.digest(correction)
                    e.save(recovery/'recovery.json',{'original_answer_sha256':e.digest(edit.model_dump()),
                        'job_id':key,'allowed_paragraph_keys':sorted(allowed),'max_additional_calls':1})
                    _,reply=next(f.gpt(recovery,ports,'lower_repair',[correction],schema,REPAIR_PROMPT))
                    corrected=Edits.model_validate(reply).items
                    f.exactly([x.model_dump() for x in corrected],[key],'job_id')
                    edit=corrected[0]
                if edit.action=='needs_source':
                    pending.append({'object_key':key,'reason':edit.reason});continue
                if edit.action=='keep' and key in calibration:
                    pending.append({'object_key':key,'reason':'invalid_source_binding_cannot_be_kept'});continue
                post,frags,projection=record_postimage(key,working[key]['payload'],edit,byid[key]['material'],working,sources)
                if post!=records[key]['payload']:
                    if records[key]['payload'].get('review_status') in f.PROTECTED:
                        pending.append({'object_key':key,'reason':'protected_lower_record'});continue
                    updates[key]=post;generated.update(frags)
                    working=f.apply_overlay(working,{key:post},frags)
                f.value(key,working,sources)
                outcomes.append({'object_key':key,'basis':'gpt_disposition','reason':edit.reason,'quote_projection':projection})
        recovered,frags,retiring,question_outcomes,resolved=recover_deleted_questions(
            working,sources,pending,root/'deleted-question-source',ports)
        updates.update(recovered);generated.update(frags);outcomes.extend(question_outcomes)
        working=f.apply_overlay(working,recovered,frags)
        for key in retiring:working.pop(key,None)
        pending=[item for item in pending if item.get('object_key') not in resolved]
        # Scan raw source in source order even if no existing record or Claim covers a paragraph.
        coverage_jobs=[];coverage=[];conclusions=[];lower_pairs=[]
        # Do not mistake an unresolved node/binding for missing source coverage.
        coverage_source=sources[source_id]['paragraphs'] if not pending else {}
        for pk,paragraph in coverage_source.items():
            if not paragraph.get('text','').strip():
                continue
            bound=paragraph_records(pk,working,excluded)
            answers=f.jev(root,ports,{'paragraph_key':pk,'text':paragraph['text'],'records':bound},jevq.coverage())
            coverage.append({'paragraph_key':pk,'precheck':answers})
            if answers['missing_lower']!='covered' or answers['conclusion_candidate'] in {'substantive_judgment','uncertain'}:
                # Complete support can span paragraphs: offer adjacent context, not a hard heading boundary.
                keys=list(sources[source_id]['paragraphs']);n=keys.index(pk)
                context={k:sources[source_id]['paragraphs'][k]['text'] for k in keys[max(0,n-1):n+2]}
                bound={k:v for p in context for k,v in paragraph_records(p,working,excluded).items()}
                coverage_jobs.append({'job_id':pk,'source_paragraphs':context,'records':bound,'precheck':answers})
        for batch,raw in f.gpt(root,ports,'lower_coverage',coverage_jobs,Coverage.model_json_schema(),PROMPT+
                '只補現有 records 未表達的實質 Evidence 或 Observation；已有承載不重建。'
                'conclusions 只保存待核對的實質判斷，不決定新建 Claim；給 statement、候選所在的記錄 key、原文及簡短候選理由。'
                '此處不以有無其他論據為候選門檻，獨立支持與論證角色交 Claim 層 GPT 判斷。'
                'new_records 新增記錄在同項中以 new:0 等暫名供 conclusions.support_keys 引用。'
                'connections 可提出本項所提供下層記錄間必要的漏連，new:0 等暫名沿用；只提候選，稍後獨立判定。'
                '證據已連到別的 Claim 不能排除另一判斷；只有沒有實質候選時 conclusions 才留空，不因未見獨立支持而丟掉。'
                '若跨段論證在提供材料中不完整，needs_source 明示，不猜測或截斷。'):
            items=Coverage.model_validate(raw).items
            f.exactly([x.model_dump() for x in items],[j['job_id'] for j in batch],'job_id')
            for item in items:
                job=next(j for j in batch if j['job_id']==item.job_id)
                if item.action=='needs_source':
                    # The coverage window is context, not a hard support boundary.
                    # Expand only to physically verified sources of the records already supplied.
                    expanded=deepcopy(job)
                    for key in job['records']:
                        for fid in e.evidence_fragment_ids(working[key]['payload']):
                            fragment=working['source_fragments/'+fid]['payload']
                            pk=fragment['paragraph_key']
                            expanded['source_paragraphs'][pk]=sources[source_id]['paragraphs'][pk]['text']
                    if set(expanded['source_paragraphs'])>set(job['source_paragraphs']):
                        expanded['source_paragraphs']={pk:expanded['source_paragraphs'][pk]
                            for pk in sources[source_id]['paragraphs'] if pk in expanded['source_paragraphs']}
                        expanded['coverage_target_paragraph_key']=item.job_id
                        expanded['context_recovery']={'defect':'bound record source excerpts omitted from coverage window',
                            'original_input_sha256':e.digest(job),'original_answer_sha256':e.digest(item.model_dump()),
                            'added_paragraph_keys':sorted(set(expanded['source_paragraphs'])-set(job['source_paragraphs'])),
                            'max_additional_calls':1}
                        e.save(root/'coverage-context'/item.job_id/'recovery.json',expanded['context_recovery'])
                        recovered=f.gpt(root/'coverage-context',ports,'lower_coverage_context',[expanded],
                            Coverage.model_json_schema(),PROMPT+
                            '目標只限 job_id 段落；source_paragraphs 其餘段落是參考，不因相鄰設問尚未展開就判目標未完成。'
                            '現在已補上所列記錄的完整綁定原文，重新處置這一項；不猜缺失來源，不作 Claim 升級裁決。')
                        _,reply=next(recovered)
                        items2=Coverage.model_validate(reply).items
                        f.exactly([x.model_dump() for x in items2],[item.job_id],'job_id')
                        item=items2[0];job=expanded
                    if item.action=='needs_source':
                        # Preserve all proposed content in the raw answer, but write none of it.
                        pending.append({'paragraph_key':item.job_id,'reason':item.reason});continue
                available={pk:{'source_id':source_id,'paragraph_key':pk,**sources[source_id]['paragraphs'][pk]}
                           for pk in job['source_paragraphs']}
                temp={};local_keys=set(job['records'])
                for i,new in enumerate(item.new_records):
                    identity=('E' if new.record_type=='evidence_steps' else 'O')+'-FL-'+e.digest(
                        [source_id,sources[source_id]['body_sha256'],new.model_dump()])[:24]
                    key=new.record_type+'/'+identity
                    base={('evidence_step_id' if new.record_type=='evidence_steps' else 'observation_id'):identity,
                          'statement':new.statement,'review_status':'candidate','visibility':'internal'}
                    edit=Edit(job_id=key,action='repair',reason=item.reason,
                        **{k:v for k,v in new.model_dump().items() if k!='record_type'})
                    post,frags,_=record_postimage(key,base,edit,{'nearby_paragraphs':list(available.values())},working,sources)
                    # Exact same semantic record is reused, not counted as another new object.
                    existing=next((k for k,r in working.items() if k.startswith(new.record_type+'/')
                        and r['payload'].get('statement')==post['statement']
                        and set(e.evidence_fragment_ids(r['payload']))==set(e.evidence_fragment_ids(post))),None)
                    if existing:
                        key=existing
                    else:
                        updates[key]=post;generated.update(frags);working=f.apply_overlay(working,{key:post},frags)
                    temp['new:'+str(i)]=key
                for connection in item.connections:
                    a,b=temp.get(connection.a,connection.a),temp.get(connection.b,connection.b)
                    if a==b or not {a,b}<=local_keys|set(temp.values()):
                        raise ValueError('lower connection has unprovided endpoints')
                    lower_pairs.append({'a':a,'b':b,'reason':connection.reason})
                for candidate in item.conclusions:
                    support=[temp.get(k,k) for k in candidate.support_keys]
                    if len(set(support))!=len(support) or not set(support)<=local_keys|set(temp.values()):
                        raise ValueError('conclusion has unprovided or duplicate support')
                    quote_projection=[]
                    for q in candidate.quotes:
                        if q.paragraph_key not in available:
                            raise ValueError('conclusion quoted an unprovided paragraph')
                        supplied=q.quote
                        q.quote=e.source_excerpt(supplied,available[q.paragraph_key]['text'],contract=e.SPAN_CONTRACT)
                        quote_projection.append({'paragraph_key':q.paragraph_key,'supplied':supplied,'exact':q.quote})
                    value={**candidate.model_dump(),'support_keys':support,'source_id':source_id}
                    e.save(root/'candidate-quote-projection'/item.job_id/(e.digest(value)+'.json'),quote_projection)
                    value['candidate_id']='J-'+e.digest(value)[:24]
                    if not any(x['candidate_id']==value['candidate_id'] for x in conclusions):
                        conclusions.append(value)
                coverage.append({'paragraph_key':item.job_id,'basis':'gpt_disposition','reason':item.reason})
        # Lower relations are reviewed after node repairs; never borrow Claim groups as a scope.
        from backend.pipeline import claim_flash_pairs as pairs
        lower_edges=[k for k,r in working.items() if k.startswith('knowledge_relations/')
                     and all(any(n+'/'+r['payload'][field] in working for n in f.LOWER)
                             for field in ['from_id','to_id'])]
        excluded_ids={k.split('/',1)[1] for k in excluded}
        lower_edges=[k for k in lower_edges if not {working[k]['payload']['from_id'],working[k]['payload']['to_id']}&excluded_ids]
        blocked={p['object_key'] for p in pending if p.get('object_key') in working}
        blocked_ids={k.split('/',1)[1] for k in blocked}
        deferred=[k for k in lower_edges if any(working[k]['payload'][x] in blocked_ids for x in ['from_id','to_id'])]
        pending.extend({'object_key':k,'reason':'lower_relation_has_unresolved_node'} for k in deferred)
        lower_edges=[k for k in lower_edges if k not in deferred]
        lower_pairs=[p for p in lower_pairs if not ({p['a'],p['b']}&blocked)]
        rel=pairs.review(working,sources,lower_edges,lower_pairs,root/'relations',ports,kind='lower')
        updates.update(rel['updates']);pending.extend(rel['pending'])
        for payload in rel['new_relations']:
            rid='KR-FL-'+e.digest(payload)[:24]
            updates['knowledge_relations/'+rid]={'relation_id':rid,**payload}
        verified=f.apply_overlay(records,updates,generated)
        for key in retiring+rel['retiring_keys']:verified.pop(key,None)
        f.source_unchanged(sources)
        result={'version':f.VERSION,'stage':'lower','status':'needs_attention' if pending else 'verified',
            'source_id':source_id,'updates':updates,'new_fragments':generated,'retiring_keys':list(dict.fromkeys(retiring+rel['retiring_keys'])),
            'outcomes':outcomes,'coverage':coverage,'conclusion_candidates':conclusions,'pending':pending,
            'excluded_context_keys':sorted(excluded),
            'question_source_contract':'complete_deleted_question_v1',
            'fidelity':{k:f.projected(r) for k,r in verified.items() if k.split('/')[0] in f.LOWER},
            'source_sha256':sources[source_id]['body_sha256'],'claim_qualification':'not_granted','applied':False}
        e.save(root/'verification.json',result)
        return e.commit_verified(store,root) if apply else e.publish(root,result)


def verify(root,records,sources):
    root=Path(root)
    result=json.loads((root/'verification.json').read_text())
    if result.get('stage')!='lower' or result['status']!='verified' or result['pending']:
        raise ValueError('complete source lower-layer result required')
    sid=result['source_id']
    if sid not in sources or sources[sid]['body_sha256']!=result['source_sha256']:
        raise ValueError('lower source version differs')
    if set(result['fidelity'])!={k for k in records if k.split('/')[0] in f.LOWER}:
        raise ValueError('lower record census changed since verification')
    for key,p in result['fidelity'].items():
        if key not in records or f.projected(records[key])!=p:
            raise ValueError('lower content changed since verification: '+key)
    for key in result.get('excluded_context_keys',[]):
        if not excluded_context(key,records):raise ValueError('excluded context acquired an argument use: '+key)
    return result
