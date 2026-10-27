"""Changed source paragraphs -> complete Claim ID Updates, in source order.

The source version must already be registered. This module does not register
sources, judge semantic impact, alter Claim contracts, or execute CVP work.
"""
from pathlib import Path
from types import SimpleNamespace
import argparse
import fcntl
import json

from backend.pipeline import claim_management as cm, claim_management_intake as intake
from backend.pipeline import claim_management_batch as batch
from backend.pipeline.cvp_sql_commit import binding, check_binding
from backend.pipeline.source_projection import project_script


def projection(bound):
    check_binding(bound)
    value = json.loads(Path(bound['path']).read_text())
    return project_script(value['script'] if isinstance(value, dict) else value)


def guard(store, plan):
    check_binding(plan['before'])
    check_binding(plan['current'])
    if plan.get('source_history'):
        check_binding(plan['source_history'])
    doc = store.get_record_state('source_documents', plan['source_id'])
    if (not doc or doc['retired'] or [doc['revision'], doc['content_sha256']] != plan['source_pin']):
        raise ValueError('registered source changed; original task cannot be refreshed')


def prepare(store, source_id, before, change_set_id, root, instruction):
    """Freeze a mechanical dependency list, never a content approval."""
    root = Path(root)
    intent = {'source_id':source_id, 'before':binding(Path(before).resolve()),
              'change_set_id':change_set_id, 'instruction':instruction}
    if not isinstance(instruction,str) or not instruction.strip():
        raise ValueError('source Update direction required')
    intake.immutable(root/'input.json',intent)
    if (root/'plan.json').exists():
        plan = json.loads((root/'plan.json').read_text()); guard(store,plan)
        return plan
    history = store.get_change_history(change_set_id)
    if not history or history['status'] != 'applied':
        raise ValueError('an applied source version ChangeSet is required')
    operations = [op for op in history['operations'] if op['collection']=='source_documents' and op['object_id']==source_id]
    if len(operations)!=1 or not operations[0]['before'] or not operations[0]['after']:
        raise ValueError('source change must have one exact before/after document')
    olddoc = operations[0]['before']; doc = store.get_record_state('source_documents',source_id)
    if not doc or doc['retired'] or doc['content_sha256'] != operations[0]['after_sha256']:
        raise ValueError('source must still be at the registered ChangeSet postimage')
    current = binding(Path(doc['payload']['source_path']).resolve())
    if doc['payload'].get('source_file_sha256') != current['file_sha256']:
        raise ValueError('current source bytes are not registered')
    old = projection(intent['before'])
    # Exact original bytes or a complete, independently registered projection.
    # Do not pretend a recovered JSON formatting has the original file hash.
    fields = {'source_body_sha256':'body_sha256','source_text_sha256':'spoken_text_sha256',
        'editorial_structure_sha256':'editorial_structure_sha256','editorial_topology_sha256':'editorial_topology_sha256'}
    if olddoc.get('source_file_sha256') != intent['before']['file_sha256']:
        if any(not olddoc.get(f) or olddoc[f]!=getattr(old,a) for f,a in fields.items()):
            raise ValueError('before source is neither the original file nor its complete registered projection')
    previous = cm.source.source_snapshot(intent['before'],source=olddoc)['paragraphs']
    after = cm.source.source_snapshot(current,source=doc['payload'])['paragraphs']
    if (list(previous)!=list(after) or any({k:v for k,v in previous[p].items() if k!='text'} !=
        {k:v for k,v in after[p].items() if k!='text'} for p in previous)):
        raise ValueError('paragraph identity changed; explicit locator mapping is required')
    changed = [p for p in after if previous[p]['text']!=after[p]['text']]
    records = store.read_affected_context([('source_documents',source_id)])
    states = {cm.source.key(*k):v for k,v in records.items()}
    payloads = {k:v['payload'] for k,v in states.items()}
    from backend.pipeline.claim_direct_scope import scope
    by_paragraph = {p:[] for p in changed}; reasons = {}
    for key,row in sorted(states.items()):
        if not key.startswith('claims/') or row['retired'] or cm.own_source_ids(row['payload'],payloads)!={source_id}:
            continue
        req = SimpleNamespace(target_keys=[key],permissions=[])
        selected = scope(req,states)
        hits = {}
        for dependency in set(selected['record_keys'])-set(selected['neighbour_claim_keys']):
            value = payloads[dependency]
            anchors = []
            if dependency.startswith('source_fragments/') and value.get('source_id')==source_id:
                anchors.append(value.get('paragraph_key'))
            for occurrence in value.get('occurrences',[]):
                if occurrence.get('source_id')==source_id:
                    anchors.extend(a.get('paragraph_key') for a in occurrence.get('anchors',[]))
            for p in set(anchors)&set(changed):hits.setdefault(p,[]).append(dependency)
        if hits:
            first = next(p for p in changed if p in hits)
            by_paragraph[first].append(key.split('/',1)[1]); reasons[key]=hits
    covered = {p for hits in reasons.values() for p in hits}
    plan = {**intent,'current':current,'source_pin':[doc['revision'],doc['content_sha256']],
        'changed_paragraphs':changed,'paragraphs':[{'paragraph_key':p,'claim_ids':ids} for p,ids in by_paragraph.items() if ids],
        'dependencies':reasons,'unowned_paragraphs':[p for p in changed if p not in covered]}
    intake.immutable(root/'source-change-history.json',history)
    plan['source_history'] = binding(root/'source-change-history.json')
    guard(store,plan); intake.immutable(root/'plan.json',plan)
    return plan


def run(store, source_id, before, change_set_id, root, instruction):
    root = Path(root).resolve(); root.mkdir(parents=True,exist_ok=True)
    with (root/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        plan = prepare(store,source_id,before,change_set_id,root,instruction)
        jobs = {}; completed = []
        for group in plan['paragraphs']:
            guard(store,plan)
            paragraph = group['paragraph_key']; output = root/'paragraphs'/paragraph
            batch.progress(root/'status.json',{'stage':'claim_update','paragraph_key':paragraph,'completed_paragraphs':completed})
            # No later batch is prepared until this one has actually completed.
            result = intake.run(group['claim_ids'],output,store=store,max_workers=1,
                instruction=instruction+'\n原文 '+paragraph+' 已校訂；對 Claim 及其 Evidence、Question、立場、關係與原文引文完整 refresh。主張未變也不得省略相關 objects。教授投影片與原話有同等證據地位。')
            if not result['all_actions_completed']:
                value={'status':'paused','paragraph_key':paragraph,'batch':result,'completed_paragraphs':completed}
                batch.progress(root/'result.json',value); return value
            manifest = json.loads((output/'prepared.json').read_text())['manifest']
            if manifest:jobs.update(batch.load(manifest['path'],output/'batch',store=store)[1])
            completed.append(paragraph)
        guard(store,plan)
        final = batch._paragraph_results(store,jobs)
        final.update(status='claims_completed' if final['all_actions_completed'] and not plan['unowned_paragraphs'] else 'paused',
            completed_paragraphs=completed,unowned_paragraphs=plan['unowned_paragraphs'],
            downstream_handoff={'status':'not_executed','claim_ids':sorted(k.split('/',1)[1] for k,q in final['current_qualifications'].items() if q and q.get('ready') and k not in final.get('retired_keys',[])),
                'retired_claim_ids':[k.split('/',1)[1] for k in final.get('retired_keys',[])]})
        batch.progress(root/'result.json',final); return final


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parents[2]/'.env')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-id',required=True);parser.add_argument('--before',type=Path,required=True)
    parser.add_argument('--change-set-id',required=True);parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--instruction',required=True);parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--detach',action='store_true')
    args=parser.parse_args()
    if not args.root.is_absolute():parser.error('--root must be absolute')
    if args.detach:
        import sys
        if args.prepare_only:parser.error('prepare-only cannot be detached')
        args.root.mkdir(parents=True,exist_ok=True)
        batch.detach([sys.executable,'-m',__spec__.name,*[v for v in sys.argv[1:] if v!='--detach']],args.root)
        return
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    function=prepare if args.prepare_only else run
    print(json.dumps(function(PostgresKnowledgeStore(),args.source_id,args.before,args.change_set_id,args.root,args.instruction),ensure_ascii=False))


if __name__=='__main__':main()
