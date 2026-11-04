"""Deterministic evidence relocation preview. No model calls or SQL writes.

A match identifies a particular old occurrence, including strikethrough spans.
Missing text is never inferred to be deleted. Preview proposals are not content
approval and this module deliberately has no apply command.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict, deque
from copy import deepcopy
import csv
import hashlib
from html import escape
import io
import json
from pathlib import Path
import re

from backend.pipeline import transcript_claim_migration as migration
from backend.pipeline import claim_management as cm
from backend.pipeline.source_projection import SOFT_DELETION, project_script

VERSION = 'wang_transcript_evidence_dry_run_v1'
SPEAKERS = re.compile(r'(?:教授|聽眾|听众|學生|学生|問|问|答|講者|讲者|speaker\s*\d*)\s*[:：]', re.I)
LAYOUT_PUNCTUATION = frozenset('，。、「」『』“”‘’,')


def literal_layout(text):
    """Only whitespace and listed prose punctuation; keep words, numbers,
    negations, diacritics, verse colons, decimal points and math symbols."""
    chars=[];offsets=[]
    for i,ch in enumerate(text):
        if ch.isspace():
            # English word and numeric token boundaries are lexical content.
            left=text[:i].rstrip();right=text[i:].lstrip()
            if (left and right and left[-1].isascii() and left[-1].isalnum()
                    and right[0].isascii() and right[0].isalnum()):
                if not chars or chars[-1]!=' ':chars.append(' ');offsets.append(i)
            continue
        if ch in LAYOUT_PUNCTUATION:
            if i and i+1<len(text) and text[i-1].isdigit() and text[i+1].isdigit():
                chars.append(ch);offsets.append(i)
            continue
        chars.append(ch);offsets.append(i)
    return ''.join(chars),offsets


def locate(text, quote, expanded, old_full):
    for normalized in (False,True):
        source,offsets=literal_layout(expanded) if normalized else (expanded,list(range(len(expanded))))
        paragraph,_=literal_layout(text) if normalized else (text,[])
        needle,_=literal_layout(quote) if normalized else (quote,[])
        old_global=literal_layout(old_full)[0] if normalized else old_full
        origins=positions(paragraph,needle)
        if len(origins)!=1:continue
        origin=origins[0];windows=[(0,len(paragraph),'complete_old_paragraph')]
        for flank in (40,20,8):
            left=max(0,origin-flank);right=min(len(paragraph),origin+len(needle)+flank)
            if right-left>=24:windows.append((left,right,f'exact_context_{flank}'))
        if len(needle)>=12:windows.append((origin,origin+len(needle),'unique_literal_quote'))
        for left,right,label in windows:
            if len(positions(old_global,paragraph[left:right]))!=1:continue
            matches=positions(source,paragraph[left:right])
            if len(matches)==1:
                start=matches[0]+origin-left;end=start+len(needle)
                return offsets[start],offsets[end-1]+1,('layout_only_' if normalized else '')+label
    return None


def positions(text, needle):
    if not needle:return []
    result=[];start=0
    while (start:=text.find(needle,start))>=0:
        result.append(start);start+=1
    return result


def expanded_source(document):
    """Use common spoken-row selection; retain deleted chars only for diagnosis."""
    rows=document.get('script',[]) if isinstance(document,dict) else document
    expanded=deepcopy(rows)
    for row in expanded:
        row['text']=SOFT_DELETION.sub(lambda m:m.group(1),str(row.get('text') or ''))
    projected=project_script(expanded)
    # Projection preserves physical row IDs, including fully deleted rows.
    raw={str(row.get('index')):row for row in rows}
    if len(raw)!=len(rows):raise ValueError('source row IDs are ambiguous')
    result=[];offset=0
    for i,row in enumerate(projected.spoken_rows,1):
        original=str(raw[str(row.get('index'))].get('text') or '')
        parts=[];deleted=[];live_positions=[];cursor=0;live_offset=0
        for match in SOFT_DELETION.finditer(original):
            prefix=original[cursor:match.start()]
            parts.append(prefix);deleted.extend([False]*len(prefix))
            live_positions.extend(range(live_offset,live_offset+len(prefix)))
            live_offset+=len(prefix)
            content=match.group(1);parts.append(content);deleted.extend([True]*len(content))
            live_positions.extend([None]*len(content));live_offset+=1 # common live_text inserts newline
            cursor=match.end()
        suffix=original[cursor:];parts.append(suffix);deleted.extend([False]*len(suffix))
        live_positions.extend(range(live_offset,live_offset+len(suffix)))
        text=str(row.get('text') or '')
        result.append({'key':f'S{i:04d}','text':text,'start':offset,'end':offset+len(text),
            'deleted':deleted,'live_positions':live_positions,'simple':''.join(parts)==text})
        offset+=len(text)+1
    return '\n'.join(row['text'] for row in result),result


def classify(fragment, old, new, expanded, rows):
    """Fail closed for ambiguous occurrence, changed speaker or split quotation."""
    quote=fragment.get('verbatim_excerpt') or '';key=str(fragment.get('paragraph_key'))
    def outcome(status,reason,**extra):return {'status':status,'reason':reason,**extra}
    if fragment.get('source_modality')=='visual':return outcome('exception','visual_evidence_requires_separate_adapter')
    if not quote:return outcome('exception','empty_quote')
    current=new['paragraphs'].get(key,{})
    if fragment.get('source_sha256')==new['body_sha256']:
        if quote in current.get('text','') and fragment.get('anchor_state') in {'source_version_bound','canonical_citation_bound','verified','valid'}:
            return outcome('already_current','exact_current_source_binding')
        return outcome('exception','current_epoch_invalid_or_inactive_binding')
    previous=old['paragraphs'].get(key,{})
    text=str(previous.get('text') or '')
    if fragment.get('source_sha256')!=old['body_sha256']:
        return outcome('exception','unknown_source_epoch')
    occurrences=positions(text,quote)
    if len(occurrences)!=1:return outcome('exception','old_quote_missing_or_repeated',old_match_count=len(occurrences))
    match=locate(text,quote,expanded,'\n'.join(str(p.get('text') or '') for p in old['paragraphs'].values()))
    if match is None:
        return outcome('exception','no_unique_exact_context',literal_candidate_count=len(positions(expanded,quote)))
    mapped,mapped_end,basis=match
    containing=[row for row in rows if row['start']<=mapped and mapped_end<=row['end']]
    if len(containing)!=1:return outcome('exception','quote_crosses_new_paragraphs',match_basis=basis)
    row=containing[0];start=mapped-row['start'];end=mapped_end-row['start']
    if not row['simple']:return outcome('exception','nonspoken_markup_requires_manual_mapping',match_basis=basis)
    marks=row['deleted'][start:end]
    if any(marks):
        return outcome('source_deleted','bound_occurrence_soft_deleted',deletion_extent='entire_quote' if all(marks) else 'partial_quote',
            target_paragraph=row['key'],match_basis=basis,proposed_fields={'anchor_state':'source_deleted',
            'source_binding_status':'source_deleted'},preserve_historical_quote=True)
    target=new['paragraphs'].get(row['key'],{});target_text=str(target.get('text') or '')
    mapped_live=row['live_positions'][start]
    new_quote=target_text[mapped_live:row['live_positions'][end-1]+1]
    if literal_layout(new_quote)[0]!=literal_layout(quote)[0]:
        return outcome('exception','live_projection_does_not_match_exact_occurrence')
    if SPEAKERS.findall(text)!=SPEAKERS.findall(target_text):
        return outcome('exception','speaker_labels_changed',target_paragraph=row['key'],match_basis=basis)
    fields={'paragraph_key':row['key'],'source_segment_index':target.get('index'),
        'source_sha256':new['body_sha256'],'paragraph_text_sha256':hashlib.sha256(target_text.encode()).hexdigest(),
        'verbatim_excerpt':new_quote,'verbatim_excerpt_sha256':hashlib.sha256(new_quote.encode()).hexdigest(),
        'media_time':target.get('start_time'),'media_end_time':target.get('end_time'),
        'anchor_state':'source_version_bound'}
    return outcome('auto_rebind','unique_exact_occurrence',target_paragraph=row['key'],match_basis=basis,
        proposed_fields=fields,quote_unchanged=new_quote==quote,lexical_characters_unchanged=True,
        timestamp_precision='paragraph_not_word')


def values(value):
    if isinstance(value,str):yield value
    elif isinstance(value,dict):
        for item in value.values():yield from values(item)
    elif isinstance(value,list):
        for item in value:yield from values(item)


def preview(records, *, source_id, old, new, document, align_sequence=False, calibrate_context=False):
    expanded,rows=expanded_source(document)
    alignment=None;calibration=None
    if calibrate_context and not align_sequence:raise ValueError('context calibration requires source sequence alignment')
    if align_sequence:
        from backend.pipeline.transcript_evidence_alignment import SourceAlignment
        alignment=SourceAlignment(old,new,expanded,rows)
    if calibrate_context:
        from backend.pipeline.transcript_evidence_calibration import ContextCalibration
        calibration=ContextCalibration(alignment)
    by_id=defaultdict(list)
    for key in records:by_id[key.split('/',1)[1]].append(key)
    reverse=defaultdict(set)
    for key,state in records.items():
        if state.get('retired'):continue
        for ref in values(state['payload']):
            for target in by_id.get(ref,[]):
                if target!=key:reverse[target].add(key)
    report=[]
    for key,state in sorted(records.items()):
        f=state['payload']
        if not key.startswith('source_fragments/') or f.get('source_id')!=source_id:continue
        result={'status':'retired','reason':'history_only'} if state.get('retired') else classify(f,old,new,expanded,rows)
        if alignment:result=alignment.refine(f,result)
        if calibration:result=calibration.refine(f,result)
        if alignment and result['status']=='source_deleted' and 'candidate_segments' not in result:
            candidate=alignment.candidate(f)
            if candidate and candidate['localized'] and candidate['matched_deleted_character_count']:
                result={**result,**candidate}
        if alignment and result['status'] in {'auto_rebind','already_current'} and 'candidate_quote' not in result:
            from backend.pipeline.transcript_evidence_alignment import changes,speaker_annotation
            target_quote=result.get('proposed_fields',{}).get('verbatim_excerpt',f.get('verbatim_excerpt',''))
            result.update(candidate_quote=target_quote,text_changes=changes(f.get('verbatim_excerpt',''),target_quote))
            if result['status']=='already_current':result['target_paragraph']=str(f.get('paragraph_key'))
            paragraph=new['paragraphs'].get(str(result.get('target_paragraph')),{}).get('text','')
            hits=positions(paragraph,target_quote)
            if len(hits)==1:
                p=new['paragraphs'][str(result['target_paragraph'])]
                segment={'paragraph_key':str(result['target_paragraph']),
                    'live_start':hits[0],'live_end':hits[0]+len(target_quote),
                    'verbatim_excerpt':target_quote,'media_time':p.get('start_time'),
                    'media_end_time':p.get('end_time'),'source_segment_index':p.get('index')}
                result['candidate_segments']=[segment]
                annotation=speaker_annotation([segment],new)
                result['speaker_annotation']=annotation
                if annotation and result['status']=='auto_rebind':result['proposed_fields'].update(annotation['proposed_fields'])
        seen=set();pending=deque(reverse[key])
        while pending:
            owner=pending.popleft()
            if owner in seen:continue
            seen.add(owner);pending.extend(reverse[owner]-seen)
        report.append({'record_key':key,'current_pin':{'revision':state['revision'],'content_sha256':state['content_sha256'],'retired':bool(state.get('retired'))},
            'old_paragraph':f.get('paragraph_key'),'quote':f.get('verbatim_excerpt'),
            'old_media_time':f.get('media_time'),'old_media_end_time':f.get('media_end_time'),'direct_consumers':sorted(reverse[key]),
            'affected_claims':sorted(k for k in seen if k.startswith('claims/')),
            'downstream_consumers':sorted(k for k in seen if k.split('/',1)[0] not in cm.CLAIM_COLLECTIONS),**result})
    fragments={row['record_key']:row for row in report};evidence=[];speakers=[]
    for key,state in sorted(records.items()):
        if not key.startswith('evidence_steps/') or state.get('retired'):continue
        f=state['payload'];ids=set(f.get('source_fragment_ids',[]))|({f['source_fragment_id']} if f.get('source_fragment_id') else set())
        matching=[fragments['source_fragments/'+fid] for fid in sorted(ids) if 'source_fragments/'+fid in fragments]
        annotations={r['record_key']:r['speaker_annotation'] for r in matching if r.get('speaker_annotation')}
        if annotations:
            roles={a['speaker'] for a in annotations.values()}
            complete=len(annotations)==len(ids) and None not in roles
            speakers.append({'record_key':key,'fragment_annotations':annotations,
                'proposed_fields':{'speaker':next(iter(roles)) if len(roles)==1 else 'mixed'} if complete else {},
                'annotation_only':True,'content_approval_granted':False})
        deleted=[r['record_key'] for r in matching if r['status']=='source_deleted']
        if not deleted:continue
        status='source_deleted' if len(deleted)==len(ids) else 'partially_deleted'
        evidence.append({'record_key':key,'status':status,'label':'已經刪除' if status=='source_deleted' else '部分證據已刪除',
            'deleted_fragment_keys':deleted,'proposed_fields':{'source_binding_status':status},
            'content_approval_granted':False,'remaining_fragment_keys':sorted('source_fragments/'+fid for fid in ids if 'source_fragments/'+fid not in deleted)})
    return {'schema_version':VERSION,'alignment_mode':'context_time_scripture_v3' if calibrate_context else 'whole_source_v2' if align_sequence else 'literal_v1','dry_run':True,'model_calls':0,'production_writes':0,
        'content_approval_granted':False,'source_id':source_id,'summary':dict(Counter(r['status'] for r in report)),
        'fragments':report,'evidence_deletion_proposals':evidence,
        'evidence_speaker_proposals':speakers,
        'exceptions':[r for r in report if r['status']=='exception'],
        'refresh_candidates':[r for r in report if r['status']=='located_review'],
        'completion_scope':'evidence_binding_preview_only','downstream_requalification_complete':False}


def dry_run(store, root, output, *, align_sequence=False, calibrate_context=False):
    root=Path(root).resolve();output=Path(output).resolve();m=migration.load(root)
    frozen=migration.read(m['records']['path']);keys={(r['collection'],r['object_id']) for r in frozen}
    # Include committed creations; proposals and unused identity reservations
    # never count as persisted records.
    actions={}
    for path in (root/'units').glob('**/request.json'):
        request=cm.Request.model_validate_json(path.read_text());aid=request.action_id
        if aid in actions:continue
        parent=store.get_cvp_group('CMA-'+cm.sha256_json(aid))
        if not parent:continue
        if (parent.get('request')!=request.model_dump(mode='json') or
            request.model_dump(mode='json')['source_bindings']!={m['source_id']:m['after']} or
            not Path(parent['execution_root']).resolve().is_relative_to(root)):
            raise ValueError('saved action escaped source migration')
        actions[aid]={'result':parent.get('result'),'created_keys':parent.get('created_keys',[])}
        keys.update(cm.source.split_key(k) for k in parent.get('created_keys',[]))
    current=store.read_current_records(sorted(keys))
    records={cm.source.key(*k):v for k,v in current.items()}
    source=records.get('source_documents/'+m['source_id'],{})
    expected_source={**m['after_source_record'],'revision':m['before_source_record']['revision']+1}
    if source.get('retired') or source.get('payload')!=expected_source:
        raise ValueError('registered source changed since source handoff')
    old=cm.source.source_snapshot(cm.source.FileBinding.model_validate(m['before']))
    new=cm.source.source_snapshot(cm.source.FileBinding.model_validate(m['after']))
    document=migration.read(m['after']['path'])
    result=preview(records,source_id=m['source_id'],old=old,new=new,document=document,align_sequence=align_sequence,calibrate_context=calibrate_context)
    result.update(item=m['item'],beta_revision=m['beta_revision'],migration_manifest=migration.binding(root/'manifest.json'),
        before_source=m['before'],after_source=m['after'],actions_at_capture=actions,
        source_missing_keys=sorted(cm.source.key(*k) for k in keys-set(current)))
    if result['source_missing_keys']:raise ValueError('frozen/current record census has missing records')
    # A repeated command reproduces the same plan. Drift never overwrites an
    # older preview or silently changes the future write preconditions.
    snapshot=migration.immutable(output/'current-records.json',[
        {'record_key':k,**v} for k,v in sorted(records.items())])
    result['current_records']=snapshot
    result['implementation']=migration.binding(Path(__file__))
    if align_sequence:
        from backend.pipeline import transcript_evidence_alignment
        result['alignment_implementation']=migration.binding(Path(transcript_evidence_alignment.__file__))
        result['literal_verification']=transcript_evidence_alignment.verify_preview(result,old,new,document)
    if calibrate_context:
        from backend.pipeline import transcript_evidence_calibration
        from backend.api import scripture
        result['calibration_implementation']=migration.binding(Path(transcript_evidence_calibration.__file__))
        result['reference_parser_implementation']=migration.binding(Path(scripture.__file__))
    migration.immutable(output/'dry-run.json',result)
    html=render(result);migration._write_bytes(output/'comparison.html',html.encode())
    buffer=io.StringIO();writer=csv.writer(buffer)
    writer.writerow(['status','reason','fragment','old_paragraph','new_paragraphs','old_quote','new_quote','old_time','new_start','new_end','deletion_extent','affected_claims','change_flags','text_changes','candidate_segments','speaker_label','calibration'])
    for row in result['fragments']:
        fields=row.get('proposed_fields',{})
        segments=row.get('candidate_segments',[])
        writer.writerow([row['status'],row['reason'],row['record_key'],row['old_paragraph'],','.join(row.get('target_paragraphs',[]) or [str(row.get('target_paragraph') or '')]),row['quote'],row.get('candidate_quote',fields.get('verbatim_excerpt','')),row['old_media_time'],segments[0]['media_time'] if segments else fields.get('media_time',''),segments[-1]['media_end_time'] if segments else fields.get('media_end_time',''),row.get('deletion_extent',''),';'.join(row['affected_claims']),';'.join(row.get('change_flags',[])),json.dumps(row.get('text_changes',[]),ensure_ascii=False),json.dumps(segments,ensure_ascii=False),(row.get('speaker_annotation') or {}).get('label',''),json.dumps(row.get('calibration'),ensure_ascii=False)])
    migration._write_bytes(output/'comparison.csv',buffer.getvalue().encode('utf-8-sig'))
    return {'output':str(output),'summary':result['summary'],'evidence_deletion_proposals':len(result['evidence_deletion_proposals']),
        'model_calls':0,'production_writes':0}


def render(result):
    labels={'auto_rebind':'可自動搬遷','located_review':'已定位／待 refresh','source_deleted':'已經刪除（擬標記）','already_current':'已綁定新版','exception':'尚未可靠定位','retired':'已退役／保留歷史'}
    def e(v):return escape(str(v if v is not None else '—'))
    reasons={'no_unique_exact_context':'新版沒有唯一的文字對應，不能自動替換',
        'old_quote_missing_or_repeated':'舊引文本身找不到或出現多次',
        'speaker_labels_changed':'段落講者標記改變，需另查說話歸屬',
        'quote_crosses_new_paragraphs':'引文跨越新版多個段落',
        'unique_exact_occurrence':'唯一文字對應（可能僅有標點／排版差異）',
        'exact_current_source_binding':'已核對目前新版引文',
        'bound_occurrence_soft_deleted':'原綁定位置的引文全部或部分被軟刪除'}
    reasons.update(sequence_verified_unchanged_quote='順序及唯一上下文確認，文字未改變',
        source_located_requires_refresh='來源位置已定位；文字或分段有改動，待 refresh',
        aligned_bound_occurrence_soft_deleted='順序及上下文確認：原引文字元落在刪除線內',
        alignment_candidate_not_verified='有候選位置，但邊界、上下文或文字重合不足，未確認')
    reasons.update(context_time_metadata_only_match='上下文與時間確認；僅移除舊問答標記',
        context_time_passage_located='前後文與音訊時間碼確認來源範圍；引文邊界待 refresh',
        context_time_bound_soft_deletion='前後文與時間確認：原引用落在刪除線內')
    flag_labels={'quote_text_changed':'引文文字有修改','cross_paragraph':'跨新版段落',
        'changed_quote_boundary':'引文邊界文字有改動','missing_unique_bracketing_context':'前後唯一上下文不足',
        'insufficient_literal_overlap':'相同文字比例不足','span_length_changed_substantially':'長度差異較大',
        'span_too_many_paragraphs':'候選跨越過多段落','negation_number_or_original_language_changed':'否定、數字或希臘／希伯來文字有變動',
        'quotation_boundary_requires_refresh':'已定位來源段落；具體引文範圍待 refresh'}
    rows=[]
    # Exceptions and deletions first; every record still has its own row.
    for row in sorted(result['fragments'],key=lambda r:({'exception':0,'source_deleted':1}.get(r['status'],2),r['record_key'])):
        fields=row.get('proposed_fields',{})
        segments=row.get('candidate_segments',[])
        clock=f"{row['old_media_time']} → {fields.get('media_time','—')}–{fields.get('media_end_time','—')}"
        if segments:clock=str(row['old_media_time'])+' → '+'; '.join(f"{s['paragraph_key']}: {s['media_time']}–{s['media_end_time']}" for s in segments)
        diff='\n'.join(f"− {p['old']}\n＋ {p['new']}" for p in row.get('text_changes',[]))
        flags='\n'.join(flag_labels.get(f,f) for f in row.get('change_flags',[]))
        speaker=(row.get('speaker_annotation') or {}).get('label','未標記')
        basis=''
        calibrated=row.get('calibration')
        if calibrated:
            old_clock=calibrated['audio_position']['old_interval']
            basis=f'唯一前後文＋時間碼相符（舊範圍 {old_clock[0]}–{old_clock[1]} 秒）'
            if calibrated.get('matched_explicit_verse'):
                from backend.api.scripture import format_chinese_reference
                basis+='\n經文定位：'+format_chinese_reference(calibrated['matched_explicit_verse'])
            elif calibrated.get('shared_explicit_chapters'):basis+='\n相同書卷章：'+', '.join(calibrated['shared_explicit_chapters'])
            if calibrated.get('removed_speaker_prefix'):basis+='\n舊問答標記另存：'+calibrated['removed_speaker_prefix']
        elif row.get('calibration_alternatives'):
            basis='有多個候選，尚未消歧：\n'+'\n'.join(
                ', '.join(f"{s['paragraph_key']} ({s['media_time']}–{s['media_end_time']} 秒)" for s in option['candidate_segments'])
                for option in row['calibration_alternatives'])
        rows.append('<tr data-status="'+e(row['status'])+'">'+''.join('<td>'+e(v)+'</td>' for v in [labels[row['status']],reasons.get(row['reason'],row['reason']),row['record_key'],row['old_paragraph'],','.join(row.get('target_paragraphs',[])) or row.get('target_paragraph'),row['quote'],row.get('candidate_quote',fields.get('verbatim_excerpt')),diff,flags,speaker,basis,clock,'\n'.join(row['affected_claims'])])+'</tr>')
    controls='<p><label>分類 <select id="filter" onchange="filterRows()"><option value="">全部</option>'+''.join('<option value="'+e(k)+'">'+e(v)+'</option>' for k,v in labels.items())+'</select></label> <input id="query" placeholder="搜尋引文或 ID" oninput="filterRows()"></p>'
    deletions='<h2>Evidence 擬標記</h2><ul>'+''.join('<li>'+e(x['record_key'])+'：'+e(x['label'])+'</li>' for x in result['evidence_deletion_proposals'])+'</ul>'
    return '<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>證據搬遷 dry-run</title><style>body{font:16px system-ui;margin:2rem;color:#172033}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccd3df;padding:.6rem;text-align:left;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere}td:nth-child(6){min-width:20rem}thead{position:sticky;top:0;background:#eef3f9}</style><h1>證據搬遷 dry-run</h1><p>'+e(result.get('item',''))+' · Beta '+e(result.get('beta_revision',''))+'</p><p>純程式比對；零模型呼叫、零正式資料寫入。Claim 內容不改寫，不授予新的審核資格。時間為段落邊界，並非逐字對齊。音訊位置採用已保存的時間碼，本次未重聽音訊；經文只用於定位原有來源，不以標準經文補寫引文。「已定位／待 refresh」只確認對應來源位置，不代表意思等價或可直接替換；可確定的聽眾發言另作標記，不因講者不同而阻擋定位；未可靠定位的候選也會顯示供查核。</p><p>'+e('；'.join(labels[k]+' '+str(v) for k,v in result['summary'].items()))+'</p><p>「已經刪除」表示原綁定的引文全部或部分落在新版刪除線內。保留歷史原文；擬將引用標為無效，相關 Evidence 標為全部／部分刪除。文字找不到不等於刪除。下游 Claim／CVP 資格須另行 refresh。</p>'+deletions+controls+'<table id="fragments"><thead><tr>'+''.join('<th>'+v+'</th>' for v in ['分類','原因','證據片段 ID','目前段落','新段落','目前引文（原記錄）','新版逐字引文（候選）','文字差異（− 舊／＋ 新）','待檢查事項','講者標記','校準依據','時間（秒）','受影響 Claims'])+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table><script>function filterRows(){const s=document.getElementById("filter").value,q=document.getElementById("query").value.toLowerCase();document.querySelectorAll("#fragments tbody tr").forEach(r=>r.hidden=(s&&r.dataset.status!==s)||!r.textContent.toLowerCase().includes(q))}</script></html>'


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--align-sequence',action='store_true',help='also locate changed quotations using source order and unique context; no content approval')
    parser.add_argument('--calibrate-context',action='store_true',help='use saved audio timecodes, speaker metadata and source scripture references; requires --align-sequence')
    args=parser.parse_args()
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    print(json.dumps(dry_run(PostgresKnowledgeStore(),args.root,args.output,align_sequence=args.align_sequence,calibrate_context=args.calibrate_context),ensure_ascii=False))


if __name__=='__main__':main()
