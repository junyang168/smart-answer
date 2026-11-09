"""Keep uncertain overlap words verbatim; never turn a failed review into pass.

Validated audio-approved pairs may retain their decision. Every other pair keeps
BOTH source edges and carries a mandatory visible human-review notice. No calls.
"""
import json
from pathlib import Path
import numpy as np
from backend.pipeline import transcript_audio_seams as audio
from backend.pipeline.slide_change_pilot import read_json,save_json,file_sha,object_sha
from backend.pipeline.slide_pilot_models import VisionReviewer
from backend.pipeline.slide_visual_evidence import check_schema
from backend.pipeline.transcript_audio_pilot import read_audio

POLICY='retain-uncertain-asr-overlap.v1'


def derive(out):
    out=Path(out);root=out/'seam-context-recovery';frozen=read_json(root/'input.json')
    asr,binding,expected=audio.materialize(out)
    if frozen['policy']!=audio.POLICY or frozen['implementation_sha256']!=file_sha(Path(audio.__file__)) or frozen['binding']!=binding:raise ValueError('Retained seam sources changed')
    for rel,digest in frozen['parents'].items():
        path=(out/rel).resolve()
        if not path.is_relative_to(out.resolve()) or file_sha(path)!=digest:raise ValueError('Original failure changed')
    if len(frozen['pairs'])!=len(expected):raise ValueError('Seam scope changed')
    for p,ex in zip(frozen['pairs'],expected):
        if any(p[k]!=ex[k] for k in ('id','left','right','left_text','right_text','overlap_in_clip_ms','clip_samples')):raise ValueError('Seam material changed')
        path=Path(p['audio_path']).resolve()
        if not path.is_relative_to(root.resolve()) or file_sha(path)!=p['audio_sha256'] or not np.array_equal(read_audio(path),ex['samples']):raise ValueError('Seam audio changed')
        for key,side in [('alignment_path','right'),('left_alignment_path','left')]:
            if Path(p[key]).resolve()!=(out/f"alignment/{p[side]}.json").resolve():raise ValueError('Alignment escaped')
    calls={};proof_bindings={'seam-context-recovery/input.json':file_sha(root/'input.json')}
    for path in (root/'calls').glob('*/request.json'):
        request=read_json(path);state=read_json(path.with_name('state.json'));raw=read_json(path.with_name('response.json'))
        if state['status']!='complete' or state['response_sha256']!=file_sha(path.with_name('response.json')) or object_sha(request)!=path.parent.name:raise ValueError('Unresolved dispatch cannot become retained approval')
        stage=request['stage'].removeprefix(audio.POLICY+'.')
        if stage not in ('propose','review') or stage in calls:raise ValueError('Changed seam call scope')
        prompt,schema=(audio.PROMPT,audio.SCHEMA) if stage=='propose' else (audio.REVIEW,audio.REVIEW_SCHEMA)
        if request['model']!='gemini-3.8-flash' or request['prompt']!=prompt or request['config'].get('response_json_schema')!=schema or request['input_sha256']!=file_sha(root/'input.json'):raise ValueError('Seam call changed')
        parser=object.__new__(VisionReviewer);parser.provider='gemini';answer=json.loads(parser.text(raw));check_schema(answer,schema)
        calls[stage]=(request,answer)
        for p in (path,path.with_name('response.json'),path.with_name('state.json')):proof_bindings[str(p.relative_to(out))]=file_sha(p)
    if 'propose' not in calls or len(calls)>2:raise ValueError('Missing bounded seam proposal')
    material=[{k:v for k,v in p.items() if k in ('id','left_text','right_text','overlap_in_clip_ms')} for p in frozen['pairs']]
    proposal=calls['propose'][1]
    if calls['propose'][0]['material']!={'sources':material}:raise ValueError('Proposal material changed')
    if [r['id'] for r in proposal['pairs']]!=[p['id'] for p in frozen['pairs']]:raise ValueError('Proposal scope changed')
    review=calls.get('review')
    if review:
        wanted={'sources':material,'proposal':{'pairs':[{k:r[k] for k in ('id','decision','right_prefix')} for r in proposal['pairs']]}}
        if review[0]['material']!=wanted or [r['id'] for r in review[1]['pairs']]!=[p['id'] for p in frozen['pairs']]:raise ValueError('Review material changed')
    overrides={};notices=[];approved=[];by_id={c['id']:c for c in asr['chunks']}
    for i,p in enumerate(frozen['pairs']):
        try:
            if review is None:raise ValueError('No approval; original words retained')
            local,accepted=audio.decisions(asr,[p],{'pairs':[proposal['pairs'][i]]},{'pairs':[review[1]['pairs'][i]]})
        except ValueError as error:
            local={p['left']:{'last':len(read_json(Path(p['left_alignment_path']))['items'])},p['right']:{'first':0}}
            left,right=by_id[p['left']],by_id[p['right']]
            notices.append(dict(kind='unresolved_asr_overlap',left_chunk=p['left'],right_chunk=p['right'],
                start_ms=max(left['sample_start'],right['sample_start'])//16,
                end_ms=min(left['sample_end'],right['sample_end'])//16,
                status='retained_pending_human_review',reason=str(error),
                note='兩份粗稿在錄音接縫未能可靠去重；完整保留原字，請聽音核對可能重複或辨識不同的短句。'))
        else:approved.append(dict(left=p['left'],right=p['right'],status='audio_verified'))
        for cid,value in local.items():overrides.setdefault(cid,{}).update(value)
    if not notices:raise ValueError('No uncertainty needs conservative retention')
    return dict(policy=POLICY,source_binding=binding,evidence=proof_bindings,overrides=overrides,source_notices=notices,approved=approved,human_confirmed=False,new_model_calls=0)


def recover(out):
    from backend.pipeline.transcript_text_layout import stitch,SeamCoverageError
    out=Path(out)
    try:proof=derive(out)
    except (ValueError,OSError,KeyError) as error:raise SeamCoverageError('Unresolved seam cannot be safely retained: '+str(error)) from error
    path=out/'seam-retention-proof.json';save_json(path,proof)
    save_json(out/'seam-repair.json',dict(policy=POLICY,proof=path.name,sha256=file_sha(path)))
    return stitch(out)


def load(out,path):
    proof=read_json(path);expected=derive(out)
    if proof!=expected:raise ValueError('Conservative retention proof changed')
    return expected['overrides']
