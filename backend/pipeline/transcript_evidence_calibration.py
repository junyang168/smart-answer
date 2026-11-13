"""Calibrate source locations with saved audio timecodes and literal context.

No audio recognition, canonical Bible substitution, content approval or writes.
Scripture IDs are parsed from the actual old/new source with the common parser.
"""
import re
from bisect import bisect_left
from backend.api.scripture import reference_slugs,spoken_references,spoken_chapters
from backend.pipeline.transcript_evidence_alignment import changes,speaker_annotation
from backend.pipeline.transcript_evidence_migration import literal_layout,positions

SPEAKER_PREFIX=re.compile(r'^\s*(?:[（(]\s*(?:問|问|答|眾|众|教授|聽眾|听众)\s*[:：]\s*[）)]|(?:教授|聽眾|听众|學生|学生)\s*[:：])\s*')
TIME_TOLERANCE_SECONDS=10


def content_quote(quote):
    match=SPEAKER_PREFIX.match(quote)
    return (quote[match.end():],quote[:match.end()]) if match else (quote,'')


def audio_position(fragment,segments):
    start,end=fragment.get('media_time'),fragment.get('media_end_time')
    intervals=[(s.get('media_time'),s.get('media_end_time')) for s in segments]
    known=(isinstance(start,(int,float)) and isinstance(end,(int,float)) and 0<=start<=end and intervals
           and all(isinstance(a,(int,float)) and isinstance(b,(int,float)) and 0<=a<=b for a,b in intervals))
    consistent=bool(known and all(a<=end+TIME_TOLERANCE_SECONDS and b>=start-TIME_TOLERANCE_SECONDS for a,b in intervals))
    return {'basis':'saved_transcript_timecodes','audio_relistened':False,'old_interval':[start,end],
        'new_intervals':[list(i) for i in intervals],'tolerance_seconds':TIME_TOLERANCE_SECONDS,'consistent':consistent}


def verified_legacy_interval(fragment, proof):
    """Recover absent legacy clocks from an exact source and verified backup.

    This is a locator witness, not new recognition or content approval. Existing
    clocks are never replaced; a changed source, timeline, paragraph or backup
    is rejected before a technical relocation can use the recovered interval.
    """
    import copy
    import hashlib
    import json
    from pathlib import Path
    from backend.pipeline import source_reconciliation as source
    from backend.api.sc_api.script_delta import ScriptDelta

    for name in ('source_file', 'timeline_file', 'backup_manifest'):
        source.verify_file(proof[name])
    item = proof['item']
    if not item or any(ch in item for ch in '/\\\x00'):
        raise ValueError('invalid legacy clock item')
    manifest_path = Path(proof['backup_manifest']['path'])
    manifest = json.loads(manifest_path.read_bytes())
    relative = 'script/' + item + '.json'
    if (manifest.get('schema_version') != 'transcript-promotion-backup.v1'
            or manifest['intent']['item'] != item
            or manifest['files']['production'][relative]['sha256'] != proof['timeline_file']['file_sha256']
            or Path(proof['timeline_file']['path']).resolve() != (manifest_path.parent / 'production' / relative).resolve()):
        raise ValueError('legacy timeline is not the exact backed-up item')
    old = source.source_snapshot(proof['source_file'])
    paragraph = old['paragraphs'].get(fragment.get('paragraph_key'))
    if (old['body_sha256'] != fragment.get('source_sha256') or not paragraph
            or (fragment.get('source_segment_index') is not None
                and str(paragraph.get('index')) != str(fragment['source_segment_index']))
            or hashlib.sha256(paragraph['text'].encode()).hexdigest() != fragment.get('paragraph_text_sha256')
            or fragment.get('verbatim_excerpt') not in paragraph['text']):
        raise ValueError('legacy clock source or fragment binding differs')
    document = json.loads(Path(proof['source_file']['path']).read_bytes())
    rows = copy.deepcopy(document['script'] if isinstance(document, dict) else document)
    entries = json.loads(Path(proof['timeline_file']['path']).read_bytes())['entries']
    entries = copy.deepcopy(entries)
    for i, entry in enumerate(entries):
        entry['next_item'] = entries[i + 1]['index'] if i + 1 < len(entries) else None
    delta = ScriptDelta.__new__(ScriptDelta)
    delta.timelineDictionary = {entry['index']: entry for entry in entries}
    delta.add_timeline(rows)
    matches = [row for row in rows if str(row['index']) == str(paragraph['index'])]
    if len(matches) != 1:
        raise ValueError('ambiguous legacy physical paragraph')
    start, end = matches[0].get('start_time'), matches[0].get('end_time')
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or not 0 <= start < end:
        raise ValueError('invalid recovered legacy interval')
    for name in ('source_file', 'timeline_file', 'backup_manifest'):
        source.verify_file(proof[name])
    for name, value in [('media_time', start), ('media_end_time', end)]:
        if fragment.get(name) is not None and fragment[name] != value:
            raise ValueError('recovered legacy clock contradicts an existing clock')
    return {**fragment, 'media_time': start, 'media_end_time': end}


def source_references(text):
    return sorted(set(reference_slugs(text))|{ref for _,ref in spoken_references(text)})


class ContextCalibration:
    def __init__(self,alignment):self.alignment=alignment

    def local_short_candidate(self,fragment,core,occurrence):
        """One-character correction inside a unique, time-bound alignment gap.

        This never alone creates an automatic replacement. It recovers short
        questions the whole-source matcher omitted, such as 在/再 corrections.
        """
        e=self.alignment;needle,local_offsets=literal_layout(core)
        if not 3<=len(needle)<=12:return None
        start=e.starts[str(fragment['paragraph_key'])]+occurrence
        a=bisect_left(e.left_offsets,start);b=bisect_left(e.left_offsets,start+len(core))
        left=[x for x in e.anchors if x['old_end']<=a and a-x['old_end']<=80]
        right=[x for x in e.anchors if x['old_start']>=b and x['old_start']-b<=80]
        if not left or not right:return None
        left=max(left,key=lambda x:x['old_end']);right=min(right,key=lambda x:x['old_start'])
        lo,hi=left['new_end'],right['new_start']
        if not 0<=hi-lo<=300:return None
        matches=[]
        for pos in range(lo,hi-len(needle)+1):
            target=e.right[pos:pos+len(needle)]
            if sum(x!=y for x,y in zip(needle,target))>1:continue
            raw_start=e.right_offsets[pos];raw_end=e.right_offsets[pos+len(needle)-1]+1
            row=next((r for r in e.rows if r['start']<=raw_start and raw_end<=r['end'] and r['simple']),None)
            if row is None:continue
            p=e.new['paragraphs'][row['key']]
            segment={'paragraph_key':row['key'],'source_segment_index':p.get('index'),
                'media_time':p.get('start_time'),'media_end_time':p.get('end_time')}
            if not audio_position(fragment,[segment])['consistent']:continue
            begin=raw_start-row['start'];end=raw_end-row['start'];live=[i for i in row['live_positions'][begin:end] if i is not None]
            segment.update(live_start=min(live) if live else None,live_end=max(live)+1 if live else None,
                verbatim_excerpt=p['text'][min(live):max(live)+1] if live else '',
                expanded_start=begin,expanded_end=end,expanded_excerpt=row['text'][begin:end])
            deleted=[{'old_offset':start+local_offsets[i],'new_offset':e.right_offsets[pos+i],'character':ch}
                for i,ch in enumerate(needle) if ch==target[i] and e.right_offsets[pos+i] in e.deleted]
            matches.append({'localized':True,'candidate_quote':segment['verbatim_excerpt'],'candidate_segments':[segment],
                'target_paragraphs':[row['key']],'target_paragraph':row['key'],
                'alignment':{'method':'unique_context_time_bounded_short_match_v1','left_anchor':left,'right_anchor':right,
                    'matched_fraction':sum(x==y for x,y in zip(needle,target))/len(needle),'length_ratio':1,'both_endpoints_matched':needle[0]==target[0] and needle[-1]==target[-1]},
                'change_flags':['local_word_correction_requires_refresh'],'text_changes':changes(core,segment['verbatim_excerpt']),
                'speaker_annotation':speaker_annotation([segment],e.new),'old_quote_range':{'start':start,'end':start+len(core)},
                'deleted_character_bindings':deleted,'matched_deleted_character_count':len(deleted),
                'deletion_extent':'entire_quote' if len(deleted)==len(needle) else 'partial_quote' if deleted else None,
                'content_equivalence_proven':False,'content_approval_granted':False,'timestamp_precision':'paragraph_not_word'})
        return matches[0] if len(matches)==1 else None

    def refine(self,fragment,previous):
        if previous['status']!='exception':return previous
        engine=self.alignment;quote=fragment.get('verbatim_excerpt') or '';core,prefix=content_quote(quote)
        trimmed=core.rstrip('…⋯');ellipsis=core[len(trimmed):];core=trimmed
        paragraph=engine.old['paragraphs'].get(str(fragment.get('paragraph_key')),{}).get('text','')
        hits=positions(paragraph,quote)
        if not hits:return previous
        options=[]
        for hit in hits:
            projected={**fragment,'verbatim_excerpt':core}
            candidate=engine.candidate(projected,old_occurrence=hit+len(prefix))
            if candidate is None:candidate=self.local_short_candidate(fragment,core,hit+len(prefix))
            if candidate is None:continue
            clocks=audio_position(fragment,candidate['candidate_segments'])
            anchors=candidate['alignment']['left_anchor'] and candidate['alignment']['right_anchor']
            if not clocks['consistent'] or not anchors:continue
            if len(candidate['candidate_segments'])>4:continue
            # Reject a lone shared word inside a substantially different quote.
            # Metadata-only short replies still retain all their spoken words.
            target_text='\n'.join(engine.new['paragraphs'][k]['text'] for k in candidate['target_paragraphs'])
            shared_chapters=sorted({ref for _,ref in spoken_chapters(core)}&{ref for _,ref in spoken_chapters(target_text)})
            if candidate['alignment']['matched_fraction']<.2 and not shared_chapters:continue
            candidate['calibration']={'audio_position':clocks,'original_occurrence':hit,
                'removed_speaker_prefix':prefix,'original_quote':quote,'content_quote':core,
                'ignored_trailing_ellipsis':ellipsis,'shared_explicit_chapters':shared_chapters,
                'boundary_verified':candidate['alignment']['both_endpoints_matched']}
            candidate['previous_reason']=previous['reason']
            options.append(candidate)
        if not options:return previous
        groups={tuple(c['target_paragraphs']) for c in options}
        # Repeated old occurrences can collapse only to the same destination
        # paragraph and the same deletion/attribution outcome. Time alone never
        # chooses one arbitrary repeated answer.
        if len(hits)>1:
            signatures={(tuple(c['target_paragraphs']),bool(c['matched_deleted_character_count']),
                (c.get('speaker_annotation') or {}).get('speaker')) for c in options}
            if len(options)!=len(hits) or len(groups)!=1 or len(signatures)!=1:
                return {**previous,'calibration_alternatives':options}
        candidate=options[0];candidate['calibration']['old_occurrence_count']=len(hits)
        candidate['calibration']['same_destination_for_all_occurrences']=len(hits)>1
        candidate['calibration']['old_source_references']=source_references(paragraph)
        candidate['calibration']['new_source_references']=source_references('\n'.join(
            engine.new['paragraphs'][key]['text'] for key in candidate['target_paragraphs']))
        candidate['calibration']['shared_references']=sorted(set(candidate['calibration']['old_source_references'])&set(candidate['calibration']['new_source_references']))
        result={**previous,**candidate}
        if candidate['matched_deleted_character_count'] and not any(ch.isalnum() for ch in candidate['candidate_quote']):
            return {**result,'status':'source_deleted','reason':'context_time_bound_soft_deletion',
                'proposed_fields':{'anchor_state':'source_deleted','source_binding_status':'source_deleted'},'preserve_historical_quote':True}
        body_equal=literal_layout(core)[0]==literal_layout(content_quote(candidate['candidate_quote'])[0])[0]
        if (body_equal and not ellipsis and candidate['alignment']['both_endpoints_matched'] and len(candidate['candidate_segments'])==1):
            from hashlib import sha256
            segment=candidate['candidate_segments'][0];p=engine.new['paragraphs'][segment['paragraph_key']]
            fields={k:segment[k] for k in ('paragraph_key','source_segment_index','verbatim_excerpt','media_time','media_end_time')}
            fields.update(source_sha256=engine.new['body_sha256'],anchor_state='source_version_bound',
                paragraph_text_sha256=sha256(p['text'].encode()).hexdigest(),
                verbatim_excerpt_sha256=sha256(segment['verbatim_excerpt'].encode()).hexdigest())
            if candidate['speaker_annotation']:fields.update(candidate['speaker_annotation']['proposed_fields'])
            return {**result,'status':'auto_rebind','reason':'context_time_metadata_only_match','proposed_fields':fields,
                'change_flags':[],'metadata_projection':{'kind':'speaker_prefix_only','removed_prefix':prefix},
                'text_changes':changes(quote,candidate['candidate_quote']),'spoken_characters_unchanged':True}
        # The passage is located by both unique context and time, but an exact
        # quotation boundary is not inferred from a low-overlap fragment.
        segments=[]
        for key in candidate['target_paragraphs']:
            p=engine.new['paragraphs'][key];text=p['text']
            segments.append({'paragraph_key':key,'source_segment_index':p.get('index'),'live_start':0,'live_end':len(text),
                'verbatim_excerpt':text,'media_time':p.get('start_time'),'media_end_time':p.get('end_time')})
        # Refine a single explicitly named verse to its actual source clause.
        # It supplies a locator, never replacement words from a Bible edition.
        old_refs=[slug for pos,slug in spoken_references(paragraph[:hits[0]+len(quote)]) if pos>=hits[0]]
        verse_matches=[]
        if len(set(old_refs))==1:
            for segment in segments:
                text=segment['verbatim_excerpt'];marks=spoken_references(text)
                for i,(pos,ref) in enumerate(marks):
                    if ref!=old_refs[0]:continue
                    stop=marks[i+1][0] if i+1<len(marks) else len(text)
                    sentence=re.search(r'[。！？\n]',text[pos:stop])
                    if sentence:stop=pos+sentence.end()
                    verse_matches.append({**segment,'live_start':pos,'live_end':stop,'verbatim_excerpt':text[pos:stop]})
        if len(verse_matches)==1:
            segments=verse_matches;result['calibration']['matched_explicit_verse']=old_refs[0]
        new_quote='\n'.join(s['verbatim_excerpt'] for s in segments)
        result.update(status='located_review',reason='context_time_passage_located',candidate_segments=segments,
            candidate_quote=new_quote,change_flags=sorted(set(candidate['change_flags'])|{'quotation_boundary_requires_refresh'}),
            text_changes=changes(quote,new_quote),speaker_annotation=speaker_annotation(segments,engine.new))
        if result['speaker_annotation']:
            result['speaker_annotation']={**result['speaker_annotation'],'speaker':None,
                'label':'來源範圍：'+result['speaker_annotation']['label']+'（引文邊界待確認）','proposed_fields':{}}
        result['calibration']['boundary_verified']=False
        result['calibration']['audio_position']=audio_position(fragment,segments)
        return result
