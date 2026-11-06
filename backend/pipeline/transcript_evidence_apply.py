"""Code-only, version-pinned relocation of existing transcript evidence.

No model calls, Claim prose edits, source registration or new qualifications.
Prepare freezes a fresh comparison; apply uses the common store and history.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
from copy import deepcopy
import csv
from html import escape
import io
import json
import re
from pathlib import Path

from backend.pipeline import claim_management as cm
from backend.pipeline import transcript_claim_migration as m
from backend.pipeline import transcript_evidence_migration as e
from backend.pipeline.transcript_evidence_calibration import audio_position
from backend.api.canonical_repository.knowledge_models import evidence_fragment_ids
from backend.api.canonical_repository.knowledge_models import SourceFragmentRecord

VERSION = 'wang_transcript_evidence_relocation_v1'
FRAGMENT_FIELDS = {
    'paragraph_key', 'source_segment_index', 'source_sha256',
    'paragraph_text_sha256', 'verbatim_excerpt', 'verbatim_excerpt_sha256',
    'media_time', 'media_end_time', 'anchor_state', 'speaker',
    'source_binding_status', 'deleted_in_source_sha256',
    'replacement_fragment_ids', 'missing_in_source_sha256',
}
EVIDENCE_FIELDS = {'source_binding_status', 'support_eligibility',
                   'valid_for_identity_review', 'speaker', 'source_fragment_id', 'source_fragment_ids',
                   'context_fragment_ids'}
REFERENCE_FIELDS = {'source_fragment_id', 'source_fragment_ids'}


def context_only(text):
    """A narrow literal list, never a heuristic that removes substantive words."""
    from backend.pipeline.transcript_evidence_calibration import content_quote
    text = content_quote(text)[0]
    text = re.sub(r'[\s。！？?!，、…⋯（）()：:「」『』]+','',text)
    return bool(re.fullmatch(r'(?:有沒有聽懂|聽懂沒有|有沒有懂|很重要|這很重要|這個很重要|對不對|對|沒有錯|什麼意思|是什麼意思|這是什麼意思|要怎麼樣|怎麼回事|小問題|小小問題|就是這樣)+',text))


def calibrate(row, state, rule, new, document):
    """Replay an exact, saved technical context decision, not content approval."""
    f = state['payload']
    if (f.get('source_sha256') == new['body_sha256'] and row['status'] == 'already_current'
            or f.get('deleted_in_source_sha256') == new['body_sha256']
            or f.get('missing_in_source_sha256') == new['body_sha256']):
        return row
    if (rule['before_pin'] != [state['revision'], state['content_sha256']]
            or rule['target_source_sha256'] != new['body_sha256'] or not rule.get('reason')):
        raise ValueError('saved locator calibration drift: '+row['record_key'])
    mode = rule['mode']
    if mode == 'source_text_missing':
        return {**row, 'status': 'source_text_missing', 'calibration_decision': rule,
                'reason': rule['reason'], 'proposed_fields': {
                    'anchor_state':'unresolved', 'source_binding_status':'source_text_missing',
                    'missing_in_source_sha256':new['body_sha256']}}
    segments = []
    for target in rule['segments']:
        p = new['paragraphs'][target['paragraph_key']]; quote = target['verbatim_excerpt']
        if mode == 'source_deleted':
            raw = next(str(r.get('text') or '') for r in document['script'] if str(r.get('index')) == str(p['index']))
            if not any(e.literal_layout(quote)[0] in e.literal_layout(match.group(1))[0]
                       for match in e.SOFT_DELETION.finditer(raw)):
                raise ValueError('calibrated deletion is not explicit strikethrough')
            continue
        if not quote or p['text'].count(quote) != 1 or '~~' in quote:
            raise ValueError('calibration is not a unique literal published slice')
        start = p['text'].index(quote)
        segments.append({**target, 'live_start':start, 'live_end':start+len(quote),
            'source_segment_index':p['index'], 'media_time':p.get('start_time'), 'media_end_time':p.get('end_time')})
    clock_segments = segments or [dict(media_time=new['paragraphs'][t['paragraph_key']].get('start_time'),
        media_end_time=new['paragraphs'][t['paragraph_key']].get('end_time')) for t in rule['segments']]
    if not audio_position(f, clock_segments)['consistent']:
        raise ValueError('calibrated location is outside the saved audio interval')
    if mode == 'source_deleted':
        return {**row, 'status':'source_deleted', 'reason':rule['reason'], 'calibration_decision':rule,
                'proposed_fields':{'anchor_state':'source_deleted','source_binding_status':'source_deleted'}}
    from backend.pipeline.transcript_evidence_alignment import speaker_annotation
    return {**row, 'status':'calibrated_rebind', 'reason':rule['reason'],
        'candidate_segments':segments, 'candidate_quote':'\n'.join(s['verbatim_excerpt'] for s in segments),
        'target_paragraphs':[s['paragraph_key'] for s in segments], 'calibration_decision':rule,
        'speaker_annotation':speaker_annotation(segments,new), 'content_approval_granted':False}


def segment_fields(segment, new):
    from backend.pipeline.transcript_evidence_alignment import speaker_annotation
    p = new['paragraphs'][segment['paragraph_key']]; quote = segment['verbatim_excerpt']
    annotation=speaker_annotation([segment],new) or {}
    return {k:segment[k] for k in ('paragraph_key','source_segment_index','verbatim_excerpt','media_time','media_end_time')} | {
        'source_sha256':new['body_sha256'], 'paragraph_text_sha256':m.sha(p['text'].encode()),
        'verbatim_excerpt_sha256':m.sha(quote.encode()), 'anchor_state':'source_version_bound',
        'source_binding_status':'needs_claim_refresh'} | annotation.get('proposed_fields',{})


def snap(path):
    bound = m.binding(Path(path))
    from backend.pipeline.transcript_publication_projection import spoken_document
    document, stripped = spoken_document(m.read(path))
    if not stripped:
        return cm.source.source_snapshot(bound)
    from backend.pipeline.source_projection import project_script
    projection = project_script(document.get('script') if isinstance(document, dict) else document)
    if projection.visual_blocks:
        raise ValueError('visual source changes require a separately scoped visual adapter')
    cm.source.verify_file(bound)
    return {'file': bound, 'body_sha256': projection.body_sha256,
            'spoken_text_sha256': projection.spoken_text_sha256,
            'editorial_structure_sha256': projection.editorial_structure_sha256,
            'paragraphs': {f'S{i:04d}': row for i, row in enumerate(projection.spoken_rows, 1)}}


def fields_for(row, fragment, new):
    """Precise changed words may relocate, never inherit semantic approval."""
    status = row['status']
    if status == 'source_deleted':
        return {**row['proposed_fields'], 'deleted_in_source_sha256': new['body_sha256']}, False
    if status == 'auto_rebind':
        return {**row['proposed_fields'], 'source_binding_status': 'source_version_bound'}, False
    if status == 'source_text_missing':
        return row['proposed_fields'], True
    if status == 'calibrated_rebind' and len(row['candidate_segments']) == 1:
        return segment_fields(row['candidate_segments'][0],new), True
    if status != 'located_review':
        return None, False
    a = row.get('alignment', {})
    segments = row.get('candidate_segments', [])
    if (len(segments) != 1 or not a.get('both_endpoints_matched')
            or not a.get('left_anchor') or not a.get('right_anchor')
            or a.get('matched_fraction', 0) < .65
            or not .5 <= a.get('length_ratio', 0) <= 1.8
            or 'quotation_boundary_requires_refresh' in row.get('change_flags', [])
            or not audio_position(fragment, segments)['consistent']):
        return None, False
    segment = segments[0]
    p = new['paragraphs'][segment['paragraph_key']]
    quote = segment['verbatim_excerpt']
    start, end = segment.get('live_start'), segment.get('live_end')
    if (not quote or start is None or end is None or p['text'][start:end] != quote
            or p['text'].count(quote) != 1 or '~~' in quote):
        return None, False
    fields = {k: segment[k] for k in ('paragraph_key', 'source_segment_index',
                                     'verbatim_excerpt', 'media_time', 'media_end_time')}
    fields.update(source_sha256=new['body_sha256'], anchor_state='source_version_bound',
                  paragraph_text_sha256=m.sha(p['text'].encode()),
                  verbatim_excerpt_sha256=m.sha(quote.encode()),
                  source_binding_status='needs_claim_refresh')
    annotation = row.get('speaker_annotation') or {}
    fields.update(annotation.get('proposed_fields', {}))
    return fields, True


def compare(records, *, source_id, old_sources, new, document, calibration=None):
    """Combine historical epochs; retain all latest records, including new IDs."""
    reports = {}; audits = {}
    for old in old_sources:
        subset = {k: v for k, v in records.items()
                  if not k.startswith('source_fragments/')
                  or v['payload'].get('source_sha256') == old['body_sha256']}
        result = e.preview(subset, source_id=source_id, old=old, new=new,
                           document=document, align_sequence=True, calibrate_context=True)
        from backend.pipeline.transcript_evidence_alignment import verify_preview
        # Independent literal checks consume saved slices and context anchors;
        # they do not repeat the alignment search or grant content approval.
        audits[old['body_sha256']] = verify_preview(result, old, new, document)
        for row in result['fragments']:
            reports[row['record_key']] = row
    updates = {}; changed_quotes = set(); rows = []; replacements = {}; creations = {}
    rules = (calibration or {}).get('fragments', {})
    for key, state in sorted(records.items()):
        f = state['payload']
        if not key.startswith('source_fragments/') or f.get('source_id') != source_id:
            continue
        row = reports.get(key, {'record_key': key, 'status': 'exception',
                               'reason': 'unknown_source_epoch', 'quote': f.get('verbatim_excerpt'),
                               'old_paragraph': f.get('paragraph_key'),
                               'old_media_time': f.get('media_time'), 'affected_claims': []})
        row = deepcopy(row)
        if key in rules and not state.get('retired'):
            row = calibrate(row,state,rules[key],new,document)
        if state.get('retired'):
            row.update(status='retired', reason='history_only')
        elif (f.get('anchor_state') == 'source_deleted'
              and f.get('deleted_in_source_sha256') == new['body_sha256']):
            row.update(status='already_deleted', reason='deletion_already_recorded')
        fields, changed = fields_for(row, f, new)
        if row['status'] == 'calibrated_rebind' and len(row['candidate_segments']) > 1:
            ids = []
            for segment in row['candidate_segments']:
                fid = 'TMR-'+cm.sha256_json([key,new['body_sha256'],segment])[:48]
                child = SourceFragmentRecord.model_validate({
                    'fragment_id':fid,'source_id':source_id, **segment_fields(segment,new),
                    'split_from_fragment_id':f['fragment_id'],
                }).model_dump(mode='json')
                if 'source_fragments/'+fid in records:
                    raise ValueError('split identity already exists; recapture its current bindings')
                creations['source_fragments/'+fid] = child; ids.append(fid)
            fields = {'anchor_state':'replaced_by_precise_fragments',
                      'source_binding_status':'needs_claim_refresh','replacement_fragment_ids':ids}
            replacements[key] = ids; changed = True
        if fields is not None and not state.get('retired'):
            post = {**f, **fields}
            # Never turn a stale deletion marker into apparently live support.
            if f.get('anchor_state') == 'source_deleted' and row['status'] != 'source_deleted':
                row.update(status='exception', reason='previous_deletion_requires_explicit_resolution')
            else:
                if post != f:
                    updates[key] = post
                if changed:
                    changed_quotes.add(key)
                    if row['status'] != 'source_text_missing':
                        row.update(status='relocate_changed_quote', content_approval_granted=False)
        if (not state.get('retired') and row['status'] not in {'source_deleted','already_deleted'}
                and context_only(f.get('verbatim_excerpt',''))):
            if key not in replacements:
                updates[key] = {**updates.get(key,f),'anchor_state':'context_only','source_binding_status':'context_only'}
            for fid in replacements.get(key,[]):
                child = creations['source_fragments/'+fid]
                child.update(anchor_state='context_only',source_binding_status='context_only')
            row.update(status='context_only',reason='rhetorical_or_emphasis_context_not_evidence')
            changed_quotes.add(key)
        row['applied_fields'] = ({k:v for k,v in updates[key].items() if f.get(k) != v}
                                 if key in updates else None)
        rows.append(row)
    by_key = {r['record_key']: r for r in rows}
    # Atomic fragments keep real paragraph clocks. All own support consumers
    # receive complete references; CVP/Route records retain their old, now
    # inactive source reference and must undergo their separate maintenance.
    reference_changes = {}
    for key,state in sorted(records.items()):
        if state.get('retired') or key.split('/',1)[0] not in {'evidence_steps','questions','observations','position_nodes'}:
            continue
        f = state['payload']; old_ids = evidence_fragment_ids(f); result_ids = []
        touched = False
        for fid in old_ids:
            fk = 'source_fragments/'+fid
            if fk not in replacements:
                result_ids.append(fid); continue
            touched = True; rule = rules[fk]
            indexes = rule.get('consumer_segments',{}).get(key,list(range(len(replacements[fk]))))
            if not indexes or any(i < 0 or i >= len(replacements[fk]) for i in indexes):
                raise ValueError('consumer split selection is invalid')
            result_ids.extend(replacements[fk][i] for i in indexes)
        if touched:
            post = {**f,'source_fragment_id':None,'source_fragment_ids':list(dict.fromkeys(result_ids))}
            reference_changes[key] = post; updates[key] = post
    evidence_rows = []
    for key, state in sorted(records.items()):
        if not key.startswith('evidence_steps/') or state.get('retired'):
            continue
        original = state['payload']; f = reference_changes.get(key,original); ids = evidence_fragment_ids(original)
        matching = [by_key['source_fragments/'+fid] for fid in ids
                    if 'source_fragments/'+fid in by_key]
        if not matching:
            continue
        deleted = [r['record_key'] for r in matching
                   if r['status'] in {'source_deleted', 'already_deleted'}]
        changed = [r['record_key'] for r in matching if r['record_key'] in changed_quotes]
        fields = {}
        context_ids = {fid for fid in evidence_fragment_ids(f)
                       if (creations.get('source_fragments/'+fid) or updates.get('source_fragments/'+fid)
                           or records.get('source_fragments/'+fid,{}).get('payload',{})).get('anchor_state') == 'context_only'}
        if context_ids:
            fields.update(source_fragment_id=None,
                source_fragment_ids=[fid for fid in evidence_fragment_ids(f) if fid not in context_ids],
                context_fragment_ids=sorted(set(f.get('context_fragment_ids',[]))|context_ids))
        if deleted:
            fields['source_binding_status'] = 'source_deleted' if len(deleted) == len(ids) else 'partially_deleted'
        elif changed:
            fields['source_binding_status'] = 'needs_claim_refresh'
        missing = [r['record_key'] for r in matching if r['status'] == 'source_text_missing']
        if missing: fields['source_binding_status'] = 'source_text_missing'
        if context_ids and not deleted and not missing:
            fields['source_binding_status'] = 'context_only' if not fields['source_fragment_ids'] else 'context_excluded'
        if deleted or changed or missing or context_ids:
            fields.update(support_eligibility='withheld_unreviewed', valid_for_identity_review=False)
        annotations = [r.get('speaker_annotation') for r in matching]
        if (len(matching) == len(ids) and annotations
                and all(a and a.get('speaker') for a in annotations)):
            roles = {a['speaker'] for a in annotations}
            fields['speaker'] = next(iter(roles)) if len(roles) == 1 else 'mixed'
        if fields:
            post = {**f, **fields}
            if post != f:
                updates[key] = post
            evidence_rows.append({'record_key': key, 'fields': fields,
                                  'deleted_fragment_keys': deleted, 'changed_fragment_keys': changed})
    updates.update(creations)
    return {'schema_version': VERSION, 'source_id': source_id,
            'summary': dict(Counter(r['status'] for r in rows)), 'fragments': rows,
            'evidence_updates': evidence_rows, 'updates': updates,
            'created_keys':sorted(creations), 'reference_changes':sorted(reference_changes),
            'literal_audits': audits,
            'model_calls': 0, 'claim_prose_changed': False, 'content_approval_granted': False}


def package_for(report, records):
    package = {'package_id': 'evidence-relocation-'+cm.sha256_json(report)[:24]}
    for key, payload in sorted(report['updates'].items()):
        collection, _ = cm.source.split_key(key)
        fields = (FRAGMENT_FIELDS if collection == 'source_fragments' else EVIDENCE_FIELDS
                  if collection == 'evidence_steps' else REFERENCE_FIELDS
                  if collection in {'questions','observations','position_nodes'} else set())
        if key in report.get('created_keys',[]):
            if collection != 'source_fragments' or key in records or not payload.get('split_from_fragment_id'):
                raise ValueError('unrecognized migration creation')
            package.setdefault(collection,[]).append(payload); continue
        before = records[key]['payload']
        changed = {k for k in set(before) | set(payload) if before.get(k) != payload.get(k)}
        if not fields or not changed <= fields:
            raise ValueError('relocation exceeded permitted fields: '+key)
        package.setdefault(collection, []).append(payload)
    return package


def source_files_current(plan):
    for bound in plan['source_files']:
        cm.source.verify_file(bound)


def render_report(report, *, applied=False):
    labels = {'auto_rebind':'引文與定位搬遷','already_current':'已完成，保留',
        'relocate_changed_quote':'新版原話／分段定位，待Claim更新',
        'source_deleted':'已刪除，不可作證據','already_deleted':'已標記刪除，保留',
        'context_only':'僅語境，不可作證據','source_text_missing':'新版未保留，不作證據',
        'located_review':'尚待可靠定位','exception':'尚待可靠定位','retired':'歷史保留'}
    cells = []
    for row in report['fragments']:
        fields = row.get('applied_fields') or {}; segments = row.get('candidate_segments',[])
        targets = row.get('target_paragraphs') or [row.get('target_paragraph') or fields.get('paragraph_key') or '']
        clock = '; '.join(f"{s['paragraph_key']} {s.get('media_time')}–{s.get('media_end_time')}" for s in segments)
        reason = (row.get('calibration_decision') or {}).get('reason',row['reason'])
        if row['status'] == 'source_deleted': new_quote = '歷史原文保留；不移入有效證據。'
        else: new_quote = row.get('candidate_quote',fields.get('verbatim_excerpt',''))
        values = [labels[row['status']],row['record_key'],row.get('old_paragraph'),', '.join(targets),
                  row.get('quote'),new_quote,f"{row.get('old_media_time')} → {clock}",reason]
        cells.append('<tr>'+''.join('<td>'+escape(str(v or ''))+'</td>' for v in values)+'</tr>')
    title = '證據搬遷已正式套用' if applied else '證據搬遷寫入前對照表'
    summary = '；'.join(labels[k]+' '+str(v) for k,v in report['summary'].items())
    return '<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>'+title+'</title><style>body{font:16px system-ui;margin:2rem;color:#172033}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccd3df;padding:.6rem;text-align:left;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere}td:nth-child(5),td:nth-child(6){min-width:20rem}thead{position:sticky;top:0;background:#eef3f9}</style><h1>'+title+'</h1><p>'+escape(summary)+'</p><p>零模型呼叫；沒有改寫 Claim 或 Evidence 論點。新版引文用字改變、跨段拆分或失去有效來源時，撤回相關 Evidence 支持資格，待 Claim 更新。口頭強調語和確認反應只保留作語境。時間為逐字稿段落邊界，不宣稱逐字對齊。原件、原來引文與全部編輯歷史保留。</p><p><input id="q" placeholder="搜尋引文、段落或ID" oninput="document.querySelectorAll(\'tbody tr\').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(this.value.toLowerCase()))"></p><table><thead><tr>'+''.join('<th>'+s+'</th>' for s in ['處理結果','片段ID','舊段落','新段落','舊引文','新版引文','時間（秒）','定位依據／說明'])+'</tr></thead><tbody>'+''.join(cells)+'</tbody></table></html>'


def prepare(store, root, *, source_id, old_paths, published, instruction, calibration_path=None):
    root = Path(root).resolve()
    if not instruction.strip():
        raise ValueError('exact owner direction required')
    if (root/'plan.json').exists():
        saved = m.read(root/'plan.json')
        if (saved['source_id'] != source_id or saved['instruction'] != instruction
                or saved['published_path'] != str(Path(published).resolve())
                or saved.get('calibration_path') != (str(Path(calibration_path).resolve()) if calibration_path else None)
                or saved['old_paths'] != [str(Path(p).resolve()) for p in old_paths]):
            raise ValueError('existing migration intent differs')
        return saved
    records = {cm.source.key(*k): cm.model_state(v)
               for k, v in store.read_affected_context([('source_documents', source_id)]).items()}
    source = records['source_documents/'+source_id]
    path = Path(source['payload']['source_path'])
    current_binding = m.binding(path)
    if current_binding['file_sha256'] != source['payload']['source_file_sha256']:
        raise ValueError('registered source file drift')
    new = snap(path)
    if new['body_sha256'] != source['payload']['source_body_sha256'] or snap(published) != {**new, 'file': m.binding(Path(published))}:
        raise ValueError('registered source is not the exact published body and clocks')
    paths = list(dict.fromkeys([*map(Path, old_paths), path, Path(published)]))
    bounds = [m.binding(p) for p in paths]
    epochs = {snap(p)['body_sha256']: snap(p) for p in paths}
    calibration = m.read(calibration_path) if calibration_path else None
    if calibration_path: bounds.append(m.binding(Path(calibration_path)))
    report = compare(records, source_id=source_id, old_sources=list(epochs.values()),
                     new=new, document=m.read(path),calibration=calibration)
    records_binding = m.immutable(root/'records-before.json', records)
    report_binding = m.immutable(root/'comparison.json', report)
    package = package_for(report, records)
    plan = store.plan_package(package, source_kind=VERSION, transactional=True)
    validate_plan(plan, report, records)
    result = {'schema_version': VERSION, 'source_id': source_id, 'instruction': instruction,
              'published_path': str(Path(published).resolve()),
              'old_paths': [str(Path(p).resolve()) for p in old_paths],
              'calibration_path':str(Path(calibration_path).resolve()) if calibration_path else None,
              'records': records_binding, 'report': report_binding,
              'source_files': bounds, 'package': package,
              'change_set_id': plan.change_set_id, 'fingerprint_sha256': plan.fingerprint_sha256,
              'implementation': m.binding(Path(__file__)), 'model_calls': 0}
    source_files_current(result)
    m.immutable(root/'plan.json', result)
    m._write_bytes(root/'comparison.html', render_report(report).encode())
    buf = io.StringIO(); writer = csv.writer(buf)
    writer.writerow(['fragment','action','reason','old_paragraph','new_paragraphs','old_quote','new_quote','fields'])
    for row in report['fragments']:
        writer.writerow([row['record_key'], row['status'], row['reason'], row.get('old_paragraph'),
            row.get('target_paragraphs', row.get('target_paragraph')), row.get('quote'),
            row.get('candidate_quote'), json.dumps(row.get('applied_fields'), ensure_ascii=False)])
    m._write_bytes(root/'comparison.csv', buf.getvalue().encode('utf-8-sig'))
    return result


def validate_plan(plan, report, records):
    expected = package_for(report, records)
    keys = {op.collection+'/'+op.object_id for op in plan.operations}
    if keys != set(report['updates']) or plan.review_events:
        raise ValueError('unexpected relocation write set or review events')
    for op in plan.operations:
        key = op.collection+'/'+op.object_id
        created = key in report.get('created_keys',[])
        if (op.operation != ('create' if created else 'update')
                or op.before_sha256 != (None if created else records[key]['content_sha256'])):
            raise ValueError('relocation preimage differs')
        post = {k: v for k, v in op.payload.items() if k != 'revision'}
        wanted = {k: v for k, v in report['updates'][key].items() if k != 'revision'}
        if post != wanted:
            raise ValueError('store changed expected relocation postimage: '+key)
    return expected


def apply(store, root):
    root = Path(root).resolve(); p = m.read(root/'plan.json')
    if p['schema_version'] != VERSION:
        raise ValueError('unsupported relocation')
    cm.source.verify_file(p['implementation'])
    for key in ('records', 'report'): cm.source.verify_file(p[key])
    report = m.read(p['report']['path']); records = m.read(p['records']['path'])
    history = store.get_change_history(p['change_set_id'])
    if history and history['status'] == 'applied':
        # Recover a lost response, never repeat a committed transaction.
        verify(store, root)
        return {'status': 'already_applied', 'change_set_id': p['change_set_id']}
    source_files_current(p)
    observed = {cm.source.key(*k): cm.model_state(v)
        for k, v in store.read_affected_context([('source_documents', p['source_id'])]).items()}
    if observed != records:
        raise ValueError('current records changed since comparison; recapture before apply')
    package = package_for(report, records)
    if package != p['package']: raise ValueError('saved package drift')
    plan = store.plan_package(package, source_kind=VERSION, transactional=True)
    validate_plan(plan, report, records)
    if plan.fingerprint_sha256 != p['fingerprint_sha256']:
        raise ValueError('planned transaction differs')
    pins = {cm.source.split_key(k): (v['revision'], v['content_sha256'], v['retired'])
            for k, v in records.items()}
    pins.update({cm.source.split_key(k):None for k in report.get('created_keys',[])})
    if not plan.operations:
        result = {'status': 'already_synchronized', 'change_set_id': None, 'model_calls': 0}
        m.immutable(root/'apply.json', result)
        verify(store, root)
        return result
    result = store.apply_plan(plan, metadata={'actor': 'owner_authorized_code_migration',
        'reason': p['instruction'], 'migration_plan': m.binding(root/'plan.json'),
        'content_approval_granted': False, 'model_calls': 0}, expected_current_record_pins=pins)
    m.immutable(root/'apply.json', result)
    verify(store, root)
    return result


def verify(store, root):
    root = Path(root); p = m.read(root/'plan.json'); report = m.read(p['report']['path'])
    before = m.read(p['records']['path']); history = store.get_change_history(p['change_set_id'])
    if report['updates'] and (not history or history['status'] != 'applied'):
        raise ValueError('missing committed history')
    for bound in [p['implementation'], p['records'], p['report']]: cm.source.verify_file(bound)
    current = store.read_current_records([cm.source.split_key(k) for k in set(before)|set(report.get('created_keys',[]))])
    mismatches = []
    for key, state in before.items():
        actual = current.get(cm.source.split_key(key))
        if key not in report['updates']:
            if actual is None or cm.model_state(actual) != state: mismatches.append(key)
        else:
            wanted = {**report['updates'][key], 'revision': state['revision']+1}
            if actual is None or actual['payload'] != wanted or actual.get('retired'):
                mismatches.append(key)
    if mismatches: raise ValueError('post-commit verification differs: '+str(mismatches[:8]))
    for key in report.get('created_keys',[]):
        actual = current.get(cm.source.split_key(key))
        if not actual or actual['revision'] != 1 or actual['payload'] != report['updates'][key]:
            raise ValueError('created atomic source fragment readback differs: '+key)
    source_files_current(p)
    result = {'status': 'verified', 'change_set_id': p['change_set_id'] if report['updates'] else None,
              'summary': report['summary'], 'updated_records': len(report['updates']),
              'model_calls': 0, 'claim_prose_changed': False,
              'content_approval_granted': False, 'downstream_requalification_complete': False}
    m.immutable(root/'verification.json', result)
    m._write_bytes(root/'result.html',render_report(report,applied=True).encode())
    return result


def locks():
    """Cooperate with existing maintenance/Claim workers; never stop them."""
    import os
    from backend.pipeline.source_reconciliation_runtime import maintenance_lock
    from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock
    stack = ExitStack()
    try:
        stack.enter_context(maintenance_lock())
        stack.enter_context(exclusive_cvp_run_lock({'global_lock_path': str(Path(os.environ['DATA_BASE_DIR']) /
            'wang-knowledge-platform/staging/cvp-production-357/registry-cvp.lock')}))
    except BaseException:
        stack.close(); raise
    return stack


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'apply', 'verify'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source-id'); parser.add_argument('--old-source', type=Path, action='append', default=[])
    parser.add_argument('--published', type=Path); parser.add_argument('--instruction-file', type=Path)
    parser.add_argument('--calibration',type=Path)
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv()
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    store = PostgresKnowledgeStore()
    if args.command == 'prepare':
        if not args.source_id or not args.published or not args.instruction_file: parser.error('prepare needs source, published and instruction')
        result = prepare(store, args.root, source_id=args.source_id, old_paths=args.old_source,
                         published=args.published, instruction=args.instruction_file.read_text(),calibration_path=args.calibration)
        print(json.dumps({'root': str(args.root), 'summary': m.read(result['report']['path'])['summary']}, ensure_ascii=False))
    elif args.command == 'apply':
        with locks(): result = apply(store, args.root)
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(json.dumps(verify(store, args.root), ensure_ascii=False))


if __name__ == '__main__': main()
