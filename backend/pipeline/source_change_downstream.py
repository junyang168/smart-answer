"""Source-change adapter to ordinary CVP management and whole-CVP route execution."""
import json
from pathlib import Path

from backend.pipeline.claim_management_intake import immutable
from backend.pipeline.cvp_sql_commit import binding
from backend.api.canonical_repository import cvp_management as domain
from backend.api.canonical_repository.cvp_recall import seal, STATEMENT_SCOPE_PROFILE

POLICIES = Path(__file__).parent/'policies'


def run(store, ids, retired, root, *, instruction):
    from backend.pipeline import cvp_management_runner as management
    from backend.pipeline import viewpoint_route_cvp_worker as routes
    root = Path(root).resolve(); root.mkdir(parents=True, exist_ok=True)
    immutable(root/'intake.json', {'claim_ids':ids, 'retired_claim_ids':retired, 'instruction':instruction})
    request_path = root/'request.json'
    if request_path.exists():
        request = domain.Request.model_validate_json(request_path.read_text())
    else:
        links = store.read_cvp_links(claim_ids=ids+retired)
        vids = sorted({row['payload']['viewpoint_id'] for row in links.values() if not row.get('retired')})
        members = store.read_cvp_links(viewpoint_ids=vids) if vids else {}
        permissions = []
        for vid in vids:
            row = store.get_record_state('canonical_viewpoints', vid)
            permissions.append(domain.Permission(collection='canonical_viewpoints', object_id=vid,
                fields=['current_revision_id','summary','summary_basis']))
        for (col, oid), row in members.items():
            if not row.get('retired'):
                permissions.append(domain.Permission(collection=col, object_id=oid,
                    fields=sorted(domain.FIELDS[col]-{'claim_id'})))
        # Related CVP relations are actual maintenance objects, not permission
        # to rewrite unrelated CVPs. The common runner supplies all endpoints.
        with store.connect() as conn:
            relations = conn.execute("""SELECT object_id FROM wang_knowledge.objects
                WHERE collection='viewpoint_relations' AND retired_at IS NULL
                  AND (payload->>'source_viewpoint_id'=ANY(%s) OR payload->>'target_viewpoint_id'=ANY(%s))""",
                (vids, vids)).fetchall() if vids else []
        permissions += [domain.Permission(collection='viewpoint_relations', object_id=oid,
            fields=sorted(domain.FIELDS['viewpoint_relations'])) for (oid,) in relations]
        target_ids = sorted(set(ids)-set(retired))
        if not target_ids:
            return {'status':'completed' if not vids else 'paused',
                    'reason':'no_downstream_consumers' if not vids else 'retired_only_cvp_requires_disposition',
                    'viewpoint_ids':vids, 'model_calls':0}
        inp = seal({'schema_version':'wang_cvp_recall_preflight_v1', 'claim_ids':target_ids,
            'grouping':{'scope_label':'source change', 'groups':[{'group_key':'source-change',
                'claim_ids':target_ids, 'rationale':'Current source corrections; maintain complete existing memberships and retire active links for these retired Claims: '+', '.join(retired)}]},
            'cvp_policy':str((POLICIES/'wang_cvp_resolution_policy_v7.json').resolve()),
            'claim_input_policy':str((POLICIES/'wang_cvp_claim_input_v1.json').resolve()),
            'index_root':None, 'recall_request_bytes':600000, 'recall_max_calls':20,
            'recall_profile':STATEMENT_SCOPE_PROFILE})
        auth = root/'authorization.json'
        request = domain.Request(action_id='source-cvp-'+domain.sha256_json(str(root))[:24], input=inp,
            mode='maintain' if vids else 'create', maintenance_viewpoint_ids=vids, permissions=permissions,
            authorization={'path':str(auth),'file_sha256':'0'*64},
            budget={'max_calls':20,'max_revisions':1,'max_request_bytes':600000,'max_output_tokens':32000},
            creation_slots={'canonical_viewpoints':min(len(target_ids),100), 'viewpoint_revisions':min(len(target_ids)+len(vids),100),
                'viewpoint_claim_links':min(len(target_ids)*2,100),'viewpoint_relations':min(len(target_ids)*2,100)})
        direction = {'schema_version':'wang_cvp_management_authorization_v1','authorized_by':'owner',
            **{k:request.model_dump(mode='json')[k] for k in ['action_id','mode','maintenance_viewpoint_ids','permissions','creation_slots','budget']},
            'input_sha256':inp['artifact_sha256']}
        request.authorization = domain.Binding.model_validate(immutable(auth, direction))
        immutable(request_path, request.model_dump(mode='json'))
    result_path = root/'cvp-result.json'
    result = json.loads(result_path.read_text()) if result_path.exists() else management.run(store, request, root=root/'execution', execute=True)
    if result.get('status') == 'completed':
        immutable(result_path, result)
    if result.get('status') != 'completed':
        return {'status':'paused','stage':'cvp_management','result':result}
    # A retired Claim's old active membership cannot be treated as cleaned up
    # simply because the management action itself reached a terminal result.
    stale = [col+'/'+oid for (col,oid), row in store.read_cvp_links(claim_ids=retired).items()
             if not row.get('retired') and row['payload'].get('effective_state') == 'active'] if retired else []
    if stale:
        return {'status':'paused','stage':'retired_memberships','record_keys':stale}
    parent = store.get_cvp_group('CVPM-'+management.sha256_json(request.action_id))
    affected = set(request.maintenance_viewpoint_ids)
    for unit in parent['units'].values():
        if unit.get('completed_step'):
            step = store.get_cvp_group(unit['completed_step'])
            affected.update(routes.route_affected_viewpoints(step['reviewed']['evidence']['package']['payload']))
    jobs_path = root/'route-jobs.json'
    if not jobs_path.exists():
        jobs = {vid: routes.enqueue_current_cvp_routes(store, [vid], root/'route-queue')[0] for vid in sorted(affected)}
        immutable(jobs_path, jobs)
    jobs = json.loads(jobs_path.read_text())
    results = {}
    for vid, jid in jobs.items():
        routes.run_queued_cvp_route(store, root/'route-queue', root/'routes', job_id=jid,
            worker_id='source-change-'+domain.sha256_json(str(root))[:16])
        state = json.loads((root/'route-queue/states'/(jid+'.json')).read_text())
        if state.get('status') == 'complete':
            verified = routes.resume_applied_cvp_route(store, vid, root/'routes'/jid)
            results[vid] = verified or {'status':'paused', 'reason':'missing_applied_route_readback'}
        else:
            results[vid] = {'status':'paused','job_state':state}
    return {'status':'completed' if all(row.get('status') == 'applied_readback_verified' for row in results.values()) else 'paused',
            'cvp_management':result,'routes':results,'viewpoint_ids':sorted(affected)}
