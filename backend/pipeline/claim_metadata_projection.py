"""Program-owned Claim metadata; no teaching, citations or human approval edits."""
from copy import deepcopy
import json
from backend.api.canonical_repository.postgres_store import sha256_json

CONTRACT='claim_metadata_projection_v1'
STALE_CONTRACT='claim_metadata_projection_v2'
REOPEN_CONTRACT='claim_metadata_projection_v3'
RESET={'review_status':'candidate','reviewed_by':None,'reviewed_at':None,'review_note':None}
FIELD='program_metadata_projection'
SUPPORT={'source_fragments':'CMF','evidence_steps':'CME'}
EVIDENCE_REFERENCES = {'evidence_step_ids', 'canonical_evidence_step_ids', 'evidence_step_id', 'evidence_id'}


def accepted_support_reference_projection(old, new, old_receipt, new_receipt):
    """Prove a saved candidate differs only by already allocated typed IDs."""
    mappings = old_receipt.get('support_creation_identities', [])
    if mappings != new_receipt.get('support_creation_identities', []):
        raise ValueError('accepted candidate support identities changed')
    ignored = {'raw_candidate_sha256', 'candidate_sha256'}
    if ({k:v for k,v in old_receipt.items() if k not in ignored}
            != {k:v for k,v in new_receipt.items() if k not in ignored}
            or old_receipt.get('candidate_sha256') != old.digest
            or new_receipt.get('candidate_sha256') != new.digest):
        raise ValueError('accepted candidate normalization changed beyond typed references')
    ids = {m['alias']:m['object_id'] for m in mappings if m['collection'] == 'evidence_steps'}
    changes = []
    def convert(value, path):
        if isinstance(value, list):
            return [convert(v, path + [i]) for i,v in enumerate(value)]
        if isinstance(value, str) and value in ids:
            changes.append(path)
            return ids[value]
        return value
    def walk(value, path):
        if isinstance(value, list):return [walk(v, path + [i]) for i,v in enumerate(value)]
        if not isinstance(value, dict):return value
        return {k:convert(v, path + [k]) if k in EVIDENCE_REFERENCES else walk(v, path + [k]) for k,v in value.items()}
    expected = old.model_dump(mode='json')
    for i,row in enumerate(expected['records']):
        row['payload'] = walk(row['payload'], ['records', i, 'payload'])
    if not changes or expected != new.model_dump(mode='json'):
        raise ValueError('accepted candidate changed teaching, citations or unrelated metadata')
    return {'rule': 'allocated_evidence_reference_completion_v1', 'paths': changes,
            'old_candidate_sha256': old.digest, 'new_candidate_sha256': new.digest}


def sampling_marker(cursor,cid,state):
    if state['payload'].get('review_status')!='human_review_required' or state.get('retired'):
        return None
    events=cursor.execute("""SELECT object_revision,reviewer_kind,decision,artifact
        FROM wang_knowledge.review_events WHERE collection='claims' AND object_id=%s
        ORDER BY created_at DESC,review_event_id DESC""",(cid,)).fetchall()
    if not events or any(kind=='human' for _,kind,_,_ in events):return None
    # A qualification handoff records no new content-review decision. It
    # must not hide the underlying AI marker, but stays in the history seal.
    def context_receipt(event):
        rev, kind, decision, artifact = event
        return (kind == 'system' and decision == 'review_context_revalidated'
            and isinstance(artifact, dict) and artifact.get('schema_version') in {
                'wang_claim_review_context_reuse_v1', 'wang_claim_review_context_reuse_v2',
                'wang_claim_review_context_reuse_v3', 'wang_claim_review_context_reuse_v4'}
            and all(artifact.get(k) for k in ('original_round_id','peer_round_id','original_approval','peer_approval'))
            and any(p.get('collection') == 'claims' and p.get('object_id') == cid
                and p.get('pin') == [state['revision'], state['content_sha256'], False]
                for p in artifact.get('current_pins', [])))
    decisions = [event for event in events if not context_receipt(event)]
    if not decisions:return None
    rev,kind,decision,artifact=decisions[0];r=(artifact or {}).get('resolution',{})
    if not (rev<=state['revision'] and kind=='ai' and decision=='human_review_required'
        and r.get('schema_version')=='wang_legacy_ai_review_reconciliation_v1'
        and r.get('claim_id')==cid and r.get('approval_status')=='not_human_approved'
        and r.get('independent_review_decision')=='pass' and r.get('adjudication_status')=='human_spot_check'
        and r.get('target_review_status')=='human_review_required'):return None
    facts=events
    if rev<state['revision']:
        from backend.api.canonical_repository.postgres_store import record_content_sha
        from backend.pipeline.claim_source_epoch_reassessment import _locator_lineage
        sid=artifact.get('source_id')
        if not isinstance(sid,str) or not sid:return None
        reviewed=cursor.execute('''SELECT v.payload,v.content_sha256,c.status
            FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c USING(change_set_id)
            WHERE v.collection='claims' AND v.object_id=%s AND v.revision=%s''',(cid,rev)).fetchone()
        if not reviewed or reviewed[2]!='applied' or record_content_sha(reviewed[0])!=reviewed[1]:return None
        lineage=_locator_lineage(cursor,cid,rev,reviewed,state,sid)
        if not lineage:return None
        facts={'events':events,'locator_lineage':lineage}
    return {'before_pin':[state['revision'],state['content_sha256'],False],'events_sha256':sha256_json(facts)}


def compile_projection(cursor,request,before,policy):
    if request.operation!='Update' or policy.model_output_contract!='claim_changes_v4':return None
    writable={p.key for p in request.permissions}
    markers={}
    for key in request.target_keys:
        if key in writable and key.startswith('claims/'):
            marker=sampling_marker(cursor,key.split('/',1)[1],before[key])
            if marker:markers[key]=marker
    stale={key:proof for key in request.target_keys if key in writable and key.startswith('claims/')
           and (proof:=stale_review_marker(cursor,key.split('/',1)[1],before[key]))}
    support=any(p.collection in SUPPORT for p in request.creation_permissions)
    result={'contract':CONTRACT,'sampling_markers':markers,'support_identities':support}
    if stale:result.update(contract=STALE_CONTRACT,stale_review_markers=stale)
    from backend.pipeline import claim_management as cm
    authority=json.loads(cm.source.verify_file(request.authorization).read_text())
    from backend.pipeline.claim_legacy_ai_reopen import authorized_markers
    reopened = authorized_markers(cursor, request, before, authority)
    from backend.pipeline.claim_source_epoch_reassessment import authorized_markers as epoch_markers
    epoch = epoch_markers(cursor, request, before, authority)
    if set(epoch) & set(reopened):
        raise ValueError('source epoch and legacy AI reopening cannot overlap')
    reopened.update(epoch)
    if reopened:
        result.update(contract=REOPEN_CONTRACT, legacy_ai_findings_markers=reopened, stale_review_markers=stale)
    repair=authority.get('stale_ai_provenance_repair')
    if repair and result!=repair['projection']:
        raise ValueError('stale AI provenance repair differs from current SQL proof')
    return result if markers or stale or support or reopened else None


def stale_review_marker(cursor,cid,state):
    """An AI review of a different statement is not current approval.

    Read one exact historical version, never replay an import or infer whether
    its teaching is right. The old version and every review event stay intact.
    """
    from backend.api.canonical_repository.postgres_store import record_content_sha
    payload=state['payload']
    if state.get('retired') or payload.get('review_status')!='ai_consensus_reviewed':return None
    if cursor.execute("SELECT 1 FROM wang_knowledge.review_events WHERE collection='claims' AND object_id=%s AND reviewer_kind='human' LIMIT 1",(cid,)).fetchone():return None
    event=cursor.execute("""SELECT review_event_id,object_revision,reviewer_kind,decision,reviewer_id,reason,artifact
        FROM wang_knowledge.review_events WHERE collection='claims' AND object_id=%s
        ORDER BY created_at DESC,review_event_id DESC LIMIT 1""",(cid,)).fetchone()
    if not event or event[2:4]!=('ai','ai_consensus_reviewed') or event[1]>=state['revision']:return None
    row=cursor.execute("""SELECT v.payload,v.content_sha256 FROM wang_knowledge.object_versions v
        JOIN wang_knowledge.change_sets c USING(change_set_id)
        WHERE v.collection='claims' AND v.object_id=%s AND v.revision=%s AND c.status='applied'""",(cid,event[1])).fetchone()
    if (not row or record_content_sha(row[0])!=row[1]
            or not row[0].get('statement') or not payload.get('statement')
            or row[0]['statement']==payload['statement']
            or any(row[0].get(k)!=payload.get(k) for k in RESET)):return None
    return {'before_pin':[state['revision'],state['content_sha256'],False],
            'reviewed_revision':event[1],'reviewed_version_sha256':row[1],'review_event_sha256':sha256_json(event)}


def assert_current(cursor,request,before,policy,projection):
    sampling_keys(request,before,projection)
    if projection!=compile_projection(cursor,request,before,policy):
        raise ValueError('Claim program metadata projection or review history drift')


def sampling_keys(request,before,projection):
    if not projection:return set()
    if (not isinstance(projection,dict) or projection.get('contract') not in {CONTRACT,STALE_CONTRACT,REOPEN_CONTRACT}
        or set(projection)!=({'contract','sampling_markers','support_identities'} |
            ({'stale_review_markers'} if projection['contract'] in {STALE_CONTRACT,REOPEN_CONTRACT} else set()) |
            ({'legacy_ai_findings_markers'} if projection['contract']==REOPEN_CONTRACT else set()))
        or not isinstance(projection['sampling_markers'],dict)
        or type(projection['support_identities']) is not bool):
        raise ValueError('unknown Claim program metadata projection')
    reopened=projection.get('legacy_ai_findings_markers', {})
    if not isinstance(reopened,dict) or projection['contract']==REOPEN_CONTRACT and not reopened:
        raise ValueError('legacy AI finding projection requires exact nonempty targets')
    if set(reopened)&set(projection['sampling_markers']):
        raise ValueError('AI finding and sampling projections cannot overlap')
    markers={**projection['sampling_markers'],**reopened}
    result=set(markers);allowed={p.key for p in request.permissions}
    for key,proof in markers.items():
        state=before.get(key)
        if (key not in request.target_keys or key not in allowed or not key.startswith('claims/') or not state
            or state['retired'] or state['payload'].get('review_status')!='human_review_required'
            or request.operation!='Update' or set(proof)!={'before_pin','events_sha256'}
            or proof['before_pin']!=[state['revision'],state['content_sha256'],False]
            or not isinstance(proof['events_sha256'],str) or len(proof['events_sha256'])!=64
            or any(c not in '0123456789abcdef' for c in proof['events_sha256'])):
            raise ValueError('invalid legacy AI sampling projection scope')
    return result


def stale_review_keys(request,before,projection):
    sampling_keys(request,before,projection)
    if not projection or projection['contract'] not in {STALE_CONTRACT,REOPEN_CONTRACT}:return set()
    markers=projection['stale_review_markers'];allowed={p.key for p in request.permissions}
    if not isinstance(markers,dict) or not markers and projection['contract']==STALE_CONTRACT:raise ValueError('invalid stale AI provenance projection')
    for key,proof in markers.items():
        state=before.get(key)
        if (key not in request.target_keys or key not in allowed or not key.startswith('claims/') or not state
                or state['retired'] or request.operation!='Update'
                or state['payload'].get('review_status')!='ai_consensus_reviewed'
                or set(proof)!={'before_pin','reviewed_revision','reviewed_version_sha256','review_event_sha256'}
                or proof['before_pin']!=[state['revision'],state['content_sha256'],False]
                or type(proof['reviewed_revision']) is not int or not 0<proof['reviewed_revision']<state['revision']
                or any(not isinstance(proof[k],str) or len(proof[k])!=64
                    or any(c not in '0123456789abcdef' for c in proof[k])
                    for k in ('reviewed_version_sha256','review_event_sha256'))):
            raise ValueError('invalid stale AI provenance projection scope')
    return set(markers)


def repair_defects(previous):
    """Carry used repairs across the SQL parent/continuation boundary."""
    continuation=previous.get('continuation') or {}
    defects=set(previous['original_goal'].get('engineering_repair_defects',[]))
    defects.update(continuation.get('engineering_repair_defects',[]))
    defects.update(continuation.get('original_goal',{}).get('engineering_repair_defects',[]))
    if continuation.get('engineering_repair'):defects.add(continuation['engineering_repair']['defect'])
    return sorted(defects)


def repair_extension(previous,request,policy,authority):
    """Consume one standing engineering pair for this proven defect only."""
    repair=authority.get('stale_ai_provenance_repair')
    if repair is None:return None
    if not isinstance(repair,dict):raise ValueError('invalid stale AI provenance repair authority')
    from pathlib import Path
    import hashlib
    from backend.pipeline import claim_management as cm
    defect='stale_ai_review_provenance_v1'
    fields={'defect','predecessor_action_id','prior_metadata_sha256','finding_id','projection',
            'fixed_commit','implementation','test_evidence'}
    old=cm.Request.model_validate(previous['request'])
    finding=previous.get('findings',{}).get(repair.get('finding_id'),{})
    if (set(repair)!=fields or repair['defect']!=defect or repair['predecessor_action_id']!=old.action_id
            or repair['prior_metadata_sha256']!=sha256_json(previous)
            or previous['result'].get('reason')!='engineering_required'
            or defect in repair_defects(previous)
            or finding.get('category')!='engineering' or 'review_note' not in finding.get('component','')
            or request.target_keys!=old.target_keys or request.permissions!=old.permissions
            or request.creation_permissions!=old.creation_permissions
            or request.allowed_operations!=old.allowed_operations or request.source_bindings!=old.source_bindings
            or policy.models!=previous['policy']['models']
            or any(policy.model_dump(mode='json').get(k)!=previous['policy'].get(k)
                for k in ('api_spend','model_output_contract','claim_scope_contract','review_unit_contract'))
            or policy.budget.gpt_calls!=1 or policy.budget.opus_calls!=1 or policy.budget.gemini_calls
            or policy.budget.max_revisions!=1 or policy.independent_audit_required
            or not isinstance(repair['fixed_commit'],str) or len(repair['fixed_commit'])!=40
            or any(c not in '0123456789abcdef' for c in repair['fixed_commit'])):
        raise ValueError('invalid one-time stale AI provenance engineering repair')
    keys=stale_review_keys(old,previous['original_goal']['original_objects'],repair['projection'])
    if not keys or not keys<=set(finding.get('affected_target_keys',[]))|{finding.get('target_key')}:
        raise ValueError('stale AI repair lacks exact affected-target proof')
    expected={str(Path('backend/pipeline')/name):hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()
        for name in ('claim_metadata_projection.py','claim_management.py','cvp_sql_actions.py')}
    if repair['implementation']!=expected:
        raise ValueError('stale AI repair fixed implementation drift')
    report=json.loads(cm.source.verify_file(cm.source.FileBinding.model_validate(repair['test_evidence'])).read_text())
    if report.get('failed')!=0 or not report.get('passed',0)>0 or report.get('implementation')!=expected:
        raise ValueError('stale AI repair requires bound passing fixed-code tests')
    return deepcopy(repair)


def project_candidate(candidate,receipt,materials):
    from backend.pipeline import claim_management as cm
    request=cm.Request.model_validate(materials['unit_request'])
    keys=sampling_keys(request,materials['before'],materials.get(FIELD))
    reopened=set((materials.get(FIELD)or{}).get('legacy_ai_findings_markers',{}))
    result=candidate.model_copy(deep=True);changed=[]
    dispositions={d.record_key:d for d in result.dispositions}
    for row in result.records:
        if row.key in keys-reopened and dispositions[row.key].action=='update':
            if row.payload.get('review_status')!=materials['before'][row.key]['payload'].get('review_status'):
                raise ValueError('model cannot release or approve a review marker')
            row.payload['review_status']='candidate';changed.append(row.key)
    stale=stale_review_keys(request,materials['before'],materials.get(FIELD));reset=[]
    rows={r.key:r for r in result.records}
    for key in sorted(stale|reopened):
        disposition=dispositions[key]
        if disposition.action not in {'retain','update'}:continue
        row=rows.get(key)
        if row is None:
            col,oid=cm.source.split_key(key)
            row=cm.source.Row(collection=col,object_id=oid,payload=deepcopy(materials['before'][key]['payload']))
            result.records.append(row)
        if any(row.payload.get(k)!=materials['before'][key]['payload'].get(k) for k in RESET):
            raise ValueError('model cannot alter stale review provenance')
        row.payload.update(RESET);reset.append(key)
        if disposition.action=='retain':
            disposition.action='update'
            disposition.reason=('Program-only reopening of an owner-authorized legacy AI change suggestion for normal review; no teaching edit or approval. Original version and events preserved.' if key in reopened else
                'Program-only reset of inherited AI review of a different historical statement; no teaching edit or approval. Original version and events preserved.')
    if set(reset)&reopened:receipt={**receipt,'candidate_sha256':result.digest,
        'legacy_ai_findings_reopened':sorted(set(reset)&reopened),'reopen_status':'candidate_not_approved_history_preserved'}
    if set(reset)&stale:receipt={**receipt,'candidate_sha256':result.digest,'stale_ai_provenance_reset':sorted(set(reset)&stale),
                      'stale_review_reset_status':'candidate_not_approved_history_preserved'}
    if changed:receipt={**receipt,'candidate_sha256':result.digest,'legacy_ai_sampling_reset':sorted(changed),
                         'sampling_reset_status':'candidate_not_human_approved'}
    from backend.pipeline.claim_source_binding_projection import project
    return project(result,receipt,materials)


def project_support_aliases(raw,materials):
    """Allocate action-scoped support IDs and rewrite only typed references.

    IDs depend on content, not aliases or array positions. Reordering or
    renaming temporary aliases cannot manufacture semantic progress.
    """
    p=materials.get(FIELD)
    if not p or not p.get('support_identities'):return raw,[]
    from backend.pipeline import claim_management as cm
    from backend.pipeline.claim_model_output import object_json
    if p.get('contract') not in {CONTRACT,STALE_CONTRACT,REOPEN_CONTRACT} or materials['policy']['model_output_contract']!='claim_changes_v4':
        raise ValueError('support identity projection requires managed Update')
    result=deepcopy(raw);maps={};before=materials['before'];request=materials['unit_request']
    creations=result.get('creations',[]);aliases=[r['object_id'] for r in creations]
    existing_ids={key.split('/',1)[1] for key in before}
    granted={g['collection'] for g in request.get('creation_permissions',[])}
    refs={'source_fragment_id':'source_fragments','source_fragment_ids':'source_fragments',
          **dict.fromkeys(EVIDENCE_REFERENCES, 'evidence_steps')}
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
