"""Group argument completeness and typed pairs; consume statements and lower judgments."""
from copy import deepcopy
import fcntl
import json
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError
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


def expand_fragment_ids(source_id, supplied, fragment_ids):
    """Restore a dropped repeated source prefix only when it identifies one supplied fragment."""
    prefix = 'FR-' + source_id.removeprefix('SRC-') + '-'
    repeated = prefix + 'DK-' + source_id.rsplit('-', 1)[-1] + '-'
    aliases = {}
    for fragment_id in supplied:
        if fragment_id.startswith(repeated):
            aliases.setdefault(prefix + fragment_id[len(repeated):], []).append(fragment_id)
    return [
        aliases[fragment_id][0] if fragment_id not in supplied and len(aliases.get(fragment_id, [])) == 1 else fragment_id
        for fragment_id in fragment_ids
    ]


def active_root(root):
    """A new input correction keeps the original stopped verification immutable."""
    root=Path(root).resolve();path=root/'material-scope-continuation.json'
    if not path.exists():return root
    authority=json.loads(path.read_text());parent=json.loads((root/'verification.json').read_text())
    before=json.loads((root/'before.json').read_text())
    allowed_layers = {'source'} if authority.get('source_context_keys') else {'claim_connections'}
    if authority.get('statement_correction'):
        allowed_layers.add('source')
    if (authority.get('contract')!=CANDIDATE_SCOPE or not authority.get('owner_direction')
            or authority.get('parent_verification_sha256')!=e.digest(parent)
            or authority.get('parent_before_sha256')!=e.digest(before)
            or parent.get('version')!=VERSION or parent.get('status')!='needs_attention'
            or not parent.get('pending') or any(x.get('layer') not in allowed_layers for x in parent['pending'])
            or (root/'commit-intent.json').exists()):
        raise ValueError('argument material correction differs from its uncommitted parent')
    original_child = root/'material-scope-continuations'/e.digest(parent)
    followup = root/'route-context-continuation.json'
    if followup.exists():
        scoped = json.loads(followup.read_text())
        review = json.loads((original_child/'verification.json').read_text())
        pending = review['pending']
        if scoped.get('review_sha256') != e.digest(review) or not scoped.get('owner_direction') or not pending:
            raise ValueError('argument route context differs from the saved source clarification')
        if 'findings' in scoped:
            routes = {route['job_id']: route for route in review['routes']}
            expected = {finding['object_key'] for finding in parent['pending']}
            notes = scoped['findings']
            if (set(notes) != expected or {finding['object_key'] for finding in pending} != expected
                    or any(routes.get(key, {}).get('action') != 'needs_source'
                           or routes[key]['reason'] != note.get('previous_reason') or not note.get('reason')
                           for key, note in notes.items())):
                raise ValueError('argument route context differs from the pending source routes')
        elif len(pending) != 1:
            raise ValueError('argument route context differs from the saved source clarification')
        elif authority.get('statement_correction'):
            checked = json.loads((original_child/'statement-correction.json').read_text())
            if (pending[0]['object_key'] != authority['statement_correction']['object_key']
                    or pending[0]['layer'] != 'source' or scoped.get('previous_reason') != pending[0]['reason']
                    or checked['pending'] or checked['updates'] or not scoped.get('finding')):
                raise ValueError('argument clarification differs from its verified wording and pending source finding')
        elif not any(item.get('basis') == 'route_source_clarification'
                     and item.get('action') == 'needs_connection'
                     and item['object_key'] == pending[0]['object_key']
                     and item['reason'] == scoped.get('finding') for item in review['outcomes']):
            raise ValueError('argument route context differs from the saved source clarification')
        route_root = original_child/'route-context'/e.digest(scoped)
        final = root/'route-final-clarification.json'
        if final.exists():
            check = json.loads(final.read_text())
            reviewed = json.loads((route_root/'verification.json').read_text())
            pending_keys = {finding['object_key'] for finding in reviewed['pending']}
            completed = {route['job_id'] for route in reviewed['routes'] if route['action']=='complete'}
            if (check.get('review_sha256') != e.digest(reviewed) or not check.get('owner_direction')
                    or not pending_keys or pending_keys != set(check['source_context_keys'])
                    or not pending_keys <= completed):
                raise ValueError('final clarification differs from the completed routes with pending support')
            return route_root/'final-clarification'/e.digest(check)
        return route_root
    return original_child


CONTINUATION_PROMPT='本次只補判remaining_findings；accepted_proposed_edges已處置，保留並只補缺口。不重做其他Claim的判斷。'
STATEMENT_HANDOFF_PROMPT=('逐項處理remaining_findings。candidate_evidence_keys是Jev找到的候選，'
                         '用原文及現有路線確認關係，候選不足時可從其他已提供材料補足。')

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
        request_path = Path(authority['final_support_request_path']).resolve()
        if not request_path.is_relative_to(parent_root / 'postimage-disposition'):
            raise ValueError('scripture clarification must use the parent final-support request')
        request = json.loads(request_path.read_text())
        response = json.loads((request_path.parent / 'response.json').read_text())
        if (request_path.parent.name != e.digest(request)
                or request['input']['task'] != 'argument_final_support'
                or request['identity'] != ports.identity['gpt']
                or e.digest(response) != authority['final_support_response_sha256']):
            raise ValueError('scripture clarification differs from the recorded finding')
        jobs = {job['job_id']: job for job in request['input']['jobs']}
        prior = {item['job_id']: item for item in response['items']}
        if authority.get('apply_saved_scripture_removals'):
            result = deepcopy(parent)
            for finding in parent['pending']:
                key = finding['object_key']
                item = prior.get(key, {})
                payload = result['updates'].get(key, records[key]['payload'])
                if (key not in before['records'] or not key.startswith('claims/')
                        or payload.get('review_status') in f.PROTECTED
                        or jobs.get(key, {}).get('findings') != {'scripture': 'mismatched'}
                        or item.get('action') != 'repair' or item.get('scripture_refs') != []
                        or item.get('statement') is not None or item.get('reason') != finding['reason']):
                    raise ValueError('saved Scripture removal differs from the isolated final-support finding')
                result['updates'][key] = flash.current_claim_payload({**payload, 'scripture_refs': []})
            result.update(status='verified', pending=[])
            e.save(root/'before.json', before)
            e.save(root/'intent.json', json.loads((parent_root/'intent.json').read_text()))
            e.save(root/'verification.json', result)
            return statements.finish(store, root) if apply else e.publish(root, result)
        candidates = []
        for finding in parent['pending']:
            key = finding['object_key']
            if (key not in jobs or key not in prior or jobs[key]['findings'] != {'scripture': 'missing'}
                    or prior[key]['action'] != 'needs_source' or prior[key]['reason'] != finding['reason']):
                raise ValueError('scripture clarification must address only recorded scripture findings')
            candidates.append({**jobs[key], 'previous_reason': prior[key]['reason']})
        result = deepcopy(parent)
        accepted = set()
        for batch, reply in f.gpt(root / 'scripture', ports, 'argument_final_support', candidates,
                                  statements.Edits.model_json_schema(), FINAL_SUPPORT_BATCH_PROMPT + SCRIPTURE_CLARIFICATION,
                                  semantic_review=False):
            edits = statements.Edits.model_validate(reply).items
            f.exactly([edit.model_dump() for edit in edits], [job['job_id'] for job in batch], 'job_id')
            for edit in edits:
                if edit.action == 'keep' and edit.statement is None and edit.scripture_refs is None:
                    accepted.add(edit.job_id)
                result['outcomes'].append({'object_key': edit.job_id, 'basis': 'scripture_clarification',
                                           'action': edit.action, 'reason': edit.reason})
        result['pending'] = [finding for finding in parent['pending'] if finding['object_key'] not in accepted]
        if not result['pending']:
            result['status'] = 'verified'
        e.save(root / 'before.json', before)
        e.save(root / 'intent.json', json.loads((parent_root / 'intent.json').read_text()))
        e.save(root / 'verification.json', result)
        return statements.finish(store, root) if apply else e.publish(root, result)


def clarify_route_source(store, source_id, group, parent_root, root, ports, authority, *, apply):
    """Recheck only a stopped route using identified, bound source context."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (root / 'verification.json').exists():
            return statements.finish(store, root) if apply else json.loads((root / 'verification.json').read_text())
        before = json.loads((parent_root / 'before.json').read_text())
        result = json.loads((parent_root / 'verification.json').read_text())
        records, sources = f.load_group_source(store, source_id, group)
        if before['sources'] != sources or any(records.get(k) != v for k, v in before['records'].items()):
            raise ValueError('source clarification parent input changed')
        bindings = authority['source_context_keys']
        pending = {item['object_key']: item for item in result['pending']}
        if set(bindings) != set(pending):
            raise ValueError('source clarification scope differs from pending routes')
        jobs = []
        working = f.apply_overlay(records, result['updates'])
        for key, context in bindings.items():
            if not context or any(not k.startswith('source_fragments/') for k in context):
                raise ValueError('source clarification requires bound fragments')
            fragments = []
            for fragment_key in context:
                row = records[fragment_key]
                fragment = row['payload']
                if row.get('retired') or fragment.get('source_id') != source_id:
                    raise ValueError('source clarification fragment differs from current source')
                binding = {'source_fragment_id': fragment_key.split('/', 1)[1],
                           'evidence_step_id': key, 'statement': ''}
                fragments.extend(e.material(binding, records, sources)['fragments'])
                before['records'][fragment_key] = row
            jobs.append({'job_id': key, 'previous_reason': pending[key]['reason'],
                         'material': flash.claim_material(key, working, sources, source_id),
                         'source_context': fragments})
        prompt = FINAL_SUPPORT_BATCH_PROMPT + (
            '本次只復核先前論證路線的原文疑點。source_context是同講道的唯讀片段。'
            '區分教授用來論證本主張的承重經文、已由間接路線承載的前提、以及別處同主題的背景。'
            '只在既有路線已完整且無須新增經文或連接時選keep。若需補直接論證或來源，說明實際缺項。'
        )
        accepted = {}
        for batch, reply in f.gpt(root / 'source-context', ports, 'argument_final_support', jobs,
                                  statements.Edits.model_json_schema(), prompt, semantic_review=False):
            edits = statements.Edits.model_validate(reply).items
            f.exactly([edit.model_dump() for edit in edits], [job['job_id'] for job in batch], 'job_id')
            for edit in edits:
                if edit.action == 'keep' and edit.statement is None and edit.scripture_refs is None:
                    accepted[edit.job_id] = edit.reason
                result['outcomes'].append({'object_key': edit.job_id, 'basis': 'route_source_clarification',
                                           'action': edit.action, 'reason': edit.reason})
        for route in result['routes']:
            if route['job_id'] in accepted:
                route.update(action='complete', reason=accepted[route['job_id']])
        result['pending'] = [item for key, item in pending.items() if key not in accepted]
        if not result['pending']:
            result['status'] = 'verified'
        e.save(root / 'before.json', before)
        e.save(root / 'intent.json', json.loads((parent_root / 'intent.json').read_text()))
        e.save(root / 'verification.json', result)
        return statements.finish(store, root) if apply else e.publish(root, result)


def bounded_jev(root, ports, material, questions):
    request = {'model': e.MODEL, 'state': material, 'questions': questions}
    if len(json.dumps(request, ensure_ascii=False).encode()) > f.JEV_BYTES:
        return None, 'not_run_input_limit'
    try:
        answers = f.jev(root, ports, material, questions)
    except (HTTPError, e.RecordedCallRejected):
        folder = Path(root)/'jev'/e.digest(request)
        refusal = e.rejection_receipt(folder, request)
        if not refusal or refusal['http_status'] != 400:
            raise
        try:
            detail = json.loads((folder/'rejection-body.bin').read_text()).get('detail')
        except (OSError, ValueError):
            raise ValueError('invalid Jev rejection receipt') from None
        if not isinstance(detail, dict) or detail.get('error_type') != 'max_tokens_exceeded':
            raise
        return None, 'not_run_provider_token_limit'
    return answers, None


def connection_candidate(root, ports, records, claim_key, finding, pool):
    """Find a candidate for one stated gap. The route still decides what to link."""
    claim=records[claim_key]['payload']
    attached={'evidence_steps/'+eid for eid in claim.get('evidence_step_ids',[])}
    candidates=sorted(k for k in pool-attached if k.startswith('evidence_steps/'))
    if not candidates or len(candidates)>254:
        return None
    choices={f'E{i}':key for i,key in enumerate(candidates)}
    material={'claim':claim['statement'],'missing_premise':finding['reason'],
              'evidence':{label:records[key]['payload']['statement'] for label,key in choices.items()}}
    question=q.choice('哪筆已核驗的 EvidenceStep 最直接承載所缺的論證前提？'
        '依實質命題配對，經文或主題相同不足以配對。沒有則選none。',
        {**{label:label for label in choices},'none':'沒有足夠符合的候選。'})
    answers,_=bounded_jev(root,ports,material,{'candidate':question})
    return choices.get(answers['candidate']) if answers else None


def support_questions(material):
    options = q.claim(with_references=bool(material.get('claim_references')))
    return {key: options[key] for key in ('support', 'scripture')}


def has_combined_support_request(root, material):
    request = {'model': e.MODEL, 'state': material, 'questions': support_questions(material)}
    saved = Path(root)/'jev'/e.digest(request)
    return (saved/'started.json').exists() or (saved/'response.json').exists()


def argument_support_issues(root, ports, material, *, scripture_verified=False):
    questions = support_questions(material)
    if scripture_verified:
        questions.pop('scripture')
    answers,unavailable=bounded_jev(root,ports,material,questions)
    if unavailable:
        return {key:unavailable for key in questions}
    return {key: value for key, value in answers.items()
            if value != ('supported' if key=='support' else 'complete')}


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
    ordinary = [job for job in remaining if not job.get('reuse_scripture_check')]
    support_only = [job for job in remaining if job.get('reuse_scripture_check')]
    legacy_batch = any(
        json.loads(path.read_text()).get('system') == FINAL_SUPPORT_BATCH_PROMPT
        for path in (root/'gpt').glob('*/request.json')
    )
    instruction = FINAL_SUPPORT_BATCH_PROMPT
    if not legacy_batch:
        instruction += statements.SCRIPTURE_SOURCE_RULE
        instruction += '講道原話（包括禱告）已支持主張且無可定位的經文引述時，選keep。'
    consume(f.gpt(root,ports,'argument_final_support',ordinary,schema,instruction,semantic_review=False))
    if support_only:
        instruction = ('只核對最終論據集合是否完整支持Claim。經文出處已核對，沿用該判定。'
                       'keep需有據；缺內容needs_source，缺連接needs_connection。不得修改節點或再建邊。'
                       '每個job_id獨立判定且恰好一次，只用該job材料。')
        consume(f.gpt(root,ports,'argument_final_support',support_only,schema,instruction,semantic_review=False))
    return [answers[j['job_id']] for j in jobs]


def added_source_context(before, current):
    """Allow additive, current source facts on the exact same transcript."""
    fields = {'human_source_guard', 'human_source_decisions'}
    if ({sid: {k:v for k,v in src.items() if k not in fields} for sid,src in before.items()}
            != {sid: {k:v for k,v in src.items() if k not in fields} for sid,src in current.items()}):
        raise ValueError('statement source differs')
    for sid, previous in before.items():
        now = current[sid]
        old_guard = previous.get('human_source_guard', {})
        new_guard = now.get('human_source_guard', {})
        old_keys = set(old_guard.get('paragraphs', {}).get(sid, []))
        new_keys = set(new_guard.get('paragraphs', {}).get(sid, []))
        old_entries = old_guard.get('entries', [])
        new_entries = new_guard.get('entries', [])
        if (not old_keys <= new_keys or any(entry not in new_entries for entry in old_entries)
                or any(fact not in now.get('human_source_decisions', [])
                       for fact in previous.get('human_source_decisions', []))
                or any(fact.get('applicability') != 'current' for fact in now.get('human_source_decisions', []))):
            raise ValueError('statement source clarification changed')


def changed_statement_context(ids, records, before, sources, source_id):
    if before['sources'] == sources:
        return []
    added_source_context(before['sources'], sources)
    return [key for cid in ids if
            flash.claim_material(key := 'claims/'+cid, records, before['sources'], source_id)
            != flash.claim_material(key, records, sources, source_id)]


def statement_proof(root,claim_ids,records,sources):
    root=Path(root).resolve();result=json.loads((root/'verification.json').read_text());before=json.loads((root/'before.json').read_text())
    if (root/'result.json').exists():
        receipt=json.loads((root/'result.json').read_text())
        if receipt.get('argument_handoffs') and (
                receipt.get('statement_verification_sha256')!=e.digest(result)
                or receipt['argument_handoffs']!=result['pending']):
            raise ValueError('Claim statement handoff differs from its saved verification')
    if (result.get('version')!=statements.VERSION or result['status'] not in {'verified','needs_attention'}
            or result.get('completion_scope')!='claim_statement_tasks_only' or not set(claim_ids)<=set(result['claim_ids'])):
        raise ValueError('completed Claim statement tasks required')
    if before['sources'] != sources:
        added_source_context(before['sources'], sources)
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
        if (root/'route-final-clarification.json').exists():
            child = active_root(root)
            group = next(g for g in plan['groups'] if g['group_id'] == group_id)
            final = json.loads((root/'route-final-clarification.json').read_text())
            result = clarify_route_source(store, source_id, group, child.parent.parent, child, ports, final, apply=apply)
        elif authority.get('source_context_keys') and not (root/'route-context-continuation.json').exists():
            group = next(g for g in plan['groups'] if g['group_id'] == group_id)
            result = clarify_route_source(store, source_id, group, root, active_root(root), ports, authority, apply=apply)
        elif authority.get('final_support_request_path'):
            group = next(g for g in plan['groups'] if g['group_id'] == group_id)
            result = clarify_final_scripture(store, source_id, group, root, active_root(root), ports, authority, apply=apply)
        else:
            result=run(store,source_id,plan,group_id,evidence_root,statement_root,active_root(root),ports,
                apply=apply,claim_ids=claim_ids,_parent_root=root)
        return e.publish(root,result)
    parent=None
    route_context = None
    if _parent_root is not None:
        if active_root(_parent_root)!=root:raise ValueError('argument correction root differs')
        parent=json.loads((Path(_parent_root)/'verification.json').read_text())
        route_path = Path(_parent_root)/'route-context-continuation.json'
        if route_path.exists():
            route_context = json.loads(route_path.read_text())
            if 'findings' in route_context:
                for finding in parent['pending']:
                    finding['reason'] = route_context['findings'][finding['object_key']]['reason']
            else:
                parent['pending'][0]['reason'] = route_context['finding']
    group=next(g for g in plan['groups'] if g['group_id']==group_id);ids=group['claim_ids']
    if claim_ids is not None:
        if claim_ids not in group.get('sequential_claim_batches', []):
            raise ValueError('Claim batch is not frozen in the completed Evidence plan')
        ids=claim_ids
    retained = Path(statement_root)/'retained-scope.json'
    if retained.exists():
        scope = json.loads(retained.read_text())
        verified = json.loads((Path(statement_root)/'verification.json').read_text())
        if scope['original_ids'] != ids or scope['retained_sha256'] != e.digest(verified):
            raise ValueError('retained Claim scope differs from the frozen statement batch')
        ids = verified['claim_ids']
    if not ids or len(ids)>10 or len(ids)!=len(set(ids)):raise ValueError('one nonempty existing Claim group required')
    with (root/'run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);pin=f.bind_source(store,source_id,root)
        saved=json.loads((root/'intent.json').read_text()) if (root/'intent.json').exists() else None
        expanded=not saved or saved.get('candidate_scope')==CANDIDATE_SCOPE
        e.save(root/'intent.json',{'version':VERSION,'source':pin,'group':group,'models':ports.identity,
            **({'claim_ids':ids} if claim_ids is not None else {}),
            **({'retained_claim_ids':ids} if retained.exists() else {}),
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
        refreshed = {}
        recheck_reads = set()
        changed = changed_statement_context(ids, records, old_before, sources, source_id)
        if changed:
            recheck = root/'statement-source-recheck'
            intent_path = recheck/'intent.json'
            if not intent_path.exists() and any(root.rglob('started.json')):
                raise ValueError('statement source clarification changes an already-started argument request')
            materials = {key: flash.claim_material(key, records, sources, source_id) for key in changed}
            e.save(intent_path, {'statement_sha256':e.digest(old_checks),
                'material_sha256':e.digest(materials)})
            needed = set()
            for material in materials.values():
                needed.update(node['object_key'] for node in material['evidence'])
                needed.update(node['object_key'] for node in material.get('argument_context', {}).get('records', []))
                context = material.get('argument_context', {})
                recheck_reads.update(row['object_key'] for row in context.get('relations', []))
                for ref in material.get('claim_references', []):
                    needed.update(ref['supporting_material_keys'])
                    recheck_reads.add(ref['object_key'])
                    recheck_reads.update(ref['supporting_relation_keys'])
                    recheck_reads.update(row['object_key'] for row in ref['relations'])
                    recheck_reads.update('source_fragments/'+fid for fid in ref['source_fragment_ids'])
            recheck_reads.update(needed)
            recheck_reads.update('source_fragments/'+fid for key in needed
                                 for fid in e.evidence_fragment_ids(records[key]['payload']))
            statements.verify_evidence(evidence_root, needed, original, sources)
            checked = statements.review(records, sources, changed, source_id, recheck, ports)
            e.save(recheck/'review.json', checked)
            source_pending = [item for item in checked['pending'] if item['layer'] != 'claim_connections']
            if source_pending:
                return e.publish(root, {'version':VERSION,'source_id':source_id,'claim_ids':ids,
                    'status':'needs_attention','updates':{},'pending':source_pending,'applied':False})
            old_checks = {**old_checks}
            for field in ('pending', 'outcomes'):
                old_checks[field] = [item for item in old_checks[field] if item['object_key'] not in changed] + checked[field]
            statement_updates.update(checked['updates'])
            records = f.apply_overlay(records, checked['updates'])
            refreshed = {key: flash.claim_material(key, records, sources, source_id) for key in changed}
        previous_records = records
        if parent is not None:
            authority = json.loads((Path(_parent_root) / 'material-scope-continuation.json').read_text())
            correction = authority.get('statement_correction')
            if correction:
                finding, = parent['pending']
                key = correction['object_key']
                payload = records[key]['payload']
                if (key != finding['object_key'] or key not in {'claims/'+cid for cid in ids}
                        or e.digest(payload) != correction['before_sha256']
                        or payload.get('review_status') in f.PROTECTED
                        or not correction['statement'].strip() or correction['statement'] == payload['statement']):
                    raise ValueError('Claim statement correction differs from pending argument')
                proposed = flash.current_claim_payload({**payload, 'statement': correction['statement']})
                projected = f.apply_overlay(records, {key: proposed})
                material = flash.claim_material(key, projected, sources, source_id)
                statements.verify_evidence(evidence_root, {row['object_key'] for row in material['evidence']}, records, sources)
                tasks = None
                if correction.get('retain_scripture_refs'):
                    if proposed['scripture_refs'] != payload['scripture_refs']:
                        raise ValueError('Claim wording correction changed retained Scripture references')
                    tasks = ['statement_fidelity', 'support']
                review_root = root/('statement-correction-support' if tasks else 'statement-correction')
                checked = statements.review(projected, sources, [key], source_id, review_root, ports, tasks=tasks)
                e.save(review_root.with_suffix('.json'), checked)
                if checked['pending'] or checked['updates']:
                    raise ValueError('Proposed Claim statement needs further disposition')
                statement_updates[key] = proposed
                records = projected
                if finding['layer'] == 'source':
                    parent['pending'] = [{**finding, 'layer': 'claim_connections',
                        'reason': 'The corrected Claim wording passed statement review. Verify its argument against the supplied source.'}]
        targets={'claims/'+cid for cid in ids}
        pool=f.active_group_lower_keys(group,records)
        for key in targets:pool.update(x['object_key'] for x in flash.claim_material(key,records,sources,source_id)['evidence'])
        candidates=material_candidates(pool,records,set(group['paragraph_keys'])|material_paragraphs(pool,records),source_id) if expanded else pool
        extra = set()
        if route_context:
            extra = set(route_context['record_keys'])
            if not extra or any(k not in records or records[k].get('retired')
                    or not k.startswith(('evidence_steps/', 'observations/'))
                    or not e.evidence_fragment_ids(records[k]['payload'])
                    or any(records.get('source_fragments/'+fid, {}).get('payload', {}).get('source_id') != source_id
                           for fid in e.evidence_fragment_ids(records[k]['payload'])) for k in extra):
                raise ValueError('argument route context requires current source-local lower records')
            candidates.update(extra)
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
        completion_pool.update(extra)
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
        if parent is None and old_checks['pending']:
            for job in jobs:
                key=job['job_id']
                findings=[item for item in old_checks['pending'] if item['object_key']==key]
                if not findings:
                    continue
                job['remaining_findings']=findings
                hints=[connection_candidate(root/'connection-lookup',ports,records,key,item,completion_pool)
                       for item in findings]
                job['candidate_evidence_keys']=list(dict.fromkeys(hint for hint in hints if hint))
            prompt+=STATEMENT_HANDOFF_PROMPT
        if parent is not None:
            shared['accepted_proposed_edges']=[p for item in parent['routes'] for p in item['pairs'] if {p['a'],p['b']}<=completion_nodes]
            prompt+=CONTINUATION_PROMPT
            if route_context:
                prompt += ('只處理補充的已核實前段材料與指定主張之間的論證缺口。'
                           '若前段只是背景，保留既有路線；已有間接承載，不重複加邊。'
                           '區分教授的前段解釋、結尾禱告與任何超出原文的推論。')
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
            for index, pair in enumerate(item.pairs):
                provided=completion_nodes if item.job_id in remaining and e.digest(pair.model_dump()) not in retained_pairs else nodes
                fragment_ids = expand_fragment_ids(source_id, fragments, pair.fragment_ids)
                if fragment_ids != pair.fragment_ids:
                    e.save(root / 'fragment-id-corrections' / (e.digest(pair.model_dump()) + '.json'),
                           {'before': pair.fragment_ids, 'after': fragment_ids})
                    pair = pair.model_copy(update={'fragment_ids': fragment_ids})
                    value['pairs'][index] = pair.model_dump()
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
        previous=f.apply_overlay(previous_records,parent['updates']) if parent is not None else None
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
            prior = refreshed.get(key, prior)
            state=flash.claim_material(key,working,sources,source_id)
            if previous is not None and state==flash.claim_material(key,previous,sources,source_id):
                unresolved=[p for p in parent['pending'] if p['object_key']==key]
                pending.extend(deepcopy(unresolved))
                outcomes.append({'object_key':key,'basis':'retained_parent_unresolved' if unresolved else 'reused_parent_final_support_and_scripture'})
                continue
            if (state==prior and not any(x['object_key']==key for x in old_checks['pending'])
                    and not (parent is not None and key in remaining)):
                outcomes.append({'object_key':key,'basis':'reused_statement_support_and_scripture'});continue
            support_root = root/'postimage-support'
            reuse_scripture = (statements.scripture_verified(old_checks['outcomes'], key, state)
                               and not has_combined_support_request(support_root, state))
            issues=argument_support_issues(support_root,ports,state,scripture_verified=reuse_scripture)
            if issues:
                final_jobs.append({'job_id':key,'material':state,'findings':issues,
                    **({'reuse_scripture_check':True} if reuse_scripture and 'scripture' not in issues else {})})
        for edit in final_support_decisions(root/'postimage-disposition',ports,final_jobs):
            if edit.action!='keep':pending.append({'object_key':edit.job_id,'layer':'claim_connections','reason':edit.reason})
            outcomes.append({'object_key':edit.job_id,'basis':'gpt_final_support','action':edit.action,'reason':edit.reason})
        read=set(nodes)|set(edges)|set(claim_edges)|{'source_fragments/'+fid for key in candidates for fid in e.evidence_fragment_ids(records[key]['payload'])}|{'source_documents/'+source_id}
        read.update(k for k,r in records.items() if k.startswith('claim_relation_constraints/') and {r['payload']['source_id'],r['payload']['target_id']}&set(by_id))
        if changed:
            read.update(old_before['records'].keys() | recheck_reads)
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
