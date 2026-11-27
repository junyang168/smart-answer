"""Read-only CVP update input adaptation; execution stays in CVP maintain.

Membership and explicit replacement candidates select material, never approve
membership or inherit old roles. No Claim diff, model call or database write.
"""
from backend.api.canonical_repository.cvp_recall import seal, STATEMENT_SCOPE_PROFILE
from backend.api.canonical_repository.cvp_management import FIELDS
from backend.api.canonical_repository.postgres_store import record_content_sha


def current_update_scope(store, update):
    if (not isinstance(update, dict) or set(update) != {'viewpoint_ids', 'replacement_claim_ids'}):
        raise ValueError('update requires explicit viewpoints and replacement Claim candidates')
    for key in ('viewpoint_ids', 'replacement_claim_ids'):
        values = update[key]
        if (not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values)
                or len(values) != len(set(values))):
            raise ValueError('update identities must be unique nonempty strings')
    if not update['viewpoint_ids']:
        raise ValueError('update needs a current viewpoint')
    views = store.read_current_records(('canonical_viewpoints', vid) for vid in update['viewpoint_ids'])
    if len(views) != len(update['viewpoint_ids']):
        raise ValueError('update viewpoint is missing')
    links = {k:r for k,r in store.read_cvp_links(viewpoint_ids=update['viewpoint_ids']).items()
             if r['payload'].get('effective_state') in {'active','invalidated'}}
    records = {**views, **links}
    if any(r.get('retired') or record_content_sha(r['payload']) != r['content_sha256'] for r in records.values()):
        raise ValueError('update has retired or inconsistent viewpoint/link material')
    if any(r['payload'].get('identity_status') != 'active' for r in views.values()):
        raise ValueError('update starts from active identities; resume committed inactive work instead')
    members = {r['payload']['claim_id'] for r in links.values()}
    candidates = set(update['replacement_claim_ids'])
    claims = store.read_current_records(('claims', cid) for cid in members | candidates)
    if len(claims) != len(members | candidates):
        raise ValueError('update current Claim is missing; resolve upstream first')
    if any(record_content_sha(r['payload']) != r['content_sha256'] for r in claims.values()):
        raise ValueError('update current Claim is inconsistent')
    unavailable = {cid for (col,cid),r in claims.items()
                   if r.get('retired') or r['payload'].get('review_status') == 'superseded'}
    if unavailable & candidates:
        raise ValueError('replacement Claim candidate is retired or superseded')
    records.update(claims)
    store.verify_current_read_pins({key:(r['revision'],r['content_sha256'],bool(r.get('retired')))
                                   for key,r in records.items()})
    return {'claim_ids': sorted((members | candidates) - unavailable),
            'unavailable_claim_ids': sorted(unavailable), 'records': records}


def prepare_update_input(store, base_input, *, viewpoint_ids, replacement_claim_ids=()):
    """Freeze one complete dependency unit in the existing input envelope.

    Usually one CVP. Include both existing endpoints when retarget/merge is
    intended; ordinary permissions and whole-afterimage review still apply.
    Current unqualified members remain in the cohort so admission holds them.
    """
    update = {'viewpoint_ids': list(viewpoint_ids), 'replacement_claim_ids': list(replacement_claim_ids)}
    scope = current_update_scope(store, update)
    return seal({k:v for k,v in base_input.items() if k not in {'artifact_sha256','update'}} | {
        'recall_profile': STATEMENT_SCOPE_PROFILE, 'update': update,
        'claim_ids': scope['claim_ids'], 'grouping': {'scope_label':'CVP update', 'groups':[
            {'group_key':'cvp-update', 'claim_ids':scope['claim_ids'],
             'rationale':'完整当前成员与显式替代候选；不以生产批次截断。'}]}})


def update_permissions(store, viewpoint_ids):
    """Concrete existing-object field scope for the normal task authorization.

    Approved CVRs remain immutable; successors use existing creation slots.
    Relation endpoint context does not grant authority over its other CVP.
    """
    records = store.read_current_records(('canonical_viewpoints',vid) for vid in viewpoint_ids)
    records.update(store.read_cvp_links(viewpoint_ids=viewpoint_ids))
    records.update(store.read_cvp_relations(viewpoint_ids=viewpoint_ids))
    return [{'collection':col, 'object_id':oid, 'fields':sorted(FIELDS[col] - {'claim_id'})}
            for (col,oid),row in sorted(records.items()) if not row.get('retired') and
            (col == 'canonical_viewpoints' or row['payload'].get('effective_state') in {'active','invalidated'})]


def affected_update_viewpoints(store, claim_ids):
    """Use the existing reverse dependency reader only for changed inputs.

    Current links, relation support, attestations and observations can all
    depend on a Claim. Historical production candidates do not create new
    update obligations just because they once mentioned it.
    """
    ids=set(claim_ids)
    if not ids:return []
    context=store.read_affected_context([('claims',cid) for cid in sorted(ids)])
    vids=set()
    for (col,_),row in context.items():
        if row.get('retired'):continue
        p=row['payload']
        if col=='viewpoint_claim_links' and p.get('effective_state') in {'active','invalidated'} and p['claim_id'] in ids:
            vids.add(p['viewpoint_id'])
        elif col=='viewpoint_relations' and p.get('effective_state')=='active' and ids.intersection(p.get('supporting_claim_ids',[])):
            vids.update((p['source_viewpoint_id'],p['target_viewpoint_id']))
        elif col=='viewpoint_observation_supports' and p.get('effective_state')=='active' and any(
                b['claim_id'] in ids for b in p['evidence_bindings']):
            vids.add(p['viewpoint_id'])
        elif col=='argument_route_attestations' and p.get('effective_state')=='active' and ids.intersection(p['claim_ids']):
            route=context.get(('argument_routes',p['argument_route_id']))
            if route and not route.get('retired') and route['payload']['route_status']=='active':
                vids.add(route['payload']['conclusion_viewpoint_id'])
    rows=store.read_current_records(('canonical_viewpoints',vid) for vid in vids)
    return sorted(vid for (col,vid),row in rows.items() if not row.get('retired')
                  and row['payload'].get('identity_status')=='active')
