#!/usr/bin/env python3
"""Apply insertion-only speaker labels through the authenticated review API.

Default is plan-only. Existing/manual/new reviews, edited text and changed bindings
are protected. The server independently enforces current actor, claim epoch and CAS.
Never writes source files or marks a paragraph confirmed.
"""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.pipeline.slide_change_pilot import read_json, save_json, file_sha
from backend.pipeline.transcript_speaker_batch import prepare_operations


def api(method, path, actor, body=None, version=None, base='http://127.0.0.1:8555'):
    secret=os.environ['TRANSCRIPT_REVIEW_ACTION_SECRET']
    data=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode() if body is not None else b''
    stamp=str(int(time.time()))
    message='\n'.join((method,path,actor,stamp,hashlib.sha256(data).hexdigest()))
    headers={'x-review-actor':actor,'x-review-time':stamp,
             'x-review-signature':hmac.new(secret.encode(),message.encode(),hashlib.sha256).hexdigest()}
    if body is not None: headers['Content-Type']='application/json'
    if version: headers['If-Match']='"'+version+'"'
    request=urllib.request.Request(base+path,data=data if body is not None else None,headers=headers,method=method)
    with urllib.request.urlopen(request,timeout=45) as response:
        value=json.load(response)
        access=json.loads(response.headers.get('X-Review-Access','{}'))
    return value,access


def main(args):
    root=args.artifacts
    initial=read_json(root/'initial-document.json')
    initial_access=read_json(root/'initial-access.json')
    batch=root/'full-audio'
    plan=read_json(batch/'plan.json')
    if file_sha(root/'initial-document.json')!=plan['snapshot_sha256']:
        raise ValueError('Initial snapshot changed')
    for name,sha in plan['source_hashes'].items():
        if file_sha(args.source/name)!=sha: raise ValueError('Original source changed')
    candidates={}
    for window in plan['windows']:
        path=batch/window['id']/'candidate.json'
        if path.exists():
            results=[read_json(path)]
        else:
            recovery=root/('recovery-'+window['id'])
            recovery_plan=read_json(recovery/'plan.json')
            if (recovery_plan['bounded_recovery']['parent_plan_sha256']!=file_sha(batch/'plan.json')
                    or recovery_plan['source_hashes']!=plan['source_hashes']):
                raise ValueError('Split recovery belongs to another parent/source')
            results=[read_json(recovery/w['id']/'candidate.json') for w in recovery_plan['windows']]
        for result in results:
            for candidate in result['accepted']:
                if candidate['paragraph_id'] in candidates: raise ValueError('Duplicate source paragraph')
                candidates[candidate['paragraph_id']]=candidate
    for path in (root/'focused-recovery').glob('*/candidate.json'):
        result=read_json(path)
        candidates[result['paragraph_id']]={'turns':result['turns'],'limitations':result['limitations']}
    source=read_json(args.source/'full_transcript.json')
    path='/sc_api/beta/review_document/'+urllib.parse.quote(args.item,safe='')
    current,access=api('GET',path,args.actor,base=args.base)
    if not access.get('can_edit') or access.get('epoch')!=initial_access['epoch'] or access.get('actor')!=args.actor:
        raise ValueError('Claim/access changed; do not write')
    result=prepare_operations(initial,current,source,candidates)
    save_json(root/'apply-plan.json',result)
    print(json.dumps({'eligible_label_operations':len(result['operations']),'skipped':len(result['skipped']),
                      'current_revision':current['context']['revision'],'apply':args.apply}))
    if args.apply and any(s['reason']=='no_valid_candidate' for s in result['skipped']):
        raise ValueError('Some eligible pending paragraphs lack validated candidates')
    if not args.apply or not result['operations']: return
    if (root/'apply-request.json').exists():
        raise ValueError('A saved operation already exists; reconcile its receipt before another write')
    request={'operation_id':args.operation_prefix+'_'+uuid.uuid4().hex,
             'version':current['version'],'epoch':access['epoch'],'operations':result['operations'],
             'assisted_edit':{'kind':'speaker_attribution_candidate','review_status':'unreviewed_model_candidate',
                              'source_hashes':plan['source_hashes'],'snapshot_sha256':plan['snapshot_sha256']}}
    save_json(root/'before-apply.json',current)
    save_json(root/'apply-request.json',request)
    try:
        receipt,_=api('PATCH',path,args.actor,request,current['version'],base=args.base)
    except Exception:
        receipt,_=api('GET',path+'/operations/'+request['operation_id'],args.actor,base=args.base)
        if not receipt: raise
    save_json(root/'apply-receipt.json',receipt)
    after,_=api('GET',path,args.actor,base=args.base)
    save_json(root/'after-apply.json',after)
    actual={str(p['index']):p for p in after['script']}
    changed={op['id']:op['text'] for op in result['operations']}
    for row in current['script']:
        expected=changed.get(str(row['index']),row['text'])
        if actual[str(row['index'])]['text']!=expected:
            raise ValueError('Saved text differs; inspect concurrent changes, never overwrite')
    if after['context']['reviews']!=current['context']['reviews']:
        raise ValueError('Review status differs; inspect current state without further writes')
    for field in ('paragraph_bindings','groups','visual_assets','editorial_metadata'):
        if after['context'][field]!=current['context'][field]:
            raise ValueError('Non-speaker context changed; inspect without further writes')
    verification={'written_paragraph_ids':list(changed),'protected_and_other_paragraphs_unchanged':True,
                  'review_records_unchanged':True,'timing_bindings_unchanged':True,
                  'before_revision':current['context']['revision'],'after_revision':after['context']['revision'],
                  'operation_id':request['operation_id'],'human_confirmations_added':0}
    save_json(root/'apply-verification.json',verification)
    print(json.dumps(verification))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--artifacts',type=Path,required=True)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--item',required=True)
    p.add_argument('--actor',required=True)
    p.add_argument('--base',default='http://127.0.0.1:8555')
    p.add_argument('--operation-prefix',default='speaker_model')
    p.add_argument('--apply',action='store_true')
    main(p.parse_args())
