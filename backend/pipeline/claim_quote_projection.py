"""Copy uniquely identified source punctuation; preserve every teaching word.

The original provider answer is immutable. Independent review sees the full
projected candidate and the normalization receipt records every replacement.
"""
from copy import deepcopy
import unicodedata

CONTRACT = 'source_literal_punctuation_v1'
FIELD = 'program_source_quote_projection'


def source_excerpt(excerpt, paragraph):
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
    if start < 0 or haystack.find(needle,start+1) >= 0:
        return excerpt
    left, right = indexed[start][0], indexed[start+len(needle)-1][0]+1
    if removable(excerpt,0):
        while left and removable(paragraph,left-1): left -= 1
    if removable(excerpt,len(excerpt)-1):
        while right < len(paragraph) and removable(paragraph,right): right += 1
    exact = paragraph[left:right]
    return exact


def project(candidate, sources):
    """Only proposed fragments and typed citations; never statements or links."""
    raw = candidate.model_dump(mode='json')
    value = deepcopy(raw)
    changes = []

    def correct(node, field, path):
        if field == 'exact_quote' and node.get('source_epoch','current') != 'current':
            return
        sid, pk = node.get('source_id'), str(node.get('paragraph_key'))
        paragraph = sources.get(sid,{}).get('paragraphs',{}).get(pk,{})
        old = node.get(field)
        new = source_excerpt(old, paragraph.get('text',''))
        if new != old:
            node[field] = new
            changes.append({'path':path+[field], 'source_id':sid, 'paragraph_key':pk,
                            'before':old, 'after':new, 'rule':CONTRACT})

    writable = {d['record_key'] for d in value['dispositions'] if d['action'] in {'create','update'}}
    for i,row in enumerate(value['records']):
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
    for field in ('dispositions','findings','finding_resolutions'):
        citations(value.get(field),[field])
    return candidate.model_validate(value), changes
