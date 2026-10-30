"""Compile a Claim ID list into existing, immutable Update batch requests.

This is intake, not another executor. The batch scheduler and Claim operation
remain responsible for calls, revisions, review, transactions and qualification.
"""
from pathlib import Path
import json

from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_commit import binding, check_binding

DEFAULT_POLICY = Path(__file__).parent / 'policies/wang_claim_management_joint_update_v8.json'

# Ordinary content maintenance cannot alter identity, human holds or approval.
FIELDS = {
    'claims': {'statement', 'claim_type', 'attribution', 'scripture_refs', 'topic_terms',
               'topic_ids', 'occurrences', 'evidence_step_ids', 'opposed_position_ids', 'limitations'},
    'evidence_steps': {'statement', 'step_type', 'scripture_refs', 'source_fragment_id',
                       'source_fragment_ids', 'speaker', 'stance', 'discourse_role',
                       'support_eligibility', 'produced_claim_ids', 'citation_ids'},
    'source_fragments': {'verbatim_excerpt', 'paragraph_key', 'source_segment_index',
                         'media_time', 'media_end_time', 'anchor_state'},
    'observations': {'statement', 'observation_type', 'argument_role', 'scripture_refs',
                     'source_fragment_id', 'source_fragment_ids'},
    'questions': {'text', 'question_type', 'questioner', 'source_fragment_id',
                  'source_fragment_ids', 'answer_state', 'answer_claim_ids'},
    'position_nodes': {'title', 'statement', 'attribution', 'source_fragment_id',
                       'source_fragment_ids', 'scripture_refs'},
    'claim_relations': {'from_id', 'to_id', 'relation_type', 'reason'},
    'knowledge_relations': {'from_id', 'to_id', 'relation_type', 'reason'},
    'claim_relation_constraints': {'source_id', 'target_id', 'forbidden_relation_types',
                                  'bidirectional', 'composition_role', 'reason'},
}

# A new Claim may need a new piece of its own professor source as evidence.
# These remain content-only grants; source documents and approval stay protected.
SUPPORT_CREATION_FIELDS = {
    'evidence_steps': FIELDS['evidence_steps'],
    'source_fragments': FIELDS['source_fragments'] | {'source_id'},
}


def claim_ids(values):
    if not isinstance(values, list) or not values:
        raise ValueError('claim_ids must be a nonempty list')
    if any(not isinstance(cid, str) or not cid.strip() or cid != cid.strip()
           or '/' in cid or '\\' in cid or any(ord(c) < 32 for c in cid) for cid in values):
        raise ValueError('claim_ids must contain literal Claim IDs, without paths or whitespace padding')
    return list(dict.fromkeys(values))


def immutable(path, value):
    """Refuse a changed task/root; never overwrite an original authority."""
    path = Path(path)
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    write_immutable(path, value)
    return binding(path)


def prepare(values, root, *, instruction, max_workers=1, store=None, policy=None, creation_count=1):
    """Resolve own sources and content permissions, then bind one action per ID.

    The instruction records the actual caller's direction, never a content pass.
    Reusing a root must reuse exactly the same list, policy and direction.
    """
    from backend.pipeline import claim_management_batch as batch
    from backend.pipeline.claim_direct_scope import scope
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    ids = claim_ids(values)
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError('the Claim Update direction is required')
    if type(max_workers) is not int or max_workers not in (1, 2):
        raise ValueError('max_workers must be 1 or 2')
    if type(creation_count) is not int or creation_count<1:raise ValueError('creation_count must be a positive integer')
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if policy is None:
        policy = root / 'policy.json' if (root / 'policy.json').exists() else DEFAULT_POLICY
    selected = cm.Policy.model_validate_json(Path(policy).read_text())
    if (selected.review_purpose != 'content' or selected.claim_scope_contract != 'claim_direct_neighbours_v1'
            or selected.independent_audit_required):
        raise ValueError('Claim list intake requires the scoped Opus-only content Update policy')
    # Execution artifacts must survive moving/removing the development checkout.
    policy_binding = immutable(root / 'policy.json', selected.model_dump(mode='json'))
    intent = {'claim_ids': ids, 'instruction': instruction, 'max_workers': max_workers,
              'policy': policy_binding}
    if creation_count!=1:intent['creation_count']=creation_count
    immutable(root / 'intake.json', intent)
    store = store if store is not None else PostgresKnowledgeStore()
    receipt = root / 'prepared.json'
    if receipt.exists():
        saved = json.loads(receipt.read_text())
        for item in saved['inputs']:
            check_binding(item)
        if saved['manifest']:
            check_binding(saved['manifest'])
            batch.load(saved['manifest']['path'], root / 'batch', store=store)
        return saved

    # Validate the complete denominator before preparing or dispatching any job.
    excluded = {}
    for cid in ids:
        state = store.get_record_state('claims', cid)
        if state is None or state['retired']:
            excluded[cid] = {'status': 'paused', 'reason': 'missing_claim' if state is None else 'retired_claim'}
        elif state['payload'].get('review_status') in cm.HELD:
            excluded[cid] = {'status': 'paused', 'reason': 'human_hold'}
    jobs, inputs = [], []
    for cid in ids:
        if cid in excluded:
            continue
        token = cm.sha256_json({'root': str(root), 'claim_id': cid})[:24]
        action_dir = root / 'claims' / token
        records = store.read_claim_context([('claims', cid)])
        before = {cm.source.key(*key): state for key, state in records.items()}
        payloads = {key: state['payload'] for key, state in before.items()}
        own = cm.own_source_ids(payloads['claims/' + cid], payloads)
        if len(own) != 1:
            raise ValueError('Claim needs exactly one complete own source: ' + cid)
        sources = {}
        for sid in own:
            doc = before['source_documents/' + sid]
            if doc['retired']:
                raise ValueError('Claim source is retired: ' + sid)
            sources[sid] = binding(Path(doc['payload']['source_path']).resolve())
        auth_path = action_dir / 'authorization.json'
        request = cm.Request(action_id='claim-list-' + token, operation='Update', allowed_operations=['Update'],
            target_keys=['claims/' + cid], source_bindings=sources, policy=policy_binding,
            authorization={'path': str(auth_path), 'file_sha256': '0' * 64})
        allocations = {}
        if selected.model_output_contract == 'claim_changes_v4':
            from backend.api.canonical_repository.claim_identity_allocation import reserve
            sid = next(iter(own))
            request.allowed_operations = ['Update', 'Create', 'Retire']
            request.creation_permissions = [cm.CreationPermission(collection='claims', source_id=sid,
                                                                  fields=sorted(FIELDS['claims']))]
            request.creation_permissions += [cm.CreationPermission(collection=col, source_id=sid,
                fields=sorted(fields)) for col,fields in SUPPORT_CREATION_FIELDS.items()]
            allocations[sid] = reserve(store, action_id=request.action_id, source_id=sid, target_id=cid,
                                       root=action_dir / 'execution',count=creation_count)
        # A writable relation makes its other Claim reviewed content, so one
        # more pass grants the Evidence and relations that review then shows
        # (#459: CR001 brought CL004's E016/ER011 into review but not into the
        # write set). Other Claims stay read-only. No further passes: chaining
        # on through neighbours of neighbours reached 333 objects for P06-CL001.
        granted = set()
        for passed in range(2):
            # The second pass widens only through relations, as joint
            # admission does; writable Evidence must not pull in its other
            # owners (#459 P07-CL001: 20 grants admission then refused).
            shown = request if not passed else request.model_copy(deep=True, update={'permissions': [
                p for p in request.permissions if p.collection in cm.RELATION_COLLECTIONS]})
            added = []
            for key in scope(shown, before)['record_keys']:
                col, oid = cm.source.split_key(key)
                if key in granted or col not in FIELDS or (col == 'claims' and oid != cid):
                    continue
                state = before[key]
                if state['retired'] or state['payload'].get('review_status') in cm.HELD:
                    continue
                if col not in cm.RELATION_COLLECTIONS and not cm.own_source_ids(state['payload'], payloads) <= own:
                    continue
                added.append(cm.source.Permission(collection=col, object_id=oid, fields=sorted(FIELDS[col])))
            if not added:
                break
            granted.update(p.key for p in added)
            request.permissions.extend(added)
        authorization = {'schema_version': cm.VERSION, 'authorized_by': 'owner',
            'authorization_quote': instruction, 'human_content_approval': False,
            'request': request.model_dump(mode='json', exclude={'authorization', 'policy'}),
            'policy': policy_binding, 'required_maintenance_keys': [],
            'review_questions': [instruction]}
        if selected.model_output_contract == 'claim_changes_v4':
            authorization['claim_creation_allocations'] = allocations
        request.authorization = cm.source.FileBinding.model_validate(immutable(auth_path, authorization))
        documents = {sid: before['source_documents/' + sid]['payload'] for sid in own}
        cm.load_authority(request, source_documents=documents)
        bound = immutable(action_dir / 'request.json', request.model_dump(mode='json'))
        jobs.append({'request': bound, 'root': str(action_dir / 'execution')})
        inputs.extend([bound, request.authorization.model_dump(mode='json')])
    manifest = immutable(root / 'manifest.json', {'jobs': jobs, 'max_workers': max_workers}) if jobs else None
    if manifest:
        batch.load(manifest['path'], root / 'batch', store=store)
    result = {'manifest': manifest, 'claim_ids': ids, 'inputs': inputs, 'excluded': excluded}
    immutable(receipt, result)
    return result


def run(values, root, *, instruction, max_workers=1, store=None, policy=None, model_capacity=None, creation_count=1):
    from backend.pipeline import claim_management_batch as batch
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    store = store if store is not None else PostgresKnowledgeStore()
    root = Path(root).resolve()
    # Also serialize preparation: duplicate invocations must not split authority.
    import fcntl
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'intake.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            prepared = prepare(values, root, instruction=instruction, max_workers=max_workers, store=store, policy=policy, creation_count=creation_count)
            manifest = prepared['manifest']['path'] if prepared['manifest'] else None
            jobs = batch.load(manifest, root / 'batch', store=store)[1] if manifest else {}
            original = batch._paragraph_results(store, jobs)
            if not all(v.get('status') == 'completed' for v in original['results'].values()):
                batch.run(manifest, root / 'batch', **({'model_capacity': model_capacity} if model_capacity else {}))
            from backend.pipeline.claim_qualification_refresh import run as refresh_currentness
            result = refresh_currentness(store, jobs, root / 'completion-currentness', instruction=instruction,
                **({'model_capacity': model_capacity} if model_capacity else {}))
            by_claim = {cid: {'action_id': None, 'result': reason, 'qualification': None}
                        for cid, reason in prepared['excluded'].items()}
            for action_id, (request, _) in jobs.items():
                for key in request.target_keys:
                    by_claim[key.split('/', 1)[1]] = {'action_id': action_id, 'result': result['results'][action_id],
                        'qualification': result['current_qualifications'][key]}
            result['all_actions_completed'] = result['all_actions_completed'] and not prepared['excluded']
            result.update(status='completed' if result['all_actions_completed'] else 'paused',
                          claims={cid: by_claim[cid] for cid in prepared['claim_ids']})
            batch.progress(root / 'result.json', result)
            return result
        except BaseException as exc:
            batch.progress(root / 'result.json', {'status': 'paused', 'all_actions_completed': False,
                                                 'detail': str(exc)})
            raise
