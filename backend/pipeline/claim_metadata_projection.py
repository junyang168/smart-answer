"""Program-owned Claim metadata; no teaching, citations or human approval edits."""
from copy import deepcopy
import json
from backend.api.canonical_repository.postgres_store import sha256_json

CONTRACT='claim_metadata_projection_v1'
FIELD='program_metadata_projection'
SUPPORT={'source_fragments':'CMF','evidence_steps':'CME'}


def sampling_marker(cursor,cid,state):
    if state['payload'].get('review_status')!='human_review_required' or state.get('retired'):
        return None
    events=cursor.execute("""SELECT object_revision,reviewer_kind,decision,artifact
        FROM wang_knowledge.review_events WHERE collection='claims' AND object_id=%s
        ORDER BY created_at DESC,review_event_id DESC""",(cid,)).fetchall()
    if not events or any(kind=='human' for _,kind,_,_ in events):return None
    rev,kind,decision,artifact=events[0];r=(artifact or {}).get('resolution',{})
    if not (rev==state['revision'] and kind=='ai' and decision=='human_review_required'
        and r.get('schema_version')=='wang_legacy_ai_review_reconciliation_v1'
        and r.get('claim_id')==cid and r.get('approval_status')=='not_human_approved'
        and r.get('independent_review_decision')=='pass' and r.get('adjudication_status')=='human_spot_check'
        and r.get('target_review_status')=='human_review_required'):return None
    return {'before_pin':[state['revision'],state['content_sha256'],False],'events_sha256':sha256_json(events)}


def compile_projection(cursor,request,before,policy):
    if request.operation!='Update' or policy.model_output_contract!='claim_changes_v4':return None
    writable={p.key for p in request.permissions}
    markers={}
    for key in request.target_keys:
        if key in writable and key.startswith('claims/'):
            marker=sampling_marker(cursor,key.split('/',1)[1],before[key])
            if marker:markers[key]=marker
    support=any(p.collection in SUPPORT for p in request.creation_permissions)
    return {'contract':CONTRACT,'sampling_markers':markers,'support_identities':support} if markers or support else None


def assert_current(cursor,request,before,policy,projection):
    sampling_keys(request,before,projection)
    if projection!=compile_projection(cursor,request,before,policy):
        raise ValueError('Claim program metadata projection or review history drift')


def sampling_keys(request,before,projection):
    if not projection:return set()
    if (not isinstance(projection,dict) or set(projection)!={'contract','sampling_markers','support_identities'}
        or projection['contract']!=CONTRACT or not isinstance(projection['sampling_markers'],dict)
        or type(projection['support_identities']) is not bool):
        raise ValueError('unknown Claim program metadata projection')
    result=set(projection['sampling_markers']);allowed={p.key for p in request.permissions}
    for key,proof in projection['sampling_markers'].items():
        state=before.get(key)
        if (key not in request.target_keys or key not in allowed or not key.startswith('claims/') or not state
            or state['retired'] or state['payload'].get('review_status')!='human_review_required'
            or request.operation!='Update' or set(proof)!={'before_pin','events_sha256'}
            or proof['before_pin']!=[state['revision'],state['content_sha256'],False]
            or not isinstance(proof['events_sha256'],str) or len(proof['events_sha256'])!=64
            or any(c not in '0123456789abcdef' for c in proof['events_sha256'])):
            raise ValueError('invalid legacy AI sampling projection scope')
    return result


def project_candidate(candidate,receipt,materials):
    from backend.pipeline import claim_management as cm
    request=cm.Request.model_validate(materials['unit_request'])
    keys=sampling_keys(request,materials['before'],materials.get(FIELD))
    result=candidate.model_copy(deep=True);changed=[]
    dispositions={d.record_key:d for d in result.dispositions}
    for row in result.records:
        if row.key in keys and dispositions[row.key].action=='update':
            if row.payload.get('review_status')!=materials['before'][row.key]['payload'].get('review_status'):
                raise ValueError('model cannot release or approve a review marker')
            row.payload['review_status']='candidate';changed.append(row.key)
    if changed:receipt={**receipt,'candidate_sha256':result.digest,'legacy_ai_sampling_reset':sorted(changed),
                         'sampling_reset_status':'candidate_not_human_approved'}
    return result,receipt


def project_support_aliases(raw,materials):
    """Allocate action-scoped support IDs and rewrite only typed references.

    IDs depend on content, not aliases or array positions. Reordering or
    renaming temporary aliases cannot manufacture semantic progress.
    """
    p=materials.get(FIELD)
    if not p or not p.get('support_identities'):return raw,[]
    from backend.pipeline import claim_management as cm
    from backend.pipeline.claim_model_output import object_json
    if p.get('contract')!=CONTRACT or materials['policy']['model_output_contract']!='claim_changes_v4':
        raise ValueError('support identity projection requires managed Update')
    result=deepcopy(raw);maps={};before=materials['before'];request=materials['unit_request']
    creations=result.get('creations',[]);aliases=[r['object_id'] for r in creations]
    existing_ids={key.split('/',1)[1] for key in before}
    granted={g['collection'] for g in request.get('creation_permissions',[])}
    refs={'source_fragment_id':'source_fragments','source_fragment_ids':'source_fragments','evidence_step_ids':'evidence_steps'}
    def rewrite(value,col):
        fields=refs | ({k:None for k in ('from_id','to_id','source_id','target_id')} if col in cm.RELATION_COLLECTIONS else {})
        def convert(item,kind):
            if isinstance(item,list):return [convert(v,kind) for v in item]
            match=maps.get(item) if isinstance(item,str) else None
            return match[1] if match and (kind is None or match[0]==kind) else item
        def visit(item):
            if isinstance(item,list):return [visit(v) for v in item]
            if not isinstance(item,dict):return item
            return {k:convert(v,fields[k]) if k in fields else visit(v) for k,v in item.items()}
        return visit(value)
    # Fragments have no Evidence dependencies. Hash Evidence after its fragment
    # references are canonical, so a model alias never enters either identity.
    for col in ('source_fragments','evidence_steps'):
        for row in creations:
            if row['collection']!=col:continue
            alias=row['object_id']
            if not alias or alias.strip()!=alias or col not in granted or alias in existing_ids or aliases.count(alias)!=1:
                raise ValueError('new support alias is ungranted, ambiguous or already in frozen input')
            fields=rewrite(object_json(row['fields_json']),col)
            oid=SUPPORT[col]+'-'+sha256_json({'action_id':request['action_id'],'collection':col,'fields':fields})
            maps[alias]=(col,oid)
    if not maps:return raw,[]
    for category in ('changes','creations'):
        for row in result.get(category,[]):
            col=row['collection'] if category=='creations' else row['record_key'].split('/',1)[0]
            fields=rewrite(object_json(row['fields_json']),col)
            row['fields_json']=json.dumps(fields,ensure_ascii=False)
            if category=='creations' and col in SUPPORT:row['object_id']=maps[row['object_id']][1]
    return result,[{'collection':col,'alias':alias,'object_id':oid} for alias,(col,oid) in sorted(maps.items())]
