"""CVP2 records in the existing pipeline_runs/call-reservation boundary.

No model, policy-file, artifact or source IO. All business rules are delegated
to the same pure compiler used before submission, then checked against SQL.
"""
from __future__ import annotations

from .viewpoint_foundation import canonical_json, sha256_json
from .postgres_store import ChangeSetConflict
from .transaction_validation import CONTRACT

VERSION='wang_cvp_management_v2'


def _load(cursor,run_id,*,lock=True):
    row=cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s'+
                       (' FOR UPDATE' if lock else ''),(run_id,)).fetchone()
    if not row:raise ChangeSetConflict('original CVP2 task/step is missing')
    return row[0]


def _save(cursor,run_id,value):
    cursor.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb,heartbeat_at=now() WHERE run_id=%s',
                   (canonical_json(value),run_id))


def _insert(cursor,run_id,value):
    cursor.execute('''INSERT INTO wang_knowledge.pipeline_runs
        (run_id,subject_kind,subject_id,stage,trigger,status,metadata)
        VALUES(%s,'batch',%s,'cvp_group','cli','running',%s::jsonb)''',
        (run_id,value.get('action_id',value.get('parent_run_id')),canonical_json(value)))


def prepare_action(store,request,*,root,preflight,supersede_unwritten_run=None):
    from .cvp_management import Request
    from .cvp_recall import work_groups,verify
    from pathlib import PurePath
    request=Request.model_validate(request).model_dump(mode='json')
    verify(preflight,'wang_cvp_recall_preflight_v1')
    if not PurePath(root).is_absolute():raise ValueError('CVP2 requires an absolute stable root')
    groups=work_groups(request['input']['grouping'],request['input']['claim_ids'])
    if preflight['groups']!=groups or preflight['request_sha256']!=request['input']['artifact_sha256']:
        raise ValueError('CVP2 admission must preserve the complete input/grouping')
    run_id='CVPM-'+sha256_json(request['action_id'])
    identity={'kind':'cvp_management_action','schema_version':VERSION,'request':request,
              'action_id':request['action_id'],'execution_root':root,
              'role_policy_sha256':preflight['role_policy_sha256'],
              'claim_input_policy':preflight['claim_input_policy']}
    with store.connect() as conn,conn.cursor() as cursor:
        from .postgres_store import POSTGRES_APPLY_ADVISORY_LOCK_KEY
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(POSTGRES_APPLY_ADVISORY_LOCK_KEY,))
        old=cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s FOR UPDATE',(run_id,)).fetchone()
        if old:
            if any(old[0].get(k)!=v for k,v in identity.items()):
                raise ChangeSetConflict('CVP2 original root/request/policy cannot reset')
            if old[0].get('result',{}).get('status')=='superseded':
                raise ChangeSetConflict('superseded CVP2 task cannot resume')
            return {**old[0],'run_id':run_id}
        conflicts=cursor.execute('''SELECT run_id,metadata FROM wang_knowledge.pipeline_runs
            WHERE metadata->>'kind'='cvp_management_action'
            AND metadata->'request'->'input'->'claim_ids' ?| %s
            AND metadata->'result'->>'status' NOT IN ('completed','superseded')
            FOR UPDATE''',(request['input']['claim_ids'],)).fetchall()
        if conflicts:
            if len(conflicts)!=1 or conflicts[0][0]!=supersede_unwritten_run:
                raise ChangeSetConflict('pending CVP2 Claims must resume their original task: '+conflicts[0][0])
            old_id,prior=conflicts[0]
            if (len(groups)!=1 or not any(g['group_key']==groups[0]['group_key'] and
                    g['claim_ids']==groups[0]['claim_ids'] for g in prior['groups']) or
                    any(s['state']!='returned' for s in prior['call_reservations'].values()) or
                    any(u.get('completed_step') or u['status']=='completed' for u in prior['units'].values())):
                raise ChangeSetConflict('only an unwritten exact original group can replace its oversized task')
            step_ids=[sid for u in prior['units'].values() for sid in u['steps']]
            steps=[_load(cursor,sid) for sid in step_ids]
            if any(s.get('checkpoint')!='prepared' or s.get('change_set_ids') or s.get('reviewed')
                   for s in steps):
                raise ChangeSetConflict('reviewed or written CVP2 work cannot be superseded')
        pins={('claims',cid):(p['revision'],p['content_sha256'],p['retired']) if p else None
              for cid,p in preflight['admission']['claim_pins'].items()}
        store._assert_transaction_read_pins(cursor,pins)
        if conflicts:
            prior['result']={'status':'superseded','previous_result':prior['result'],
                'successor_run_id':run_id,'successor_action_id':request['action_id']}
            _save(cursor,old_id,prior)
            cursor.execute("UPDATE wang_knowledge.pipeline_runs SET status='cancelled',finished_at=now() WHERE run_id=%s",(old_id,))
            if step_ids:
                cursor.execute("UPDATE wang_knowledge.pipeline_runs SET status='cancelled',finished_at=now() WHERE run_id=ANY(%s)",(step_ids,))
        value={**identity,'groups':groups,'work_groups':groups,'units':{g['group_key']:{'status':'pending','steps':[],'claim_ids':g['claim_ids']} for g in groups},
            'call_reservations':{},'revision_rounds':[], 'input_generations':[],
            'initial_admission':preflight['admission'],'human_constraints':[],'quality_findings':[],'independent_audit':{'status':'not_performed'},
            'result':{'status':'running'}}
        _insert(cursor,run_id,value)
    return {**value,'run_id':run_id}


def prepare_step(store,parent_id,*,group_key,generation,ordinal,materials,revision=False):
    """A material expansion is a step, never an automatic extra revision budget."""
    step_id='CVPMR-'+sha256_json({'parent':parent_id,'group':group_key,'generation':generation,'ordinal':ordinal})
    identity={'kind':'cvp_management_round','schema_version':VERSION,'contract':CONTRACT,
        'parent_run_id':parent_id,'group_key':group_key,'generation':generation,'ordinal':ordinal,
        'materials':materials,'revision':revision}
    with store.connect() as conn,conn.cursor() as cursor:
        parent=_load(cursor,parent_id)
        if parent.get('kind')!='cvp_management_action' or group_key not in parent['units']:
            raise ChangeSetConflict('foreign CVP2 task/group')
        if parent['result'].get('status') in {'completed','superseded'}:raise ChangeSetConflict('closed CVP2 task cannot reopen its budget')
        old=cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s FOR UPDATE',(step_id,)).fetchone()
        if old:
            if any(old[0].get(k)!=v for k,v in identity.items()):raise ChangeSetConflict('original CVP2 step/material changed')
            return step_id
        unit=parent['units'][group_key]
        if unit.get('status')=='completed':raise ChangeSetConflict('completed unit cannot silently restart')
        current=[_load(cursor,sid,lock=False) for sid in unit['steps']]
        same=[s for s in current if s['generation']==generation]
        if ordinal!=len(same):raise ChangeSetConflict('CVP2 step order cannot skip or reset')
        if revision and len(parent['revision_rounds'])>=parent['request']['budget']['max_revisions']:
            raise ChangeSetConflict('CVP2 cumulative revision budget exhausted')
        if revision:parent['revision_rounds'].append(step_id)
        if not same:
            parent['input_generations'].append({'group_key':group_key,'generation':generation,'step_id':step_id})
        unit['steps'].append(step_id);unit.update(status='running',latest_step=step_id)
        value={**identity,'claim_ids':unit['claim_ids'],
               'checkpoint':'prepared','change_set_ids':[],
               'independent_audit_complete':False,'revisit_required':False}
        _insert(cursor,step_id,value);_save(cursor,parent_id,parent)
    return step_id


def reserve_call(store,parent_id,*,key,role,intent):
    if role not in {'gpt','opus'}:raise ValueError('CVP2 has only proposal/recall and independent review roles')
    with store.connect() as conn,conn.cursor() as cursor:
        parent=_load(cursor,parent_id)
        if parent.get('kind')!='cvp_management_action':raise ChangeSetConflict('call has no original CVP2 task')
        slots=parent['call_reservations'];old=slots.get(key)
        identity={'role':role,'intent':intent,'intent_sha256':sha256_json(intent)}
        if old:
            if any(old.get(k)!=v for k,v in identity.items()):raise ChangeSetConflict('CVP2 original call changed')
            return old
        if parent['result'].get('status') in {'completed','superseded'}:raise ChangeSetConflict('closed task cannot acquire calls')
        if len(slots)>=parent['request']['budget']['max_calls']:
            raise ChangeSetConflict('CVP2 cumulative model call budget exhausted')
        if intent['request_bytes']>parent['request']['budget']['max_request_bytes']:
            raise ChangeSetConflict('CVP2 actual request exceeds original byte budget')
        slots[key]={**identity,'state':'planned'};_save(cursor,parent_id,parent)
        return slots[key]


def replace_undispatched_call_plan(store,parent_id,*,key,prior_intent_sha256,role,intent):
    """Repair a planned call only after its owner proves no dispatch files exist.

    A started or returned call is immutable. The old plan remains in SQL so a
    packet-size repair never masquerades as the original reservation.
    """
    with store.connect() as conn,conn.cursor() as cursor:
        parent=_load(cursor,parent_id)
        if parent['result'].get('status') in {'completed','superseded'}:
            raise ChangeSetConflict('closed CVP2 task cannot replan calls')
        slot=parent['call_reservations'].get(key)
        if (not slot or slot['state']!='planned' or slot['intent_sha256']!=prior_intent_sha256
                or slot['role']!=role or slot['intent']['cache_path']!=intent['cache_path']):
            raise ChangeSetConflict('only the exact undispatched CVP2 call can be replanned')
        if intent['request_bytes']>parent['request']['budget']['max_request_bytes']:
            raise ChangeSetConflict('CVP2 actual request exceeds original byte budget')
        slot.setdefault('pre_dispatch_plans',[]).append({
            'intent':slot['intent'],'intent_sha256':slot['intent_sha256']})
        slot.update(intent=intent,intent_sha256=sha256_json(intent))
        _save(cursor,parent_id,parent)
        return slot


def begin_call(store,parent_id,*,key,intent):
    with store.connect() as conn,conn.cursor() as cursor:
        parent=_load(cursor,parent_id);slot=parent['call_reservations'].get(key)
        if not slot or slot['state']!='planned' or slot['intent']!=intent:
            raise ChangeSetConflict('CVP2 call already started/unknown; do not resend')
        slot['state']='started';_save(cursor,parent_id,parent)


def returned_call(store,parent_id,*,key,raw,artifact_sha256):
    with store.connect() as conn,conn.cursor() as cursor:
        parent=_load(cursor,parent_id);slot=parent['call_reservations'].get(key)
        record={'response':raw,'response_sha256':sha256_json(raw),'artifact_sha256':artifact_sha256}
        if not slot or slot['state'] not in {'started','returned'}:raise ChangeSetConflict('call lacks a durable dispatch')
        if slot['state']=='returned' and slot['returned']!=record:raise ChangeSetConflict('original provider answer changed')
        slot.update(state='returned',returned=record);_save(cursor,parent_id,parent)


def import_verified_call(store,parent_id,*,key,role,intent,raw,artifact_sha256,trial_root):
    """Record an already returned trial call, with its origin visible in SQL.

    The caller authenticates the immutable raw response and call intent against
    the exact packet before invoking this method. This never dispatches a model.
    """
    if role not in {'gpt','opus'}:raise ValueError('unknown CVP2 role')
    with store.connect() as conn,conn.cursor() as cursor:
        parent=_load(cursor,parent_id)
        if parent['result'].get('status') in {'completed','superseded'}:
            raise ChangeSetConflict('closed task cannot import a call')
        if len(parent['call_reservations'])>=parent['request']['budget']['max_calls'] and key not in parent['call_reservations']:
            raise ChangeSetConflict('CVP2 cumulative model call budget exhausted')
        if intent['request_bytes']>parent['request']['budget']['max_request_bytes']:
            raise ChangeSetConflict('CVP2 actual request exceeds original byte budget')
        slot={'role':role,'intent':intent,'intent_sha256':sha256_json(intent),
            'state':'returned','returned':{'response':raw,'response_sha256':sha256_json(raw),
                'artifact_sha256':artifact_sha256},
            'origin':{'kind':'verified_trial_import','root':trial_root}}
        old=parent['call_reservations'].get(key)
        if old:
            if old!=slot:raise ChangeSetConflict('imported original call changed')
            return old
        parent['call_reservations'][key]=slot;_save(cursor,parent_id,parent)
        return slot


def progress(store,parent_id,*,group_key,status,detail=None,step_id=None):
    statuses={'running','superseded_input','completed','resource_hold','engineering_required','human_required','needs_upstream'}
    if status not in statuses:raise ValueError('typed CVP2 outcome required')
    with store.connect() as conn,conn.cursor() as cursor:
        parent=_load(cursor,parent_id)
        if parent['result'].get('status')=='superseded':raise ChangeSetConflict('superseded CVP2 task cannot progress')
        unit=parent['units'][group_key]
        if status=='completed':
            step=_load(cursor,step_id,lock=False)
            if (step.get('checkpoint')!='audited' or step.get('parent_run_id')!=parent_id
                    or step.get('group_key')!=group_key or step_id not in unit['steps']
                    or step.get('claim_ids')!=unit['claim_ids']):
                raise ChangeSetConflict('CVP2 completion requires actual readback/closure')
            unit['completed_step']=step_id
        unit.update(status=status,detail=detail)
        for original in parent['groups']:
            aggregate=parent['units'][original['group_key']]
            if aggregate.get('children'):
                aggregate['status']='completed' if all(parent['units'][k]['status']=='completed' for k in aggregate['children']) else 'partitioned'
        parent['result']={'status':'completed' if all(parent['units'][g['group_key']]['status']=='completed' for g in parent['groups']) else 'partial',
            'units':{key:u['status'] for key,u in parent['units'].items()},'independent_audit':'not_performed'}
        if parent['result']['status']=='completed':
            from .cvp_management_authority import validate_parent_current
            validate_parent_current(store,cursor,parent)
        _save(cursor,parent_id,parent)
        if parent['result']['status']=='completed':
            cursor.execute("UPDATE wang_knowledge.pipeline_runs SET status='succeeded',finished_at=now() WHERE run_id=%s",(parent_id,))
        return parent['result']


def complete_step(store,step_id,*,readback,read_pins):
    """Current program readback is completion, not an independent model audit."""
    with store.connect() as conn,conn.cursor() as cursor:
        from .postgres_store import POSTGRES_APPLY_ADVISORY_LOCK_KEY
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(POSTGRES_APPLY_ADVISORY_LOCK_KEY,))
        step=_load(cursor,step_id)
        if step.get('kind')!='cvp_management_round' or step.get('checkpoint') not in {'applied','audited'}:
            raise ChangeSetConflict('CVP2 completion has no applied candidate')
        evidence=step['reviewed']['evidence']['cvp_management']
        from .cvp_management_authority import validate_readback,validate_completion_scope
        observed=store._assert_transaction_read_pins(cursor,read_pins)
        validate_readback(evidence,readback,observed)
        validate_completion_scope(store,conn,cursor,evidence,observed)
        if step.get('checkpoint')=='audited' and step.get('completion')!=readback:
            raise ChangeSetConflict('CVP2 original completion cannot change')
        if step.get('checkpoint')!='audited':_resolve_current_quality(cursor,evidence,readback,observed)
        step.update(checkpoint='audited',completion=readback,program_readback_complete=True,
                    independent_audit_complete=False,independent_audit={'status':'not_performed'})
        _save(cursor,step_id,step)
    return readback


def review_step(store,step_id,*,plan,evidence):
    from .cvp_management_authority import validate_sql_review
    review={'plan':plan.as_dict(),'evidence':dict(evidence)};digest=sha256_json(review)
    with store.connect() as conn,conn.cursor() as cursor:
        step=_load(cursor,step_id)
        if step.get('checkpoint')!='prepared':
            if step.get('reviewed_sha256')==digest and step.get('reviewed')==review:return digest
            raise ChangeSetConflict('CVP2 approved step cannot be overwritten')
        validate_sql_review(store,cursor,step,evidence,plan)
        step.update(checkpoint='reviewed',reviewed=review,reviewed_sha256=digest)
        _save(cursor,step_id,step)
    return digest


def bind_task_constraints(store,parent_id,bundle):
    """Expose durable human and independent-quality facts to the same task.

    An empty set preserves the ordinary material byte for byte. Adding a fact
    creates a material version; it never grants a call or approves a candidate.
    """
    from .cvp_recall import seal
    with store.connect() as conn,conn.cursor() as cursor:
        parent=_load(cursor,parent_id,lock=False)
        quality=_quality_findings(cursor,[(r['collection'],r['object_id']) for r in bundle['records']])
    additions={}
    constraints=[c for c in parent.get('human_constraints',[]) if c['group_key']==bundle['group_key']]
    if constraints:additions['human_constraints']=constraints
    if quality:additions['quality_findings']=quality
    if not additions:return bundle
    return seal({k:v for k,v in bundle.items() if k!='artifact_sha256'}|additions)


def register_content_dispute(store,parent_id,step_id,*,dispute,inputs):
    """Persist only an authenticated, source-grounded dispute from this step."""
    from . import cvp_management as domain
    with store.connect() as conn,conn.cursor() as cursor:
        parent=_load(cursor,parent_id);step=_load(cursor,step_id)
        if step.get('parent_run_id')!=parent_id or step.get('checkpoint')!='prepared':
            raise ChangeSetConflict('content dispute must belong to the unresolved original step')
        bundle=step['materials']['bundle']
        candidate=inputs['candidate']
        expected=domain.build_content_dispute(parent['request'],bundle,**inputs,
            previous=step['materials']['previous'],prior_findings=step['materials']['prior_findings'])
        if expected!=dispute:raise ChangeSetConflict('content dispute changed actual judgments/material')
        for role,name in [('gpt','proposal'),('opus','review')]:
            artifact=dispute['judgment_artifacts'][name]
            matching=[s for s in parent['call_reservations'].values() if s['role']==role
                and s['state']=='returned' and s['returned']['artifact_sha256']==artifact['artifact_sha256']
                and s['returned']['response']==artifact['response']]
            if len(matching)!=1:raise ChangeSetConflict('content dispute lacks its actual original role response')
        store._assert_transaction_read_pins(cursor,{(r['collection'],r['object_id']):
            (r['revision'],r['content_sha256'],r['retired']) for r in bundle['records']})
        prior=step.get('content_dispute')
        if prior and prior!=dispute:raise ChangeSetConflict('original content dispute cannot be replaced')
        step['content_dispute']=dispute
        parent['units'][step['group_key']].update(status='human_required',content_dispute_sha256=dispute['artifact_sha256'])
        _save(cursor,step_id,step);_save(cursor,parent_id,parent)
    return dispute


def register_human_constraint(store,parent_id,step_id,*,decision):
    from .cvp_management import validate_human_constraint
    with store.connect() as conn,conn.cursor() as cursor:
        parent=_load(cursor,parent_id);step=_load(cursor,step_id)
        if step.get('parent_run_id')!=parent_id or not step.get('content_dispute'):
            raise ChangeSetConflict('human explanation has no original content dispute')
        constraint=validate_human_constraint(parent['request'],step['content_dispute'],decision,
            existing_constraints=[c for c in parent['human_constraints'] if c['group_key']==step['group_key']])
        existing=[c for c in parent['human_constraints'] if c['constraint_id']==constraint['constraint_id']]
        if existing and existing!=[constraint]:raise ChangeSetConflict('human decision identity cannot change')
        if not existing:
            if parent.get('result',{}).get('status')=='completed':
                raise ChangeSetConflict('completed task cannot accept a new constraint; use scoped maintenance')
            parent['human_constraints'].append(constraint)
        parent['units'][step['group_key']].update(status='running',human_constraint_id=constraint['constraint_id'])
        _save(cursor,parent_id,parent)
    return constraint


def register_independent_audit(store,parent_id,*,observation,current_records):
    from .cvp_management import register_quality_findings
    with store.connect() as conn,conn.cursor() as cursor:
        from .postgres_store import POSTGRES_APPLY_ADVISORY_LOCK_KEY
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(POSTGRES_APPLY_ADVISORY_LOCK_KEY,))
        parent=_load(cursor,parent_id)
        previous=[r for r in parent.get('quality_findings',[]) if r['observation']['audit_id']==observation['audit_id']]
        if previous:
            if len(previous)!=1 or previous[0]['observation']!=observation:raise ChangeSetConflict('original independent audit cannot change')
            return previous[0]
        registration=register_quality_findings(observation,current_records)
        store._assert_transaction_read_pins(cursor,{key:(row['revision'],row['content_sha256'],False)
            for key,row in current_records.items()})
        previous=[r for r in parent.get('quality_findings',[]) if r['observation']['audit_id']==observation['audit_id']]
        if previous and previous!=[registration]:raise ChangeSetConflict('original independent audit cannot change')
        if not previous:parent.setdefault('quality_findings',[]).append(registration)
        parent['independent_audit']=registration['independent_audit']
        # Preserve submitted/completed business facts. New restrictions belong
        # to current usage and the next explicitly scoped maintenance action.
        _save(cursor,parent_id,parent)
    return registration


def current_quality_findings(store,keys):
    with store.connect() as conn,conn.cursor() as cursor:
        return _quality_findings(cursor,keys)


def _quality_findings(cursor,keys):
    """Targeted current objections in existing task metadata, not a new ledger."""
    found={}
    targets=set(keys)
    if not targets:return []
    # Quality registrations are rare. Read nonempty registrations once instead
    # of rescanning the task table for every eight source/material records.
    query="""SELECT run_id,metadata->'quality_findings' FROM wang_knowledge.pipeline_runs
        WHERE metadata->'quality_findings' IS NOT NULL
          AND metadata->'quality_findings' <> '[]'::jsonb ORDER BY run_id"""
    for run_id,registrations in cursor.execute(query).fetchall():
        for registration in registrations or []:
            if not any((pin['collection'],pin['object_id']) in targets
                       for finding in registration['findings'] for pin in finding['target_pins']):
                continue
            key=run_id,registration['observation']['audit_id']
            found[key]=registration
    return [found[k] for k in sorted(found)]


def _resolve_current_quality(cursor,evidence,readback,observed):
    """Record subsequent normal maintenance resolution without rewriting audit facts."""
    from .cvp_management import resolve_quality_findings
    registrations=evidence['bundle'].get('quality_findings',[])
    if not registrations:return
    for registration in registrations:
        audit_id=registration['observation']['audit_id']
        rows=cursor.execute("""SELECT run_id,metadata FROM wang_knowledge.pipeline_runs
            WHERE metadata->'quality_findings' @> %s::jsonb ORDER BY run_id FOR UPDATE""",
            (canonical_json([{'observation':{'audit_id':audit_id}}]),)).fetchall()
        for run_id,parent in rows:
            updated=[]
            for saved in parent.get('quality_findings',[]):
                if saved['observation']['audit_id']!=audit_id:updated.append(saved);continue
                if saved!=registration:raise ChangeSetConflict('quality finding changed during maintenance completion')
                updated.append(resolve_quality_findings(saved,evidence,readback,observed))
            parent['quality_findings']=updated
            _save(cursor,run_id,parent)


def register_work_units(store,parent_id,*,group_key,plan,recall,index_root):
    """Refine an unstarted original group, preserving its denominator/budget."""
    from .cvp_management_capacity import validate_work_units
    from .cvp_recall import verify
    verify(recall,'wang_cvp_recall_v1')
    with store.connect() as conn,conn.cursor() as cursor:
        parent=_load(cursor,parent_id);unit=parent['units'][group_key]
        group=next(g for g in parent['groups'] if g['group_key']==group_key)
        candidates={r['claim_id']:r['viewpoint_ids'] for r in recall['claim_candidates']}
        if list(candidates)!=group['claim_ids']:raise ChangeSetConflict('subdivision recall lost the original Claim denominator')
        # Before proposal, each recalled CVP is a possible connection and
        # summary target. Splitting Claims that share it would allow two
        # independent postimages for the same viewpoint in one group.
        shared=[['canonical_viewpoints',vid] for vid in parent['request']['maintenance_viewpoint_ids']]
        shared.extend([p['collection'],p['object_id']] for p in parent['request']['permissions'])
        dependencies={cid:[*shared,*(["canonical_viewpoints",vid] for vid in candidates[cid])]
                      for cid in group['claim_ids']}
        validate_work_units(plan,claim_ids=group['claim_ids'],parent_group_key=group_key,
            candidates_by_claim=candidates,mutable_dependencies_by_claim=dependencies,
            max_request_bytes=parent['request']['budget']['max_request_bytes'])
        authority={'plan':plan,'recall':recall,'index_root':index_root}
        if unit.get('capacity'):
            if unit['capacity']!=authority:raise ChangeSetConflict('original capacity subdivision cannot change')
            return parent
        if unit['steps'] or unit.get('status')=='completed':
            raise ChangeSetConflict('capacity cannot repartition a reviewed/unknown original component plan')
        units=[]
        for child in plan['units']:
            key=child['group_key']
            if key in parent['units']:raise ChangeSetConflict('capacity child identity collides')
            state='pending' if child['status']=='ready' else 'resource_hold'
            parent['units'][key]={'status':state,'steps':[],'claim_ids':child['claim_ids'],
                'parent_group_key':group_key,'capacity_status':child['status']}
            units.append({'group_key':key,'claim_ids':child['claim_ids'],
                'original_group_key':group_key,
                'rationale':'Complete Claim capacity subdivision'})
        work=parent.get('work_groups',parent['groups'])
        parent['work_groups']=[entry for g in work for entry in (units if g['group_key']==group_key else [g])]
        unit.update(status='partitioned',children=[u['group_key'] for u in units],capacity=authority)
        _save(cursor,parent_id,parent)
    return parent
