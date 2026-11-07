"""Whole-source lower layer for Flash. No Claim-group execution or qualification."""
from copy import deepcopy
import fcntl
import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from backend.pipeline import evidence_refresh as e
from backend.pipeline import claim_flash_common as f


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


def paragraph_records(pk, records):
    return {k:f.projected(r) for k,r in records.items() if k.split('/')[0] in f.LOWER
            and any(records.get('source_fragments/'+fid,{}).get('payload',{}).get('paragraph_key')==pk
                    for fid in e.evidence_fragment_ids(r['payload']))}


def run(store, source_id, root, ports, *, apply=False):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    with (root/'run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        f.bind_source(store,source_id,root)
        e.save(root/'intent.json',{'version':f.VERSION,'stage':'lower','source_id':source_id,'models':ports.identity})
        if (root/'verification.json').exists():
            return e.commit_verified(store,root) if apply else json.loads((root/'verification.json').read_text())
        records,sources=f.load_source(store,source_id)
        e.save(root/'before.json',{'records':records,'sources':sources})
        working=deepcopy(records);updates={};generated={};outcomes=[];pending=[];jobs=[]
        materials={};calibration=[]
        for key,row in records.items():
            if key.split('/')[0] in f.LOWER:
                try:materials[key]=f.value(key,records,sources)
                except (ValueError,KeyError) as exc:
                    calibration.append({'object_key':key,'reason':'source_calibration_required','detail':str(exc)})
        if calibration:
            result={'version':f.VERSION,'stage':'lower','status':'needs_attention','source_id':source_id,
                'updates':{},'new_fragments':{},'retiring_keys':[],'outcomes':[],'coverage':[],
                'conclusion_candidates':[],'pending':calibration,'fidelity':{},
                'source_sha256':sources[source_id]['body_sha256'],'claim_qualification':'not_granted','applied':False}
            e.save(root/'verification.json',result)
            return e.publish(root,result)
        for key,row in records.items():
            if key.split('/')[0] not in f.LOWER:
                continue
            v=materials[key]
            verdict=f.jev(root,ports,{'record_type':key.split('/')[0],
                'record':f.projected(row),'original_text_excerpts':v['original_text_excerpts']},
                {'record_fidelity':f.yn('本記錄是否有任何實質內容、歸屬或經文用途不受完整原文清單支持？'
                 'Question 只核對提問，不檢查其 answer_claim_ids；不要求支持材料成為獨立 Claim。')})['record_fidelity']
            if verdict=='no':
                outcomes.append({'object_key':key,'basis':'jev_precheck'})
            else:
                jobs.append({'job_id':key,'record':f.projected(row),'material':v,'verdict':verdict})
        for batch,raw in f.gpt(root,ports,'lower_repair',jobs,Edits.model_json_schema(),PROMPT+
                '修正表述或來源用 repair；異議不成立 keep；無法解答 needs_source。不得修改 Question 答案連線。'):
            edits=Edits.model_validate(raw).items
            f.exactly([x.model_dump() for x in edits],[j['job_id'] for j in batch],'job_id')
            byid={j['job_id']:j for j in batch}
            for edit in edits:
                key=edit.job_id
                if edit.action=='needs_source':
                    pending.append({'object_key':key,'reason':edit.reason});continue
                post,frags,projection=record_postimage(key,records[key]['payload'],edit,byid[key]['material'],working,sources)
                if post!=records[key]['payload']:
                    if records[key]['payload'].get('review_status') in f.PROTECTED:
                        pending.append({'object_key':key,'reason':'protected_lower_record'});continue
                    updates[key]=post;generated.update(frags)
                    working=f.apply_overlay(working,{key:post},frags)
                outcomes.append({'object_key':key,'basis':'gpt_disposition','reason':edit.reason,'quote_projection':projection})
        # Scan raw source in source order even if no existing record or Claim covers a paragraph.
        coverage_jobs=[];coverage=[];conclusions=[];lower_pairs=[]
        for pk,paragraph in sources[source_id]['paragraphs'].items():
            if not paragraph.get('text','').strip():
                continue
            bound=paragraph_records(pk,working)
            answers=f.jev(root,ports,{'paragraph_key':pk,'text':paragraph['text'],'records':bound}, {
                'missing_lower':f.yn('教授在這段提出的實質論據、經文觀察或解釋依據，是否有任何部分未被 records 表達？'
                    '只查下層內容覆蓋，口頭強調與重複不算，已有忠實概括不算漏抽。'),
                'conclusion_candidate':f.yn('這段是否有值得交 GPT 核對保存方式的實質判斷候選？'
                    '只作寬鬆候選篩選，不判斷有無獨立支持，也不判斷是否應成為 Claim。'
                    '單純故事細節、例子、設問或口頭強調不算。已有連接不排除候選。')})
            coverage.append({'paragraph_key':pk,'precheck':answers})
            if any(v!='no' for v in answers.values()):
                # Complete support can span paragraphs: offer adjacent context, not a hard heading boundary.
                keys=list(sources[source_id]['paragraphs']);n=keys.index(pk)
                context={k:sources[source_id]['paragraphs'][k]['text'] for k in keys[max(0,n-1):n+2]}
                bound={k:v for p in context for k,v in paragraph_records(p,working).items()}
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
        rel=pairs.review(working,sources,lower_edges,lower_pairs,root/'relations',ports,kind='lower')
        updates.update(rel['updates']);pending.extend(rel['pending'])
        for payload in rel['new_relations']:
            rid='KR-FL-'+e.digest(payload)[:24]
            updates['knowledge_relations/'+rid]={'relation_id':rid,**payload}
        verified=f.apply_overlay(records,updates,generated)
        f.source_unchanged(sources)
        result={'version':f.VERSION,'stage':'lower','status':'needs_attention' if pending else 'verified',
            'source_id':source_id,'updates':updates,'new_fragments':generated,'retiring_keys':rel['retiring_keys'],
            'outcomes':outcomes,'coverage':coverage,'conclusion_candidates':conclusions,'pending':pending,
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
    return result
