"""Grouped lower verification with source-prepared candidates for flagged records."""
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
from backend.pipeline import claim_human_source as human

VERSION = 'claim_flash_grouped_evidence_v1'
COLLECTIONS = {'evidence_steps','observations'}
FIELDS = ('statement','speaker','stance','attribution','scripture_refs')
SOURCE_INTERPRETATION = (
    'human_source_decisions是已保存的人工來源澄清，只在applicability=current的指定段落內解釋原文。'
    '按澄清核對所指意思，逐字引文仍保留原樣；不推定錄音字音、錯誤原因或Claim已獲批准。'
    '未涵蓋的內容仍須正常核對，記錄自己聲稱有人工確認不能代替這份依據。')
PROMPT = (
    '只處置Evidence/Observation本身，不審Claim、EvidenceStep論證、問題答案或節點關係。'
    'sources只包含已驗證的SourceFragments；不查整篇原文、不補未掛片段作為自身支持。'
    'Jev只提供線索；GPT可修正或有據保留。檢查statement全部意思、否定、條件、程度、模態、'
    '發言者及採納立場；合理同義概括可保留，不為吹毛求疵增加限定。保存教授引用的經文及反方。'
    '經文規範化節次不要求片段逐字念出；缺上下文時保留並交接，不因局部未重述就刪除既有經文。'
    '只輸出必要欄位修改及簡短理由。keep不改記錄。缺少來源用needs_source交回原文層，'
    '不得改片段、連接、owners、審閱狀態或資格。每個job_id恰好一次。'
    + f.KNOWLEDGE_TEXT_RULES +
    '既有記錄若只有準確的轉場，不憑空改成實質論據，也不在本任務越權刪除；keep時明示其僅為轉場。')
COVERAGE_PROMPT = PROMPT + (
    '本次只處置指定重要意思是否已保存，不全面重審參考記錄。優先enrich既有Evidence/Observation，'
    '必要才create一筆，不把口頭語、純轉場、預告或重複拆成新卡，不新建Claim。'
    'enrich只准writable_keys；其他組記錄需要修改用handoff，不能為繞過範圍重複create。'
    'fragment_ids只能選所給sources，須完整覆蓋候選原話，不要求採用全部候選ID。'
    'enrich只能使用該記錄已掛載的片段；需要改來源綁定用needs_source，不繞過原文層。'
    '本組多個候選若enrich同一記錄，每項須給相同、完整保存全部相關候選的最終表述，不能互相覆蓋。'
    'keep表示已有保存或確定不必另存；需要更多原話用needs_source。')

FOCUSED_CONTRACT='claim_flash_jev_findings_only_v1'
FOCUSED_PROMPT=(
    '唯一任務：核對每個job.review_focus列出的Jev疑點，保留或作最小修正。Jev可能誤判，不能盲從。'
    'record、sources及其他已通過成分只作上下文，不重新全面審閱。只准改writable_fields列出的欄位；'
    '若只列stance，就只判採納立場，不重寫已通過的statement。修訂statement時保留其他已通過意思。'
    '只用已掛SourceFragments；缺必要上下文用needs_source交回原文層，不自行借鄰段。'
    '不審Claim、論證或節點關係。keep表示原值可保留。只輸出必要修改和針對疑點的簡短理由，'
    '每個job_id恰好一次；未改欄位用null。保留教授的經文引用。'+f.KNOWLEDGE_TEXT_RULES)
COMPLETION_CONTRACT='claim_flash_evidence_source_completion_v1'
COMPLETION_RULES=(
    '本次來源層已先提供候選原文：source_paragraphs按段落ID共用，'
    '每項只能選source_candidates.nearby_paragraph_keys列出的段落；候選不是支持成立的結論。'
    '一次處置指定Jev疑點：keep保留；repair可修改摘要／獲准欄位、補充quotes，或兩者同時做。'
    'quotes是需補充的完整逐字原文，含paragraph_key與quote；原有效片段由程式保留。'
    '若判定依賴尚未掛載的候選原文，必須repair並輸出quotes；不得只保留或改摘要卻漏掛所需原文。'
    '若候選仍不能解決必要來源缺口，才用needs_source。'
    'keep及needs_source不得夾帶修改或quotes；不為支持摘要借用無關原文。'
    '若實際修改statement改變內容歸屬或命題，須在同一答覆同步修正statement_change_fields所列的相關分類；'
    '原分類只對舊摘要成立。由「教授承認」改為「聽眾表示」時，speaker也須明確輸出audience，'
    '不能只在statement或reason說已修正歸屬，卻漏改speaker。未改statement時，仍只准修改writable_fields，不重判已通過欄位。'
    '不審Claim、論證關係或其他已通過記錄。')


def completion_prompt(focused=False):
    base=FOCUSED_PROMPT if focused else PROMPT
    base=base.replace('只用已掛SourceFragments；缺必要上下文用needs_source交回原文層，不自行借鄰段。','')
    base=base.replace('sources只包含已驗證的SourceFragments；不查整篇原文、不補未掛片段作為自身支持。','')
    base=base.replace('缺少來源用needs_source交回原文層，','')
    base=base.replace('缺上下文時保留並交接，','必要上下文先核對所給候選，')
    base=base.replace('只准改writable_fields列出的欄位；',
        '未改statement時，只准改writable_fields；實際修改statement時，也准同步修正statement_change_fields所列的相關分類；')
    return base+COMPLETION_RULES+q.gpt_role_rules()


def completion_input(data):
    paragraphs={}
    for job in data['jobs']:
        value=job['source_candidates'];keys=[]
        for paragraph in value.pop('nearby_paragraphs'):
            pk=paragraph['paragraph_key']
            if pk in paragraphs and paragraphs[pk]!=paragraph:raise ValueError('conflicting source candidate paragraph')
            paragraphs[pk]=paragraph;keys.append(pk)
        job['source_candidates']={'nearby_paragraph_keys':keys,'source_lookup':value['source_lookup']}
    data['source_paragraphs']=paragraphs
    data['source_completion_contract']=COMPLETION_CONTRACT
    return data


def completion_schema(schema,job_ids):
    """Require the first reply to cover the batch, not merely its recovery reply."""
    result=deepcopy(schema);array=result['properties']['items']
    array.update(minItems=len(job_ids),maxItems=len(job_ids))
    definition=array['items']
    if '$ref' in definition:definition=result['$defs'][definition['$ref'].rsplit('/',1)[1]]
    definition['properties']['job_id']['enum']=job_ids
    return result


def assert_candidate_context(decision,all_decisions):
    context=decision.get('source_context')
    if context and human.scoped(all_decisions,context['paragraphs'])!=context['entries']:
        raise ValueError('Evidence candidate source interpretation changed; explicit continuation required')


def focused_request(request):
    result=deepcopy(request)
    result['system']=completion_prompt(True) if result['input'].get('source_completion_contract')==COMPLETION_CONTRACT else FOCUSED_PROMPT
    result['focused_contract']=FOCUSED_CONTRACT
    for job in result['input']['jobs']:
        issues={k:v for k,v in job['jev'].items() if not k.endswith('_classification')
            and v!=('supported' if k.startswith('component_') else 'matched' if k.endswith('_consistency') else 'faithful')}
        fields=set()
        if any(k in {'record_fidelity','decomposition_fidelity'} or k.startswith('component_') for k in issues):
            fields.update({'statement','scripture_refs'})
        if 'speaker_consistency' in issues:fields.update({'speaker','attribution'})
        if 'stance_consistency' in issues:fields.add('stance')
        if not issues:raise ValueError('focused GPT job has no Jev finding')
        job.update(review_focus=issues,writable_fields=sorted(fields))
        if result['input'].get('source_completion_contract')==COMPLETION_CONTRACT and 'statement' in fields:
            job['statement_change_fields']=['speaker','stance','attribution']
    return result


def validate_focused_reply(request,reply):
    jobs={j['job_id']:j for j in request['input']['jobs']}
    schema=CompletionEdits if request['input'].get('source_completion_contract')==COMPLETION_CONTRACT else Edits
    for edit in schema.model_validate(reply).items:
        if edit.action!='repair':continue
        job=jobs[edit.job_id]
        changed={field for field in FIELDS if getattr(edit,field) is not None
                 and getattr(edit,field)!=job['record'].get(field)}
        allowed=set(job['writable_fields'])
        if 'statement' in changed and 'statement' in allowed:
            allowed.update(job.get('statement_change_fields',[]))
        if not changed<=allowed:
            raise ValueError('focused Evidence repair changed a field outside the Jev findings')


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


class CompletionEdit(Edit):
    quotes: list[e.Quote] | None = None


class CompletionEdits(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    items: list[CompletionEdit]


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


def coverage_edits(edits,jobs):
    """A source unit can enrich several records; each write must be distinct."""
    expected={job['job_id'] for job in jobs}
    if {edit.job_id for edit in edits}!=expected:
        raise ValueError('coverage disposition must address every unit and no unknown unit')
    for unit_id in expected:
        local=[edit for edit in edits if edit.job_id==unit_id]
        if len(local)<2:continue
        if any(edit.action not in {'enrich','create'} for edit in local):
            raise ValueError('repeated coverage unit has conflicting decisions')
        keys=[('enrich',edit.record_key) if edit.action=='enrich' else
              ('create',edit.record_type,edit.statement,tuple(sorted(edit.fragment_ids))) for edit in local]
        if len(keys)!=len(set(keys)):
            raise ValueError('repeated coverage unit writes the same record twice')
    return edits


def covered_handoff(root,job,records,sources,ports):
    """Consume a focused successor; never rewrite the original handoff reply."""
    folder=Path(root)/'handoff-resolutions'/e.digest(job)
    if not (folder/'resolution.json').exists():return None
    context=json.loads((folder/'input.json').read_text());saved=json.loads((folder/'resolution.json').read_text())
    sid=next(iter(sources))
    if (context['source']!=sources[sid]['file'] or context['unit']!=job
            or saved['input_sha256']!=e.digest(context) or saved['database_writes']!=0):
        raise ValueError('coverage handoff successor source/input differs')
    for key,expected in context['material'].items():
        p=records[key]['payload']
        projected={field:p[field] for field in ('statement','title','speaker','stance','attribution','scripture_refs') if field in p}
        value=e.material({**p,'evidence_step_id':key,'statement':p.get('statement',p.get('title',''))},records,sources)
        if expected!={'record':projected,'sources':value['fragments']}:
            raise ValueError('coverage handoff successor material changed')
    original=Path(context['original_request']['path'])
    if (e.digest(json.loads(original.read_text()))!=context['original_request']['sha256']
            or e.digest(json.loads((original.parent/'response.json').read_text()))!=context['original_reply_sha256']):
        raise ValueError('original coverage handoff changed')
    replies=[]
    for path in folder.glob('gpt/*/request.json'):
        req=json.loads(path.read_text());reply=path.parent/'response.json'
        if (req['identity']!=ports.identity['gpt'] or req['input']['task']!='evidence_coverage_handoff_disposition'
                or len(req['input']['jobs'])!=1 or e.digest(req)!=path.parent.name or not reply.exists()):continue
        actual=req['input']['jobs'][0]
        if actual['unit']==job['unit'] and actual['existing_records']==context['material']:
            replies.extend(json.loads(reply.read_text())['items'])
    verdict=saved['verdict']
    if replies!=[verdict] or verdict['job_id']!=job['job_id']:
        raise ValueError('coverage handoff successor lacks its exact saved model reply')
    if verdict['action']!='covered':return None
    if not verdict['record_keys'] or not set(verdict['record_keys'])<=set(context['material']):
        raise ValueError('coverage handoff successor cites an unprovided record')
    return {**verdict,'resolution_path':str(folder/'resolution.json'),'resolution_sha256':e.digest(saved)}


def visible(payload):
    return {k:payload[k] for k in FIELDS if k in payload}


def reference_visible(payload):
    # Supply the content and bindings used by the coverage contract. They
    # remain read-only; enrichment cannot change a record's source list.
    return {**visible(payload), **({'title':payload['title']} if 'title' in payload else {}),
            'source_fragment_ids':e.evidence_fragment_ids(payload)}


def coverage_request_material(ports, jobs, shared):
    """Pack repeated field names only when the complete request is oversized."""
    prompt=COVERAGE_PROMPT
    request={'identity':ports.identity['gpt'],'system':prompt,
             'input':{'task':'evidence_coverage_repair','jobs':jobs,**shared},
             'schema':MissingEdits.model_json_schema()}
    if len(json.dumps(request,ensure_ascii=False).encode())<=f.GPT_BYTES:return prompt,shared
    packed=deepcopy(shared)
    columns=['paragraph_key','quote']
    packed.update(source_columns=columns,
                  sources={fid:[value[field] for field in columns] for fid,value in shared['sources'].items()})
    prompt+=('本次sources每個fragment_id的值為陣列，欄位順序由source_columns定義；'
             'paragraph_key與quote仍是完整位置及逐字原文，所有ID均保留。')
    request.update(system=prompt,input={'task':'evidence_coverage_repair','jobs':jobs,**packed})
    if len(json.dumps(request,ensure_ascii=False).encode())>f.GPT_BYTES:
        fields={name:str(i) for i,name in enumerate(sorted({field for record in shared['records'].values() for field in record}))}
        packed.update(record_fields={alias:name for name,alias in fields.items()},
                      records={key:{fields[field]:value for field,value in record.items()} for key,record in shared['records'].items()})
        prompt+='records內的欄位鍵按record_fields還原為完整欄位名稱；物件ID、欄位值與來源清單不變。'
    return prompt,packed


def material(key, records, sources, decisions=None):
    payload = records[key]['payload']
    value = e.material({**payload,'evidence_step_id':key},records,sources)
    # Deliberately do not use f.value: no between-fragment expansion or nearby paragraphs.
    result = {k:value[k] for k in ('fragments','original_text_excerpts')}
    paragraphs = {}
    for fragment in value['fragments']:
        sid = records['source_fragments/'+fragment['fragment_id']]['payload']['source_id']
        paragraphs.setdefault(sid, []).append(fragment['paragraph_key'])
    if decisions is None:
        decisions = [x for src in sources.values() for x in src.get('human_source_guard', {}).get('entries', [])]
    selected = human.scoped(decisions, paragraphs)
    if selected:
        projected = {sid:{'file':s['file'],'paragraphs':s['paragraphs']} for sid,s in sources.items()}
        human.project(projected, selected)
        facts = [fact for sid in sorted(projected) for fact in projected[sid].get('human_source_decisions', [])]
        if any(fact['applicability'] != 'current' for fact in facts):
            raise ValueError('human source clarification needs reconfirmation for changed paragraph')
        result.update(human_source_decisions=facts, source_interpretation=SOURCE_INTERPRETATION)
    return result


def fingerprint(key, payload, value):
    return e.digest({'key':key,'record':visible(payload),'fragments':value['fragments'],
        **({k:value[k] for k in ('human_source_decisions','source_interpretation')} if value.get('human_source_decisions') else {})})


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
        if req['input']['task'] != 'source_preservation_inventory' or req['system'] not in coverage.INVENTORY_PROMPTS:
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


def run_group(store, records, sources, group, targets, units, root, ports, previous, *, apply, complete_source=False):
    sid = next(iter(sources)); working = dict(records); updates = {}; generated = {}; outcomes = []; pending = []; values = {}; ready = {}
    pool, _ = group_records(group, records, targets)
    paragraphs = {sid: sorted(set(group['paragraph_keys']) | {p['paragraph_key'] for p in pool.values()})}
    all_decisions=store.read_human_source_decisions([sid]) if store is not None else sources[sid].get('human_source_guard',{}).get('entries',[])
    decisions = human.scoped(all_decisions, paragraphs)
    guard = {'paragraphs': paragraphs, 'entries': decisions}
    for key in targets:
        try:
            values[key]=material(key,working,sources,decisions)
        except (KeyError,ValueError) as exc:
            pending.append({'object_key':key,'layer':'source','reason':str(exc)});continue
        fp=fingerprint(key,working[key]['payload'],values[key]); accepted=root/'accepted'/(fp+'.json')
        if accepted.exists():
            proof=json.loads(accepted.read_text());decision=json.loads(Path(proof['decision_path']).read_text())
            if proof['decision_sha256']!=e.digest(decision) or proof['fingerprint']!=fp:
                raise ValueError('Evidence acceptance proof differs')
            assert_candidate_context(decision,all_decisions)
            outcomes.append({'object_key':key,'basis':'reused_accepted_input'});continue
        path=root/'decisions'/fp/'result.json'
        if path.exists():
            ready[key]=json.loads(path.read_text());assert_candidate_context(ready[key],all_decisions)
    todo={k:working[k]['payload']['statement'] for k in values if k not in ready and not any(x.get('object_key')==k for x in outcomes)}
    parts=decompose(root,ports,todo);jobs=[]
    for key in todo:
        answers=components.check(root/'fidelity',ports,key.split('/')[0],visible(working[key]['payload']),values[key],parts[key],fragment_only=True)
        if components.needs_disposition(answers):
            jobs.append({'job_id':key,'record':visible(working[key]['payload']),'sources':values[key]['fragments'],
                         'components':parts[key],'jev':answers,
                         **{k:values[key][k] for k in ('human_source_decisions','source_interpretation') if k in values[key]}})
        else:ready[key]={'patch':{},'basis':'jev_precheck','reason':'本層逐項檢查無異議。'}
    candidates={}
    if complete_source and jobs:
        candidates=source.binding_candidates(jobs,working,sources,group['paragraph_keys'],
            root/'fidelity-source'/e.digest(jobs),ports)
        paragraphs[sid]=sorted(set(paragraphs[sid])|{p['paragraph_key'] for value in candidates.values() for p in value['nearby_paragraphs']})
        decisions=human.scoped(all_decisions,paragraphs);guard={'paragraphs':paragraphs,'entries':decisions}
        for job in jobs:
            value=candidates[job['job_id']];job['source_candidates']=value
            scope={sid:[p['paragraph_key'] for p in value['nearby_paragraphs']]}
            facts=human.scoped(all_decisions,scope)
            projected=deepcopy(sources);human.project(projected,facts)
            facts=projected[sid].get('human_source_decisions',[])
            if any(fact['applicability']!='current' for fact in facts):
                raise ValueError('human source clarification needs reconfirmation for candidate paragraph')
            if facts:job.update(human_source_decisions=facts,source_interpretation=SOURCE_INTERPRETATION)
    schema=CompletionEdits if complete_source else Edits
    for batch,raw in f.gpt(root/'fidelity-edits',ports,'evidence_fidelity_repair',jobs,schema.model_json_schema(),
            completion_prompt() if complete_source else PROMPT,semantic_review=False,
            project_input=completion_input if complete_source else None):
        replies=schema.model_validate(raw).items;f.exactly([x.model_dump() for x in replies],[j['job_id'] for j in batch],'job_id')
        for edit in replies:
            quotes=getattr(edit,'quotes',None)
            changed=any(getattr(edit,k) is not None and getattr(edit,k)!=working[edit.job_id]['payload'].get(k) for k in FIELDS)
            if complete_source and edit.action!='repair' and (quotes or changed):
                raise ValueError('non-repair disposition contains modifications')
            if edit.action=='needs_source':
                pending.append({'object_key':edit.job_id,'layer':'source','reason':edit.reason,
                    **({'source_completion_attempted':True} if complete_source else {})});continue
            key=edit.job_id;p=working[key]['payload'];patch=patch_for(key,p,edit);fragments={}
            if quotes:
                post,fragments=source.supplement_binding(key,{**p,**patch},[q.model_dump() for q in quotes],
                    candidates[key],working,sources)
                post=KNOWLEDGE_COLLECTIONS[key.split('/')[0]][0].model_validate(post).model_dump(mode='json')
                patch={k:v for k,v in post.items() if p.get(k)!=v}
            ready[key]={'patch':patch,'basis':'gpt_'+edit.action,'reason':edit.reason,
                        **({'new_fragments':fragments} if fragments else {})}
            if complete_source:
                scope={sid:sorted({p['paragraph_key'] for p in candidates[key]['nearby_paragraphs']})}
                ready[key]['source_context']={'paragraphs':scope,'entries':human.scoped(all_decisions,scope)}
    # Check the compiled changes before accepting any disposition in this group.
    for key,decision in ready.items():
        if decision['basis']=='gpt_repair' and not decision['patch']:
            raise ValueError('Evidence repair has no effective changes: '+key)
    for key,decision in ready.items():
        p=working[key]['payload'];fp=fingerprint(key,p,values[key]);path=root/'decisions'/fp/'result.json'
        e.save(path,decision)
        if decision['patch'] and p.get('review_status') in f.PROTECTED:
            pending.append({'object_key':key,'layer':'evidence','reason':'protected_record'});continue
        fragments=decision.get('new_fragments',{})
        generated.update(fragments);working=f.apply_overlay(working,{},fragments)
        post={**p,**decision['patch']}
        if post!=p:updates[key]=post;working[key]={**working[key],'payload':post}
        values[key]=material(key,working,sources,decisions)
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
    projected = deepcopy(sources)
    human.project(projected, decisions)
    facts = projected[sid].get('human_source_decisions', [])
    if facts:
        if any(fact['applicability'] != 'current' for fact in facts):
            pending.append({'layer':'source','reason':'human source clarification needs reconfirmation for changed paragraph'})
            units=[]
        else:
            state.update(human_source_decisions=facts,source_interpretation=SOURCE_INTERPRETATION)
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
    instruction,shared=coverage_request_material(ports,grouped,
        {'records':{k:reference_visible(p) for k,p in catalog.items()},'writable_keys':targets,
                **{k:state[k] for k in ('human_source_decisions','source_interpretation') if k in state},
                'sources':{p['fragment_id']:{'paragraph_key':p['paragraph_key'],'quote':p['verbatim_excerpt']} for p in valid.values()}})
    for batch,raw in f.gpt(root/'coverage-edits',ports,'evidence_coverage_repair',grouped,MissingEdits.model_json_schema(),instruction,
        shared=shared,semantic_review=False):
        bundles=MissingEdits.model_validate(raw).items;f.exactly([x.model_dump() for x in bundles],[j['job_id'] for j in batch],'job_id')
        edits=coverage_edits(bundles[0].items,jobs)
        for edit in edits:
            if edit.action=='keep':outcomes.append({'unit_id':edit.job_id,'basis':'gpt_keep','reason':edit.reason});continue
            if edit.action in {'needs_source','handoff'}:
                job=next(j for j in jobs if j['job_id']==edit.job_id)
                resolution=covered_handoff(root,job,records,sources,ports)
                if resolution:
                    outcomes.append({'unit_id':edit.job_id,'basis':'gpt_handoff_resolution',**resolution});continue
                target={}
                if edit.action=='needs_source' and edit.record_key:
                    if edit.record_key not in targets or edit.record_key.split('/')[0]!=edit.record_type:
                        raise ValueError('coverage source handoff attempted other-group target')
                    target={'object_key':edit.record_key}
                pending.append({'unit_id':edit.job_id,'layer':'source' if edit.action=='needs_source' else 'handoff',
                                'reason':edit.reason,**target});continue
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
            # Persist the same schema defaults SQL will store, so acceptance
            # fingerprints survive the transaction without a second review.
            post=KNOWLEDGE_COLLECTIONS[edit.record_type][0].model_validate(post).model_dump(mode='json')
            if key in coverage_updates and post!=coverage_updates[key]:raise ValueError('coverage edits conflict on the same Evidence')
            coverage_updates[key]=post
            updates[key]=post;working[key]={**working.get(key,{}),'payload':post,'retired':False}
            value=material(key,working,sources,decisions);fp=fingerprint(key,post,value)
            decision={'postimage':visible(post),'fragment_ids':e.evidence_fragment_ids(post),'reason':edit.reason}
            path=root/'coverage-decisions'/coverage_fingerprint(state)/(e.digest([job,decision])+'.json');e.save(path,decision)
            accepted=root/'accepted'/(fp+'.json')
            coverage_proofs.append((accepted,{'fingerprint':fp,'decision_path':str(path.resolve()),'decision_sha256':e.digest(decision)}))
            outcomes.append({'unit_id':edit.job_id,'basis':'gpt_'+edit.action,'object_key':key,'reason':edit.reason})
    if units and not pending:
        for accepted,proof in coverage_proofs:
            if not accepted.exists():e.save(accepted,proof)
        post_state=coverage_state(group_records(group,working,targets)[1])
        post_state.update({k:state[k] for k in ('human_source_decisions','source_interpretation') if k in state})
        decision={'unit_outcomes':[x for x in outcomes if 'unit_id' in x], 'jev':answers,
                  'postimage_fingerprint':coverage_fingerprint(post_state)}
        path=root/'coverage-decisions'/coverage_fingerprint(state)/(e.digest(decision)+'-accepted-outcome.json')
        e.save(path,decision)
        accepted=root/'coverage-accepted'/(coverage_fingerprint(post_state)+'.json')
        if not accepted.exists():e.save(accepted,{'decision_path':str(path.resolve()),'decision_sha256':e.digest(decision)})
    read_keys=set(targets)|set(catalog)|set(valid)|{'source_documents/'+sid}
    for key in targets:
        read_keys.update('source_fragments/'+fid for fid in e.evidence_fragment_ids(records[key]['payload']))
    frozen={k:records[k] for k in read_keys if k in records}
    result={'version':VERSION,'status':'needs_attention' if pending else 'verified','source_id':sid,'group_id':group['group_id'],
        'updates':{k:p for k,p in updates.items() if k not in records or p!=records[k]['payload']},'new_fragments':generated,
        'outcomes':outcomes,'pending':pending,'applied':False,'claim_qualification':'not_granted',
        'commit_reason':'Grouped fragment-bound Evidence/Observation dispositions only'}
    if complete_source:
        result.update(source_completion_contract=COMPLETION_CONTRACT,
            commit_reason='Evidence/Observation dispositions with program-compiled literal source supplements')
    if store is not None:result['human_source_guard']=guard
    stage=root/'groups'/group['group_id']/e.digest({'before':{k:(v.get('revision'),v.get('content_sha256'),v['payload']) for k,v in frozen.items()},'result':result})
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
        complete_source=not saved_intent or saved_intent.get('source_completion_contract')==COMPLETION_CONTRACT
        authorized=saved_intent['selected'] if saved_intent else selected
        if not set(selected)<=set(authorized):raise ValueError('Evidence group selection expands frozen scope')
        e.save(root/'intent.json',{'version':VERSION,'source':pin,'groups':plan,'selected':authorized,'models':ports.identity,
            'gpt_output_limit':getattr(getattr(ports,'gpt',None),'max_output_tokens',None),
            'policy':e.digest([PROMPT,COVERAGE_PROMPT,components.PROMPT,Edits.model_json_schema(),MissingEdits.model_json_schema()]),
            **({'source_completion_contract':COMPLETION_CONTRACT,
                'source_completion_policy':e.digest([completion_prompt(),completion_prompt(True),CompletionEdits.model_json_schema()])} if complete_source else {}),
            'previous':str(Path(previous).resolve())})
        for started in root.rglob('started.json'):
            if not (started.parent/'response.json').exists():
                if (started.parent.parent.name=='jev' and (started.parent/'rejection.json').exists()
                        and e.rejection_receipt(started.parent,
                        json.loads((started.parent/'request.json').read_text()))):
                    continue  # Retained refusal; a different corrected input may proceed.
                if started.parent.parent.name=='gpt' and (started.parent/'returned-answer-admission.json').exists():
                    from backend.pipeline.claim_flash_answer_reuse import returned_answer
                    returned_answer(started.parent.parent.parent,ports,
                        json.loads((started.parent/'request.json').read_text()))
                    continue  # Same exact successful answer; old timeout remains unknown.
                if started.parent.parent.name=='gpt' and (started.parent/'owner-recovery.json').exists():
                    f.stopped_gpt_authority(started.parent.parent.parent,ports,
                        json.loads((started.parent/'request.json').read_text()))
                else:raise ValueError('Evidence call has no saved reply; inspect original transport before continuing')
        # Complete the original receipt after a post-commit crash, even when
        # the current postimage already has a reusable semantic decision.
        for intent in sorted((root/'groups').glob('*/*/commit-intent.json')):
            if (intent.parent/'commit.json').exists():continue
            saved=json.loads((intent.parent/'verification.json').read_text())
            if (saved.get('version')!=VERSION or any(k.split('/')[0] not in COLLECTIONS for k in saved['updates'])
                    or saved.get('new_fragments') and (saved.get('source_completion_contract')!=COMPLETION_CONTRACT
                        or any(not k.startswith('source_fragments/') for k in saved['new_fragments']))):
                raise ValueError('Evidence recovery write scope differs')
            recovered=e.commit_verified(store,intent.parent)
            if recovered.get('applied'):
                for key in recovered['updates']:records[key]=store.get_record_state(*key.split('/',1))
                for key in recovered['new_fragments']:records[key]=store.get_record_state(*key.split('/',1))
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
            result=run_group(store,records,sources,group,targets,[u for u in units if u['paragraph_key'] in group['paragraph_keys']],root,ports,previous,apply=apply,
                complete_source=complete_source)
            results.append(result)
            if result.get('applied'):
                for key in result['updates']:
                    if key in records and visible(records[key]['payload'])!=visible(result['updates'][key]):
                        affected.update((readers.get(key,set())&finished)-{group['group_id']})
                    records[key]=store.get_record_state(*key.split('/',1))
                for key in result['new_fragments']:records[key]=store.get_record_state(*key.split('/',1))
            elif result['updates'] or result['pending']:break  # No later group consumes an unapplied draft.
            if not result['pending']:finished.add(group['group_id']);affected.discard(group['group_id'])
        return e.publish(root,{'version':VERSION,'source_id':source_id,'groups':results,
            'requested_group_ids':selected,'authorized_group_ids':authorized,'affected_prior_group_ids':sorted(affected),
            'status':'completed' if not affected and len(results)==len(selected) and all(not g['pending'] for g in results) and (apply or not any(g['updates'] for g in results)) else 'needs_attention',
            'claim_qualification':'not_granted'})
