#!/usr/bin/env python3
"""Privileged timing maintenance; default is plan-only, never rewrites text."""
import argparse
import json
from pathlib import Path
import sys
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.api.sc_api.transcript_review import ReviewStore,atomic,read,file_hash
from backend.pipeline.transcript_timing_repair import apply_timing_repair


def main(args):
    store=ReviewStore(args.data_root,permission_root=args.permission_root)
    snapshot=read(args.snapshot);access=read(args.access);evidence=read(args.evidence)
    if access.get('actor')!=args.actor or not access.get('can_edit'):
        raise ValueError('Original snapshot does not authorize this claimed editor')
    args.out.mkdir(parents=True,exist_ok=True)
    path=args.out/'timing-repair-request.json'
    if path.exists():request=read(path)
    else:
        request={'operation_id':'timing627_'+uuid.uuid4().hex,'version':snapshot['version'],
                 'epoch':access['epoch'],'evidence_sha256':file_hash(args.evidence),
                 'operations':[{'kind':'paragraph_timing','id':evidence['paragraph_id']}]}
        atomic(path,request)
    print(json.dumps({'paragraph':evidence['paragraph_id'],'before':evidence['before_binding'],
                      'after':evidence['after_binding'],'apply':args.apply}))
    if not args.apply:return
    result=apply_timing_repair(store,args.actor,args.item,request,args.evidence)
    atomic(args.out/'timing-repair-receipt.json',result)
    after=store.document(args.actor,args.item);atomic(args.out/'after-document.json',after)
    if after['script']!=snapshot['script']:raise ValueError('Transcript changed; inspect without more writes')
    key=evidence['paragraph_id']
    expected_bindings={**snapshot['context']['paragraph_bindings'],key:evidence['after_binding']}
    if after['context']['paragraph_bindings']!=expected_bindings:raise ValueError('Unexpected binding postimage')
    old_reviews=snapshot['context']['reviews'];new_reviews=after['context']['reviews']
    for review_key,old in old_reviews.items():
        new=new_reviews[review_key]
        if {k:v for k,v in old.items() if k!='sha256'}!={k:v for k,v in new.items() if k!='sha256'}:
            raise ValueError('A review decision/actor/time changed')
        if review_key!='p:'+key and new!=old:raise ValueError('Another review changed')
    if set(new_reviews)!=set(old_reviews):raise ValueError('Review records added or removed')
    atomic(args.out/'verification.json',{'text_and_all_rows_unchanged':True,'only_selected_binding_changed':True,
            'original_review_decisions_actors_times_preserved':True,'new_human_confirmations':0,
            'before_revision':snapshot['context']['revision'],'after_revision':after['context']['revision'],
            'target_review_hash_matches_current_content':new_reviews.get('p:'+key,{}).get('sha256')==after['paragraph_shas'][key]})
    print('Timing-only repair saved and verified')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['data-root','permission-root','snapshot','access','evidence','out']:
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--item',required=True);parser.add_argument('--actor',required=True)
    parser.add_argument('--apply',action='store_true');main(parser.parse_args())
