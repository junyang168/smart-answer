"""Read-only grouped reverse coverage for Flash. No Update or content approval."""
import fcntl
import json
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from backend.api.sermon_search.bible_refs import normalize_ref
from backend.pipeline import claim_flash_common as f, claim_flash_grouping as grouping
from backend.pipeline import claim_flash_source as source_stage, evidence_refresh as e
from backend.pipeline import claim_flash_questions as questions

CONTRACT = 'claim_flash_reverse_coverage_v1'
COLLECTIONS = {'evidence_steps', 'observations', 'position_nodes'}
INVENTORY_PROMPT = (
    '只從原文列出應核對是否已保存的重要概念、承重材料及必要限定，不看資料庫，不新建知識記錄。'
    'units每項一個實質意思，保留對象、否定、條件及反方角色；重複合併。'
    '不是每句判斷都須建卡：口頭語、轉場、無關細節及只預告下講而未講解的內容不列。'
    '經文只列承重內容，不要求每個分句獨立保存；例如提前4:10此處承重為所有人的救主、'
    '更是信實忠心人的救主，勞苦努力前半句不必另存。雙重國籍不能以一般責任原則代替。'
    'citations另列當下實際引用或講解的經文出處，同組同出處可合併，不因units過濾而漏掉；'
    '未明說章節就保留已知範圍，不從外部聖經猜補或糾正教授口誤。'
    'quote須連續逐字出自paragraph_key，勿抄無關文字。每個job_id恰好一次，允許空清單。')
DISPOSITION_PROMPT = (
    '只做反向查漏，不修改資料，不新建記錄，不審Claim或論證關係。每個candidate_id至少處置一次，'
    '合併同一疑點須列齊ID。all_sermon_statements是本講全部現有材料摘要，供跨組已有承載核對，'
    '不重審參考物件；position_attributions標出的R是已保存的反方Position。'
    '判缺前核對existing_details中的出處及全部綁定片段；不要因承載物件不叫Evidence而忽略它。'
    '經文記錄允許忠實概括，不因摘要未逐字重抄完整引文就判漏記；獨立重要概念也不能僅因'
    '共用段落含其文字就算已保存。必存的是承重內容及出處，不是無關經文分句。'
    '如提前4:10，後半救主內容和出處已有，就不用另存勞苦努力前半；雙重國籍是獨立重要概念。'
    '只預告下一講、口頭語、重複不算遺漏。未明說節次不算漏記，不用外部知識猜補。'
    '兩端材料已在、僅涉及如何論證時用claim_layer_out_of_scope；本次未查連接，不可斷言它真缺失。'
    '歷史引文與未驗證綁定不作現版來源批准。對原文節次和現有記錄不一致，記入source_notes，'
    '不自動改原文，也不單憑差異判漏記。quote逐字出自所給原文；record_refs只用提供R代號，理由簡短具體。')


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Unit(Strict):
    paragraph_key: str
    quote: str = Field(min_length=1)
    statement: str = Field(min_length=1)


class Citation(Strict):
    paragraph_key: str
    quote: str = Field(min_length=1)
    reference: str = Field(min_length=1)


class Inventory(Strict):
    job_id: str
    units: list[Unit]
    citations: list[Citation]


class Inventories(Strict):
    items: list[Inventory]


class Finding(Strict):
    candidate_ids: list[str] = Field(min_length=1)
    paragraph_key: str
    verdict: Literal['missing_material', 'missing_source_binding', 'missing_scripture_reference',
                     'already_recorded', 'not_required', 'claim_layer_out_of_scope', 'needs_context']
    quote: str = Field(min_length=1)
    statement: str
    reference: str
    record_refs: list[str]
    reason: str = Field(min_length=1)
    source_notes: list[str]


class Disposition(Strict):
    job_id: str
    findings: list[Finding]


class Dispositions(Strict):
    items: list[Disposition]


def expand_reference(text):
    """Expand explicit numeric shorthand only; unknown chapters stay unknown."""
    if any(x in text for x in ('未明示', '章未', '不明', '未知')):
        return [], [text]
    refs = []; unknown = []; previous = None
    for part in re.split('[；;、,，]', text):
        part = part.strip()
        if not part:
            continue
        if re.fullmatch(r'\d+(?:[-–—]\d+)?', part) and previous and previous.verse_start is not None:
            part = f'{previous.book_zh} {previous.chapter_start}:' + part
        elif re.fullmatch(r'\d+:\d+(?:[-–—]\d+)?', part) and previous:
            part = previous.book_zh + ' ' + part
        value = normalize_ref(part)
        if value:
            refs.append(value); previous = value
        else:
            unknown.append(part)
    return refs, unknown


def covering_records(records, wanted):
    if wanted.chapter_end not in (None, wanted.chapter_start):
        return []  # No guessed verse counts across chapters; leave to disposition.
    eligible = []
    for key, row in records.items():
        for raw in row.get('scripture_refs', []):
            refs, _ = expand_reference(raw)
            eligible.extend((key, r) for r in refs if r.book == wanted.book
                and r.chapter_start == wanted.chapter_start and r.chapter_end in (None, r.chapter_start))
    if wanted.verse_start is None:
        return sorted({key for key, _ in eligible})
    target = set(range(wanted.verse_start, (wanted.verse_end or wanted.verse_start) + 1))
    seen = set(); matches = set()
    for key, r in eligible:
        if r.verse_start is not None:
            overlap = target & set(range(r.verse_start, (r.verse_end or r.verse_start) + 1))
            if overlap:
                seen.update(overlap); matches.add(key)
    return sorted(matches) if target <= seen else []


def validate_groups(plan, source_id, source):
    if plan.get('source_id') != source_id or plan.get('source_body_sha256') != source['body_sha256']:
        raise ValueError('coverage grouping source/version differs')
    groups = plan.get('groups', []); paragraphs = source['paragraphs']; rank = {pk:i for i,pk in enumerate(paragraphs)}
    ids = [g['group_id'] for g in groups]; flat = [pk for g in groups for pk in g['paragraph_keys']]
    if (not groups or len(ids) != len(set(ids)) or any(not g['paragraph_keys'] for g in groups)
            or len(flat) != len(set(flat)) or set(flat) != set(paragraphs)):
        raise ValueError('coverage grouping must cover source paragraphs exactly once')
    if any(g['paragraph_keys'] != sorted(g['paragraph_keys'], key=rank.get) for g in groups):
        raise ValueError('coverage paragraphs must retain source order')


def literal(item, paragraphs, receipts):
    pk = item['paragraph_key']
    if pk not in paragraphs:
        raise ValueError('coverage quoted unprovided paragraph')
    raw = item['quote']; exact = e.source_excerpt(raw, paragraphs[pk]['text'])
    if not exact or exact not in paragraphs[pk]['text']:
        raise ValueError('coverage quote is not literal source text')
    if raw != exact:
        receipts.append({'paragraph_key': pk, 'supplied': raw, 'exact': exact})
    item['quote'] = exact


def details(keys, material, records, sources, aliases):
    result = {}
    for key in sorted(keys):
        p = material[key]; bindings = []
        for fid in e.evidence_fragment_ids(p):
            try:
                value = e.material({'evidence_step_id': key, 'statement': '', 'source_fragment_ids': [fid]}, records, sources)
                fragment = value['fragments'][0]
                binding = {'paragraph_key':fragment['paragraph_key'],'quote':fragment['quote'],'binding_verified':True}
            except (KeyError, ValueError):
                old = records.get('source_fragments/'+fid, {}).get('payload', {})
                binding = {'fragment_id': fid, 'paragraph_key': old.get('paragraph_key'),
                           'historical_excerpt': old.get('verbatim_excerpt'), 'binding_verified': False}
            if binding not in bindings:
                bindings.append(binding)
        result[aliases[key]] = {'scripture_refs': p.get('scripture_refs', []), 'bindings': bindings,
            **{k:p[k] for k in ('speaker','stance','attribution') if k in p}}
    return result


def check(records, sources, source_id, plan, root, ports):
    """No store reference: this stage cannot submit any knowledge changes."""
    root = Path(root); source = source_stage.current_source(records, sources, source_id)
    validate_groups(plan, source_id, source)
    paragraphs = source['paragraphs']; receipts = []
    # Source-only intent and cache survive later changes to the material catalogue.
    e.save(root/'intent.json', {'contract': CONTRACT, 'source_id': source_id, 'source': source['file'],
        'groups': [{k:g[k] for k in ('group_id','title','paragraph_keys')} for g in plan['groups']],
        'models': ports.identity, 'database_writes': False,
        'policy_sha256':e.digest({'inventory':INVENTORY_PROMPT,'inventory_schema':Inventories.model_json_schema(),
            'disposition':DISPOSITION_PROMPT,'disposition_schema':Dispositions.model_json_schema(),
            'unit_question':questions.coverage_unit('contract target'),'collections':sorted(COLLECTIONS),
            'alias_contract':'sha256-prefix8-collision-checked'}),
        'gpt_output_limit':getattr(getattr(ports,'gpt',None),'max_output_tokens',None)})
    for started in root.rglob('started.json'):
        reply = started.parent/'response.json'
        if not reply.exists():
            raise ValueError('coverage call has no saved reply; inspect before retrying: '+str(started.parent))
        try:
            json.loads(reply.read_text())
        except (ValueError, OSError) as exc:
            raise ValueError('coverage saved reply is unreadable; inspect before retrying: '+str(reply)) from exc
    material = {k:r['payload'] for k,r in records.items() if k.split('/')[0] in COLLECTIONS and not r.get('retired')}
    aliases = {key:'R'+e.digest(key)[:8] for key in sorted(material)}
    if len(set(aliases.values())) != len(aliases): raise ValueError('coverage reference collision')
    reverse = {v:k for k,v in aliases.items()}
    statements = {aliases[k]:p.get('statement',p.get('title','')) for k,p in material.items()}
    attributions = {aliases[k]:p.get('attribution') for k,p in material.items() if k.startswith('position_nodes/')}
    positions = {key:{records['source_fragments/'+fid]['payload'].get('paragraph_key')
                      for fid in e.evidence_fragment_ids(p) if 'source_fragments/'+fid in records
                      and records['source_fragments/'+fid]['payload'].get('source_id') == source_id}
                 for key,p in material.items()}
    inventory = []
    jobs = [{'job_id':g['group_id'], 'title':g['title'],
             'paragraphs':{pk:paragraphs[pk]['text'] for pk in g['paragraph_keys']}} for g in plan['groups']]
    for batch, raw in f.gpt(root/'source-inventory', ports, 'source_preservation_inventory', jobs,
                           Inventories.model_json_schema(), INVENTORY_PROMPT, semantic_review=False):
        rows = Inventories.model_validate(raw).model_dump()['items']
        f.exactly(rows, [j['job_id'] for j in batch], 'job_id')
        for row in rows:
            job = next(j for j in batch if j['job_id'] == row['job_id'])
            for item in row['units'] + row['citations']:
                literal(item, {pk:paragraphs[pk] for pk in job['paragraphs']}, receipts)
            inventory.append(row)
    # Actual comparison inputs, not bookkeeping-only revisions, determine reuse.
    compared = {k:{'statement':statements[aliases[k]], 'type':k.split('/')[0],
                  'fragment_ids':e.evidence_fragment_ids(p),
                  **{field:p.get(field) for field in ('scripture_refs','speaker','stance','attribution')}}
                for k,p in material.items()}
    used_fragments = {fid for p in material.values() for fid in e.evidence_fragment_ids(p)}
    binding_fields = ('fragment_id','source_id','paragraph_key','verbatim_excerpt','source_sha256',
                      'anchor_state','source_segment_index','media_time','media_end_time',
                      'paragraph_text_sha256','verbatim_excerpt_sha256','source_modality')
    input_sha = e.digest({'material':compared, 'fragments':{fid:{field:records['source_fragments/'+fid]['payload'].get(field)
                    for field in binding_fields} if 'source_fragments/'+fid in records else None
                    for fid in sorted(used_fragments)}})
    review = root/'reviews'/input_sha
    e.save(review/'input.json', {'contract': CONTRACT, 'input_sha256': input_sha, 'record_keys': reverse})
    checks = []; jobs = []
    for row in inventory:
        g = next(g for g in plan['groups'] if g['group_id'] == row['job_id']); gid = g['group_id']
        local_keys = {k for k in material if positions[k]&set(g['paragraph_keys'])}
        local_keys |= set(g.get('reference_record_keys', [])) & set(material)
        local = {k:material[k] for k in local_keys}
        state = {'records': {aliases[k]:{'statement':statements[aliases[k]], 'scripture_refs':p.get('scripture_refs',[]),
                 'record_type':k.split('/')[0], 'attribution':p.get('attribution')} for k,p in local.items()}}
        specs = {'U'+str(i):questions.coverage_unit(unit['statement']) for i,unit in enumerate(row['units'])}
        batches = []; batch = {}
        for q,spec in specs.items():
            trial = {**batch,q:spec}
            if len(json.dumps({'model':e.MODEL,'state':state,'questions':trial},ensure_ascii=False).encode()) > f.JEV_BYTES:
                if not batch: raise ValueError('complete coverage item exceeds Jev limit; no truncation')
                batches.append(batch); batch = {q:spec}
            else: batch = trial
        if batch: batches.append(batch)
        # Even a single item must be checked before dispatching this group's batches.
        if any(len(json.dumps({'model':e.MODEL,'state':state,'questions':b},ensure_ascii=False).encode()) > f.JEV_BYTES for b in batches):
            raise ValueError('complete coverage item exceeds Jev limit; no truncation')
        answers = {q:'missing' for q in specs} if not local else {}
        if local:
            for batch in batches: answers.update(f.jev(root/'coverage'/gid, ports, state, batch))
        candidates = [{'candidate_id':gid+'-U'+str(i),'kind':'material','source_item':unit,'precheck':answers['U'+str(i)]}
                      for i,unit in enumerate(row['units']) if answers['U'+str(i)] != 'covered']
        citation_keys = set()
        for i,cite in enumerate(row['citations']):
            refs, unknown = expand_reference(cite['reference']); components = []; missing = bool(unknown or not refs)
            for ref in refs:
                present = covering_records(local, ref); anywhere = covering_records(material, ref)
                components.append({'osis':ref.osis,'record_refs':[aliases[k] for k in anywhere]})
                if not present: missing = True; citation_keys.update(anywhere)
            if missing:
                candidates.append({'candidate_id':gid+'-C'+str(i),'kind':'scripture_reference','source_item':cite,
                                   'components':components,'unresolved_reference':unknown})
        checks.append({'group_id':gid,'answers':answers,'candidates':candidates})
        if candidates:
            target = {x['source_item']['paragraph_key'] for x in candidates}
            needed = {k for k in material if positions[k]&target} | citation_keys
            needed |= {k for k in local_keys if k.startswith('position_nodes/')}
            jobs.append({'job_id':gid,'title':g['title'],'source_paragraphs':{pk:paragraphs[pk]['text'] for pk in g['paragraph_keys']},
                         'candidates':candidates,'existing_details':details(needed,material,records,sources,aliases)})
    e.save(review/'prechecks.json', checks)
    results = []
    for batch,raw in f.gpt(root/'dispositions', ports, 'reverse_coverage_adjudication', jobs,
                          Dispositions.model_json_schema(), DISPOSITION_PROMPT,
                          shared={'all_sermon_statements':statements,'position_attributions':attributions}, semantic_review=False):
        rows = Dispositions.model_validate(raw).model_dump()['items']
        f.exactly(rows, [j['job_id'] for j in batch], 'job_id')
        for row in rows:
            job = next(j for j in batch if j['job_id'] == row['job_id']); expected = {x['candidate_id'] for x in job['candidates']}; seen = set()
            for item in row['findings']:
                if not set(item['candidate_ids']) <= expected: raise ValueError('coverage finding has unknown candidate')
                seen.update(item['candidate_ids'])
                # A joint disposition may cover several candidates. Its supporting quote
                # must belong to one of them; preserve every candidate's own source below.
                allowed = {x['source_item']['paragraph_key'] for x in job['candidates'] if x['candidate_id'] in item['candidate_ids']}
                if item['paragraph_key'] not in allowed: raise ValueError('coverage finding has wrong source paragraph')
                literal(item, {pk:paragraphs[pk] for pk in allowed}, receipts)
                if len(allowed) > 1:
                    item['candidate_sources'] = [{'candidate_id':x['candidate_id'], **x['source_item']}
                        for x in job['candidates'] if x['candidate_id'] in item['candidate_ids']]
                if not set(item['record_refs']) <= set(reverse): raise ValueError('coverage finding invented a record')
                item['existing_record_keys'] = [reverse[r] for r in item['record_refs']]
            if seen != expected: raise ValueError('coverage disposition omitted a candidate')
            results.append(row)
    f.source_unchanged(sources)
    findings = [item for row in results for item in row['findings']]
    unresolved = [x for x in findings if x['verdict'] in {'missing_material','missing_source_binding','missing_scripture_reference','needs_context'}]
    result = {'contract':CONTRACT,'stage':'reverse_coverage','source_id':source_id,
        'status':'needs_attention' if unresolved else 'checked','groups':len(inventory),
        'units':sum(len(x['units']) for x in inventory),'citation_records':sum(len(x['citations']) for x in inventory),
        'candidate_count':sum(len(x['candidates']) for x in checks),'findings':results,
        'quote_projection':receipts,'report_path':str(review/'result.json'),
        'database_writes':0,'claim_qualification':'not_granted',
        'scope':'read-only coverage; not statement fidelity, Claim review, relationship verification or exhaustive recall proof'}
    e.save(review/'result.json', result)
    return result


def run(store, source_id, root, ports, *, plan=None, max_claims=8):
    root = Path(root).resolve(); root.mkdir(parents=True, exist_ok=True)
    with (root/'coverage.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pin = f.bind_source(store, source_id, root)
        records, sources = f.load_source(store, source_id, visual_context=True)
        if pin != {'source_id':source_id, **sources[source_id]['file']}:
            raise ValueError('source changed between binding and coverage census')
        saved_groups = root/'shared-groups.json'
        if plan is None:
            plan = json.loads(saved_groups.read_text()) if saved_groups.exists() else grouping.build(records, sources, source_id, max_claims=max_claims)
        validate_groups(plan, source_id, sources[source_id])
        e.save(saved_groups, plan)
        return check(records, sources, source_id, plan, root, ports)
