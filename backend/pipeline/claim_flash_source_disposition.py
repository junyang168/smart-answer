"""Prepare and apply an exact owner disposition for stopped Flash statements."""
import json
from pathlib import Path

from backend.api.canonical_repository.knowledge_models import KNOWLEDGE_COLLECTIONS
from backend.pipeline import claim_flash_common as f, evidence_refresh as e, claim_update_flash as flash

VERSION = 'claim_flash_owner_confirmed_retirement_v1'


def read(path):
    return json.loads(Path(path).read_text())


def consumers(cursor, ids):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    return PostgresKnowledgeStore._reference_consumers(cursor, ids, sorted(KNOWLEDGE_COLLECTIONS))


def disposition(records, ids):
    retiring = {'claims/'+cid for cid in ids}
    updates, blocked = {}, []
    for key, row in records.items():
        if key in retiring:
            continue
        collection = key.split('/', 1)[0]
        payload = row['payload']
        if payload.get('review_status') in f.PROTECTED:
            blocked.append(key)
        elif collection in {'claim_relations', 'knowledge_relations'} and ids.intersection(
                (payload.get('from_id'), payload.get('to_id'))):
            retiring.add(key)
        elif (field := {'evidence_steps':'produced_claim_ids', 'questions':'answer_claim_ids'}.get(collection)) and ids.intersection(payload.get(field, [])):
            remaining = [cid for cid in payload[field] if cid not in ids]
            updates[key] = {**payload, field:remaining}
            if collection == 'questions' and not remaining and payload.get('answer_state') == 'answered':
                updates[key]['answer_state'] = 'unanswered'
        else:
            blocked.append(key)
    return sorted(retiring), updates, sorted(blocked)


def prepare(store, sid, group, statement_root, *, evidence_root=None, ports=None):
    result = read(statement_root/'verification.json')
    before = read(statement_root/'before.json')
    records, sources = f.load_group_source(store, sid, group)
    from backend.pipeline import claim_flash_arguments as arguments, claim_flash_statements as statements

    if before['sources'] != sources:
        arguments.added_source_context(before['sources'], sources)
    findings = {}
    rechecked = []
    for item in result['pending']:
        key = item['object_key']
        if item['layer'] != 'source' or key not in records or records[key]['payload'].get('review_status') in f.PROTECTED:
            continue
        if records[key] != before['records'].get(key):
            raise ValueError('source finding Claim changed: '+key)
        material = flash.claim_material(key, records, sources, sid)
        facts = {fact['decision_id']:fact for part in [material, *material['evidence'],
                 *material.get('argument_context', {}).get('records', [])]
                 for fact in part.get('human_source_decisions', [])}
        if facts:
            findings[key] = {'finding':item, 'statement':material['claim']['statement'],
                'source_confirmations':list(facts.values()), 'source_paragraphs':material['source_paragraphs']}
    if not findings:
        return None
    if evidence_root is not None and ports is not None:
        changed = arguments.changed_statement_context(
            [key.split('/',1)[1] for key in findings], records, before, sources, sid)
        if changed:
            recheck = statement_root/'source-disposition'/'recheck'
            materials = {key:flash.claim_material(key,records,sources,sid) for key in changed}
            needed = {node['object_key'] for material in materials.values()
                      for node in material['evidence'] + material.get('argument_context', {}).get('records', [])}
            needed.update(key for material in materials.values() for ref in material.get('claim_references', [])
                          for key in ref['supporting_material_keys'])
            e.save(recheck/'intent.json', {'parent_sha256':e.digest(result), 'material_sha256':e.digest(materials)})
            statements.verify_evidence(evidence_root,needed,records,sources)
            reviewed = statements.review(records,sources,changed,sid,recheck,ports)
            e.save(recheck/'review.json',reviewed)
            unresolved = {item['object_key']:item for item in reviewed['pending'] if item['layer']=='source'}
            for key in changed:
                if key in unresolved:
                    findings[key]['finding'] = unresolved[key]
                else:
                    findings.pop(key)
                    rechecked.append(key)
            # Feed successful dispositions forward using their exact source
            # snapshot and preserve the original verification.
            affected = arguments.changed_statement_context(result['claim_ids'], records, before, sources, sid)
            if (rechecked and set(affected) <= set(rechecked)
                    and not any(item['layer']=='source' and item['object_key'] not in rechecked
                                for item in result['pending'])):
                child = statement_root/'source-disposition'/'rechecked'
                keys = set(before['records']) | needed | {'source_documents/'+sid}
                for material in materials.values():
                    keys.update(row['object_key'] for row in material.get('argument_context', {}).get('relations', []))
                    for ref in material.get('claim_references', []):
                        keys.add(ref['object_key'])
                        keys.update(ref['supporting_relation_keys'])
                        keys.update(row['object_key'] for row in ref['relations'])
                        keys.update('source_fragments/'+fid for fid in ref['source_fragment_ids'])
                target_ids = {key.split('/',1)[1] for key in changed}
                keys.update(key for key,row in records.items() if key.startswith('knowledge_relations/')
                            and row['payload'].get('to_id') in target_ids)
                keys.update('source_fragments/'+fid for key in needed
                            for fid in e.evidence_fragment_ids(records[key]['payload']))
                current = {k:records[k] for k in keys if k in records}
                if any(records.get(k) != row for k,row in before['records'].items()):
                    raise ValueError('statement inputs changed during source recheck')
                merged = {**result,'updates':{**result['updates'],**reviewed['updates']},
                    'pending':[item for item in result['pending'] if item['object_key'] not in rechecked]+reviewed['pending'],
                    'outcomes':[item for item in result['outcomes'] if item['object_key'] not in rechecked]+reviewed['outcomes']}
                merged['status'] = 'needs_attention' if merged['pending'] else 'verified'
                e.save(child/'before.json', {'records':current,'sources':sources})
                e.save(child/'verification.json', merged)
                return {'rechecked':str(child)}
            if not findings:
                return None
    ids = {key.split('/',1)[1] for key in findings}
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        references = consumers(cursor, ids)
        cursor.execute('''SELECT collection,object_id,payload,revision,content_sha256,retired_at IS NOT NULL
                          FROM wang_knowledge.objects WHERE object_id=ANY(%s)''',
                       (sorted({oid for _,oid,_ in references}),))
        keys = {(col,oid) for col,oid,_ in references}
        refs = {col+'/'+oid:{'payload':payload,'revision':revision,'content_sha256':sha,'retired':retired}
                for col,oid,payload,revision,sha,retired in cursor.fetchall() if (col,oid) in keys}
    if any(key in before['records'] and before['records'][key] != row for key,row in refs.items()):
        raise ValueError('retirement consumer changed since statement review')
    retiring, updates, blocked = disposition(refs, ids)
    survivors = set(result['claim_ids']) - ids
    blocked.extend(key for key in retiring if key in refs
                   and refs[key]['payload'].get('from_id') in ids
                   and refs[key]['payload'].get('to_id') in survivors)
    blocked = sorted(set(blocked))
    return {'version':VERSION, 'statement_sha256':e.digest(result), 'before_sha256':e.digest(before),
        'findings':findings,'retiring_keys':retiring,'updates':updates,'blocked':blocked,
        'consumer_keys':sorted(refs), 'records':{**before['records'], **refs},'sources':sources}


def apply(store, proposal, approval, root):
    """Approval pins the proposal and explicitly names each retirement."""
    ids = {key.split('/',1)[1] for key in proposal['findings']}
    if (proposal['blocked'] or approval.get('proposal_sha256') != e.digest(proposal)
            or approval.get('decision') != 'retire' or sorted(ids) != approval.get('claim_ids')
            or approval.get('authorized_by') != 'owner' or not approval.get('authorization_quote')):
        raise ValueError('exact owner approval of an executable Claim retirement is required')
    if proposal['version'] != VERSION:
        raise ValueError('unsupported Flash retirement proposal')
    relevant = set(proposal['consumer_keys']) | set(proposal['findings'])
    retiring, updates, blocked = disposition({k:proposal['records'][k] for k in relevant}, ids)
    if (retiring, updates, blocked) != (proposal['retiring_keys'], proposal['updates'], []):
        raise ValueError('approved retirement write set differs')
    e.save(root/'approval.json', approval)
    e.save(root/'before.json', {'records':proposal['records'], 'sources':proposal['sources']})
    result = {'version':VERSION,'status':'verified','updates':proposal['updates'],'new_fragments':{},
        'retiring_keys':proposal['retiring_keys'],'pending':[],'applied':False,
        'claim_retirement_consumers':{'ids':sorted(ids),'keys':proposal['consumer_keys']},
        'commit_reason':'Owner-approved Flash Claim retirement'}
    e.save(root/'verification.json', result)
    return e.commit_verified(store, root)

def retain_statements(store, parent, child, proposal, retirement):
    """Carry only unchanged surviving statement judgments into the original flow."""
    result = read(parent/'verification.json')
    before = read(parent/'before.json')
    if e.digest(result) != proposal['statement_sha256'] or e.digest(before) != proposal['before_sha256']:
        raise ValueError('retirement statement inputs changed')
    removed = set(proposal['findings'])
    remaining = ['claims/'+cid for cid in result['claim_ids'] if 'claims/'+cid not in removed]
    f.source_unchanged(proposal['sources'])
    e.readback(store, retirement)
    frozen = {}
    for key,row in before['records'].items():
        if key in retirement['retiring_keys']:
            continue
        current = store.get_record_state(*key.split('/',1))
        if key in retirement['updates']:
            expected = retirement['updates'][key]
            fields = {'produced_claim_ids', 'answer_claim_ids', 'answer_state', 'revision'}
            if {k:v for k,v in row['payload'].items() if k not in fields} != {
                    k:v for k,v in expected.items() if k not in fields}:
                raise ValueError('retirement changed surviving statement input: '+key)
        elif current != row:
            raise ValueError('surviving statement input changed: '+key)
        frozen[key] = current
    # A removed Claim may itself have been a premise of another Claim.
    for key in remaining:
        if any(row['payload'].get('to_id') == key.split('/',1)[1]
               and row['payload'].get('from_id') in {k.split('/',1)[1] for k in removed}
               for edge,row in proposal['records'].items() if edge in retirement['retiring_keys']):
            raise ValueError('surviving Claim used a retired premise: '+key)
    pending = [p for p in result['pending'] if p['object_key'] not in removed]
    projected = {**result, 'claim_ids':[key.split('/',1)[1] for key in remaining],
        'updates':{k:v for k,v in result['updates'].items() if k not in removed},
        'outcomes':[row for row in result['outcomes'] if row['object_key'] not in removed],
        'pending':pending,'status':'needs_attention' if pending else 'verified',
        'retirement_sha256':e.digest(retirement)}
    # Keep the original source view for the argument handoff's per-Claim
    # comparison. New clarifications must not masquerade as reviewed material.
    e.save(child/'before.json', {'records':frozen,'sources':before['sources']})
    e.save(child/'verification.json', projected)
    return projected


def continue_statements(store, sid, group, parent, evidence_root, ports, run):
    from backend.pipeline import claim_flash_statements as statements

    root = parent/'source-disposition'
    approval = root/'owner-approval.json'
    if approval.exists():
        proposal = read(root/'proposal.json')
        if (e.digest(read(parent/'verification.json')) != proposal['statement_sha256']
                or e.digest(read(parent/'before.json')) != proposal['before_sha256']):
            raise ValueError('retirement statement inputs changed')
        if not (root/'commit-intent.json').exists():
            frozen = read(parent/'before.json')['records']
            for key,row in frozen.items():
                if store.get_record_state(*key.split('/',1)) != row:
                    raise ValueError('retirement statement read set changed: '+key)
        retirement = apply(store, proposal, read(approval), root)
        child = root/'retained'
        verification = child/'verification.json'
        result = read(verification) if verification.exists() else retain_statements(store, parent, child, proposal, retirement)
        if result['retirement_sha256'] != e.digest(retirement):
            raise ValueError('retained Claim retirement receipt changed')
        e.save(child/'retained-scope.json', {'original_ids':read(parent/'verification.json')['claim_ids'],
                                            'retained_sha256':e.digest(result)})
        return statements.finish(store,child), child

    rechecked = root/'rechecked'
    if (rechecked/'verification.json').exists():
        return statements.finish(store, rechecked), rechecked
    result = run()
    if not any(item['layer']=='source' for item in result.get('pending', [])):
        return result, parent
    proposal = prepare(store, sid, group, parent, evidence_root=evidence_root, ports=ports)
    if proposal and 'rechecked' in proposal:
        child = Path(proposal['rechecked'])
        return statements.finish(store, child), child
    if proposal:
        path = root/'proposal.json'
        e.save(path, proposal)
        result = {**result, 'source_disposition':{'path':str(path), 'sha256':e.digest(proposal),
                  'blocked':proposal['blocked'],'claim_ids':sorted(key.split('/',1)[1] for key in proposal['findings'])}}
        e.publish(parent,result)
    return result, parent
