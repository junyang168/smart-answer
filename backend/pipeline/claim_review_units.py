"""Bounded same-source review units on the existing Claim Update executor.

Only an explicitly frozen policy opts in. Existing actions keep their original
scope, budgets, findings and wire contracts.
"""
import json
from pathlib import Path

CONTRACT = 'claim_bounded_review_unit_v1'
MAX_CLAIMS = 10


def enabled(request, policy=None):
    if policy is not None:
        return getattr(policy, 'review_unit_contract', None) == CONTRACT
    from backend.pipeline import claim_management as cm
    if isinstance(request, dict):
        request = cm.Request.model_validate(request)
    bound = getattr(request, 'policy', None)
    if bound is None or not Path(bound.path).is_file():
        return False
    return json.loads(cm.source.verify_file(bound).read_text()).get('review_unit_contract') == CONTRACT


def validate_members(groups):
    """Freeze membership once; no graph traversal discovers new review roots."""
    from backend.pipeline.claim_management_intake import claim_ids
    if not isinstance(groups, list) or not groups:
        raise ValueError('review units require a nonempty list of Claim ID lists')
    seen = set()
    for group in groups:
        ids = claim_ids(group)
        if len(ids) != len(group) or len(ids) > MAX_CLAIMS or seen.intersection(ids):
            raise ValueError('review units must be disjoint, without duplicates, and contain 1..10 Claims')
        seen.update(ids)
    return groups


def prepare(members, root, *, instruction, prior_reviews=None, policy=None, store=None):
    """One frozen unit; previous passes are references, never model targets.

    Prior review IDs identify SQL history, not caller-provided approval JSON.
    A failed reuse check stops preparation instead of quietly re-reviewing an
    already reviewed Claim. The ordinary executor owns all remaining work.
    """
    from backend.pipeline import claim_management as cm, claim_management_intake as intake
    from backend.pipeline import claim_review_reuse
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    validate_members([members])
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError('review unit requires a concrete owner instruction')
    prior_reviews = prior_reviews or {}
    if set(prior_reviews) - set(members):
        raise ValueError('prior reviews must belong to the fixed review unit')
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    store = store or PostgresKnowledgeStore()
    policy = Path(policy or Path(__file__).parent/'policies/wang_claim_management_bounded_unit_v1.json')
    selected = cm.Policy.model_validate_json(policy.read_text())
    if not enabled(None, selected) or len(members) > selected.joint_update_max_targets:
        raise ValueError('unit requires an explicit bounded policy with sufficient capacity')
    pb = intake.immutable(root/'unit-policy.json', selected.model_dump(mode='json'))
    intent = {'members':members, 'prior_reviews':prior_reviews, 'instruction':instruction, 'policy':pb}
    intake.immutable(root/'unit-intent.json', intent)
    saved_path = root/'unit-prepared.json'
    if saved_path.exists():
        saved = json.loads(saved_path.read_text())
        if saved['members'] != members:
            raise ValueError('saved review unit membership drift')
        update = saved['prepared_update']
        if update and update['manifest']:
            from backend.pipeline.cvp_sql_commit import check_binding
            from backend.pipeline.claim_management_batch import load
            check_binding(update['manifest'])
            load(update['manifest']['path'], root/'update'/'batch', store=store)
        return saved
    records = store.read_claim_context([('claims', cid) for cid in members])
    payloads = {cm.source.key(*k):v['payload'] for k,v in records.items()}
    sources = [cm.own_source_ids(payloads['claims/'+cid], payloads) for cid in members]
    if len(sources[0]) != 1 or any(s != sources[0] for s in sources):
        raise ValueError('review unit must belong to exactly one same source')
    references, pending, proofs = {}, [], {}
    for cid in members:
        q = store.claim_management_qualification(cid)
        if q and q.get('ready'):
            references[cid] = q
        elif cid in prior_reviews:
            proofs[cid] = claim_review_reuse.build(store, cid, prior_reviews[cid])
        else:
            # Do not silently dispatch an already fully reviewed bystander
            # merely because the caller omitted its historical round ID.
            with store.connect() as conn, conn.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')
                cursor.execute("""SELECT run_id,metadata FROM wang_knowledge.pipeline_runs
                    WHERE metadata->>'kind'='claim_management_round' AND metadata->'claim_ids' ? %s
                      AND metadata->>'outcome'='completed' AND metadata->>'opus_approved'='true'
                      AND metadata->>'program_readback_complete'='true'
                    ORDER BY started_at DESC,run_id DESC""", (cid,))
                previous = cursor.fetchall()
            full = []
            for rid, value in previous:
                from backend.pipeline.claim_direct_scope import scope
                rq = cm.Request.model_validate(value['materials']['unit_request'])
                candidate = cm.Candidate.model_validate(value['candidate'])
                if (value['materials']['policy'].get('claim_scope_contract') != 'claim_direct_neighbours_v1'
                        or 'claims/'+cid in scope(rq, value['materials']['before'], candidate)['content_claim_keys']):
                    full.append(rid)
            if full:
                failures = []
                for rid in full:
                    try:
                        proofs[cid] = claim_review_reuse.build(store, cid, rid)
                        break
                    except (ValueError, OSError, KeyError) as exc:
                        failures.append(str(exc))
                if cid not in proofs:
                    raise ValueError('previous complete review needs focused reconciliation, not automatic re-review: '
                                     + cid + ': ' + '; '.join(failures))
            else:
                pending.append(cid)
    # Every proposed reuse is checked before any additive registration.
    for cid in proofs:
        claim_review_reuse.admit(store, cid, proofs[cid]['review_run_id'])
        q = store.claim_management_qualification(cid)
        if not q or not q.get('ready'):
            raise ValueError('admitted review reuse is no longer current: ' + cid)
        references[cid] = q
    prepared = intake.prepare(pending, root/'update', instruction=instruction, groups=[pending],
        policy=pb['path'], store=store) if pending else None
    result = {'members':members, 'reference_claim_ids':[cid for cid in members if cid in references],
              'target_claim_ids':pending, 'reference_qualifications':references,
              'prepared_update':prepared, 'model_calls':0}
    # Current qualifications may legitimately change; only the intent above
    # is immutable. This is a derived view, not a second qualification store.
    from backend.pipeline.claim_management_batch import progress
    progress(root/'unit-prepared.json', result)
    return result


def finish(prepared, *, store):
    """Recheck every original member after the ordinary transaction/readback."""
    qualifications = {cid:store.claim_management_qualification(cid) for cid in prepared['members']}
    return {'members':prepared['members'], 'qualifications':qualifications,
            'completed':all(q and q.get('ready') for q in qualifications.values())}


def run(members, root, *, instruction, prior_reviews=None, policy=None, store=None, model_capacity=None):
    """Dispatch only the existing batch, then verify the fixed whole unit."""
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline import claim_management_batch as batch
    store = store or PostgresKnowledgeStore()
    prepared = prepare(members, root, instruction=instruction, prior_reviews=prior_reviews, policy=policy, store=store)
    update = prepared['prepared_update']
    if update and update['manifest']:
        batch.run(update['manifest']['path'], Path(root)/'update'/'batch', model_capacity=model_capacity)
    result = finish(prepared, store=store)
    batch.progress(Path(root)/'unit-result.json', result)
    return result


def main():
    import argparse
    from dotenv import load_dotenv
    load_dotenv('.env')
    parser = argparse.ArgumentParser(description='One fixed same-source Claim review unit, maximum ten members')
    parser.add_argument('--members-file', required=True)
    parser.add_argument('--instruction-file', required=True)
    parser.add_argument('--root', required=True)
    parser.add_argument('--policy')
    parser.add_argument('--prior-reviews-file')
    parser.add_argument('--capacity-file')
    parser.add_argument('--run', action='store_true', help='Otherwise only prepare; no model dispatch')
    args = parser.parse_args()
    kwargs = dict(instruction=Path(args.instruction_file).read_text(), policy=args.policy,
                  prior_reviews=json.loads(Path(args.prior_reviews_file).read_text()) if args.prior_reviews_file else None)
    if args.run:
        kwargs['model_capacity'] = json.loads(Path(args.capacity_file).read_text()) if args.capacity_file else None
    result = (run if args.run else prepare)(json.loads(Path(args.members_file).read_text()), args.root, **kwargs)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
