"""CVP2 orchestration on the existing call, SQL and completion boundaries.

Models consume prepared material only. SQL and business rules live in their
owners; this module sequences units and preserves original task/budget facts.
"""
from __future__ import annotations

from copy import deepcopy, copy
from pathlib import Path

from backend.api.canonical_repository import cvp_management as domain
from backend.api.canonical_repository import cvp_management_store as persistence
from backend.api.canonical_repository.cvp_management_authority import validate_evidence
from backend.api.canonical_repository.cvp_recall import (
    seal,verify,recall_plan,combine_recall_candidates,build_statement_index,STATEMENT_SCOPE_PROFILE,
)
from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.cvp_materials import prepare_materials,expand_source,action_member_requests
from backend.pipeline.cvp_recall import maintain_reviewed_index,load_reviewed_index,refresh_recall,subset_recall,recall_adapter
from backend.pipeline.cvp_recall_preparation import prepare_recall
from backend.pipeline.cvp_management_models import adapter,validate_response
from backend.pipeline.cvp_sql_commit import binding,check_binding,pins,decoded_pins,commit_package,resume_reviewed_action
from backend.pipeline.source_reconciliation_runner import exclusive
from backend.pipeline.viewpoint_resolution_runtime import (
    call_model,model_request_identity,read_artifact,read_saved_model_response,write_immutable,stable_decided_at,
    response_adapter,
)


class InputChanged(ValueError):pass


def validate_authorization(request):
    """Concrete scoped direction; a design document is not execution authority."""
    check_binding(request.authorization.model_dump())
    direction=read_artifact(request.authorization.path)
    expected={'schema_version':'wang_cvp_management_authorization_v1','authorized_by':'owner',
        'action_id':request.action_id,'input_sha256':request.input['artifact_sha256'],
        'mode':request.mode,'maintenance_viewpoint_ids':request.maintenance_viewpoint_ids,
        'permissions':[p.model_dump(mode='json') for p in request.permissions],
        'creation_slots':request.creation_slots,'budget':request.budget.model_dump(mode='json')}
    if direction!=expected:raise ValueError('CVP2 authorization differs from exact operation/scope/budget')


def _guard(store,bundle):
    from backend.pipeline.cvp_contextual_summary import guard as summary_guard
    for binding in bundle.get('summary_bindings',[]):summary_guard(store,binding)
    if store.read_human_source_decisions(
            [s['source_id'] for s in bundle['sources']])!=bundle.get('human_source_decisions',[]):
        raise InputChanged('superseded_input: human source decisions changed')
    store.verify_current_read_pins({(r['collection'],r['object_id']):
        (r['revision'],r['content_sha256'],r['retired']) for r in bundle['records']})
    authorities={q['claim_id']:q['qualification'] for q in bundle.get('claim_authority',[])}
    for q in bundle['claim_input']['claims']:
        qualification=store.claim_management_qualification(q['claim_id'])
        if (not qualification or qualification.get('ready') is not True
                or qualification!=authorities.get(q['claim_id'])):
            raise InputChanged('superseded_input: current Claim qualification is no longer valid')
    from backend.pipeline.source_editorial_transition import verify_current_source_binding
    for source in bundle['sources']:
        verify_current_source_binding({'path':source['path'],'file_sha256':source['file_sha256']})


def _transport(store,parent_id,root,request,*,invoke=call_model):
    """Use the common once-call runtime with the existing SQL reservation form."""
    def call(model,payload,cache):
        cache=Path(cache).resolve();key=str(cache.relative_to(root))
        # The common runtime binds the response schema before dispatch. Count
        # that actual wire form, including its wrapper, against this task cap.
        size=response_adapter(model).request_bytes(payload)
        if size>request.budget.max_request_bytes:raise ValueError('indivisible complete material exceeds request budget')
        role='gpt' if model.backend=='codex_subscription' else 'opus'
        intent={**model_request_identity(model,payload),'request_bytes':size,'cache_path':str(cache)}
        pending=store.get_cvp_group(parent_id)['call_reservations']
        if any(slot['state']=='started' and slot.get('applicability')!='obsolete_for_execution'
               and oldkey!=key for oldkey,slot in pending.items()):
            raise ValueError('unknown original model dispatch; reconcile its exact answer before any new call')
        persistence.reserve_call(store,parent_id,key=key,role=role,intent=intent)
        raw,count,seconds=invoke(model,payload,cache,before_dispatch=lambda:
            persistence.begin_call(store,parent_id,key=key,intent=intent))
        # Reauthenticate the real leaf, not a synthetic provider pass supplied
        # by a caller. Test transports write through the same common runtime.
        if read_saved_model_response(model,payload,cache)!=raw:raise ValueError('actual CVP2 raw response drift')
        artifact=read_artifact(cache)
        persistence.returned_call(store,parent_id,key=key,raw=raw,artifact_sha256=artifact['artifact_sha256'])
        return raw,count,seconds
    return call



def _reconcile_saved_dispatches(store,parent_id,unit,request,proposer,reviewer):
    """A cached answer from an interrupted ledger update must authenticate first.

    Existence alone cannot let changed inputs evade a started old Opus call.
    Reconstruct its exact original packet and adapter; never dispatch here.
    """
    parent=store.get_cvp_group(parent_id)
    for step_id in unit['steps']:
        step=store.get_cvp_group(step_id);materials=step['materials']
        directory=Path(materials['step_root']);bundle=materials['bundle']
        for role,client,filename in [('proposal',proposer,'proposal.json'),('review',reviewer,'review.json')]:
            path=directory/filename
            matching=[(key,slot) for key,slot in parent['call_reservations'].items()
                      if slot['state']=='started' and slot.get('applicability')!='obsolete_for_execution'
                      and slot['intent']['cache_path']==str(path)]
            if not matching:continue
            if not path.exists():raise ValueError('unknown original unit call; resume its exact dispatch only')
            if role=='proposal':
                packet=domain.proposal_packet(request,bundle,previous=materials['previous'],
                                               prior_findings=materials['prior_findings'])
            else:
                candidate=read_artifact(directory/'candidate.json')
                packet=domain.review_packet(request,bundle,candidate,prior_findings=materials['prior_findings'])
            model=adapter(role,request,bundle,packet,client=client)
            identity=model_request_identity(model,packet)
            key,slot=matching[0]
            if any(slot['intent'].get(k)!=v for k,v in identity.items()):
                raise ValueError('unknown original unit call identity changed')
            raw=read_saved_model_response(model,packet,path)
            artifact=read_artifact(path)
            persistence.returned_call(store,parent_id,key=key,raw=raw,artifact_sha256=artifact['artifact_sha256'])


def _dispatch_identities(store,parent,cache,intent):
    """Rebuild the original adapter, including its schema/pin wire wrapper."""
    for unit in parent['units'].values():
        for step_id in unit['steps']:
            material=store.get_cvp_group(step_id)['materials']
            if Path(material['step_root'])!=cache.parent or cache.name!='proposal.json':continue
            request=domain.Request.model_validate(parent['request'])
            packet=domain.proposal_packet(request,material['bundle'],previous=material['previous'],
                                          prior_findings=material['prior_findings'])
            model=adapter('proposal',request,material['bundle'],packet)
            return model_request_identity(model,packet),model_request_identity(response_adapter(model,intent),packet)
    return None,None


def _reconcile_capacity_refusals(store,parent_id):
    """Read terminal provider receipts; unknown/partial output stays unknown."""
    from backend.pipeline.claim_management import pre_generation_rejection
    from backend.pipeline.subscription_process import digest_file
    parent=store.get_cvp_group(parent_id)
    for key,slot in parent['call_reservations'].items():
        if slot['state']!='started' or slot['role']!='gpt':continue
        cache=Path(slot['intent']['cache_path']);transport=cache.with_name(cache.stem+'.transport')
        files=[transport/name for name in ('stdout.log','stderr.log')]
        paths=[cache.with_name(cache.stem+'.call-intent.json'),cache.with_name(cache.stem+'.call-failure.json'),
               transport/'invocation.json',transport/'exit.json',*files]
        if cache.exists() or not all(p.is_file() for p in paths) or (transport/'response.json').exists():continue
        intent,failure,_,exit_state=[read_artifact(p) for p in paths[:4]]
        original,wire=_dispatch_identities(store,parent,cache,intent)
        if (exit_state.get('returncode') in (None,0)
                or exit_state.get('files')!={p.name:digest_file(p) for p in files}
                or failure.get('invocation_sha256')!=intent.get('artifact_sha256')
                or original is None or any(slot['intent'].get(k)!=v for k,v in original.items())
                or any(intent.get(k)!=v for k,v in wire.items()) or intent.get('cache_path')!=str(cache)
                or intent.get('artifact_sha256')!=sha256_json({k:v for k,v in intent.items() if k!='artifact_sha256'})):
            continue
        raw={'stdout':files[0].read_text(),'stderr':files[1].read_text(),'last_message':None}
        if pre_generation_rejection(raw)!='capacity':continue
        persistence.record_pre_generation_refusal(store,parent_id,key=key,intent_sha256=slot['intent_sha256'],
            proof={'kind':'capacity','generated_content':False,'automatic_retry':False,
                   'original_failure_preserved':True,'bindings':[binding(p) for p in paths]})


def _stage_call_budget(parent,root,request):
    """A stage's total capacity includes its original reserved/cache calls.

    Stage planners compare their whole call set, whereas the parent ledger
    counts only new reservations. Replay must not charge the same call twice;
    the transport still authenticates every leaf and enforces the parent cap.
    """
    prefix=str(Path(root).resolve())+'/'
    slots=parent['call_reservations']
    own=sum(slot['intent']['cache_path'].startswith(prefix) for slot in slots.values())
    return max(0,persistence.call_limit(parent)-len(slots))+own


def _semantic(candidate):
    def content(row):return {k:v for k,v in row.items() if k not in {'reason','provenance','revision'}}
    return sha256_json({'components':candidate['component_plan']['components'],
        'dispositions':[content(r) for r in candidate['component_dispositions']],
        'members':[content(r) for r in candidate.get('member_dispositions',[])],
        'writes':[{k:content(v) if k=='payload' else v for k,v in r.items()} for r in candidate['writes']],
        'summaries':candidate['summaries']})


def _all_findings(previous,approval):
    rows=[*(previous.get('prior_findings',[]) if previous else []),
          *(previous.get('findings',[]) if previous else []),*approval.get('findings',[])]
    return list({r['finding_id']:r for r in rows}.values())


def _expand(store,request,preflight,group_key,recall,bundle,response):
    from backend.pipeline.cvp_materials import validate_material_requests
    validate_material_requests(bundle,response)
    if response['member_requests']:
        reverse={ref:cid for cid,ref in bundle['references']['claims'].items()}
        members=set(bundle['member_support_ids'])
        for item in response['member_requests']:
            if item['member_ref'] not in reverse:raise ValueError('unknown member support reference')
            cid=reverse[item['member_ref']]
            if cid in members:raise ValueError('member support request is repeated')
            members.add(cid)
        expanded=prepare_materials(store,preflight,group_key,recall,member_support_ids=members,
                                   maintenance_viewpoint_ids=request.maintenance_viewpoint_ids,
                                   maintenance_record_keys=[(p.collection,p.object_id) for p in request.permissions],
                                   summary_root=request.input.get('contextual_summary_root'))
        # Retain source expansions already provided; source refs may be
        # renumbered only with a new fully bound material version.
        old_by_id={sid:next(s for s in bundle['material']['sources'] if s['source_ref']==ref)
                   for sid,ref in bundle['references']['source_documents'].items()}
        for sid,ref in expanded['references']['source_documents'].items():
            if sid in old_by_id:
                old=old_by_id[sid];new=next(s for s in expanded['material']['sources'] if s['source_ref']==ref)
                if old['expansion_level']:
                    old_binding=next(s for s in bundle['sources'] if s['source_id']==sid)
                    new_binding=next(s for s in expanded['sources'] if s['source_id']==sid)
                    if (old_binding!=new_binding or any(old[k]!=new[k] for k in
                            ('source_body_sha256','editorial_structure_sha256'))):
                        raise InputChanged('superseded_input: source changed while retaining requested expansion')
                    # New member support may require another section of this same
                    # source. Retain both the required sections and prior expansion.
                    sections={s['section_ref']:deepcopy(s) for s in [*new['sections'],*old['sections']]}
                    new.update(sections=sorted(sections.values(),key=lambda s:s['start']),
                               expansion_level=old['expansion_level'])
        expanded['context_requests']=deepcopy(bundle.get('context_requests',[]))
        if bundle['material'].get('screening'):
            expanded['material']['screening']=deepcopy(bundle['material']['screening'])
        if bundle['material'].get('initial_screening'):
            expanded['material']['initial_screening']=deepcopy(bundle['material']['initial_screening'])
        expanded['previous_material_sha256']=bundle['artifact_sha256']
        expanded=seal({k:v for k,v in expanded.items() if k!='artifact_sha256'})
    else:expanded=bundle
    # Requests reference the pre-expansion packet, so source-ref remapping
    # after loading a member is resolved by canonical source identity.
    by_source={}
    for item in response['context_requests']:
        sid=next(sid for sid,ref in bundle['references']['source_documents'].items() if ref==item['source_ref'])
        mapped={**item,'source_ref':expanded['references']['source_documents'][sid]}
        by_source.setdefault(mapped['source_ref'],[]).append(mapped)
    for source_ref,requests in by_source.items():
        # All locations refer to the same pre-response level. Union their
        # adjacent ranges and consume this source's gradient only once.
        before=expanded
        variants=[expand_source(before,item) for item in requests]
        expanded=variants[0]
        target=next(s for s in expanded['material']['sources'] if s['source_ref']==source_ref)
        sections={s['section_ref']:s for version in variants
                  for source in version['material']['sources'] if source['source_ref']==source_ref
                  for s in source['sections']}
        target['sections']=sorted(sections.values(),key=lambda s:s['start'])
        expanded['context_requests']=[*before.get('context_requests',[]),*requests]
        expanded=seal({k:v for k,v in expanded.items() if k!='artifact_sha256'})
    return expanded


def _readback(store,evidence):
    package=validate_evidence(evidence)
    context=store.read_package_cvp_context(package)
    context.update(store.read_current_dependency_context(
        [(r['collection'],r['object_id']) for r in evidence['bundle']['records']]))
    keys={(col,row[domain.KNOWLEDGE_COLLECTIONS[col][1]]) for col,rows in package.items()
          if col in domain.KNOWLEDGE_COLLECTIONS for row in rows}
    context.update(store.read_current_records(keys))
    vids=set(evidence['bundle'].get('candidate_viewpoint_ids',[]))
    vids.update(row['viewpoint_id'] for col in ('canonical_viewpoints','viewpoint_revisions','viewpoint_claim_links')
                for row in package.get(col,[]))
    targets=[c['claim_id'] for c in evidence['bundle']['claim_input']['claims']]
    links=store.read_cvp_links(viewpoint_ids=vids,claim_ids=targets)
    expected={(r['collection'],r['object_id']):r['payload'] for r in evidence['candidate']['records']}
    for col,rows in package.items():
        if col in domain.KNOWLEDGE_COLLECTIONS:
            expected.update({(col,row[domain.KNOWLEDGE_COLLECTIONS[col][1]]):row for row in rows})
    active_expected={oid for (col,oid),p in expected.items() if col=='viewpoint_claim_links' and p.get('effective_state')=='active'}
    active_actual={oid for (col,oid),r in links.items() if r['payload'].get('effective_state')=='active'}
    if active_actual!=active_expected:raise ValueError('current owned/member links have unresolved impacts')
    context.update(links)
    # Reverse consumers are observed after the one actual write, including
    # store-generated invalidations. No model is invoked by this program audit.
    affected=store.read_affected_context(sorted(keys)) if keys else {}
    context.update(affected)
    readback=seal({'schema_version':domain.VERSION+'_readback',
        'candidate_sha256':evidence['candidate']['artifact_sha256'],
        'approval_sha256':evidence['approval']['artifact_sha256'],
        'actual_records':[{'collection':col,'object_id':oid,'payload':row['payload']}
                          for (col,oid),row in sorted(context.items())],
        'downstream_invalidations':[{'collection':col,'object_id':oid,'status':row['payload'].get('status')}
            for (col,oid),row in sorted(context.items())
            if col=='product_dependencies' and row['payload'].get('status')!='current'],
        'pending_impacts':[],'independent_audit':'not_performed'})
    return readback,pins(context)



def _rebase_previous(request,old_bundle,new_bundle,previous,*,prior_findings):
    if previous.get('schema_version')==domain.VERSION+'_rebase_frame':
        return domain.prepare_rebase_frame(request,previous['old_bundle'],new_bundle,
            previous['previous_candidate'],prior_findings=prior_findings)
    current={(r['collection'],r['object_id']):r for r in new_bundle['records']}
    changed=any(current.get((r['collection'],r['object_id']))!=r for r in old_bundle['records'])
    if old_bundle['claim_input']!=new_bundle['claim_input'] or changed:
        return domain.prepare_rebase_frame(request,old_bundle,new_bundle,previous,prior_findings=prior_findings)
    return domain.rebind_candidate(request,old_bundle,new_bundle,previous,prior_findings=prior_findings)


def _same_material_inputs(current,saved):
    """Previously requested member/source expansion may add read-only material."""
    old={(r['collection'],r['object_id']):r for r in saved['records']}
    if any(old.get((r['collection'],r['object_id']))!=r for r in current['records']):return False
    source_ids={s['source_id'] for s in current['sources']}
    if ('human_source_decisions' in current or 'human_source_decisions' in saved):
        if current.get('human_source_decisions')!=[d for d in saved.get('human_source_decisions',[])
                                                  if d['source_id'] in source_ids]:return False
    return all(current.get(k)==saved.get(k) for k in (
        'claim_input','claim_authority','member_claim_authority','human_constraints','quality_findings','maintenance_viewpoint_ids','link_evidence_contract'))


def _omitted_duplicate_relations(store,step):
    """Identify the specific missing-input defect, never a rejected verdict."""
    if not step or step.get('checkpoint')!='reviewed' or step.get('change_set_ids'):
        return {}
    bundle=step['materials']['bundle']
    original={(r['collection'],r['object_id']) for r in bundle['records']}
    created={tuple(op['payload'][k] for k in ('source_viewpoint_id','target_viewpoint_id','relation_type'))
             for op in step['reviewed']['plan']['operations']
             if op['collection']=='viewpoint_relations' and op['operation']=='create'}
    if not created:return {}
    return {key:row for key,row in store.read_cvp_relations(
        viewpoint_ids=bundle['candidate_viewpoint_ids']).items()
        if key not in original and not row.get('retired')
        and row['payload'].get('effective_state')=='active'
        and tuple(row['payload'][k] for k in ('source_viewpoint_id','target_viewpoint_id','relation_type')) in created}


def _summary_only_preimage_refresh(old_bundle,new_bundle):
    """A promoted editorial summary invalidates old writes, not CVP identity.

    Restart proposal on the current material within the same task. Never carry
    an old candidate across a changed CVP preimage or changed Claim wording.
    """
    if old_bundle['claim_input']!=new_bundle['claim_input']:return False
    old={(r['collection'],r['object_id']):r for r in old_bundle['records']}
    new={(r['collection'],r['object_id']):r for r in new_bundle['records']}
    support={'evidence_steps','source_fragments','source_documents','citations','claim_relations'}
    changed=False
    for key,row in old.items():
        current=new.get(key)
        # Prior on-demand member/source expansion is read-only context. The
        # current proposal can request it again if needed.
        if key[0] in support:continue
        if current is None:return False
        if (row['revision'],row['content_sha256'])==(current['revision'],current['content_sha256']):
            continue
        if key[0]!='canonical_viewpoints':return False
        before={k:v for k,v in row['payload'].items() if k not in {'revision','summary','summary_basis'}}
        after={k:v for k,v in current['payload'].items() if k not in {'revision','summary','summary_basis'}}
        if before!=after or not current['payload'].get('summary') or not current['payload'].get('summary_basis'):
            return False
        changed=True
    return changed


def _completed_summary_proofs(store,step):
    """Restore direct approved metadata after a restart, without another review."""
    root=Path(step['materials']['step_root'])/'summary-proofs'
    existing=sorted(root.glob('summary-*.json')) if root.exists() else []
    if existing:return [read_artifact(p) for p in existing]
    evidence=step['reviewed']['evidence']['cvp_management']
    snapshots={(r['collection'],r['object_id']):r['payload'] for r in step['completion']['actual_records']}
    heads=[]
    for summary in evidence['candidate']['summaries']:
        ref=evidence['candidate']['references'][summary['ref']]
        vid=ref['object_id'];view=snapshots['canonical_viewpoints',vid]
        if view['identity_status']!='active':continue
        rid=view['current_revision_id'];revision=snapshots['viewpoint_revisions',rid]
        heads.append({'viewpoint_id':vid,'viewpoint_revision_id':rid,
            'head_sha256':sha256_json({'viewpoint':[view['revision'],record_content_sha(view)],
                'revision':[revision['revision'],record_content_sha(revision)]}),
            **{k:revision[k] for k in ('core_proposition','scope','proposition_signature')}})
    from backend.pipeline.cvp_management_index import write_committed_summary_proofs
    return write_committed_summary_proofs(evidence,Path(step['materials']['step_root'])/'proposal.json',
        Path(step['materials']['step_root'])/'review.json',heads,root)



def _group_preflight(current,group,plan):
    return seal({k:v for k,v in current.items() if k!='artifact_sha256'}|
                {'groups':[group],'recall_plans':[{'group_key':group['group_key'],'plan':plan}]})


def _capacity_plan(store,request,current,group,recall,index,proposer,parent_id):
    from backend.api.canonical_repository.cvp_management_capacity import RequestProbe,plan_work_units
    from backend.api.canonical_repository.cvp_claim_input import compile_input
    ids=group['claim_ids'];raw=store.read_current_records([('claims',cid) for cid in ids])
    cards={c['claim_id']:{'claim_id':c['claim_id'],**c['content']} for c in compile_input(
        [raw['claims',cid]['payload'] for cid in ids],current['claim_input_policy'])['claims']}
    candidates={r['claim_id']:r['viewpoint_ids'] for r in recall['claim_candidates']}
    shared=[('canonical_viewpoints',vid) for vid in request.maintenance_viewpoint_ids]
    shared.extend((p.collection,p.object_id) for p in request.permissions)
    def probe(selected):
        child={**group,'claim_ids':list(selected),'original_group_key':group['group_key'],
            'group_key':group['group_key']+':capacity:'+sha256_json({
                'parent_group_key':group['group_key'],'claim_ids':list(selected)})[:20]}
        plan=recall_plan(index,[cards[cid] for cid in selected],max_request_bytes=request.budget.max_request_bytes,
            measure=lambda p:recall_adapter(proposer,p,max_request_bytes=request.budget.max_request_bytes).request_bytes(p))
        result=combine_recall_candidates(plan,{cid:set(candidates[cid]) for cid in selected})
        prepared=_group_preflight(current,child,plan)
        material=prepare_materials(store,prepared,child['group_key'],result,
            maintenance_viewpoint_ids=request.maintenance_viewpoint_ids,
            maintenance_record_keys=[(p.collection,p.object_id) for p in request.permissions],
                                   summary_root=request.input.get('contextual_summary_root'))
        material=persistence.bind_task_constraints(store,parent_id,material)
        packet=domain.proposal_packet(request,material)
        return [RequestProbe('proposal',adapter('proposal',request,material,packet,client=proposer),packet)]
    # A recalled viewpoint may become the target of a new connection and one
    # shared summary afterimage. Keep those Claims together before proposing.
    dependencies={cid:[*shared,*(('canonical_viewpoints',vid) for vid in candidates[cid])]
                  for cid in ids}
    return plan_work_units(ids,parent_group_key=group['group_key'],candidates_by_claim=candidates,
        mutable_dependencies_by_claim=dependencies,
        max_request_bytes=request.budget.max_request_bytes,probe=probe)


def run(store,request,*,root,execute=False,clients=None):
    request=domain.Request.model_validate(request);root=Path(root).resolve()
    validate_authorization(request)
    saved=store.get_cvp_group('CVPM-'+sha256_json(request.action_id)) if execute else None
    if saved:
        if saved.get('request')!=request.model_dump(mode='json') or saved.get('execution_root')!=str(root):
            from backend.api.canonical_repository.postgres_store import ChangeSetConflict
            raise ChangeSetConflict('CVP2 original root/request/policy cannot reset')
        if saved.get('result',{}).get('status')=='completed':
            from backend.pipeline.viewpoint_route_cvp_worker import handoff_completed_cvp_groups
            handoff_completed_cvp_groups(store,saved,root)
            return saved['result']
    preflight=prepare_recall(store,request.input,allow_unqualified=True,defer_index=execute)
    if not execute:return {'status':'preflight','preflight':preflight,'model_calls':0,'semantic_writes':0}
    with exclusive(root):
        parent=persistence.prepare_action(store,request,root=str(root),preflight=preflight)
        parent_id=parent['run_id'];call=_transport(store,parent_id,root,request)
        _reconcile_capacity_refusals(store,parent_id)
        if parent['result'].get('status')=='completed':
            from backend.pipeline.viewpoint_route_cvp_worker import handoff_completed_cvp_groups
            handoff_completed_cvp_groups(store,parent,root)
            return parent['result']
        from backend.pipeline.viewpoint_resolution_runtime import subscription_client
        proposer=clients['gpt'] if clients else subscription_client('codex','gpt-6.1-sol','high')
        reviewer=clients['opus'] if clients else subscription_client('claude','claude-opus-5-5','high')
        proposer=copy(proposer);reviewer=copy(reviewer)
        proposer.max_output_tokens=reviewer.max_output_tokens=request.budget.max_output_tokens
        previous_index_root=request.input.get('index_root')
        approved_cards=[]
        position=0
        while True:
            parent=store.get_cvp_group(parent_id)
            groups=parent.get('work_groups',parent['groups'])
            if position>=len(groups):break
            group=groups[position];position+=1
            key=group['group_key'];unit=parent['units'][key]
            if unit.get('capacity_status')=='resource_hold':
                persistence.progress(store,parent_id,group_key=key,status='resource_hold',
                    detail='indivisible complete Claim dependency cluster exceeds original request budget')
                continue
            if unit['status']=='completed':
                completed=store.get_cvp_group(unit['completed_step'])
                from backend.pipeline.viewpoint_route_cvp_worker import handoff_cvp_route_jobs
                route_package=completed['reviewed']['evidence']['package']['payload']
                handoff_cvp_route_jobs(store,route_package,root/'route-queue-v2',
                                       Path(completed['materials']['step_root']))
                if not domain.statement_recall(request):
                    approved_cards.extend(_completed_summary_proofs(store,completed))
                previous_index_root=completed['materials'].get('index_root',previous_index_root)
                continue
            try:
                blocked=[b for b in preflight.get('admission',{}).get('blocked_claims',[]) if b['claim_id'] in group['claim_ids']]
                if blocked:
                    persistence.progress(store,parent_id,group_key=key,status='needs_upstream',detail=blocked)
                    continue
                # Recover an approved/applying unit before any proposal or
                # stale-input preparation. Committed identity always wins.
                latest=store.get_cvp_group(unit['steps'][-1]) if unit['steps'] else None
                omitted_relations=_omitted_duplicate_relations(store,latest)
                saved_input_repair=((latest or {}).get('materials',{}).get('bundle',{}).get('material',{}).get('input_repair')
                                   or parent.get('fragment_selection_repairs',{}).get(key))
                if latest and latest['checkpoint'] in {'reviewed','applied','audited'} and not omitted_relations:
                    if latest['checkpoint']=='reviewed':resume_reviewed_action(store,latest['run_id'])
                    evidence=store.get_cvp_group(latest['run_id'])['reviewed']['evidence']['cvp_management']
                    readback,rpins=_readback(store,evidence)
                    persistence.complete_step(store,latest['run_id'],readback=readback,read_pins=rpins)
                    persistence.progress(store,parent_id,group_key=key,status='completed',step_id=latest['run_id'])
                    from backend.pipeline.viewpoint_route_cvp_worker import handoff_cvp_route_jobs
                    handoff_cvp_route_jobs(store,
                                           store.get_cvp_group(latest['run_id'])['reviewed']['evidence']['package']['payload'],
                                           root/'route-queue-v2',
                                           Path(latest['materials']['step_root']))
                    completed=store.get_cvp_group(latest['run_id'])
                    if not domain.statement_recall(request):
                        approved_cards.extend(_completed_summary_proofs(store,completed))
                    previous_index_root=completed['materials'].get('index_root',previous_index_root)
                    continue
                _reconcile_saved_dispatches(store,parent_id,unit,request,proposer,reviewer)
                parent=store.get_cvp_group(parent_id)
                # An unresolved semantic dispatch owns its original step even
                # when a Claim/head changes. Never acquire a new generation to
                # evade an unknown Opus/proposal response.
                unit_prefix=str(root/('unit-'+sha256_json(key)[:16]))+'/'
                if any(slot['state']=='started' and slot['intent']['cache_path'].startswith(unit_prefix)
                       and not Path(slot['intent']['cache_path']).exists()
                       for slot in parent['call_reservations'].values()):
                    raise ValueError('unknown original unit call; resume its exact dispatch only')
                heads=store.read_cvp_heads();index_root=root/('index-'+sha256_json(heads)[:24])
                remaining=_stage_call_budget(parent,index_root,request)
                # A changed head cannot bypass an unknown original index call.
                for slot in parent['call_reservations'].values():
                    if slot['state']=='started' and '/index-' in slot['intent']['cache_path'] and not Path(slot['intent']['cache_path']).exists():
                        raise ValueError('unknown original index call; no new input/root retry')
                live={h['viewpoint_id']:h for h in heads}
                approved_by_view={}
                for proof in approved_cards:
                    h=proof['heads'][0]
                    if live.get(h['viewpoint_id'])==h:approved_by_view[h['viewpoint_id']]=proof
                if request.input.get('recall_profile') == STATEMENT_SCOPE_PROFILE:
                    index=build_statement_index(heads)
                    write_immutable(index_root/'index.json',index)
                elif (index_root/'index.json').exists():
                    index=load_reviewed_index(heads,proposer=proposer,reviewer=reviewer,output_dir=index_root)
                else:
                    index,_=maintain_reviewed_index(heads,previous_output_dir=previous_index_root,
                        proposer=proposer,reviewer=reviewer,output_dir=index_root,
                        max_request_bytes=request.budget.max_request_bytes,max_calls=max(0,remaining),
                        invoke=call,approved_card_proofs=list(approved_by_view.values()))
                current=prepare_recall(store,request.input,index=index,allow_unqualified=True)
                generation=sha256_json({'group':group,'authority':[q for q in current['claim_authority']
                    if q['claim_id'] in group['claim_ids']], 'index_sha256':index['artifact_sha256'],
                    'existing_links':[link for link in current['existing_links'] if link.get('claim_id') in group['claim_ids']]})[:24]
                unit_root=root/('unit-'+sha256_json(key)[:16])/generation
                if group.get('original_group_key'):
                    from backend.api.canonical_repository.cvp_claim_input import compile_input
                    raw=store.read_current_records([('claims',cid) for cid in group['claim_ids']])
                    child_cards=[{'claim_id':c['claim_id'],**c['content']} for c in compile_input(
                        [raw['claims',cid]['payload'] for cid in group['claim_ids']],current['claim_input_policy'])['claims']]
                    child_plan=recall_plan(index,child_cards,max_request_bytes=request.budget.max_request_bytes,
                        measure=lambda p:recall_adapter(proposer,p,max_request_bytes=request.budget.max_request_bytes).request_bytes(p))
                    current=_group_preflight(current,group,child_plan)
                group_plan=next(p['plan'] for p in current['recall_plans'] if p['group_key']==key)
                cards=[{'claim_id':group_plan['claim_refs'][c['claim_ref']],
                        **{k:v for k,v in c.items() if k!='claim_ref'}} for c in
                    (group_plan['shards'][0]['claims'] if group_plan['shards'] else [])]
                if not cards:
                    raw=store.read_current_records([('claims',cid) for cid in group['claim_ids']])
                    from backend.api.canonical_repository.cvp_claim_input import compile_input
                    cards=[{'claim_id':c['claim_id'],**c['content']} for c in compile_input(
                        [raw['claims',cid]['payload'] for cid in group['claim_ids']],current['claim_input_policy'])['claims']]
                recall_root=unit_root/'recall'
                previous_recall=Path(latest['materials']['recall_root']) if latest else None
                if (recall_root/'recall-authentication.json').exists():
                    from backend.pipeline.cvp_recall import _authenticate_recall_root
                    authenticated,_=_authenticate_recall_root(recall_root,client=proposer,previous_index=index)
                    if authenticated['claims']!=cards or authenticated['plan']!=group_plan:
                        raise ValueError('saved group recall differs from current complete bindings')
                    # Ignore unrelated groups' links; they cannot change this
                    # group's frozen recall action or acquire another call.
                    relevant=lambda rows:[l for l in rows if l.get('claim_id') in group['claim_ids']]
                    if relevant(authenticated['existing_links'])!=relevant(current['existing_links']):
                        raise ValueError('saved group recall current active links changed')
                    recall=read_artifact(recall_root/'recall-result.json')
                    metrics={'current_plan':authenticated['plan'],'model_calls':0}
                else:
                    if previous_recall is None and group.get('original_group_key'):
                        capacity=parent['units'][group['original_group_key']]['capacity']
                        origin_root=Path(capacity['plan']['origin_recall_root'])
                        origin=read_artifact(origin_root/'recall-authentication.json')
                        seed=unit_root/'capacity-origin'
                        subset_recall(origin['index'],cards,origin_output_dir=origin_root,
                            output_dir=seed,client=proposer,existing_links=current['existing_links'])
                        previous_recall=seed
                    recall,metrics=refresh_recall(index,cards,previous_output_dir=previous_recall,
                        client=proposer,output_dir=recall_root,max_request_bytes=group_plan['max_request_bytes'],
                        max_calls=_stage_call_budget(store.get_cvp_group(parent_id),recall_root,request),
                        existing_links=current['existing_links'],invoke=call)
                current=seal({k:v for k,v in current.items() if k!='artifact_sha256'}|{'recall_plans':[
                    {'group_key':p['group_key'],'plan':metrics['current_plan'] if p['group_key']==key else p['plan']}
                    for p in current['recall_plans']]})
                filtered_path=unit_root/'filtered-recall.json'
                if filtered_path.exists() and not (omitted_relations or saved_input_repair):
                    recall=read_artifact(filtered_path)
                    saved_bundle=unit_root/'filtered-bundle.json'
                    if not saved_bundle.exists():
                        raise ValueError('screened recall lacks its bound material proof')
                    bundle=read_artifact(saved_bundle)
                else:
                    bundle=prepare_materials(store,current,key,recall,maintenance_viewpoint_ids=request.maintenance_viewpoint_ids,
                                       maintenance_record_keys=[(p.collection,p.object_id) for p in request.permissions],
                                       member_support_ids=(latest['materials']['bundle']['member_support_ids'] if omitted_relations or saved_input_repair else ()),
                                       summary_root=request.input.get('contextual_summary_root'))
                bundle=persistence.bind_task_constraints(store,parent_id,bundle)
                repair=saved_input_repair
                if omitted_relations:
                    _guard(store,bundle)
                    repair=persistence.reserve_missing_relation_repair(store,parent_id,latest['run_id'],
                        bundle=bundle,relations=omitted_relations,implementation=[
                            binding(Path(__file__)),binding(Path(__file__).with_name('cvp_materials.py')),
                            binding(Path(persistence.__file__))])
                if repair:
                    # The old approval stays immutable. This new generation
                    # repairs its missing input and must receive its own review.
                    generation=sha256_json({'base_generation':generation,'input_repair':repair['artifact_sha256']})[:24]
                    unit_root=root/('unit-'+sha256_json(key)[:16])/generation
                    bundle=seal({k:v for k,v in bundle.items() if k!='artifact_sha256'}|{
                        'material':{**bundle['material'],'input_repair':repair}})
                if not latest and not filtered_path.exists() and request.input.get('recall_profile')==STATEMENT_SCOPE_PROFILE:
                    initial_packet=domain.proposal_packet(request,bundle)
                    initial_adapter=adapter('proposal',request,bundle,initial_packet,client=proposer)
                    if initial_adapter.request_bytes(initial_packet)>request.budget.max_request_bytes:
                        from backend.pipeline.cvp_candidate_screening import screen_to_fit
                        recall,bundle,_=screen_to_fit(store,current,key,recall,request,
                            proposer=proposer,reviewer=reviewer,
                            screening_root=unit_root/'screening',call=call)
                        bundle=persistence.bind_task_constraints(store,parent_id,bundle)
                        write_immutable(unit_root/'filtered-bundle.json',bundle)
                        write_immutable(filtered_path,recall)
                # Control facts version semantic material while the original
                # authenticated recall remains reusable. They never reset calls.
                controls={k:bundle[k] for k in ('human_constraints','quality_findings') if bundle.get(k)}
                if controls:
                    generation=sha256_json({'base_generation':generation,'controls':controls})[:24]
                    unit_root=root/('unit-'+sha256_json(key)[:16])/generation
                if not latest and not group.get('original_group_key'):
                    initial_packet=domain.proposal_packet(request,bundle)
                    initial_adapter=adapter('proposal',request,bundle,initial_packet,client=proposer)
                    if initial_adapter.request_bytes(initial_packet)>request.budget.max_request_bytes:
                        plan=_capacity_plan(store,request,current,group,recall,index,proposer,parent_id)
                        plan=seal({k:v for k,v in plan.items() if k!='artifact_sha256'}|{'origin_recall_root':str(recall_root)})
                        persistence.register_work_units(store,parent_id,group_key=key,plan=plan,
                            recall=recall,index_root=str(index_root))
                        position-=1
                        continue
                previous=None;prior_findings=[];ordinal=0;revision=False;seen=set();proposal_origin=None
                if latest:
                    oldroot=Path(latest['materials']['step_root'])
                    oldbundle=latest['materials']['bundle']
                    same=_same_material_inputs(bundle,oldbundle)
                    if same:
                        _guard(store,oldbundle)
                        bundle=oldbundle
                    has_approval=(oldroot/'approval.json').exists()
                    replay_step=same and (not has_approval or read_artifact(oldroot/'approval.json')['approved'])
                    if replay_step:
                        # A saved passing review still owns this prepared
                        # step when SQL admission failed. Reuse both answers
                        # and finish its exact postimage, without a new round.
                        previous=latest['materials']['previous']
                        prior_findings=latest['materials']['prior_findings']
                        proposal_origin=latest['materials'].get('proposal_origin')
                        ordinal=latest['ordinal'];revision=latest['revision']
                    else:
                        if (oldroot/'candidate.json').exists():
                            previous=read_artifact(oldroot/'candidate.json')
                            prior_findings=list(previous['prior_findings'])
                            if has_approval:prior_findings=_all_findings(previous,read_artifact(oldroot/'approval.json'))
                            if not same:
                                if _summary_only_preimage_refresh(oldbundle,bundle):
                                    previous=None;prior_findings=[]
                                else:
                                    previous=_rebase_previous(request,oldbundle,bundle,previous,prior_findings=prior_findings)
                        elif latest['materials']['previous'] is not None:
                            previous=latest['materials']['previous']
                            prior_findings=latest['materials']['prior_findings']
                            if not same:
                                if _summary_only_preimage_refresh(oldbundle,bundle):
                                    previous=None;prior_findings=[]
                                else:
                                    previous=_rebase_previous(request,oldbundle,bundle,previous,prior_findings=prior_findings)
                        ordinal=latest['ordinal']+1 if latest['generation']==generation else 0
                        revision=has_approval and not read_artifact(oldroot/'approval.json')['approved']
                    for oldstep_id in unit['steps']:
                        # Resume authenticates this exact step's cached
                        # proposal/review below. It is not a new semantic
                        # attempt, even when it was itself a revision. Keep
                        # every earlier candidate in the progress comparison.
                        if replay_step and oldstep_id==latest['run_id']:continue
                        oldstep=store.get_cvp_group(oldstep_id)
                        path=Path(oldstep['materials']['step_root'])/'candidate.json'
                        if path.exists():
                            saved=read_artifact(path);verify(saved,domain.VERSION+'_candidate')
                            seen.add((oldstep['materials']['bundle']['artifact_sha256'],_semantic(saved)))
                while True:
                    step_root=unit_root/f'step-{ordinal:04d}'
                    materials={'bundle':bundle,'previous':previous,'prior_findings':prior_findings,
                               'step_root':str(step_root),'recall_root':str(recall_root),'index_root':str(index_root)}
                    if proposal_origin is not None:
                        materials['proposal_origin']=proposal_origin
                    if latest and latest['generation']==generation and latest['ordinal']==ordinal:
                        if latest['materials'].get('index_root') is None:
                            materials.pop('index_root')
                    step_id=persistence.prepare_step(store,parent_id,group_key=key,generation=generation,
                        ordinal=ordinal,materials=materials,revision=revision)
                    write_immutable(step_root/'materials.json',materials)
                    _guard(store,bundle)
                    if proposal_origin is None:
                        packet=domain.proposal_packet(request,bundle,previous=previous,prior_findings=prior_findings)
                        pa=adapter('proposal',request,bundle,packet,client=proposer)
                        raw,_,_=call(pa,packet,step_root/'proposal.json');response=validate_response('proposal',raw)
                        if response['result']=='context_insufficient':
                            newbundle=_expand(store,request,current,key,recall,bundle,response)
                            if previous:previous=_rebase_previous(request,bundle,newbundle,previous,prior_findings=prior_findings)
                            bundle=persistence.bind_task_constraints(store,parent_id,newbundle);ordinal+=1;revision=False;continue
                        candidate=domain.compile_candidate(request,bundle,response['candidate'],previous=previous,prior_findings=prior_findings)
                        proposal_path=step_root/'proposal.json'
                    else:
                        origin=proposal_origin
                        proposal_path=Path(origin['proposal_path'])
                        packet=domain.proposal_packet(request,origin['bundle'],previous=origin['previous'],
                            prior_findings=origin['prior_findings'])
                        pa=adapter('proposal',request,origin['bundle'],packet,client=proposer)
                        raw=read_saved_model_response(pa,packet,proposal_path)
                        candidate=previous
                        if candidate['material_sha256']!=bundle['artifact_sha256']:
                            raise ValueError('review-only candidate differs from expanded material')
                    from backend.pipeline import cvp_candidate_screening as screening
                    detail_recall=screening.effective_recall(recall,bundle,current,key)
                    missing=action_member_requests(bundle,detail_recall,candidate)
                    if missing:
                        if proposal_origin is not None:
                            raise ValueError('review-only candidate unexpectedly lacks member support')
                        # Complete the action's comparison material before
                        # Opus reviews the existing proposal's afterimage.
                        write_immutable(step_root/'candidate.json',candidate)
                        newbundle=_expand(store,request,current,key,recall,bundle,
                            {'member_requests':missing,'context_requests':[]})
                        newbundle=persistence.bind_task_constraints(store,parent_id,newbundle)
                        rebound=domain.rebind_candidate(request,bundle,newbundle,candidate,
                            prior_findings=prior_findings)
                        needs_followup=bool(rebound.get('summary_material_pending') or rebound['findings'])
                        def next_request_bytes(material,prior):
                            if needs_followup:
                                packet=domain.proposal_packet(request,material,previous=prior,
                                    prior_findings=prior_findings)
                                return adapter('proposal',request,material,packet,client=proposer).request_bytes(packet)
                            packet=domain.review_packet(request,material,prior,prior_findings=prior_findings)
                            return adapter('review',request,material,packet,client=reviewer).request_bytes(packet)
                        next_bytes=next_request_bytes(newbundle,rebound)
                        if next_bytes>request.budget.max_request_bytes and request.input.get('recall_profile')==STATEMENT_SCOPE_PROFILE:
                            target_claims={ref:cid for cid,ref in bundle['references']['claims'].items()}
                            component_claims={part['component_ref']:target_claims[part['claim_ref']]
                                              for part in candidate['component_plan']['components']}
                            applicable={component_claims[row['component_ref']]
                                        for row in candidate['component_dispositions']
                                        if row['disposition']=='connected' and row['target_ref'] in candidate['new_viewpoint_refs']}
                            measured_bundle=[newbundle,rebound]
                            def measure(narrowed,decisions):
                                selected_missing=action_member_requests(bundle,narrowed,candidate)
                                expanded=(_expand(store,request,current,key,recall,bundle,
                                    {'member_requests':selected_missing,'context_requests':[]})
                                    if selected_missing else bundle)
                                expanded=screening.with_screening(expanded,recall,decisions)
                                expanded=persistence.bind_task_constraints(store,parent_id,expanded)
                                prior=domain.rebind_candidate(request,bundle,expanded,candidate,
                                    prior_findings=prior_findings)
                                measured_bundle[:]=[expanded,prior]
                                return next_request_bytes(expanded,prior)
                            screening.screen_expansion_to_fit(store,current,key,recall,request,
                                reviewer=reviewer,screening_root=step_root/'screening',call=call,
                                measure=measure,applicable_claim_ids=applicable)
                            newbundle,rebound=measured_bundle
                        narrowed=screening.effective_recall(recall,newbundle,current,key)
                        if action_member_requests(newbundle,narrowed,rebound):
                            raise ValueError('screened review still lacks required member support')
                        proposal_origin=(None if needs_followup else
                            {'step_id':step_id,'proposal_path':str(proposal_path),
                             'bundle':bundle,'previous':previous,'prior_findings':prior_findings})
                        previous=rebound
                        bundle=newbundle
                        ordinal+=1;revision=False;continue
                    if candidate.get('summary_material_pending'):
                        # All source support is present, but the preliminary
                        # proposal has not supplied a reviewable summary yet.
                        # Keep this in the original propose budget.
                        write_immutable(step_root/'candidate.json',candidate)
                        previous=candidate;proposal_origin=None
                        ordinal+=1;revision=False;continue
                    digest=_semantic(candidate)
                    if revision and (bundle['artifact_sha256'],digest) in seen:raise ValueError('no semantic progress or oscillation; root cause investigation required')
                    write_immutable(step_root/'candidate.json',candidate)
                    if candidate['findings']:
                        # A proposal's own unresolved finding cannot receive a
                        # passing review verdict. Resolve it in the next proposal
                        # before asking Opus to review a complete afterimage.
                        seen.add((bundle['artifact_sha256'],digest))
                        previous=candidate;prior_findings=_all_findings(candidate,{'findings':[]});proposal_origin=None
                        ordinal+=1;revision=True;continue
                    # Freeze before the FIRST Opus call, even a rejection or
                    # unknown return. Later plans require explicit lineage.
                    write_immutable(step_root/'review-plan.json',candidate['component_plan'])
                    _guard(store,bundle)
                    packet=domain.review_packet(request,bundle,candidate,prior_findings=prior_findings)
                    ra=adapter('review',request,bundle,packet,client=reviewer)
                    raw_review,_,_=call(ra,packet,step_root/'review.json');review=validate_response('review',raw_review)
                    if review['result']=='context_insufficient':
                        newbundle=_expand(store,request,current,key,recall,bundle,review)
                        previous=domain.rebind_candidate(request,bundle,newbundle,candidate,prior_findings=prior_findings)
                        bundle=persistence.bind_task_constraints(store,parent_id,newbundle);proposal_origin=None
                        ordinal+=1;revision=False;continue
                    approval=domain.validate_review(request,bundle,candidate,review['review'],prior_findings=prior_findings)
                    write_immutable(step_root/'approval.json',approval)
                    if not approval['approved']:
                        previous=candidate;prior_findings=_all_findings(candidate,approval);proposal_origin=None
                        seen.add((bundle['artifact_sha256'],digest))
                        ordinal+=1;revision=True;continue
                    evidence={'round_id':step_id,'request':request.model_dump(mode='json'),'bundle':bundle,
                        'candidate':candidate,'approval':approval,'proposal_raw':raw,'review_raw':raw_review,
                        'previous':previous,'prior_findings':prior_findings,
                        **({'proposal_origin':proposal_origin} if proposal_origin is not None else {}),
                        'call_keys':{'gpt':str(proposal_path.relative_to(root)),
                                     'opus':str((step_root/'review.json').relative_to(root))},
                        'provenance':{'proposal_model':pa.model_id,'review_model':ra.model_id,
                            'proposal_artifact_sha256':read_artifact(proposal_path)['artifact_sha256'],
                            'review_artifact_sha256':read_artifact(step_root/'review.json')['artifact_sha256'],
                            'created_at':stable_decided_at(step_root)}}
                    package=validate_evidence(evidence);_guard(store,bundle)
                    commit_package(store,step_id,package=package,approval=binding(step_root/'review.json'),
                        authorization=request.authorization.model_dump(),backup={},sources=bundle['sources'],
                        read_pins={(r['collection'],r['object_id']):(r['revision'],r['content_sha256'],False) for r in bundle['records']},
                        reason='CVP2 exact final Opus candidate',actor=domain.VERSION,source_kind=domain.VERSION,
                        management_evidence=evidence)
                    readback,rpins=_readback(store,evidence)
                    persistence.complete_step(store,step_id,readback=readback,read_pins=rpins)
                    persistence.progress(store,parent_id,group_key=key,status='completed',step_id=step_id)
                    from backend.pipeline.viewpoint_route_cvp_worker import handoff_cvp_route_jobs
                    handoff_cvp_route_jobs(store,package,root/'route-queue-v2',step_root)
                    from backend.pipeline.cvp_management_index import write_committed_summary_proofs
                    if not domain.statement_recall(request):
                        approved_cards.extend(write_committed_summary_proofs(evidence,proposal_path,
                            step_root/'review.json',store.read_cvp_heads(),step_root/'summary-proofs'))
                    previous_index_root=index_root
                    break
            except (ValueError,RuntimeError,OSError) as exc:
                text=str(exc)
                status=('needs_upstream' if text.startswith('needs_upstream:') else
                    'superseded_input' if isinstance(exc,InputChanged) or 'superseded_input' in text else
                    'resource_hold' if any(s in text.lower() for s in ('budget','capacity','exceeds','unknown','uncertain','progress','oscillation'))
                    else 'engineering_required')
                persistence.progress(store,parent_id,group_key=key,status=status,detail=text)
        return store.get_cvp_group(parent_id)['result']
