"""Privileged, guarded timing-only maintenance using the existing review journal.

No public route. Text and source identities cannot change. An existing, current
text-review decision is retained with explicit timing-only hash-rebind provenance;
its original actor/time/decision remain unchanged, and no confirmation is created.
"""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path

from backend.api.sc_api.transcript_review import ReviewError, digest, file_hash, read


def apply_timing_repair(store, actor: str, item: str, request: dict, evidence_path: Path):
    with store.lock(item):
        store.require(actor,item)
        prior=store.receipt(item,request.get('operation_id'),actor,request)
        if prior:return prior
        store.require(actor,item,write=True,epoch=request.get('epoch'))
        rows,context=store.load(item)
        store.version(rows,context,request.get('version'))
        if file_hash(evidence_path)!=request.get('evidence_sha256'):
            raise ReviewError(412,'時間修正證據已改變')
        evidence=read(evidence_path)
        folder=store.folder(item)
        if (evidence.get('schema_version')!='paragraph-timing-correction.v1'
                or evidence.get('source_video_sha256')!=context['source_video_sha256']
                or file_hash(folder/'source.mp4')!=context['source_video_sha256']
                or evidence.get('source_record_sha256')!=context['source_record_sha256']
                or file_hash(folder/'source-record.json')!=context['source_record_sha256']):
            raise ReviewError(412,'原始音訊或來源版本不一致')
        operations=request.get('operations')
        key=evidence.get('paragraph_id')
        row=next((p for p in rows if p['index']==key),None)
        binding=context['paragraph_bindings'].get(key)
        if (row is None or not binding or operations!=[{'kind':'paragraph_timing','id':key}]
                or binding!=evidence.get('before_binding')
                or hashlib.sha256(row['text'].encode()).hexdigest()!=evidence.get('current_text_sha256')):
            raise ReviewError(412,'段落文字、位置或修正範圍已改變')
        after=evidence.get('after_binding',{})
        start,end=after.get('start_ms'),after.get('end_ms')
        duration=read(folder/'source-record.json')['duration_ms']
        if (type(start) is not int or type(end) is not int or not 0<=start<end<=duration
                or after!={**binding,'start_ms':start,'end_ms':end} or after==binding):
            raise ReviewError(400,'只能修正同一來源段落的有效時間範圍')
        updated=copy.deepcopy(context)
        old_sha=digest({'text':row['text'],**binding})
        new_sha=digest({'text':row['text'],**after})
        review=updated['reviews'].get('p:'+key)
        rebound=bool(review and review.get('sha256')==old_sha)
        if rebound:review['sha256']=new_sha
        updated['paragraph_bindings'][key]=after
        updated.setdefault('timing_repairs',[]).append({
            'paragraph_id':key,'before_binding':binding,'after_binding':after,
            'before_content_sha256':old_sha,'after_content_sha256':new_sha,
            'evidence_sha256':request['evidence_sha256'],'operation_id':request['operation_id'],
            'actor':actor,'existing_review_hash_rebound':rebound,
            'review_decision_unchanged':True,'new_human_confirmation':False})
        return store.commit(item,actor,request['operation_id'],request,rows,updated,'timing_repair')
