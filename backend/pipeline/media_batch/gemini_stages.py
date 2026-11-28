"""Gemini stage adapter for the existing persistent batch worker."""
from argparse import Namespace
from pathlib import Path
import math

from .runtime import atomic, file_sha, ensure_storage, progress
from .store import key
from backend.pipeline.slide_change_pilot import read_json
from backend.api.sc_api.transcript_review import ReviewStore, digest
from backend.pipeline.gemini_review import initialize
from .gemini_plan import PIPELINE, implementation


def execute(stage,payload):
    cfg=payload['config'];ensure_storage(cfg)
    if cfg['implementation'] != implementation():
        from .engineering_recovery import verify
        verify(payload, implementation())
    from .stages import resolve_source
    source=resolve_source(payload);out=Path(payload['out']);out.mkdir(parents=True,exist_ok=True)
    review=ReviewStore(Path(cfg['review_root']));actor=cfg['actor'];item=payload['item']
    review.require(actor,item)
    if not review.permissions(actor).intersection({'write_owned_item','write_any_item'}):
        raise PermissionError('Pipeline actor no longer has write permission')
    _,meta=review.meta(item)
    if meta.get('assigned_to') and meta['assigned_to']!=actor:
        raise PermissionError('Ownership changed; preserving the current manuscript')
    from backend.pipeline import transcript_gemini_proofreading as flow
    if stage=='audio':
        from backend.pipeline.transcript_gemini_audio import extract
        manifest={'source_sha256':payload['info']['source_sha'],'windows':[]}
        duration=payload['info']['duration']
        for i,start in enumerate(range(0,math.ceil(duration),180)):
            end=min(start+180,duration)
            path=out/f'asr-{i}.mp3'
            extract(source,path,max(0,start-2),min(duration,end+2))
            manifest['windows'].append({'id':str(i),'start':start,'end':end,'audio_sha256':file_sha(path)})
            progress('audio',i+1,math.ceil(duration/180),'root_windows')
        old=out/'audio-prepared.json'
        if old.exists() and read_json(old)!=manifest:raise ValueError('Prepared audio drift')
        atomic(old,manifest)
        return {'root_windows':len(manifest['windows'])}
    if stage in flow.STAGES:
        if (out/'audio-prepared.json').exists():
            for w in read_json(out/'audio-prepared.json')['windows']:
                if file_sha(out/f'asr-{w["id"]}.mp3')!=w['audio_sha256']:raise ValueError('Prepared audio changed')
        flow.run(None,source,out,aligner=Path(cfg['aligner_python']),speaker_name=cfg.get('speaker_name'),through=stage)
        return {'artifact':str(out/f'{stage}.json'),'sha256':file_sha(out/f'{stage}.json')}
    if stage=='media':
        from .gemini_media import playback
        return playback(payload)
    if stage=='editor':
        result=initialize(review,item,actor,source,out,read_json(out/'media-receipt.json'),
                          payload['empty'],'gemini-'+key([item,payload['empty'],payload['info']['source_sha']])[:36])
        receipt={'version':result['version'],'source_sha256':payload['info']['source_sha'],
                 'editor_href':f'/admin/surmons/{__import__("urllib.parse",fromlist=["quote"]).quote(item,safe="")}',
                 'operation_id':result['operation_id']}
        atomic(out/'editor-receipt.json',receipt)
        return receipt
    if stage=='slides':
        if not payload['info']['video']:
            result={'not_applicable':True,'reason':'audio_only'}
            atomic(out/'slides-receipt.json',result);return result
        receipt=out/'slides-receipt.json'
        if receipt.exists():return read_json(receipt)
        expected=(read_json(out/'editor-receipt.json')['version'] if (out/'editor-receipt.json').exists()
                  else payload['expected_version'])
        slide_out=out/'editor-slides'
        # Once a runner snapshot exists its own CAS/idempotent import receipt governs recovery.
        if not (slide_out/'snapshot.json').exists():
            with review.lock(item):
                rows,ctx=review.load(item)
                review.version(rows,ctx,expected)
        from backend.pipeline.editor_slide_runner import run
        result=run(Namespace(root=review.root,video=source,sha256=payload['info']['source_sha'],
                             out=slide_out,item=item,replay=None,apply=True))
        report=read_json(slide_out/'report.json')
        held=[e for e in report['events'] if e['reason'] in ('missing placement','source highlight missing from SVG')
              or e['reason'].startswith(('fidelity:', 'layout:'))]
        # Preserve original classifications/reasons; do not turn a fidelity hold into success.
        result.update(events=len(report['events']),held=held,report_sha256=file_sha(slide_out/'report.json'))
        atomic(receipt,result)
        return result
    if stage=='delivery':
        receipts={}
        for want in payload['wanted']:
            name={'editor_draft':'editor','playback':'media','slides':'slides','rough':'transcribe'}[want]
            path=out/f'{name}-receipt.json' if want!='rough' else out/'transcribe.json'
            receipts[want]={'path':str(path),'sha256':file_sha(path)}
        if 'slides' in payload['wanted'] and read_json(out/'slides-receipt.json').get('held'):
            raise ValueError('Important slide candidates remain held; partial delivery')
        result={'pipeline':PIPELINE,'receipts':receipts,'source_sha256':file_sha(source),
                'editor_href':read_json(out/'editor-receipt.json').get('editor_href') if (out/'editor-receipt.json').exists() else None}
        atomic(out/'delivery-receipt.json',result);return result
    raise ValueError('Unknown Gemini stage')
