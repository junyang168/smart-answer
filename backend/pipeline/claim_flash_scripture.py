"""Attach an already identified citation through real source spans; no GPT review.

Locating a quote never establishes its book/verse. Callers supply the accepted
reference and target keys; unresolved reference identities remain unresolved.
"""
from copy import deepcopy
import unicodedata
import fcntl
import json
from pathlib import Path
import re

from backend.api.scripture import spoken_references, format_chinese_reference, chinese_number
from backend.api.sermon_search.bible_refs import normalize_ref
from backend.pipeline import claim_flash_common as f, claim_flash_source as source
from backend.pipeline import claim_flash_source_fill as fill, evidence_refresh as e
from backend.pipeline.claim_flash_source_lookup import Locator
from backend.pipeline.corpus_scripture_enrichment import _display_for_ref
from backend.pipeline.transcript_evidence_migration import script_layout

VERSION='claim_flash_scripture_attachment_v1'


def references(text):
    """Reuse book aliases and spoken numerals; keep unknown scope, never guess it."""
    text=unicodedata.normalize('NFKC',text)
    if re.search('未明|不明|未知|未說|未说',text):return []
    result=[];previous=None
    for part in re.split('[；;、,，/]',text):
        part=part.strip();chapter_only=bool(re.search(r'章$',part))
        nums=r'[0-9〇零一二兩两三四五六七八九十百]+'
        def chapter(m):
            ch=chinese_number(m[1]);verse=chinese_number(m[2]) if m[2] else None
            return str(ch)+(':'+str(verse) if verse is not None else '')
        part=re.sub(r'('+nums+r')[至–—~-]('+nums+r')章',lambda m:str(chinese_number(m[1]))+'-'+str(chinese_number(m[2])),part)
        part=re.sub(r'第?('+nums+r')章(?:第?('+nums+r'))?',chapter,part)
        part=re.sub(r'第?('+nums+r')[節节]',lambda m:str(chinese_number(m[1])),part)
        part=re.sub(r'(?<=[至–—~:：-])第?('+nums+r')',lambda m:str(chinese_number(m[1])),part)
        if previous and (re.match(r'^\d+[:：]',part) or re.fullmatch(r'\d+(?:[至–—~-]\d+)?',part)):
            part=previous.book_zh+' '+(str(previous.chapter_start)+':'+part if ':' not in part and '：' not in part and previous.verse_start is not None and not chapter_only else part)
        ref=normalize_ref(part)
        if ref is not None and re.sub(r'\s','',ref.raw).casefold()!=re.sub(r'\s','',part).casefold():
            tail=part[len(ref.raw):].strip()
            if ref.verse_start is None and re.fullmatch(r'[至–—~-]\d+',tail):
                end=int(tail[1:])
                if end<ref.chapter_start:return []
                ref=ref.model_copy(update={'raw':part,'chapter_end':end,'osis':f'{ref.book}.{ref.chapter_start}-{ref.book}.{end}'})
            else:return []  # Never silently drop an unparsed range or second reference.
        if ref is None:
            spoken=spoken_references(part)
            if len(spoken)!=1:return []
            ref=normalize_ref(format_chinese_reference(spoken[0][1]))
        if ref is None:return []
        result.append(ref);previous=ref
    return list({ref.osis:ref for ref in result}.values())


def normalized(text):return script_layout(text)[0]


def select(root,ports,quote,candidates):
    """Short sparse Jev lookup, never one yes/no call per fragment or a GPT call."""
    if not candidates:return []
    blocks=[];block={}
    for key,value in candidates.items():
        trial={**block,key:value}
        if block and (len(trial)>253 or len(json.dumps(trial,ensure_ascii=False).encode())>f.JEV_BYTES//2):
            blocks.append(block);block={};trial={key:value}
        if len(json.dumps({'quote':quote,'candidates':trial},ensure_ascii=False).encode())>f.JEV_BYTES//2:
            raise ValueError('complete citation lookup exceeds Jev limit; no truncation')
        block=trial
    if block:blocks.append(block)
    for block in blocks:
        options={key:'此片段承載這段經文引用，允許不同譯法、半句或口頭轉述；僅同主題不算。' for key in block}
        options.update(none='沒有對應片段。',uncertain='材料不足，無法確定。')
        wire={'model':e.MODEL,'state':{'citation_quote':quote,'candidates':block},'questions':{'match':{'type':'choice','instructions':'哪個片段承載 citation_quote？只定位引用，不審主張或推理。','criteria':options}}}
        if len(json.dumps(wire,ensure_ascii=False).encode())>f.JEV_BYTES:raise ValueError('complete citation lookup exceeds Jev limit; no call made')
    found=[]
    for block in blocks:
        remaining=dict(block)
        while remaining:
            options={key:'此片段承載這段經文引用，允許不同譯法、半句或口頭轉述；僅同主題不算。' for key in remaining}
            options.update(none='沒有對應片段。',uncertain='材料不足，無法確定。')
            answer=f.jev(root,ports,{'citation_quote':quote,'candidates':dict(remaining)},
                {'match':{'type':'choice','instructions':'哪個片段承載 citation_quote？只定位引用，不審主張或推理。', 'criteria':options}})['match']
            if answer=='uncertain':return []  # Do not turn partial retrieval into a complete match.
            if answer=='none':break
            if answer not in remaining:raise ValueError('citation lookup invented a fragment')
            found.append(remaining.pop(answer))
    return found


def prepare(records,sources,sid,jobs,root,ports):
    root=Path(root);valid={};updates={};generated={};outcomes=[];pending=[];working=deepcopy(records)
    if len(jobs)!=len({j['job_id'] for j in jobs}):raise ValueError('duplicate citation job id')
    locator=Locator(sources);paragraphs=sources[sid]['paragraphs']
    for key,row in records.items():
        if not key.startswith('source_fragments/') or row.get('retired') or row['payload']['source_id']!=sid:continue
        try:e.material({'evidence_step_id':key,'statement':'','source_fragment_ids':[row['payload']['fragment_id']]},records,sources)
        except (KeyError,ValueError):continue
        valid[key]=row['payload']
    # Validate every target before any model call; no cross-sermon or protected writes.
    for job in jobs:
        if not job.get('target_keys') or not job.get('quote','').strip():raise ValueError('citation needs a quote and explicit lower targets')
        for key in job['target_keys']:
            if key.split('/')[0] not in {'evidence_steps','observations'} or key not in records:raise ValueError('invalid citation target')
            if records[key]['payload'].get('review_status') in f.PROTECTED:raise ValueError('protected citation target')
            value=e.material({**records[key]['payload'],'evidence_step_id':key},records,sources)
            if any(records['source_fragments/'+x['fragment_id']]['payload']['source_id']!=sid for x in value['fragments']):raise ValueError('foreign citation target')
    normalized_fragments={k:normalized(p['verbatim_excerpt']) for k,p in valid.items()}
    for job in jobs:
        refs=references(job['reference']);token=e.digest(job);pk=job.get('paragraph_key')
        if not refs:
            pending.append({'job_id':job['job_id'],'reason':'unidentified_scripture_reference'});continue
        local={k:p for k,p in valid.items() if not pk or p['paragraph_key']==pk}
        matched=[];basis='program_fragment'
        if pk in paragraphs:
            matched=fill.covered_by({'paragraph_key':pk,'quote':job['quote']},paragraphs[pk],local)
        if not matched:
            needle=normalized(job['quote'])
            matched=[k for k,p in local.items() if needle and needle in normalized_fragments[k]]
        if not matched:
            selected=select(root/token/'fragments',ports,job['quote'],
                {'F'+str(n):{'key':k,'quote':p['verbatim_excerpt'],'paragraph_key':p['paragraph_key']} for n,(k,p) in enumerate(local.items())})
            matched=[x['key'] for x in selected];basis='jev_fragment'
        if not matched:
            found=locator.unique(job['quote']);basis='program_source'
            if found and pk and found['paragraph_key']!=pk:found=None
            spans=[found] if found else []
            if not spans:
                # Program supplies literal sentence spans. Jev selects locations, not rewritten quotes.
                candidates={}
                for paragraph_key,p in paragraphs.items():
                    if pk and paragraph_key!=pk:continue
                    for m in re.finditer(r'[^。！？!?]+[。！？!?]*',p['text']):
                        candidates['S'+str(len(candidates))]={'paragraph_key':paragraph_key,'quote':m.group()}
                spans=select(root/token/'source',ports,job['quote'],candidates);basis='jev_source'
            if not spans:
                pending.append({'job_id':job['job_id'],'reason':'citation_not_located_in_current_source'});continue
            quotes=[{'paragraph_key':x['paragraph_key'],'quote':x['quote']} for x in spans]
            material={'nearby_paragraphs':[{'source_id':sid,'paragraph_key':p,**paragraphs[p]} for p in dict.fromkeys(x['paragraph_key'] for x in quotes)]}
            post,fragments,_=source.binding_postimage('citation-only',{'statement':job['quote']},quotes,material,working,sources)
            generated.update(fragments);working=f.apply_overlay(working,{},fragments)
            valid.update({k:v for k,v in fragments.items()});normalized_fragments.update({k:normalized(v['verbatim_excerpt']) for k,v in fragments.items()});matched=['source_fragments/'+fid for fid in post['source_fragment_ids']]
        # Prefer already attached matches; otherwise reuse a shortest complete literal match.
        for key in job['target_keys']:
            payload=deepcopy(updates.get(key,records[key]['payload']));owned=set(e.evidence_fragment_ids(payload))
            chosen=[k for k in matched if valid[k]['fragment_id'] in owned] or matched
            complete=[k for k in chosen if normalized(job['quote']) in normalized_fragments[k]]
            if complete:chosen=[min(complete,key=lambda k:(len(valid[k]['verbatim_excerpt']),k))]
            extra=[valid[k]['fragment_id'] for k in chosen if valid[k]['fragment_id'] not in owned]
            if extra:
                payload['source_fragment_ids']=list(dict.fromkeys(e.evidence_fragment_ids(payload)+extra))
                payload['source_fragment_id']=None
            existing={r.osis for raw in payload.get('scripture_refs',[]) for r in references(raw)}
            payload['scripture_refs']=list(payload.get('scripture_refs',[]))+[_display_for_ref(ref) for ref in refs if ref.osis not in existing]
            if payload!=records[key]['payload']:updates[key]=payload
        outcomes.append({'job_id':job['job_id'],'basis':basis,'reference_osis':[r.osis for r in refs],
                         'fragment_keys':matched,'target_keys':job['target_keys']})
    f.source_unchanged(sources)
    return {'version':VERSION,'status':'needs_attention' if pending else 'verified','source_id':sid,
        'updates':updates,'new_fragments':generated,'pending':pending,'outcomes':outcomes,'applied':False,
        'claim_qualification':'not_granted','commit_reason':'Attach confirmed scripture references through current literal source fragments; no statement or Claim edits'}


def run(store,sid,jobs,root,ports,*,apply=False):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    with (root/'run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        pin=f.bind_source(store,sid,root)
        e.save(root/'intent.json',{'version':VERSION,'source':pin,'jobs':jobs,'models':ports.identity})
        if (root/'verification.json').exists():
            return e.commit_verified(store,root) if apply else json.loads((root/'verification.json').read_text())
        records,sources=f.load_source(store,sid)
        result=prepare(records,sources,sid,jobs,root,ports)
        reads={k for k in records if k.startswith('source_fragments/')}|{'source_documents/'+sid}|{k for job in jobs for k in job['target_keys']}
        e.save(root/'before.json',{'records':{k:records[k] for k in reads},'sources':sources});e.save(root/'verification.json',result)
        return e.commit_verified(store,root) if apply else e.publish(root,result)


def coverage_jobs(findings):
    """Only a confirmed missing reference with explicit existing owners may be written."""
    jobs=[]
    for finding in findings:
        if finding['verdict']!='missing_scripture_reference' or not references(finding['reference']):continue
        targets=[k for k in finding.get('existing_record_keys',[]) if k.split('/')[0] in {'evidence_steps','observations'}]
        if targets:jobs.append({'job_id':e.digest(finding),'reference':finding['reference'],'quote':finding['quote'],
            'paragraph_key':finding['paragraph_key'],'target_keys':sorted(set(targets))})
    return jobs
