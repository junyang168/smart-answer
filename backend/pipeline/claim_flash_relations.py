"""One group's Claim relationships; consume completed argument routes without reruns."""
from copy import deepcopy
import fcntl
import json
from pathlib import Path
from backend.pipeline import claim_flash_common as f, claim_flash_arguments as arguments
from backend.pipeline import claim_flash_pairs as pairs, claim_flash_statements as statements
from backend.pipeline import claim_update_flash as flash, claim_refresh as c, evidence_refresh as e

VERSION = 'claim_flash_relations_v1'
PROMPT = (flash.PROMPT + '本次只發現所列Claim之間遺漏的重要直接關係。'
          '已驗證statement與下層只作依據，不重審、不新建節點。'
          '原文中已由間接路線承載的意思不強加直接邊；主題相近或共享證據不等於直接關係。'
          '只用提供的Claim object_key，不沿鄰居展開。無缺口可空；材料不足列unresolved_keys。'
          '既有邊會獨立檢查，不在此修正。每個job_id恰好一次，只輸出必要候選及簡短理由。')
PROMPT+=('每條新連接一次給齊relation_type、direction、reason，作完整判定；'
         'completed_connections已完成，只作唯讀路線，不重判或重複提出；同一端點組合只給一個最合適直接關係。'
         + f.RELATION_REASON_RULES)


def owned_edges(records, ids, groups=None):
    """Review a frozen cross-group edge once, at its later endpoint's task."""
    ranks={}
    if groups is not None:
        for n,group in enumerate(groups):
            batches=group.get('sequential_claim_batches',[group['claim_ids']])
            if [cid for batch in batches for cid in batch]!=group['claim_ids']:
                raise ValueError('relation group batches differ from frozen Claims')
            for b,batch in enumerate(batches):
                for cid in batch:
                    if cid in ranks:raise ValueError('duplicate Claim relation group owner')
                    ranks[cid]=(n,b)
        current={ranks.get(cid) for cid in ids}
        if len(current)!=1 or None in current:raise ValueError('Claim relation task differs from frozen groups')
        rank=next(iter(current))
    result={}
    for key,row in records.items():
        if not key.startswith('claim_relations/') or row.get('retired'):continue
        p=row['payload'];a,b=p['from_id'],p['to_id']
        if groups is None:owned={a,b}<=set(ids)
        else:owned=a in ranks and b in ranks and max(ranks[a],ranks[b])==rank
        if owned:result[key]=p
    return result


def discovery_schema(contract=None):
    schema=flash.CompleteDiscoveries.model_json_schema()
    if contract=='claim_relation_types_v1':
        allowed=set(pairs.TYPES)-{'answers','applies'}
        schema['$defs']['CompletedProposal']['properties']['relation_type']['enum']=sorted(allowed)
    return schema


def consume_arguments(store, root, ids, records, sources):
    root = arguments.active_root(root)
    result = json.loads((root/'result.json').read_text())
    frozen = json.loads((root/'before.json').read_text())
    if (result.get('version') != arguments.VERSION or result.get('pending')
            or result.get('status') not in {'verified', 'applied'}
            or result.get('completion_scope') != 'group_lower_argument_routes_only'
            or result.get('claim_ids') != ids or frozen['sources'] != sources):
        raise ValueError('completed matching group argument routes required')
    if result['updates'] or result.get('retiring_keys'):
        intent = json.loads((root/'commit-intent.json').read_text())
        history = store.get_change_history(intent['change_set_id'])
        if (not result.get('applied') or not history or history['status'] != 'applied'
                or history['source_kind'] != arguments.VERSION
                or history['fingerprint_sha256'] != intent['fingerprint_sha256']):
            raise ValueError('argument SQL completion differs')
    e.readback(store, result)
    # Claim edges are this task's targets. Lower materials and route results must
    # still be exactly those accepted; changing a Claim edge does not re-review them.
    consumed = set(result['updates'])
    for key, row in frozen['records'].items():
        if key.startswith(('claim_relations/', 'claim_relation_constraints/')):
            continue
        consumed.add(key)
        if key in result['updates'] or key in result.get('retiring_keys', []):
            continue
        current = records.get(key)
        if not current or any(current[k] != row[k] for k in ['revision', 'content_sha256', 'retired']):
            raise ValueError('completed argument input changed: '+key)
    return consumed - set(result.get('retiring_keys', []))


def active_root(root):
    root = Path(root).resolve()
    path = root / 'conflict-continuation.json'
    if not path.exists():
        return root
    authority = json.loads(path.read_text())
    before = json.loads((root / 'before.json').read_text())
    parent = json.loads((root / 'verification.json').read_text())
    if (not authority.get('owner_direction') or authority.get('parent_sha256') != e.digest(parent)
            or authority.get('before_sha256') != e.digest(before) or parent['status'] != 'needs_attention'
            or not parent['pending'] or any(p.get('reason') != 'conflicting_completed_relation_proposals' for p in parent['pending'])
            or (root / 'commit-intent.json').exists()):
        raise ValueError('Claim relation conflict continuation differs from its stopped parent')
    return root / 'conflict-continuations' / e.digest(parent)


def resolve_conflicts(store, source_id, parent_root, root, ports, *, apply):
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (root / 'verification.json').exists():
            return statements.finish(store, root) if apply else json.loads((root / 'verification.json').read_text())
        before = json.loads((parent_root / 'before.json').read_text())
        result = json.loads((parent_root / 'verification.json').read_text())
        intent = json.loads((parent_root / 'intent.json').read_text())
        if result['source_id'] != source_id or intent['models'] != ports.identity:
            raise ValueError('Claim relation conflict model or source changed')
        records, sources = before['records'], before['sources']
        f.source_unchanged(sources)
        for key, old in records.items():
            current = store.get_record_state(*key.split('/', 1))
            if not current or any(current[field] != old[field] for field in ('revision', 'content_sha256', 'retired')):
                raise ValueError('Claim relation conflict input changed: ' + key)
        proposals = result['consumed_argument_claim_handoffs'] + [
            p for item in result['discoveries'] for p in item['pairs']]
        for finding in result['pending']:
            a, b = sorted([finding['a'], finding['b']])
            endpoint_ids = {a.split('/', 1)[1], b.split('/', 1)[1]}
            if any(k.startswith('claim_relations/') and {row['payload']['from_id'], row['payload']['to_id']} == endpoint_ids
                   for k, row in records.items()):
                raise ValueError('Conflict continuation requires a new Claim relation pair')
            candidates = []
            for pair in proposals:
                if {pair['a'], pair['b']} != {a, b}:
                    continue
                src = pair['a'] if pair['direction'] == 'a_to_b' else pair['b']
                candidates.append({'a': a, 'b': b, 'direction': 'a_to_b' if src == a else 'b_to_a',
                                   'relation_type': pair['relation_type'], 'reason': pair['reason']})
            choices = {(p['direction'], p['relation_type']) for p in candidates}
            if len(choices) < 2:
                raise ValueError('No conflicting relation proposals for the saved pair')
            job = {'job_id': e.digest([a, b]), 'A': pairs.node(a, records, sources),
                   'B': pairs.node(b, records, sources), 'candidates': candidates}
            _, reply = next(f.gpt(root / 'adjudication', ports, 'claim_relation_conflict', [job],
                                  pairs.Decisions.model_json_schema(),
                                  '只處置這一對主張。用原文判斷兩種提案，若一條成立，選set並照該提案的方向和類型。若都不成立選remove。'
                                  + f.RELATION_REASON_RULES, semantic_review=False))
            decision, = pairs.Decisions.model_validate(reply).items
            f.exactly([decision.model_dump()], [job['job_id']], 'job_id')
            if decision.action not in {'set', 'remove'} or (decision.action == 'set' and
                    (decision.direction, decision.relation_type) not in choices):
                raise ValueError('Claim relation conflict reply did not choose a supplied option')
            result['updates'] = {key: row for key, row in result['updates'].items()
                                 if {row['from_id'], row['to_id']} != endpoint_ids}
            if decision.action == 'set':
                selected = {'a': a, 'b': b, 'direction': decision.direction,
                            'relation_type': decision.relation_type, 'reason': decision.reason}
                checked = pairs.review(records, sources, [], [], root / 'pair-validation', ports,
                                       kind='claim', completed_proposals=[selected])
                if checked['pending']:
                    raise ValueError('Selected Claim relation failed validation')
                post, = checked['new_relations']
                oid = 'CR-FLA-' + e.digest([source_id, post['from_id'], post['to_id'], post['relation_type']])[:24]
                if store.get_record_state('claim_relations', oid):
                    raise ValueError('Selected Claim relation ID is already occupied')
                result['updates']['claim_relations/' + oid] = {'claim_relation_id': oid, **post}
            result['outcomes'].append({'a': a, 'b': b, 'basis': 'gpt_conflict_resolution', **decision.model_dump()})
        result.update(status='verified', pending=[])
        e.save(root / 'intent.json', intent)
        e.save(root / 'before.json', before)
        e.save(root / 'verification.json', result)
        return statements.finish(store, root) if apply else e.publish(root, result)


def run(store, source_id, argument_root, root, ports, *, apply=False):
    root = Path(root).resolve(); root.mkdir(parents=True, exist_ok=True)
    successor = active_root(root)
    if successor != root:
        return e.publish(root, resolve_conflicts(store, source_id, root, successor, ports, apply=apply))
    argument_root = arguments.active_root(argument_root)
    prior = json.loads((argument_root/'intent.json').read_text())
    ids = prior.get('claim_ids', prior['group']['claim_ids'])
    if 'claim_ids' in prior and ids not in prior['group'].get('sequential_claim_batches', []):
        raise ValueError('Claim relation batch differs from the frozen argument group')
    if not ids or len(ids) > 10 or len(ids) != len(set(ids)):
        raise ValueError('one existing group of one to ten Claims required')
    with (root/'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pin = f.bind_source(store, source_id, root)
        saved=json.loads((root/'intent.json').read_text()) if (root/'intent.json').exists() else None
        contract=saved.get('discovery_contract') if saved is not None else 'claim_relation_types_v1'
        schema=discovery_schema(contract)
        scope=(saved or {}).get('existing_relation_scope')
        if saved is None:
            plan=json.loads((Path(prior['evidence_root'])/'intent.json').read_text())['groups']
            scope={'contract':'later_endpoint_group_v1','groups':plan['groups']}
        e.save(root/'intent.json', {'version': VERSION, 'source': pin, 'claim_ids': ids,
            'argument_root': str(argument_root), 'models': ports.identity,
            'policy_sha256': e.digest([PROMPT, flash.CompleteDiscoveries.model_json_schema()]),
            **({'discovery_contract':contract} if contract is not None else {}),
            **({'existing_relation_scope':scope} if scope is not None else {})})
        if (root/'verification.json').exists():
            return statements.finish(store, root) if apply else json.loads((root/'verification.json').read_text())
        records, sources = f.load_group_source(store, source_id, prior['group']); c.fetch_claims(store, ids)
        if prior['source'] != pin or any(not f.belongs_to_source(records['claims/'+cid]['payload'], records, source_id) for cid in ids):
            raise ValueError('Claim relation group/source differs')
        read = consume_arguments(store, argument_root, ids, records, sources)
        targets = {'claims/'+cid for cid in ids}
        edges = owned_edges(records,ids,scope['groups'] if scope is not None else None)
        references={'claims/'+cid for p in edges.values() for cid in (p['from_id'],p['to_id'])}-targets
        if references:
            # Endpoint support is read-only; no neighbours-of-neighbours or new
            # relationship discovery across the frozen group boundary.
            lower={'evidence_steps/'+eid for key in references for eid in records[key]['payload'].get('evidence_step_ids',[])}
            expanded={**prior['group'],'reference_record_keys':sorted(set(prior['group'].get('reference_record_keys',[]))|lower)}
            argument_sources=sources
            records,sources=f.load_group_source(store,source_id,expanded)
            consume_arguments(store,argument_root,ids,records,argument_sources)
            read.update(references|lower)
            for key in references|lower:
                read.update('source_fragments/'+fid for fid in e.evidence_fragment_ids(records[key]['payload']))
        read.update(edges)
        read.update(k for k,r in records.items() if k.startswith('claim_relation_constraints/')
                    and {r['payload']['source_id'], r['payload']['target_id']} & set(ids))
        # One bounded group, one discovery task; no Cartesian candidate enumeration.
        shared = {'claims': {k:pairs.node(k, records, sources) for k in sorted(targets)},
                  'existing_edges': {k:p for k,p in edges.items() if {p['from_id'],p['to_id']}<=set(ids)}}
        handoffs=json.loads((argument_root/'result.json').read_text()).get('claim_pair_handoffs',[])
        if any(p['a'] not in targets or p['b'] not in targets or p['a']==p['b'] for p in handoffs):
            raise ValueError('argument Claim handoff exceeded group')
        completed_handoffs=[{k:p[k] for k in pairs.CompletedProposal.model_fields} for p in handoffs
                            if 'relation_type' in p and 'direction' in p]
        # Old actions supplied only candidates. Complete only their missing
        # decision, preserving the saved reason, if no new complete edge covers it.
        proposed=[{**{k:p[k] for k in ['a','b','reason']},'candidate_reason':p['reason']}
                  for p in handoffs if 'relation_type' not in p or 'direction' not in p]
        completed=list(completed_handoffs);pending = []; discoveries = []
        if completed_handoffs:shared['completed_connections']=completed_handoffs
        for batch, raw in f.gpt(root/'discovery', ports, 'missing_group_claim_relations',
                [{'job_id': prior['group']['group_id']}], schema,
                PROMPT, shared=shared, semantic_review=False):
            items = flash.CompleteDiscoveries.model_validate(raw).items
            f.exactly([x.model_dump() for x in items], [j['job_id'] for j in batch], 'job_id')
            for item in items:
                if not set(item.unresolved_keys) <= targets:
                    raise ValueError('unprovided unresolved Claim')
                for pair in item.pairs:
                    if pair.a not in targets or pair.b not in targets or pair.a == pair.b:
                        raise ValueError('Claim relation discovery exceeded group')
                    completed.append(pair.model_dump())
                pending.extend({'object_key': k, 'reason': item.reason} for k in item.unresolved_keys)
                discoveries.append(item.model_dump())
        result = pairs.review(records, sources, sorted(edges), proposed, root/'pairs', ports,
                              kind='claim', adjudicate_proposed_none=True,completed_proposals=completed,
                              repair_invalid_completed=True)
        updates = deepcopy(result['updates'])
        for post in result['new_relations']:
            oid = 'CR-FLA-'+e.digest([source_id, post['from_id'], post['to_id'], post['relation_type']])[:24]
            key = 'claim_relations/'+oid
            previous = store.get_record_state('claim_relations', oid)
            if previous:
                if not previous['retired'] or any(previous['payload'][k] != post[k] for k in ['from_id','to_id','relation_type']):
                    raise ValueError('new Claim relation identity already occupied')
                records[key] = previous; read.add(key)
                # Deliberate retirement is a lifecycle fact, not a missing edge.
                # Preserve the candidate for Restore instead of re-ingestion or
                # silently allocating a different identity to bypass retirement.
                pending.append({'object_key': key, 'reason': 'protected_retired_relation'
                    if previous['payload'].get('review_status') in f.PROTECTED else 'retired_relation_requires_restore',
                    'candidate': post})
                continue
            updates[key] = {'claim_relation_id': oid, **post}
        if not (set(updates) | set(result['retiring_keys'])) <= set(edges) | {k for k in updates if k.startswith('claim_relations/') and k not in records}:
            raise ValueError('Claim relation write scope expanded')
        pending += result['pending']
        output = {'version': VERSION, 'source_id': source_id, 'claim_ids': ids,
            'status': 'needs_attention' if pending else 'verified', 'updates': updates,
            'new_fragments': {}, 'retiring_keys': result['retiring_keys'], 'pending': pending,
            'discoveries': discoveries, 'outcomes': result['outcomes'], 'applied': False,
            'consumed_argument_claim_handoffs':handoffs,
            'reviewed_existing_relation_keys':sorted(edges),'readonly_endpoint_keys':sorted(references),
            'completion_scope': 'group_claim_relations_only', 'claim_qualification': 'not_granted',
            'commit_reason': 'Group Claim relationships only; reuse completed lower and argument tasks'}
        e.save(root/'before.json', {'records': {k:records[k] for k in read}, 'sources': sources})
        e.save(root/'consumed-arguments.json', {'root': str(argument_root),
            'result_sha256': e.digest(json.loads((argument_root/'result.json').read_text()))})
        e.save(root/'verification.json', output)
        return statements.finish(store, root) if apply else e.publish(root, output)


def main():
    import argparse
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
    from backend.pipeline.claim_model_capacity import ClaimModelCapacity
    parser = argparse.ArgumentParser(description='Claim relationships in one completed argument group')
    for name in ['source-id', 'argument-root', 'capacity-file', 'root']: parser.add_argument('--'+name, required=True)
    parser.add_argument('--database-url'); parser.add_argument('--codex-executable'); parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(); load_dotenv('.env')
    client = CodexSubscriptionClient(model='gpt-6.1-sol', reasoning_effort='medium', max_output_tokens=4000, executable=args.codex_executable)
    ports = c.Models(client, Path(args.root), capacity=ClaimModelCapacity(json.loads(Path(args.capacity_file).read_text())))
    result = run(PostgresKnowledgeStore(args.database_url), args.source_id, args.argument_root, Path(args.root), ports, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__': main()
