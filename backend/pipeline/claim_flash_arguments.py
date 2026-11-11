"""Group argument completeness and typed pairs; consume statements and lower judgments."""
from copy import deepcopy
import fcntl
import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from backend.pipeline import claim_flash_common as f,claim_flash_statements as statements
from backend.pipeline import claim_update_flash as flash,claim_flash_pairs as pairs,claim_flash_questions as q
from backend.pipeline import claim_refresh as c,evidence_refresh as e

VERSION='claim_flash_arguments_v1'
CANDIDATE_SCOPE='claim_direct_material_paragraphs_v1'
PROMPT=(flash.PROMPT+'只檢查指定Claim的論證路線是否完整，不重新審查已驗證的節點statement，不新建節點。'
 '對照本組原文、材料與existing_edges；材料有一條連接、Claim已有足夠支持，都不代表重要論據沒有漏接。'
 '找教授實際使用但未被現有直接或間接路線承載的重要經文、例子及推理步驟。'
 '已有間接路線完整承載者不強加直接邊；主題相近不算論證。'
 '不要求每筆材料都有連接；背景及同義重複不必為消除孤立而加邊。'
 '每條缺少連接一次給齊兩端object_key、relation_type、direction、直接相關fragment_ids及簡短reason。'
 '這是該連接的完整處置；依教授原文決定最合適的直接類型與方向，同一端點組合不要重複提出。'
 '不改既有邊或文字，已有的直接或間接路線作唯讀參考。'
 '缺材料本身用needs_material，缺來源用needs_source；每項恰好一次。'
 + f.RELATION_REASON_RULES)
class Proposed(pairs.CompletedProposal):
    fragment_ids:list[str]=Field(min_length=1)
class Route(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    job_id:str
    action:Literal['complete','needs_source','needs_material']
    pairs:list[Proposed]
    reason:str=Field(min_length=1)
class Routes(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    items:list[Route]


def material_paragraphs(keys,records):
    return {records['source_fragments/'+fid]['payload']['paragraph_key'] for key in keys
            for fid in e.evidence_fragment_ids(records[key]['payload']) if 'source_fragments/'+fid in records}


def material_candidates(pool,records,paragraphs,source_id):
    """Read one bounded source neighbourhood, never a Claim-neighbour graph."""
    result=set(pool)
    for key,row in records.items():
        if key.split('/')[0] not in {'evidence_steps','observations'} or row.get('retired'):continue
        ids=e.evidence_fragment_ids(row['payload'])
        if ids and all('source_fragments/'+fid in records for fid in ids):
            if (all(records['source_fragments/'+fid]['payload'].get('source_id')==source_id for fid in ids)
                    and material_paragraphs([key],records)<=paragraphs):result.add(key)
    return result


def active_root(root):
    """A new input correction keeps the original stopped verification immutable."""
    root=Path(root).resolve();path=root/'material-scope-continuation.json'
    if not path.exists():return root
    authority=json.loads(path.read_text());parent=json.loads((root/'verification.json').read_text())
    before=json.loads((root/'before.json').read_text())
    if (authority.get('contract')!=CANDIDATE_SCOPE or not authority.get('owner_direction')
            or authority.get('parent_verification_sha256')!=e.digest(parent)
            or authority.get('parent_before_sha256')!=e.digest(before)
            or parent.get('version')!=VERSION or parent.get('status')!='needs_attention'
            or not parent.get('pending') or any(x.get('layer')!='claim_connections' for x in parent['pending'])
            or (root/'commit-intent.json').exists()):
        raise ValueError('argument material correction differs from its uncommitted parent')
    return root/'material-scope-continuations'/e.digest(parent)


CONTINUATION_PROMPT='本次只補判remaining_findings；accepted_proposed_edges已處置，保留並只補缺口。不重做其他Claim的判斷。'

FINAL_SUPPORT_PROMPT='只判斷最終論據集合是否仍完整支持此Claim及關鍵經文。keep需有據；缺內容needs_source，缺連接needs_connection。不得修改節點或再建邊。'

FINAL_SUPPORT_BATCH_PROMPT=FINAL_SUPPORT_PROMPT+'每個job_id獨立判定且恰好一次；只用該job材料，不將其他job的論據視為它的支持。'

SCRIPTURE_CLARIFICATION = (
    '只復核上一輪的經文疑點。區分教授實際引用的具體經文與泛稱「聖經所說」。'
    '只有原文確實引用了可辨認、承重的經文而材料遺漏時，才因經文不足選needs_source。'
    '若原文只是泛稱，論據已完整支持主張，選keep；不得補造經文。'
)


def clarify_final_scripture(store, source_id, group, parent_root, root, ports, authority, *, apply):
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (root / 'verification.json').exists():
            return statements.finish(store, root) if apply else json.loads((root / 'verification.json').read_text())
        before = json.loads((parent_root / 'before.json').read_text())
        parent = json.loads((parent_root / 'verification.json').read_text())
        records, sources = f.load_group_source(store, source_id, group)
        if before['sources'] != sources or any(records.get(k) != v for k, v in before['records'].items()):
            raise ValueError('scripture clarification parent input changed')
        finding, = parent['pending']
        request_path = Path(authority['final_support_request_path']).resolve()
        if not request_path.is_relative_to(parent_root / 'postimage-disposition'):
            raise ValueError('scripture clarification must use the parent final-support request')
        request = json.loads(request_path.read_text())
        response = json.loads((request_path.parent / 'response.json').read_text())
        job, = [j for j in request['input']['jobs'] if j['job_id'] == finding['object_key']]
        prior, = [item for item in response['items'] if item['job_id'] == job['job_id']]
        if (request_path.parent.name != e.digest(request)
                or request['input']['task'] != 'argument_final_support'
                or request['identity'] != ports.identity['gpt']
                or e.digest(response) != authority['final_support_response_sha256']
                or job['findings'] != {'scripture': 'missing'}
                or prior['action'] != 'needs_source' or prior['reason'] != finding['reason']):
            raise ValueError('scripture clarification differs from the recorded finding')
        candidate = {**job, 'previous_reason': prior['reason']}
        _, reply = next(f.gpt(root / 'scripture', ports, 'argument_final_support', [candidate],
                              statements.Edits.model_json_schema(), FINAL_SUPPORT_PROMPT + SCRIPTURE_CLARIFICATION,
                              semantic_review=False))
        edit, = statements.Edits.model_validate(reply).items
        f.exactly([edit.model_dump()], [job['job_id']], 'job_id')
        result = deepcopy(parent)
        if edit.action == 'keep' and edit.statement is None and edit.scripture_refs is None:
            result.update(status='verified', pending=[])
        result['outcomes'].append({'object_key': edit.job_id, 'basis': 'scripture_clarification',
                                   'action': edit.action, 'reason': edit.reason})
        e.save(root / 'before.json', before)
        e.save(root / 'intent.json', json.loads((parent_root / 'intent.json').read_text()))
        e.save(root / 'verification.json', result)
        return statements.finish(store, root) if apply else e.publish(root, result)


def final_support_decisions(root,ports,jobs):
    """Reuse exact legacy single-job replies; batch only unanswered checks."""
    root=Path(root);schema=statements.Edits.model_json_schema();answers={};remaining=[]
    def consume(batches):
        for batch,raw in batches:
            edits=statements.Edits.model_validate(raw).items
            f.exactly([x.model_dump() for x in edits],[j['job_id'] for j in batch],'job_id')
            for edit in edits:answers[edit.job_id]=edit
    for job in jobs:
        legacy={'identity':ports.identity['gpt'],'system':FINAL_SUPPORT_PROMPT,
                'input':{'task':'argument_final_support','jobs':[job]},'schema':schema}
        saved=root/'gpt'/e.digest(legacy)
        if (saved/'response.json').exists() or (saved/'started.json').exists():
            # Unknown earlier calls still fail closed in the common recorder.
            consume(f.gpt(root,ports,'argument_final_support',[job],schema,FINAL_SUPPORT_PROMPT,semantic_review=False))
        else:remaining.append(job)
    consume(f.gpt(root,ports,'argument_final_support',remaining,schema,FINAL_SUPPORT_BATCH_PROMPT,semantic_review=False))
    return [answers[j['job_id']] for j in jobs]


def statement_proof(root,claim_ids,records,sources):
    root=Path(root).resolve();result=json.loads((root/'verification.json').read_text());before=json.loads((root/'before.json').read_text())
    if (result.get('version')!=statements.VERSION or result['status'] not in {'verified','needs_attention'}
            or result.get('completion_scope')!='claim_statement_tasks_only' or not set(claim_ids)<=set(result['claim_ids'])):
        raise ValueError('completed Claim statement tasks required')
    if before['sources']!=sources:raise ValueError('statement source differs')
    prepared={}
    for cid in claim_ids:
        key='claims/'+cid;prior=result['updates'].get(key,before['records'][key]['payload']);now=records[key]['payload']
        pending=[x for x in result['pending'] if x['object_key']==key]
        if any(x['layer']!='claim_connections' for x in pending):raise ValueError('statement has unresolved non-connection findings')
        if not any(x['object_key']==key and x['task']=='claim_fidelity' and
                   (x.get('verdict')=='faithful' or x.get('action') in {'keep','repair'}) for x in result['outcomes']):
            raise ValueError('Claim fidelity decision missing')
        # Connections are this stage's review targets; their changes do not alter the statement itself.
        if any(prior.get(k)!=now.get(k) for k in ['statement','attribution','scripture_refs']):
            base=before['records'][key]['payload']
            if (root/'commit.json').exists() or any(base.get(k)!=now.get(k) for k in ['statement','attribution','scripture_refs']):
                raise ValueError('Claim statement changed since its completed tasks: '+cid)
            post={**now,**{k:prior[k] for k in ['statement','scripture_refs'] if k in prior}}
            prepared[key]=flash.current_claim_payload(post)
    return result,before,prepared


def run(store,source_id,plan,group_id,evidence_root,statement_root,root,ports,*,apply=False,claim_ids=None,_parent_root=None):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    if _parent_root is None and active_root(root)!=root:
        stopped=json.loads((root/'result.json').read_text())
        if stopped.get('pending'):e.save(root/'stopped-results'/(e.digest(stopped)+'.json'),stopped)
        authority = json.loads((root / 'material-scope-continuation.json').read_text())
        if authority.get('final_support_request_path'):
            group = next(g for g in plan['groups'] if g['group_id'] == group_id)
            result = clarify_final_scripture(store, source_id, group, root, active_root(root), ports, authority, apply=apply)
        else:
            result=run(store,source_id,plan,group_id,evidence_root,statement_root,active_root(root),ports,
                apply=apply,claim_ids=claim_ids,_parent_root=root)
        return e.publish(root,result)
    parent=None
    if _parent_root is not None:
        if active_root(_parent_root)!=root:raise ValueError('argument correction root differs')
        parent=json.loads((Path(_parent_root)/'verification.json').read_text())
    group=next(g for g in plan['groups'] if g['group_id']==group_id);ids=group['claim_ids']
    if claim_ids is not None:
        if claim_ids not in group.get('sequential_claim_batches', []):
            raise ValueError('Claim batch is not frozen in the completed Evidence plan')
        ids=claim_ids
    if not ids or len(ids)>10 or len(ids)!=len(set(ids)):raise ValueError('one nonempty existing Claim group required')
    with (root/'run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);pin=f.bind_source(store,source_id,root)
        saved=json.loads((root/'intent.json').read_text()) if (root/'intent.json').exists() else None
        expanded=not saved or saved.get('candidate_scope')==CANDIDATE_SCOPE
        e.save(root/'intent.json',{'version':VERSION,'source':pin,'group':group,'models':ports.identity,
            **({'claim_ids':ids} if claim_ids is not None else {}),
            'evidence_root':str(Path(evidence_root).resolve()),'statement_root':str(Path(statement_root).resolve()),
            'policy_sha256':e.digest([PROMPT,Routes.model_json_schema()]),
            **({'candidate_scope':CANDIDATE_SCOPE} if expanded else {}),
            **({'parent_verification_sha256':e.digest(parent),
                'continuation_policy_sha256':e.digest([CONTINUATION_PROMPT,CANDIDATE_SCOPE])} if parent is not None else {})})
        if (root/'verification.json').exists():return statements.finish(store,root) if apply else json.loads((root/'verification.json').read_text())
        records,sources=f.load_group_source(store,source_id,group);c.fetch_claims(store,ids);original=deepcopy(records)
        if parent is not None:
            old=json.loads((Path(_parent_root)/'before.json').read_text())
            if old['sources']!=sources or parent['claim_ids']!=ids:raise ValueError('argument correction source/scope changed')
            if any(records.get(k)!=v for k,v in old['records'].items()):raise ValueError('argument correction parent input changed')
        if (plan.get('source_id')!=source_id or plan.get('source_body_sha256')!=sources[source_id]['body_sha256']
                or json.loads((Path(evidence_root)/'intent.json').read_text())['groups']!=plan):
            raise ValueError('argument group/source differs from completed Evidence plan')
        if any(not f.belongs_to_source(records['claims/'+cid]['payload'],records,source_id) for cid in ids):
            raise ValueError('argument target belongs to another source')
        old_checks,old_before,statement_updates=statement_proof(statement_root,ids,records,sources)
        records=f.apply_overlay(records,statement_updates)
        targets={'claims/'+cid for cid in ids}
        pool={k for k in group['lower_record_keys'] if k.split('/')[0] in {'evidence_steps','observations'}}
        for key in targets:pool.update(x['object_key'] for x in flash.claim_material(key,records,sources,source_id)['evidence'])
        candidates=material_candidates(pool,records,set(group['paragraph_keys'])|material_paragraphs(pool,records),source_id) if expanded else pool
        proof=statements.verify_evidence(evidence_root,candidates,records,sources)
        nodes=candidates|targets;by_id={k.split('/',1)[1]:k for k in nodes}
        if len(by_id)!=len(nodes):raise ValueError('ambiguous argument endpoint')
        edges={k:r['payload'] for k,r in records.items() if k.startswith('knowledge_relations/')
               and {r['payload']['from_id'],r['payload']['to_id']}<=set(by_id)}
        claim_edges={k:r['payload'] for k,r in records.items() if k.startswith('claim_relations/')
                     and {r['payload']['from_id'],r['payload']['to_id']}<=set(by_id)}
        implicit=[{'a':'evidence_steps/'+eid,'b':key} for key in sorted(targets) for eid in records[key]['payload'].get('evidence_step_ids',[])]
        remaining={p['object_key'] for p in parent['pending']} if parent is not None else targets
        completion_pool=candidates
        if parent is not None:
            own={x['object_key'] for key in remaining for x in flash.claim_material(key,records,sources,source_id)['evidence']}
            own.update(k for item in parent['routes'] if item['job_id'] in remaining
                       for pair in item['pairs'] for k in (pair['a'],pair['b']) if k in candidates)
            completion_pool=material_candidates(own,records,material_paragraphs(own,records),source_id)
        completion_nodes=completion_pool|remaining
        fragments={fid:records['source_fragments/'+fid]['payload'] for key in completion_pool for fid in e.evidence_fragment_ids(records[key]['payload'])}
        shared={'nodes':{k:{field:r for field,r in records[k]['payload'].items() if field in
                {'statement','speaker','stance','attribution','scripture_refs','source_fragment_id','source_fragment_ids'}} for k in sorted(completion_nodes)},
            'fragments':{fid:{'paragraph_key':p['paragraph_key'],'quote':p['verbatim_excerpt']} for fid,p in fragments.items()},
            'existing_edges':[{'a':by_id[p['from_id']],'b':by_id[p['to_id']],'relation_type':p['relation_type'],'reason':p.get('reason','')} for p in edges.values()]
                 +[{'a':by_id[p['from_id']],'b':by_id[p['to_id']],'relation_type':p['relation_type'],'reason':p.get('reason',''),'read_only':True} for p in claim_edges.values()]
                 +[{**p,'relation_type':'supports','reason':'Claim.evidence_step_ids'} for p in implicit]}
        shared['existing_edges']=[p for p in shared['existing_edges'] if {p['a'],p['b']}<=completion_nodes]
        shared['existing_edges']=sorted(shared['existing_edges'],key=e.digest)
        shared.update(f.source_interpretation(sources,{source_id:group['paragraph_keys']}))
        proposals=[];pending=[];routes=[];claim_handoffs={}
        jobs=[{'job_id':k,**({'remaining_findings':[p for p in parent['pending'] if p['object_key']==k]} if parent is not None else {})} for k in sorted(remaining)]
        prompt=PROMPT
        if parent is not None:
            shared['accepted_proposed_edges']=[p for item in parent['routes'] for p in item['pairs'] if {p['a'],p['b']}<=completion_nodes]
            prompt+=CONTINUATION_PROMPT
        route_map={p['job_id']:deepcopy(p) for p in parent['routes']} if parent is not None else {}
        for batch,raw in f.gpt(root/'completeness',ports,'argument_completeness',jobs,Routes.model_json_schema(),prompt,shared=shared,semantic_review=False):
            replies=Routes.model_validate(raw).items;f.exactly([x.model_dump() for x in replies],[j['job_id'] for j in batch],'job_id')
            for item in replies:
                value=item.model_dump()
                prior=route_map.get(item.job_id,{}).get('pairs',[])
                value['pairs']=list({e.digest(p):p for p in prior+value['pairs']}.values())
                route_map[item.job_id]=value
        routes=list(route_map.values())
        known_fragments={fid for key in candidates for fid in e.evidence_fragment_ids(records[key]['payload'])}
        retained_pairs={e.digest(p) for item in parent['routes'] for p in item['pairs']} if parent else set()
        for value in routes:
            item=Route.model_validate(value)
            if item.action!='complete':pending.append({'object_key':item.job_id,'layer':'source' if item.action=='needs_source' else 'evidence','reason':item.reason})
            for pair in item.pairs:
                provided=completion_nodes if item.job_id in remaining and e.digest(pair.model_dump()) not in retained_pairs else nodes
                if pair.a not in provided or pair.b not in provided or pair.a==pair.b or not set(pair.fragment_ids)<=known_fragments:
                    raise ValueError('unprovided argument endpoint or source fragment')
                if pair.a.startswith('claims/') and pair.b.startswith('claims/'):
                    claim_handoffs[e.digest(pair.model_dump())]=pair.model_dump()
                    continue
                proposals.append(pair.model_dump())
        # Extra candidates are read-only until a route actually selects them.
        pool.update(by_id[p['from_id']] for p in edges.values()
                    if by_id[p['to_id']] in targets and p['relation_type'] in {'supports','corroborates','explains'})
        pool.update(k for p in proposals for k in (p['a'],p['b']) if k in candidates)
        selected=pool|targets
        edges={k:p for k,p in edges.items() if {by_id[p['from_id']],by_id[p['to_id']]}<=selected}
        updates=deepcopy(statement_updates);retiring=[];outcomes=[];new=[];removals=[]
        for kind in ['lower','attachment']:
            local=[k for k,p in edges.items() if pairs.allowed_pair(by_id[p['from_id']],by_id[p['to_id']],kind)]
            proposed=[p for p in proposals if pairs.allowed_pair(p['a'],p['b'],kind)]
            pair_root=Path(_parent_root)/('pairs-'+kind) if parent is not None else root/('pairs-'+kind)
            result=pairs.review(records,sources,local,[],pair_root,ports,kind=kind,implicit=implicit if kind=='attachment' else (),
                completed_proposals=[{k:p[k] for k in pairs.CompletedProposal.model_fields} for p in proposed])
            updates.update(result['updates']);retiring+=result['retiring_keys'];new+=result['new_relations']
            pending+=result['pending'];outcomes+=result['outcomes'];removals+=result['implicit_changes']
        for post in new:
            oid='KR-FLA-'+e.digest([source_id,post['from_id'],post['to_id'],post['relation_type']])[:24]
            updates['knowledge_relations/'+oid]={'relation_id':oid,**post}
        working=f.apply_overlay(records,updates)
        for key in retiring:working.pop(key,None)
        for key in sorted(targets):
            p=deepcopy(records[key]['payload']);removed={r['a'].split('/',1)[1] for r in removals if r['b']==key}
            supported=[r['payload']['from_id'] for k,r in working.items() if k.startswith('knowledge_relations/')
                and r['payload']['to_id']==p['claim_id'] and r['payload']['relation_type']=='supports'
                and 'evidence_steps/'+r['payload']['from_id'] in pool]
            p['evidence_step_ids']=list(dict.fromkeys([x for x in p.get('evidence_step_ids',[]) if x not in removed]+sorted(supported)))
            if p!=records[key]['payload']:
                p['occurrences']=c.refresh_occurrences(p,p['evidence_step_ids'],working,source_id,source_binding=sources[source_id]['file'])
                updates[key]=flash.current_claim_payload(p)
        working=f.apply_overlay(working,updates)
        # Reciprocal owner fields are structural writes, not a lower content re-review.
        for key in sorted(pool):
            if not key.startswith('evidence_steps/'):continue
            p=deepcopy(records[key]['payload']);owners=p.get('produced_claim_ids',[])
            for ck in targets:
                cid=ck.split('/',1)[1];owners=[x for x in owners if x!=cid]
                if key.split('/',1)[1] in working[ck]['payload'].get('evidence_step_ids',[]):owners.append(cid)
            if set(owners)!=set(p.get('produced_claim_ids',[])):
                p['produced_claim_ids']=list(dict.fromkeys(owners));updates[key]=p
        # Reuse unchanged whole-support/scripture decisions; only changed argument sets need those tasks.
        final_jobs=[]
        previous=f.apply_overlay(records,parent['updates']) if parent is not None else None
        if previous is not None:
            for retired in parent.get('retiring_keys',[]):previous.pop(retired,None)
        for key in sorted(targets):
            if (not working[key]['payload'].get('evidence_step_ids') and not any(
                    k.startswith('knowledge_relations/') and r['payload']['to_id']==key.split('/',1)[1]
                    and r['payload']['relation_type'] in {'supports','corroborates','explains'} for k,r in working.items())):
                pending.append({'object_key':key,'layer':'claim_connections','reason':'final_argument_has_no_support'})
                continue
            prior=flash.claim_material(key,old_before['records'],old_before['sources'],source_id)
            if key in old_checks['updates']:
                prior_records=f.apply_overlay(old_before['records'],old_checks['updates'])
                prior=flash.claim_material(key,prior_records,old_before['sources'],source_id)
            state=flash.claim_material(key,working,sources,source_id)
            if previous is not None and state==flash.claim_material(key,previous,sources,source_id):
                unresolved=[p for p in parent['pending'] if p['object_key']==key]
                pending.extend(deepcopy(unresolved))
                outcomes.append({'object_key':key,'basis':'retained_parent_unresolved' if unresolved else 'reused_parent_final_support_and_scripture'})
                continue
            if (state==prior and not any(x['object_key']==key for x in old_checks['pending'])
                    and not (parent is not None and key in remaining)):
                outcomes.append({'object_key':key,'basis':'reused_statement_support_and_scripture'});continue
            answers=f.jev(root/'postimage-support',ports,state,{k:q.claim(with_references=bool(state.get('claim_references')))[k] for k in ['support','scripture']})
            issues={k:v for k,v in answers.items() if v!=('supported' if k=='support' else 'complete')}
            if issues:final_jobs.append({'job_id':key,'material':state,'findings':issues})
        for edit in final_support_decisions(root/'postimage-disposition',ports,final_jobs):
            if edit.action!='keep':pending.append({'object_key':edit.job_id,'layer':'claim_connections','reason':edit.reason})
            outcomes.append({'object_key':edit.job_id,'basis':'gpt_final_support','action':edit.action,'reason':edit.reason})
        read=set(nodes)|set(edges)|set(claim_edges)|{'source_fragments/'+fid for key in candidates for fid in e.evidence_fragment_ids(records[key]['payload'])}|{'source_documents/'+source_id}
        read.update(k for k,r in records.items() if k.startswith('claim_relation_constraints/') and {r['payload']['source_id'],r['payload']['target_id']}&set(by_id))
        allowed=targets|pool|set(edges)|{k for k in updates if k.startswith('knowledge_relations/') and k not in records}
        if not set(updates)<=allowed:raise ValueError('argument write scope expanded')
        for key in pool&set(updates):
            if {k:v for k,v in updates[key].items() if k!='produced_claim_ids'}!={k:v for k,v in records[key]['payload'].items() if k!='produced_claim_ids'}:
                raise ValueError('argument stage modified lower content')
        result={'version':VERSION,'status':'needs_attention' if pending else 'verified','source_id':source_id,'claim_ids':ids,
            'updates':updates,'new_fragments':{},'retiring_keys':sorted(set(retiring)),'pending':pending,'routes':routes,'outcomes':outcomes,
            'claim_pair_handoffs':list(claim_handoffs.values()),
            'completion_scope':'group_lower_argument_routes_only','claim_qualification':'not_granted','applied':False,
            'resolved_statement_handoffs':[x for x in old_checks['pending'] if x['object_key'] in targets and not pending],
            'commit_reason':'Group argument completeness and typed pairs; preserve lower content and Claim statements'}
        e.save(root/'before.json',{'records':{k:original[k] for k in read if k in original},'sources':sources});e.save(root/'consumed-evidence.json',proof)
        e.save(root/'verification.json',result)
        return statements.finish(store,root) if apply else e.publish(root,result)


def main():
    import argparse
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
    from backend.pipeline.claim_model_capacity import ClaimModelCapacity
    parser=argparse.ArgumentParser(description='One-group argument routes, using completed statement and Evidence tasks')
    for name in ['source-id','shared-groups','group-id','evidence-root','statement-root','capacity-file','root']:parser.add_argument('--'+name,required=True)
    parser.add_argument('--database-url');parser.add_argument('--codex-executable');parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();load_dotenv('.env')
    client=CodexSubscriptionClient(model='gpt-6.1-sol',reasoning_effort='medium',max_output_tokens=4000,executable=args.codex_executable)
    ports=c.Models(client,Path(args.root),capacity=ClaimModelCapacity(json.loads(Path(args.capacity_file).read_text())))
    result=run(PostgresKnowledgeStore(args.database_url),args.source_id,json.loads(Path(args.shared_groups).read_text()),args.group_id,
        args.evidence_root,args.statement_root,Path(args.root),ports,apply=args.apply)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
