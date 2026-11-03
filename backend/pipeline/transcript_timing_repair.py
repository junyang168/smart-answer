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
        if (evidence.get('schema_version') not in ('paragraph-timing-correction.v1','paragraph-timing-corrections.v1')
                or evidence.get('source_video_sha256')!=context['source_video_sha256']
                or file_hash(folder/'source.mp4')!=context['source_video_sha256']
                or evidence.get('source_record_sha256')!=context['source_record_sha256']
                or file_hash(folder/'source-record.json')!=context['source_record_sha256']):
            raise ReviewError(412,'原始音訊或來源版本不一致')
        entries=evidence.get('entries') if evidence.get('schema_version')=='paragraph-timing-corrections.v1' else [evidence]
        if not isinstance(entries,list) or not entries:
            raise ReviewError(400,'沒有時間修正項目')
        ids=[entry.get('paragraph_id') for entry in entries]
        if len(set(ids))!=len(ids) or request.get('operations')!=[{'kind':'paragraph_timing','id':key} for key in ids]:
            raise ReviewError(400,'時間修正項目重複或範圍不一致')
        updated=copy.deepcopy(context)
        duration=read(folder/'source-record.json')['duration_ms']
        for entry in entries:
            key=entry.get('paragraph_id')
            row=next((p for p in rows if p['index']==key),None)
            binding=context['paragraph_bindings'].get(key)
            if (row is None or not binding or binding!=entry.get('before_binding')
                    or hashlib.sha256(row['text'].encode()).hexdigest()!=entry.get('current_text_sha256')):
                raise ReviewError(412,'段落文字或位置已改變')
            after=entry.get('after_binding',{})
            start,end=after.get('start_ms'),after.get('end_ms')
            if (type(start) is not int or type(end) is not int or not 0<=start<end<=duration
                    or after!={**binding,'start_ms':start,'end_ms':end} or after==binding):
                raise ReviewError(400,'只能修正同一來源段落的有效時間範圍')
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
        quality=evidence.get('quality_audit')
        if quality is not None:
            states=quality.get('paragraph_states',{})
            if set(states)!=set(updated['paragraph_bindings']):
                raise ReviewError(400,'時間品質檢查必須涵蓋全部來源段落')
            for key,state in states.items():
                if state.get('binding_sha256')!=digest(updated['paragraph_bindings'][key]) or state.get('status') not in ('qualified','withheld'):
                    raise ReviewError(412,'時間品質檢查與實際段落位置不同')
            expected='qualified' if all(s['status']=='qualified' for s in states.values()) else 'withheld'
            if quality.get('status')!=expected:
                raise ReviewError(400,'時間品質總狀態與段落檢查不一致')
            updated['timing_quality']={**quality,'evidence_sha256':request['evidence_sha256']}
        return store.commit(item,actor,request['operation_id'],request,rows,updated,'timing_repair')
