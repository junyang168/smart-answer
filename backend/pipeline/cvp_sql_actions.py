"""Bounded revisions share the normal SQL execution/checkpoint boundary.

Content proposal/review contracts are reused; old prepare/run/apply/continuation
methods are not. No per-Claim wrapper, full Registry freeze, or ancestry replay.
"""
from __future__ import annotations

from pathlib import Path
import json
import time

from backend.api.canonical_repository.postgres_store import canonical_json,sha256_json
from backend.pipeline.cvp_sql_flow import binding,check_binding,pins,pin_rows,decoded_pins,commit_package,resume_reviewed_action
from backend.pipeline import source_reconciliation as content
from backend.pipeline.source_reconciliation_runner import Runner as ModelStages,body,read,write,validate_reviewed_records

SOURCE_SCHEMA='wang_source_sql_reconciliation_v2'
LINK_SCHEMA='wang_owner_sql_link_correction_v2'


def initialize_subscription_runtime(ports):
    # Restoring an applied SQL group can bypass normal proposal startup. All
    # live bounded branches still use the same local canonical identity proof.
    from backend.pipeline.source_reconciliation_runtime import ProductionPorts
    if isinstance(ports,ProductionPorts):
        import os
        from backend.pipeline.viewpoint_s201129_source_continuation import canonical_runtime
        os.environ['CODEX_EXECUTABLE']=canonical_runtime()['identity']['cli']['path']


def resume_bound_action(store,run_id,action_id):
    """All special cases consume the same SQL checkpoint/once authority."""
    saved=store.get_cvp_group(run_id)
    active=saved.get('reviewed',{}).get('evidence',{}).get('action',{})
    if active.get('action_id')==action_id:
        if saved['checkpoint']=='reviewed':return resume_reviewed_action(store,run_id)
        if saved['checkpoint'] in {'applied','audited'}:
            return {'status':'already_applied','action_id':action_id}
    if store.cvp_action_committed(run_id,action_id):
        return {'status':'already_applied','action_id':action_id}
    return None


def action_sources(materials):
    manifest=content.Manifest.model_validate(materials['manifest'])
    result = [b.model_dump() for b in (manifest.before_source,*manifest.current_sources,manifest.authorization,
                                    manifest.accepted_membership,*manifest.prior_updates)] + [s['file'] for s in materials.get('context_sources',[])]
    if manifest.viewpoint_scope_repair:
        context=materials['viewpoint_scope_context']
        result += [manifest.viewpoint_scope_repair.model_dump(),context['policy'],*context['reports']]
    elif manifest.claim_issue:
        context=materials['claim_issue_context']
        result += [manifest.claim_issue.model_dump(),context['policy'],*(i['report'] for i in context['issues'])]
    return result


def validate_source_package(package):
    """Exact actual proposal/Opus answer, scoped materials and permissions.

    This validates the material packet itself, not a prior/whole SQL snapshot.
    Current rows and consumer closure are checked inside apply_plan.
    """
    application=package['source_repair_application']
    material_binding=application['materials'];check_binding(material_binding)
    materials=body(read(Path(material_binding['path'])))
    if sha256_json(materials)!=application['materials_sha256']:
        raise ValueError('source action material identity drift')
    content.guard_files(materials)
    scope=materials.get('sql_scope')
    if not scope or scope['contract']!='wang_cvp_sql_transaction_v2' or scope['run_id']!=application['run_id']:
        raise ValueError('source action lacks exact SQL group/readset')
    # Shared pure content validator authenticates original model/prompt/once
    # reservation and the complete reviewed records. It does not rebuild freeze.
    validate_reviewed_records(package,materials,frozen_sha256=application['materials_sha256'])


def prepare_source_action(store,run_id,manifest,*,root):
    manifest=content.Manifest.model_validate(manifest)
    action_id=source_action_id(manifest)
    store.bind_cvp_execution_root(run_id,identity=action_id,root=root)
    saved=store.get_cvp_group(run_id)
    if not saved:raise ValueError('source action requires admitted SQL group')
    previous=store.get_cvp_action(run_id,action_id)
    if previous:
        check_binding(binding(root/'scoped-materials.json'))
        if body(read(root/'scoped-materials.json'))!=previous['materials']:
            raise ValueError('saved SQL source action materials differ')
        return previous
    started=time.perf_counter()
    if manifest.viewpoint_scope_repair:
        from backend.pipeline.cvp_scope_management import direction
        target=direction(manifest)
        keys=[('canonical_viewpoints',target['viewpoint_id']),('viewpoint_revisions',target['before_revision_id'])]
    elif manifest.claim_issue:
        context=json.loads(content.bound_bytes(manifest.claim_issue))
        # Source is unchanged. Starting reverse closure at its document would
        # incorrectly load every unrelated Claim in the same sermon.
        keys=[('claims',cid) for cid in context['claim_ids']]
    else:
        keys=[('source_documents',manifest.source_id)]
    keys.extend((p.collection,p.object_id) for p in manifest.permissions if not p.create)
    records=store.read_affected_context(keys)
    states={content.key(col,oid):row['payload'] for (col,oid),row in records.items() if not row.get('retired')}
    # The existing content compiler gets only the complete affected current
    # graph. Its before_records are scoped model evidence, not a Registry dump.
    materials=content.freeze(manifest,states)
    materials['sql_scope']={'contract':'wang_cvp_sql_transaction_v2','run_id':run_id,
                           'read_pins':pin_rows(pins(records)),'consumer_roots':[list(k) for k in keys]}
    if len(canonical_json(materials).encode())>64*1024**2 or time.perf_counter()-started>5:
        raise ValueError('bounded source materials exceed mechanical guard; no whole-store fallback')
    write(root/'scoped-materials.json',materials)
    return store.prepare_cvp_action(run_id,action_id=action_id,
        kind='cvp_scope_revision' if manifest.viewpoint_scope_repair else 'source_claim_revision',
        authorization=manifest.authorization.model_dump(),materials=materials)


def source_action_id(manifest):
    if manifest.viewpoint_scope_repair:
        from backend.pipeline.cvp_scope_management import direction
        value = direction(manifest)
        return 'CVPA-'+sha256_json({'kind':'viewpoint_scope_revision','viewpoint_id':value['viewpoint_id'],
                                  'before_revision_id':value['before_revision_id']})
    if manifest.claim_issue:
        from backend.pipeline.claim_issue_management import Issue
        context=__import__('json').loads(content.bound_bytes(manifest.claim_issue))
        return 'CVPA-'+sha256_json({'claim_issues':sorted(Issue.model_validate(i).issue_id for i in context['issues'])})
    return 'CVPA-'+sha256_json(manifest.model_dump(mode='json'))


class ScopedSourceModelStages(ModelStages):
    """Only the existing four model stages; no legacy run/prepare/SQL ports."""
    def __init__(self,root,ports,store,read_pins):
        self.root=Path(root);self.ports=ports;self.store=store;self.read_pins=read_pins

    def guard(self,materials,*,committed=False):
        content.guard_files(materials)
        with self.store.connect() as conn,conn.cursor() as cursor:
            self.store._assert_transaction_read_pins(cursor,self.read_pins)

    def call(self,stage,packet):
        if stage in {'proposal','correction'} and not (self.root/(stage+'.raw.json')).exists():
            from backend.pipeline.source_reconciliation_runtime import ProductionPorts
            if isinstance(self.ports,ProductionPorts):
                from backend.pipeline.viewpoint_s201129_source_continuation import canonical_runtime
                import os
                os.environ['CODEX_EXECUTABLE']=canonical_runtime()['identity']['cli']['path']
        return super().call(stage,packet)

    def prepare(self,*args,**kwargs):raise RuntimeError('legacy source prepare is retired')
    def run(self,*args,**kwargs):raise RuntimeError('legacy source execution is retired')


def execute_source_action(store,run_id,manifest,*,root,ports):
    manifest=content.Manifest.model_validate(manifest).model_dump(mode='json')
    root=root/source_action_id(content.Manifest.model_validate(manifest))
    action=prepare_source_action(store,run_id,manifest,root=root)
    materials=action['materials'];read_pins=decoded_pins(materials['sql_scope']['read_pins'])
    saved=store.get_cvp_group(run_id)
    resumed=resume_bound_action(store,run_id,action['action_id'])
    if resumed:return resumed
    issue_context=materials.get('claim_issue_context')
    if issue_context:
        from backend.pipeline.claim_issue_management import Issue
        store.bind_claim_issue_action([Issue.model_validate(i).issue_id for i in issue_context['issues']],
                                     run_id=run_id,action_id=action['action_id'])
    # Resume a committed source action directly from SQL. Neither content
    # stages nor apply are replayed. Audit/coverage continue in the group flow.
    initialize_subscription_runtime(ports)
    stages=ScopedSourceModelStages(root,ports,store,read_pins)
    proposal,raw=stages.reviewed(materials)
    if manifest.get('viewpoint_scope_repair') and not proposal.records:
        raise ValueError('scope assessment retained existing scope; unresolved original finding needs direction, no empty commit/retry loop')
    package=content.compile_package(materials,proposal,raw,frozen_sha256=action['materials_sha256'])
    package['source_repair_application']={'schema_version':SOURCE_SCHEMA,'run_root':str(Path(root).resolve()),
        'run_id':run_id,'materials':binding(root/'scoped-materials.json'),
        'materials_sha256':action['materials_sha256'],'terminal_stage':raw['stage'],
        'review_sha256':raw['artifact_sha256'],'proposal_sha256':sha256_json(proposal.model_dump())}
    validate_source_package(package)
    from backend.pipeline.cvp_sql_flow import verified_group_backup
    backup=verified_group_backup(root,package,ports) if issue_context or manifest.get('viewpoint_scope_repair') else ports.backup(root)
    original=store.get_cvp_group(run_id).get('reviewed',{}).get('evidence',{})
    approval={'terminal_review':binding(root/(raw['stage']+'.raw.json'))}
    for stage in ('initial_review','final_review','final_review_input_repair'):
        proof=root/(stage+'.raw.program-binding.json')
        if proof.exists():approval[stage+'_program_binding']=binding(proof)
    views=set(original.get('viewpoint_ids',[]))
    if issue_context or manifest.get('viewpoint_scope_repair'):
        views.update(p['viewpoint_id'] for p in content.index(materials['before_records']).values() if 'viewpoint_id' in p)
    pending=content.Manifest.model_validate(materials['manifest']).deferred_repair.claim_ids if materials['manifest'].get('deferred_repair') else []
    if issue_context:
        # Uncommitted original model materials include the changed Claim. They
        # are retained as history, never re-labelled approved for the new cut.
        pending=list(saved['claim_ids']) if not original else sorted(set(original.get('pending_cvp_claim_ids',[]))|set(issue_context['claim_ids']))
    return commit_package(store,run_id,package=package,
        approval=approval,
        authorization=action['authorization'],backup=backup,sources=action_sources(materials),
        read_pins=read_pins,reason='approved CVP management scope enrichment' if manifest.get('viewpoint_scope_repair') else 'approved bounded source/Claim revision',actor='owner-authorized-pipeline',
        source_kind=SOURCE_SCHEMA,viewpoint_ids=sorted(views),
        disposition=original.get('disposition'),action=action,
        pending_cvp_claim_ids=pending)


def merge_dispositions(old,new):
    """Keep every original Claim; do not pretend two approvals are one raw."""
    if not old:return new
    claims={r['claim_id']:r for r in old['claims']}
    claims.update({r['claim_id']:r for r in new['claims']})
    return {'schema_version':'cvp_sql_component_disposition.v2','claims':[claims[k] for k in sorted(claims)],
            'source_disposition_sha256s':[sha256_json(old),sha256_json(new)]}


def validated_sufficient_deferred(root):
    """A source diagnosis is not CVP approval; it only selects once-only work."""
    from backend.pipeline.viewpoint_deferred_source_check import Findings,PROMPT,validate_findings
    from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact,read_saved_model_response,subscription_client
    packet=read_artifact(root/'packet.json');handoff=read_artifact(root/'handoff.json')
    if handoff['packet_sha256']!=packet['artifact_sha256']:raise ValueError('source investigation identity drift')
    findings=[]
    for label,provider,model in [('gpt','codex','gpt-6.1-sol'),('opus','claude','claude-opus-5-5')]:
        adapter=StructuredJsonReviewerAdapter(client=subscription_client(provider,model,'high'),prompt=PROMPT,
            response_model=Findings,schema_name='wang_deferred_source_findings_v1')
        findings.append(validate_findings(read_saved_model_response(adapter,packet,root/f'raw-{label}.json'),packet))
    ids=[]
    for item in packet['items']:
        left,right=(f[item['item_id']] for f in findings)
        actions=[a for a in handoff['actions'] if a['item_id']==item['item_id']]
        if (len(actions)!=1 or actions[0]['gpt']!=left or actions[0]['opus']!=right
                or left['diagnosis']!='existing_evidence_sufficient' or right['diagnosis']!='existing_evidence_sufficient'
                or actions[0]['next_action']!='reviewed_cvp_repair_required'):
            raise ValueError('true source/Claim ambiguity or missing bound evidence requires bounded owner direction')
        ids.append(item['claim_id'])
    return sorted(set(ids)),binding(root/'handoff.json')


def execute_coverage_action(store,run_id,definition,group,*,root,ports,investigation_root=None):
    from backend.pipeline.cvp_sql_flow import prepare_group,reviewed_cvp_package,NORMAL_COLLECTIONS
    saved=store.get_cvp_group(run_id);old=saved['reviewed']['evidence']
    if investigation_root is not None:
        ids,investigation=validated_sufficient_deferred(investigation_root)
        check_binding(definition['cvp_policy'])
        from backend.pipeline.viewpoint_resolution_runtime import read_artifact
        if read_artifact(definition['cvp_policy']['path'])['coverage_contract_version']!='wang_cvp_required_coverage_v1':
            raise ValueError('normal flow lacks standing required-coverage authority')
    else:
        ids=old['pending_cvp_claim_ids'];investigation=None
        if old.get('action',{}).get('kind')!='source_claim_revision':
            raise ValueError('required coverage must follow the approved bounded Claim revision')
    if not ids or not set(ids)<=set(saved['claim_ids']):raise ValueError('coverage changes original membership')
    action_id='CVPA-'+sha256_json({'prior':saved['reviewed_sha256'],'kind':'required_cvp_coverage','claims':ids})
    root=root/action_id
    store.bind_cvp_execution_root(run_id,identity=action_id,root=root)
    previous=store.get_cvp_action(run_id,action_id)
    materials=previous['materials'] if previous else prepare_group(store,definition,{**group,'claim_ids':ids})
    if not previous and old.get('action',{}).get('materials',{}).get('claim_issue_context'):
        # A retained Claim is not a newly approved normal CVP proposal. Its
        # joint management review is explicit input to this new coverage stage;
        # never silently repeat/relabel the former deferred request.
        materials['claim_management_resolution']={
            'prior_reviewed_sha256':saved['reviewed_sha256'],
            'management_action_id':old['action']['action_id'],
            'approved_package_sha256':old['package']['sha256'],
            'terminal_review':old['approval']['terminal_review'],
            'issues':old['action']['materials']['claim_issue_context']['issues']}
    action=store.prepare_cvp_action(run_id,action_id=action_id,kind='required_cvp_coverage',
                                    authorization={**old['authorization'],**({'source_investigation':investigation} if investigation else {})},materials=materials)
    if saved['checkpoint']=='reviewed':return resume_reviewed_action(store,run_id)
    initialize_subscription_runtime(ports)
    package,approval,disposition=reviewed_cvp_package(root/'model-stages',materials,store,require_resolved=True)
    plan=store.plan_package(package,transactional=True,source_kind='cvp_batch_resolution')
    if any(op.operation!='create' or op.collection not in NORMAL_COLLECTIONS for op in plan.operations):
        raise ValueError('required coverage may create CVPs/links only; no existing content revision')
    if any(link['claim_id'] not in ids for link in package.get('viewpoint_claim_links',[])):
        raise ValueError('required coverage added a foreign Claim')
    views=sorted(set(old['viewpoint_ids'])|{l['viewpoint_id'] for l in package.get('viewpoint_claim_links',[])})
    from backend.pipeline.cvp_sql_flow import verified_group_backup
    return commit_package(store,run_id,package=package,approval=approval,authorization=old['authorization'],
        backup=verified_group_backup(root,package,ports),
        sources=materials['sources'],read_pins=decoded_pins(materials['read_pins']),
        reason='required CVP coverage after bounded Claim revision',actor=definition['authorized_by'],
        viewpoint_ids=views,disposition=merge_dispositions(old.get('disposition'),disposition),action=action)


def execute_supports_action(store,run_id,adjudication_root,*,root,ports):
    from backend.pipeline.viewpoint_adjudication_history import validated_history
    from backend.pipeline.viewpoint_audit_link_repair import select_repairs,link_review_adapter,package_for,POLICY,PROMPT
    from backend.pipeline.viewpoint_authorized_source_repair_357 import Review,review_call
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact,subscription_client,write_immutable
    packet,_=validated_history(adjudication_root)
    report=read_artifact(adjudication_root/'gpt-final-handoff.json')
    selected=select_repairs(packet,report)  # rejects l3/unresolved/non-equivalent/new scope
    policy=read_artifact(POLICY)
    if (policy['allowed_after_type']!='supports' or not policy['require_dual_model_postimage_review']
            or policy['maximum_corrections_per_adjudication']!=1):raise ValueError('standing narrow policy drift')
    ids=[i['claim']['claim_id'] for i in selected]
    action_id='CVPA-'+sha256_json({'adjudication':packet['artifact_sha256'],'handoff':report['artifact_sha256'],
                                 'policy':binding(POLICY),'links':[i['before_link']['viewpoint_claim_link_id'] for i in selected]})
    root=root/action_id
    store.bind_cvp_execution_root(run_id,identity=action_id,root=root)
    resumed=resume_bound_action(store,run_id,action_id)
    if resumed:return resumed
    records=store.read_affected_context([('viewpoint_claim_links',i['before_link']['viewpoint_claim_link_id']) for i in selected])
    states={k:r['payload'] for k,r in records.items() if not r['retired']}
    for item in selected:
        old=item['before_link'];cv=old['viewpoint_id']
        if states['viewpoint_claim_links',old['viewpoint_claim_link_id']]!=old or states['claims',old['claim_id']]!=item['claim']:
            raise ValueError('actual upheld link/Claim changed before bounded review')
        item['viewpoint_revision']=states['viewpoint_revisions',old['validated_against_viewpoint_revision_id']]
        item['adjacent_links']=[p for (c,_),p in states.items() if c=='viewpoint_claim_links'
                                and p.get('effective_state')=='active' and p['viewpoint_id']==cv]
        item['adjacent_claims']=[states['claims',cid] for cid in sorted({p['claim_id'] for p in item['adjacent_links']})]
        if any(p['claim_id']==old['claim_id'] and p['link_type']=='supports' for p in item['adjacent_links']):
            raise ValueError('existing supports edge requires separately approved merge')
        item['dependent_records']=[p for (c,_),p in states.items() if c in
            {'viewpoint_structures','argument_routes','argument_route_attestations','viewpoint_relations'}
            and (cv in canonical_json(p) or old['viewpoint_claim_link_id'] in canonical_json(p))]
    repair={'schema_version':'wang_audit_link_repair_packet_v1','targets':selected,
            'adjudication_packet_sha256':packet['artifact_sha256'],'adjudication_handoff':report,
            'policy':policy,'claim_source_viewpoint_text_writes':0}
    write_immutable(root/'packet.json',repair)
    action=store.prepare_cvp_action(run_id,action_id=action_id,kind='audit_supports_correction',
        authorization=binding(POLICY),materials={'packet':repair,'read_pins':pin_rows(pins(records))})
    reviews={}
    for label,provider,model in [('gpt','codex','gpt-6.1-sol'),('opus','claude','claude-opus-5-5')]:
        if provider=='codex' and not (root/f'raw-{label}.json').exists():
            import os
            from backend.pipeline.viewpoint_s201129_source_continuation import canonical_runtime
            os.environ['CODEX_EXECUTABLE']=canonical_runtime()['identity']['cli']['path']
        adapter=link_review_adapter(subscription_client(provider,model,'high'),PROMPT,contextual_semantics=True)
        adapter.max_request_bytes=500000
        response,_,_=review_call(adapter,repair,root,label)
        result=Review.model_validate(response)
        if len(result.verdicts)!=len(ids) or {v.claim_id for v in result.verdicts}!=set(ids) or any(not v.approved or v.problems for v in result.verdicts):
            raise ValueError('bounded supports rejected; no second review or automatic content expansion')
        reviews[label]=result.model_dump(mode='json')
    package=package_for(states,repair,reviews,root)
    plan=store.plan_package(package,source_kind='cvp_batch_resolution',transactional=True)
    expected={i['before_link']['viewpoint_claim_link_id'] for i in selected}
    if any(op.collection not in {'viewpoint_claim_links','viewpoint_identity_candidates','viewpoint_identity_decisions'}
           or (op.collection=='viewpoint_claim_links' and (op.operation!='update' or op.object_id not in expected))
           or (op.collection!='viewpoint_claim_links' and op.operation!='create') for op in plan.operations):
        raise ValueError('bounded supports package exceeds exact authorized delta')
    saved=store.get_cvp_group(run_id)['reviewed']['evidence']
    sources={s['path']:{'path':s['path'],'file_sha256':s['file_sha256']} for i in selected for s in i['sources']}
    return commit_package(store,run_id,package=package,
        approval={label:binding(root/f'raw-{label}.json') for label in reviews},authorization=binding(POLICY),
        backup=ports.backup(root),sources=list(sources.values()),read_pins=pins(records),
        reason='standing once own-source upheld equivalent-to-supports correction',actor='owner-authorized-pipeline',
        viewpoint_ids=saved['viewpoint_ids'],disposition=saved.get('disposition'),action=action)


def validate_owner_link_direction(direction,packet,run_id):
    wanted={'schema_version':'cvp_sql_owner_link_direction.v2','run_id':run_id,
            'link_id':packet['before_link']['viewpoint_claim_link_id'],
            'adjudication_packet_sha256':packet['adjudication_packet_sha256'],
            'before_link_sha256':sha256_json(packet['before_link']),
            'after_link_sha256':sha256_json(packet['after_link'])}
    if any(direction.get(k)!=v for k,v in wanted.items()) or not direction.get('authorization_quote'):
        raise ValueError('owner direction does not bind exact SQL action/postimage')
    if direction.get('human_content_approval') is not False or direction.get('automatic_future_repairs_authorized') is not False:
        raise ValueError('execution direction is not future content approval')


def validate_owner_link_package(package):
    """Exact bound direction and saved single Opus answer; no parent rebuild."""
    from backend.pipeline.owner_approved_link_correction import package_for,review_raw
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    app=package['source_repair_application']
    if app['schema_version']!=LINK_SCHEMA:raise ValueError('SQL link contract differs')
    check_binding(app['packet']);check_binding(app['authorization'])
    packet=read_artifact(app['packet']['path'])
    raw=review_raw(Path(app['run_root']),packet)
    expected=package_for(packet,raw,Path(app['run_root']))
    expected['viewpoint_identity_decisions'][0]['policy_version']=LINK_SCHEMA
    expected['source_repair_application']=app
    direction=read_artifact(app['authorization']['path'])
    validate_owner_link_direction(direction,packet,app['run_id'])
    if package!=expected:raise ValueError('SQL link package differs from actual reviewed three-record delta')


def execute_owner_link_action(store,run_id,adjudication_root,*,link_id,authorization,root,ports):
    from backend.pipeline.owner_approved_link_correction import target_postimage,validate_target,package_for,review_raw,VERSION
    from backend.pipeline.viewpoint_adjudication_history import validated_history
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact,write_immutable
    check_binding(authorization)
    direction=read_artifact(authorization['path'])
    action_id='CVPA-'+sha256_json({'kind':'owner_link_correction','authorization':authorization})
    root=root/action_id
    store.bind_cvp_execution_root(run_id,identity=action_id,root=root)
    resumed=resume_bound_action(store,run_id,action_id)
    if resumed:return resumed
    saved=store.get_cvp_group(run_id);previous=store.get_cvp_action(run_id,action_id)
    if previous:
        material=previous['materials'];packet=material['packet'];read_pins=decoded_pins(material['read_pins'])
    else:
        adjudication,findings=validated_history(Path(adjudication_root))
        selected=[i for i in adjudication['items'] if any(l.get('viewpoint_claim_link_id')==link_id for l in i.get('connections',[]))]
        if len(selected)!=1 or findings[selected[0]['item_id']]['verdict']!='audit_objection_upheld':
            raise ValueError('owner link direction requires a unique actual upheld target')
        item=selected[0]
        connections=[l for l in item['connections'] if l['viewpoint_claim_link_id']==link_id]
        if len(connections)!=1:raise ValueError('ambiguous owner link target')
        original_link=connections[0]
        records=store.read_cvp_context([item['claim']['claim_id']],viewpoint_ids=[original_link['viewpoint_id']])
        states={content.key(*k):r['payload'] for k,r in records.items() if not r['retired']}
        old=states[content.key('viewpoint_claim_links',link_id)]
        claim=states[content.key('claims',old['claim_id'])]
        if old!=original_link or claim!=item['claim']:
            raise ValueError('current link/Claim differs from owner-approved target')
        after=target_postimage(old,claim,states,'equivalent_full')
        packet={'before_link':old,'after_link':after,'item':item,
                'context_records':[{'collection':content.split_key(k)[0],'object_id':content.split_key(k)[1],'payload':p} for k,p in sorted(states.items())],
                'sources':item['sources'],'adjudication_packet_sha256':adjudication['artifact_sha256'],
                'authorization':{**direction,'schema_version':VERSION}}
        validate_target(packet)
        material={'packet':packet,'read_pins':pin_rows(pins(records))};read_pins=pins(records)
    packet=json.loads(canonical_json(packet))
    validate_owner_link_direction(direction,packet,run_id)
    action=store.prepare_cvp_action(run_id,action_id=action_id,kind='owner_link_correction',authorization=authorization,materials=material)
    write_immutable(root/'packet.json',packet)
    stages=ScopedSourceModelStages(root,ports,store,read_pins)
    for source in packet['sources']:check_binding({'path':source['path'],'file_sha256':source['file_sha256']})
    with store.connect() as conn,conn.cursor() as cursor:store._assert_transaction_read_pins(cursor,read_pins)
    stages.call('initial_review',packet)
    raw=review_raw(root,packet)
    package=package_for(packet,raw,root)
    package['viewpoint_identity_decisions'][0]['policy_version']=LINK_SCHEMA
    package['source_repair_application']={'schema_version':LINK_SCHEMA,'run_id':run_id,'run_root':str(root.resolve()),
        'packet':binding(root/'packet.json'),'authorization':authorization}
    validate_owner_link_package(package)
    original=saved.get('reviewed',{}).get('evidence') or saved.get('import_certificate',{})
    return commit_package(store,run_id,package=package,approval={'opus':binding(root/'initial_review.raw.json')},
        authorization=authorization,backup=ports.backup(root),sources=[{'path':s['path'],'file_sha256':s['file_sha256']} for s in packet['sources']],
        read_pins=read_pins,reason='owner-authorized exact full-Claim link classification',actor='owner-authorized-pipeline',
        source_kind=LINK_SCHEMA,viewpoint_ids=original['viewpoint_ids'],disposition=original.get('disposition'),action=action)
