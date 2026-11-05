"""Restore literal quotations from their bound source; never edit judgments.

The original provider answer is immutable. Independent review sees the full
projected candidate and the normalization receipt records every replacement.
"""
from copy import deepcopy
import unicodedata
import re
from difflib import SequenceMatcher

CONTRACT = 'source_literal_punctuation_v1'
NEAR_CONTRACT = 'source_literal_near_match_v1'
SPAN_CONTRACT = 'source_literal_span_v2'
FIELD = 'program_source_quote_projection'


def selected(materials):
    return materials.get(FIELD, {}).get('contract') or materials['policy'].get('source_quote_projection')


def initial_candidate(materials):
    """Calibrate authorized own-source quotations before the first Opus call.

    The database is still the immutable before image. Corrections become
    ordinary proposed updates, independently reviewed and committed normally.
    Old frozen policies and returned-review recovery do not gain new writes.
    """
    from backend.pipeline import claim_management as cm
    candidate = cm.existing_candidate(materials['before'], replacements=materials['replacements'])
    original = candidate.digest
    corrected_keys = []
    request = cm.Request.model_validate(materials['unit_request'])
    if materials['policy'].get('source_quote_projection') == SPAN_CONTRACT and request.operation == 'Update':
        from backend.pipeline.claim_direct_scope import obligation_keys
        own = obligation_keys(request, materials['before'])
        grants = {p.key for p in request.permissions if 'verbatim_excerpt' in p.fields}
        dispositions = {d.record_key: d for d in candidate.dispositions}
        for key in sorted(own & grants):
            state = materials['before'].get(key)
            if not key.startswith('source_fragments/') or not state or state['retired']:
                continue
            value = state['payload']
            sid, pk = value.get('source_id'), value.get('paragraph_key')
            paragraph = materials['sources'].get(sid, {}).get('paragraphs', {}).get(str(pk), {}).get('text', '')
            old = value.get('verbatim_excerpt')
            if not isinstance(old, str):
                continue
            literal = source_excerpt(old, paragraph, contract=SPAN_CONTRACT)
            if literal == old:
                continue
            # Preserve the old value until the common normalizer restores it,
            # so the standard receipt records the exact original/replacement.
            candidate.records.append(cm.source.Row(collection='source_fragments',
                object_id=key.split('/', 1)[1], payload=deepcopy(value)))
            disposition = dispositions[key]
            disposition.action = 'update'
            disposition.reason = 'Program restored a unique literal own-source quotation before initial review; no content approval.'
            disposition.citations = [cm.Citation(source_id=sid, paragraph_key=str(pk), exact_quote=literal)]
            corrected_keys.append(key)
    candidate, receipt = cm.normalize_candidate(candidate.model_dump(mode='json'), materials['sources'],
                                                 source_quote_projection=selected(materials))
    if corrected_keys:
        receipt['initial_quote_preflight'] = {'contract': SPAN_CONTRACT,
            'original_existing_candidate_sha256': original,
            'before_sha256': cm.sha256_json(materials['before']), 'record_keys': corrected_keys}
    return candidate, receipt


def _near_excerpt(excerpt, paragraph):
    # Similarity selects a literal SOURCE span, never a Claim judgment.
    # Use sentence boundaries within the already-bound paragraph only.
    if len(excerpt) < 12:
        return excerpt
    cuts = sorted({0,len(paragraph),*[m.end() for m in re.finditer(r'[。！？!?；;\n]+', paragraph)]})
    matches = {}
    for i,left in enumerate(cuts[:-1]):
        for right in cuts[i+1:]:
            text = paragraph[left:right].strip()
            if len(text) > len(excerpt)/0.9:
                break
            if min(len(text),len(excerpt))/max(len(text),len(excerpt)) < 0.9:
                continue
            score = SequenceMatcher(None,excerpt,text,autojunk=False).ratio()
            if score >= 0.9 and paragraph.count(text) == 1:
                matches[text] = score
    ranked = sorted(matches, key=lambda text:matches[text], reverse=True)
    if not ranked or len(ranked)>1 and matches[ranked[0]]-matches[ranked[1]] < 0.02:
        return excerpt
    return ranked[0]


def _span_excerpt(excerpt, paragraph):
    """Find a unique nearby literal span, including quotes starting mid-sentence.

    Disjoint exact seeds locate short windows; banded edit alignment then
    resolves their actual boundaries. Work is bounded by quote length and a
    small edit allowance, not every pair of paragraph substring boundaries.
    """
    size = len(excerpt)
    if size < 12:
        return excerpt
    budget = max(3, min(8, size // 8))
    markers = _meaning_markers(excerpt)
    windows = set()
    for part in range(budget + 1):
        offset = part * size // (budget + 1)
        stop = min((part + 1) * size // (budget + 1), offset + 12)
        seed = excerpt[offset:stop]
        position = paragraph.find(seed)
        while position >= 0:
            start = position - offset
            if start >= -budget:
                windows.add((max(0, start - budget), min(len(paragraph), start + size + budget)))
            position = paragraph.find(seed, position + 1)
    matches = {}
    for left, right in sorted(windows):
        text = paragraph[left:right]
        previous = {j: (0, j) for j in range(min(len(text), 2 * budget) + 1)}
        for i, char in enumerate(excerpt, 1):
            current = {0: (i, 0)} if i <= budget else {}
            for j in range(max(1, i - budget), min(len(text), i + 3 * budget) + 1):
                options = [(cost + extra, start) for value, extra in (
                    (previous.get(j), 1), (current.get(j - 1), 1),
                    (previous.get(j - 1), char != text[j - 1]))
                    if value is not None for cost, start in [value]]
                if options:
                    best = min(options)
                    if best[0] <= budget:
                        current[j] = best
            previous = current
            if not previous:
                break
        else:
            for end, (distance, start) in previous.items():
                a, b = left + start, left + end
                # Keep a real source terminator when the model supplied one;
                # a spurious period on a half-sentence may simply disappear.
                if unicodedata.category(excerpt[-1]).startswith('P'):
                    while b < len(paragraph) and unicodedata.category(paragraph[b]).startswith('P'):
                        b += 1
                literal = paragraph[a:b]
                if len(literal) < 12 or _meaning_markers(literal) != markers:
                    continue
                score = SequenceMatcher(None, excerpt, literal, autojunk=False).ratio()
                # Three changed letters in a short quote can fall just below
                # 90%; require both a tight edit bound and >=85% in that case.
                if score < .9 and not (size <= 40 and distance <= 3 and score >= .85):
                    continue
                matches[a, b] = max(matches.get((a, b), 0), score)
    ranked = sorted(matches, key=lambda span: (-matches[span], abs(span[1] - span[0] - size), span))
    if not ranked:
        return excerpt
    best = ranked[0]
    literal = paragraph[best[0]:best[1]]
    if paragraph.find(literal) != best[0] or paragraph.find(literal, best[0] + 1) >= 0:
        return excerpt
    for other in ranked[1:]:
        # Nested boundary variants of this occurrence are not another quote.
        nested = (best[0] <= other[0] and other[1] <= best[1]
                  or other[0] <= best[0] and best[1] <= other[1])
        if not nested and matches[best] - matches[other] < .02:
            return excerpt
    return paragraph[best[0]:best[1]]


def _meaning_markers(text):
    """A nearby verse, negation or scope change is not a copying typo.

    This does not judge theology. It leaves recognizable substantive changes
    to the existing content repair, instead of letting similarity erase them.
    """
    numbers = re.findall(r'[-+−]?\d+(?:[.:/–—-]\d+)*', text)
    terms = re.findall(r'[A-Za-z\u0370-\u03ff\u1f00-\u1fff\u0590-\u05ff]+', text)
    words = ''.join(c for c in text if not c.isspace() and not unicodedata.category(c).startswith('P'))
    critical = [(m.group(), words[max(0, m.start()-2):m.start()], words[m.end():m.end()+2])
                for m in re.finditer(r'沒有|没有|所有|主要|唯一|必須|必须|可能|一定|應該|应该|不|非|未|無|只|仅|僅|都|全', words)]
    return numbers, terms, critical


def source_excerpt(excerpt, paragraph, *, contract=CONTRACT):
    if not isinstance(paragraph, str):
        return excerpt
    if not isinstance(excerpt, str) or not excerpt or excerpt in paragraph:
        return excerpt

    def removable(text, i):
        c = text[i]
        # Numeric signs, decimal points, ranges and verse separators carry
        # meaning. They cannot be corrected as punctuation decoration.
        if ((i and text[i-1].isdigit()) or (i+1 < len(text) and text[i+1].isdigit())):
            return False
        return unicodedata.category(c).startswith('P')

    needle = ''.join(c for i,c in enumerate(excerpt) if not removable(excerpt,i))
    indexed = [(i,c) for i,c in enumerate(paragraph) if not removable(paragraph,i)]
    haystack = ''.join(c for _,c in indexed)
    if len(needle) < 2:
        return excerpt
    start = haystack.find(needle)
    if start < 0:
        if contract == SPAN_CONTRACT:
            return _span_excerpt(excerpt, paragraph)
        return _near_excerpt(excerpt,paragraph) if contract == NEAR_CONTRACT else excerpt
    if haystack.find(needle,start+1) >= 0:
        return excerpt
    left, right = indexed[start][0], indexed[start+len(needle)-1][0]+1
    if removable(excerpt,0):
        while left and removable(paragraph,left-1): left -= 1
    if removable(excerpt,len(excerpt)-1):
        while right < len(paragraph) and removable(paragraph,right): right += 1
    exact = paragraph[left:right]
    return exact


def project(candidate, sources, *, contract=CONTRACT):
    """Only proposed fragments and typed citations; never statements or links."""
    value = candidate.model_dump(mode='json')
    value,changes = _project(value,sources,('dispositions','findings','finding_resolutions'),contract,fragments=True)
    return candidate.model_validate(value),changes


def project_review(raw, sources, *, contract=CONTRACT):
    return _project(raw,sources,('findings','prior_findings'),contract)


def _project(raw,sources,citation_fields,contract,*,fragments=False):
    value = deepcopy(raw)
    changes = []
    restored = {}

    def correct(node, field, path):
        if field == 'exact_quote' and node.get('source_epoch','current') != 'current':
            return
        sid, pk = node.get('source_id'), str(node.get('paragraph_key'))
        paragraph = sources.get(sid,{}).get('paragraphs',{}).get(pk,{})
        old = node.get(field)
        if not isinstance(old, str):
            return  # Leave malformed fields to the ordinary typed validator.
        identity = (sid, pk, old)
        if identity not in restored:
            restored[identity] = source_excerpt(old, paragraph.get('text',''),contract=contract)
        new = restored[identity]
        if new != old:
            node[field] = new
            changes.append({'path':path+[field], 'source_id':sid, 'paragraph_key':pk,
                            'before':old, 'after':new, 'rule':contract})

    writable = {d['record_key'] for d in value.get('dispositions',[]) if d['action'] in {'create','update'}}
    for i,row in enumerate(value.get('records',[]) if fragments else []):
        if row['collection'] == 'source_fragments' and row['collection']+'/'+row['object_id'] in writable:
            correct(row['payload'],'verbatim_excerpt',['records',i,'payload'])

    def citations(node, path):
        if isinstance(node,list):
            for i,child in enumerate(node): citations(child,path+[i])
        elif isinstance(node,dict):
            if 'exact_quote' in node and 'source_id' in node and 'paragraph_key' in node:
                correct(node,'exact_quote',path)
            for key,child in node.items(): citations(child,path+[key])
    # These fields contain typed Citation objects. An arbitrary record payload
    # resembling a citation is not permission to change its content.
    for field in citation_fields:
        citations(value.get(field),[field])
    return value,changes
