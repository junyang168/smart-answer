"""Shared media operations, independent of the retired transcription orchestrator."""
from backend.pipeline.slide_model_response import parse_slide_answer
import json
import os
from pathlib import Path
from backend.pipeline import slide_change_pilot as slides
from backend.pipeline.slide_pilot_models import VisionReviewer
from backend.pipeline.media_model_calls import saved_call
from backend.pipeline.transcript_text_layout import text_sha
read, save, sha = slides.read_json, slides.save_json, slides.file_sha
CLEANUP_POLICY = 'conservative-editorial-cleanup.v4'

def classify_slides(out: Path) -> dict:
    folder = out / 'slides'
    manifest, candidates = read(folder / 'manifest.json'), read(folder / 'candidates.json')
    if manifest['identity']['source_sha256'] != read(out/'master.json')['source_sha256']:
        raise ValueError('Slide samples belong to a different source')
    adapter = VisionReviewer('gemini', slides.PAIR_PROMPT, slides.REVIEW_SCHEMA)
    adapter.config.pop('response_json_schema')
    adapter.config.update(max_output_tokens=16384, thinking_config={'thinking_level': 'LOW'})
    adapter.prompt += '\nJSON schema:\n' + json.dumps(slides.REVIEW_SCHEMA, ensure_ascii=False)
    rows, decisions, costs = candidates['candidates'], [], []
    for start in range(0, len(rows), 8):
        batch = rows[start:start+8]; ids = [r['id'] for r in batch]
        paths = [slides.pair_image(folder, manifest, row) for row in batch]
        request = {'stage': 'full_video_slide_classification', 'manifest_sha256': sha(folder / 'manifest.json'),
            'candidate_sha256': slides.object_sha(batch), 'prompt': adapter.prompt, 'model': adapter.model,
            'config': adapter.config, 'images': [{'path': str(p), 'sha256': sha(p)} for p in paths]}
        parts = adapter.contents(ids, paths)
        raw = saved_call(folder, request, .08, lambda: adapter.generate(parts), max_calls=160, budget_usd=13)
        cost = adapter.usage(raw)['estimated_usd_upper']
        try:
            validated = slides.validate_results(parse_slide_answer(adapter.text(raw), slides.REVIEW_SCHEMA, id_field="candidate_id", expected_ids=ids), ids)
        except (ValueError, KeyError) as error:
            from google.genai import types
            repair = {**request, 'stage': 'slide_json_repair_once',
                'parent_response_sha256': slides.object_sha(raw), 'validation_error': str(error)}
            repair_parts = parts + [types.Part.from_text(text='Repair only the JSON structure and required IDs. '
                'Return the same decisions as valid JSON. Error: '+str(error)+' Previous answer: '+adapter.text(raw))]
            raw = saved_call(folder, repair, .08, lambda: adapter.generate(repair_parts), max_calls=160, budget_usd=13)
            validated = slides.validate_results(parse_slide_answer(adapter.text(raw), slides.REVIEW_SCHEMA, id_field="candidate_id", expected_ids=ids), ids)
            cost += adapter.usage(raw)['estimated_usd_upper']
        decisions += validated
        costs.append(cost)
        print(f'Slide classification {min(start+8,len(rows))}/{len(rows)}', flush=True)
    review = {'candidates_sha256': slides.object_sha(candidates), 'prompt_version': slides.PROMPT_VERSION,
              'results': decisions, 'estimated_usd_upper': sum(costs)}
    save(folder / 'full_review.json', review)
    events = slides.selected_events(candidates, review)
    events = slides.export_originals(folder, manifest, candidates, events)
    report = {'source_sha256': manifest['identity']['source_sha256'], 'events': events,
        'visibility': slides.visibility_timeline(manifest, candidates, review, events),
        'reference_benchmark': None, 'human_confirmed': False,
        'limitations': ['No full-video human reference; no precision/recall claim.'],
        'estimated_usd_upper': sum(costs)}
    save(folder / 'report.json', report)
    return report

def cleanup_generate(adapter, prompt):
    contents=[{'role':'user','content':prompt}]
    if os.getenv('MEDIA_BATCH_CONTEXT'):
        return adapter.generate(contents)  # batch_call holds the shared Sol lease.
    from backend.pipeline.media_batch.runtime import slot, atomic
    capacity=os.getenv('MEDIA_BATCH_CAPACITY_ROOT')
    if not capacity:raise ValueError('Standalone cleanup requires the shared MEDIA_BATCH_CAPACITY_ROOT')
    with slot(capacity,'sol',2) as journal:
        atomic(journal,{'state':'unknown','stage':CLEANUP_POLICY})
        try:raw=adapter.generate(contents)
        except Exception as error:
            if getattr(error,'status_code',None) in (400,401,402,403,404,422,429):
                atomic(journal,{'state':'rejected','stage':CLEANUP_POLICY})
            raise
        atomic(journal,{'state':'complete','stage':CLEANUP_POLICY,'response_sha256':slides.object_sha(raw)})
        return raw

def apply_exclusions(paragraphs: list[dict], answer: dict) -> tuple[list[dict], list[dict]]:
    if not isinstance(answer,dict) or set(answer) != {'deletions'} or not isinstance(answer['deletions'], list):
        raise ValueError('Invalid editorial exclusions')
    by_id = {p['id']: p for p in paragraphs}
    changes = []
    for row in answer['deletions']:
        if (set(row) != {'paragraph_id','old','reason','kind'} or row['paragraph_id'] not in by_id
            or row['kind'] not in ('technical_setup','logistics','speech_restart','unrelated_aside') or not isinstance(row['reason'],str) or not row['reason'].strip()):
            raise ValueError('Invented exclusion source or category')
        text = by_id[row['paragraph_id']]['text']
        old = row['old']
        if not isinstance(old,str) or not old.strip() or text.count(old) != 1:
            raise ValueError('Exclusion must identify unique exact source text')
        if not any(c.isalnum() for c in old) or '~' in old:
            raise ValueError('Exclusion contains only punctuation or nested deletion markers')
        if row['kind']=='speech_restart' and (len(old)>80 or not text.replace(old,'',1).strip()):
            raise ValueError('Restart must retain the completed speech')
        if row['kind']=='speech_restart' and any(token in old for token in ('不','沒','没','未','無','无','非','勿','別','别')):
            raise ValueError('Restart deletion containing a negation requires review')
        start = text.index(old); end = start + len(old)
        if any(r['paragraph_id']==row['paragraph_id'] and r['start']<end and start<r['end'] for r in changes):
            raise ValueError('Overlapping editorial exclusions')
        changes.append({**row,'start':start,'end':end,'source_text_sha256':text_sha(text),
                        'derivation':'model_selected_non_sermon_exclusion','human_confirmed':False})
    result = []
    for p in paragraphs:
        rows = sorted((r for r in changes if r['paragraph_id']==p['id']),key=lambda r:r['start'])
        parts=[];cursor=0
        for row in rows:
            parts.append(p['text'][cursor:row['start']]);cursor=row['end']
        parts.append(p['text'][cursor:]);text=''.join(parts)
        if text.strip():result.append({**p,'text':text})
    return result,changes
