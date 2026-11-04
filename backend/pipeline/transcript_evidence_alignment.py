"""Source-ordered relocation candidates, never semantic approval or a writer."""
from bisect import bisect_left
import difflib
import re

from backend.pipeline.transcript_evidence_migration import literal_layout, positions

RULES = {'minimum_unique_anchor_characters':12,'maximum_anchor_distance':80,
         'minimum_matched_fraction':0.65,'maximum_length_ratio':1.8,'maximum_paragraphs':4}
SPEAKER = re.compile(r'(教授|聽眾|听众|學生|学生|講者|讲者|speaker\s*\d*)\s*[:：]',re.I)


def speaker_at(text,offset):
    labels=list(SPEAKER.finditer(text,0,offset))
    return labels[-1].group(1) if labels else None


def speaker_annotation(segments,new):
    roles=set();unknown=False
    for segment in segments:
        text=new['paragraphs'][segment['paragraph_key']]['text']
        start=segment.get('live_start');end=segment.get('live_end')
        if start is None or end is None:continue
        labels=[speaker_at(text,start),*[m.group(1) for m in SPEAKER.finditer(text,start,end)]]
        if not any(labels):unknown=True
        for label in labels:
            if label in {'聽眾','听众','學生','学生'}:roles.add('audience')
            elif label in {'教授','講者','讲者'}:roles.add('professor')
    role=next(iter(roles)) if len(roles)==1 else 'mixed' if roles else None
    return ({'speaker':role if not unknown else None,
        'label':('部分引文：' if unknown else '')+{'audience':'聽眾','professor':'教授','mixed':'多人對話'}[role],
        'basis':'explicit_source_labels','proposed_fields':{} if unknown else {'speaker':role}} if role else None)


def changes(before,after):
    return [{'operation':tag,'old':before[a:b],'new':after[c:d]}
            for tag,a,b,c,d in difflib.SequenceMatcher(None,before,after,autojunk=False).get_opcodes()
            if tag!='equal']


class SourceAlignment:
    def __init__(self,old,new,expanded,rows):
        self.old=old;self.new=new;self.expanded=expanded;self.rows=rows
        self.starts={};parts=[];offset=0
        for key,row in old['paragraphs'].items():
            text=str(row.get('text') or '');self.starts[key]=offset;parts.append(text);offset+=len(text)+1
        self.left,self.left_offsets=literal_layout('\n'.join(parts))
        self.right,self.right_offsets=literal_layout(expanded)
        blocks=difflib.SequenceMatcher(None,self.left,self.right,autojunk=False).get_matching_blocks()
        self.char_map={i:block.b+i-block.a for block in blocks for i in range(block.a,block.a+block.size)}
        self.matched_indexes=sorted(self.char_map)
        self.anchors=[]
        for block in blocks:
            if block.size<RULES['minimum_unique_anchor_characters']:continue
            text=self.left[block.a:block.a+block.size]
            if len(positions(self.left,text))==len(positions(self.right,text))==1:
                self.anchors.append({'old_start':block.a,'old_end':block.a+block.size,
                    'new_start':block.b,'new_end':block.b+block.size,'literal':text})
        self.deleted=set()
        for row in rows:
            if row['simple']:self.deleted.update(row['start']+i for i,flag in enumerate(row['deleted']) if flag)

    def candidate(self,fragment):
        quote=fragment.get('verbatim_excerpt') or '';key=str(fragment.get('paragraph_key'))
        if (not quote or fragment.get('source_modality')=='visual' or
                fragment.get('source_sha256')!=self.old['body_sha256']):return None
        paragraph=self.old['paragraphs'].get(key,{}).get('text','');hits=positions(paragraph,quote)
        if len(hits)!=1:return None
        start=self.starts[key]+hits[0];end=start+len(quote)
        a=bisect_left(self.left_offsets,start);b=bisect_left(self.left_offsets,end)
        if a==b:return None
        matched=[i for i in range(a,b) if i in self.char_map]
        if len(matched)<2:return None
        exact_start=a in self.char_map;exact_end=b-1 in self.char_map
        # A changed boundary can be bracketed, but never becomes an automatic
        # rebinding: the proposed excerpt may include an insertion at that edge.
        before=bisect_left(self.matched_indexes,a)-1
        after=bisect_left(self.matched_indexes,b)
        ns=self.char_map[a] if exact_start else (self.char_map[self.matched_indexes[before]]+1 if before>=0 else None)
        ne=self.char_map[b-1]+1 if exact_end else (self.char_map[self.matched_indexes[after]] if after<len(self.matched_indexes) else None)
        if ns is None or ne is None or not 0<=ns<ne<=len(self.right_offsets):return None
        lo,hi=self.right_offsets[ns],self.right_offsets[ne-1]+1
        span=[row for row in self.rows if row['start']<hi and row['end']>lo]
        if not span or any(not row['simple'] for row in span):return None
        left=[x for x in self.anchors if x['old_start']<=a and x['old_end']>=a-RULES['maximum_anchor_distance']]
        right=[x for x in self.anchors if x['old_end']>=b and x['old_start']<=b+RULES['maximum_anchor_distance']]
        left_anchor=max(left,key=lambda x:x['old_end']) if left else None
        right_anchor=min(right,key=lambda x:x['old_start']) if right else None
        coverage=len(matched)/(b-a);length_ratio=(ne-ns)/(b-a)
        anchored=bool(left_anchor and right_anchor)
        localized=(anchored and coverage>=RULES['minimum_matched_fraction']
                   and 1/RULES['maximum_length_ratio']<=length_ratio<=RULES['maximum_length_ratio']
                   and len(span)<=RULES['maximum_paragraphs'])
        segments=[]
        for row in span:
            local_start=max(lo,row['start'])-row['start'];local_end=min(hi,row['end'])-row['start']
            live=[i for i in row['live_positions'][local_start:local_end] if i is not None]
            target=self.new['paragraphs'][row['key']];text=target.get('text','')
            live_start=min(live) if live else None;live_end=max(live)+1 if live else None
            segments.append({'paragraph_key':row['key'],'source_segment_index':target.get('index'),
                'expanded_start':local_start,'expanded_end':local_end,
                'expanded_excerpt':row['text'][local_start:local_end],
                'live_start':live_start,'live_end':live_end,
                'verbatim_excerpt':text[live_start:live_end] if live else '',
                'media_time':target.get('start_time'),'media_end_time':target.get('end_time')})
        candidate='\n'.join(s['verbatim_excerpt'] for s in segments)
        # Only characters from the OLD bound quotation that match the explicit
        # deletion count. Newly inserted struck-through words alone do not.
        deleted=[i for i in matched if self.right_offsets[self.char_map[i]] in self.deleted]
        annotation=speaker_annotation(segments,self.new)
        flags=[]
        if literal_layout(quote)[0]!=literal_layout(candidate)[0]:flags.append('quote_text_changed')
        if len(span)>1:flags.append('cross_paragraph')
        if not (exact_start and exact_end):flags.append('changed_quote_boundary')
        if not anchored:flags.append('missing_unique_bracketing_context')
        if coverage<RULES['minimum_matched_fraction']:flags.append('insufficient_literal_overlap')
        if not 1/RULES['maximum_length_ratio']<=length_ratio<=RULES['maximum_length_ratio']:flags.append('span_length_changed_substantially')
        if len(span)>RULES['maximum_paragraphs']:flags.append('span_too_many_paragraphs')
        if any(re.search(r'[不未沒无無非\d\u0370-\u03ff\u0590-\u05ff]',part['old']+part['new'])
               for part in changes(literal_layout(quote)[0],literal_layout(candidate)[0])):flags.append('negation_number_or_original_language_changed')
        return {'localized':localized,'candidate_quote':candidate,'candidate_segments':segments,
            'target_paragraphs':[s['paragraph_key'] for s in segments],
            'target_paragraph':segments[0]['paragraph_key'] if len(segments)==1 else None,
            'alignment':{'method':'whole_source_monotone_literal_alignment_v1','matched_fraction':round(coverage,6),
                'length_ratio':round(length_ratio,6),'both_endpoints_matched':exact_start and exact_end,
                'left_anchor':left_anchor,'right_anchor':right_anchor,'rules':RULES},
            'change_flags':flags,'text_changes':changes(quote,candidate),
            'speaker_annotation':annotation,
            'old_quote_range':{'start':start,'end':end},
            'deleted_character_bindings':[{'old_offset':self.left_offsets[i],
                'new_offset':self.right_offsets[self.char_map[i]],'character':self.left[i]} for i in deleted],
            'matched_deleted_character_count':len(deleted),
            'deletion_extent':'entire_quote' if len(deleted)==b-a else 'partial_quote' if deleted else None,
            'content_equivalence_proven':False,'content_approval_granted':False,
            'timestamp_precision':'paragraph_not_word'}

    def refine(self,fragment,original):
        if original['status']!='exception':return original
        candidate=self.candidate(fragment)
        if candidate is None:return original
        result={**original,**candidate,'previous_reason':original['reason']}
        if not candidate['localized']:
            return {**result,'reason':'alignment_candidate_not_verified'}
        if candidate['matched_deleted_character_count']:
            return {**result,'status':'source_deleted','reason':'aligned_bound_occurrence_soft_deleted',
                'preserve_historical_quote':True,'proposed_fields':{'anchor_state':'source_deleted','source_binding_status':'source_deleted'}}
        if candidate['change_flags']:
            return {**result,'status':'located_review','reason':'source_located_requires_refresh'}
        # An unchanged lexical sequence, exact boundaries and unique local
        # context can yield a mechanical proposal; no Claim qualification.
        from hashlib import sha256
        segment=candidate['candidate_segments'][0];paragraph=self.new['paragraphs'][segment['paragraph_key']]
        fields={k:segment[k] for k in ('paragraph_key','source_segment_index','verbatim_excerpt','media_time','media_end_time')}
        fields.update(source_sha256=self.new['body_sha256'],anchor_state='source_version_bound',
            paragraph_text_sha256=sha256(paragraph['text'].encode()).hexdigest(),
            verbatim_excerpt_sha256=sha256(segment['verbatim_excerpt'].encode()).hexdigest())
        if candidate['speaker_annotation']:fields.update(candidate['speaker_annotation']['proposed_fields'])
        return {**result,'status':'auto_rebind','reason':'sequence_verified_unchanged_quote',
            'proposed_fields':fields,'lexical_characters_unchanged':True,
            'quote_unchanged':fragment['verbatim_excerpt']==segment['verbatim_excerpt']}



def verify_preview(report,old,new,document):
    """Verify saved excerpts/anchors without rerunning sequence alignment.

    This checks literal locations, not theological or semantic equivalence.
    It deliberately grants no Claim qualification and performs no I/O.
    """
    from backend.pipeline.transcript_evidence_migration import expanded_source
    expanded,rows=expanded_source(document)
    old_text='\n'.join(str(p.get('text') or '') for p in old['paragraphs'].values())
    left,_=literal_layout(old_text);right,_=literal_layout(expanded)
    deleted={row['start']+i for row in rows if row['simple'] for i,flag in enumerate(row['deleted']) if flag}
    counts={'automatic_quotes':0,'candidate_segments':0,'unique_anchor_bindings':0}
    for row in report['fragments']:
        for item in row.get('deleted_character_bindings',[]):
            a=item['old_offset'];b=item['new_offset'];ch=item['character']
            if (b not in deleted or
                not (old_text[a]==expanded[b]==ch or ch==' ' and old_text[a].isspace() and expanded[b].isspace())):
                raise ValueError('deleted character is not bound to old literal and explicit new strikethrough')
            bounds=row['old_quote_range']
            if not bounds['start']<=a<bounds['end']:raise ValueError('deleted character is outside the old bound quote')
        for segment in row.get('candidate_segments',[]):
            p=new['paragraphs'][segment['paragraph_key']];start=segment['live_start'];end=segment['live_end']
            quote=p['text'][start:end] if start is not None and end is not None else ''
            if quote!=segment['verbatim_excerpt']:raise ValueError('candidate is not the saved literal source slice')
            if segment['media_time']!=p.get('start_time') or segment['media_end_time']!=p.get('end_time'):
                raise ValueError('candidate clock is not the bound paragraph clock')
            counts['candidate_segments']+=1
        for field in ('left_anchor','right_anchor'):
            anchor=row.get('alignment',{}).get(field)
            if not anchor:continue
            literal=anchor['literal']
            if (left[anchor['old_start']:anchor['old_end']]!=literal or
                    right[anchor['new_start']:anchor['new_end']]!=literal or
                    len(positions(left,literal))!=1 or len(positions(right,literal))!=1):
                raise ValueError('alignment anchor is not unique literal old/new context')
            counts['unique_anchor_bindings']+=1
        if row['status']=='auto_rebind':
            fields=row['proposed_fields'];paragraph=new['paragraphs'][fields['paragraph_key']]
            if (not fields['verbatim_excerpt'] or fields['verbatim_excerpt'] not in paragraph['text'] or
                    literal_layout(fields['verbatim_excerpt'])[0]!=literal_layout(row['quote'])[0] or
                    fields['source_sha256']!=new['body_sha256'] or row.get('change_flags')):
                raise ValueError('automatic quote changed lexical content or has unresolved flags')
            if fields['media_time']!=paragraph.get('start_time') or fields['media_end_time']!=paragraph.get('end_time'):
                raise ValueError('automatic quote has incorrect paragraph clock')
            counts['automatic_quotes']+=1
        if row['status']=='located_review' and 'proposed_fields' in row:
            raise ValueError('changed quotation cannot be an automatic write proposal')
        if row['status']=='source_deleted' and row['proposed_fields'].get('anchor_state')!='source_deleted':
            raise ValueError('deleted source must not acquire an active binding')
    return {'literal_checks':counts,'content_equivalence_proven':False,'qualification_granted':False,
            'model_calls':0,'production_writes':0}
