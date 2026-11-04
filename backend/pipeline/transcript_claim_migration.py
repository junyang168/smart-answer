"""Versioned beta-source handoff to the existing Claim Management Update.

This module freezes inputs, registers one source epoch, and prepares ordinary
bounded Update jobs. It does not implement a second reviewer, retry loop,
scheduler, Claim writer, or qualification database. Public transcripts and beta
review decisions are never changed here.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_commit import binding, check_binding
from backend.pipeline.source_projection import project_script
from backend.pipeline.viewpoint_resolution_runtime import write_immutable

VERSION = 'wang_transcript_claim_migration_v1'
SOURCE_KIND = 'transcript_claim_source_registration_v1'
RULES = '''Treat this as a newly authorized source-version migration, not a retry
of an old unchanged-source action. Existing action answers/budgets/holds remain
history. Review every target using the complete NEW own source. Soft-deleted
words are not source: neither quotations nor Claim summaries may reintroduce
their teaching without independent undeleted professor support. Check assertion,
negation, conditions, scripture, argument steps and speaker attribution. Audience
questions/interjections are context, not professor assertions; evaluate his actual
response. Repair the Claim and all necessary own Evidence/Fragments/Questions/
Observations/Positions/relations. Retire unsupported claims with a specific
source-based reason; do not preserve unsupported summaries by relabelling them.
Use existing active same-source Claims rather than inventing duplicates. Preserve
unresolved genuine human decisions. Resolve model/engineering/material defects
autonomously within the existing bounded recovery contract. Only an unavoidable
content question supported by exact source/context goes to the owner. CVP/route
semantic maintenance is a separate downstream handoff, not approval by this job.'''


def read(path):
    return json.loads(Path(path).read_bytes())


def immutable(path, value):
    write_immutable(Path(path), value)
    return binding(Path(path))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _write_bytes(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as stream:
            stream.write(raw)
    except FileExistsError:
        if path.read_bytes() != raw:
            raise ValueError('frozen source bytes differ')
    return binding(path)


def beta_source(rows, context, *, item):
    """Keep physical IDs/empty rows and attach exact sidecar media boundaries."""
    result = deepcopy(rows)
    seen = set()
    for row in result:
        pid = str(row['index'])
        if pid in seen:
            raise ValueError('beta paragraph IDs are ambiguous')
        seen.add(pid)
        if row.get('type') == 'subtitle':
            continue
        clock = context.get('paragraph_bindings', {}).get(pid)
        if clock is None:
            raise ValueError('missing beta media binding: ' + pid)
        start, end = clock.get('start_ms'), clock.get('end_ms')
        if (start is None or end is None or start < 0 or end < start):
            raise ValueError('invalid beta media boundary: ' + pid)
        row['start_time'], row['end_time'] = start / 1000, end / 1000
    return {'metadata': {'transcript_id': item, 'beta_revision': context['revision'],
                         'candidate_source': True, 'human_content_approval': False},
            'script': result}


def _population(records, source_id):
    payloads = {key: state['payload'] for key, state in records.items()}
    selected, excluded = [], []
    for (collection, oid), state in records.items():
        if collection != 'claims' or state.get('retired'):
            continue
        own = cm.own_source_ids(state['payload'], {
            cm.source.key(*key): value for key, value in payloads.items()})
        if source_id not in own:
            continue
        if state['payload'].get('review_status') == 'superseded':
            excluded.append(oid)
        elif own != {source_id}:
            raise ValueError('source migration has an indirect/multi-source owner: ' + oid)
        else:
            selected.append(oid)
    return selected, excluded


def _ordered_claims(records, source_id, ids):
    """Old bound paragraph order is scheduling only, never a new anchor map."""
    payloads = {cm.source.key(*key): v['payload'] for key, v in records.items()}
    rank = {}
    for cid in ids:
        claim = payloads['claims/' + cid]
        keys = []
        for eid in claim.get('evidence_step_ids', []):
            evidence = payloads.get('evidence_steps/' + eid, {})
            fids = {evidence.get('source_fragment_id'), *evidence.get('source_fragment_ids', [])} - {None}
            for fid in fids:
                fragment = payloads.get('source_fragments/' + fid, {})
                if fragment.get('source_id') == source_id:
                    key = str(fragment.get('paragraph_key', ''))
                    if key.startswith('S') and key[1:].isdigit():
                        keys.append(int(key[1:]))
        if not keys:
            raise ValueError('Claim has no source-bound scheduling position: ' + cid)
        rank[cid] = min(keys)
    return sorted(ids, key=lambda cid: (rank[cid], cid)), rank


def prepare(store, *, beta_root, item, source_id, root, instruction, group_size=5):
    if not instruction.strip() or not 1 <= group_size <= 10:
        raise ValueError('owner direction and group size 1..10 required')
    root, beta_root = Path(root).resolve(), Path(beta_root).resolve()
    intent = {'schema_version': VERSION, 'source_id': source_id, 'item': item,
              'beta_root': str(beta_root), 'instruction': instruction, 'group_size': group_size}
    root.mkdir(parents=True, exist_ok=True)
    immutable(root/'intent.json', intent)
    if (root/'manifest.json').exists():
        return load(root)
    source = store.get_record_state('source_documents', source_id)
    if (not source or source['retired'] or source['payload'].get('transcript_id') != item
            or source['payload'].get('source_type') != 'sermon_transcript'):
        raise ValueError('missing/foreign registered source')
    rowpath = beta_root/'script_review'/f'{item}.json'
    contextpath = beta_root/'transcript_review'/item/'context.json'
    rowraw, contextraw = rowpath.read_bytes(), contextpath.read_bytes()
    rows, context = json.loads(rowraw), json.loads(contextraw)
    candidate = beta_source(rows, context, item=item)
    projection = project_script(candidate['script'])
    oldraw = Path(source['payload']['source_path']).read_bytes()
    if sha(oldraw) != source['payload'].get('source_file_sha256'):
        raise ValueError('registered old source file drift')
    if project_script(json.loads(oldraw)['script']).body_sha256 != source['payload']['source_body_sha256']:
        raise ValueError('registered old body drift')
    if projection.body_sha256 == source['payload']['source_body_sha256']:
        raise ValueError('no source epoch change; reuse existing Claim authority')
    records = store.read_affected_context([('source_documents', source_id)])
    ids, excluded = _population(records, source_id)
    ids, rank = _ordered_claims(records, source_id, ids)
    if not ids:
        raise ValueError('source has no live Claims')
    frozen = [{'collection': col, 'object_id': oid, **cm.model_state(state)}
              for (col, oid), state in sorted(records.items())]
    before = _write_bytes(root/'source-before.json', oldraw)
    after = immutable(root/'source-after.json', candidate)
    immutable(root/'beta-before.json', {'script': rows, 'context': context})
    frozen_binding = immutable(root/'records-before.json', frozen)
    newdoc = deepcopy(source['payload'])
    newdoc.update(source_path=after['path'], source_file_sha256=after['file_sha256'],
                  source_sha256=projection.body_sha256, source_body_sha256=projection.body_sha256,
                  source_text_sha256=projection.spoken_text_sha256,
                  anchor_binding_sha256=projection.body_sha256,
                  editorial_structure_sha256=projection.editorial_structure_sha256,
                  editorial_topology_sha256=projection.editorial_topology_sha256)
    # Reuse the existing bounded unit schema and current subscription direction.
    policy = read(Path(__file__).parent/'policies/wang_claim_management_bounded_unit_v1.json')
    policy.update(policy_id='wang_transcript_claim_migration_v1', api_spend=None,
                  proposal_empty_arrays='program_validated_v1')
    policy['models']['opus']['backend'] = 'claude_subscription'
    cm.Policy.model_validate(policy)
    pb = immutable(root/'policy.json', policy)
    manifest = {**intent, 'before': before, 'after': after, 'records': frozen_binding,
                'before_source_record': cm.model_state(source), 'after_source_record': newdoc,
                'beta_revision': context['revision'], 'beta_files': {
                    str(rowpath): sha(rowraw), str(contextpath): sha(contextraw)},
                'claim_ids': ids, 'excluded_superseded_ids': excluded,
                'groups': [ids[i:i+group_size] for i in range(0, len(ids), group_size)],
                'scheduling_old_paragraphs': rank, 'policy': pb,
                'claim_approval_granted': False, 'public_transcript_changed': False}
    for path, expected in manifest['beta_files'].items():
        if sha(Path(path).read_bytes()) != expected:
            raise ValueError('beta changed during source freeze')
    immutable(root/'manifest.json', manifest)
    return manifest


def load(root):
    root = Path(root).resolve()
    m = read(root/'manifest.json')
    if m.get('schema_version') != VERSION:
        raise ValueError('unsupported migration contract')
    for key in ('before', 'after', 'records', 'policy'):
        check_binding(m[key])
    flat = [cid for group in m['groups'] for cid in group]
    if flat != m['claim_ids'] or len(set(flat)) != len(flat):
        raise ValueError('migration Claim population has a gap/duplicate')
    if not flat or any(not 1 <= len(group) <= 10 for group in m['groups']):
        raise ValueError('invalid migration groups')
    return m


def register(store, root):
    """Only register the immutable internal source; no Claim approval/write."""
    root = Path(root).resolve();m = load(root)
    package_id = 'transcript-source-' + cm.sha256_json({
        'source_id': m['source_id'], 'before': m['before']['file_sha256'],
        'after': m['after']['file_sha256']})[:32]
    current = store.get_record_state('source_documents', m['source_id'])
    receipt = root/'source-registration.json'
    if receipt.exists():
        value = read(receipt)
        history = store.get_change_history(value['change_set_id'])
        if not history or history['status'] != 'applied':
            raise ValueError('registered source has no applied ChangeSet')
        if current['payload'].get('source_file_sha256') != m['after']['file_sha256']:
            raise ValueError('source has moved beyond this migration')
        return value
    # Recover a committed-but-unacknowledged handoff without a second write.
    if current['payload'].get('source_file_sha256') == m['after']['file_sha256']:
        intent = read(root/'source-registration-intent.json')
        history = store.get_change_history(intent['change_set_id'])
        ops = (history or {}).get('operations', [])
        if (not history or history['status'] != 'applied' or len(ops) != 1
                or ops[0].get('collection') != 'source_documents'
                or ops[0].get('object_id') != m['source_id']
                or ops[0].get('before_sha256') != m['before_source_record']['content_sha256']
                or ops[0].get('after_sha256') != current['content_sha256']
                or ops[0].get('after_revision') != current['revision']):
            raise ValueError('source registration has an unverified commit; do not reapply')
        value = {'change_set_id': intent['change_set_id'], 'status': 'applied',
                 'recovered_without_second_write': True}
        immutable(receipt, value)
        return value
    if cm.model_state(current) != m['before_source_record']:
        raise ValueError('source registration CAS changed')
    if sha(Path(current['payload']['source_path']).read_bytes()) != m['before']['file_sha256']:
        raise ValueError('registered old source file drift before handoff')
    for path, expected in m['beta_files'].items():
        if sha(Path(path).read_bytes()) != expected:
            raise ValueError('beta changed before registration')
    plan = store.plan_package({'package_id': package_id,
                              'source_documents': [m['after_source_record']]}, source_kind=SOURCE_KIND)
    if len(plan.operations) != 1 or plan.operations[0].collection != 'source_documents':
        raise ValueError('source registration tried to alter knowledge content')
    immutable(root/'source-registration-intent.json', {'package_id': package_id,
              'fingerprint': plan.fingerprint_sha256, 'source_id': m['source_id'],
              'change_set_id': plan.change_set_id})
    value = store.apply_plan(plan, metadata={'actor': 'owner_authorized_migration',
        'reason': m['instruction'], 'migration_manifest': binding(root/'manifest.json'),
        'source_registration_only': True, 'human_content_approval': False})
    immutable(receipt, value)
    return value


def prepare_unit(store, root, index):
    from backend.pipeline.claim_management_intake import prepare as intake
    root = Path(root).resolve();m = load(root)
    register_receipt = read(root/'source-registration.json')
    current = store.get_record_state('source_documents', m['source_id'])
    if current['payload'].get('source_file_sha256') != m['after']['file_sha256']:
        raise ValueError('registered source epoch changed')
    group = m['groups'][index]
    unit = root/'units'/f'{index:03d}'
    from backend.pipeline.claim_source_epoch_reassessment import VERSION as EPOCH_VERSION
    epoch=immutable(root/'source-epoch-reassessment.json',{'schema_version':EPOCH_VERSION,
        'manifest':binding(root/'manifest.json'),'change_set_id':register_receipt['change_set_id']})
    # Preserve the rejected preflight. No action, reservation or answer existed;
    # this is a recorded input repair, not a new budget after a model rejection.
    old_prepared=unit/'prepared.json'
    if old_prepared.exists():
        prior=read(old_prepared)
        if prior.get('manifest') is None and not prior.get('inputs') and prior.get('excluded'):
            immutable(unit/'preflight-repair.json',{'previous':binding(old_prepared),
                'epoch':epoch,'model_calls':0,'reason':'new source epoch AI-only question admission'})
            unit=unit/'source-epoch-preflight-repair'
    # This explicit before/after authority is part of every ordinary Update.
    direction = (m['instruction'] + '\n' + RULES + '\nNew committed source ChangeSet: '
        + register_receipt['change_set_id'] + '\nOld file SHA: ' + m['before']['file_sha256']
        + '\nNew file SHA: ' + m['after']['file_sha256'])
    # Existing units keep their exact intent. New units explicitly maintain
    # linked Questions and their evidence in the same ordinary transaction.
    question_evidence=(read(unit/'intake.json').get('include_question_evidence',False)
                       if (unit/'intake.json').exists() else True)
    return intake(group, unit, instruction=direction, groups=[group], store=store,
                  policy=m['policy']['path'], creation_count=3,
                  legacy_ai_findings_direction=m['instruction'],source_epoch_reassessment=epoch,
                  include_question_evidence=question_evidence)


def run_unit(store, root, index, *, capacity):
    from backend.pipeline import claim_management_batch as batch
    root = Path(root).resolve();m = load(root)
    prepared = prepare_unit(store, root, index)
    if prepared.get('excluded'):
        return {'status': 'paused', 'excluded': prepared['excluded']}
    prepared=current_unit_prepared(store,root,index,prepared)
    register_progress(root, prepared, capacity)
    batch_root=Path(prepared['manifest']['path']).parent/'batch'
    batch.run(prepared['manifest']['path'], batch_root, model_capacity=capacity)
    from backend.pipeline.claim_refresh_handoff import inspect
    result = inspect(batch_root, store=store, check_completion=True)
    return {'group': m['groups'][index], 'result': result}


def current_unit_prepared(store, root, index, prepared):
    """Follow only SQL's already-admitted continuation; create no new action."""
    root=Path(root).resolve();m=load(root)
    check_binding(prepared['manifest'])
    jobs=read(prepared['manifest']['path'])['jobs']
    if len(jobs)!=1:raise ValueError('migration unit needs one exact joint action')
    check_binding(jobs[0]['request'])
    original=read(jobs[0]['request']['path']);request=original
    expected=['claims/'+cid for cid in m['groups'][index]]
    if original['target_keys']!=expected:raise ValueError('migration unit membership changed')
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(request['action_id']))
    seen=set();execution=None
    while parent:
        if parent['request']!=request:raise ValueError('saved migration action differs from SQL')
        rid='CMA-'+cm.sha256_json(request['action_id'])
        if rid in seen:raise ValueError('migration continuation cycle')
        seen.add(rid)
        following=parent.get('continued_by')
        if not following:break
        child=store.get_cvp_group(following)
        if not child:raise ValueError('migration continuation head is missing')
        next_request=child['request']
        if (following!='CMA-'+cm.sha256_json(next_request['action_id'])
                or next_request.get('predecessor_action_id')!=request['action_id']
                or child.get('continuation',{}).get('predecessor_action_id')!=request['action_id']
                or next_request['target_keys']!=expected
                or next_request['source_bindings']!=original['source_bindings']):
            raise ValueError('migration continuation changed its admitted source/group')
        execution=Path(child['execution_root']).resolve()
        unit=root/'units'/f'{index:03d}'
        if not execution.is_relative_to(unit):raise ValueError('migration continuation escaped its unit')
        request=next_request;parent=child
    if execution is None:return prepared
    directory=root/'units'/f'{index:03d}'/'admitted-action-heads'/cm.sha256_json(request['action_id'])
    rb=immutable(directory/'request.json',request)
    mb=immutable(directory/'manifest.json',{'jobs':[{'request':rb,'root':str(execution)}],'max_workers':1})
    immutable(directory/'origin.json',{'original_manifest':prepared['manifest'],
        'head_run_id':'CMA-'+cm.sha256_json(request['action_id']),'new_action':False,'new_budget':False})
    return {**prepared,'manifest':mb}


def register_progress(root, prepared, capacity):
    """Expose the frozen population and exact actions to the existing reader."""
    import fcntl
    import os
    from backend.api.sc_api.transcript_review import atomic
    root=Path(root).resolve();m=load(root)
    source_key=m['source_id']
    if (root/'progress-plan.json').exists():
        prior=read(root/'progress-plan.json')
        if len(prior['sources'])!=1:raise ValueError('migration progress needs one exact source')
        source_key=next(iter(prior['sources']))
    plan={'original_count':len(m['claim_ids']),'sources':{source_key:{
        'source_id':m['source_id'],'name':m['item'],
        'groups':[{'id':f'{i:03d}','members':g} for i,g in enumerate(m['groups'])]}}}
    for name,value in [('progress-plan.json',plan),('capacity.json',capacity)]:
        path=root/name
        if path.exists():
            if read(path)!=value:raise ValueError('registered migration progress input changed')
        else:immutable(path,value)
    jobs=read(prepared['manifest']['path'])['jobs']
    ids={read(job['request']['path'])['action_id'] for job in jobs}
    index=Path(os.environ.get('WANG_CLAIM_REFRESH_CAMPAIGNS_FILE') or
               str(Path(os.environ['DATA_BASE_DIR'])/'wang-knowledge-platform/admin/claim-refresh-campaigns.json'))
    index.parent.mkdir(parents=True,exist_ok=True)
    with index.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        data=read(index) if index.exists() else {'campaigns':[]}
        matches=[e for e in data['campaigns'] if e['root']==str(root)]
        if len(matches)>1:raise ValueError('duplicate migration progress root')
        entry=matches[0] if matches else {'id':'transcript-migration-'+sha(str(root).encode())[:24],
            'title':m['item']+' · Claim 來源移植','root':str(root),
            'plan':binding(root/'progress-plan.json'),'seed_action_ids':[]}
        if entry['plan']!=binding(root/'progress-plan.json'):
            raise ValueError('registered migration population changed')
        entry['seed_action_ids']=sorted(set(entry['seed_action_ids'])|ids)
        if not matches:data['campaigns'].append(entry)
        atomic(index,data)
    return entry


def resume_rejected_unit(store, root, index):
    """Explicitly admit a saved rejected answer to native correction; no call."""
    import fcntl
    import os
    from contextlib import ExitStack
    from backend.pipeline.claim_management_batch import load as load_batch
    from backend.pipeline.claim_review_recovery import resume_rejected_output
    from backend.pipeline.source_reconciliation_runner import exclusive
    from backend.pipeline.source_reconciliation_runtime import maintenance_lock
    from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock
    root=Path(root).resolve();m=load(root)
    unit=root/'units'/f'{index:03d}'
    if (unit/'preflight-repair.json').exists():unit=unit/'source-epoch-preflight-repair'
    prepared=current_unit_prepared(store,root,index,read(unit/'prepared.json'))
    batch_root=Path(prepared['manifest']['path']).parent/'batch';result={}
    batch_root.mkdir(parents=True,exist_ok=True)
    with ExitStack() as stack:
        controller=stack.enter_context((batch_root/'controller.lock').open('a'))
        fcntl.flock(controller,fcntl.LOCK_EX|fcntl.LOCK_NB)
        stack.enter_context(maintenance_lock())
        stack.enter_context(exclusive_cvp_run_lock({'global_lock_path':str(Path(os.environ['DATA_BASE_DIR'])/
            'wang-knowledge-platform/staging/cvp-production-357/registry-cvp.lock')}))
        capacity=read(root/'capacity.json')
        lane=Path(capacity['root'])/'sources'/(cm.sha256_json(m['source_id'])+'.lock')
        handle=stack.enter_context(lane.open('a'))
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        _,jobs=load_batch(prepared['manifest']['path'],batch_root,store=store)
        if {key for request,_ in jobs.values() for key in request.target_keys}!={
                'claims/'+cid for cid in m['groups'][index]}:
            raise ValueError('rejected recovery changed fixed group membership')
        for aid,(request,execution) in jobs.items():
            stack.enter_context(exclusive(execution))
            parent=store.get_cvp_group('CMA-'+cm.sha256_json(aid))
            if (not parent or parent['request']!=request.model_dump(mode='json')
                    or parent['execution_root']!=str(execution) or parent.get('continued_by')):
                raise ValueError('rejected recovery requires the exact original action head')
            rejected=parent['result'].get('returned_rejected_call')
            if not rejected:
                result[aid]={'status':'unchanged','result':parent['result']}
                continue
            rid,role=rejected.rsplit(':',1)
            result[aid]=resume_rejected_output(store,rid,role=role,error=parent['result']['detail'])
    return {'model_calls':0,'new_budget':False,'recoveries':result}


def verify(store, root):
    """Current native qualifications plus exact live quotation checks."""
    root = Path(root).resolve();m = load(root)
    snapshot = cm.source.source_snapshot(cm.source.FileBinding.model_validate(m['after']))
    document=store.get_record_state('source_documents',m['source_id'])
    if (not document or document['retired'] or
            document['payload'].get('source_file_sha256')!=m['after']['file_sha256']):
        raise ValueError('migration verification source epoch changed')
    from backend.pipeline import claim_management_batch as batch
    jobs={}
    for index in range(len(m['groups'])):
        unit=root/'units'/f'{index:03d}'
        if (unit/'preflight-repair.json').exists():unit=unit/'source-epoch-preflight-repair'
        if not (unit/'prepared.json').exists():continue
        prepared=read(unit/'prepared.json')
        if not prepared.get('manifest'):continue
        prepared=current_unit_prepared(store,root,index,prepared)
        for job in read(prepared['manifest']['path'])['jobs']:
            check_binding(job['request'])
            request=cm.Request.model_validate_json(Path(job['request']['path']).read_text())
            jobs[request.action_id]=(request,Path(job['root']))
    native=batch._paragraph_results(store,jobs)
    created=[key.split('/',1)[1] for key in native['created_keys']]
    retired=set(native.get('retired_keys',[]))
    from backend.pipeline.claim_direct_scope import scope
    results = {}
    for cid in dict.fromkeys([*m['claim_ids'],*created]):
        state = store.get_record_state('claims', cid)
        if not state or state['retired']:
            results[cid] = {'status': 'reviewed_retirement' if 'claims/'+cid in retired else 'retired_or_missing',
                            'qualification': store.claim_management_qualification(cid)}
            continue
        qualification = store.claim_management_qualification(cid)
        failures = []
        records = store.read_claim_context([('claims', cid)])
        # Use the common read-only own-proof footprint. A neighbour's private
        # fragments are not this Claim's evidence or migration obligation.
        probe=cm.Request(action_id='migration-quote-check',operation='Update',allowed_operations=['Update'],
            target_keys=['claims/'+cid],permissions=[],source_bindings={m['source_id']:m['after']},policy=m['policy'],
            authorization={'path':str(root/'not-an-execution-authority'),'file_sha256':'0'*64})
        states={cm.source.key(*key):cm.model_state(value) for key,value in records.items()}
        own=scope(probe,states,include_write_context=False,include_owner_context=False,
                  include_impact_evidence=False)['record_keys']
        for (col, oid), value in records.items():
            if (col != 'source_fragments' or cm.source.key(col,oid) not in own
                    or value['retired'] or value['payload'].get('source_id') != m['source_id']):
                continue
            fragment = value['payload'];p = snapshot['paragraphs'].get(str(fragment.get('paragraph_key')), {})
            if (not fragment.get('verbatim_excerpt') or fragment['verbatim_excerpt'] not in p.get('text', '')
                    or fragment.get('source_sha256') != snapshot['body_sha256']):
                failures.append(oid)
        results[cid] = {'status': 'current' if qualification.get('ready') and not failures else 'pending',
                        'qualification': qualification, 'invalid_live_quote_ids': failures}
    value = {'schema_version': VERSION, 'source_id': m['source_id'],
             'results': results,'original_claim_ids':m['claim_ids'],'created_claim_ids':created,
             'native_completion':native,
             'complete':all(r['status'] in {'current','reviewed_retirement'} for r in results.values())
                        and len(jobs)==len(m['groups']) and native['all_actions_completed'],
             'completion_scope':'claims_and_own_evidence_only',
             'cvp_route_migration_complete': False}
    # A report is a dated view, not another qualification authority.
    from backend.api.sc_api.transcript_review import atomic
    atomic(root/'current-report.json', value)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare','register','prepare-unit','run-unit','resume-rejected-unit','verify','comparison'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source-id');parser.add_argument('--item')
    parser.add_argument('--beta-root', type=Path);parser.add_argument('--instruction-file', type=Path)
    parser.add_argument('--group-size', type=int, default=5);parser.add_argument('--unit', type=int)
    parser.add_argument('--capacity', type=Path);parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    store = PostgresKnowledgeStore()
    if args.command=='prepare':
        value=prepare(store,beta_root=args.beta_root,item=args.item,source_id=args.source_id,
                      root=args.root,instruction=args.instruction_file.read_text(),group_size=args.group_size)
        print(json.dumps({'claim_count':len(value['claim_ids']),'groups':len(value['groups'])}))
        return
    if not args.execute:
        parser.error('mutating/model stages require --execute')
    if args.command=='register':value=register(store,args.root)
    elif args.command=='prepare-unit':value=prepare_unit(store,args.root,args.unit)
    elif args.command=='run-unit':value=run_unit(store,args.root,args.unit,capacity=read(args.capacity))
    elif args.command=='resume-rejected-unit':value=resume_rejected_unit(store,args.root,args.unit)
    elif args.command=='comparison':
        from backend.pipeline.transcript_claim_migration_report import compare
        value=compare(store,args.root)
    else:value=verify(store,args.root)
    print(json.dumps(value,ensure_ascii=False,default=str))


if __name__=='__main__':main()
