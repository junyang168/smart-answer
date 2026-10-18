"""Prepare the common, version-bound CVP material. No model calls or writes.

Target Claims carry their support; candidates can carry reviewed summaries.
The wire projection hides summarized members until their support is requested. Source expansion changes
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


def _short_ref(collection, object_id, prefix):
    # Program refs remain c/v/... plus digits, while identities are independent
    # of the other records loaded into this exact material generation.
    return prefix + str(int(sha256_json([collection,object_id])[:12],16)+1)


def _protection(payload):
    flags={key:payload[key] for key in ('human_hold','human_retained','human_preserve','protected')
           if payload.get(key) is True}
    held=payload.get('review_status') in {'human_hold','human_review_required','rejected'} or bool(flags)
    return {'read_only_protection':held,'review_status':payload.get('review_status'),
            'flags':flags,**{key:payload[key] for key in ('review_note','reviewed_by','reviewed_at') if key in payload}}


def prepare_materials(store,preflight,group_key,recall,*,member_support_ids=(),
                      maintenance_viewpoint_ids=(),maintenance_record_keys=(),summary_root=None,source_loader=load_context_source):
    """Prepare exact read dependencies, not authorization to change any object.

    Maintenance owns complete active members of explicitly named viewpoints.
    Relationships have one-hop endpoint context; that does not grant authority
    over those endpoints or recursively load their other members/relationships.
    """
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
    maintenance=set(maintenance_viewpoint_ids)
    candidate_vids={vid for row in rows for vid in row['viewpoint_ids']}
    primary_vids=sorted(candidate_vids|maintenance)
    heads={h['viewpoint_id']:h for h in preflight['heads']}
    if not set(primary_vids)<=set(heads):raise ValueError('recall or maintenance includes a foreign CVP')
    explicit={tuple(key) for key in maintenance_record_keys}
    if explicit and not maintenance:raise ValueError('explicit maintenance objects need a viewpoint scope')
    if any(len(key)!=2 or key[0] not in {'canonical_viewpoints','viewpoint_revisions','viewpoint_claim_links','viewpoint_relations'} for key in explicit):
        raise ValueError('maintenance object has a forbidden collection')
    records={}
    def accept(got):
        for key,row in got.items():
            if row.get('retired') or record_content_sha(row['payload'])!=row['content_sha256']:
                raise ValueError('common material contains a retired or inconsistent record')
            if key in records and records[key]!=row:
                raise ValueError('superseded_input: common dependency changed while reading')
        records.update(got)
    def read(keys):
        wanted=set(keys)-records.keys()
        if not wanted:return
        got=store.read_current_records(wanted)
        if set(got)!=wanted:raise ValueError('common material is missing a current dependency')
        accept(got)
    read([('claims',cid) for cid in ids]+[('canonical_viewpoints',vid) for vid in primary_vids]+
         [('viewpoint_revisions',heads[vid]['viewpoint_revision_id']) for vid in primary_vids])
    authority={r['claim_id']:r['qualification'] for r in preflight['claim_authority']}
    for cid in ids:
        row=records['claims',cid];q=authority[cid]
        if (row['revision'],row['content_sha256'])!=(q['revision'],q['content_sha256']):
            raise ValueError('superseded_input: Claim changed after recall')
        if store.claim_management_qualification(cid)!=q:
            raise ValueError('superseded_input: Claim qualification/dependencies changed after recall')
    for vid in primary_vids:
        view=records['canonical_viewpoints',vid];revision=records['viewpoint_revisions',heads[vid]['viewpoint_revision_id']]
        if (view['payload'].get('identity_status')!='active' or
                view['payload'].get('current_revision_id')!=heads[vid]['viewpoint_revision_id'] or
                revision['payload'].get('viewpoint_id')!=vid or
                sha256_json({'viewpoint':[view['revision'],view['content_sha256']],
                    'revision':[revision['revision'],revision['content_sha256']]})!=heads[vid]['head_sha256']):
            raise ValueError('superseded_input: CVP head changed after recall')
    links=store.read_cvp_links(viewpoint_ids=primary_vids,claim_ids=ids)
    active={k:r for k,r in links.items() if r['payload'].get('effective_state')=='active'}
    candidates={r['claim_id']:set(r['viewpoint_ids']) for r in rows}
    for row in active.values():
        p=row['payload']
        if p['claim_id'] in candidates and p['viewpoint_id'] not in candidates[p['claim_id']]:
            raise ValueError('recall omitted an existing active connection')
    active={k:r for k,r in active.items() if r['payload']['viewpoint_id'] in primary_vids}
    accept(active)
    read(explicit)
    for col,oid in explicit:
        payload=records[col,oid]['payload']
        scope=({oid} if col=='canonical_viewpoints' else {payload['viewpoint_id']}
               if col in {'viewpoint_revisions','viewpoint_claim_links'} else
               {payload['source_viewpoint_id'],payload['target_viewpoint_id']})
        # A relation touches both endpoints, but repinning the maintained
        # endpoint does not authorize or require maintenance of its neighbour.
        in_scope=bool(scope & maintenance) if col=='viewpoint_relations' else scope<=maintenance
        if not in_scope:raise ValueError('explicit maintenance object is outside viewpoint authority')
    if maintenance:
        relation_rows=store.read_cvp_relations(viewpoint_ids=sorted(maintenance))
        accept({k:r for k,r in relation_rows.items() if r['payload'].get('effective_state')=='active'})
    relationship_rows={k:r for k,r in records.items() if k[0]=='viewpoint_relations'}
    context_vids={row['payload'][end+'_viewpoint_id'] for row in relationship_rows.values() for end in ('source','target')}-set(primary_vids)
    read(('canonical_viewpoints',vid) for vid in context_vids)
    read(('viewpoint_revisions',records['canonical_viewpoints',vid]['payload']['current_revision_id']) for vid in context_vids)
    for vid in context_vids:
        view=records['canonical_viewpoints',vid]['payload']
        if records['viewpoint_revisions',view['current_revision_id']]['payload'].get('viewpoint_id')!=vid:
            raise ValueError('relationship endpoint has a foreign current revision')
    vids=sorted(set(primary_vids)|context_vids)
    supplied_links={k:r for k,r in records.items() if k[0]=='viewpoint_claim_links'}
    members={r['payload']['claim_id'] for r in supplied_links.values()}
    if not set(member_support_ids)<=members:raise ValueError('support request is not a current candidate member')
    relation_claims={cid for row in relationship_rows.values() for cid in row['payload'].get('supporting_claim_ids',[])}
    relation_ids={rid for row in relationship_rows.values() for rid in row['payload'].get('supporting_claim_relation_ids',[])}
    relation_ids.update(rid for row in supplied_links.values() for rid in row['payload'].get('supporting_relation_ids',[]))
    read(('claim_relations',rid) for rid in relation_ids)
    for rid in relation_ids:
        relation=records['claim_relations',rid]['payload']
        relation_claims.update(relation[key] for key in ('from_id','to_id') if relation.get(key))
    all_claims=sorted(set(ids)|members|relation_claims)
    read(('claims',cid) for cid in all_claims)
    support_ids=set(ids)|set(member_support_ids)|relation_claims|{r['payload']['claim_id'] for r in supplied_links.values()
        if r['payload']['viewpoint_id'] in maintenance}
    evidence_ids={eid for cid in support_ids for eid in records['claims',cid]['payload'].get('evidence_step_ids',[])}
    read(('evidence_steps',eid) for eid in evidence_ids)
    fragment_ids={fid for eid in evidence_ids for fid in evidence_fragment_ids(records['evidence_steps',eid]['payload'])}
    read(('source_fragments',fid) for fid in fragment_ids)
    source_ids=sorted({records['source_fragments',fid]['payload']['source_id'] for fid in fragment_ids})
    read(('source_documents',sid) for sid in source_ids)
    citation_ids={cid for eid in evidence_ids for cid in records['evidence_steps',eid]['payload'].get('citation_ids',[])}
    citation_ids.update(records['source_fragments',fid]['payload'].get('citation_id') for fid in fragment_ids)
    read(('citations',cid) for cid in citation_ids if cid)
    decisions={r['payload']['decision_id'] for r in supplied_links.values() if r['payload'].get('decision_id')}
    decisions.update(did for (col,_),row in records.items() if col in {'viewpoint_revisions','viewpoint_relations'}
                     for provenance in [row['payload'].get('provenance') or {},row['payload'].get('review_provenance') or {}]
                     for did in provenance.get('basis_identity_decision_ids',[]))
    read(('viewpoint_identity_decisions',did) for did in decisions)
    prefixes=[('claims','c'),('canonical_viewpoints','v'),('viewpoint_revisions','r'),('viewpoint_claim_links','l'),
        ('viewpoint_relations','q'),('viewpoint_identity_decisions','d'),('claim_relations','a'),
        ('evidence_steps','e'),('source_fragments','f'),('source_documents','s'),('citations','b')]
    refs={col:{oid:_short_ref(col,oid,prefix) for c,oid in sorted(records) if c==col} for col,prefix in prefixes}
    all_refs=[ref for values in refs.values() for ref in values.values()]
    if len(all_refs)!=len(set(all_refs)):raise ValueError('program short reference hash collision')
    sources=[];physical=[]
    from backend.pipeline import claim_human_source
    human_source_decisions=store.read_human_source_decisions(source_ids)
    for sid in source_ids:
        doc=records['source_documents',sid]['payload'];loaded=source_loader(doc)
        own=[records['source_fragments',fid]['payload'] for fid in fragment_ids if records['source_fragments',fid]['payload']['source_id']==sid]
        packet=_source_packet(refs['source_documents'][sid],doc,loaded,own)
        # Applicability is checked against complete spoken paragraphs, never
        # a quoted fragment or the currently selected section window.
        snapshot={sid:{'file':{'file_sha256':loaded[1]},
            'paragraphs':{f'S{i:04d}':row for i,row in enumerate(loaded[2].spoken_rows,1)}}}
        claim_human_source.project(snapshot,human_source_decisions)
        packet['human_source_decisions']=snapshot[sid].get('human_source_decisions',[])
        packet['human_source_decisions_are_metadata']=True
        sources.append(packet)
        physical.append({'source_id':sid,'path':str(loaded[0]),'file_sha256':loaded[1]})
    wording=compile_input([records['claims',cid]['payload'] for cid in all_claims],preflight['claim_input_policy'])
    target_input=compile_input([records['claims',cid]['payload'] for cid in ids],preflight['claim_input_policy'],
                             claim_refs={cid:refs['claims'][cid] for cid in ids})
    content={row['claim_id']:row['content'] for row in wording['claims']}
    keyrefs={(col,oid):ref for col,values in refs.items() for oid,ref in values.items()}
    member_authority={cid:deepcopy(store.claim_management_qualification(cid)) for cid in all_claims if cid not in ids}
    all_authority={**member_authority,**authority}
    material={'claims':[{'qualification':deepcopy(all_authority.get(cid)),'claim_ref':refs['claims'][cid],'target':cid in ids,'support_loaded':cid in support_ids,
                        'content':content[cid]} for cid in all_claims],
        'viewpoints':[{'cvp_ref':refs['canonical_viewpoints'][vid],
            'context_only':vid in context_vids,'viewpoint':records['canonical_viewpoints',vid]['payload'],
            'head':records['viewpoint_revisions',records['canonical_viewpoints',vid]['payload']['current_revision_id']]['payload']} for vid in vids],
        'links':[{'link_ref':refs['viewpoint_claim_links'][oid], 'claim_ref':refs['claims'][r['payload']['claim_id']],
            'cvp_ref':refs['canonical_viewpoints'][r['payload']['viewpoint_id']], 'content':r['payload']}
            for (_,oid),r in sorted(supplied_links.items())],
        'relations':[{'relation_ref':refs['viewpoint_relations'][oid],
            'source_cvp_ref':refs['canonical_viewpoints'][r['payload']['source_viewpoint_id']],
            'target_cvp_ref':refs['canonical_viewpoints'][r['payload']['target_viewpoint_id']], 'content':r['payload']}
            for (_,oid),r in sorted(relationship_rows.items())],
        'identity_decisions':[{'decision_ref':refs['viewpoint_identity_decisions'][did],
                               'content':records['viewpoint_identity_decisions',did]['payload']} for did in sorted(decisions)],
        'claim_relations':[{'relation_ref':refs['claim_relations'][rid],'content':records['claim_relations',rid]['payload']} for rid in sorted(relation_ids)],
        'maintenance_objects':[{'ref':keyrefs[key],'collection':key[0],'content':records[key]['payload']} for key in sorted(explicit)],
        'protections':[{'ref':keyrefs[key],'collection':key[0],**_protection(row['payload'])}
                       for key,row in sorted(records.items()) if _protection(row['payload'])['read_only_protection']],
        'maintenance_scope':{'complete_member_viewpoint_refs':[refs['canonical_viewpoints'][vid] for vid in sorted(maintenance)],
            'relation_endpoint_context_refs':[refs['canonical_viewpoints'][vid] for vid in sorted(context_vids)],
            'endpoint_members_loaded':False,'context_grants_write_permission':False},
        'evidence':[{'evidence_ref':refs['evidence_steps'][eid],'content':records['evidence_steps',eid]['payload']} for eid in sorted(evidence_ids)],
        'fragments':[{'fragment_ref':refs['source_fragments'][fid],'content':records['source_fragments',fid]['payload']} for fid in sorted(fragment_ids)],
        'citations':[{'citation_ref':refs['citations'][cid],'content':records['citations',cid]['payload']} for cid in sorted(citation_ids-{None,''})],
        'sources':sources}
    summary_bindings=[]
    if summary_root:
        from backend.pipeline.cvp_contextual_summary import load_current_cards
        summaries,summary_bindings=load_current_cards(store,summary_root,vids)
        for item in material['viewpoints']:
            vid=item['viewpoint']['viewpoint_id']
            if vid in summaries:
                item['contextual_summary']={'text':summaries[vid],'attribution':'editorial_synthesis'}
    pins={(col,oid):(r['revision'],r['content_sha256'],False) for (col,oid),r in records.items()}
    store.verify_current_read_pins(pins)
    return seal({'schema_version':VERSION,'preflight_sha256':preflight['artifact_sha256'],
        'recall_sha256':recall['artifact_sha256'],'group_key':group_key,'material':material,
        'summary_bindings':summary_bindings,
        'references':refs,'records':[{'collection':col,'object_id':oid,**row} for (col,oid),row in sorted(records.items())],
        'sources':physical,'human_source_decisions':human_source_decisions,'claim_input':target_input,'claim_authority':[{'claim_id':cid,'qualification':deepcopy(authority[cid])} for cid in ids],
        'member_claim_authority':[{'claim_id':cid,'qualification':member_authority[cid]}
                                  for cid in all_claims if cid not in ids],
        'member_support_ids':sorted((support_ids & members)-set(ids)),
        'relationship_support_claim_ids':sorted(relation_claims), 'maintenance_record_keys':[list(key) for key in sorted(explicit)],
        'maintenance_viewpoint_ids':sorted(maintenance),'candidate_viewpoint_ids':primary_vids})



def expand_source(bundle,request,*,source_loader=load_context_source):
    """One adjacent expansion then one full-body expansion per source."""
    verify(bundle,VERSION)
    if (set(request)!={'source_ref','section_ref','extent','reason'} or
            not isinstance(request['reason'],str) or not request['reason'].strip()):
        raise ValueError('context request must identify a source/location and concrete reason')
    result=deepcopy(bundle);materials=result['material']['sources']
    source,expected=_source_request(materials,request)
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


def _source_request(materials,request):
    selected=[s for s in materials if s['source_ref']==request['source_ref']]
    if len(selected)!=1:raise ValueError('context request has a foreign source')
    source=selected[0];level=source.get('expansion_level',0)
    expected='adjacent' if level==0 else 'full' if level==1 else None
    if expected is None or request['extent']!=expected:
        raise ValueError('source expansion is repeated or skips its bounded gradient')
    if request['section_ref'] not in {s['section_ref'] for s in source['sections']}:
        raise ValueError('context request must identify a currently supplied location')
    return source,expected


def validate_material_requests(bundle,response):
    """Check concrete locations after parsing, independent of model schema."""
    members={c['claim_ref'] for c in bundle['material']['claims']
             if not c.get('target',True) and not c.get('support_loaded',False)}
    for item in response['member_requests']:
        if item['member_ref'] not in members:
            raise ValueError('member support request must select an unloaded candidate member')
    for item in response['context_requests']:
        _source_request(bundle['material'].get('sources',[]),item)
