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


def read_source_manifest(path):
    """Accept plain manifests or verify the existing immutable artifact envelope."""
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    value=read_artifact(path)
    return body(read(Path(path))) if 'artifact_sha256' in value else value


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
        if context.get('scope_amendment'):result.append(context['scope_amendment'])
    if manifest.anchor_qualification:result.append(manifest.anchor_qualification.model_dump())
    if manifest.evidence_binding_repair:result.append(manifest.evidence_binding_repair.model_dump())
    return result


def validate_source_package(package):
    """Exact actual proposal/Opus answer, scoped materials and permissions.

    This validates the material packet itself, not a prior/whole SQL snapshot.
    Current rows and consumer closure are checked inside apply_plan.
    """
    application=package['source_repair_application']
    if application.get('source_write_slice'):
        from backend.pipeline.claim_source_write_slice import validate_package
        return validate_package(package)
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
    templates={}
    proof=application.get('identity_template_read_pins')
    if proof:
        from backend.api.canonical_repository.postgres_store import record_content_sha
        check_binding(proof);receipt=body(read(Path(proof['path'])))
        if (not materials['manifest'].get('viewpoint_scope_repair')
                or receipt.get('schema_version')!='wang_cvp_scope_identity_template_read_pins_v1'
                or receipt.get('materials_sha256')!=application['materials_sha256']
                or receipt.get('terminal_review_sha256')!=application['review_sha256']):
            raise ValueError('scope template authority drift')
        rp=decoded_pins(receipt['read_pins'])
        for row in receipt['templates']:
            k=(row['collection'],row['object_id']);p=row['payload']
            if (content.key(*k) in templates or k[0]!='viewpoint_identity_candidates' or k not in rp
                    or p.get('identity_candidate_id')!=k[1] or record_content_sha(p)!=rp[k][1]
                    or p.get('review_status') in {'human_hold','human_review_required','withheld_unreviewed','rejected','superseded'}):
                raise ValueError('scope template payload/pin conflict')
            templates[content.key(*k)]=p
        if len(templates)!=len(rp):raise ValueError('scope template denominator drift')
    validate_reviewed_records(package,materials,frozen_sha256=application['materials_sha256'],provenance_templates=templates)
    from backend.pipeline.claim_evidence_completion import package_withdrawals
    return package_withdrawals(package,materials)


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
    amendment=None
    if manifest.evidence_binding_repair:
        d=json.loads(content.bound_bytes(manifest.evidence_binding_repair))
        keys=[('evidence_steps',d['evidence_id'])]
    elif manifest.anchor_qualification:
        d=json.loads(content.bound_bytes(manifest.anchor_qualification))
        keys=[('claims',d['claim_id']),('evidence_steps',d['evidence_id'])]
    elif manifest.viewpoint_scope_repair:
        from backend.pipeline.cvp_scope_management import direction
        target=direction(manifest)
        keys=[('canonical_viewpoints',target['viewpoint_id']),('viewpoint_revisions',target['before_revision_id'])]
    elif manifest.claim_issue:
        context=json.loads(content.bound_bytes(manifest.claim_issue))
        # Source is unchanged. Starting reverse closure at its document would
        # incorrectly load every unrelated Claim in the same sermon.
        keys=[('claims',cid) for cid in context['claim_ids']]
        keys.extend((r['historical_source_pin']['collection'], r['historical_source_pin']['object_id'])
                    for r in context.get('retained_occurrence_context', []))
        if context.get('scope_amendment'):
            from backend.pipeline.claim_issue_rescope import validate_direction,amendment_records
            direction=json.loads(content.bound_bytes(context['scope_amendment']))
            # Indexed current pins/consumer checks below establish this
            # material generation; the once reservation validates the SQL
            # parent/action before any model call. No duplicate SQL coldstart.
            original,_=validate_direction(direction)
            amendment=amendment_records(store,direction,original)
    else:
        keys=[('source_documents',manifest.source_id)]
    keys.extend((p.collection,p.object_id) for p in manifest.permissions if not p.create)
    records=amendment if amendment is not None else store.read_affected_context(keys)
    states={content.key(col,oid):row['payload'] for (col,oid),row in records.items() if not row.get('retired')}
    # The existing content compiler gets only the complete affected current
    # graph. Its before_records are scoped model evidence, not a Registry dump.
    materials=content.freeze(manifest,states)
    from backend.pipeline.claim_owner_revision import attach_material
    attach_material(store,saved,materials,root)
    materials['sql_scope']={'contract':'wang_cvp_sql_transaction_v2','run_id':run_id,
                           'read_pins':pin_rows(pins(records)),'consumer_roots':[list(k) for k in keys]}
    if len(canonical_json(materials).encode())>64*1024**2 or time.perf_counter()-started>5:
        raise ValueError('bounded source materials exceed mechanical guard; no whole-store fallback')
    write(root/'scoped-materials.json',materials)
    return store.prepare_cvp_action(run_id,action_id=action_id,
        kind='cvp_scope_revision' if manifest.viewpoint_scope_repair else 'source_claim_revision',
        authorization=manifest.authorization.model_dump(),materials=materials)


def source_action_id(manifest):
    if manifest.evidence_binding_repair:
        value=json.loads(content.bound_bytes(manifest.evidence_binding_repair))
        return 'CVPA-'+sha256_json({'kind':'eligible_evidence_binding','evidence_id':value['evidence_id'],
                                    'before_pin':value['before_pin'],'source_id':manifest.source_id})
    if manifest.viewpoint_scope_repair:
        from backend.pipeline.cvp_scope_management import direction
        value = direction(manifest)
        return 'CVPA-'+sha256_json({'kind':'viewpoint_scope_revision','viewpoint_id':value['viewpoint_id'],
                                  'before_revision_id':value['before_revision_id']})
    if manifest.claim_issue:
        from backend.pipeline.claim_issue_management import Issue
        context=__import__('json').loads(content.bound_bytes(manifest.claim_issue))
        if context.get('scope_amendment'):
            direction=json.loads(content.bound_bytes(context['scope_amendment']))
            return 'CVPA-'+sha256_json({'kind':'claim_issue_scope_amendment_once','parent':direction['old_action_id']})
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


def execute_source_action(store,run_id,manifest,*,root,ports,claim_first=False):
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
        if issue_context.get('scope_amendment'):
            store.bind_claim_issue_scope_amendment([Issue.model_validate(i).issue_id for i in issue_context['issues']],
                run_id=run_id,action_id=action['action_id'],authorization=issue_context['scope_amendment'])
        else:
            store.bind_claim_issue_action([Issue.model_validate(i).issue_id for i in issue_context['issues']],
                                         run_id=run_id,action_id=action['action_id'])
    # Resume a committed source action directly from SQL. Neither content
    # stages nor apply are replayed. Audit/coverage continue in the group flow.
    initialize_subscription_runtime(ports)
    stages=ScopedSourceModelStages(root,ports,store,read_pins)
    stages.claim_first=claim_first
    proposal,raw=stages.reviewed(materials)
    if manifest.get('viewpoint_scope_repair') and not proposal.records:
        raise ValueError('scope assessment retained existing scope; unresolved original finding needs direction, no empty commit/retry loop')
    templates={}
    template_proof=None
    if manifest.get('viewpoint_scope_repair'):
        from backend.pipeline.viewpoint_resolution_runtime import write_immutable
        before=content.index(materials['before_records'])
        records={tuple(k.split('/',1)):{'payload':p,'revision':read_pins[tuple(k.split('/',1))][0],
                 'content_sha256':read_pins[tuple(k.split('/',1))][1],
                 'retired':read_pins[tuple(k.split('/',1))][2]} for k,p in before.items()}
        selected=[]
        for k in content.index(proposal.records):
            if not k.startswith('viewpoint_claim_links/'):continue
            old=before[k]
            decision=before[content.key('viewpoint_identity_decisions',old['decision_id'])]
            if content.key('viewpoint_identity_candidates',decision['identity_candidate_id']) not in before:
                selected.append({'before_link':old})
        extra=supports_identity_dependencies(store,records,selected)
        templates={content.key(*k):r['payload'] for k,r in extra.items()}
        for k,pin in pins(extra).items():
            if k in read_pins and tuple(read_pins[k])!=tuple(pin):
                raise ValueError('scope forward template readpin drift')
            read_pins[k]=pin
        receipt={
            'schema_version':'wang_cvp_scope_identity_template_read_pins_v1',
            'action_id':action['action_id'],'materials_sha256':action['materials_sha256'],
            'terminal_review_sha256':raw['artifact_sha256'],
            'read_pins':pin_rows(pins(extra)),
            'templates':[{'collection':k[0],'object_id':k[1],'payload':r['payload']} for k,r in sorted(extra.items())],
            'semantic_review_input_changed':False}
        write_immutable(root/'identity-template-read-pins.json',{**receipt,'artifact_sha256':sha256_json(receipt)})
        template_proof=binding(root/'identity-template-read-pins.json')
    package=content.compile_package(materials,proposal,raw,frozen_sha256=action['materials_sha256'],claim_first=claim_first,
                                    provenance_templates=templates)
    from backend.pipeline.claim_owner_revision import validate_joint
    joint_owner_claim=validate_joint(materials,proposal,package)
    if (root/'membership-resolution.json').exists():
        from backend.pipeline.cvp_membership_management import append_joint_package
        from backend.pipeline.source_reconciliation_runner import effective_proposal, validate_raw
        correction_packet=body(read(root/'correction.packet.json'))
        correction=validate_raw(root,'correction',correction_packet)
        package=append_joint_package(package,root,materials,effective_proposal(materials,correction['response']),correction)
    package['source_repair_application']={'schema_version':SOURCE_SCHEMA,'run_root':str(Path(root).resolve()),
        'run_id':run_id,'materials':binding(root/'scoped-materials.json'),
        'materials_sha256':action['materials_sha256'],'terminal_stage':raw['stage'],
        'review_sha256':raw['artifact_sha256'],'proposal_sha256':sha256_json(proposal.model_dump())}
    from backend.pipeline.claim_evidence_completion import direction_for
    completion=direction_for(materials.get('claim_issue_context',{}))
    if completion:
        package['source_repair_application']['knowledge_relation_withdrawals']=completion['withdraw_relation_ids']
    if template_proof:package['source_repair_application']['identity_template_read_pins']=template_proof
    validate_source_package(package)
    from backend.pipeline.cvp_sql_flow import verified_group_backup
    backup=verified_group_backup(root,package,ports) if issue_context or manifest.get('viewpoint_scope_repair') or manifest.get('anchor_qualification') or manifest.get('evidence_binding_repair') else ports.backup(root)
    original=store.get_cvp_group(run_id).get('reviewed',{}).get('evidence',{})
    approval={**(original.get('approval',{}) if joint_owner_claim else {}),
              'terminal_review':binding(root/(raw['stage']+'.raw.json'))}
    if template_proof:approval['scope_identity_template_read_pins']=template_proof
    if joint_owner_claim:
        approval['owner_claim_completion']=materials['owner_claim_completion']
        if materials.get('prior_cvp_management'):
            approval['prior_cvp_management']=materials['prior_cvp_management']
    if (root/'membership-resolution.json').exists():
        approval['membership_management']=binding(root/'membership-resolution.json')
    if raw['stage']=='normalization_recovery_review':
        from backend.pipeline.source_normalization_recovery import NAME
        approval['normalization_engineering_recovery']=binding(root/NAME)
    for stage in ('initial_review','final_review','final_review_input_repair','normalization_recovery_review'):
        proof=root/(stage+'.raw.program-binding.json')
        if proof.exists():approval[stage+'_program_binding']=binding(proof)
    views=set(original.get('viewpoint_ids',[]))
    if issue_context or manifest.get('viewpoint_scope_repair') or manifest.get('anchor_qualification') or manifest.get('evidence_binding_repair'):
        views.update(p['viewpoint_id'] for p in content.index(materials['before_records']).values() if 'viewpoint_id' in p)
    pending=content.Manifest.model_validate(materials['manifest']).deferred_repair.claim_ids if materials['manifest'].get('deferred_repair') else []
    if manifest.get('anchor_qualification'):
        d=json.loads(content.bound_bytes(manifest['anchor_qualification']))
        pending=sorted(set(saved['claim_ids'])|set(original.get('pending_cvp_claim_ids',[]))|{d['claim_id']})
    if manifest.get('evidence_binding_repair'):
        d=json.loads(content.bound_bytes(manifest['evidence_binding_repair']))
        pending=sorted(set(saved['claim_ids'])|set(original.get('pending_cvp_claim_ids',[]))|set(d['claim_ids']))
    if issue_context:
        # Uncommitted original model materials include the changed Claim. They
        # are retained as history, never re-labelled approved for the new cut.
        pending=list(saved['claim_ids']) if not original else sorted(set(original.get('pending_cvp_claim_ids',[]))|set(issue_context['claim_ids']))
    if joint_owner_claim:
        # Actual complete joint proposal/Opus reviewed every existing active
        # connection against the new Claim. This is not create-only coverage.
        # Other already-pending Claims are never cleared by this repair.
        pending=sorted(set(original.get('pending_cvp_claim_ids',[]))-set(issue_context['claim_ids']))
    return commit_package(store,run_id,package=package,
        approval=approval,
        authorization=action['authorization'],backup=backup,sources=action_sources(materials),
        read_pins=read_pins,reason='approved CVP management scope enrichment' if manifest.get('viewpoint_scope_repair') else 'approved bounded source/Claim revision',actor='owner-authorized-pipeline',
        source_kind=SOURCE_SCHEMA,viewpoint_ids=sorted(views),
        disposition=original.get('disposition'),action=action,
        pending_cvp_claim_ids=pending,
        pending_cvp_management_record_keys=original.get('pending_cvp_management_record_keys',[]) if joint_owner_claim or manifest.get('anchor_qualification') or manifest.get('evidence_binding_repair') else ())


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
    from backend.pipeline.claim_evidence_binding import coverage_context as evidence_coverage_context
    sequential=evidence_coverage_context(store,saved,group) if investigation_root is None else None
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
    if sequential:ids=sequential['coverage_claim_ids']
    allowed=set(sequential['coverage_claim_ids']) if sequential else set(saved['claim_ids'])
    if not ids or (set(ids)!=allowed if sequential else not set(ids)<=allowed):
        raise ValueError('coverage changes original membership or audited dependency scope')
    action_id='CVPA-'+sha256_json({'prior':saved['reviewed_sha256'],'kind':'required_cvp_coverage','claims':ids})
    root=root/action_id
    store.bind_cvp_execution_root(run_id,identity=action_id,root=root)
    previous=store.get_cvp_action(run_id,action_id)
    materials=previous['materials'] if previous else prepare_group(store,definition,{**group,'claim_ids':ids})
    if not previous and sequential:
        materials['claim_management_resolution']=sequential
    elif not previous and saved.get('claim_layer_audit'):
        from backend.pipeline.claim_first_management import coverage_context
        materials['claim_management_resolution']=coverage_context(store,saved,group)
    elif not previous and old.get('action',{}).get('materials',{}).get('claim_issue_context'):
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
    if saved.get('claim_layer_audit'):
        from backend.pipeline.claim_first_management import REPROPOSAL_VERSION
        if materials.get('claim_management_resolution',{}).get('schema_version')!=REPROPOSAL_VERSION:
            raise ValueError('saved coverage predates Claim-first handoff; no cache reinterpretation or new budget')
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
        viewpoint_ids=views,disposition=merge_dispositions(old.get('disposition'),disposition),action=action,
        pending_cvp_management_record_keys=materials.get('claim_management_resolution',{}).get('pending_existing_cvp_record_keys',[]))


def restore_exact_supports_packet_order(saved, current):
    """Preserve the actual request order after exact indexed graph rebuild.

    Only graph context arrays are unordered SQL sets. Source paragraphs,
    evidence, spans, targets, findings and all content retain exact ordering.
    """
    from copy import deepcopy
    projected=deepcopy(current)
    if len(saved.get('targets',[]))!=len(projected.get('targets',[])):
        raise ValueError('saved supports target population drift')
    for old,new in zip(saved['targets'],projected['targets']):
        for field in ('adjacent_links','adjacent_claims','dependent_records'):
            if (field not in old or field not in new
                    or sorted(map(canonical_json,old[field]))!=sorted(map(canonical_json,new[field]))):
                raise ValueError('saved supports context field or record drift')
            new[field]=old[field]
    if projected!=saved:raise ValueError('saved supports content/input drift')
    return saved


def supports_identity_dependencies(store, records, selected):
    """Read exact forward candidate references omitted by consumer closure.

    Candidates are provenance templates, not semantic review input. Keep the
    saved packet and calls unchanged; pin these rows in the actual transaction.
    """
    from backend.api.canonical_repository.postgres_store import record_content_sha
    keys=set()
    for item in selected:
        old=item['before_link']
        decision=records.get(('viewpoint_identity_decisions',old['decision_id']))
        if not decision or decision.get('retired'):
            raise ValueError('bounded supports decision missing or retired')
        value=decision['payload']
        if value.get('review_status') not in {'approved','system_approved','human_approved','ai_consensus_reviewed'}:
            raise ValueError('bounded supports decision not approved')
        cid=value.get('identity_candidate_id')
        if not cid:raise ValueError('bounded supports candidate reference missing')
        keys.add(('viewpoint_identity_candidates',cid))
    extra={}
    with store.connect() as conn:
        for key in sorted(keys):
            row=conn.execute('SELECT revision,content_sha256,payload,retired_at IS NOT NULL FROM wang_knowledge.objects WHERE collection=%s AND object_id=%s',key).fetchone()
            if not row:raise ValueError('bounded supports candidate missing')
            history=conn.execute('''SELECT v.payload,v.content_sha256,o.after_sha256,c.status,o.operation
                FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c
                ON c.change_set_id=v.change_set_id JOIN wang_knowledge.change_operations o
                ON o.change_set_id=v.change_set_id AND o.collection=v.collection AND o.object_id=v.object_id
                AND o.after_revision=v.revision WHERE v.collection=%s AND v.object_id=%s AND v.revision=%s''',
                (*key,row[0])).fetchall()
            if (len(history)!=1 or history[0][1:4]!=(row[1],row[1],'applied')
                    or history[0][0].get('revision')!=row[0]
                    or {k:v for k,v in history[0][0].items() if k!='revision'}!={k:v for k,v in row[2].items() if k!='revision'}
                    or (history[0][4]=='retire')!=row[3]):
                raise ValueError('bounded supports candidate lacks exact committed history')
            # Retired candidate is historical provenance only. Copy to a new ID;
            # never revive it, reinterpret it as a current member or edit it.
            extra[key]={'revision':row[0],'content_sha256':row[1],'payload':row[2],'retired':row[3]}
    for key,row in extra.items():
        value=row['payload']
        if (value.get('identity_candidate_id')!=key[1]
                or record_content_sha(value)!=row['content_sha256']
                or value.get('review_status') in {'human_hold','human_review_required','withheld_unreviewed','rejected','superseded'}):
            raise ValueError('bounded supports candidate identity, version or hold conflict')
        if key in records and records[key]!=row:
            # _existing omits the false retired field; compare actual pins/payload.
            if pins({key:records[key]})!=pins({key:row}) or records[key]['payload']!=value:
                raise ValueError('bounded supports candidate changed within readset')
    for item in selected:
        old=item['before_link'];decision=records['viewpoint_identity_decisions',old['decision_id']]['payload']
        candidate=extra['viewpoint_identity_candidates',decision['identity_candidate_id']]['payload']
        if (old['claim_id'] not in candidate.get('candidate_claim_ids',[])
                or decision.get('resolved_viewpoint_id')!=old['viewpoint_id']
                or not any(d.get('claim_id')==old['claim_id'] and d.get('link_type')==old['link_type']
                           for d in decision.get('claim_link_decisions',[]))):
            raise ValueError('bounded supports candidate has foreign Claim or viewpoint')
    return extra


def execute_supports_action(store,run_id,adjudication_root,*,root,ports):
    from backend.pipeline.viewpoint_adjudication_history import validated_history
    from backend.pipeline.viewpoint_audit_link_repair import select_repairs,link_review_adapter,package_for,validate_supports_review,POLICY,PROMPT
    from backend.pipeline.viewpoint_authorized_source_repair_357 import Review,review_call
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact,subscription_client,write_immutable
    packet,_=validated_history(adjudication_root)
    report=read_artifact(adjudication_root/'gpt-final-handoff.json')
    selected=select_repairs(packet,report)  # rejects l3/unresolved/non-equivalent/new scope
    from backend.pipeline.cvp_retained_impact_resolution import protected_link_ids
    protected=protected_link_ids(store.get_cvp_group(run_id),store)
    if any(i['before_link']['viewpoint_claim_link_id'] in protected for i in selected):
        raise ValueError('human NO-CHANGE disposition forbids another automatic demotion review')
    policy=read_artifact(POLICY)
    if (policy['allowed_after_type']!='supports' or not policy['require_dual_model_postimage_review']
            or policy['maximum_corrections_per_adjudication']!=1):raise ValueError('standing narrow policy drift')
    ids=sorted({i['claim']['claim_id'] for i in selected})
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
    if (root/'packet.json').exists():
        repair=restore_exact_supports_packet_order(read_artifact(root/'packet.json'),repair)
        selected=repair['targets']
    write_immutable(root/'packet.json',repair)
    action=store.prepare_cvp_action(run_id,action_id=action_id,kind='audit_supports_correction',
        authorization=binding(POLICY),materials={'packet':repair,'read_pins':pin_rows(pins(records))})
    reviews={}
    for label,provider,model in [('gpt','codex','gpt-6.1-sol'),('opus','claude','claude-opus-5-5')]:
        if provider=='codex' and not (root/f'raw-{label}.json').exists():
            import os
            from backend.pipeline.viewpoint_s201129_source_continuation import canonical_runtime
            os.environ['CODEX_EXECUTABLE']=canonical_runtime()['identity']['cli']['path']
        adapter=link_review_adapter(subscription_client(provider,model,'high'),PROMPT,contextual_semantics=True,
                                    multiple_targets_per_claim=len(ids)!=len(selected))
        adapter.max_request_bytes=500000
        response,_,_=review_call(adapter,repair,root,label)
        result=Review.model_validate(response)
        validate_supports_review(result,selected)
        reviews[label]=result.model_dump(mode='json')
    extra=supports_identity_dependencies(store,records,selected)
    write_immutable(root/'identity-template-read-pins.json',{
        'schema_version':'wang_cvp_supports_identity_template_read_pins_v1',
        'action_id':action_id,'packet_sha256':sha256_json(repair),
        'read_pins':pin_rows(pins(extra)),'semantic_review_input_changed':False})
    records.update(extra)
    states.update({k:r['payload'] for k,r in extra.items()})
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
