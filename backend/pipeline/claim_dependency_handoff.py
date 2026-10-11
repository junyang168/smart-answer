"""Current, jointly reviewed Claim impacts; execution groups are not owners.

The original pending list and all semantic verdicts remain unchanged. This
handoff is only for a committed source package that already reviewed ALL of
the dependency's active connections. Uncovered Claims still use coverage.
"""
from pathlib import Path
from backend.api.canonical_repository.postgres_store import sha256_json, record_content_sha
from backend.pipeline import source_reconciliation as c
from backend.pipeline.source_reconciliation_runner import read, body
from backend.pipeline.cvp_sql_flow import check_binding

VERSION = 'wang_joint_reviewed_dependency_handoff_v1'


def subject(value):
    e = value.get('reviewed', {}).get('evidence', {})
    a = e.get('action', {})
    if a.get('kind') != 'source_claim_revision':
        return None
    app = e.get('package', {}).get('payload', {}).get('source_repair_application', {})
    if not app or app.get('source_write_slice'):
        return None
    check_binding(app['materials'])
    materials = body(read(Path(app['materials']['path'])))
    context = materials.get('claim_issue_context', {})
    issues = context.get('issues', [])
    ids = context.get('claim_ids', [])
    if len(ids) != 1 or len(issues) != 1:
        return None
    if e.get('pending_cvp_claim_ids') != ids:
        # This accounting domain cannot resolve another Claim or source slice.
        return None
    if not e['package']['payload'].get('viewpoint_claim_links'):
        # A Claim-only source approval is not a connection approval. Leave it
        # in the existing required-coverage path rather than invent coverage.
        return None
    if (issues[0]['claim_id'] != ids[0] or app['run_id'] != value['run_id']
            or materials != a['materials'] or sha256_json(materials) != a['materials_sha256']
            or app['materials_sha256'] != a['materials_sha256']
            or materials['sql_scope']['run_id'] != value['run_id']):
        raise ValueError('dependency source action/material/issue identity drift')
    return app['materials'], ids[0], materials


def joint_links(materials, package, cid):
    before = c.index(materials['before_records'])
    claims = [p for p in package.get('claims', []) if p['claim_id'] == cid]
    if len(claims) != 1 or len(package.get('claims', [])) != 1:
        raise ValueError('dependency handoff requires one exact corrected Claim')
    claim = claims[0]
    old = {p['viewpoint_claim_link_id']: p for k, p in before.items()
           if k.startswith('viewpoint_claim_links/') and p['claim_id'] == cid
           and p['effective_state'] == 'active'}
    new = {p['viewpoint_claim_link_id']: p for p in package.get('viewpoint_claim_links', [])}
    if not old or set(new) != set(old) or len(new) != len(package.get('viewpoint_claim_links', [])):
        raise ValueError('dependency handoff omitted/added/duplicated active owned links')
    for ident, link in new.items():
        if (link.get('review_status') != 'system_approved'
                or link['pinned_claim_revision'] != claim['revision']
                or any(link.get(k) != old[ident].get(k) for k in ('claim_id', 'viewpoint_id',
                    'link_type', 'effective_state', 'validated_against_viewpoint_revision_id',
                    'occurrence_refs', 'supporting_relation_ids'))):
            raise ValueError('dependency handoff lacks complete approved unchanged link roles/targets')
    return new


def context(value, store=None, *, cursor=None):
    selected = subject(value)
    if selected is None:
        return None
    if cursor is None:
        if store is None:
            raise ValueError('dependency handoff requires actual SQL commit authority')
        with store.connect() as conn, conn.cursor() as cur:
            return context(value, cursor=cur)
    proof, cid, materials = selected
    e = value['reviewed']['evidence']; package = e['package']['payload']; plan = value['reviewed']['plan']
    if sha256_json(value['reviewed'])!=value['reviewed_sha256']:
        raise ValueError('dependency handoff supplied review differs from its actual commit identity')
    if e.get('pending_cvp_claim_ids') != [cid]:
        raise ValueError('dependency handoff cannot clear unrelated pending obligations')
    from backend.pipeline.cvp_sql_actions import validate_source_package
    validate_source_package(package)  # Exact original actual model transport and full postimages.
    links = joint_links(materials, package, cid)
    if not set(p['viewpoint_id'] for p in links.values()) <= set(e['viewpoint_ids']):
        raise ValueError('dependency handoff omitted affected CVP population')
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.claim_issue_management import Issue
    issue = Issue.model_validate(materials['claim_issue_context']['issues'][0])
    row = cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s',
                         ('CVPI-' + issue.issue_id,)).fetchone()
    if (not row or row[0].get('owner_run_id') != value['run_id']
            or row[0].get('scope_amendment')
            or row[0].get('action') != {'run_id': value['run_id'], 'action_id': e['action']['action_id']}):
        raise ValueError('dependency handoff lacks exact registered original issue/action')
    if not PostgresKnowledgeStore._cvp_action_committed(cursor, value['run_id'], e['action']['action_id']):
        raise ValueError('dependency handoff original source action not committed')
    commit = cursor.execute('SELECT status,fingerprint_sha256,source_sha256,metadata FROM wang_knowledge.change_sets WHERE change_set_id=%s',
                            (plan['change_set_id'],)).fetchone()
    if (not commit or commit[:3] != ('applied', plan['fingerprint_sha256'], plan['source_sha256'])
            or commit[3].get('run_id') != value['run_id']
            or commit[3].get('reviewed_sha256') != value['reviewed_sha256']
            or commit[3].get('approval') != e['approval']):
        raise ValueError('dependency handoff lacks exact applied reviewed commit')
    ops = plan['operations']
    actual = cursor.execute('SELECT operation,collection,object_id,before_revision,after_revision,before_sha256,after_sha256 FROM wang_knowledge.change_operations WHERE change_set_id=%s ORDER BY operation_index',
                            (plan['change_set_id'],)).fetchall()
    fields = ('operation','collection','object_id','before_revision','after_revision','before_sha256','after_sha256')
    if actual != [tuple(op[k] for k in fields) for op in ops]:
        raise ValueError('dependency handoff actual complete write set drift')
    from backend.pipeline.cvp_sql_flow import decoded_pins
    pins = decoded_pins(materials['sql_scope']['read_pins'])
    written = set()
    for op in ops:
        key = (op['collection'], op['object_id'])
        if key in written or op['operation'] not in {'update', 'create'}:
            raise ValueError('dependency handoff duplicate/retired operation')
        written.add(key)
        history = cursor.execute('SELECT payload,content_sha256 FROM wang_knowledge.object_versions WHERE collection=%s AND object_id=%s AND revision=%s AND change_set_id=%s',
                                 (*key, op['after_revision'], plan['change_set_id'])).fetchone()
        if (not history or history[0] != op['payload'] or history[1] != op['after_sha256']
                or record_content_sha(history[0]) != history[1]):
            raise ValueError('dependency handoff full historical afterimage drift')
        pins[key] = (op['after_revision'], op['after_sha256'], False)
    PostgresKnowledgeStore._assert_transaction_read_pins(cursor, pins)
    rows = cursor.execute("SELECT object_id FROM wang_knowledge.objects WHERE collection='viewpoint_claim_links' AND retired_at IS NULL AND payload->>'claim_id'=%s AND payload->>'effective_state'='active'", (cid,)).fetchall()
    if {r[0] for r in rows} != set(links):
        raise ValueError('dependency handoff current active owned-link population drift')
    result={'schema_version': VERSION, 'materials': proof, 'claim_id': cid,
            'change_set_id': plan['change_set_id'], 'original_group_claim_ids': value['claim_ids'],
            'pending_claim_ids': [cid], 'link_ids': sorted(links), 'viewpoint_ids': sorted({p['viewpoint_id'] for p in links.values()}),
            'campaign_membership_changes': 0, 'content_write_authority': False,
            'group_complete': False}
    if e.get('pending_cvp_management_record_keys'):
        result['separate_pending_record_keys']=list(e['pending_cvp_management_record_keys'])
    return result
