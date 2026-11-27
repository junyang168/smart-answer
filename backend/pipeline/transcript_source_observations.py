"""Owner-reported recording defects, bound to exact audio and unconfirmed text.

These observations are not transcription approvals or playback qualifications.
"""
from pathlib import Path
from datetime import datetime,timezone
import hashlib
from backend.pipeline.slide_change_pilot import read_json,save_json,object_sha


def record_unintelligible(evidence:Path,paragraph_id:str,actor:str,note:str):
    plan=read_json(evidence/'plan.json')
    matches=[(r,p) for r in plan['records'].values() for p in r['paragraphs'] if p['id']==paragraph_id]
    if len(matches)!=1 or not actor.strip() or not note.strip():raise ValueError('Exact source paragraph and owner observation required')
    record,p=matches[0]
    value=dict(schema_version='transcript-source-observation.v1',kind='unintelligible_recording_noise',
        paragraph_id=paragraph_id,source_sha256=record['source_sha256'],text_sha256=hashlib.sha256(p['text'].encode()).hexdigest(),
        reported_by=actor,note=note,text_confirmed=False,raw_audio_preserved=True,unconfirmed_draft_preserved=True,
        inferred_replacement_allowed=False,automatic_model_retry_allowed=False)
    path=evidence/'source-observations'/(object_sha(value)+'.json')
    if not path.exists():save_json(path,{**value,'recorded_at':datetime.now(timezone.utc).isoformat()})
    return path


def load_observations(evidence:Path):
    plan=read_json(evidence/'plan.json')
    sources={p['id']:(r['source_sha256'],hashlib.sha256(p['text'].encode()).hexdigest()) for r in plan['records'].values() for p in r['paragraphs']}
    result={}
    for path in sorted((evidence/'source-observations').glob('*.json')):
        value=read_json(path);pid=value['paragraph_id']
        if (value.get('schema_version')!='transcript-source-observation.v1' or value.get('kind')!='unintelligible_recording_noise'
            or sources.get(pid)!=(value['source_sha256'],value['text_sha256']) or value.get('text_confirmed') is not False
            or value.get('inferred_replacement_allowed') is not False):raise ValueError('Source observation does not match this audio/text')
        if pid not in result or value['recorded_at']>result[pid]['recorded_at']:result[pid]=value
    return result
