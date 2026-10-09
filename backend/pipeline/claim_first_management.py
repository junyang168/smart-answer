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
    from backend.pipeline.cvp_sql_flow import binding
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


def validate_claim_audit_report(audit, claim_ids):
    from backend.pipeline.cvp_sql_flow import validate_transaction_audit
    validate_transaction_audit(audit,claim_ids,[])
    if set(audit['layers'])!={'1','2','3'} or audit.get('meta',{}).get('model')!='gemini-3.8-flash':
        raise ValueError('Claim audit must use independent model without CVP content management')
    report=audit['layers']['3'];results=report['results']
    if (not report.get('complete') or len(results)!=len(claim_ids)
            or report.get('judged')!=len(claim_ids)
            or {r['claim_id'] for r in results}!=set(claim_ids)
            or any(r.get('verdict')!='supported' or r.get('paragraphs_dropped_to_pass_filter',0) for r in results)
            or any(report.get(k,0) for k in ('model_errors','blocked','no_source_text','disputed'))):
        raise ValueError('Claim independent audit has actual unresolved/error; preserve original result, no retry')


def finish_claim_audit(store, run_id, root):
    """Exact source/Claim audit only. Never complete or start CVP management."""
    from pathlib import Path
    import os
    import subprocess
    from backend.pipeline.cvp_sql_flow import (binding, pins, pin_rows, decoded_pins,
        validate_transaction_audit, write_immutable, PROJECT)
    from backend.pipeline.source_reconciliation_runner import read, body
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    from backend.pipeline.viewpoint_content_blocked_fallback import verify_saved_full_audit
    saved=store.get_cvp_group(run_id); evidence=saved.get('reviewed',{}).get('evidence',{})
    application=evidence.get('package',{}).get('payload',{}).get('source_repair_application',{})
    original_root=Path(application.get('run_root',''))
    if (saved['checkpoint'] not in {'applied','audited'} or original_root.resolve()!=Path(root).resolve()
            or not evidence.get('pending_cvp_claim_ids')
            or not body(read(original_root/'final_review.packet.json')).get('claim_first_management')):
        raise ValueError('Claim-only audit lacks actual committed source-first action and pending CVP state')
    material=body(read(original_root/'scoped-materials.json'));c.guard_files(material)
    current={}
    for pin in material['sql_scope']['read_pins']:
        key=pin['collection'],pin['object_id'];row=store.get_record_state(*key)
        if row is None:raise ValueError('Claim audit current readset missing')
        current[key]=row
    current_pins=pin_rows(pins(current)); audit_root=original_root/'claim-first-audit'
    intent={'schema_version':VERSION,'claim_ids':saved['claim_ids'],'viewpoint_ids':[],
        'read_pins':current_pins,'change_set_ids':saved['change_set_ids'],
        'model':'gemini-3.8-flash','workers':2,'layers':['1','2','3'],'automatic_retry':False}
    path=audit_root/'audit.json'
    if not path.exists():
        if (audit_root/'audit-intent.json').exists():
            raise ValueError('Claim audit intent has no complete result; no automatic replay')
        write_immutable(audit_root/'audit-intent.json',intent)
        subprocess.run([str(PROJECT/'backend/.venv/bin/python'),'scripts/audit-library.py',
            '--scope','all','--layers','1,2,3','--claim-ids',','.join(saved['claim_ids']),
            '--workers','2','--model','gemini-3.8-flash','--no-status-file','--preserve-full-context',
            '--sql-group-scope','--out',str(audit_root)],cwd=PROJECT,check=True,
            env={**os.environ,'KNOWLEDGE_DATABASE_URL':store.database_url})
    if read_artifact(audit_root/'audit-intent.json')!=intent:
        raise ValueError('Claim audit committed version/readset changed')
    audit=read_artifact(path);validate_claim_audit_report(audit,saved['claim_ids'])
    source_proofs=verify_saved_full_audit(path,layers=('3',))
    receipt={'schema_version':VERSION,'status':'claim_layer_audited_cvp_pending',
        'audit':binding(path),'source_proofs':source_proofs,'claim_ids':saved['claim_ids'],
        'read_pins':current_pins,'change_set_ids':saved['change_set_ids'],
        'cvp_management_complete':False,'group_complete':False,'next_group_started':False}
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
