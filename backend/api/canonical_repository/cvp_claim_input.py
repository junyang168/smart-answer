"""Lossless Claim wording/position contract for CVP; no semantic splitting."""
from __future__ import annotations

from collections.abc import Mapping

from .cvp_recall import seal, verify

VERSION='wang_cvp_claim_input_v1'


def validate_policy(policy):
    if (set(policy)!={'schema_version','policy_id','position_fields','context_fields','mechanical_fields'} or
            policy['schema_version']!=VERSION or not isinstance(policy['policy_id'],str) or not policy['policy_id']):
        raise ValueError('explicit versioned CVP Claim input policy required')
    fields=[]
    for name in ('position_fields','context_fields','mechanical_fields'):
        values=policy[name]
        if not isinstance(values,list) or any(not isinstance(v,str) or not v for v in values):
            raise ValueError('Claim input fields must be explicit names')
        fields.extend(values)
    if len(set(fields))!=len(fields) or 'statement' not in policy['position_fields']:
        raise ValueError('Claim input fields must be disjoint and include the complete statement')


def _strings(value,path):
    if value is None:return
    if isinstance(value,str):
        if value:yield path,value
    elif isinstance(value,list):
        for i,item in enumerate(value):yield from _strings(item,[*path,i])
    elif isinstance(value,Mapping):
        for key,item in sorted(value.items()):yield from _strings(item,[*path,key])
    else:
        raise ValueError('position field is not text; bind a new input contract before execution')


def compile_input(claims,policy,*,claim_refs=None):
    """Keep all bound fields, assigning raw Unicode code-point text positions."""
    validate_policy(policy)
    if claim_refs is not None and (set(claim_refs)!={c['claim_id'] for c in claims} or
            any(not isinstance(ref,str) or not ref for ref in claim_refs.values()) or
            len(set(claim_refs.values()))!=len(claim_refs)):
        raise ValueError('exact unique program Claim references required')
    allowed=set().union(*(policy[k] for k in ('position_fields','context_fields','mechanical_fields')))
    rows=[];units=[];seen=set()
    for i,claim in enumerate(claims,1):
        cid=claim['claim_id']
        if cid in seen:raise ValueError('duplicate Claim in text input')
        seen.add(cid)
        unknown=set(claim)-allowed
        if unknown:raise ValueError('unregistered Claim fields require an input contract: '+', '.join(sorted(unknown)))
        if not isinstance(claim.get('statement'),str) or not claim['statement'].strip():
            raise ValueError('complete Claim statement required')
        ref=claim_refs[cid] if claim_refs is not None else f'c{i}'
        content={k:claim[k] for k in [*policy['position_fields'],*policy['context_fields']] if k in claim}
        rows.append({'claim_id':cid,'claim_ref':ref,'content':content})
        for field in policy['position_fields']:
            if field not in claim:continue
            for path,text in _strings(claim[field],[field]):
                units.append({'text_ref':f't{len(units)+1}','claim_ref':ref,'path':path,
                              'text':text,'start_char':0,'end_char':len(text)})
    return seal({'schema_version':VERSION,'policy':policy,'claims':rows,'text_units':units,
                 'offset_space':'raw_unicode_code_points_zero_based_half_open'})


def compile_positions(input_document,components):
    """Extract exact text ourselves and prove positional coverage, not meaning.

    This output is not an approved CVP proposal. The complete candidate still
    needs independent semantic review and the existing transaction authority.
    """
    verify(input_document,VERSION)
    units={u['text_ref']:u for u in input_document['text_units']}
    coverage={ref:[] for ref in units};result=[]
    for i,component in enumerate(components,1):
        if not isinstance(component,dict) or set(component)!={'claim_ref','spans'} or not component['spans']:
            raise ValueError('component location uses only a Claim ref and nonempty spans')
        spans=[]
        for span in component['spans']:
            if not isinstance(span,dict) or set(span)!={'text_ref','start_char','end_char'}:
                raise ValueError('model must select positions without copying text or hashes')
            ref=span['text_ref'];a=span['start_char'];b=span['end_char']
            if ref not in units or units[ref]['claim_ref']!=component['claim_ref']:
                raise ValueError('component uses a foreign text or Claim reference')
            unit=units[ref]
            if type(a) is not int or type(b) is not int or not 0<=a<b<=len(unit['text']):
                raise ValueError('component raw Unicode position is out of bounds')
            coverage[ref].append((a,b))
            spans.append({**span,'path':unit['path'],'exact_text':unit['text'][a:b]})
        result.append({'component_ref':f'k{i}','claim_ref':component['claim_ref'],'spans':spans})
    for ref,ranges in coverage.items():
        position=0
        for a,b in sorted(ranges):
            if a!=position:raise ValueError('component text has an uncovered range or unexplained overlap: '+ref)
            position=b
        if position!=len(units[ref]['text']):raise ValueError('component text has an uncovered range: '+ref)
    return seal({'schema_version':'wang_cvp_component_positions_v1',
        'input_sha256':input_document['artifact_sha256'],'components':result,
        'semantic_coverage_approved':False})
