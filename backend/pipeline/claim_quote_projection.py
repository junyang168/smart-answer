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
FIELD = 'program_source_quote_projection'


def selected(materials):
    return materials.get(FIELD, {}).get('contract') or materials['policy'].get('source_quote_projection')


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

    def correct(node, field, path):
        if field == 'exact_quote' and node.get('source_epoch','current') != 'current':
            return
        sid, pk = node.get('source_id'), str(node.get('paragraph_key'))
        paragraph = sources.get(sid,{}).get('paragraphs',{}).get(pk,{})
        old = node.get(field)
        new = source_excerpt(old, paragraph.get('text',''),contract=contract)
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
