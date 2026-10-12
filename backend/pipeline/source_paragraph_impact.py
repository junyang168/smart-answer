"""Separate a changed paragraph's semantic graph from source epoch bookkeeping.

This compiler is pure: it does not approve content, change a model answer, or
write knowledge rows. The complete source readset remains the transaction and
invalidation denominator. Only unchanged paragraphs can enter the binding-only
plane; full CVP member/source context remains in the semantic plane.
"""
from collections import deque
from copy import deepcopy
import hashlib

from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline import source_reconciliation as c

VERSION = "wang_source_paragraph_impact_v1"
REVIEW_VERSION = "wang_source_epoch_binding_review_v1"
PROFILE = "wang_source_paragraph_execution_v1"


def committed_context(store, saved):
    """Authenticate the actual whole source transaction, not a profile label.

    The named application is the current independently reviewed source action.
    This is a source audit handoff only, never CVP identity or completion approval.
    """
    from pathlib import Path
    from backend.pipeline.cvp_sql_actions import validate_source_package
    from backend.pipeline.cvp_sql_flow import check_binding, decoded_pins, pins
    from backend.pipeline.source_reconciliation_runner import body, read
    from backend.api.canonical_repository.viewpoint_foundation import sha256_json
    from backend.pipeline.viewpoint_validation_context import read_current_objects
    reviewed = saved.get('reviewed', {})
    evidence = reviewed.get('evidence', {})
    package = evidence.get('package', {}).get('payload', {})
    app = package.get('source_repair_application', {})
    if not package.get('source_epoch_binding_material'):
        return None
    if (saved.get('checkpoint') not in {'applied', 'audited'} or app.get('run_id') != saved['run_id']
            or sha256_json(reviewed) != saved.get('reviewed_sha256')):
        raise ValueError('paragraph source handoff is not the current committed review')
    check_binding(app['materials'])
    frozen = body(read(Path(app['materials']['path'])))
    execution_context(frozen)
    if (sha256_json(frozen) != app['materials_sha256']
            or evidence.get('action', {}).get('materials_sha256') != app['materials_sha256']
            or evidence.get('action', {}).get('materials') != frozen):
        raise ValueError('paragraph source action/material identity drift')
    validate_source_package(package)
    plan = reviewed['plan']; history = store.get_change_history(plan['change_set_id'])
    if (not history or history['status'] != 'applied' or plan['change_set_id'] not in saved['change_set_ids']
            or history['fingerprint_sha256'] != plan['fingerprint_sha256']
            or history['source_sha256'] != plan['source_sha256']
            or history['metadata'].get('run_id') != saved['run_id']
            or history['metadata'].get('reviewed_sha256') != saved['reviewed_sha256']
            or history['metadata'].get('approval') != evidence['approval']):
        raise ValueError('paragraph source actual SQL commit/approval drift')
    def op_identity(op):
        return op['collection'], op['object_id'], op['operation'], op['after_revision'], op['after_sha256']
    if sorted(map(op_identity, plan['operations'])) != sorted(map(op_identity, history['operations'])):
        raise ValueError('paragraph source complete committed write set drift')
    expected = decoded_pins(evidence['read_pins'])
    for op in history['operations']:
        expected[(op['collection'], op['object_id'])] = (op['after_revision'], op['after_sha256'], False)
    current = read_current_objects(store, expected)
    if pins(current) != expected:
        raise ValueError('paragraph source current audit readset drift')
    for op in history['operations']:
        state = current.get((op['collection'], op['object_id']))
        if (op['operation'] != 'update' or not state or state['retired']
                or state['payload'] != op['after'] or state['content_sha256'] != op['after_sha256']):
            raise ValueError('paragraph source current/history postimage drift')
    terminal = evidence['approval']['terminal_review']; check_binding(terminal)
    if terminal['path'] != str(Path(app['run_root'])/(app['terminal_stage']+'.raw.json')):
        raise ValueError('paragraph source terminal approval root drift')
    return {'frozen': frozen, 'root': app['run_root'], 'terminal_review': terminal,
            'plan': plan, 'current': current, 'source_only_approval': True,
            'existing_cvp_content_approved': False, 'group_complete': False}


def assert_issue_resolution(cursor, run_id, issue):
    """Consume new-source review, without claiming the old action committed."""
    from backend.pipeline.cvp_source_impact_resolution import context, applied_review
    from backend.pipeline.cvp_sql_actions import validate_source_package
    from backend.pipeline.cvp_sql_flow import check_binding
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    row=cursor.execute("SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s AND stage='cvp_group'",(run_id,)).fetchone()
    if not row:return False
    value={**row[0],'run_id':run_id};handoff=context(value,cursor=cursor)
    if not handoff or handoff.get('source_slice_mode')!=PROFILE:return False
    source=applied_review(None,value,handoff['prior_reviewed_sha256'],cursor=cursor)
    package=source['evidence']['package']['payload'];validate_source_package(package)
    frozen=package['source_epoch_binding_material']['frozen'];marker,_=execution_context(frozen)
    matches=[p for p in marker['prior_source_issues'] if p['issue']['issue_id']==issue['issue_id']]
    if len(matches)!=1:raise ValueError('new-source issue resolution missing/ambiguous')
    old=matches[0]['issue']
    for field in ('issue_id','owner_run_id','action','reports','correction_used','terminal_refusal'):
        if old.get(field)!=issue.get(field):raise ValueError('new-source issue original reservation/report drift')
    check_binding(handoff['source_terminal_packet']);packet=read_artifact(handoff['source_terminal_packet']['path'])
    items=[p for p in packet.get('prior_source_issue_resolution_scope',[]) if p['issue_id']==issue['issue_id']]
    if len(items)!=1:raise ValueError('new-source issue has no actual independent resolution scope')
    cid=items[0]['claim_key'].split('/',1)[1]
    ops=[o for o in source['plan']['operations'] if o['collection']=='claims' and o['object_id']==cid]
    if (len(ops)!=1 or ops[0]['operation']!='update'
            or any(r['claim_id']!=cid or r['claim_sha256']!=ops[0]['before_sha256']
                   or r['claim_revision']!=ops[0]['before_revision'] for r in old['reports'])):
        raise ValueError('new-source issue does not own the actual reviewed Claim transition')
    PostgresKnowledgeStore._assert_transaction_read_pins(cursor,
        {('claims',cid):(ops[0]['after_revision'],ops[0]['after_sha256'],False)})
    check_binding(handoff['claim_layer_completion'])
    receipt=read_artifact(handoff['claim_layer_completion']['path'])
    if cid not in receipt.get('audited_claim_ids',[]):
        raise ValueError('new-source issue Claim absent from actual current source audit')
    return True


def attach(frozen, store=None, *, run_id=None):
    """Bind a new actual-source stage before its first model intent.

    Existing saved actions never acquire this profile. Complete source inventory
    and SQL readset stay in the original frozen material, not a second runner.
    """
    if frozen['before']['body_sha256'] == frozen['after'][0]['body_sha256']:
        return
    m = c.Manifest.model_validate(frozen['manifest'])
    if any(p.collection not in {'source_documents','source_fragments','claims','evidence_steps',
                                'observations','questions','knowledge_relations','claim_relations','position_nodes'}
           for p in m.permissions):
        raise ValueError('paragraph source execution is source-only; CVP/route writes need their own management authority')
    if any(m.model_dump().get(k) for k in ('claim_issue', 'deferred_repair', 'viewpoint_scope_repair',
                                           'anchor_qualification', 'evidence_binding_repair')):
        raise ValueError('paragraph source epoch cannot be mixed with another management profile')
    states = c.index(frozen['before_records'])
    plan = partition(m, states, frozen['before'], frozen['after'][0])
    protected = {}
    for k in plan['proof']['semantic_inventory']['affected_keys']:
        old = states[k]
        if not k.startswith('claims/') or old.get('review_status') != 'human_review_required':
            continue
        if store is None:
            raise ValueError('protected source anchor requires exact actual AI ledger, not a status guess')
        with store.connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT review_event_id,object_revision,reviewer_kind,decision,artifact '
                        'FROM wang_knowledge.review_events WHERE collection=%s AND object_id=%s '
                        'AND object_revision=%s', ('claims', c.split_key(k)[1], old['revision']))
            events = cur.fetchall()
        if (len(events) != 1 or events[0][2:4] != ('ai', 'human_review_required')
                or events[0][4].get('resolution', {}).get('approval_status') != 'not_human_approved'
                or events[0][4].get('resolution', {}).get('independent_review_decision') != 'pass'
                or events[0][4].get('source_id') != m.source_id):
            raise ValueError('protected source anchor is held/missing/conflicting actual review ledger')
        protected[k] = {'before_revision': old['revision'], 'before_content_sha256': record_content_sha(old),
                        'review_event_id': events[0][0], 'actual_review_artifact': events[0][4],
                        'preserve_review_status': old['review_status'], 'semantic_write_authority': False}
    candidates = {}
    if store is not None:
        from backend.api.canonical_repository.knowledge_models import KNOWLEDGE_COLLECTIONS
        selected = {k:states[k] for k in plan['proof']['semantic_inventory']['affected_keys']
                    if c.split_key(k)[0] in {'knowledge_relations','claim_relations','questions'}
                    and states[k].get('review_status')=='candidate' and states[k].get('visibility')=='internal'
                    and states[k]['revision']==1}
        ids = [c.split_key(k)[1] for k in selected]
        creation_by_key, reviewed_ids, consumed_ids = {}, set(), set()
        if ids:
            with store.connect() as conn, conn.cursor() as cur:
                origins = cur.execute('SELECT v.collection,v.object_id,v.change_set_id,c.source_kind,v.content_sha256 '
                    'FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c USING(change_set_id) '
                    "WHERE v.object_id=ANY(%s) AND v.revision=1 AND c.status='applied'", (ids,)).fetchall()
                for col,oid,cid,kind,digest in origins:
                    creation_by_key.setdefault(c.key(col,oid),[]).append((cid,kind,digest))
                reviewed_ids = {row[0] for row in cur.execute('SELECT object_id FROM wang_knowledge.review_events WHERE object_id=ANY(%s)',(ids,)).fetchall()}
                refs = store._reference_consumers(cur, ids, sorted(KNOWLEDGE_COLLECTIONS))
                for col,oid,value in refs:
                    consumed_ids.update(set(ids) & (store._record_reference_ids(value,exclude_provenance=True) - {oid}))
                edges = cur.execute('SELECT from_id,to_id FROM wang_knowledge.edges WHERE '
                                    '(from_id=ANY(%s) OR to_id=ANY(%s)) AND retired_at IS NULL',(ids,ids)).fetchall()
                consumed_ids.update(set(ids) & {i for pair in edges for i in pair})
        for k,old in selected.items():
            oid=c.split_key(k)[1]; creation=creation_by_key.get(k,[])
            if (len(creation)==1 and creation[0][1]=='knowledge_package' and creation[0][2]==record_content_sha(old)
                    and oid not in reviewed_ids | consumed_ids):
                candidates[k] = {'pin':[old['revision'],record_content_sha(old),False], 'creation_change_set_id':creation[0][0],
                    'source_kind':'knowledge_package', 'independent_reviews':0,'business_consumers':[],
                    'semantic_approval':False,'content_write_authority':False}
    prior_issues=[]
    if store is not None and run_id is not None:
        from backend.pipeline.cvp_sql_flow import check_binding, binding
        from backend.pipeline.source_reconciliation_runner import read, body, validate_raw
        from backend.pipeline.claim_issue_management import Issue
        from pathlib import Path
        saved=store.get_cvp_group(run_id)
        with store.connect() as conn,conn.cursor() as cur:
            cur.execute("SELECT metadata FROM wang_knowledge.pipeline_runs WHERE metadata->>'kind'='claim_issue' "
                        "AND metadata->>'owner_run_id'=%s",(run_id,))
            issues=[r[0] for r in cur.fetchall()]
        for issue in issues:
            reports=issue.get('reports',[])
            if not reports or any(r.get('source_id')!=m.source_id for r in reports):continue
            action=issue.get('action',{});old_root=saved.get('execution_roots',{}).get(action.get('action_id'))
            if (issue.get('state')!='action_reserved' or action.get('run_id')!=run_id or not old_root
                    or issue.get('terminal_refusal')
                    or any(r.get('source_file_sha256')!=m.before_source.file_sha256
                        or c.key('claims',r['claim_id']) not in plan['proof']['semantic_inventory']['affected_keys']
                        or r['claim_revision']!=states[c.key('claims',r['claim_id'])]['revision']
                        or r['claim_sha256']!=record_content_sha(states[c.key('claims',r['claim_id'])]) for r in reports)):
                raise ValueError('prior source issue/action/generation ambiguous or foreign')
            old_root=Path(old_root)
            if old_root.name!=action['action_id'] or run_id not in old_root.parts:
                raise ValueError('prior source issue root drift')
            for report in reports:
                check_binding(report['report'])
                if (report.get('run_id')!=run_id
                        or Issue.model_validate({k:v for k,v in report.items() if k!='run_id'}).issue_id!=issue['issue_id']):
                    raise ValueError('prior source issue report identity drift')
            answers=[]
            for stage in ('proposal','initial_review','correction','final_review'):
                path=old_root/(stage+'.raw.json')
                if path.exists():
                    raw=validate_raw(old_root,stage,body(read(old_root/(stage+'.packet.json'))))
                    answers.append({'stage':stage,'raw':binding(path),'response':raw['response']})
                elif (old_root/(stage+'.call-intent.json')).exists():
                    raise ValueError('prior source issue unknown outcome; no epoch retry')
            if not answers:raise ValueError('prior source issue has no complete diagnostic answer')
            prior_issues.append({'issue':issue,'original_answers':answers,
                                 'original_verdicts_changed':False,'old_action_committed':False})
    frozen['source_paragraph_execution'] = {'schema_version': PROFILE, 'partition': plan,
        'protected_anchor_reviews': protected,'internal_candidate_context':candidates,'prior_source_issues':prior_issues}


def execution_context(frozen):
    context = frozen.get('source_paragraph_execution')
    if not context or context.get('schema_version') != PROFILE:
        raise ValueError('exact paragraph execution profile required')
    states = c.index(frozen['before_records'])
    verify(context['partition'], frozen['manifest'], states, frozen['before'], frozen['after'][0])
    # Saved diagnostic answers remain immutable evidence, not approval of this
    # new source epoch. Check their exact leaves; never rebuild an ancestor chain.
    from backend.pipeline.cvp_sql_flow import check_binding
    for prior in context.get('prior_source_issues', []):
        if prior.get('old_action_committed') is not False or prior.get('original_verdicts_changed') is not False:
            raise ValueError('paragraph source cannot upgrade the original diagnostic verdict')
        for report in prior['issue']['reports']:
            check_binding(report['report'])
        for answer in prior['original_answers']:
            check_binding(answer['raw'])
    return context, states


def document_postimage(frozen):
    """SourceDocument identity/review remains exact; only declared epoch hashes."""
    import json
    context, states = execution_context(frozen)
    m = c.Manifest.model_validate(frozen['manifest'])
    k = c.key('source_documents', m.source_id)
    old = states[k]
    raw = c.bound_bytes(m.current_sources[0], frozen.get('physical_snapshots'))
    document = json.loads(raw)
    projection = c.project_script(document.get('script') if isinstance(document, dict) else document)
    permission = next((p for p in m.permissions if p.key == k), None)
    values = {'source_sha256': projection.body_sha256, 'source_body_sha256': projection.body_sha256,
        'source_file_sha256': hashlib.sha256(raw).hexdigest(), 'source_text_sha256': projection.spoken_text_sha256,
        'anchor_binding_sha256': projection.body_sha256, 'editorial_structure_sha256': projection.editorial_structure_sha256,
        'editorial_topology_sha256': projection.editorial_topology_sha256}
    if not permission or permission.create or set(permission.fields) - set(values):
        raise ValueError('deterministic SourceDocument epoch exceeds exact permission')
    new = deepcopy(old)
    new.update({f: values[f] for f in permission.fields})
    new['revision'] = old['revision'] + 1
    return k, new


def semantic_proposal(frozen, proposal):
    """Recover model-owned rows/dispositions; never overwrite the model raw."""
    context, states = execution_context(frozen)
    required = set(context['partition']['proof']['semantic_inventory']['affected_keys'])
    mechanical = set(c.index(context['partition']['binding_postimages'])) | {document_postimage(frozen)[0]}
    return c.Proposal(records=[r for r in proposal.records if r.key not in mechanical],
        dispositions=[d for d in proposal.dispositions if d.record_key in required],
        unadmitted_candidates=proposal.unadmitted_candidates, sentence_dispositions=proposal.sentence_dispositions)


def compose_proposal(frozen, response):
    """Combine semantic model output with separately declared program accounting.

    Program dispositions are not model judgments and never grant content approval.
    The reviewer receives only the actual semantic proposal plus the exact cohort
    scope; package validation reconstructs both from the saved immutable raw.
    """
    context, states = execution_context(frozen)
    plan = context['partition']; proof = plan['proof']
    semantic = c.Proposal.model_validate(deepcopy(response))
    for row in semantic.records:
        if row.collection!='source_fragments':continue
        value=row.payload;source=frozen['after'][0]
        text=source['paragraphs'].get(value.get('paragraph_key'),{}).get('text','')
        quote=value.get('verbatim_excerpt','')
        if (value.get('source_id')!=frozen['manifest']['source_id'] or not quote or text.count(quote)!=1):
            raise ValueError('semantic source fragment must be exact unique own-source quote')
        value.update(source_sha256=source['body_sha256'],paragraph_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                     verbatim_excerpt_sha256=hashlib.sha256(quote.encode()).hexdigest(),anchor_state='source_version_bound')
    required = set(proof['semantic_inventory']['affected_keys'])
    ids = [d.record_key for d in semantic.dispositions]
    if len(ids) != len(set(ids)) or set(ids) != required:
        raise ValueError('semantic paragraph proposal must cover exact complete semantic denominator')
    data = c.index(semantic.records)
    mechanical = c.index(plan['binding_postimages'])
    doc_key, doc = document_postimage(frozen)
    mechanical[doc_key] = doc
    if set(data) & set(mechanical):
        raise ValueError('model cannot author deterministic source epoch rows')
    allowed = {p.key for p in c.Manifest.model_validate(frozen['manifest']).permissions}
    if set(data) - (required & allowed):
        raise ValueError('semantic proposal contains an undeclared/out-of-scope write')
    dispositions = list(semantic.dispositions)
    for k in sorted(set(frozen['inventory']['affected_keys']) - required):
        action = 'update' if k in mechanical else 'impact_only' if c.split_key(k)[0] in c.IMPACT_ONLY else 'retain'
        dispositions.append(c.Disposition(record_key=k, action=action,
            reason='Program source epoch accounting only; no new semantic approval. Exact paragraph partition and full current audit remain required.'))
    return c.Proposal(records=[c.Row.model_validate(r) for r in c.rows({**data, **mechanical})],
        dispositions=dispositions, unadmitted_candidates=semantic.unadmitted_candidates,
        sentence_dispositions=semantic.sentence_dispositions)


def mechanical_keys(frozen):
    context, _ = execution_context(frozen)
    return set(c.index(context['partition']['binding_postimages'])) | {document_postimage(frozen)[0]}


def pending_candidates(frozen, proposal):
    context, before = execution_context(frozen)
    unresolved = {d.record_key for d in proposal.dispositions if d.action == 'unresolved'}
    if unresolved - set(context['internal_candidate_context']):
        raise ValueError('paragraph source unresolved item is not exact unconsumed internal context')
    proposed = c.index(proposal.records)
    for k in unresolved:
        receipt = context['internal_candidate_context'][k]
        if (k in proposed or receipt['pin'] != [before[k]['revision'],record_content_sha(before[k]),False]
                or receipt['content_write_authority'] is not False or receipt['semantic_approval'] is not False):
            raise ValueError('paragraph source pending candidate drift or write')
    return unresolved


def protected_anchor_delta(frozen, k, old, new, proposed):
    """Preserve legacy AI hold; permit ONLY exact own quote synchronization."""
    context, states = execution_context(frozen)
    receipt = context['protected_anchor_reviews'].get(k)
    if (not receipt or receipt['before_revision'] != old['revision']
            or receipt['before_content_sha256'] != record_content_sha(old)
            or receipt['semantic_write_authority'] is not False):
        return False
    if {f:v for f,v in c._semantic(old).items() if f != 'occurrences'} != {
            f:v for f,v in c._semantic(new).items() if f != 'occurrences'}:
        raise ValueError('protected Claim semantic meaning cannot change with source binding')
    if len(old['occurrences']) != len(new['occurrences']):
        raise ValueError('protected Claim occurrence membership changed')
    post = {**states, **proposed}
    for previous, current in zip(old['occurrences'], new['occurrences']):
        if {f:v for f,v in previous.items() if f != 'anchors'} != {f:v for f,v in current.items() if f != 'anchors'}:
            raise ValueError('protected occurrence source identity changed')
        if len(previous['anchors']) != len(current['anchors']):
            raise ValueError('protected anchor membership changed')
        for a, b in zip(previous['anchors'], current['anchors']):
            if a == b: continue
            if {f:v for f,v in a.items() if f != 'proposed_highlight'} != {f:v for f,v in b.items() if f != 'proposed_highlight'}:
                raise ValueError('protected anchor changed more than exact corrected quote')
            if (a['paragraph_key'] not in context['partition']['proof']['changed_paragraph_keys']
                    or set(a.get('proposed_highlight', {})) != set(b.get('proposed_highlight', {}))
                    or {f:v for f,v in a['proposed_highlight'].items() if f != 'text'} != {
                        f:v for f,v in b['proposed_highlight'].items() if f != 'text'}):
                raise ValueError('protected anchor status/outside-paragraph changed')
            evidence_id = c.anchor_evidence_id(old, previous, a)
            own = post[c.key('evidence_steps', evidence_id)]
            fragments = [post[c.key('source_fragments', fid)] for fid in own.get('source_fragment_ids', [])]
            if (not fragments or not any(f['source_id'] == frozen['manifest']['source_id']
                    and f['paragraph_key'] == a['paragraph_key'] and f['verbatim_excerpt'] == b['proposed_highlight']['text']
                    and f['source_sha256'] == frozen['after'][0]['body_sha256'] for f in fragments)):
                raise ValueError('protected anchor quote is not the exact new own fragment')
    return True


def proposal_packet(frozen):
    context, _ = execution_context(frozen)
    result = review_material(frozen, context['partition'])
    result.pop('scope_preview_only'); result.pop('execution_boundary')
    result['schema_version'] = PROFILE
    result['update_id'] = frozen['update_id']
    result['manifest'] = result.pop('manifest_projection')
    result['source_paragraph_profile'] = PROFILE
    result['protected_anchor_reviews'] = context['protected_anchor_reviews']
    result['internal_candidate_context'] = context['internal_candidate_context']
    result['prior_source_issues'] = context['prior_source_issues']
    result['content_write_authority'] = False
    return result


def review_packet(frozen, proposal):
    # The existing complete graph/permissions/source validator checks the
    # expanded program+semantic postimage BEFORE any independent review.
    c.validate_proposal(frozen, proposal)
    packet = proposal_packet(frozen)
    packet['before_records'] = packet.pop('records')
    semantic = semantic_proposal(frozen, proposal)
    packet['proposal'] = semantic.model_dump()
    packet['proposal_sha256'] = sha256_json(semantic.model_dump())
    packet['provenance_contract'] = {'old_approval_is_history_only': True,
        'binding_cohort_preserves_original_provenance': True, 'protected_anchor_status_preserved': True,
        'correction_limit': 1, 'full_affected_audit_required': True}
    proposed=c.index(proposal.records)
    issues=[]
    for prior in frozen['source_paragraph_execution']['prior_source_issues']:
        reports=prior['issue']['reports'];ids={r['claim_id'] for r in reports}
        if len(ids)!=1:raise ValueError('paragraph source issue has ambiguous Claim identity')
        cid=next(iter(ids));k=c.key('claims',cid)
        if k not in proposed:
            raise ValueError('prior source issue needs an actual new-source Claim postimage, not an old verdict rewrite')
        issues.append({'record_key':'source_issue_resolution/'+prior['issue']['issue_id'],
                       'issue_id':prior['issue']['issue_id'],'claim_key':k,
                       'old_action_committed':False,'original_verdicts_changed':False,
                       'new_source_postimage':proposed[k]})
    if issues:packet['prior_source_issue_resolution_scope']=issues
    pending = pending_candidates(frozen,proposal)
    if pending:
        packet['source_readonly_pending'] = {'schema_version':PROFILE,'pending_record_keys':sorted(pending),
            'problem':'unapproved_internal_context_not_a_semantic_write','semantic_approval':False,
            'content_write_authority':False,'group_completion':False}
    return packet


def scoped_adapter(adapter, packet):
    if packet.get('source_paragraph_profile') != PROFILE:
        return adapter
    # Separate prompt/config identity. Old saved source inputs cannot acquire
    # this profile or be interpreted as approval of the narrowed new material.
    from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
    prompt = adapter._prompt + '\n来源段落分域执行 v1：inventory 只列实际语义影响和完整观点成员。' \
        '完整自身新旧原文不删；源版本机械 cohort 与 SourceDocument 由程序按逐字段保留规则计算，模型不得输出其 records。' \
        '仅返回语义 inventory 的完整 dispositions 和获准完整后像。来源 epoch 审核项须单独返回 verdict，' \
        '不把该项当成 cohort 的语义批准。protected_anchor_reviews 的 Claim 仅同步自身精确改后引文，' \
        '不得改其主张/资格/原审核状态。所有原未决和独立审计义务保留。'
    prompt += ' 模型不要计算或抄写新 SHA；语义 fragment 的 source/paragraph/quote SHA 可以为 null，由程序在初审前按完整新原文唯一逐字引用计算并独立披露原 raw 与归一化 receipt。不得把旧 SourceDocument/cohort 提议为 candidate 或提升原 protected Claim 状态。此规则替代一般来源提示中相关 hash/暂存状态要求。'
    if packet.get('prior_source_issue_resolution_scope'):
        prompt += ' 必须逐项审查 prior_source_issue_resolution_scope：完整旧异议及原回答保留，新录音校订来源和当前实际 Claim 后像是否解决该来源问题；仅对新来源后像作新的批准，不把旧未决改 pass，不声称旧 action 已提交。对每个精确 record_key 独立返回 verdict，真实未解决则拒绝。'
    if packet.get('source_readonly_pending'):
        prompt += ' 对 source_readonly_pending.pending_record_keys 返回 approved=false、problems=["unapproved_internal_context_not_a_semantic_write"]；说明只是未批准内部候选，未决不变，不授予写入/语义批准。其他项照常独立审核。'
    result = StructuredJsonReviewerAdapter(client=adapter._client, prompt=prompt,
        response_model=adapter._response_model, schema_name=adapter._schema_name + '_paragraph_v1',
        compact_payload=True, shared_text_payload=True, shared_record_payload=True)
    result.max_request_bytes = adapter.max_request_bytes
    return result


def partition(manifest, states, before, after):
    """Return exact semantic scope and an independently verifiable binding plan.

    No source-wide alias is a semantic root. Every changed paragraph fragment,
    direct own occurrence, and named reverse consumer IS a root/dependency.
    The source-wide inventory is still retained for invalidation and audits.
    """
    manifest = c.Manifest.model_validate(manifest)
    sid = manifest.source_id
    doc_key = c.key("source_documents", sid)
    doc = states[doc_key]
    old_paragraphs, new_paragraphs = before["paragraphs"], after["paragraphs"]
    if set(old_paragraphs) != set(new_paragraphs):
        raise ValueError("paragraph scope cannot insert/remove/reorder source paragraphs")
    changed = {k for k in old_paragraphs if old_paragraphs[k] != new_paragraphs[k]}
    if not changed or not changed <= set(manifest.authorized_paragraph_keys):
        raise ValueError("paragraph scope requires actual bounded source changes")
    for k in changed:
        if ({f: v for f, v in old_paragraphs[k].items() if f != "text"}
                != {f: v for f, v in new_paragraphs[k].items() if f != "text"}):
            raise ValueError("paragraph scope cannot change timing/coordinates/source-bearing fields")
    if (before["body_sha256"] != doc.get("source_body_sha256")
            or before["file"]["file_sha256"] != doc.get("source_file_sha256")
            or after["file"]["path"] != doc.get("source_path")):
        raise ValueError("paragraph scope source epoch/registered locator mismatch")
    permissions = {p.key: p for p in manifest.permissions}
    forward, reverse = c.dependency_index(states)
    own_fragments = {k for k, p in states.items()
                     if k.startswith("source_fragments/") and p.get("source_id") == sid}
    if not own_fragments:
        raise ValueError("paragraph scope requires complete own fragment inventory")
    roots, binding_rows = set(), {}
    for k in sorted(own_fragments):
        p = states[k]
        para = p.get("paragraph_key")
        quote = p.get("verbatim_excerpt")
        text = old_paragraphs.get(para, {}).get("text", "")
        # Unchanged text is not a new content approval or locator selection.
        # Preserve an already bound ambiguous quote's exact stored coordinates;
        # never choose a first occurrence or infer a new Evidence binding.
        if (not quote or not text or quote not in text
                or p.get("source_sha256") != before["body_sha256"]
                or p.get("paragraph_text_sha256") != hashlib.sha256(text.encode()).hexdigest()
                or p.get("verbatim_excerpt_sha256") != hashlib.sha256(quote.encode()).hexdigest()
                or p.get("anchor_state") != "source_version_bound"):
            raise ValueError("paragraph scope original fragment binding is incomplete/stale: " + k)
        if para in changed:
            roots.add(k)
        else:
            permission = permissions.get(k)
            if (permission is None or permission.create or permission.fields != ["source_sha256"]
                    or p.get("review_status") in {"human_hold", "withheld_unreviewed", "rejected"}):
                raise ValueError("binding-only fragment lacks exact permission or is held: " + k)
            new = deepcopy(p)
            new["source_sha256"] = after["body_sha256"]
            new["revision"] = p["revision"] + 1
            binding_rows[k] = new
    aliases = {sid, doc.get("transcript_id"), doc.get("canonical_source_id")} - {None, ""}
    # Anchors may reference local Evidence IDs rather than canonical fragment IDs.
    # They must be independently included, even when reverse JSON references
    # alone would not discover their dependency on the changed paragraph.
    for k, p in states.items():
        if not k.startswith("claims/"):
            continue
        for occurrence in p.get("occurrences", []):
            anchors = occurrence.get("anchors", [])
            if any(a.get("paragraph_key") in changed for a in anchors):
                if not aliases.intersection(c.strings(occurrence)):
                    continue  # The same S number in another sermon is not own.
                roots.add(k)
    if not roots:
        raise ValueError("changed paragraph has no indexed content root")
    affected = set(roots)
    queue = deque(sorted(roots))
    while queue:
        target = queue.popleft()
        for consumer in sorted(reverse[target] - affected):
            affected.add(consumer)
            if c.split_key(consumer)[0] in c.WRITABLE and not consumer.startswith("canonical_viewpoints/"):
                queue.append(consumer)
    views = {c.key("canonical_viewpoints", states[k]["viewpoint_id"])
             for k in affected if k.startswith("viewpoint_claim_links/")}
    for cv in views:
        if cv not in states:
            raise ValueError("paragraph scope missing current CVP")
        revision = c.key("viewpoint_revisions", states[cv]["current_revision_id"])
        if revision not in states:
            raise ValueError("paragraph scope missing current CVR")
        affected.update({cv, revision})
        affected.update(reverse[cv])  # ALL members/consumers, never top-k.
    context = affected | {doc_key}
    queue = deque(sorted(context))
    while queue:
        for target in sorted(forward[queue.popleft()] - context):
            context.add(target)
            queue.append(target)
    semantic = {
        "affected_keys": sorted(affected), "context_keys": sorted(context - affected),
        "unknown_keys": sorted(k for k in affected if c.split_key(k)[0] not in c.WRITABLE | c.IMPACT_ONLY),
        "reverse_edges": {k: sorted(reverse[k]) for k in sorted(affected)},
    }
    complete = c.inventory(states, sid)
    if not affected <= set(complete["affected_keys"]):
        raise ValueError("paragraph semantic scope is outside complete source impact")
    # Binding-only fragments can also be forward evidence of a semantic member.
    # In that case their FULL original payload stays in semantic context too.
    bindings = [{"record_key": k, "before_revision": states[k]["revision"],
                 "before_content_sha256": record_content_sha(states[k]),
                 "after_revision": p["revision"], "after_content_sha256": record_content_sha(p)}
                for k, p in sorted(binding_rows.items())]
    proof = {
        "schema_version": VERSION, "source_id": sid,
        "before_file": before["file"], "after_file": after["file"],
        "before_body_sha256": before["body_sha256"], "after_body_sha256": after["body_sha256"],
        "changed_paragraph_keys": sorted(changed), "semantic_roots": sorted(roots),
        "semantic_inventory": semantic, "complete_inventory_sha256": sha256_json(complete),
        "complete_current_records_sha256": sha256_json(states),
        "complete_affected_keys": complete["affected_keys"],
        "binding_only_rows": bindings,
        "unchanged_paragraphs_sha256": sha256_json({k: old_paragraphs[k] for k in sorted(set(old_paragraphs) - changed)}),
        "content_approval": False, "group_completion": False,
        "binding_rule": "Only source_sha256 and storage revision change; all quote, coordinate, ownership, qualification, review and payload fields remain exact.",
    }
    return {"proof": proof, "binding_postimages": c.rows(binding_rows)}


def verify(plan, manifest, states, before, after):
    """Recompute from exact source/current rows; a stored boolean is no authority."""
    if plan != partition(manifest, states, before, after):
        raise ValueError("paragraph scope/binding full postimage proof drift")
    return plan


def binding_review_scope(plan):
    """An explicit reviewer item for bookkeeping, never 370 content approvals.

    The full deterministic postimages remain in program material. The reviewer
    sees their complete ID/version/hash denominator and the preservation rule,
    separately from the semantic postimages it must actually judge. The common
    review validator requires this item in addition to every semantic verdict.
    This function alone neither approves a package nor enables runtime dispatch.
    """
    proof = plan["proof"]
    if (proof.get("schema_version") != VERSION or proof.get("content_approval") is not False
            or proof.get("group_completion") is not False):
        raise ValueError("binding review requires a non-approval paragraph partition")
    bindings = proof["binding_only_rows"]
    keys = [r["record_key"] for r in bindings]
    if keys != sorted(set(keys)) or any(not k.startswith("source_fragments/") for k in keys):
        raise ValueError("binding review cohort is duplicate/foreign/unordered")
    return {
        "schema_version": REVIEW_VERSION,
        "record_key": "source_epoch_binding/" + proof["source_id"],
        "partition_proof": deepcopy(proof),
        "partition_sha256": sha256_json(proof),
        "automatic_content_approval": False,
        "review_instruction": "Judge the exact source epoch transition and preservation accounting. "
            "Each cohort row retains its complete original quote, coordinates, ownership, qualification "
            "and review state; only source_sha256 and storage revision change. This verdict is NOT "
            "a fresh semantic approval of these rows or of unrelated Claims/CVPs. All semantic "
            "postimages require their own complete verdicts; all original affected groups require "
            "current independent audit before completion.",
    }


def validate_review_scope(scope):
    """Require the exact typed item; raw reviewer reasons/verdicts stay untouched."""
    if not isinstance(scope, dict) or scope.get("schema_version") != REVIEW_VERSION:
        raise ValueError("source epoch binding review schema mismatch")
    proof = scope.get("partition_proof")
    if not isinstance(proof, dict) or scope != binding_review_scope({"proof": proof}):
        raise ValueError("source epoch binding review receipt drift")
    return scope["record_key"]


def review_material(frozen, partition_plan):
    """Full semantic material plus ONE complete binding-accounting receipt.

    Do not duplicate the large complete cohort proof under two packet fields.
    Its exact value is retained in the typed scope, not dropped/summarized. This
    remains no-call material until the runtime/semantic compiler/completion
    integration is enabled and tested as a whole.
    """
    material = semantic_preview(frozen, partition_plan)
    original_proof = material.pop("source_epoch_binding_proof")
    material["source_epoch_binding_scope"] = binding_review_scope(partition_plan)
    if material["source_epoch_binding_scope"]["partition_proof"] != original_proof:
        raise ValueError("binding review material lost complete partition proof")
    return material


def validate_binding_operations(frozen, partition_plan, operations, current_before):
    """Validate the REAL plan cohort before the common transaction boundary.

    No reviewer boolean substitutes for recomputation. Do not normalize the
    mechanical rows through semantic provenance: doing so would manufacture
    approval for unchanged professor text. This is a stricter plan check only;
    it does not supply the still-required semantic approval or completion proof.
    """
    original = c.index(frozen["before_records"])
    if current_before != original:
        raise ValueError("source binding plan current readset drift")
    verify(partition_plan, frozen["manifest"], original, frozen["before"], frozen["after"][0])
    expected = c.index(partition_plan["binding_postimages"])
    actual = {}
    for op in operations:
        k = c.key(op["collection"], op["object_id"])
        if k not in expected:
            continue  # The common semantic plan validator checks all other ops.
        if k in actual:
            raise ValueError("duplicate source binding operation")
        old, new = original[k], expected[k]
        if (op["operation"] != "update" or op["before_revision"] != old["revision"]
                or op["after_revision"] != new["revision"]
                or record_content_sha(op["payload"]) != record_content_sha(new)
                or ("revision" in op["payload"] and op["payload"]["revision"] != new["revision"])):
            raise ValueError("source binding operation differs from exact unchanged-text postimage")
        actual[k] = op
    if set(actual) != set(expected):
        raise ValueError("source binding plan omitted a cohort operation")
    return sorted(actual)


def semantic_preview(frozen, plan):
    """No-call review-scope preview, NOT an executable or approved package.

    All source bytes remain physical-bound; full own body and ALL member sources
    remain visible. The complete source inventory/roster stay in saved material
    and transaction authority rather than being repeated as unrelated content.
    This preview cannot be passed to the common runner until its typed review,
    transaction and completion domains are integrated and independently tested.
    """
    states = c.index(frozen["before_records"])
    m = c.Manifest.model_validate(frozen["manifest"])
    verify(plan, m, states, frozen["before"], frozen["after"][0])
    proof = plan["proof"]
    inv = proof["semantic_inventory"]
    selected = set(inv["affected_keys"] + inv["context_keys"])
    source_ids = {states[k]["source_id"] for k in selected if k.startswith("source_documents/")}
    claims = {c.split_key(k)[1] for k in inv["affected_keys"] if k.startswith("claims/")}
    views = {c.split_key(k)[1] for k in inv["affected_keys"] if k.startswith("canonical_viewpoints/")}
    manifest = m.model_dump()
    manifest["permissions"] = [p.model_dump() for p in m.permissions if p.key in selected
                               and p.key not in {r["record_key"] for r in proof["binding_only_rows"]}]
    manifest["groups"] = [g.model_dump() for g in m.groups
                          if claims.intersection(g.claim_ids) or views.intersection(g.viewpoint_ids)]
    manifest.pop("campaign_claim_ids")
    return {
        "schema_version": VERSION, "scope_preview_only": True, "content_write_authority": False,
        "manifest_projection": manifest,
        "full_manifest_sha256": sha256_json(frozen["manifest"]),
        "campaign_member_count": len(m.campaign_claim_ids), "campaign_group_count": len(m.groups),
        "before_source": frozen["before"], "current_sources": frozen["after"],
        "context_sources": [s for s in frozen.get("context_sources", []) if s["source_id"] in source_ids],
        "sentence_inventory": frozen["sentence_inventory"], "inventory": inv,
        "records": c.rows({k: states[k] for k in sorted(selected)}),
        "source_epoch_binding_proof": proof,
        "execution_boundary": "Not a Proposal, Review or completion authority. No original model decisions/dispositions are removed or changed.",
    }
