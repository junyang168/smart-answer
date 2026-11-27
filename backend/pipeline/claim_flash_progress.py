"""Read saved Flash results in layer order; never launch models or open SQL."""
import hashlib
import json
from pathlib import Path

ORDER=['grouping','source','evidence_observation','claims']
CLAIM_STEPS={'statements':'claim_statement_tasks_only','arguments':'group_lower_argument_routes_only',
             'relations':'group_claim_relations_only','missing':'group_missing_claims_only'}


def read(reference):
    path=Path(reference['path'])
    if not path.is_absolute():raise ValueError('saved result path must be absolute')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=reference['sha256']:raise ValueError('saved result binding changed: '+str(path))
    return json.loads(raw)


def complete(value):
    return (value.get('status') in {'verified','applied'} and not value.get('pending')
            and (value.get('applied') or not any(value.get(k) for k in ['updates','new_fragments','retiring_keys','new_claim_candidates'])))


def inspect(manifest):
    """Progress from exact receipts, not current SQL eligibility or a new review."""
    if manifest.get('version')!='claim_flash_saved_flow_v1':raise ValueError('unknown saved-flow contract')
    plan=read(manifest['grouping']);sid=manifest['source_id']
    if plan['source_id']!=sid:raise ValueError('grouping source differs')
    groups=plan['groups'];group_ids=[g['group_id'] for g in groups]
    all_claims=[cid for g in groups for cid in g['claim_ids']]
    if len(set(group_ids))!=len(groups) or len(set(all_claims))!=len(all_claims):raise ValueError('shared grouping repeats a group or Claim')
    source=read(manifest['source'])
    if source['source_id']!=sid or source['source_body_sha256']!=plan['source_body_sha256']:
        raise ValueError('saved source/grouping version differs')
    source_done=source.get('status')=='verified'
    evidence=read(manifest['evidence'])
    if evidence['source_id']!=sid:raise ValueError('Evidence source differs')
    if 'completed_groups_in_this_scope' in evidence:
        # Existing scope reconciliation already includes previously finished FG002.
        done=set(evidence['completed_groups_in_this_scope'])
        if evidence.get('previously_completed_group_preserved'):done.add(evidence['previously_completed_group_preserved'])
        if evidence.get('sermon_group_count')!=len(groups) or evidence.get('total_completed_sermon_groups')!=len(done):
            raise ValueError('saved Evidence counts differ from shared grouping')
    else:
        done={g['group_id'] for g in evidence.get('groups',[]) if complete(g)}
    if not done<=set(group_ids):raise ValueError('Evidence result contains an unplanned group')
    remaining=[gid for gid in group_ids if gid not in done]
    holds=manifest.get('holds',[])
    if any(h['group_id'] not in group_ids for h in holds):raise ValueError('hold belongs to unplanned group')
    held_candidates={h['claim_id'] for h in holds if h.get('claim_id')}
    progress=[];completed_checks=[];recorded_candidates=[]
    if not set(manifest.get('claims',{}))<=set(group_ids):raise ValueError('Claim result group is unplanned')
    for group in groups:
        gid=group['group_id'];saved=manifest.get('claims',{}).get(gid,{})
        if not set(saved)<=set(CLAIM_STEPS):raise ValueError('unknown Claim step')
        steps=[]
        for step,scope in CLAIM_STEPS.items():
            if step not in saved:steps.append({'step':step,'state':'not_recorded','action':'not_started'});continue
            value=read(saved[step])
            if value['source_id']!=sid or value.get('completion_scope')!=scope or value.get('claim_ids')!=group['claim_ids']:
                raise ValueError('saved Claim result scope differs: '+gid+'/'+step)
            ready=complete(value);new_ids=value.get('new_claim_ids',[])
            recorded_candidates.extend(new_ids)
            held=sorted(set(new_ids)&held_candidates)
            steps.append({'step':step,'state':'held' if held else 'completed' if ready else 'unfinished',
                'action':'hold' if held else 'skip' if ready else 'resume_saved_stage',
                'held_claim_ids':held,'pending':value.get('pending',[])})
        if all(s['state']=='completed' for s in steps[:3]):completed_checks.extend(group['claim_ids'])
        progress.append({'group_id':gid,'claim_ids':group['claim_ids'],'steps':steps})
    unknown=held_candidates-set(all_claims)-set(recorded_candidates)
    if unknown:raise ValueError('held Claim is neither a planned target nor a recorded new candidate')
    if not source_done:next_action={'layer':'source','reason':'saved_source_stage_unfinished'}
    elif remaining:next_action={'layer':'source' if evidence.get('status')=='source_clarification_required' else 'evidence_observation',
        'group_ids':remaining,'reason':evidence.get('hold_reason','Evidence groups not completed')}
    else:
        unfinished=[{'group_id':g['group_id'],**s} for g in progress for s in g['steps'] if s['state'] not in {'completed','held'}]
        next_action={'layer':'claims','remaining_steps':unfinished} if unfinished else {'layer':None,'reason':'only_holds_remain' if holds else 'saved_stages_completed'}
    return {'version':manifest['version'],'source_id':sid,'execution_order':ORDER,
        'grouping':{'state':'completed','groups':len(groups),'claims':len(all_claims)},
        'source':{'state':'completed' if source_done else 'unfinished'},
        'evidence_observation':{'completed_group_ids':[gid for gid in group_ids if gid in done],'remaining_group_ids':remaining},
        'claim_groups':progress,'completed_existing_claim_checks':completed_checks,'holds':holds,
        'next_action':next_action,'mode':'saved_results_only','model_calls':0,'database_writes':0,
        'claim_qualification':'not_granted','scope':'saved stage progress; not current database eligibility'}


def main():
    import argparse
    parser=argparse.ArgumentParser(description='Read exact saved Flash results; grouping first, then whole-source layers')
    parser.add_argument('--manifest',required=True);parser.add_argument('--out')
    args=parser.parse_args();result=inspect(json.loads(Path(args.manifest).read_text()))
    text=json.dumps(result,ensure_ascii=False,indent=2)
    if args.out:Path(args.out).write_text(text)
    print(text)


if __name__=='__main__':main()
