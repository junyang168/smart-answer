"""Explicit Gemini-to-Editor adapter. Never manufactures legacy timing certificates."""
import math
import os
from pathlib import Path
import shutil

from backend.api.sc_api.transcript_review import ReviewError, digest, file_hash, read, atomic
from backend.pipeline.slide_change_pilot import file_sha, object_sha

SCHEMA = 'gemini-editor-candidate.v1'


def register_only(store,item,actor,source,out,playback):
    """Versioned side candidate; never occupies the legacy adoption pointer."""
    record=candidate(source,out,playback)
    with store.lock(item):
        store.require(actor,item)
        _,meta=store.meta(item)
        if not store.permissions(actor).intersection({'write_owned_item','write_any_item'}):
            raise ReviewError(403,'沒有登記候選的權限')
        if meta.get('assigned_to') and meta['assigned_to']!=actor:
            raise ReviewError(403,'講道已由其他人認領')
        identity=digest(record)
        path=store.root/'gemini_transcript_candidates'/item/f'{identity}.json'
        if path.exists() and read(path)!=record:raise ReviewError(409,'候選版本不符')
        if not path.exists():atomic(path,record)
        return {'candidate_id':identity,'candidate_sha256':file_sha(path),
                'source_sha256':record['source_sha256'],'adopted':False}


def install_playback(store,item,actor,playback,expected,operation_id):
    """Supplement an Editor's playback mapping without changing its text."""
    request={'playback':playback,'expected_version':expected}
    with store.lock(item):
        store.require(actor,item)
        prior=store.receipt(item,operation_id,actor,request)
        if prior:return prior
        rows,context=store.load(item)
        _,meta=store.meta(item)
        store.require(actor,item,write=True,epoch=meta.get('assignment_epoch'))
        store.version(rows,context,expected)
        if context['source_video_sha256']!=playback['source_sha256']:
            raise ReviewError(409,'播放媒體與既有稿件來源不同')
        name='playback.mp4' if playback['mime']=='video/mp4' else 'playback.mp3'
        preserve_media(Path(playback['path']),store.folder(item)/name,playback['sha256'])
        context={**context,'playback_media':{'file':name,'sha256':playback['sha256'],
                 'source_sha256':playback['source_sha256'],'receipt_sha256':digest(playback)}}
        return store.commit(item,actor,operation_id,request,rows,context,'playback_media')


def empty_snapshot(store, item, actor):
    store.folder(item)
    _, meta = store.meta(item)
    perms = store.permissions(actor)
    if not perms.intersection({'write_owned_item','write_any_item'}):
        raise ReviewError(403, '沒有初始化講道的權限')
    if meta.get('assigned_to') and meta['assigned_to'] != actor:
        raise ReviewError(403, '講道已由其他人認領')
    if meta.get('status') == 'published':
        raise ReviewError(409, '已發布稿不能自動取代')
    files = {}
    for sub in ('script','script_patched','script_review','script_published'):
        p = store.root/sub/f'{item}.json'
        value = read(p)
        if p.exists() and value not in ([], {}, None):
            raise ReviewError(409, '已有稿件；只可另存候選或補件')
        if sub == 'script_published' and p.exists():
            raise ReviewError(409, '已有發布記錄')
        files[sub] = file_hash(p)
    folder=store.folder(item)
    if (folder/'context.json').exists() or any((folder/'revisions').glob('*.json')):
        raise ReviewError(409, '已有 Editor 歷史，不覆寫')
    if store.candidate_path(item).exists():
        raise ReviewError(409, '已有候選，不自動取代')
    return {'metadata_sha256':digest(meta), 'files':files,
            'candidate':file_hash(store.candidate_path(item)), 'epoch':meta.get('assignment_epoch')}


def candidate(source, out, playback):
    out=Path(out)
    run=read(out/'run.json')
    saved=read(out/'layout.json')
    if not run or not saved or saved['output_sha256'] != object_sha(saved['value']):
        raise ValueError('Invalid saved Gemini layout')
    source_sha=file_sha(source)
    if run['identity']['audio_sha256'] != source_sha:
        raise ValueError('Layout belongs to another source')
    duration=read(out/'transcribe.json')['value']['duration']
    rows=saved['value']['paragraphs']
    prior=-1
    for n,p in enumerate(rows,1):
        if str(p['index']) != str(n) or not isinstance(p['text'],str):
            raise ValueError('Invalid Gemini paragraph identity')
        if not all(isinstance(p.get(k),(int,float)) and math.isfinite(p[k]) for k in ('start','end')):
            raise ValueError('Missing paragraph timing')
        if not 0 <= p['start'] < p['end'] <= duration or p['start'] < prior:
            raise ValueError('Invalid paragraph interval')
        prior=p['end']
        if type(p.get('timing_review')) is not bool:
            raise ValueError('Missing timing review status')
        if not p['timing_review'] and p.get('start_timing_method') not in (
                'original_word','same_length_correction','exact_word_match','qwen_context','qwen_opening_context'):
            raise ValueError('Unproven paragraph start')
    if not rows: raise ValueError('Empty candidate')
    if playback['source_sha256'] != source_sha or file_sha(Path(playback['path'])) != playback['sha256']:
        raise ValueError('Playback/source binding changed')
    return {'schema_version':SCHEMA, 'source_sha256':source_sha,
            'duration_ms':round(duration*1000),'layout_sha256':file_sha(out/'layout.json'),
            'layout':saved['value'], 'playback':playback,
            'paragraphs':[dict(id=str(p['index']),text=p['text'],start_ms=round(p['start']*1000),
                                end_ms=round(p['end']*1000)) for p in rows]}


def preserve_media(source, dest, expected):
    if dest.exists():
        if dest.is_symlink() or file_sha(dest)!=expected:
            raise ReviewError(409,'既有媒體不同，不覆寫')
        return
    dest.parent.mkdir(parents=True,exist_ok=True)
    temporary=dest.with_name('.'+dest.name+'.partial')
    try:
        if temporary.exists():
            if file_sha(temporary)!=expected: raise ReviewError(409,'媒體暫存不同')
        else:
            try: os.link(source,temporary)
            except OSError: shutil.copyfile(source,temporary)
        if file_sha(temporary)!=expected: raise ReviewError(409,'保存媒體 SHA 不符')
        # A hard link is no-clobber even if another process created the destination.
        os.link(temporary,dest)
    finally:
        temporary.unlink(missing_ok=True)


def initialize(store,item,actor,source,out,playback,expected,operation_id):
    record=candidate(Path(source),Path(out),playback)
    request={'schema':SCHEMA,'candidate_sha256':digest(record),'expected':expected}
    with store.lock(item):
        store.require(actor,item)
        receipt=store.receipt(item,operation_id,actor,request)
        if receipt:return receipt
        if empty_snapshot(store,item,actor)!=expected:
            raise ReviewError(412,'稿件、來源或認領前像已改變')
        paragraphs=record['layout']['paragraphs']
        rows=[dict(index=str(p['index']),end_index=str(p['index']),type='content',text=p['text']) for p in paragraphs]
        bindings={str(p['index']):dict(source_paragraph_id=str(p['index']),
                    start_ms=round(p['start']*1000),end_ms=round(p['end']*1000)) for p in paragraphs}
        states={str(p['index']):{'status':'withheld' if p['timing_review'] else 'qualified',
                    'method':p.get('start_timing_method','provisional')} for p in paragraphs}
        holds={}
        for omission in record['layout'].get('omissions_for_review',[]):
            for p in paragraphs:
                if p['start'] < omission['end'] and omission['start'] < p['end']:
                    holds[str(p['index'])]={'reason':'possible_omission','evidence':omission}
        for interval in record['layout'].get('coverage',[]):
            if not interval.get('coverage_review'):continue
            for p in paragraphs:
                if p['start'] < interval['end'] and interval['start'] < p['end']:
                    holds[str(p['index'])]={'reason':'asr_coverage_unverified','evidence':interval}
        for key in holds:states[key]['status']='withheld'
        quality={'policy':'gemini-paragraph-start.v1',
                 'status':'qualified' if all(v['status']=='qualified' for v in states.values()) else 'editor_draft_only',
                 'paragraph_states':states,'source_holds':holds}
        folder=store.folder(item)
        preserve_media(Path(source).resolve(),folder/'source.mp4',record['source_sha256'])
        ext='.mp4' if playback['mime']=='video/mp4' else '.mp3'
        preserve_media(Path(playback['path']),folder/('playback'+ext),playback['sha256'])
        _,meta=store.meta(item)
        context={'schema_version':'transcript-review-context.v1','revision':0,'script_sha256':digest([]),
            'source_video_sha256':record['source_sha256'],'source_record_sha256':digest(record),
            'source_record_path':f'transcript_review/{item}/source-record.json',
            'media_mime_type':playback['mime'], 'source_media_mime_type': 'video/mp4' if ext=='.mp4' else 'audio/mpeg',
            'playback_media':{'file':'playback'+ext,'sha256':playback['sha256'],
                              'source_sha256':record['source_sha256'],'receipt_sha256':digest(playback)},
            'paragraph_bindings':bindings, 'timing_quality':quality,
            'reviews':{},'groups':[],'visual_assets':{},'occurrences':{},'visibility':{'spans':[]},
            'editorial_exclusions':[], 'source_notices':[],
            'speaker_attribution':{'pipeline_release':'gemini-transcript-v1','status':'candidate'},
            'editorial_metadata':{k:meta.get(k) or ([] if k=='core_bible_verse' else '')
                for k in ('title','summary','keypoints','core_bible_verse')},
            'source_metadata':{k:meta.get(k) for k in ('item','source','author','series_id','type')},
        }
        for p in paragraphs:
            if any(t.get('role')=='uncertain' for t in p.get('speaker_turns',[])):
                context['source_notices'].append({'kind':'uncertain_speaker','paragraph_ids':[str(p['index'])]})
        return store.commit(item,actor,operation_id,request,rows,context,'adopt',extra={
            folder/'source-record.json':record,folder/'source-layout.json':record['layout'],
            folder/'legacy-baseline.json':{'script':[],'patched':[],'timeline':None},
            store.root/'script_patched'/f'{item}.json':rows})
