"""Semantic model input; durable packets retain IDs, pins and recovery metadata."""
from copy import deepcopy

# Bookkeeping is omitted. Source text, qualification, evidence bindings, limits
# and attribution stay visible; summarized readonly members can be requested.
BOOKKEEPING={'artifact_sha256','content_sha256','file_sha256','request_sha256',
    'material_sha256','request_scope_sha256','input_sha256','source_body_sha256',
    'editorial_structure_sha256','claim_sha256','before_sha256','revision',
    'revision_number','before_revision','pinned_claim_revision','provenance',
    'created_at','approved_at','approved_by','run_id','audit','decision_id',
    'created_from_candidate_id','target_bindings','independent_audit',
    'reviewer_id','reviewer_kind','reviewer_model_ids','approval_basis','policy_version',
    'identity_candidate_id','reviewed_by','reviewed_at','summary_basis'}


def project(packet):
    context=packet.get('context',{})
    candidate=packet.get('candidate') or packet.get('previous')
    refs={**context.get('references',{}),**(candidate or {}).get('references',{})}
    ids={value['object_id']:ref for ref,value in refs.items()}
    def clean(value,key=''):
        if isinstance(value,str):return ids.get(value,value) if key.endswith(('_id','_ids')) else value
        if isinstance(value,list):return [clean(v,key) for v in value]
        if isinstance(value,dict):return {k:clean(v,k) for k,v in value.items() if k not in BOOKKEEPING and not k.endswith(('_sha256','_sha256s'))}
        return value
    result={'schema_version':packet['schema_version'],'material':clean(packet['material']),
            'input':clean({'claims':[{'claim_ref':c['claim_ref']} for c in packet['input']['claims']],
                           'text_units':packet['input']['text_units'],'offset_space':packet['input']['offset_space']}),
            'instructions':packet['instructions']}
    # Keep member references discoverable, but let each summarized CVP speak
    # for itself. Targets, requested evidence and maintenance remain complete.
    material=result['material']
    # The source binding and durable packet retain timing/index metadata.
    # Semantic review needs the exact paragraph text and its section/paragraph
    # locators, not the transport coordinates repeated on every source row.
    for source in material.get('sources',[]):
        for section in source.get('sections',[]):
            for paragraph in section.get('paragraphs',[]):
                row=paragraph.get('row')
                if isinstance(row,dict) and 'text' in row:
                    paragraph['row']={'text':row['text']}
    # A fragment's excerpt, source and paragraph identify the exact words in
    # the source sections above. Its timestamps, ingest index and lifecycle
    # fields are repeated transport metadata. Evidence and existing links also
    # carry their own short ref outside content; retain their semantic fields.
    redundant={
        'fragments':{'schema_version','review_status','visibility','fragment_id',
            'anchor_state','source_segment_index','source_url','media_time',
            'media_end_time','citation_id'},
        'evidence':{'schema_version','review_status','visibility',
            'evidence_step_id','citation_ids'},
        'links':{'schema_version','review_status','visibility',
            'viewpoint_claim_link_id','validated_against_viewpoint_revision_id',
            'occurrence_refs'},
    }
    for collection,fields in redundant.items():
        for item in material.get(collection,[]):
            if isinstance(item.get('content'),dict):
                for field in fields:
                    item['content'].pop(field,None)
    summarized={v['cvp_ref'] for v in material.get('viewpoints',[]) if v.get('contextual_summary')}
    links=material.get('links',[])
    hidden_claims=set()
    for claim in material.get('claims',[]):
        owners={link['cvp_ref'] for link in links if link['claim_ref']==claim.get('claim_ref')}
        if (not claim.get('target') and not claim.get('support_loaded')
                and owners and owners<=summarized):
            hidden_claims.add(claim['claim_ref'])
            claim.pop('content',None)
            claim['content_available_on_request']=True
    def afterimage(value):
        if value is None:return None
        # The durable candidate keeps every unchanged source, Claim and CVP
        # record for SQL replay. The reviewer already has those in material;
        # send only actual changed afterimages alongside the semantic plan.
        view={k:deepcopy(v) for k,v in value.items() if k not in
              {'references','records','writes','rebase_history','request_sha256',
               'material_sha256','request_scope_sha256','artifact_sha256'}}
        view['changes']=[{'ref':row['ref'],'collection':row['collection']} for row in value.get('writes',[])]
        view['records']=[{'ref':row['ref'],'collection':row['collection'],'payload':deepcopy(row['payload'])}
            for row in value.get('writes',[])]
        # The short ref and semantic plan already identify these postimages.
        # Repeating program-generated lifecycle/validation fields in every
        # correction record adds bytes without giving the model a decision.
        for row in view['records']:
            for field in ('schema_version','review_status','visibility',
                          'viewpoint_claim_link_id','validated_against_viewpoint_revision_id'):
                row['payload'].pop(field,None)
        view=clean(view)
        return view
    if 'request' in packet:
        request=packet['request']
        result['operation']={'mode':request['mode'],
            'permissions':clean(request['permissions']),
            'maintenance_viewpoint_refs':clean(request['maintenance_viewpoint_ids'],'viewpoint_ids'),
            'creation_slots':[{k:slot[k] for k in ('ref','collection')} for slot in context['creation_slots']]}
        result['previous']=afterimage(packet.get('previous'))
        if context.get('human_constraints'):result['human_constraints']=clean(context['human_constraints'])
    else:
        result['candidate']=afterimage(packet['candidate'])
        if packet.get('human_constraints'):result['human_constraints']=clean(packet['human_constraints'])
    # Corrections also receive the latest review findings, which need not yet
    # occur in the previous candidate. Deduplicate only identical full records.
    existing=[*(candidate or {}).get('prior_findings',[]),*(candidate or {}).get('findings',[])]
    result['prior_findings']=clean([f for f in packet.get('prior_findings',[]) if f not in existing])
    return result
