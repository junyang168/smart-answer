"""Exact human disposition of an upheld erroneous support, shared SQL only.

The original payload and model rejection stay intact. Retirement withdraws
the active assertion, not the professor's Claim, evidence or application.
"""
import json
from pathlib import Path

from backend.api.canonical_repository.postgres_store import canonical_json, sha256_json
from backend.pipeline.cvp_sql_flow import binding, check_binding, pins, pin_rows, decoded_pins
from backend.pipeline.source_reconciliation_runner import read, body, write
from backend.pipeline.viewpoint_resolution_runtime import read_artifact

SCHEMA = 'wang_cvp_owner_sql_link_retirement_v1'


def validate_material(material, direction):
    old = material['before_link']; retained = material['retained_equivalent_link']
    wanted = {'schema_version': SCHEMA, 'run_id': material['run_id'],
              'campaign_sha256': material['campaign_sha256'],
              'link_id': old['viewpoint_claim_link_id'], 'before_link_sha256': sha256_json(old),
              'retained_link_id': retained['viewpoint_claim_link_id'],
              'retained_link_sha256': sha256_json(retained),
              'adjudication_packet_sha256': material['adjudication_packet_sha256'],
              'original_reports': material['original_reports'],
              'disposition': 'retire_only_this_supports_link', 'authorized_by': 'owner',
              'human_content_approval': True, 'automatic_future_repairs_authorized': False}
    if any(direction.get(k) != v for k, v in wanted.items()) or not direction.get('authorization_quote'):
        raise ValueError('human disposition does not bind exact retirement and retained equivalent')
    if (old.get('effective_state') != 'active' or old['link_type'] != 'supports'
            or retained.get('effective_state') != 'active'
            or retained['link_type'] not in {'equivalent_full', 'equivalent_component'}
            or retained['viewpoint_claim_link_id'] == old['viewpoint_claim_link_id']
            or any(old[k] != retained[k] for k in ('claim_id', 'viewpoint_id', 'pinned_claim_revision',
                                                  'validated_against_viewpoint_revision_id'))
            or old.get('supporting_relation_ids')):
        raise ValueError('retirement would change scope, coverage or relation-bound support')
    for row in material['records']:
        p = row['payload']
        if p.get('review_status') in {'human_hold', 'rejected', 'needs_review'} or p.get('effective_state') == 'held':
            raise ValueError('retirement cannot override a current hold')


def prepare_material(store, run_id, direction):
    from backend.pipeline.viewpoint_adjudication_history import validated_history
    reports = direction['original_reports']
    for report in reports: check_binding(report)
    adjudication, findings = validated_history(Path(direction['adjudication_root']))
    old_state = store.get_record_state('viewpoint_claim_links', direction['link_id'])
    if not old_state or old_state['retired']: raise ValueError('retirement target not active')
    old = old_state['payload']
    group=store.get_cvp_group(run_id)
    if old['viewpoint_id'] not in group.get('reviewed',{}).get('evidence',{}).get('viewpoint_ids',[]):
        raise ValueError('retirement target is outside this group affected audit')
    selected = [i for i in adjudication['items']
                if any(l.get('viewpoint_claim_link_id') == direction['link_id'] for l in i.get('connections', []))]
    if len(selected) != 1 or findings[selected[0]['item_id']]['verdict'] != 'audit_objection_upheld':
        raise ValueError('retirement requires a unique real upheld objection')
    item = selected[0]
    if old not in item['connections'] or store.get_record('claims', old['claim_id']) != item['claim']:
        raise ValueError('retirement current Claim/link differs from actual adjudication')
    records = store.read_affected_context([('viewpoint_claim_links', direction['link_id'])])
    records.update(store.read_cvp_context([old['claim_id']], viewpoint_ids=[old['viewpoint_id']]))
    retained = records['viewpoint_claim_links', direction['retained_link_id']]
    if retained['retired']: raise ValueError('retained equivalent was retired')
    sources={s['path']:s for s in item['sources']}
    for (collection, _), row in records.items():
        if collection=='source_documents' and not row['retired']:
            p=row['payload']; source={'path':p['source_path'],'file_sha256':p['source_file_sha256']}
            check_binding(source)
            sources.setdefault(source['path'],source)
    material = {'run_id': run_id, 'campaign_sha256': group['campaign_sha256'],
                'before_link': old, 'retained_equivalent_link': retained['payload'],
                'adjudication_packet_sha256': adjudication['artifact_sha256'],
                'original_reports': reports, 'adjudication_root': direction['adjudication_root'],
                'sources': [sources[p] for p in sorted(sources)], 'read_pins': pin_rows(pins(records)),
                'records': [{'collection': c, 'object_id': i, 'payload': r['payload']}
                            for (c, i), r in sorted(records.items()) if not r['retired']]}
    validate_material(material, direction)
    return json.loads(canonical_json(material))


def package_for(material, authorization, root):
    return {'package_id': 'CVP-LINK-RETIRE-' + sha256_json(material)[:24],
            'source_repair_application': {'schema_version': SCHEMA, 'authorization': authorization,
                                         'materials': binding(root/'materials.json')}}


def validate_package(package):
    app = package['source_repair_application']
    check_binding(app['authorization']); check_binding(app['materials'])
    material = body(read(Path(app['materials']['path'])))
    direction = read_artifact(app['authorization']['path'])
    validate_material(material, direction)
    for report in material['original_reports']: check_binding(report)
    for source in material['sources']: check_binding({'path': source['path'], 'file_sha256': source['file_sha256']})
    if package != package_for(material, app['authorization'], Path(app['materials']['path']).parent):
        raise ValueError('retirement package exceeds exact human disposition')
    return (('viewpoint_claim_links', material['before_link']['viewpoint_claim_link_id']),)


def execute(store, run_id, *, authorization, root, ports):
    from backend.pipeline.cvp_sql_actions import resume_bound_action
    from backend.pipeline.cvp_sql_flow import commit_package, verified_group_backup
    check_binding(authorization); direction = read_artifact(authorization['path'])
    action_id = 'CVPA-' + sha256_json({'kind': SCHEMA, 'link_id': direction['link_id'],
                                     'before_link_sha256': direction['before_link_sha256']})
    root = Path(root)/action_id
    store.bind_cvp_execution_root(run_id, identity=action_id, root=root)
    previous = store.get_cvp_action(run_id, action_id)
    if previous and previous['authorization'] != authorization:
        raise ValueError('retirement authority/root drift')
    resumed = resume_bound_action(store, run_id, action_id)
    if resumed: return resumed
    material = previous['materials'] if previous else prepare_material(store, run_id, direction)
    validate_material(material, direction)
    action = store.prepare_cvp_action(run_id, action_id=action_id, kind='owner_link_retirement',
                                     authorization=authorization, materials=material)
    write(root/'materials.json', material)
    package = package_for(material, authorization, root)
    validate_package(package)
    plan = store.plan_package(package, source_kind=SCHEMA, transactional=True)
    if len(plan.operations) != 1 or plan.operations[0].operation != 'retire':
        raise ValueError('retirement must be exactly one actual withdrawal')
    evidence = store.get_cvp_group(run_id)['reviewed']['evidence']
    return commit_package(store, run_id, package=package,
        approval={'human_exact_disposition': authorization, **{str(i): b for i, b in enumerate(material['original_reports'])}},
        authorization=authorization, backup=verified_group_backup(root, package, ports),
        sources=[{'path': s['path'], 'file_sha256': s['file_sha256']} for s in material['sources']],
        read_pins=decoded_pins(material['read_pins']), reason='human exact retirement of reversed support; original objection retained',
        actor='owner', source_kind=SCHEMA, viewpoint_ids=evidence['viewpoint_ids'],
        disposition=evidence.get('disposition'), action=action)
