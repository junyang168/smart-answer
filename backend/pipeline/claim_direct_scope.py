"""One-hop Claim context, complete own evidence, bounded maintenance impacts.

Related Claims are immutable relation endpoints, not new content-review roots.
The existing SQL transaction readset remains separate from this model scope.
"""
import json
from pathlib import Path

CONTRACT = 'claim_direct_neighbours_v1'
EVIDENCE_OWNER_NOTE = (
    'Exact frozen same-source Claim records explicitly named by Evidence.produced_claim_ids but absent from before. '
    'Use only to check existing ownership and duplicate judgments before approving Claim creation. '
    'They are reference facts, not new review roots, writable objects or approvals. '
    'Compare the full statement, attribution and limitations against the professor source; existence alone '
    'does not prove an equivalent judgment. '
    'Do not infer absence from this unit or from a missing owner payload; do not recreate a judgment '
    'already stated by a listed active Claim.')

RETIREMENT_REFERENCE_NOTE = (
    'Explicit current Claim snapshots for comparing preservation of the retired teaching. '
    'They are read-only transaction pins, not additional review targets, write permissions '
    'or new approvals. Compare the full judgments, attribution and limitations against the '
    'complete professor sources. Existence or topical similarity alone is insufficient.')


def retirement_reference_keys(request):
    """An opt-in Retire comparison never expands write or review authority."""
    from backend.pipeline import claim_management as cm
    bound = request.authorization
    if request.operation != 'Retire' or bound.file_sha256 == '0' * 64 or not Path(bound.path).is_file():
        return []
    keys = json.loads(cm.source.verify_file(bound).read_text()).get('retirement_reference_keys', [])
    if (not isinstance(keys, list) or any(not isinstance(k, str) or k.count('/') != 1
            or not k.startswith('claims/') or not k.split('/', 1)[1].strip()
            or k.split('/', 1)[1] != k.split('/', 1)[1].strip() for k in keys)
            or len(keys) != len(set(keys))):
        raise ValueError('retirement references require distinct explicit Claim keys')
    if set(keys) & (set(request.target_keys) | {p.key for p in request.permissions}):
        raise ValueError('retirement references must remain outside targets and write permissions')
    return keys


def retirement_references(request, transaction_before):
    """Use actual frozen SQL rows, including their exact versions; never prose snapshots."""
    from backend.pipeline import claim_management as cm
    keys = retirement_reference_keys(request)
    if not keys:
        return {}
    rows = {k:v['payload'] for k,v in transaction_before.items()}
    result = {}
    for key in keys:
        state = transaction_before.get(key)
        if state:
            state = cm.model_state(state)
        if (not state or state['retired'] or state['payload'].get('superseded_by')
                or state['payload'].get('review_status') in cm.HELD):
            raise ValueError('retirement reference must be an active unheld frozen Claim: ' + key)
        own = cm.own_source_ids(state['payload'], rows)
        if len(own) != 1 or not own <= set(request.source_bindings):
            raise ValueError('retirement reference requires its complete authorized own source: ' + key)
        result[key] = state
    return result


def owner_context_enabled(request):
    """New authorizations opt in; frozen packets retain their exact table."""
    from backend.pipeline import claim_management as cm
    bound = request.authorization
    return (bound.file_sha256 != '0' * 64 and Path(bound.path).is_file()
        and json.loads(cm.source.verify_file(bound).read_text()).get('include_evidence_owner_context') is True)


def evidence_owner_references(request, before, transaction_before):
    """Resolve declared owners for duplicate checks, without traversing Claims."""
    from copy import deepcopy
    from backend.pipeline import claim_management as cm
    if owner_context_enabled(request):
        # Provided Evidence owners are already in the resolvable table.
        # Do not discover further Claims from their private reference Evidence.
        return {}
    rows = {k:v['payload'] for k,v in transaction_before.items()}
    ids = {cid for key,state in before.items() if key.startswith('evidence_steps/')
           for cid in state['payload'].get('produced_claim_ids', [])}
    result = {}
    for cid in sorted(ids):
        key = cm.source.key('claims', cid)
        if key in before:
            continue
        if key not in transaction_before:
            raise ValueError('declared Evidence owner missing from frozen transaction context: ' + key)
        own = cm.own_source_ids(rows[key], rows)
        if own and own <= set(request.source_bindings):
            result[key] = deepcopy(transaction_before[key])
    return result


def enabled(request, policy=None):
    from backend.pipeline import claim_management as cm
    if isinstance(request, dict):
        request = cm.Request.model_validate(request)
    # Structural/Create/source-impact contracts retain their existing scope.
    if getattr(request, 'operation', None) not in {'Update', 'Review'} or cm.is_source_impact(request):
        return False
    if policy is None:
        bound = getattr(request, 'policy', None)
        if bound is None or not Path(bound.path).is_file():
            return False
        policy = json.loads(cm.source.verify_file(bound).read_text())
        return policy.get('claim_scope_contract') == CONTRACT
    return policy.claim_scope_contract == CONTRACT


def review_roots(request, *, actual_write_keys=(), bounded=None):
    """The same semantic frontier for packets, guards, commits and readback."""
    from backend.pipeline.claim_review_units import enabled as bounded_unit
    if bounded is None:
        bounded = bounded_unit(request)
    targets = set(request.target_keys)
    if bounded:
        from backend.pipeline.claim_joint_update import required_keys
        maintenance = (set() if request.authorization.file_sha256 == '0' * 64 else {
            k for k in required_keys(request, strict=False) if not k.startswith('claims/')})
    else:
        maintenance = {p.key for p in request.permissions} - targets
    return targets | maintenance | set(actual_write_keys)


def scope(request, before, candidate=None, *, include_write_context=True, include_owner_context=True,
          include_impact_evidence=True, include_contextual_evidence=True):
    from backend.pipeline import claim_management as cm
    # A completed readback includes tombstones for reviewed retirements.
    # They are history, not active incident edges in a read-only footprint.
    # Live proposals retain the entire pre/post scope and write protections.
    payloads = {k: v['payload'] for k, v in before.items()
                if include_write_context or candidate is not None or not v.get('retired', False)}
    old = dict(payloads)
    if candidate:
        payloads.update(cm.source.index(candidate.records))
    targets = set(request.target_keys)
    from backend.pipeline.claim_review_units import enabled as bounded_unit
    bounded = bounded_unit(request)
    writes = [d.record_key for d in candidate.dispositions if d.action != 'retain'] if candidate else []
    base_roots = review_roots(request, bounded=bounded)
    roots = base_roots | set(writes)
    maintenance = base_roots - targets
    def named(col, oid):
        key = cm.source.key(col, oid)
        if key not in payloads:
            raise ValueError('direct Claim scope missing dependency: ' + key)
        return key
    def deps(key, rows):
        return cm.domain_dependencies(key.split('/', 1)[0], rows[key], rows)

    # Maintenance can require inspecting shared owners. This does not turn
    # their other Claim relations into traversal roots. Check both pre/post
    # owners so dropping an old owner cannot hide its impact.
    impact_claims = set()
    impact_context = set()
    for rows in [old, payloads]:
        for key in roots - targets:
            if key not in rows:
                continue
            col = key.split('/', 1)[0]
            p = rows[key]
            if col == 'evidence_steps':
                impact_claims.update(named('claims', cid) for cid in p.get('produced_claim_ids', []))
                impact_claims.update(k for k, v in rows.items() if k.startswith('claims/')
                    and key.split('/', 1)[1] in v.get('evidence_step_ids', []))
            elif col == 'questions':
                impact_claims.update(named('claims', cid) for cid in p.get('answer_claim_ids', []))
            elif col in {'source_fragments', 'position_nodes'}:
                # A shared opposed Position has Claim owners just like a
                # shared fragment. Include those owners, without promoting
                # their other Claim relations to new traversal roots.
                for owner, value in rows.items():
                    if owner.split('/', 1)[0] in cm.CLAIM_COLLECTIONS and key in deps(owner, rows):
                        impact_context.add(owner)
                        if owner.startswith('claims/'):
                            impact_claims.add(owner)
                        elif owner.startswith('evidence_steps/'):
                            impact_claims.update(named('claims', cid) for cid in value.get('produced_claim_ids', []))
                            impact_claims.update(k for k, v in rows.items() if k.startswith('claims/')
                                and owner.split('/', 1)[1] in v.get('evidence_step_ids', []))
            elif col in cm.RELATION_COLLECTIONS:
                impact_claims.update(k for k in deps(key, rows) if k.startswith('claims/'))
    content_claims = targets | impact_claims | {k for k in roots if k.startswith('claims/')}
    if bounded:
        # An affected owner is inspected for the shared delta, not assigned
        # another complete Claim review or a new qualification by proximity.
        content_claims = targets | {k for k in roots if k.startswith('claims/')}
        impact_context.update(impact_claims)
    core = set()
    support_indexes = {}
    def evidence_chain(seeds, rows, *, leaf_claims=False):
        if id(rows) not in support_indexes:
            incoming = {}
            for relation, value in rows.items():
                if not relation.startswith('knowledge_relations/'):
                    continue
                ends = deps(relation, rows)
                if any(k.startswith('claims/') for k in ends):
                    continue
                destination = value.get('to_id') or value.get('target_id')
                for endpoint in ends:
                    if endpoint.split('/', 1)[1] == destination:
                        # Background remains visible in ordinary packets. For
                        # mandatory maintenance, the edge itself is still our
                        # relation, but its contextual source is not a premise.
                        # A direct/shared binding or another support path still
                        # reaches that source independently.
                        upstream = (ends if include_contextual_evidence
                                    or value.get('relation_type') != 'contextualizes' else set())
                        incoming.setdefault(endpoint, set()).update(upstream | {relation})
            support_indexes[id(rows)] = incoming
        incoming = support_indexes[id(rows)]
        found = set(); pending = list(seeds)
        while pending:
            key = pending.pop()
            if key in found:
                continue
            if key not in rows:
                raise ValueError('direct Claim evidence chain is incomplete: ' + key)
            found.add(key)
            if key.startswith('claims/') and leaf_claims and key not in content_claims:
                continue
            # Relations and Question.answer_claim_ids do not extend Claim
            # membership. Their endpoints are context, handled separately.
            col = key.split('/', 1)[0]
            if col in cm.RELATION_COLLECTIONS:
                continue
            pending.extend(k for k in deps(key, rows) if not k.startswith('claims/'))
            pending.extend(incoming.get(key, set()))
        return found
    for rows in [old, payloads]:
        # Shared owners' evidence must remain available for reviewing the
        # actual delta, but their private defects do not become a full Update
        # obligation. Finding classification can ask for roots' own complete
        # support only; normal packets/transactions keep the full impact scope.
        seeds = (roots | impact_claims | impact_context if include_impact_evidence else roots) & set(rows)
        # Evidence whose explicit owner is the target is also its evidence,
        # even if an older Claim omitted its forward evidence_step_ids.
        seeds.update(k for k, p in rows.items() if k.startswith('evidence_steps/')
            and {cm.source.key('claims', c) for c in p.get('produced_claim_ids', [])} & content_claims)
        core.update(evidence_chain(seeds, rows))

    # A relation is selected only at the original targets or explicitly named
    # maintenance roots. Endpoints never become a new relation frontier.
    relations = {k for k in core if k.split('/', 1)[0] in cm.RELATION_COLLECTIONS}
    endpoints = set(); questions = set()
    for rows in [old, payloads]:
        # Explicitly maintained edges must keep both endpoints even when a
        # reversal makes one endpoint cease to be an upstream premise.
        for key in relations & rows.keys():
            endpoints.update(deps(key, rows))
        for key, p in rows.items():
            if key.split('/', 1)[0] in cm.RELATION_COLLECTIONS:
                endpoint_ids = {p.get(f) for f in ('from_id','to_id','source_id','target_id','from_claim_id','to_claim_id')}
                if endpoint_ids & {k.split('/', 1)[1] for k in roots}:
                    ends = deps(key, rows)
                    relations.add(key); endpoints.update(ends)
            elif key.startswith('questions/') and {cm.source.key('claims', c) for c in p.get('answer_claim_ids', [])} & targets:
                questions.add(key)
    neighbours = {k for k in endpoints if k.startswith('claims/')} - content_claims
    context = (endpoints | questions) - core
    writable = core | relations | questions
    if bounded and include_write_context:
        # Potential shared consumers are reference material, not more review
        # roots or capabilities. Freeze their necessary evidence before a
        # proposal can change a shared fragment/step; do not wait until after
        # GPT to discover that its old owners were missing from the packet.
        possible = {p.key for p in request.permissions}
        if not possible and not owner_context_enabled(request):
            # Historical packets used a hypothetical full write frontier.
            # New bound empty grants mean read-only, including admission's
            # natural-closure probe; do not invent fragment write impacts.
            possible = writable
        for rows in [old, payloads]:
            owners = set()
            for owner, value in rows.items():
                col = owner.split('/', 1)[0]
                if col in cm.CLAIM_COLLECTIONS and col not in {'claims', 'source_fragments'} and deps(owner, rows) & possible:
                    owners.add(owner)
                    if col in cm.RELATION_COLLECTIONS:
                        # A read-only shared-use edge still needs both typed
                        # endpoints. Otherwise projecting the frozen packet
                        # again cannot resolve this edge at commit/readback.
                        owners.update(deps(owner, rows))
                    elif col == 'evidence_steps':
                        owners.update(named('claims', cid) for cid in value.get('produced_claim_ids', []))
                    elif col == 'questions':
                        owners.update(named('claims', cid) for cid in value.get('answer_claim_ids', []))
                elif col == 'claims' and deps(owner, rows) & possible:
                    owners.add(owner)
            context.update(evidence_chain(owners, rows))
        neighbours.update(k for k in context if k.startswith('claims/') and k not in content_claims)
    if bounded and include_write_context and include_owner_context and owner_context_enabled(request):
        # Resolve the exact owner facts supplied by the original reference
        # table. Freeze discovery at the pre-proposal frontier: adding their
        # own support must not discover owners-of-owners or Claim neighbours.
        frontier = scope(request, before, include_write_context=include_write_context,
                         include_owner_context=False)['record_keys']
        authority = json.loads(cm.source.verify_file(request.authorization).read_text())
        fixed = authority.get('evidence_owner_context_keys')
        if fixed is not None:
            if not isinstance(fixed, list) or len(fixed) != len(set(fixed)) or any(
                    not isinstance(k, str) or not k.startswith('claims/') for k in fixed):
                raise ValueError('owner context requires exact frozen Claim keys')
            owners = {named(*k.split('/', 1)) for k in fixed}
        else:
            # Compatibility for the first frozen packets of this opt-in.
            owners = {named('claims', cid) for k in frontier if k.startswith('evidence_steps/')
                for cid in old[k].get('produced_claim_ids', [])} - set(frontier)
        owners = {k for k in owners
            if (own := cm.own_source_ids(old[k], old)) and own <= set(request.source_bindings)}
        context.update(evidence_chain(owners, old))
        neighbours.update(owners)
    for rows in [old, payloads]:
        context.update(evidence_chain(context & set(rows), rows, leaf_claims=True))
    keys = core | relations | context

    # Full physical sources belong to content/evidence, not neighbour Claim
    # payloads. Cross-sermon neighbours keep their own identity and are not
    # evidence for this target.
    source_ids = set()
    for key in keys - neighbours:
        if key.startswith('claims/'):
            own = cm.own_source_ids(payloads[key], payloads)
            if len(own) != 1:
                raise ValueError('direct Claim content needs one own source')
            source_ids.update(own)
        elif key.startswith('source_fragments/'):
            source_ids.add(payloads[key]['source_id'])
    # Verify each content Claim's own evidence stays in its own sermon.
    for claim in content_claims:
        if claim not in payloads:
            continue
        own = cm.own_source_ids(payloads[claim], payloads)
        chain = evidence_chain({claim}, payloads)
        if any(payloads[k]['source_id'] not in own for k in chain if k.startswith('source_fragments/')):
            raise ValueError('Claim evidence crosses its own source boundary')
    keys.update(named('source_documents', sid) for sid in source_ids)
    result = {'contract': CONTRACT, 'record_keys': sorted(keys),
            'content_claim_keys': sorted(content_claims), 'neighbour_claim_keys': sorted(neighbours),
            'relation_keys': sorted(relations), 'source_ids': sorted(source_ids),
            'maintenance_keys': sorted(maintenance)}
    if bounded:
        result['impact_claim_keys'] = sorted(impact_claims - content_claims)
        result['writable_record_keys'] = sorted(writable)
    return result


def assert_current(store, cursor, request, records, *, actual_write_keys=(), include_write_context=True):
    """Check adjacency at roots, not reverse closure at every read endpoint."""
    from backend.pipeline import claim_management as cm
    from backend.pipeline.claim_review_units import enabled as bounded_unit
    bounded = bounded_unit(request)
    roots = {cm.source.split_key(k) for k in review_roots(request,
        actual_write_keys=actual_write_keys if bounded else (), bounded=bounded)}
    consumers = store._claim_reference_consumers(cursor, roots)
    # New incoming non-Claim support can change the target's argument even
    # when no root row changed. Other Claims sharing evidence stay outside.
    non_claim_ids = [oid for col, oid in records
                     if col in cm.CLAIM_COLLECTIONS and col != 'claims']
    if non_claim_ids:
        cursor.execute('''SELECT e.edge_collection,e.edge_id
            FROM wang_knowledge.edges e
            JOIN wang_knowledge.objects o ON o.object_id=e.from_id
            WHERE e.edge_collection='knowledge_relations'
              AND e.retired_at IS NULL AND e.to_id=ANY(%s)
              AND o.retired_at IS NULL AND o.collection=ANY(%s)''',
            (non_claim_ids, sorted(cm.CLAIM_COLLECTIONS - {'claims'})))
        consumers.update(cursor.fetchall())
    if consumers - set(records):
        from backend.api.canonical_repository.postgres_store import ChangeSetConflict
        raise ChangeSetConflict('direct Claim scope omitted a current neighbour or maintenance owner')
    selected = scope(request, {cm.source.key(*k): v for k, v in records.items()},
                     include_write_context=include_write_context)
    if not set(selected['record_keys']) <= {cm.source.key(*k) for k in records}:
        raise ValueError('direct Claim scope has incomplete current evidence')
