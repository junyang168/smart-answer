"""Submit a precisely directed source write slice; retain every other issue.

Uses the original authenticated correction and its unused final review. No new
GPT proposal, root, correction budget, or model approval for exact retirement.
"""
from copy import deepcopy
from pathlib import Path

from backend.pipeline import source_reconciliation as c
from backend.api.canonical_repository.postgres_store import sha256_json

VERSION = 'wang_claim_source_write_slice_v1'


def owner_statement_projection(frozen, original, direction):
    """A statement-only slice keeps protected storage fields, not model aliases.

    The exact model statement and three/all owned link components still require
    the original unused independent final review. The unsubmitted metadata and
    every unrelated unresolved finding remain in its disclosed parent response.
    """
    from backend.pipeline.claim_owner_revision import validate_direction, validate_joint
    from backend.pipeline.cvp_sql_flow import check_binding
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    from backend.api.canonical_repository.postgres_store import record_content_sha
    from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha
    proof = frozen.get('owner_claim_completion')
    if not proof or direction.get('owner_claim_direction') != proof:
        raise ValueError('statement slice requires the original exact owner direction')
    check_binding(proof); owner = read_artifact(proof['path'])
    before = c.index(frozen['before_records']); proposed = c.index(original.records)
    cid = owner['claim_id']; claim_key = c.key('claims', cid)
    old = before[claim_key]
    validate_direction(owner, {'run_id': frozen['sql_scope']['run_id'],
        'campaign_sha256': owner['campaign_sha256'], 'claim_ids': [cid]},
        {'payload': old, 'revision': old['revision'], 'content_sha256': record_content_sha(old),
         'retired': False})
    pins = {(r['collection'], r['object_id']): r['pin'] for r in frozen['sql_scope']['read_pins']}
    owned = {k for k,p in before.items() if k.startswith('viewpoint_claim_links/')
             and p['claim_id'] == cid and p['effective_state'] == 'active'}
    selected = direction['source_write_keys']
    if (direction.get('withdraw_relation_ids') != [] or not owned
            or selected != sorted({claim_key, *owned}) or set(proposed) != set(selected)
            or len(original.records) != len(selected)
            or len(original.claim_assessments) != 1
            or original.claim_assessments[0].cause != 'claim_statement'):
        raise ValueError('statement slice requires one complete Claim and ALL active owned link postimages')
    masked = {}; changes = []
    for k in selected:
        current, candidate = before[k], proposed[k]
        if (pins.get(c.split_key(k)) != [current['revision'], record_content_sha(current), False]
                or current.get('review_status') in c.HUMAN_HOLDS | {'human_hold','rejected','withheld_unreviewed'}
                or candidate.get('review_status') != 'candidate'
                or candidate.get('revision') != current['revision'] + 1):
            raise ValueError('statement slice exact current version/hold/draft conflict')
        allowed = {'statement'} if k == claim_key else {'pinned_claim_revision','component_locator','evidence_bindings'}
        compatible = deepcopy(candidate)
        if k == claim_key:
            # These are a model's storage-vocabulary substitutions, not valid
            # aliases in the repository schema. Preserve the original semantic
            # bucket and disclose the unsubmitted row to independent review.
            if ('context_evidence_step_ids' in current and 'context_evidence_step_ids' not in compatible
                    and 'canonical_evidence_step_ids' not in current
                    and compatible.get('canonical_evidence_step_ids') == current['context_evidence_step_ids']):
                compatible['context_evidence_step_ids'] = compatible.pop('canonical_evidence_step_ids')
            if len(compatible.get('occurrences', [])) != len(current.get('occurrences', [])):
                raise ValueError('statement slice cannot change occurrence membership')
            for old_occ, new_occ in zip(current.get('occurrences', []), compatible.get('occurrences', [])):
                for stored, unsubmitted in (('canonical_evidence_step_ids','citation_ids'),
                                             ('project_id','parent_topic_id')):
                    if (stored in old_occ and stored not in new_occ and unsubmitted not in old_occ
                            and new_occ.get(unsubmitted) == old_occ[stored]):
                        new_occ[stored] = new_occ.pop(unsubmitted)
        if ({f:v for f,v in c._semantic(compatible).items() if f not in allowed}
                != {f:v for f,v in c._semantic(current).items() if f not in allowed}):
            raise ValueError('statement slice contains a non-storage content/ownership/anchor change')
        if k != claim_key and any(candidate.get(f) != current.get(f) for f in (
                'claim_id','viewpoint_id','link_type','effective_state','validated_against_viewpoint_revision_id')):
            raise ValueError('statement slice cannot retarget, retire or change a connection role')
        value = deepcopy(current)
        for f in allowed | c.PROVENANCE | {'revision'}:
            if f in candidate: value[f] = deepcopy(candidate[f])
            elif f in c.PROVENANCE: value.pop(f, None)
        for f in sorted(set(current) | set(candidate)):
            if f not in allowed | c.PROVENANCE | {'revision'} and current.get(f) != candidate.get(f):
                changes.append({'record_key': k, 'field': f,
                    'unsubmitted_model_value': deepcopy(candidate.get(f)),
                    'preserved_current_value': deepcopy(current.get(f))})
        masked[k] = value
    if not isinstance(masked[claim_key]['statement'], str) or not masked[claim_key]['statement'].strip():
        raise ValueError('statement slice has no actual model-authored statement')
    for k in owned:
        locator = masked[k].get('component_locator')
        if locator is not None:
            locator['claim_sha256'] = semantic_record_sha(masked[claim_key])
    proposal = type(original)(records=c.rows(masked), dispositions=[
        c.Disposition(record_key=k, action='update', reason=next(d.reason for d in original.dispositions
            if d.record_key == k)) for k in selected], sentence_dispositions=[],unadmitted_candidates=[],
        claim_assessments=deepcopy(original.claim_assessments),cvp_impacts=[])
    validate_joint(frozen, proposal, {})
    scoped = deepcopy(frozen)
    scoped['source_write_slice_material'] = True
    scoped['manifest'].pop('claim_issue', None)
    scoped['manifest']['permissions'] = [p for p in frozen['manifest']['permissions']
        if c.key(p['collection'],p['object_id']) in selected]
    scoped['inventory'] = {'affected_keys': selected, 'context_keys': sorted(set(before)-set(selected)),
                           'unknown_keys': []}
    scoped['sentence_inventory'] = []
    scoped['owner_statement_storage_preservation'] = changes
    c.validate_proposal(scoped, proposal)
    return scoped, proposal


def projection(frozen, original, direction):
    if direction.get('schema_version') != VERSION or direction.get('human_content_approval') is not False:
        raise ValueError('source slice requires exact independent-review authority')
    if not frozen.get('sql_scope') or not frozen['manifest'].get('claim_issue'):
        raise ValueError('source slice requires original SQL Claim management')
    if direction.get('mode') == 'owner_statement_completion':
        return owner_statement_projection(frozen, original, direction)
    from backend.pipeline.claim_evidence_completion import direction_for, evidence_permission_ids
    parent = direction_for(frozen['claim_issue_context'])
    if not parent or direction['withdraw_relation_ids'] != parent['withdraw_relation_ids']:
        raise ValueError('source slice retirement exceeds original exact direction')
    before = c.index(frozen['before_records']); proposed = deepcopy(c.index(original.records))
    selected = direction['source_write_keys']
    if not selected or selected != sorted(set(selected)) or set(selected) != set(proposed):
        raise ValueError('source slice cannot omit or invent corrected source postimages')
    if any(c.split_key(k)[0] not in {'claims','evidence_steps','source_fragments'} for k in selected):
        raise ValueError('source slice may not mutate CVP, relations or professor sources')
    allowed = {c.key(p['collection'],p['object_id']):p for p in frozen['manifest']['permissions']}
    evidence_ids = evidence_permission_ids(parent)
    for k in selected:
        col, oid = c.split_key(k); old = before.get(k); new = proposed[k]
        if col == 'evidence_steps':
            if oid not in evidence_ids: raise ValueError('source slice Evidence exceeds parent scope')
            # Restore binding; an unchanged held owner is NOT cleared by this.
            if oid in parent['evidence_ids']:
                fields = {'source_fragment_id','source_fragment_ids','anchor_quality',
                          'support_eligibility','valid_for_identity_review'}
                if ({f:v for f,v in c._semantic(old).items() if f not in fields}
                        != {f:v for f,v in c._semantic(new).items() if f not in fields}):
                    raise ValueError('source slice cannot rewrite withdrawn Evidence teaching')
                if new.get('support_eligibility') not in {'eligible','eligible_with_label','withheld_unreviewed'}:
                    raise ValueError('source slice unsupported qualification')
                if new.get('support_eligibility') == 'withheld_unreviewed' and new.get('valid_for_identity_review') is not False:
                    raise ValueError('pending Evidence cannot become identity evidence')
        if col == 'claims' and oid in parent['unchanged_held_context_claim_ids']:
            raise ValueError('source slice may not change held shared Claim')
        if col == 'claims' and old and any(new.get(f)!=old.get(f) for f in (
                'statement','claim_type','attribution','discourse_mode','scripture_refs','evidence_step_ids')):
            raise ValueError('source binding slice cannot change Claim words/ownership')
        if col == 'claims' and old and new.get('metrics') != old.get('metrics'):
            # Frequency/product navigation is not this source repair's scope.
            # Keep its exact old metadata; disclose the delta to the reviewer.
            new['metrics'] = deepcopy(old.get('metrics'))
    target_permissions = [p for p in frozen['manifest']['permissions']
        if c.key(p['collection'],p['object_id']) in selected or
        (p.get('create') and p['collection']=='source_fragments' and p.get('parent_id')==frozen['manifest']['source_id'])]
    scoped = deepcopy(frozen)
    scoped['source_write_slice_material'] = True
    # The parent Claim management and all its unresolved answers remain intact.
    # This exact independently reviewed write set is not whole-issue approval.
    scoped['manifest'].pop('claim_issue',None)
    scoped['manifest']['permissions'] = target_permissions
    scoped['inventory'] = {'affected_keys': sorted(set(selected)&set(before)),
        'context_keys': sorted(set(before)-set(selected)), 'unknown_keys': []}
    scoped['sentence_inventory'] = []
    proposal = c.Proposal(records=c.rows(proposed),
        dispositions=[c.Disposition(record_key=k,action='update',reason=next(
            d.reason for d in original.dispositions if d.record_key==k)) for k in selected],
        sentence_dispositions=[],unadmitted_candidates=[])
    c.validate_proposal(scoped,proposal)
    return scoped,proposal


def prepare(direction):
    from backend.pipeline.source_reconciliation_runner import read,body,validate_raw,effective_proposal
    from backend.pipeline.cvp_sql_flow import check_binding
    if direction.get('artifact_sha256') != sha256_json({k:v for k,v in direction.items() if k!='artifact_sha256'}):
        raise ValueError('source slice direction seal drift')
    if direction.get('authorized_by')!='owner' or not direction.get('authorization_quote'):
        raise ValueError('source slice lacks explicit owner direction')
    if (direction.get('new_gpt_calls')!=0 or direction.get('additional_corrections')!=0
            or direction.get('original_unused_final_review_only') is not True):
        raise ValueError('source slice cannot mint a second proposal/correction/review budget')
    root = Path(direction['parent_root'])
    for stage in ('proposal','initial_review','correction'):
        for suffix in ('packet','raw'):
            b=direction['original_leaves'][stage+'.'+suffix]
            if Path(b['path']) != root/(stage+'.'+suffix+'.json'): raise ValueError('foreign parent answer')
            check_binding(b)
    check_binding(direction['original_materials'])
    if Path(direction['original_materials']['path'])!=root/'scoped-materials.json':
        raise ValueError('foreign source material')
    frozen=body(read(root/'scoped-materials.json'));c.guard_files(frozen)
    if frozen['sql_scope']['run_id']!=direction['run_id']:raise ValueError('source slice foreign SQL group')
    for stage in ('proposal','initial_review','correction'):
        validate_raw(root,stage,body(read(root/(stage+'.packet.json'))))
    original=effective_proposal(frozen,read(root/'correction.raw.json')['response'])
    scoped,proposal=projection(frozen,original,direction)
    packet=c.review_packet(scoped,proposal)
    packet['source_write_slice']={'schema_version':VERSION,'direction':direction,
        'original_initial_review':read(root/'initial_review.raw.json')['response'],
        'original_corrected_proposal':original.model_dump(),
        'program_projection_delta':scoped.get('owner_statement_storage_preservation', [{ 'record_key':k,'field':'metrics',
            'unsubmitted_model_value':c.index(original.records)[k].get('metrics'),
            'preserved_current_value':c.index(frozen['before_records'])[k].get('metrics')}
            for k in direction['source_write_keys'] if k.startswith('claims/')
            and c.index(original.records)[k].get('metrics')!=c.index(frozen['before_records'])[k].get('metrics')]),
        'pending_dispositions':[d.model_dump() for d in original.dispositions if d.record_key not in direction['source_write_keys']],
        'original_cvp_impacts':[i.model_dump() for i in original.cvp_impacts],
        'retirement_requires_no_new_model_verdict':True,
        'group_complete':False,'additional_gpt_calls':0,'additional_corrections':0,
        'instructions':'Review only ALL declared slice postimages and full own body, including every active owned link when this is owner_statement_completion. '
            'The exact GPT statement is retained; protected storage metadata is restored from current SQL, not treated as aliases or promoted from context to canonical Evidence. '
            'The original raw and every protected-field difference are disclosed. Independently judge the ACTUAL scoped postimages, never borrow approval for the unsubmitted model row. '
            'Other relations/group impacts are explicitly pending, not approved/retained. '
            'Do not require this write slice to resolve those unrelated issues. Exact owner retirement is program-directed, not a model question. '
            'A source-bound Evidence still withheld_unreviewed remains ineligible and its held Claim stays unchanged; '
            'judge its binding and faithful excerpt, not approval of the held Claim. No CVP identity or group completion is approved.'}
    if direction.get('mode') != 'owner_statement_completion':
        # Reconstruct every already-saved source slice packet byte-for-byte.
        packet['source_write_slice']['instructions'] = 'Review only all source postimages and full own body. Other relations/group impacts are explicitly pending, not approved/retained. ' \
            'Do not require this write slice to resolve those unrelated issues. Exact owner retirement is program-directed, not a model question. ' \
            'A source-bound Evidence still withheld_unreviewed remains ineligible and its held Claim stays unchanged; ' \
            'judge its binding and faithful excerpt, not approval of the held Claim. No CVP identity or group completion is approved.'
    return root,frozen,scoped,proposal,packet


def scoped_adapter(adapter,packet):
    if not packet.get('source_write_slice'):return adapter
    from copy import copy
    if packet['source_write_slice'].get('schema_version')!=VERSION:raise ValueError('unknown source write slice')
    result=copy(adapter)
    text='\n本次仅审核 source_write_slice 明示的完整写集；owner_statement_completion 包含实际新Claim表述及全部active自有连接的新版成分/证据绑定。程序保留SQL原非授权字段，不把context Evidence提升为canonical，所有未提交模型字段差异已披露；必须独立审核此完整实际后像，不借旧行批准。其他完整保留的原未决属于下一独立步骤，不批准、不改成retain。只返回proposal.dispositions各项verdict。退役由具名人工方向/程序验证执行，不另作语义判读。绑定仍withheld_unreviewed的Evidence不提升identity资格、不释放其Claim hold。新绑定/引文/Claim锚点必须完整独立判断，拒绝仍停止；零追加纠正。'
    if packet['source_write_slice']['direction'].get('mode') != 'owner_statement_completion':
        text='\n本次仅审核 source_write_slice 明示的本源写集。其他完整保留的原未决属于下一独立步骤，不批准、不改成retain。只返回proposal.dispositions各项verdict。退役由具名人工方向/程序验证执行，不另作语义判读。绑定仍withheld_unreviewed的Evidence不提升identity资格、不释放其Claim hold。新绑定/引文/Claim锚点必须完整独立判断，拒绝仍停止；零追加纠正。'
    result._base_prompt=adapter._base_prompt+text;result._prompt=adapter._prompt+text
    result.prompt_sha256=sha256_json({'prompt':result._prompt})
    return result


def package(direction,raw):
    root,original,scoped,proposal,packet=prepare(direction)
    if not c.validate_review(packet,raw['response']):raise ValueError('source slice independent postimage rejected')
    result=c.compile_package(scoped,proposal,raw,frozen_sha256=sha256_json(scoped))
    from backend.pipeline.cvp_sql_actions import SOURCE_SCHEMA
    from backend.pipeline.cvp_sql_flow import binding
    result['source_repair_application']={'schema_version':SOURCE_SCHEMA,'run_id':direction['run_id'],
        'run_root':str(root),'materials':binding(root/'scoped-materials.json'),
        'materials_sha256':sha256_json(original),'terminal_stage':'final_review',
        'review_sha256':raw['artifact_sha256'],'source_write_slice':direction}
    return result


def validate_package(value):
    from backend.pipeline.source_reconciliation_runner import validate_raw,read,body
    direction=value['source_repair_application']['source_write_slice']
    root,original,scoped,proposal,packet=prepare(direction)
    saved=body(read(root/'final_review.packet.json'))
    if saved!=packet:raise ValueError('source slice independent full packet drift')
    raw=validate_raw(root,'final_review',saved)
    if value!=package(direction,raw):raise ValueError('source slice differs from actual independently reviewed postimages')
    return tuple(('knowledge_relations',rid) for rid in direction['withdraw_relation_ids'])


def validate_claim_layer_authority(saved, root):
    """The applied statement slice may audit Claims, never complete CVP work."""
    value=saved['reviewed']['evidence']['package']['payload']
    app=value.get('source_repair_application', {})
    direction=app.get('source_write_slice', {})
    if (saved.get('checkpoint')!='applied' or direction.get('mode')!='owner_statement_completion'
            or direction.get('run_id')!=saved['run_id']
            or Path(app.get('run_root','')).resolve()!=Path(root).resolve()
            or Path(direction.get('parent_root','')).resolve()!=Path(root).resolve()
            or not saved['reviewed']['evidence'].get('pending_cvp_claim_ids')):
        raise ValueError('Claim-layer audit requires exact committed owner statement slice')
    validate_package(value)
    return True


def execute(store,authorization,*,ports):
    from backend.pipeline.cvp_sql_flow import (check_binding,decoded_pins,commit_package,
                                             verified_group_backup,binding)
    from backend.pipeline.source_reconciliation_runner import read,Runner,write
    from backend.pipeline.cvp_sql_actions import SOURCE_SCHEMA,initialize_subscription_runtime
    check_binding(authorization);direction=read(Path(authorization['path']))
    root,frozen,scoped,proposal,packet=prepare(direction)
    run_id=direction['run_id'];saved=store.get_cvp_group(run_id)
    if (not saved or saved['campaign_sha256']!=direction['campaign_sha256']
            or not str(root.resolve()).startswith(str(Path(direction['execution_root']).resolve()/run_id)+'/')
            or saved.get('execution_roots',{}).get(root.name)!=str(root.resolve())
            or store.get_cvp_action(run_id,root.name) is None):raise ValueError('source slice not original registered action')
    previous=saved.get('reviewed',{}).get('evidence',{})
    prior=previous.get('package',{}).get('payload',{}).get('source_repair_application',{})
    if prior.get('source_write_slice')==direction and saved['checkpoint']=='applied':
        history=store.get_change_history(saved['reviewed']['plan']['change_set_id'])
        if not history or history['status']!='applied':raise ValueError('source slice committed history missing')
        for op in history['operations']:
            st=store.get_record_state(op['collection'],op['object_id'])
            expected=op['after']
            if op['operation']=='retire':expected={**expected,'revision':op['before_revision']}
            if (not st or st['payload']!=expected or st['revision']!=op['after_revision']
                    or st['content_sha256']!=op['after_sha256'] or st['retired']!=(op['operation']=='retire')):
                raise ValueError('source slice committed current/history drift; never reapply')
        return {'status':'source_write_applied_pending','change_set_ids':saved['change_set_ids'],'replayed':False}
    if (root/'final_review.reservation.json').exists() and not (root/'final_review.packet.json').exists():
        raise ValueError('source slice cannot reuse uncertain final review')
    if (root/'final_review.packet.json').exists() and read(root/'final_review.packet.json').get('source_write_slice')!=packet['source_write_slice']:
        raise ValueError('source slice cannot replace an already consumed final review')
    read_pins=decoded_pins(frozen['sql_scope']['read_pins'])
    with store.connect() as conn,conn.cursor() as cur:store._assert_transaction_read_pins(cur,read_pins)
    initialize_subscription_runtime(ports)
    stages=Runner(root,ports);stages.root=root
    raw=stages.call('final_review',packet)
    value=package(direction,raw);validate_package(value)
    write(root/'source-write-slice-package.json',value)
    action=store.get_cvp_action(run_id,root.name)
    result=commit_package(store,run_id,package=value,
        approval={'terminal_review':binding(root/'final_review.raw.json'),'source_write_slice':authorization},
        authorization={'source_write_slice':authorization},backup=verified_group_backup(root,value,ports),
        sources=[s['file'] for s in frozen['after']+frozen.get('context_sources',[])],read_pins=read_pins,
        reason='exact owner relation withdrawal with independently reviewed source bindings; other issues pending',
        actor='owner-authorized-pipeline',source_kind=SOURCE_SCHEMA,action=action,
        pending_cvp_claim_ids=saved['claim_ids'],
        pending_cvp_management_record_keys=sorted(set(previous.get('pending_cvp_management_record_keys', [])) |
            {d.record_key for d in original_dispositions(direction) if d.action=='unresolved'}))
    return {'status':'source_write_applied_pending','group_complete':False,**result}


def original_dispositions(direction):
    from backend.pipeline.source_reconciliation_runner import read,body,effective_proposal
    root=Path(direction['parent_root']);frozen=body(read(root/'scoped-materials.json'))
    return effective_proposal(frozen,read(root/'correction.raw.json')['response']).dispositions


def impact_audit_scope(store, saved):
    """Recover a complete audit population, not approval of pending impacts.

    The current source-only review deliberately has no CVP denominator. Read
    the named committed normal review archives and the indexed current graph;
    never infer the population from only the changed links or the empty list.
    """
    from backend.pipeline.cvp_source_impact_resolution import applied_review
    from backend.pipeline.cvp_sql_flow import pins, pin_rows
    from backend.api.canonical_repository.postgres_store import record_content_sha
    evidence=saved.get('reviewed',{}).get('evidence',{})
    app=evidence.get('package',{}).get('payload',{}).get('source_repair_application',{})
    root=Path(app.get('run_root',''))
    validate_claim_layer_authority(saved,root)
    plan=saved['reviewed']['plan']
    committed=store.get_change_history(plan['change_set_id'])
    if (not committed or committed['status']!='applied'
            or committed['fingerprint_sha256']!=plan['fingerprint_sha256']
            or committed['source_sha256']!=plan['source_sha256']
            or committed['metadata'].get('run_id')!=saved['run_id']
            or committed['metadata'].get('reviewed_sha256')!=saved['reviewed_sha256']
            or committed['metadata'].get('approval')!=evidence['approval']):
        raise ValueError('slice audit current SQL review is not the exact committed source slice')
    def operation_identity(op):
        return (op['collection'],op['object_id'],op['operation'],op['after_revision'],op['after_sha256'])
    expected=[operation_identity(op) for op in plan['operations']]
    actual=[operation_identity(op) for op in committed['operations']]
    if not expected or len(expected)!=len(set(expected)) or sorted(expected)!=sorted(actual):
        raise ValueError('slice audit actual committed write set differs from approved plan')
    views=set(); archives=[]
    for cid in saved['change_set_ids']:
        history=store.get_change_history(cid)
        if not history or history['status']!='applied' or history['metadata'].get('run_id')!=saved['run_id']:
            raise ValueError('slice audit lacks exact committed group history')
        if history['source_kind']!='cvp_batch_resolution':continue
        sha=history['metadata']['reviewed_sha256']
        review=applied_review(store,saved,sha)
        if review['plan']['change_set_id']!=cid:
            raise ValueError('slice audit normal review/commit identity drift')
        declared=review['evidence'].get('viewpoint_ids')
        if not declared or len(declared)!=len(set(declared)):
            raise ValueError('slice audit original normal population missing/duplicate')
        views.update(declared)
        archives.append({'change_set_id':cid,'reviewed_sha256':sha})
    if not archives:raise ValueError('slice audit requires original committed normal CVP population')
    # The first indexed read discovers all active original-member links; the
    # second reads complete members of their full union with the original set.
    current=store.read_cvp_context(saved['claim_ids'],viewpoint_ids=sorted(views))
    original_views=set(views)
    views.update(r['payload']['viewpoint_id'] for (col,_),r in current.items()
        if col=='viewpoint_claim_links' and not r['retired']
        and r['payload']['effective_state']=='active' and r['payload']['claim_id'] in saved['claim_ids'])
    if views!=original_views:
        current=store.read_cvp_context(saved['claim_ids'],viewpoint_ids=sorted(views))
    pending=evidence.get('pending_cvp_management_record_keys',[])
    if len(pending)!=len(set(pending)):raise ValueError('slice audit duplicate pending obligations')
    cvp_keys=[];source_keys=[]
    from backend.pipeline.cvp_sql_flow import NORMAL_COLLECTIONS
    for key in pending:
        col,oid=c.split_key(key)
        if col not in NORMAL_COLLECTIONS and col not in {'evidence_steps','knowledge_relations'}:
            raise ValueError('slice audit unknown pending management domain')
        row=current.get((col,oid)) or store.get_record_state(col,oid)
        if row is None:raise ValueError('slice audit missing pending current record')
        current[col,oid]=row
        (cvp_keys if col in NORMAL_COLLECTIONS else source_keys).append(key)
    for (col,oid),row in current.items():
        if record_content_sha(row['payload'])!=row['content_sha256']:
            raise ValueError('slice audit current payload SHA drift')
        # Protected approved/superseded rows are readable audit context, not
        # writable candidates. The audit does not clear any protected status.
        if row['payload'].get('review_status')=='human_hold':
            raise ValueError('slice audit current dependency hold')
        if col=='canonical_viewpoints' and oid in views and row['retired']:
            raise ValueError('slice audit declared viewpoint retired')
    if any(('canonical_viewpoints',v) not in current for v in views):
        raise ValueError('slice audit complete viewpoint population missing')
    for op in committed['operations']:
        key=op['collection'],op['object_id']
        row=current.get(key) or store.get_record_state(*key)
        if (op['operation']=='retire' or not row or row['retired'] or row['payload']!=op['after']
                or row['revision']!=op['after_revision'] or row['content_sha256']!=op['after_sha256']):
            raise ValueError('slice audit committed source postimage drift')
        current[key]=row
    return {'claim_ids':saved['claim_ids'],'viewpoint_ids':sorted(views),
        'normal_review_archives':archives,'read_pins':pin_rows(pins(current)),
        'pending_record_keys':pending,'cvp_record_keys':cvp_keys,'source_record_keys':source_keys}


def finish_impact_audit(store, saved):
    """Full current independent audit only; pending management stays pending."""
    import os
    import subprocess
    from backend.pipeline.cvp_sql_flow import binding,check_binding,decoded_pins,PROJECT,validate_transaction_audit
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact,write_immutable
    from backend.pipeline.viewpoint_content_blocked_fallback import verify_saved_full_audit
    from backend.pipeline.source_reconciliation_runner import body,read
    marker=saved.get('claim_layer_audit',{})
    proof=marker.get('receipt')
    app=saved.get('reviewed',{}).get('evidence',{}).get('package',{}).get('payload',{}).get('source_repair_application',{})
    root=Path(app.get('run_root',''))
    if not proof or marker.get('status')!='claim_layer_audited_cvp_pending' or marker.get('cvp_management_complete') is not False:
        raise ValueError('slice impact audit requires actual completed Claim layer')
    if proof.get('path')!=str(root/'claim-first-audit/claim-layer-completion.json'):
        raise ValueError('slice impact audit foreign Claim-layer receipt')
    check_binding(proof);receipt=read_artifact(proof['path'])
    if (receipt['claim_ids']!=saved['claim_ids'] or receipt['change_set_ids']!=saved['change_set_ids']
            or any(receipt.get(k) is not False for k in ('cvp_management_complete','group_complete','next_group_started'))):
        raise ValueError('slice impact audit Claim-layer population/commit drift')
    check_binding(receipt['audit'])
    from backend.pipeline.claim_first_management import VERSION as CLAIM_VERSION,validate_claim_audit_report
    if receipt.get('schema_version')!=CLAIM_VERSION or receipt.get('status')!=marker['status']:
        raise ValueError('slice impact audit Claim-layer receipt schema/status drift')
    validate_claim_audit_report(read_artifact(receipt['audit']['path']),saved['claim_ids'])
    for b in receipt['source_proofs']:check_binding(b)
    # This exact receipt was registered only after complete independent Claim
    # validation. Consume its SQL binding, complete leaf hashes and unchanged
    # current readset; do not rebuild every old model request again at handoff.
    with store.connect() as conn,conn.cursor() as cur:
        store._assert_transaction_read_pins(cur,decoded_pins(receipt['read_pins']))
    scope=impact_audit_scope(store,saved)
    c.guard_files(body(read(root/'scoped-materials.json')))
    audit_root=root/('cvp-impact-audit-'+sha256_json(scope)[:20])
    intent={'schema_version':'wang_source_slice_complete_impact_audit_v1','run_id':saved['run_id'],
        'reviewed_sha256':saved['reviewed_sha256'],'claim_layer_completion':proof,
        'change_set_ids':saved['change_set_ids'],**scope,'model':'gemini-3.8-flash','workers':2,
        'layers':['1','2','3','4'],'automatic_retry':False,'group_complete':False}
    path=audit_root/'audit.json';intent_path=audit_root/'audit-intent.json'
    if intent_path.exists() and read_artifact(intent_path)!=intent:
        raise ValueError('slice impact audit original intent drift')
    if path.exists() and not intent_path.exists():
        raise ValueError('slice impact audit result lacks original exact intent')
    if not path.exists():
        if intent_path.exists():raise ValueError('slice impact audit outcome unknown; no automatic replay')
        write_immutable(intent_path,intent)
        subprocess.run([str(PROJECT/'backend/.venv/bin/python'),'scripts/audit-library.py',
            '--scope','all','--layers','1,2,3,4','--claim-ids',','.join(scope['claim_ids']),
            '--viewpoint-ids',','.join(scope['viewpoint_ids']),'--workers','2','--model','gemini-3.8-flash',
            '--no-status-file','--preserve-full-context','--component-roles','--sql-group-scope',
            *[a for k in scope['cvp_record_keys'] for a in ('--sql-record-key',k)],'--out',str(audit_root)],
            cwd=PROJECT,check=True,env={**os.environ,'KNOWLEDGE_DATABASE_URL':store.database_url})
    audit=read_artifact(path)
    validate_transaction_audit(audit,scope['claim_ids'],scope['viewpoint_ids'])
    proofs=verify_saved_full_audit(path,layers=('3','4'))
    with store.connect() as conn,conn.cursor() as cur:
        store._assert_transaction_read_pins(cur,decoded_pins(scope['read_pins']))
    result={'status':'independent_impact_audited_management_pending','audit':binding(path),
        'audit_proofs':proofs,'intent':binding(intent_path),**scope,
        'group_complete':False,'next_group_started':False,'model_verdicts_changed':False}
    write_immutable(audit_root/'impact-audit-receipt.json',result)
    return result
