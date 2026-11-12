"""Flash source-only preparation and read-only verification; no semantic model calls.

Reuses literal binding checks and unique migration lookup. Calibration is a
preview; only an already-authorized full Update may persist its candidates.
"""
from copy import deepcopy
import fcntl
from pathlib import Path
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from backend.pipeline import claim_flash_common as f, evidence_refresh as e
from backend.pipeline import claim_flash_source_lookup as source_lookup

CONTRACT = 'claim_flash_source_verification_v2'
HISTORICAL = {'source_deleted', 'replaced_by_precise_fragments', 'context_only'}


class BindingDecision(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    job_id:str
    action:Literal['bind','needs_source']
    quotes:list[e.Quote]
    reason:str=Field(min_length=1)


class BindingDecisions(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    items:list[BindingDecision]


def shared_binding_input(data):
    """Transmit each paragraph once; each job retains its exact allowed keys."""
    paragraphs={}
    for job in data['jobs']:
        material=job['material']
        keys=[]
        for paragraph in material.pop('nearby_paragraphs'):
            key=paragraph['paragraph_key']
            if key in paragraphs and paragraphs[key]!=paragraph:
                raise ValueError('conflicting shared source paragraph')
            paragraphs[key]=paragraph;keys.append(key)
        material['nearby_paragraph_keys']=keys
    data['source_paragraphs']=paragraphs
    return data


def binding_candidates(jobs,records,sources,paragraph_keys,root,ports):
    """Source preparation before one lower disposition; candidates are not approval."""
    locator=source_lookup.Locator(sources);prepared=[]
    allowed=set(paragraph_keys)
    for job in jobs:
        value,_=locator.prepare(job['job_id'],records,sources)
        allowed.update(p['paragraph_key'] for p in value['fragments'])
        prepared.append({**job,'material':value,'verdict':job['jev']})
    for job in prepared:
        job['material']['nearby_paragraphs']=[p for p in job['material']['nearby_paragraphs'] if p['paragraph_key'] in allowed]
    scoped={sid:{**s,'paragraphs':{pk:p for pk,p in s['paragraphs'].items() if pk in allowed}} for sid,s in sources.items()}
    found=source_lookup.lookup(prepared,scoped,Path(root)/'group',ports)
    missing=[{**j,'material':found[j['job_id']]} for j in prepared
             if not found[j['job_id']]['source_lookup']['candidate_paragraph_keys']]
    if missing and any(len(s['paragraphs'])>len(scoped[sid]['paragraphs']) for sid,s in sources.items()):
        found.update(source_lookup.lookup(missing,sources,Path(root)/'sermon',ports))
    return found


def supplement_binding(key,payload,quotes,value,records,sources):
    """Compile selected exact spans and retain every previously valid attachment."""
    allowed={p['paragraph_key'] for p in value['nearby_paragraphs']}
    if any(q['paragraph_key'] not in allowed for q in quotes):
        raise e.QuoteLocationError('unprovided source repair paragraph')
    quotes=list({(q['paragraph_key'],q['quote']):q for q in quotes}.values())
    post,fragments,_=binding_postimage(key,payload,quotes,value,records,sources)
    post['source_fragment_ids']=list(dict.fromkeys(
        [p['fragment_id'] for p in value['fragments']]+post['source_fragment_ids']))
    return post,fragments


def repair_bindings(store,source_id,findings,root,ports,*,apply=False):
    """Consume lower-layer handoffs; change only literal source attachments."""
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    with (root/'run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        pin=f.bind_source(store,source_id,root)
        if (root/'verification.json').exists():
            result=json.loads((root/'verification.json').read_text())
            return e.commit_verified(store,root) if apply else result
        records,sources=f.load_source(store,source_id,visual_context=True)
        current_source(records,sources,source_id)
        if pin!={'source_id':source_id,**sources[source_id]['file']}:raise ValueError('source repair binding changed')
        intent={'source':pin,'findings':findings,'models':ports.identity}
        if (root/'intent.json').exists() and json.loads((root/'intent.json').read_text())!=intent:
            original=json.loads((root/'intent.json').read_text())
            lookup=json.loads((root/'lookup/input.json').read_text())
            before={j['job_id']:j['record'] for j in lookup['jobs']}
            keys={x.get('object_key') for x in findings}
            if (original['source']!=pin or original['models']!=ports.identity
                    or keys!={x.get('object_key') for x in original['findings']}
                    or any(x.get('layer')!='source' for x in findings)
                    or any(key not in records or before.get(key)!=f.projected(records[key]) for key in keys)):
                raise ValueError('source handoff material changed; explicit continuation required')
            e.save(root/'continuation-findings'/ (e.digest(findings)+'.json'),{'findings':findings,
                'original_intent_sha256':e.digest(original),'basis':'same source and target; preserve original lookup/answer'})
            findings=original['findings'];intent=original
        e.save(root/'intent.json',intent)
        locator=source_lookup.Locator(sources);jobs=[]
        for item in findings:
            key=item['object_key']
            if item.get('layer')!='source' or key.split('/')[0] not in f.LOWER or key not in records:
                raise ValueError('explicit existing lower-record source handoff required')
            row=records[key]
            if row['payload'].get('review_status') in f.PROTECTED:raise ValueError('protected source repair target')
            material,_=locator.prepare(key,records,sources)
            jobs.append({'job_id':key,'record':f.projected(row),'material':material,'reason':item['reason']})
        enriched=source_lookup.lookup(jobs,sources,root/'lookup',ports)
        for job in jobs:job['material']=enriched[job['job_id']]
        updates={};generated={};pending=[];outcomes=[];working=deepcopy(records)
        prompt=('唯一任務：針對下層交回的來源缺口，選出能補足指代、主詞或必要上下文的逐字原文片段。'
            '只用本項material提供的原文，不修改或審閱Evidence／Observation摘要、角色、立場或論證。'
            '保留完整有意義的語句，不只取「這種文法」「所以如此」等失去指代的短句；原有有效片段會由程式保留。'
            'quotes只列需要補充的原文；找不到就needs_source，說明具體缺口，不為維持摘要而借其他主題。'
            'Jev只提供候選位置，不是支持成立的判決。')
        batched=len(jobs)>1
        if batched:
            prompt+='各項獨立判定；共用原文在source_paragraphs，本項只能選material.nearby_paragraph_keys列出的段落。'
        for batch,raw in f.gpt(root/'selection',ports,'source_binding_completion',jobs,
                BindingDecisions.model_json_schema(),prompt,semantic_review=False,
                project_input=shared_binding_input if batched else None):
            decisions=BindingDecisions.model_validate(raw).items
            f.exactly([x.model_dump() for x in decisions],[j['job_id'] for j in batch],'job_id')
            for decision in decisions:
                key=decision.job_id;job=next(j for j in batch if j['job_id']==key)
                outcomes.append(decision.model_dump())
                if decision.action!='bind' or not decision.quotes:
                    pending.append({'object_key':key,'layer':'source','reason':decision.reason});continue
                allowed={p['paragraph_key'] for p in job['material']['nearby_paragraphs']}
                if any(q.paragraph_key not in allowed for q in decision.quotes):raise ValueError('unprovided source repair paragraph')
                # Existing valid IDs already bind their paragraph; rebuilding
                # them can fail on a repeated phrase and adds no authority.
                # Compile only the newly selected spans, then retain old IDs.
                old_ids=[p['fragment_id'] for p in job['material']['fragments']]
                quotes=[q.model_dump() for q in decision.quotes]
                quotes=list({(q['paragraph_key'],q['quote']):q for q in quotes}.values())
                post,fragments,projection=binding_postimage(key,records[key]['payload'],quotes,job['material'],working,sources)
                post['source_fragment_ids']=list(dict.fromkeys(old_ids+post['source_fragment_ids']))
                if post!=records[key]['payload']:updates[key]=post
                generated.update(fragments);working=f.apply_overlay(working,{key:post},fragments)
        f.source_unchanged(sources)
        result={'version':'claim_flash_source_binding_completion_v1','status':'needs_attention' if pending else 'verified',
            'source_id':source_id,'updates':updates,'new_fragments':generated,'pending':pending,'outcomes':outcomes,
            'applied':False,'claim_qualification':'not_granted','commit_reason':'Complete literal source attachments; no statement or argument changes'}
        e.save(root/'before.json',{'records':records,'sources':sources});e.save(root/'verification.json',result)
        return e.commit_verified(store,root) if apply else e.publish(root,result)


def excluded_context(key, records):
    """Preserve the existing unused-dialogue exclusion, never mark it verified."""
    p = records[key]['payload']; oid = key.split('/', 1)[1]
    if (not key.startswith('evidence_steps/') or p.get('step_type') != 'dialogue_context'
            or p.get('support_eligibility') != 'context_only' or p.get('produced_claim_ids')
            or p.get('review_status') in f.PROTECTED):
        return False
    for k, r in records.items():
        q = r['payload']
        if k.startswith('claims/') and oid in q.get('evidence_step_ids', []):
            return False
        if (k.startswith('knowledge_relations/') and oid in {q['from_id'], q['to_id']}
                and q['relation_type'] != 'contextualizes'):
            return False
    return True


def binding_postimage(key, record, quotes, material, records, sources):
    """Use the existing fragment builder while preserving every content field."""
    synthetic = {**record, 'evidence_step_id': key,
                 'statement': record.get('statement', record.get('text', record.get('title', ''))),
                 'speaker': record.get('speaker') or 'professor',
                 'stance': record.get('stance') or 'asserted'}
    post, fragments, projection = e.repair_postimage(synthetic, {
        'evidence_id': key, 'action': 'repair', 'statement': synthetic['statement'],
        'speaker': synthetic['speaker'], 'stance': synthetic['stance'],
        'quotes': quotes, 'reason': '唯一逐字／排版／繁簡對應，校準現版位置。'},
        material, records, sources)
    result = deepcopy(record)
    result.update(source_fragment_id=None, source_fragment_ids=post['source_fragment_ids'])
    if result.get('source_binding_status') in {'source_deleted', 'partially_deleted'}:
        result['source_binding_status'] = 'needs_claim_refresh'
    return result, fragments, projection


def prepare(records, sources):
    """The former lower-stage mechanical preparation, without content review."""
    working = deepcopy(records)
    materials = {}; calibration = {}; mechanical = []; excluded = []; outcomes = []; pending = []
    updates = {}; generated = {}; locator = None
    for key in sorted(records):
        if key.split('/')[0] not in f.LOWER or records[key].get('retired'):
            continue
        try:
            materials[key] = f.value(key, records, sources)
            p=records[key]['payload']; ids=p.get('source_fragment_ids')
            if isinstance(ids,list) and len(ids)!=len(set(ids)):
                if p.get('review_status') in f.PROTECTED:
                    pending.append({'object_key':key,'reason':'protected_duplicate_fragment_references'})
                else:
                    post={**deepcopy(p),'source_fragment_ids':list(dict.fromkeys(ids))}
                    updates[key]=post;working=f.apply_overlay(working,{key:post})
                    mechanical.append({'object_key':key,'rule':'deduplicate_identical_fragment_references',
                                       'before':ids,'after':post['source_fragment_ids']})
        except (ValueError, KeyError) as exc:
            if excluded_context(key, records):
                excluded.append(key)
                outcomes.append({'object_key': key, 'basis': 'excluded_unbound_dialogue_context',
                                 'source_verified': False, 'argument_use_permitted': False})
                continue
            if locator is None:
                locator = source_lookup.Locator(sources)
            try:
                material, quotes = locator.prepare(key, records, sources)
            except (ValueError, KeyError) as failure:
                pending.append({'object_key': key, 'reason': 'source_calibration_required', 'detail': str(failure)})
                continue
            if quotes is None:
                materials[key] = material; calibration[key] = str(exc)
                continue
            p = records[key]['payload']
            if p.get('review_status') in f.PROTECTED:
                pending.append({'object_key': key, 'reason': 'protected_lower_record'})
                continue
            post, frags, projection = binding_postimage(key, p, quotes, material, working, sources)
            allowed = {'source_fragment_id', 'source_fragment_ids', 'source_binding_status'}
            if any(post.get(k) != p.get(k) for k in set(post) | set(p) if k not in allowed):
                raise ValueError('mechanical source calibration changed record content')
            updates[key] = post; generated.update(frags)
            working = f.apply_overlay(working, {key: post}, frags)
            materials[key] = f.value(key, working, sources)
            mechanical.append({'object_key': key, 'quote_projection': projection})
    return dict(working=working, materials=materials, calibration=calibration, mechanical=mechanical,
                excluded=excluded, outcomes=outcomes, pending=pending, updates=updates, generated=generated)


def current_source(records, sources, source_id):
    """Physical source identity only; shared by the two read-only modes."""
    if set(sources) != {source_id}:
        raise ValueError('source verification requires one explicit source')
    source = sources[source_id]
    doc = records['source_documents/' + source_id]['payload']
    for field in ('source_body_sha256', 'source_sha256'):
        if doc.get(field) and doc[field] != source['body_sha256']:
            raise ValueError('registered source body differs: ' + field)
    f.source_unchanged(sources)
    return source


def verify(records, sources, source_id):
    """Report physical bindings only; no database writes, theology or statement checks."""
    source = current_source(records, sources, source_id)
    prepared = prepare(records, sources)
    owners = {}
    for key, row in records.items():
        if key.split('/')[0] in f.LOWER and not row.get('retired'):
            for fid in e.evidence_fragment_ids(row['payload']):
                owners.setdefault(fid, []).append(key)
    fragments = []
    for key, row in sorted(records.items()):
        if not key.startswith('source_fragments/') or row.get('retired'):
            continue
        p = row['payload']
        if p.get('source_id') != source_id:
            continue
        fid = key.split('/', 1)[1]; used_by = owners.get(fid, [])
        item = {'fragment_id': fid, 'paragraph_key': p.get('paragraph_key'),
                'anchor_state': p.get('anchor_state'), 'used_by': used_by, 'source_verified': False}
        if p.get('fragment_id') != fid:
            item.update(status='unresolved' if used_by else 'unreferenced_unverified',
                        reason='fragment identity differs from record key')
        elif p.get('source_modality') == 'visual':
            item.update(status='future_release', reason='visual_semantic_verification_out_of_scope')
        elif p.get('anchor_state') in HISTORICAL and not used_by:
            item.update(status='historical', reason=p['anchor_state'])
        else:
            try:
                e.material({'evidence_step_id': key, 'statement': '', 'source_fragment_ids': [fid]}, records, sources)
                item.update(status='valid', source_verified=True)
            except (ValueError, KeyError) as exc:
                status = 'historical_excluded' if used_by and set(used_by) <= set(prepared['excluded']) else 'unresolved' if used_by else 'unreferenced_unverified'
                item.update(status=status, reason=str(exc))
        fragments.append(item)
    excluded = set(prepared['excluded']); updates = prepared['updates']
    visual_ids = {x['fragment_id'] for x in fragments if x['status'] == 'future_release'}
    valid_ids = {x['fragment_id'] for x in fragments if x['status'] == 'valid'}
    pending = {p['object_key']: p for p in prepared['pending']}
    entries = []
    for key in sorted(k for k in records if k.split('/')[0] in f.LOWER and not records[k].get('retired')):
        ids = set(e.evidence_fragment_ids(records[key]['payload']))
        if ids & visual_ids and ids <= visual_ids | valid_ids:
            status = 'future_release'
        elif key in excluded:
            status = 'historical_excluded'
        elif key in pending or key in prepared['calibration']:
            status = 'unresolved'
        elif key in updates:
            status = 'calibration_available'
        else:
            status = 'valid'
        entries.append({'object_key': key, 'status': status,
                        'fragment_ids': e.evidence_fragment_ids(records[key]['payload']),
                        **({'detail': pending[key]} if key in pending else {}),
                        **({'reason': prepared['calibration'][key]} if key in prepared['calibration'] else {})})
    # A payload/key mismatch must never pass merely because material() found the quote.
    bad_identity = {x['fragment_id'] for x in fragments if x.get('reason') == 'fragment identity differs from record key'}
    for item in entries:
        if bad_identity.intersection(item['fragment_ids']):
            item.update(status='unresolved', reason='fragment_identity_mismatch')
    unresolved = [x for x in entries if x['status'] == 'unresolved']
    from collections import Counter
    f.source_unchanged(sources)
    return {'contract': CONTRACT, 'stage': 'source', 'source_id': source_id,
            'source_body_sha256': source['body_sha256'], 'source_file': source['file'],
            'status': 'needs_attention' if unresolved else 'calibration_available' if updates else 'verified',
            'scope': 'existing textual source bindings; not semantic completeness or content approval',
            'records': entries, 'fragments': fragments,
            'counts': {'records': dict(Counter(x['status'] for x in entries)),
                       'fragments': dict(Counter(x['status'] for x in fragments))},
            'calibration_candidates': {'updates': updates, 'new_fragments': prepared['generated'],
                                       'receipts': prepared['mechanical']},
            'model_calls': 0, 'database_writes': 0, 'claim_qualification': 'not_granted',
            'future_release': ['visual_semantics', 'word_level_audio_alignment'],
            'timestamp_precision': 'paragraph_not_word'}


def run(store, source_id, root):
    root = Path(root).resolve(); root.mkdir(parents=True, exist_ok=True)
    with (root / 'source.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pin = f.bind_source(store, source_id, root)
        records, sources = f.load_source(store, source_id, visual_context=True)
        if pin != {'source_id': source_id, 'path': sources[source_id]['file']['path'],
                   'file_sha256': sources[source_id]['file']['file_sha256']}:
            raise ValueError('source changed between binding and census')
        e.save(root / 'intent.json', {'contract': CONTRACT, 'source_id': source_id,
               'source': sources[source_id]['file'], 'input_sha256': e.digest({'records': records, 'sources': sources}),
               'database_writes': False, 'model_calls': False})
        result = verify(records, sources, source_id)
        e.save(root / 'source-verification.json', result)
        return result
