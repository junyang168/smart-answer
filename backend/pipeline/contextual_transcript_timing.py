"""Source-bound contextual acoustic timing. No transcript text or production writes.

Two staggered neighbouring-paragraph contexts must agree on actual edge words.
Invalid or clip-edge anchors are withheld, never interpolated or clamped.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import unicodedata

from backend.pipeline.slide_change_pilot import file_sha,object_sha,read_json,save_json
from backend.pipeline.transcript_audio_pilot import read_audio,write_audio

POLICY='contextual-acoustic-boundaries.v2'


def paragraph_groups(rows,offset):
    owned=[]
    if offset:owned.append((0,1))
    owned += [(i,min(i+2,len(rows))) for i in range(offset,len(rows),2)]
    result=[]
    for a,b in owned:
        span=max(p['end_ms'] for p in rows[a:b])-min(p['start_ms'] for p in rows[a:b])
        result.extend([(i,i+1) for i in range(a,b)] if span>130000 else [(a,b)])
    return result


def prepare(sources:dict[str,Path],out:Path):
    out.mkdir(parents=True,exist_ok=True);chunks=[];records={}
    for lecture,source in sources.items():
        record=read_json(source/'full_transcript.json');master=read_json(source/'master.json');rows=record['paragraphs']
        records[lecture]={'source':str(source),'record_sha256':file_sha(source/'full_transcript.json'),
                          'audio_sha256':file_sha(source/'master.wav'),'source_sha256':master['source_sha256'],
                          'duration_ms':master['duration_ms'],'paragraphs':rows}
        samples=read_audio(source/'master.wav')
        for pass_id in [0,1]:
            for ordinal,(a,b) in enumerate(paragraph_groups(rows,pass_id)):
                left,right=max(0,a-1),min(len(rows),b+1);pad=1000*(pass_id+1)
                def bounds():
                    selected=rows[left:right]
                    lo=max(0,min(min(p['start_ms'],p.get('prior_start_ms') or p['start_ms']) for p in selected)-pad)
                    hi=min(master['duration_ms'],max(max(p['end_ms'],(p.get('alignment_clip_origin_sample',0)+p.get('alignment_clip_samples',0))//16) for p in selected)+pad)
                    return lo,hi
                lo,hi=bounds()
                while hi-lo>180000 and (left<a or right>b):
                    if right>b:right-=1
                    elif left<a:left+=1
                    lo,hi=bounds()
                if hi-lo>180000:raise ValueError('Owned paragraph exceeds bounded acoustic window')
                text='';ranges=[]
                for i in range(left,right):
                    if text:text+='\n'
                    start=len(text);text+=rows[i]['text'];ranges.append({'id':rows[i]['id'],'start':start,'end':len(text),'owned':a<=i<b})
                key=f'L{lecture}-P{pass_id}-{ordinal:04d}';path=out/'audio'/f'{key}.wav'
                if path.exists():
                    import numpy as np
                    if not np.array_equal(read_audio(path),samples[lo*16:hi*16]):raise ValueError('Saved contextual audio changed')
                else:write_audio(path,samples[lo*16:hi*16])
                chunks.append({'id':key,'lecture':lecture,'pass':pass_id,'text':text,'audio':str(path.relative_to(out)),
                               'audio_sha256':file_sha(path),'source_origin_ms':lo,'sample_count':(hi-lo)*16,'ranges':ranges})
    plan={'schema_version':POLICY,'records':records,'chunks':chunks}
    if (out/'plan.json').exists():
        existing=read_json(out/'plan.json')
        if existing!=plan:
            if existing.get('base_plan_object_sha256')!=object_sha(plan):raise ValueError('Contextual plan changed')
            plan=existing;chunks=existing['chunks']
    save_json(out/'plan.json',plan);save_json(out/'asr.json',{'chunks':chunks,'schema_version':POLICY})
    return {'chunks':len(chunks),'paragraphs':sum(len(v['paragraphs']) for v in records.values())}


def token_ranges(text,tokens):
    # Canonical accent decomposition is for coordinate mapping only; text is immutable.
    def flat(value):return ''.join(c for c in unicodedata.normalize('NFKD',value) if unicodedata.category(c)[0] in 'LN' or c=="'")
    positions=[];letters=''
    for i,char in enumerate(text):
        part=flat(char);letters+=part;positions.extend([i]*len(part))
    expected=''.join(flat(t) for t in tokens)
    if letters!=expected:raise ValueError('Acoustic tokenizer cannot be mapped exactly to source text')
    ranges=[];cursor=0
    for token in tokens:
        size=len(flat(token))
        if not size:raise ValueError('Empty acoustic text unit')
        ranges.append((positions[cursor],positions[cursor+size-1]+1));cursor+=size
    return ranges


def acoustic_activity(path):
    import numpy as np
    samples=read_audio(path).astype(np.float32)/32768
    size=320;complete=len(samples)//size*size
    rms=np.sqrt(np.mean(samples[:complete].reshape(-1,size)**2,axis=1))
    voice=float(np.quantile(rms,.9)) if len(rms) else 0
    threshold=min(max(.0015,float(np.quantile(rms,.15))*2),voice*.2) if voice else .0015
    def interval(a,b):
        values=rms[max(0,int(a//20)):min(len(rms),int(b//20)+1)]
        return bool(len(values) and float(np.mean(values<threshold))>=.65)
    def near(point):
        values=rms[max(0,int((point-160)//20)):min(len(rms),int((point+160)//20)+1)]
        return bool(len(values) and float(values.max())>threshold)
    return interval,near


def edge_anchor(items,duration,*,end=False,quiet_interval=None,voice_near=None):
    if not items:raise ValueError('No source units at paragraph boundary')
    item=items[-1] if end else items[0];start,stop=item['start_ms'],item['end_ms']
    # Equal timestamp classes are quantized acoustic points, not invented word durations.
    if not (0<=start<=stop<=duration):raise ValueError('Boundary unit has no valid acoustic coordinate')
    max_duration=1200 if len(item['text'])==1 else min(6000,500+len(item['text'])*300)
    if stop-start>max_duration:raise ValueError('Boundary unit spans implausibly long speech')
    if (duration-stop if end else start)<160:raise ValueError('Boundary unit is glued to audio clip edge')
    neighbours=items[-3:] if end else items[:3]
    if len(neighbours)>=2:
        gaps=[b['start_ms']-a['end_ms'] for a,b in zip(neighbours,neighbours[1:])]
        for a,b,gap in zip(neighbours,neighbours[1:],gaps):
            if gap>2000 and (quiet_interval is None or not quiet_interval(a['end_ms'],b['start_ms'])):
                raise ValueError('Boundary unit is detached from its source phrase')
    if len(neighbours)>=3:
        if not end and stop>neighbours[-1]['start_ms']+240:raise ValueError('Opening unit extends across later source units')
        if end and start<neighbours[0]['end_ms']-240:raise ValueError('Closing unit extends across earlier source units')
    point=stop if end else start
    if voice_near is not None and not voice_near(point):raise ValueError('Boundary coordinate is unsupported by nearby acoustic energy')
    return point


def verified_overlap(previous,current):
    left=previous.get('end_audio_witness',{});right=current.get('start_audio_witness',{})
    return bool(left.get('genuine_overlap') and right.get('genuine_overlap')
                and left.get('pair_id') and left.get('pair_id')==right.get('pair_id')
                and left.get('left_id')==previous['id'] and left.get('right_id')==current['id']
                and left.get('raw_response_sha256')==right.get('raw_response_sha256')
                and left.get('left_end_ms')==previous.get('end_ms')
                and right.get('right_start_ms')==current.get('start_ms'))


def analyse(out:Path,tolerance_ms=240,*,partial=False,write=True,detail=False,apply_coherence=True):
    plan=read_json(out/'plan.json');observations={};errors=[]
    for chunk in plan['chunks']:
        path=out/'alignment'/f'{chunk["id"]}.json'
        if not path.exists():
            if partial:continue
            raise ValueError('Contextual inference is incomplete')
        if file_sha(out/chunk['audio'])!=chunk['audio_sha256']:
            raise ValueError('Contextual acoustic clip changed')
        raw=read_json(path)
        quiet_interval,voice_near=acoustic_activity(out/chunk['audio'])
        if raw['identity']['text_sha256']!=hashlib.sha256(chunk['text'].encode()).hexdigest() or raw['identity']['audio_sha256']!=chunk['audio_sha256']:
            raise ValueError('Contextual alignment belongs to changed text/audio')
        items_raw=raw.get('monotonic_items',raw['items'])
        try:ranges=token_ranges(chunk['text'],[t['text'] for t in items_raw])
        except ValueError as error:
            ranges=None;mapping_error=str(error)
        for p in chunk['ranges']:
            if not p['owned']:continue
            key=(chunk['lecture'],p['id']);obs={'context':chunk['id'],'pass':chunk['pass'],'alignment_sha256':file_sha(path),
                 'input_fingerprint':object_sha({'audio':chunk['audio_sha256'],'text':raw['identity']['text_sha256']})}
            try:
                if raw.get('monotonic_diagnostics',{}).get('mean_logit_regret',0)>3:
                    raise ValueError('Acoustic monotonic path requires excessive posterior changes')
                if ranges is None:raise ValueError(mapping_error)
                items=[t for t,span in zip(items_raw,ranges) if p['start']<=span[0]<p['end']]
                obs.update(first_unit=items[0]['text'],last_unit=items[-1]['text'])
                for edge in ['start','end']:
                    try:
                        value=edge_anchor(items,chunk['sample_count']//16,end=edge=='end',quiet_interval=quiet_interval,voice_near=voice_near)
                        obs[edge+'_ms']=chunk['source_origin_ms']+value
                        unit=items[0] if edge=='start' else items[-1]
                        obs[edge+'_precision']='quantized_point' if unit['start_ms']==unit['end_ms'] else 'acoustic_unit'
                    except ValueError as error:obs[edge+'_error']=str(error)
                if 'start_ms' in obs and 'end_ms' in obs and obs['start_ms']>=obs['end_ms']:
                    obs['start_error']=obs['end_error']='Paragraph acoustic range is reversed'
            except ValueError as error:obs['error']=str(error)
            observations.setdefault(key,[]).append(obs)
    from backend.pipeline.boundary_audio_review import saved_verdicts
    witnesses=saved_verdicts(out)
    lectures={}
    for lecture,record in plan['records'].items():
        results=[]
        for p in record['paragraphs']:
            views=observations.get((lecture,p['id']),[]);row={'id':p['id'],'text_sha256':hashlib.sha256(p['text'].encode()).hexdigest(),'observations':views,'status':'withheld'}
            for edge in ['start','end']:
                good=[v for v in views if 'error' not in v and edge+'_error' not in v and edge+'_ms' in v]
                row[edge+'_status']='withheld'
                if len(good)>=2 and len({v['pass'] for v in good})>=2 and len({v['input_fingerprint'] for v in good})>=2:
                    delta=max(v[edge+'_ms'] for v in good)-min(v[edge+'_ms'] for v in good)
                    row[edge+'_disagreement_ms']=delta
                    if delta<=tolerance_ms:
                        row[edge+'_status']='qualified'
                        row[edge+'_ms']=(min if edge=='start' else max)(v[edge+'_ms'] for v in good)
            for edge in ['start','end']:
                witness=witnesses.get((lecture,p['id'],edge))
                if witness is not None:
                    row[edge+'_ms']=witness['time_ms'];row[edge+'_status']='qualified';row[edge+'_audio_witness']=witness
            if row['start_status']==row['end_status']=='qualified' and row['start_ms']<row['end_ms']:
                row['status']='qualified'
            results.append(row)
        from backend.pipeline.paired_boundary_review import saved_pairs
        positions={p['id']:p for p in results}
        for pair in saved_pairs(out):
            if pair['lecture']!=lecture:continue
            left,right=positions[pair['left_id']],positions[pair['right_id']]
            if ((left.get('start_ms') is not None and pair['left_end_ms']<=left['start_ms'])
                or (right.get('end_ms') is not None and pair['right_start_ms']>=right['end_ms'])):continue
            left.update(end_ms=pair['left_end_ms'],end_status='qualified',end_audio_witness={**pair,'genuine_overlap':pair['genuine_overlap']})
            right.update(start_ms=pair['right_start_ms'],start_status='qualified',start_audio_witness={**pair,'genuine_overlap':pair['genuine_overlap']})
            for row in [left,right]:
                if row['start_status']==row['end_status']=='qualified' and row['start_ms']<row['end_ms']:row['status']='qualified'
        if apply_coherence:
            from backend.pipeline.paragraph_audio_coherence import needs_coherence,saved_ranges
            whole=saved_ranges(out)
            for row in results:
                verified=whole.get((lecture,row['id']))
                if verified:
                    row.update(start_ms=verified['start_ms'],end_ms=verified['end_ms'],
                               start_status='qualified',end_status='qualified',status='qualified',
                               continuous_audio_witness=verified)
                    row.pop('issue',None)
                elif needs_coherence(row):
                    row['status']='withheld';row['issue']='discordant_boundary_occurrences_need_continuous_audio'
        # Large overlaps require additional acoustic work, not mechanical cutting.
        conflicts=set()
        for previous,current in zip(results,results[1:]):
            if previous['status']=='qualified' and current['status']=='qualified' and previous['end_ms']-current['start_ms']>320 and not verified_overlap(previous,current):
                conflicts.update([previous['id'],current['id']])
        for row in results:
            if row['id'] in conflicts:
                row['status']='withheld';row['issue']='unverified_cross_paragraph_overlap'
        value={'source_sha256':record['source_sha256'],'duration_ms':record['duration_ms'],'paragraphs':results,'qualified':sum(p['status']=='qualified' for p in results),'withheld':sum(p['status']!='qualified' for p in results)}
        lectures[lecture]=value
    result={'schema_version':POLICY,'plan_sha256':file_sha(out/'plan.json'),'lectures':lectures,'all_qualified':all(v['withheld']==0 for v in lectures.values())}
    if write:save_json(out/'assessment.json',result)
    return result if detail else {k:{name:v[name] for name in ['qualified','withheld']} for k,v in lectures.items()}




def add_recovery_contexts(out:Path):
    """At most one additional acoustic context per unresolved source paragraph."""
    plan=read_json(out/'plan.json')
    if plan.get('bounded_recovery'):return 0
    detail=analyse(out,detail=True)
    if detail['all_qualified']:return 0
    base_sha=object_sha(plan);extra=[]
    for lecture,record in plan['records'].items():
        rows=record['paragraphs'];samples=read_audio(Path(record['source'])/'master.wav')
        unresolved={p['id'] for p in detail['lectures'][lecture]['paragraphs'] if p['status']!='qualified'}
        for index,p in enumerate(rows):
            if p['id'] not in unresolved:continue
            left,right=max(0,index-1),min(len(rows),index+2)
            def bounds():
                selected=rows[left:right]
                return (max(0,min(min(x['start_ms'],x.get('prior_start_ms') or x['start_ms']) for x in selected)-3000),
                        min(record['duration_ms'],max(x['end_ms'] for x in selected)+3000))
            lo,hi=bounds()
            while hi-lo>180000 and (left<index or right>index+1):
                if right>index+1:right-=1
                else:left+=1
                lo,hi=bounds()
            text='';ranges=[]
            for i in range(left,right):
                if text:text+='\n'
                start=len(text);text+=rows[i]['text'];ranges.append({'id':rows[i]['id'],'start':start,'end':len(text),'owned':i==index})
            key=f'L{lecture}-P2-{index:04d}';path=out/'audio'/f'{key}.wav'
            write_audio(path,samples[lo*16:hi*16])
            extra.append({'id':key,'lecture':lecture,'pass':2,'text':text,'audio':str(path.relative_to(out)),
                          'audio_sha256':file_sha(path),'source_origin_ms':lo,'sample_count':(hi-lo)*16,'ranges':ranges})
    save_json(out/'original-plan.json',plan)
    plan={**plan,'chunks':plan['chunks']+extra,'base_plan_object_sha256':base_sha,
          'bounded_recovery':{'parent_plan_sha256':file_sha(out/'original-plan.json'),'max_additional_contexts':len(extra),
                              'reason':'one local source-bound context per unresolved boundary; all previous predictions retained'}}
    save_json(out/'plan.json',plan);save_json(out/'asr.json',{'chunks':plan['chunks'],'schema_version':POLICY})
    return len(extra)


def timing_fingerprint(record):
    return object_sha({'source_sha256':record['source_sha256'],'paragraphs':[
        {'id':p['id'],'text_sha256':hashlib.sha256(p['text'].encode()).hexdigest(),
         'start_ms':p['start_ms'],'end_ms':p['end_ms']} for p in record['paragraphs']]})


def playback_bindings(lecture):
    rows=lecture['paragraphs'];result={}
    for i,p in enumerate(rows):
        if p['status']!='qualified':continue
        start,end=p['start_ms'],p['end_ms']
        previous=rows[i-1] if i else None;following=rows[i+1] if i+1<len(rows) else None
        # Explicit playback guard time only in an acoustically supported gap.
        if start>=120 and (previous is None or (previous.get('status')=='qualified' and start-previous['end_ms']>=240)):
            start-=120
        if end+120<=lecture['duration_ms'] and (following is None or (following.get('status')=='qualified' and following['start_ms']-end>=240)):
            end+=120
        result[p['id']]={'start_ms':start,'end_ms':end}
    return result


def require_verified_timing(source:Path,record:dict):
    """Fail closed at handoff and fresh machine-candidate registration/adoption."""
    if not isinstance(record,dict):raise ValueError('Timing quality gate: staged source is incomplete')
    machine=(record.get('schema_version')=='full-illustrated-corrected-transcript.v1'
             or 'repeat_asr_verification_calls' in record)
    if not machine:return  # Existing human/legacy imports keep their contract.
    quality=record.get('timing_quality',{})
    if quality.get('policy')!=POLICY or quality.get('status')!='qualified':
        raise ValueError('Timing quality gate: contextual acoustic boundaries are unverified')
    root=source.resolve();path=(root/quality.get('certificate_path','')).resolve()
    if not path.is_relative_to(root) or not path.is_file() or file_sha(path)!=quality.get('certificate_sha256'):
        raise ValueError('Timing quality gate: missing or changed certificate')
    certificate=read_json(path)
    if certificate.get('policy')!=POLICY or certificate.get('fingerprint')!=timing_fingerprint(record):
        raise ValueError('Timing quality gate: certificate belongs to different text/times/source')
    evidence=(root/certificate['evidence_root']).resolve()
    if not evidence.is_relative_to(root) or file_sha(evidence/'plan.json')!=certificate['plan_sha256']:
        raise ValueError('Timing quality gate: changed acoustic evidence plan')
    # Recompute from saved real predictions; a boolean certificate cannot approve itself.
    detail=analyse(evidence,write=False,detail=True)
    lecture=detail['lectures'][certificate['lecture_key']]
    if not detail['all_qualified'] or lecture['source_sha256']!=record['source_sha256']:
        raise ValueError('Timing quality gate: unresolved acoustic boundaries')
    plan=read_json(evidence/'plan.json')
    for chunk in plan['chunks']:
        raw=read_json(evidence/'alignment'/f'{chunk["id"]}.json')
        if raw['identity']['config'].get('decode_profile')!='monotonic-logit-path.v1':
            raise ValueError('Timing quality gate: unqualified acoustic decoder profile')
    expected=playback_bindings(lecture)
    if len(expected)!=len(record['paragraphs']) or any(expected.get(p['id'])!={'start_ms':p['start_ms'],'end_ms':p['end_ms']} for p in record['paragraphs']):
        raise ValueError('Timing quality gate: saved timing differs from qualified acoustic evidence')
    text_shas={p['id']:p['text_sha256'] for p in lecture['paragraphs']}
    if any(text_shas.get(p['id'])!=hashlib.sha256(p['text'].encode()).hexdigest() for p in record['paragraphs']):
        raise ValueError('Timing quality gate: speech text changed')


def qualify_staged_record(out:Path,record:dict,aligner_python:Path|None):
    """Required producer step. Uncertain results stay staged and cannot hand off."""
    import shutil
    import subprocess
    from backend.pipeline.media_batch.runtime import inherited_fds
    lease_fds = inherited_fds()
    subprocess_options = {'pass_fds': lease_fds} if lease_fds else {}
    # Invalidate the old export before inference/network work can fail. The
    # immutable old bytes remain available for inspection, never as current.
    if (out/'handoff').exists():
        import uuid
        archive=out/'withheld-handoffs'/uuid.uuid4().hex
        archive.parent.mkdir(exist_ok=True)
        shutil.move(str(out/'handoff'),str(archive))
    signature=timing_fingerprint(record)[:20];base=out/'contextual-timing'/signature
    input_root=base/'input';evidence=base/'evidence';input_root.mkdir(parents=True,exist_ok=True)
    source_copy=input_root/'full_transcript.json'
    if source_copy.exists() and read_json(source_copy)!=record:
        raise ValueError('Contextual timing input revision changed')
    if not source_copy.exists():save_json(source_copy,record)
    for name in ['master.json','master.wav']:
        link=input_root/name
        if not link.exists():link.symlink_to((out/name).resolve())
    prepare({'source':input_root},evidence)
    plan=read_json(evidence/'plan.json')
    if any(not (evidence/'alignment'/f'{c["id"]}.json').exists() for c in plan['chunks']):
        if aligner_python is None:
            raise ValueError('Timing quality gate: finish requires --aligner-python for contextual verification')
        subprocess.run([str(aligner_python.absolute()),'-m','backend.pipeline.transcript_alignment_worker',
                        '--out',str(evidence.resolve()),'--device','mps','--decode-profile','monotonic'],check=True,**subprocess_options)
    detail=analyse(evidence,detail=True)
    if not detail['all_qualified'] and aligner_python is not None and add_recovery_contexts(evidence):
        subprocess.run([str(aligner_python.absolute()),'-m','backend.pipeline.transcript_alignment_worker',
                        '--out',str(evidence.resolve()),'--device','mps','--decode-profile','monotonic'],check=True,**subprocess_options)
        detail=analyse(evidence,detail=True)
    if not detail['all_qualified']:
        from backend.pipeline.boundary_audio_review import run as review_boundaries
        review_boundaries(evidence)
        detail=analyse(evidence,detail=True)
        if not detail['all_qualified']:
            review_boundaries(evidence,phase='extended')
            detail=analyse(evidence,detail=True)
        if not detail['all_qualified']:
            from backend.pipeline.paired_boundary_review import run as review_pairs
            review_pairs(evidence)
            detail=analyse(evidence,detail=True)
    if not detail['all_qualified']:
        from backend.pipeline.paragraph_audio_coherence import run as review_whole
        review_whole(evidence)
        detail=analyse(evidence,detail=True)
    if not detail['all_qualified']:
        record['timing_quality']={'policy':POLICY,'status':'withheld',
            'assessment_path':str((evidence/'assessment.json').relative_to(out))}
        save_json(out/'full_transcript.json',record)
        # Do not leave an older successful handoff looking current after a failed finish.
        if (out/'handoff').exists():
            archive=out/'withheld-handoffs'/signature;archive.parent.mkdir(exist_ok=True)
            if archive.exists():raise ValueError('Prior withheld handoff archive exists; preserve both for inspection')
            shutil.move(str(out/'handoff'),str(archive))
        raise ValueError('Timing quality gate: unresolved paragraph boundaries; handoff withheld')
    bindings=playback_bindings(detail['lectures']['source'])
    qualified={p['id']:p for p in detail['lectures']['source']['paragraphs']}
    for p in record['paragraphs']:
        p.update(bindings[p['id']])
        if 'alignment_sha256' in p:p['prior_alignment_sha256']=p.pop('alignment_sha256')
        p['timing_precision']='contextual_acoustic_phrase'
        p['contextual_alignment_sha256']=object_sha(qualified[p['id']]['observations'])
        p['timing_boundary_evidence_sha256']=object_sha(qualified[p['id']])
        p['word_timing_valid']=False
    certificate={'policy':POLICY,'fingerprint':timing_fingerprint(record),
                 'evidence_root':str(evidence.relative_to(out)),'plan_sha256':file_sha(evidence/'plan.json'),
                 'lecture_key':'source','word_timestamps_published':False}
    path=base/'certificate.json';save_json(path,certificate)
    record['timing_quality']={'policy':POLICY,'status':'qualified',
        'certificate_path':str(path.relative_to(out)),'certificate_sha256':file_sha(path)}
    return record


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('stage',choices=['prepare','analyse']);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--sources',type=Path)
    args=parser.parse_args()
    if args.stage=='prepare':print(prepare({k:Path(v) for k,v in read_json(args.sources).items()},args.out),flush=True)
    else:print(analyse(args.out),flush=True)

