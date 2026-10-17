"""Append source-owned Evidence through the existing Claim/SQL lifecycle.

No runner, alternate budget or implicit CVP permission. Old policies opt out.
"""
from copy import copy

from backend.pipeline import source_reconciliation as c

VERSION = 'wang_claim_evidence_intake_v1'
BUCKETS = {'evidence_step_ids', 'eligible_evidence_step_ids'}
LINK_FIELDS = {'pinned_claim_revision', 'component_locator', 'evidence_bindings'}

PROMPT = '''
本次 policy 明示 wang_claim_evidence_intake_v1：可提出此前尚未記錄的自身正文論據。
僅在已有 Claim 下新增完整 EvidenceStep 和必要精確 SourceFragment；不是新增 Claim 或重新抽取全講道。
提供完整 Evidence 表述、step_type、speaker=professor、stance=asserted、discourse_role、
anchor_quality、support_eligibility=eligible_with_label、valid_for_identity_review=true、
produced_claim_ids 及 source_fragment_id(s)。不得冒充逐字經文、使用 AI 標題或外部來源。
全部 produced_claim_ids 必須是本次已有 issue Claims，各有完整自身正文判讀和逐字 citations。
Claim 完整後像只追加 evidence_step_ids；已有 eligible_evidence_step_ids 時同樣追加。
保留所有舊 Evidence、context/withheld buckets、occurrences、Claim 文字和其他欄位。
每個新 Evidence/fragment 和 Claim 後像都須有明示 disposition，不能 orphan 或移動舊證據。
已存在相同來源片段與論據不能重複建立。資格是待獨立審核的提案，不是 GPT 自我批准。
Opus 檢查每項完整新後像、論證作用及所有共享 Claims；必要最多原唯一纠正與終審。
新增 Evidence 亦改變 Claim 版本及 CVP 依據；全部舊連接/成員須評估或明示待管理。
已有有效連接時，返回全部自有連接完整後像，由本次 Opus 真正核查；只准必要版本/定位/證據引用。
保持原 type/target/CVR/state/其他欄位，不借此重新分類或退役；未完成後像不得提交。
不自動改 CVP/連接、提升 hold/context-only、改教授來源或原組成員。
SQL 提交後完整 Claim audit、所有受影響 CVP 審計和 pending 結案缺一不可。
'''


def enabled(frozen):
    from backend.pipeline.claim_issue_management import read_policy
    binding = frozen.get('claim_issue_context', {}).get('policy')
    return bool(binding and read_policy(binding, frozen.get('physical_snapshots')).evidence_intake == VERSION)


def manifest_enabled(manifest, snapshots=None):
    import json
    if not manifest.claim_issue:
        return False
    context = json.loads(c.bound_bytes(manifest.claim_issue, snapshots))
    from backend.pipeline.claim_issue_management import read_policy
    return read_policy(context['policy'], snapshots).evidence_intake == VERSION


def scoped_adapter(adapter, packet):
    if not enabled(packet):
        return adapter
    result = copy(adapter)
    result._base_prompt = adapter._base_prompt + '\n' + PROMPT
    result._prompt = adapter._prompt + '\n' + PROMPT
    result.prompt_sha256 = c.sha256_json({'prompt': result._prompt})
    return result


def allocation_matches(frozen, payload, permission):
    owners = payload.get('produced_claim_ids', [])
    return (enabled(frozen) and permission.parent_id == frozen['manifest']['source_id']
            and bool(owners) and len(owners) == len(set(owners))
            and set(owners) <= set(frozen['claim_issue_context']['claim_ids']))


def validate(frozen, proposal, before, post):
    """Strict append-only ownership and own-source citations BEFORE Opus review."""
    from backend.pipeline.claim_issue_management import HOLDS, Issue, bound_fragments
    new_keys = post.keys() - before.keys()
    evidence = {k: post[k] for k in new_keys if k.startswith('evidence_steps/')}
    changed = {k for k in before if k.startswith('claims/')
               and any(before[k].get(f) != post[k].get(f) for f in BUCKETS)}
    if not evidence and not changed:
        return
    if not enabled(frozen):
        raise ValueError('new Evidence requires explicit intake policy')
    issues = {Issue.model_validate(i).issue_id: Issue.model_validate(i)
              for i in frozen['claim_issue_context']['issues']}
    assessments = {issues[a.issue_id].claim_id: a for a in proposal.claim_assessments}
    owners = set()
    bases = set()
    for k, e in evidence.items():
        ids = e.get('produced_claim_ids', [])
        if (not e.get('statement', '').strip() or not e.get('step_type')
                or not e.get('discourse_role') or not e.get('anchor_quality')
                or e.get('speaker') != 'professor' or e.get('stance') != 'asserted'
                or e.get('support_eligibility') != 'eligible_with_label'
                or e.get('valid_for_identity_review') is not True
                or e.get('review_status') not in {'candidate', 'withheld_unreviewed'}
                or not ids or len(ids) != len(set(ids)) or not set(ids) <= assessments.keys()):
            raise ValueError('new Evidence lacks exact source-owned eligible draft/owners')
        fragments = bound_fragments([e], lambda fid: post[c.key('source_fragments', fid)])
        if not fragments or any(f.get('source_id') != frozen['manifest']['source_id']
                               or f.get('review_status') in {'human_review_required','human_hold',
                                   'withheld_unreviewed','rejected','superseded'} for f in fragments):
            raise ValueError('new Evidence needs unheld own-source fragments')
        def basis(values):
            return sorted((f['source_id'],f.get('source_sha256'),f.get('paragraph_key'),
                           f.get('verbatim_excerpt')) for f in values)
        token = c.sha256_json({'statement':e['statement'],'fragments':basis(fragments)})
        if token in bases:
            raise ValueError('duplicate proposed Evidence basis')
        bases.add(token)
        # Duplicate basis under a fresh ID is not new evidence intake.
        for old in before.values():
            if 'evidence_step_id' not in old:
                continue
            old_fragments = bound_fragments([old], lambda fid: before[c.key('source_fragments',fid)])
            if old.get('statement') == e['statement'] and basis(old_fragments) == basis(fragments):
                raise ValueError('duplicate existing Evidence basis')
        for cid in ids:
            a = assessments[cid]
            ck = c.key('claims', cid)
            if (a.cause != 'evidence_binding' or before[ck].get('review_status') in HOLDS
                    or e['evidence_step_id'] not in post[ck]['evidence_step_ids']
                    or not all(any(cite.source_id == f['source_id']
                                   and cite.paragraph_key == f['paragraph_key']
                                   and cite.exact_quote in f['verbatim_excerpt']
                                   for cite in a.citations) for f in fragments)):
                raise ValueError('new Evidence missing reciprocal assessed own Claim/citations')
            owners.add(ck)
    if changed != owners:
        raise ValueError('Evidence intake must append every and only assessed owner')
    for ck in owners:
        old, new = before[ck], post[ck]
        if ({k:v for k,v in c._semantic(old).items() if k not in BUCKETS}
                != {k:v for k,v in c._semantic(new).items() if k not in BUCKETS}):
            raise ValueError('Evidence intake cannot change Claim teaching/other buckets/anchors')
        appended = [post[k]['evidence_step_id'] for k in sorted(evidence)
                    if old['claim_id'] in post[k]['produced_claim_ids']]
        for bucket in BUCKETS:
            if bucket != 'evidence_step_ids' and bucket not in old:
                if bucket in new:
                    raise ValueError('intake preserves absent optional bucket shape')
                continue
            actual = new.get(bucket, [])
            prior = old.get(bucket, [])
            if (len(actual) != len(set(actual)) or actual[:len(prior)] != prior
                    or set(actual[len(prior):]) != set(appended)):
                raise ValueError('Evidence intake ownership must be append-only exact')
    for k, old in before.items():
        if k.startswith(('evidence_steps/', 'source_fragments/')) and post[k] != old:
            raise ValueError('fresh Evidence intake cannot alter old Evidence/fragment')
        if k.startswith('viewpoint_claim_links/') and c.key('claims',old['claim_id']) in owners:
            if old.get('effective_state') != 'active':
                if post[k] != old:raise ValueError('intake cannot revive retired connection')
                continue
            new = post[k]
            if (new == old or new['pinned_claim_revision'] != post[c.key('claims',old['claim_id'])]['revision']
                    or {f:v for f,v in c._semantic(old).items() if f not in LINK_FIELDS}
                    != {f:v for f,v in c._semantic(new).items() if f not in LINK_FIELDS}):
                raise ValueError('intake requires every active owned link jointly reviewed without meaning/type/target change')


def committed_context(value, store=None, *, cursor=None):
    """Authenticate exact applied intake; does NOT approve the full CVP audit."""
    from pathlib import Path
    from backend.pipeline.source_reconciliation_runner import body, read
    from backend.pipeline.cvp_sql_commit import check_binding, decoded_pins
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore, record_content_sha
    e = value.get('reviewed', {}).get('evidence', {})
    package = e.get('package', {}).get('payload', {})
    app = package.get('source_repair_application', {})
    if not app or not app.get('materials'):
        return None
    # Avoid looking through unrelated historical source operations.
    material = e.get('action', {}).get('materials', {})
    if not enabled(material):
        return None
    before=c.index(material['before_records'])
    if not any(c.key('evidence_steps',p['evidence_step_id']) not in before
               for p in package.get('evidence_steps',[])):
        return None  # A retain/no-content-write diagnosis is not an intake commit.
    check_binding(app['materials'])
    if body(read(Path(app['materials']['path']))) != material:
        raise ValueError('intake committed material drift')
    from backend.pipeline.cvp_sql_actions import validate_source_package
    validate_source_package(package)
    if (c.sha256_json(value['reviewed']) != value['reviewed_sha256']
            or app['materials_sha256'] != c.sha256_json(material)
            or e['package']['sha256'] != c.sha256_json(package)
            or app['run_id'] != value['run_id']):
        raise ValueError('intake committed approval/action identity drift')
    ids = material['claim_issue_context']['claim_ids']
    pending=e.get('pending_cvp_claim_ids',[])
    if (set(ids) - set(value['claim_ids']) or not set(ids) <= set(pending)
            or set(pending) - set(value['claim_ids']) or len(pending)!=len(set(pending))
            or len(ids) != len(set(ids))):
        raise ValueError('intake cannot enlarge roster or close unrelated pending Claims')
    if cursor is None:
        if store is None:raise ValueError('intake requires actual SQL authority')
        with store.connect() as conn, conn.cursor() as cur:
            return committed_context(value, cursor=cur)
    plan = value['reviewed']['plan']
    commit = cursor.execute('SELECT status,fingerprint_sha256,source_sha256,metadata FROM wang_knowledge.change_sets WHERE change_set_id=%s',
                            (plan['change_set_id'],)).fetchone()
    if (not commit or commit[:3] != ('applied',plan['fingerprint_sha256'],plan['source_sha256'])
            or commit[3].get('run_id') != value['run_id']
            or commit[3].get('reviewed_sha256') != value['reviewed_sha256']
            or commit[3].get('approval') != e['approval']):
        raise ValueError('intake actual applied SQL approval drift')
    from backend.pipeline.claim_issue_management import Issue
    for issue in map(Issue.model_validate,material['claim_issue_context']['issues']):
        row=cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s',('CVPI-'+issue.issue_id,)).fetchone()
        if (not row or row[0].get('owner_run_id') != value['run_id']
                or row[0].get('action') != {'run_id':value['run_id'],'action_id':e['action']['action_id']}):
            raise ValueError('intake actual registered issue/action drift')
    fields=('operation','collection','object_id','before_revision','after_revision','before_sha256','after_sha256')
    actual=cursor.execute('SELECT operation,collection,object_id,before_revision,after_revision,before_sha256,after_sha256 FROM wang_knowledge.change_operations WHERE change_set_id=%s ORDER BY operation_index',
                          (plan['change_set_id'],)).fetchall()
    if actual != [tuple(op[f] for f in fields) for op in plan['operations']]:
        raise ValueError('intake complete actual SQL writeset drift')
    pins=decoded_pins(material['sql_scope']['read_pins'])
    for op in plan['operations']:
        key=op['collection'],op['object_id']
        row=cursor.execute('SELECT payload,content_sha256 FROM wang_knowledge.object_versions WHERE collection=%s AND object_id=%s AND revision=%s AND change_set_id=%s',
                           (*key,op['after_revision'],plan['change_set_id'])).fetchone()
        if not row or row != (op['payload'],op['after_sha256']) or record_content_sha(row[0]) != row[1]:
            raise ValueError('intake full SQL history postimage drift')
        pins[key]=(op['after_revision'],op['after_sha256'],False)
    PostgresKnowledgeStore._assert_transaction_read_pins(cursor,pins)
    active=cursor.execute("SELECT object_id,payload FROM wang_knowledge.objects WHERE collection='viewpoint_claim_links' AND retired_at IS NULL AND payload->>'claim_id'=ANY(%s) AND payload->>'effective_state'='active'",(ids,)).fetchall()
    written={p['viewpoint_claim_link_id']:p for p in package.get('viewpoint_claim_links',[])}
    if set(written) != {ident for ident,p in active} or any(p != written[ident] for ident,p in active):
        raise ValueError('intake current all-owned-link population drift')
    covered = {p['claim_id'] for ident,p in active if p['link_type'].startswith('equivalent_')}
    if not {p['viewpoint_id'] for ident,p in active} <= set(e['viewpoint_ids']):
        raise ValueError('intake full affected CVP population missing')
    return {'schema_version':VERSION,'claim_ids':ids,'joint_links_approved':covered == set(pending),
            'materials':app['materials'],'change_set_id':plan['change_set_id'],
            'content_write_authority':False,'group_complete':False}


def validate_completion(value,cursor):
    """The new Evidence must actually occur in the complete Claim audit pins."""
    from pathlib import Path
    from backend.pipeline.cvp_sql_commit import check_binding, decoded_pins
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    from backend.pipeline.claim_first_management import VERSION as CLAIM_AUDIT_VERSION, validate_claim_audit_report
    from backend.pipeline.viewpoint_content_blocked_fallback import verify_saved_full_audit, KEY
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    authority=committed_context(value,cursor=cursor)
    if not authority:return None
    marker=value.get('claim_layer_audit',{})
    if marker.get('status')!='claim_layer_audited_cvp_pending' or marker.get('cvp_management_complete') is not False:
        raise ValueError('Evidence intake complete Claim audit remains pending')
    proof=marker['receipt'];check_binding(proof);receipt=read_artifact(proof['path'])
    app=value['reviewed']['evidence']['package']['payload']['source_repair_application']
    if (proof['path']!=str(Path(app['run_root'])/'claim-first-audit/claim-layer-completion.json')
            or receipt.get('schema_version')!=CLAIM_AUDIT_VERSION
            or receipt.get('status')!=marker['status'] or receipt.get('claim_ids')!=value['claim_ids']
            or receipt.get('change_set_ids')!=value['change_set_ids']
            or any(receipt.get(f) is not False for f in ('cvp_management_complete','group_complete','next_group_started'))):
        raise ValueError('Evidence intake Claim audit scope/status drift')
    check_binding(receipt['audit']);audit=read_artifact(receipt['audit']['path'])
    validate_claim_audit_report(audit,value['claim_ids'])
    if audit.get('meta',{}).get(KEY) and (receipt.get('revisit_required') is not True or receipt.get('independent_complete') is not False):
        raise ValueError('intake safety fallback disclosure missing')
    if verify_saved_full_audit(Path(receipt['audit']['path']),layers=('3',))!=receipt['source_proofs']:
        raise ValueError('intake complete Claim model/source proof drift')
    expected=decoded_pins(receipt['read_pins'])
    for op in value['reviewed']['plan']['operations']:
        key=op['collection'],op['object_id'];pin=(op['after_revision'],op['after_sha256'],False)
        if expected.get(key)!=pin:
            raise ValueError('intake Claim audit omitted new Evidence/fragment/link generation')
    audited=decoded_pins(audit['meta']['registry_read_scope']['read_pins'])
    required={('claims',cid) for cid in value['claim_ids']} | {
        (op['collection'],op['object_id']) for op in value['reviewed']['plan']['operations']
        if op['collection'] in {'claims','evidence_steps','source_fragments'}}
    if any(audited.get(key)!=expected[key] for key in required):
        raise ValueError('intake independent Claim audit missing complete new source graph')
    PostgresKnowledgeStore._assert_transaction_read_pins(cursor,expected)
    return authority
