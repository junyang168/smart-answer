"""Prepare the common, version-bound CVP material. No model calls or writes.

Target Claims carry their support; candidate members initially carry wording
and link roles. Reading member support is explicit. Source expansion changes
the shared material, never the professor's source or its citation authority.
"""
from __future__ import annotations

from copy import deepcopy
import re

from backend.api.canonical_repository.cvp_recall import RECALL_VERSION, seal, verify
from backend.api.canonical_repository.cvp_claim_input import compile_input
from backend.api.canonical_repository.knowledge_models import evidence_fragment_ids
from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.cvp_recall_preparation import VERSION as PREFLIGHT_VERSION
from backend.pipeline.extraction_sections import sections_from_structure
from backend.pipeline.viewpoint_evidence_source_context import load_context_source

VERSION='wang_cvp_common_material_v1'


def _catalog(projection):
    """Untitled text uses paragraph ranges, never an implicit whole sermon."""
    result=[]
    for section in sections_from_structure(len(projection.body_rows),projection.headings,level=2):
        if section.title:
            result.append({'start':section.start,'end':section.end,'title':section.title,'kind':'heading_section'})
        else:
            result.extend({'start':i,'end':i+1,'title':'','kind':'paragraph_range'}
                          for i in range(section.start,section.end))
    return [{**s,'section_ref':f'p{i}'} for i,s in enumerate(result,1)]


def _source_packet(ref,doc,loaded,fragment_rows):
    _,_,projection=loaded
    if doc.get('locator_space')!='spoken_body_v1':
        raise ValueError('common source material requires explicit body coordinates')
    catalog=_catalog(projection);selected=set()
    for fragment in fragment_rows:
        key=str(fragment.get('paragraph_key') or '')
        if not re.fullmatch(r'S[0-9]{4,}',key):raise ValueError('source fragment has no stable paragraph locator')
        i=int(key[1:])-1
        if not 0<=i<len(projection.body_rows):raise ValueError('source fragment paragraph is out of bounds')
        quote=fragment.get('verbatim_excerpt')
        if fragment.get('source_modality')!='visual' and (not quote or quote not in projection.spoken_rows[i].get('text','')):
            raise ValueError('source fragment excerpt differs from its physical paragraph')
        selected.update(s['section_ref'] for s in catalog if s['start']<=i<s['end'])
    def part(section):
        return {**section,'paragraphs':[{'paragraph_key':f'S{i+1:04d}','row':projection.body_rows[i]}
                                       for i in range(section['start'],section['end'])]}
    return {'source_ref':ref,'source_body_sha256':projection.body_sha256,
        'editorial_structure_sha256':projection.editorial_structure_sha256,
        'headings_are_metadata':True,'expansion_level':0,
        'sections':[part(s) for s in catalog if s['section_ref'] in selected]}


def prepare_materials(store,preflight,group_key,recall,*,member_support_ids=(),
                      maintenance_viewpoint_ids=(),source_loader=load_context_source):
    """Prepare exact read dependencies, not authorization to change any object."""
    verify(preflight,PREFLIGHT_VERSION);verify(recall,RECALL_VERSION)
    if preflight.get('status')!='prepared':raise ValueError('recall preflight is not prepared')
    groups={g['group_key']:g['claim_ids'] for g in preflight['groups']}
    if group_key not in groups:raise ValueError('unknown accepted Claim group')
    ids=groups[group_key]
    plans={p['group_key']:p['plan'] for p in preflight['recall_plans']}
    if recall.get('plan_sha256')!=plans[group_key]['artifact_sha256']:
        raise ValueError('recall result belongs to a different bound group plan')
    rows=recall['claim_candidates']
    if [r['claim_id'] for r in rows]!=ids or recall.get('identity_decided') is not False:
        raise ValueError('recall Claim denominator or identity boundary differs')
    vids=sorted({vid for r in rows for vid in r['viewpoint_ids']})
    heads={h['viewpoint_id']:h for h in preflight['heads']}
    if not set(vids)<=set(heads):raise ValueError('recall includes a foreign CVP')
    if not set(maintenance_viewpoint_ids)<=set(vids):raise ValueError('maintenance context is outside candidate scope')
    records={}
    def read(keys):
        wanted=set(keys)-records.keys()
        got=store.read_current_records(wanted)
        if set(got)!=wanted:raise ValueError('common material is missing a current dependency')
        for key,row in got.items():
            if row.get('retired') or record_content_sha(row['payload'])!=row['content_sha256']:
                raise ValueError('common material contains a retired or inconsistent record')
        records.update(got)
    read([('claims',cid) for cid in ids]+[('canonical_viewpoints',vid) for vid in vids]+
         [('viewpoint_revisions',heads[vid]['viewpoint_revision_id']) for vid in vids])
    authority={r['claim_id']:r['qualification'] for r in preflight['claim_authority']}
    for cid in ids:
        row=records['claims',cid];q=authority[cid]
        if (row['revision'],row['content_sha256'])!=(q['revision'],q['content_sha256']):
            raise ValueError('superseded_input: Claim changed after recall')
        if store.claim_management_qualification(cid)!=q:
            raise ValueError('superseded_input: Claim qualification/dependencies changed after recall')
    for vid in vids:
        view=records['canonical_viewpoints',vid];revision=records['viewpoint_revisions',heads[vid]['viewpoint_revision_id']]
        if (view['payload'].get('identity_status')!='active' or
                view['payload'].get('current_revision_id')!=heads[vid]['viewpoint_revision_id'] or
                revision['payload'].get('viewpoint_id')!=vid or
                sha256_json({'viewpoint':[view['revision'],view['content_sha256']],
                    'revision':[revision['revision'],revision['content_sha256']]})!=heads[vid]['head_sha256']):
            raise ValueError('superseded_input: CVP head changed after recall')
    links=store.read_cvp_links(viewpoint_ids=vids,claim_ids=ids)
    active={k:r for k,r in links.items() if r['payload'].get('effective_state')=='active'}
    candidates={r['claim_id']:set(r['viewpoint_ids']) for r in rows}
    for row in active.values():
        p=row['payload']
        if p['claim_id'] in candidates and p['viewpoint_id'] not in candidates[p['claim_id']]:
            raise ValueError('recall omitted an existing active connection')
    active={k:r for k,r in active.items() if r['payload']['viewpoint_id'] in vids}
    records.update(active)
    members=sorted({r['payload']['claim_id'] for r in active.values()})
    read(('claims',cid) for cid in members)
    if not set(member_support_ids)<=set(members):raise ValueError('support request is not a current candidate member')
    support_ids=set(ids)|set(member_support_ids)|{r['payload']['claim_id'] for r in active.values()
        if r['payload']['viewpoint_id'] in maintenance_viewpoint_ids}
    evidence_ids={eid for cid in support_ids for eid in records['claims',cid]['payload'].get('evidence_step_ids',[])}
    read(('evidence_steps',eid) for eid in evidence_ids)
    fragment_ids={fid for eid in evidence_ids for fid in evidence_fragment_ids(records['evidence_steps',eid]['payload'])}
    read(('source_fragments',fid) for fid in fragment_ids)
    source_ids=sorted({records['source_fragments',fid]['payload']['source_id'] for fid in fragment_ids})
    read(('source_documents',sid) for sid in source_ids)
    citation_ids={cid for eid in evidence_ids for cid in records['evidence_steps',eid]['payload'].get('citation_ids',[])}
    citation_ids.update(records['source_fragments',fid]['payload'].get('citation_id') for fid in fragment_ids)
    read(('citations',cid) for cid in citation_ids if cid)
    # Short refs are local transport, not canonical identities or approval.
    refs={col:{oid:f'{prefix}{i}' for i,oid in enumerate(sorted(oid for c,oid in records if c==col),1)}
          for col,prefix in [('claims','c'),('canonical_viewpoints','v'),('viewpoint_revisions','r'),
              ('viewpoint_claim_links','l'),('evidence_steps','e'),('source_fragments','f'),
              ('source_documents','s'),('citations','b')]}
    sources=[];physical=[]
    for sid in source_ids:
        doc=records['source_documents',sid]['payload'];loaded=source_loader(doc)
        own=[records['source_fragments',fid]['payload'] for fid in fragment_ids
             if records['source_fragments',fid]['payload']['source_id']==sid]
        sources.append(_source_packet(refs['source_documents'][sid],doc,loaded,own))
        physical.append({'source_id':sid,'path':str(loaded[0]),'file_sha256':loaded[1]})
    # Preserve the complete evolving semantic payload. Mechanical/provenance
    # fields stay in bound records, not discarded or model-reproduced output.
    wording=compile_input([records['claims',cid]['payload'] for cid in sorted(set(ids)|set(members))],
                          preflight['claim_input_policy'])
    target_input=compile_input([records['claims',cid]['payload'] for cid in ids],
        preflight['claim_input_policy'],claim_refs={cid:refs['claims'][cid] for cid in ids})
    content={row['claim_id']:row['content'] for row in wording['claims']}
    claim_text=[{'claim_ref':refs['claims'][cid],'target':cid in ids,
        'support_loaded':cid in support_ids,
        'content':content[cid]}
        for cid in sorted(set(ids)|set(members))]
    material={'claims':claim_text,'viewpoints':[
        {'cvp_ref':refs['canonical_viewpoints'][vid],'head':records['viewpoint_revisions',heads[vid]['viewpoint_revision_id']]['payload']}
        for vid in vids], 'links':[{'link_ref':refs['viewpoint_claim_links'][oid],
        'claim_ref':refs['claims'][r['payload']['claim_id']],
        'cvp_ref':refs['canonical_viewpoints'][r['payload']['viewpoint_id']],
        'content':r['payload']} for (_,oid),r in sorted(active.items())],
        'evidence':[{'evidence_ref':refs['evidence_steps'][eid],'content':records['evidence_steps',eid]['payload']}
                    for eid in sorted(evidence_ids)],
        'fragments':[{'fragment_ref':refs['source_fragments'][fid],'content':records['source_fragments',fid]['payload']}
                     for fid in sorted(fragment_ids)],
        'citations':[{'citation_ref':refs['citations'][cid],'content':records['citations',cid]['payload']}
                     for cid in sorted(citation_ids-{None,''})], 'sources':sources}
    pins={(col,oid):(r['revision'],r['content_sha256'],False) for (col,oid),r in records.items()}
    store.verify_current_read_pins(pins)
    return seal({'schema_version':VERSION,'preflight_sha256':preflight['artifact_sha256'],
        'recall_sha256':recall['artifact_sha256'],'group_key':group_key,'material':material,
        'references':refs,'records':[{'collection':col,'object_id':oid,**row} for (col,oid),row in sorted(records.items())],
        'sources':physical,'claim_input':target_input,'member_support_ids':sorted(support_ids-set(ids)),
        'maintenance_viewpoint_ids':sorted(maintenance_viewpoint_ids)})


def expand_source(bundle,request,*,source_loader=load_context_source):
    """One adjacent expansion then one full-body expansion per source."""
    verify(bundle,VERSION)
    if (set(request)!={'source_ref','section_ref','extent','reason'} or
            not isinstance(request['reason'],str) or not request['reason'].strip()):
        raise ValueError('context request must identify a source/location and concrete reason')
    result=deepcopy(bundle);materials=result['material']['sources']
    selected=[s for s in materials if s['source_ref']==request['source_ref']]
    if len(selected)!=1:raise ValueError('context request has a foreign source')
    source=selected[0];level=source['expansion_level']
    expected='adjacent' if level==0 else 'full' if level==1 else None
    if expected is None or request['extent']!=expected:
        raise ValueError('source expansion is repeated or skips its bounded gradient')
    if request['section_ref'] not in {s['section_ref'] for s in source['sections']}:
        raise ValueError('context request must identify a currently supplied location')
    sid=next(sid for sid,ref in bundle['references']['source_documents'].items() if ref==source['source_ref'])
    doc=next(r['payload'] for r in bundle['records'] if r['collection']=='source_documents' and r['object_id']==sid)
    loaded=source_loader(doc);projection=loaded[2]
    old=next(s for s in bundle['sources'] if s['source_id']==sid)
    if (str(loaded[0]),loaded[1],projection.body_sha256,projection.editorial_structure_sha256)!=(
            old['path'],old['file_sha256'],source['source_body_sha256'],source['editorial_structure_sha256']):
        raise ValueError('physical or editorial source changed during expansion')
    catalog=_catalog(projection);current={s['section_ref'] for s in source['sections']}
    anchor=next(i for i,s in enumerate(catalog) if s['section_ref']==request['section_ref'])
    chosen=current|{catalog[i]['section_ref'] for i in range(max(0,anchor-1),min(len(catalog),anchor+2))}
    if expected=='full':chosen={s['section_ref'] for s in catalog}
    source['sections']=[{**s,'paragraphs':[{'paragraph_key':f'S{i+1:04d}','row':projection.body_rows[i]}
                                         for i in range(s['start'],s['end'])]}
                        for s in catalog if s['section_ref'] in chosen]
    source['expansion_level']+=1
    result.setdefault('context_requests',[]).append(request)
    result['previous_material_sha256']=bundle['artifact_sha256']
    return seal({k:v for k,v in result.items() if k!='artifact_sha256'})
