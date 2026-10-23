"""Finish source-owned repairs before any CVP management.

Project the authenticated correction, not its verdicts. CVP proposals and
unresolved findings remain saved and explicitly pending, never silently retained
or approved. Use only the original, still unused final review and SQL action.
"""
from copy import deepcopy

from backend.pipeline import source_reconciliation as c
from backend.api.canonical_repository.viewpoint_foundation import sha256_json

VERSION = 'wang_claim_first_management_v1'
SOURCE_COLUMNS = {'claims', 'evidence_steps', 'source_fragments'}
CVP_COLUMNS = {'canonical_viewpoints', 'viewpoint_revisions', 'viewpoint_claim_links', 'viewpoint_relations'}
PENDING_PROBLEM = 'pending_cvp_management'
REPROPOSAL_VERSION = 'wang_post_claim_cvp_reproposal_v1'

REVIEW_SCOPE = '''
本次完整終審的寫入範圍是 Claim/Evidence/fragment，本源先完成，CVP 之後另行處理。
這是明示分域提交，不是忽略影響或批准原 CVP。保留全部上下文與每個 disposition。
若 packet.claim_first_management 與 source_first_pending_cvp 明示此契約：
所有 CVP head/revision/link/relation 必須逐字不變、明示 pending，原 CVP 提案與未決不撤回。
對 pending_record_keys 返回 approved=false、problems=["pending_cvp_management"]，不是批准身份或保留結論。
對其他每項核對完整自身正文、精確本源後像及將 CVP 待驗而阻止結案的安全性，成立才 approved=true。
只有全部本源與非 CVP 項成立才允許本源交易；CVP 未審不因此要求先補等價成員，也不使本源自動拒絕。
真本源／來源／資格／歸屬／版本／hold 問題仍拒絕。只用原一次終審，不新增纠正或額外模型輪次。
此明示本源先行契約取代一般聯合提交時「全部 CVP 先完成才提交」的順序；不改任何內容忠實性門檻。
'''


def scoped_adapter(adapter, packet):
    if not packet.get('claim_first_management'):
        return adapter
    if (packet['claim_first_management'].get('schema_version') != VERSION
            or not packet.get('manifest', {}).get('claim_issue')):
        raise ValueError('Claim-first prompt requires exact scoped Claim contract')
    from copy import copy
    result=copy(adapter)
    result._base_prompt=adapter._base_prompt+'\n'+REVIEW_SCOPE
    result._prompt=adapter._prompt+'\n'+REVIEW_SCOPE
    result.prompt_sha256=sha256_json({'prompt':result._prompt})
    return result


def project(frozen, original):
    if not frozen.get('manifest', {}).get('claim_issue'):
        raise ValueError('Claim-first requires existing bounded Claim management')
    c.validate_proposal(frozen, original, review_only=True)
    result = deepcopy(original)
    result.records = [r for r in result.records if c.split_key(r.key)[0] in SOURCE_COLUMNS]
    if not result.records:
        raise ValueError('Claim-first has no source-owned repair to submit')
    if any(c.split_key(r.key)[0] not in SOURCE_COLUMNS | CVP_COLUMNS for r in original.records):
        raise ValueError('Claim-first cannot discard foreign source operations')
    writes = {r.key for r in result.records}
    for d in result.dispositions:
        col = c.split_key(d.record_key)[0]
        if col in CVP_COLUMNS:
            d.action = 'unresolved'
            d.reason = 'Program domain separation: CVP management pending; original finding/proposal preserved. ' + d.reason
        elif d.record_key in writes:
            d.action = 'update'
        elif d.action == 'update':
            raise ValueError('Claim-first omitted a non-CVP proposed update')
    for impact in result.cvp_impacts:
        impact.action = 'needs_direction'
        impact.reason = 'Pending CVP management after source-owned repair; not a human approval request. ' + impact.reason
    c.validate_proposal(frozen, result, claim_first=True)
    return result


def final_packet(root, frozen, original, correction):
    from backend.pipeline.source_reconciliation_runner import read, body
    from backend.pipeline.cvp_sql_commit import binding
    root = __import__('pathlib').Path(root)
    if (root/'final_review.reservation.json').exists() or (root/'final_review.raw.json').exists():
        prior = body(read(root/'final_review.packet.json'))
        if not prior.get('claim_first_management'):
            raise ValueError('Claim-first cannot replace an already reserved final review')
    proposal = project(frozen, original)
    packet = c.review_packet(frozen, proposal, claim_first=True)
    packet['claim_first_management'] = {
        'schema_version': VERSION,
        'original_correction': binding(root/'correction.raw.json'),
        'original_response_sha256': sha256_json(correction['response']),
        'original_proposal_sha256': sha256_json(original.model_dump()),
        'source_write_keys': sorted(r.key for r in proposal.records),
        'pending_cvp_keys': sorted(d.record_key for d in proposal.dispositions
                                  if c.split_key(d.record_key)[0] in CVP_COLUMNS),
        'owner_direction': '先把 Claim 相关问题彻底解决, 再来看 CVP, 顺序要搞清楚!',
        'human_content_approval': False,
        'additional_gpt_calls': 0,
        'original_final_review_budget_only': True,
        'instructions': 'Review every source-owned Claim/Evidence/fragment and its complete own body/postimage. '
            'All CVP heads/revisions/links/relations stay byte-for-byte unchanged and explicitly PENDING, '
            'not semantically retained or approved. Their old proposals/findings remain historical. '
            'Return all disposition IDs. For each pending CVP ID return approved=false, '
            'problems=["pending_cvp_management"], with a reason that no CVP content is approved. '
            'For every other record independently approve/reject its actual postimage and safety of deferring CVP management. '
            'Source-only commit must keep group completion blocked; no further correction is allowed.'}
    return proposal, packet


def validate_claim_audit_report(audit, claim_ids, *, store=None, saved=None, root=None):
    from backend.pipeline.cvp_sql_commit import validate_transaction_audit
    accounting = None
    validation = audit
    if store is not None:
        from backend.pipeline.claim_source_write_slice import pending_locator_audit_view
        validation, accounting = pending_locator_audit_view(store, saved, root, audit)
    validate_transaction_audit(validation,claim_ids,[])
    if set(audit['layers'])!={'1','2','3'} or audit.get('meta',{}).get('model')!='gemini-3.8-flash':
        raise ValueError('Claim audit must use independent model without CVP content management')
    report=audit['layers']['3'];results=report['results']
    if (not report.get('complete') or len(results)!=len(claim_ids)
            or report.get('judged')!=len(claim_ids)
            or {r['claim_id'] for r in results}!=set(claim_ids)
            or any(r.get('verdict')!='supported' or r.get('paragraphs_dropped_to_pass_filter',0) for r in results)
            or any(report.get(k,0) for k in ('model_errors','blocked','no_source_text','disputed'))):
        raise ValueError('Claim independent audit has actual unresolved/error; preserve original result, no retry')
    return accounting


def finish_claim_audit(store, run_id, root):
    """Exact source/Claim audit only. Never complete or start CVP management."""
    from pathlib import Path
    import os
    import subprocess
    from backend.pipeline.cvp_sql_commit import binding, pins, pin_rows, decoded_pins, validate_transaction_audit, PROJECT
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    from backend.pipeline.source_reconciliation_runner import read, body
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    from backend.pipeline.viewpoint_content_blocked_fallback import verify_saved_full_audit
    saved=store.get_cvp_group(run_id); evidence=saved.get('reviewed',{}).get('evidence',{})
    application=evidence.get('package',{}).get('payload',{}).get('source_repair_application',{})
    original_root=Path(application.get('run_root',''))
    statement_slice=application.get('source_write_slice',{}).get('mode') in {'owner_statement_completion','policy_evidence_binding','policy_claim_source_revision'}
    from backend.pipeline.source_paragraph_impact import committed_context
    paragraph = committed_context(store, saved)
    from backend.pipeline.claim_evidence_intake import committed_context as intake_context
    intake = intake_context(saved,store)
    if statement_slice:
        from backend.pipeline.claim_source_write_slice import validate_claim_layer_authority
        validate_claim_layer_authority(saved,root)
    if (saved['checkpoint'] not in {'applied','audited'} or original_root.resolve()!=Path(root).resolve()
            or not evidence.get('pending_cvp_claim_ids')
            or not statement_slice and not paragraph and not intake and not body(read(original_root/'final_review.packet.json')).get('claim_first_management')):
        raise ValueError('Claim-only audit lacks actual committed source-first action and pending CVP state')
    material=body(read(original_root/'scoped-materials.json'));c.guard_files(material)
    current={}
    for pin in material['sql_scope']['read_pins']:
        key=pin['collection'],pin['object_id'];row=store.get_record_state(*key)
        if row is None:raise ValueError('Claim audit current readset missing')
        current[key]=row
    if intake:
        for op in saved['reviewed']['plan']['operations']:
            key=op['collection'],op['object_id']
            current[key]=store.get_record_state(*key)
    current_pins=pin_rows(pins(current)); audit_root=original_root/'claim-first-audit'
    dependency_ids = sorted({op['object_id'] for op in paragraph['plan']['operations']
                             if op['collection']=='claims'} - set(saved['claim_ids'])) if paragraph else []
    audit_ids = sorted(set(saved['claim_ids']) | set(dependency_ids)) if paragraph else saved['claim_ids']
    intent={'schema_version':VERSION,'claim_ids':audit_ids,'viewpoint_ids':[],
        'read_pins':current_pins,'change_set_ids':saved['change_set_ids'],
        'model':'gemini-3.8-flash','workers':2,'layers':['1','2','3'],'automatic_retry':False}
    if paragraph:
        intent.update(original_group_claim_ids=saved['claim_ids'], dependency_claim_ids=dependency_ids,
                      campaign_membership_changes=0, source_paragraph_profile=paragraph['frozen']['source_paragraph_execution']['schema_version'])
    path=audit_root/'audit.json'
    if not path.exists():
        if (audit_root/'audit-intent.json').exists():
            raise ValueError('Claim audit intent has no complete result; no automatic replay')
        write_immutable(audit_root/'audit-intent.json',intent)
        subprocess.run([str(PROJECT/'backend/.venv/bin/python'),'scripts/audit-library.py',
            '--scope','all','--layers','1,2,3','--claim-ids',','.join(audit_ids),
            '--workers','2','--model','gemini-3.8-flash','--no-status-file','--preserve-full-context',
            '--sql-group-scope','--out',str(audit_root)],cwd=PROJECT,check=True,
            env={**os.environ,'KNOWLEDGE_DATABASE_URL':store.database_url})
    if read_artifact(audit_root/'audit-intent.json')!=intent:
        raise ValueError('Claim audit committed version/readset changed')
    from backend.pipeline.viewpoint_content_blocked_fallback import apply as content_fallback, KEY as FALLBACK_KEY
    path=content_fallback(path,audit_root/'content-blocked-fallback')
    audit=read_artifact(path)
    accounting=validate_claim_audit_report(audit,audit_ids,**({} if paragraph else {'store':store,'saved':saved,'root':root}))
    source_proofs=verify_saved_full_audit(path,layers=('3',))
    receipt={'schema_version':VERSION,'status':'claim_layer_audited_cvp_pending',
        'audit':binding(path),'source_proofs':source_proofs,'claim_ids':saved['claim_ids'],
        'read_pins':current_pins,'change_set_ids':saved['change_set_ids'],
        'cvp_management_complete':False,'group_complete':False,'next_group_started':False}
    if paragraph:
        receipt.update(dependency_claim_ids=dependency_ids, audited_claim_ids=audit_ids,
                       campaign_membership_changes=0, source_paragraph_profile=intent['source_paragraph_profile'])
    if audit.get('meta',{}).get(FALLBACK_KEY):
        receipt.update(revisit_required=True,independent_complete=False)
    if accounting is not None:
        receipt['pending_cvp_locator_accounting']=accounting
    receipt_path=audit_root/'claim-layer-completion.json';write_immutable(receipt_path,receipt)
    with store.connect() as conn,conn.cursor() as cursor:
        cursor.execute("SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s FOR UPDATE",(run_id,))
        value=cursor.fetchone()[0]
        if value.get('change_set_ids')!=saved['change_set_ids'] or value.get('checkpoint')!='applied':
            raise ValueError('Claim-layer audit cannot replace a different group checkpoint')
        store._assert_transaction_read_pins(cursor,decoded_pins(current_pins))
        marker={'receipt':binding(receipt_path),'status':receipt['status'],'cvp_management_complete':False}
        if value.get('claim_layer_audit') and value['claim_layer_audit']!=marker:
            raise ValueError('Claim-layer audit receipt drift')
        value['claim_layer_audit']=marker
        from backend.api.canonical_repository.postgres_store import canonical_json
        cursor.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb,heartbeat_at=now() WHERE run_id=%s",(canonical_json(value),run_id))
    return receipt


def coverage_context(store, saved, group):
    """Authenticate the completed Claim layer, not a new CVP approval.

    This consumes the SQL-pinned receipt once per coverage preparation. It
    neither reruns audits/models nor resurrects the uncommitted joint proposal.
    Existing SQL links stay visible to the normal reviewer and pending impacts
    cannot be cleared merely by creating replacement coverage.
    """
    from pathlib import Path
    from backend.pipeline.cvp_sql_commit import check_binding, decoded_pins, binding
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    from backend.pipeline.source_reconciliation_runner import body, read
    marker = saved.get('claim_layer_audit')
    if not marker:
        return None
    evidence = saved['reviewed']['evidence']
    application = evidence['package']['payload'].get('source_repair_application', {})
    root = Path(application.get('run_root', ''))
    from backend.pipeline.claim_evidence_intake import committed_context as intake_context
    intake = intake_context(saved,store)
    receipt_binding = marker.get('receipt', {})
    if (saved['checkpoint'] != 'applied' or marker.get('status') != 'claim_layer_audited_cvp_pending'
            or marker.get('cvp_management_complete') is not False
            or receipt_binding.get('path') != str(root/'claim-first-audit/claim-layer-completion.json')
            or application.get('run_id') != saved['run_id']
            or saved['claim_ids'] != group['claim_ids']
            or (not intake and evidence.get('pending_cvp_claim_ids') != saved['claim_ids'])):
        raise ValueError('CVP reproposal requires exact completed original Claim layer')
    check_binding(receipt_binding)
    receipt = read_artifact(receipt_binding['path'])
    if (receipt.get('schema_version') != VERSION or receipt.get('status') != marker['status']
            or receipt.get('claim_ids') != saved['claim_ids']
            or receipt.get('change_set_ids') != saved['change_set_ids']
            or any(receipt.get(k) is not False for k in
                   ('cvp_management_complete', 'group_complete', 'next_group_started'))):
        raise ValueError('Claim completion receipt scope or status drift')
    check_binding(receipt['audit'])
    for proof in receipt['source_proofs']:
        check_binding(proof)
    if intake:
        from backend.pipeline.claim_evidence_intake import validate_completion
        with store.connect() as conn, conn.cursor() as cursor:
            validate_completion(saved,cursor)
        return {'schema_version':REPROPOSAL_VERSION,
            'management_action_id':evidence['action']['action_id'],
            'prior_reviewed_sha256':saved['reviewed_sha256'],
            'claim_layer_completion':receipt_binding,
            'source_terminal_review':evidence['approval']['terminal_review'],
            'source_terminal_packet':binding(root/(application['terminal_stage']+'.packet.json')),
            'pending_existing_cvp_record_keys':list(evidence.get('pending_cvp_management_record_keys',[])),
            'source_only_approval':True,'existing_cvp_content_approved':False,
            'old_proposals_and_budgets_preserved':True,
            'instructions':'Use the actual reviewed new Evidence and unchanged Claim teaching. '
                'All incomplete coverage and retained CVP impacts still require normal management/full audits. '
                'No source/Claim retry, new member, endpoint or CVP-core permission is granted.'}
    from backend.pipeline.viewpoint_validation_context import read_current_objects
    from backend.pipeline.cvp_sql_commit import pins
    from backend.api.canonical_repository.postgres_store import record_content_sha
    expected = decoded_pins(receipt['read_pins'])
    current = read_current_objects(store, expected)
    if pins(current) != expected or any(record_content_sha(r['payload']) != r['content_sha256']
            or (not r['retired'] and r['payload'].get('revision') != r['revision'])
            for r in current.values()):
        raise ValueError('completed Claim audit current dependency drift')
    plan = saved['reviewed']['plan']
    history = store.get_change_history(plan['change_set_id'])
    from backend.pipeline.source_paragraph_impact import committed_context, PROFILE
    paragraph = committed_context(store, saved)
    if (not history or history['status'] != 'applied' or not history['operations']
            or not paragraph and any(op['collection'] not in SOURCE_COLUMNS for op in history['operations'])
            or plan['change_set_id'] not in receipt['change_set_ids']):
        raise ValueError('Claim-first transaction is not exclusively committed source work')
    if paragraph:
        audit_ids = sorted(set(saved['claim_ids']) | {op['object_id'] for op in plan['operations'] if op['collection']=='claims'})
        if (receipt.get('source_paragraph_profile') != PROFILE or receipt.get('campaign_membership_changes') != 0
                or receipt.get('audited_claim_ids') != audit_ids
                or receipt.get('dependency_claim_ids') != sorted(set(audit_ids)-set(saved['claim_ids']))):
            raise ValueError('paragraph Claim audit lost exact dependency/original roster accounting')
        validate_claim_audit_report(read_artifact(receipt['audit']['path']), audit_ids)
        from backend.pipeline.viewpoint_content_blocked_fallback import verify_saved_full_audit
        if verify_saved_full_audit(Path(receipt['audit']['path']), layers=('3',)) != receipt['source_proofs']:
            raise ValueError('paragraph complete Claim audit proof drift')
        terminal_stage = application['terminal_stage']
        packet_path = root/(terminal_stage+'.packet.json')
        check_binding(binding(packet_path))
        return {'schema_version': REPROPOSAL_VERSION,
                'management_action_id': evidence['action']['action_id'],
                'prior_reviewed_sha256': saved['reviewed_sha256'],
                'claim_layer_completion': receipt_binding,
                'source_terminal_review': paragraph['terminal_review'],
                'source_terminal_packet': binding(packet_path),
                'pending_existing_cvp_record_keys': sorted(evidence.get('pending_cvp_management_record_keys', [])),
                'source_only_approval': True, 'existing_cvp_content_approved': False,
                'old_proposals_and_budgets_preserved': True, 'source_slice_mode': PROFILE,
                'instructions': 'Use the actually committed new source and fully Claim-audited current graph. '
                    'Original proposals, failed source judgments and budgets remain historical. '
                    'Every existing/current CVP and all source pending obligations require separate full current audits. '
                    'No Claim roster change, candidate promotion, old verdict rewrite or CVP content approval is granted.'}
    packet_path = root/'final_review.packet.json'
    packet = body(read(packet_path))
    slice_mode = application.get('source_write_slice', {}).get('mode')
    if slice_mode in {'policy_evidence_binding', 'policy_claim_source_revision'}:
        from backend.pipeline.claim_source_write_slice import validate_claim_layer_authority
        validate_claim_layer_authority(saved, root)
        if (sha256_json(saved['reviewed']) != saved['reviewed_sha256']
                or history['fingerprint_sha256'] != plan['fingerprint_sha256']
                or history['source_sha256'] != plan['source_sha256']
                or history['metadata'].get('run_id') != saved['run_id']
                or history['metadata'].get('reviewed_sha256') != saved['reviewed_sha256']
                or history['metadata'].get('approval') != evidence['approval']):
            raise ValueError('source slice coverage actual SQL approval drift')
        def identity(op):
            return op['collection'],op['object_id'],op['operation'],op['after_revision'],op['after_sha256']
        if sorted(map(identity, plan['operations'])) != sorted(map(identity, history['operations'])):
            raise ValueError('source slice coverage actual write set drift')
        for op in history['operations']:
            row=current.get((op['collection'],op['object_id']))
            if not row or row['retired'] or row['payload'] != op['after']:
                raise ValueError('source slice coverage current/history postimage drift')
        accounting=validate_claim_audit_report(read_artifact(receipt['audit']['path']), saved['claim_ids'],store=store,saved=saved,root=root)
        if receipt.get('pending_cvp_locator_accounting') != accounting:
            raise ValueError('source slice pending locator accounting receipt drift')
        from backend.pipeline.viewpoint_content_blocked_fallback import verify_saved_full_audit
        if verify_saved_full_audit(Path(receipt['audit']['path']), layers=('3',)) != receipt['source_proofs']:
            raise ValueError('source slice complete Claim audit proof drift')
        pending = evidence.get('pending_cvp_management_record_keys', [])
        declared = packet.get('source_write_slice', {}).get('pending_dispositions', [])
        unresolved = sorted(d['record_key'] for d in declared if d['action'] == 'unresolved')
        if sorted(pending) != unresolved or len(pending) != len(set(pending)):
            raise ValueError('source slice coverage lost separate pending obligations')
        terminal = evidence['approval']['terminal_review']
        if terminal.get('path') != str(root/'final_review.raw.json'):
            raise ValueError('source slice coverage terminal review root drift')
        check_binding(terminal)
        return {'schema_version': REPROPOSAL_VERSION,
            'management_action_id': evidence['action']['action_id'],
            'prior_reviewed_sha256': saved['reviewed_sha256'],
            'claim_layer_completion': receipt_binding,
            'source_terminal_review': terminal,
            'source_terminal_packet': binding(packet_path),
            'pending_existing_cvp_record_keys': sorted(pending),
            'source_only_approval': True, 'existing_cvp_content_approved': False,
            'old_proposals_and_budgets_preserved': True,
            'source_slice_mode': slice_mode,
            'instructions': 'Propose required CVP coverage from the actually committed and independently '
                'Claim-audited current source graph. The old unsubmitted CVP proposal and all consumed '
                'budgets remain historical, not approval of current material. Do not repeat source '
                'repair or reopen a rejected unchanged candidate. Existing links/core remain unchanged. '
                'All separate pending source/question/relationship obligations remain unresolved and '
                'block group completion; this handoff grants no mutation or semantic approval for them.'}
    if not packet.get('claim_first_management'):
        raise ValueError('source terminal review did not defer CVP approval')
    pending = packet.get('source_first_pending_cvp', {}).get('pending_record_keys', [])
    if (not pending or len(pending) != len(set(pending))
            or any(c.split_key(k)[0] not in CVP_COLUMNS for k in pending)):
        raise ValueError('Claim-first handoff lost pending existing CVP impacts')
    terminal = evidence['approval']['terminal_review']
    program = evidence['approval'].get('final_review_program_binding')
    if terminal.get('path') != str(root/'final_review.raw.json') or not program:
        raise ValueError('Claim-first handoff lacks SQL-bound original final review')
    check_binding(terminal); check_binding(program)
    raw = read(root/'final_review.raw.json')
    bound = read_artifact(program['path'])
    verdicts = bound.get('bound_response',{}).get('verdicts',[])
    observed = [v['record_key'] for v in verdicts
                if not v['approved'] and v['problems']==[PENDING_PROBLEM]]
    if (raw['artifact_sha256'] != application.get('review_sha256')
            or bound.get('raw_artifact_sha256') != raw['artifact_sha256']
            or bound.get('bound_response',{}).get('proposal_sha256') != application.get('proposal_sha256')
            or packet.get('proposal_sha256') != application.get('proposal_sha256')
            or sorted(observed) != sorted(pending)):
        raise ValueError('source-only final review lost exact pending CVP verdict binding')
    from backend.pipeline.cvp_sql_commit import binding
    return {'schema_version': REPROPOSAL_VERSION,
        'management_action_id': evidence['action']['action_id'],
        'prior_reviewed_sha256': saved['reviewed_sha256'],
        'claim_layer_completion': receipt_binding,
        'source_terminal_review': evidence['approval']['terminal_review'],
        'source_terminal_packet': binding(packet_path),
        'pending_existing_cvp_record_keys': sorted(pending),
        'source_only_approval': True, 'existing_cvp_content_approved': False,
        'old_proposals_and_budgets_preserved': True,
        'instructions': 'Propose CVP coverage from the current approved Claim/Evidence, not from the '
            'uncommitted old joint CVP proposal. Reuse an existing identity only if actually equivalent; '
            'otherwise propose a supported distinct viewpoint. Do not force a new equivalent member '
            'for an old core, revive retired Claims, change Claim/Evidence/source, or repeat a rejected '
            'membership candidate. Existing committed links remain visible and cannot be silently '
            'discarded, retired, duplicated or labelled approved by the source-only review. '
            'Their pending management is separate from new coverage and still prevents group completion.'}
