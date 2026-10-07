"""Exact owner-bounded statement revision, with fresh single-Opus authority.

This is not source reconciliation or a Claim generator. Sources, evidence,
membership, existing link predicates/locators and all dependents are retained.
An applied revision is eligible for its pending full-group audit, not complete.
"""
from copy import deepcopy
import json
from pathlib import Path

from backend.api.canonical_repository.viewpoint_foundation import sha256_json, semantic_record_sha
from backend.api.canonical_repository.viewpoint_production_safety import file_sha256
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, stable_decided_at, subscription_client, call_model
from backend.pipeline.viewpoint_validation_context import validated_history, current_check, read_current_objects
from backend.pipeline.viewpoint_authorized_source_repair_357 import fingerprint, Review
from backend.pipeline.viewpoint_evidence_source_context import load_context_source

VERSION = 'wang_owner_claim_statement_revision_v1'
REVIEWER = 'owner_claim_statement_opus_terminal_review'
HUMAN_HOLDS = {'human_review_required', 'human_hold', 'withheld_unreviewed'}
PROMPT = '''独立终局审核一个负责人授权的精确Claim句子修订及所有现有CVP成员连接后像。不是生成新Claim，不评价神学对错。
只用完整自身原文、逐字Evidence/fragment、当前Claim及CVP和完整反向依赖；其他Claim不是本Claim的来源证据。owner授权不是内容批准，旧审核不是新版本批准。
检查新完整句子是否忠于自身语境，是否改变了归属、必要/充分、强度、时点或限定；指出任何未授权或证据不足处。不能为了续跑而批准。
现有连接仅更新Claim版本及SHA和fresh identity provenance，原predicate、目标、locator文字/坐标、evidence bindings保持不变。逐条核查新版本下关系仍成立；需调整定位、证据、关系或其他consumer内容则拒绝，不偷偷扩大权限。
所有Evidence/source/CVP正文/其他Claim和原历史版本原样保留。已有human hold不能被本审核解除。candidate依赖仍未批准，不把它们作为论据；如修订使保留consumer不安全则拒绝。
新after_claim的independent_terminal_reviewed只会在本次完整通过后写入，fresh candidate/decision仅诚实记录单次Opus，不伪造双审或人工批准。
恰好返回给定Claim的一个verdict；完整句子、每条连接及所有保留consumer都可安全实施才approved=true且problems=[]。'''


def binding(path):
    path = Path(path).resolve()
    return {'path': str(path), 'file_sha256': file_sha256(path)}


def verify_file(bound):
    if set(bound) != {'path', 'file_sha256'} or binding(bound['path']) != bound:
        raise ValueError('statement revision physical artifact drift')
    return Path(bound['path'])


def records(rows):
    result = {(r['collection'], r['object_id']): r['payload'] for r in rows}
    if len(result) != len(rows):
        raise ValueError('duplicate statement revision context')
    return result


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def derive_claim(old, authorization, root):
    if (set(authorization) != {'claim_id', 'before_revision', 'before_sha256', 'old_text', 'new_text',
                               'start_char', 'authorization_quote', 'authorization_id', 'accepted_membership'}
            or not authorization['authorization_quote'] or not authorization['authorization_id']
            or old['claim_id'] != authorization['claim_id'] or old['revision'] != authorization['before_revision']
            or semantic_record_sha(old) != authorization['before_sha256']
            or old.get('review_status') in HUMAN_HOLDS):
        raise ValueError('statement revision owner scope/version/human hold drift')
    start = authorization['start_char']
    text, replacement = authorization['old_text'], authorization['new_text']
    if not isinstance(start, int) or start < 0 or not text or not replacement or text == replacement or old['statement'][start:start+len(text)] != text:
        raise ValueError('statement revision exact edit range mismatch')
    result = deepcopy(old)
    result.update(statement=old['statement'][:start]+replacement+old['statement'][start+len(text):],
        revision=old['revision']+1, review_status='independent_terminal_reviewed',
        reviewed_by='claude-opus-5-5', reviewed_at=stable_decided_at(Path(root)),
        review_note=f'Exact owner-bounded statement revision; fresh single Opus terminal review: {Path(root)/"review-report.json"}. Historical review and all evidence retained; pending full-group audit.')
    return result


def derive_links(old_links, claim):
    result = []
    for old in old_links:
        if old['claim_id'] != claim['claim_id'] or old['effective_state'] != 'active':
            raise ValueError('foreign or inactive dependent link')
        new = deepcopy(old)
        new.update(revision=old['revision']+1, pinned_claim_revision=claim['revision'])
        locator = new.get('component_locator')
        if locator:
            for span in locator['canonical_spans']:
                if claim['statement'][span['start_char']:span['end_char']] != span['exact_text']:
                    raise ValueError('statement revision changes an existing component; separate locator authority required')
            locator['claim_sha256'] = semantic_record_sha(claim)
        result.append(new)
    return result


def source_context(states, claim):
    from backend.api.canonical_repository.knowledge_models import evidence_fragment_ids
    evidence, fragments, sources = [], [], {}
    for eid in claim['evidence_step_ids']:
        step = states['evidence_steps', eid]
        if claim['claim_id'] not in step['produced_claim_ids'] or step.get('valid_for_identity_review') is False or step.get('review_status') in HUMAN_HOLDS:
            raise ValueError('statement revision evidence reciprocity/eligibility hold')
        evidence.append(step)
        for fid in evidence_fragment_ids(step):
            fragment = states['source_fragments', fid]
            source = states['source_documents', fragment['source_id']]
            if fragment['source_id'] not in {o['source_id'] for o in claim['occurrences']}:
                raise ValueError('statement revision borrowed foreign source')
            if fragment['source_id'] not in sources:
                path, physical, projection = load_context_source(source)
                sources[fragment['source_id']] = {'source': source, 'binding': binding(path),
                    'body_sha256': projection.body_sha256, 'complete_original': projection.spoken_rows}
            rows = sources[fragment['source_id']]['complete_original']
            key = fragment['paragraph_key']
            index = int(key[1:])-1
            if (not key.startswith('S') or not 0 <= index < len(rows)
                    or not fragment.get('verbatim_excerpt') or fragment['verbatim_excerpt'] not in rows[index]['text']):
                raise ValueError('statement revision evidence not exact in own paragraph')
            import hashlib
            if (fragment['source_sha256'] != source['source_body_sha256']
                    or fragment.get('verbatim_excerpt_sha256') != hashlib.sha256(fragment['verbatim_excerpt'].encode()).hexdigest()
                    or fragment.get('paragraph_text_sha256') != hashlib.sha256(rows[index]['text'].encode()).hexdigest()):
                raise ValueError('statement revision fragment/source/paragraph SHA drift')
            fragments.append(fragment)
    return {'evidence': evidence, 'fragments': sorted({f['fragment_id']: f for f in fragments}.values(),key=lambda f:f['fragment_id']),
            'sources': [sources[k] for k in sorted(sources)]}


def prepare(states, authorization, root):
    cid = authorization['claim_id']
    old = states['claims', cid]
    claim = derive_claim(old, authorization, root)
    links = sorted([p for (c, _), p in states.items() if c == 'viewpoint_claim_links'
                    and p.get('claim_id') == cid and p.get('effective_state') == 'active'],key=lambda p:p['viewpoint_claim_link_id'])
    if any(p['pinned_claim_revision'] != old['revision'] for p in links):
        raise ValueError('dependent link predecessor version mismatch')
    refs = [{'collection':c,'object_id':o,'payload':p} for (c,o),p in sorted(states.items())
            if cid in set(strings(p)) and (c,o) != ('claims',cid)]
    allowed = {'viewpoint_claim_links','viewpoint_identity_candidates','viewpoint_identity_decisions','evidence_steps','claim_relations','questions'}
    if any(r['collection'] not in allowed or (r['collection'] in {'claim_relations','questions'}
            and r['payload'].get('review_status') != 'candidate') for r in refs):
        raise ValueError('statement revision has unplanned approved/pinned dependents')
    context_keys = {('claims',cid)} | {(r['collection'],r['object_id']) for r in refs}
    own = source_context(states,old)
    context_keys.update(('evidence_steps',p['evidence_step_id']) for p in own['evidence'])
    context_keys.update(('source_fragments',p['fragment_id']) for p in own['fragments'])
    context_keys.update(('source_documents',s['source']['source_id']) for s in own['sources'])
    for link in links:
        context_keys.update({('canonical_viewpoints',link['viewpoint_id']),('viewpoint_revisions',link['validated_against_viewpoint_revision_id']),
                             ('viewpoint_identity_decisions',link['decision_id'])})
        decision = states['viewpoint_identity_decisions',link['decision_id']]
        context_keys.add(('viewpoint_identity_candidates',decision['identity_candidate_id']))
        for (c,o),p in states.items():
            if c == 'viewpoint_claim_links' and p['viewpoint_id'] == link['viewpoint_id'] and p.get('effective_state') == 'active':
                context_keys.update({(c,o),('claims',p['claim_id'])})
    accepted = json.loads(verify_file(authorization['accepted_membership']).read_text())
    predecessors = [r for r in accepted['claim_packets'] if r['claim_id'] == cid]
    if len(predecessors) != 1 or predecessors[0]['claim_revision'] != old['revision'] or predecessors[0]['claim_content_sha256'] != semantic_record_sha(old):
        raise ValueError('statement revision differs from accepted historical membership')
    packet = {'schema_version':VERSION,'run_root':str(Path(root).resolve()),'authorization':authorization,
        'before_claim':old,'after_claim':claim,'before_links':links,'after_links':derive_links(links,claim),
        'reverse_consumers':refs,'context_records':[{'collection':c,'object_id':o,'payload':states[c,o]} for c,o in sorted(context_keys)],
        'own_source_context':own,'collections':sorted({c for c,o in states}),
        'pre_fingerprint_sha256':fingerprint(states,sorted({c for c,o in states})),
        'fresh_review_provenance':'pending_single_Opus_not_inherited_approval',
        'full_group_independent_audit_complete':False}
    validate_packet(packet)
    return packet


def validate_packet(packet):
    states = records(packet['context_records'])
    old, new = packet['before_claim'], packet['after_claim']
    if states.get(('claims',old['claim_id'])) != old or new != derive_claim(old,packet['authorization'],packet['run_root']):
        raise ValueError('statement revision Claim postimage drift')
    if packet['after_links'] != derive_links(packet['before_links'],new):
        raise ValueError('statement revision dependency postimage drift')
    current = sorted([p for (c,o),p in states.items() if c=='viewpoint_claim_links' and p['claim_id']==old['claim_id']
                     and p.get('effective_state')=='active'],key=lambda p:p['viewpoint_claim_link_id'])
    if current != packet['before_links']:
        raise ValueError('statement revision dependent population drift')
    if source_context(states,old) != packet['own_source_context']:
        raise ValueError('statement revision physical source/context drift')
    accepted = json.loads(verify_file(packet['authorization']['accepted_membership']).read_text())
    pins = [r for r in accepted['claim_packets'] if r['claim_id']==old['claim_id']]
    if len(pins)!=1 or (pins[0]['claim_revision'],pins[0]['claim_content_sha256'])!=(old['revision'],semantic_record_sha(old)):
        raise ValueError('statement revision accepted membership drift')
    actual = [{'collection':c,'object_id':o,'payload':p} for (c,o),p in sorted(states.items())
              if old['claim_id'] in set(strings(p)) and (c,o)!=('claims',old['claim_id'])]
    if actual != packet['reverse_consumers']:
        raise ValueError('statement revision frozen reverse consumer drift')
    allowed={'viewpoint_claim_links','viewpoint_identity_candidates','viewpoint_identity_decisions','evidence_steps','claim_relations','questions'}
    if any(r['collection'] not in allowed or (r['collection'] in {'claim_relations','questions'}
            and r['payload'].get('review_status')!='candidate') for r in actual):
        raise ValueError('statement revision cannot clear approved consumer or human hold')


def adapter(client):
    from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
    result = StructuredJsonReviewerAdapter(client=client,prompt=PROMPT,response_model=Review,
        schema_name=VERSION,compact_payload=True,shared_text_payload=True,shared_record_payload=True)
    result.max_request_bytes = 500000
    return result


@validated_history
def reviewed_inputs(root):
    root = Path(root)
    packet = read_artifact(root/'packet.json')
    if packet['run_root'] != str(root.resolve()):
        raise ValueError('statement review detached from frozen run')
    validate_packet(packet)
    reservation = read_artifact(root/'review-reservation.json')
    grant=read_artifact(root/'review-budget-proof.json')
    marker=read_artifact(verify_file(grant['reservation']))
    if marker!={**reservation,'root':str(root)} or grant['authorization_id']!=packet['authorization']['authorization_id']:
        raise ValueError('cross-root statement review budget proof drift')
    reader = adapter(subscription_client('claude','claude-opus-5-5','high'))
    reader.generate = lambda _: (_ for _ in ()).throw(ValueError('saved statement review cannot be replayed'))
    response,calls,_ = call_model(reader,packet,root/'raw-opus.json')
    raw = read_artifact(root/'raw-opus.json')
    report = read_artifact(root/'review-report.json')
    expected = {'policy':VERSION,'packet_sha256':sha256_json(packet),'prompt_sha256':reader.prompt_sha256,
                'config_sha256':reader.generation_config_sha256,'model':reader.model_id,'backend':reader.backend,
                'request_bytes':reader.request_bytes(packet),'authorization_id':packet['authorization']['authorization_id'],
                'GPT_new_calls':0,'calls_allowed':1}
    verdicts = Review.model_validate(response).verdicts
    if (reservation != expected or calls or raw['model_id']!='claude-opus-5-5' or raw['backend']!='claude_subscription'
            or len(verdicts)!=1 or verdicts[0].claim_id!=packet['before_claim']['claim_id'] or not verdicts[0].approved or verdicts[0].problems
            or report != {'status':'Opus_approved_not_applied','packet_sha256':sha256_json(packet),'review':response,
                          'raw_binding':binding(root/'raw-opus.json'),'calls_executed':1}):
        raise ValueError('fresh exact single-Opus statement review missing/refused/drifted')
    return packet,raw


def package_for(packet,raw):
    from backend.api.canonical_repository.knowledge_models import ClaimRecord, ViewpointClaimLinkRecord, ViewpointIdentityCandidateRecord, ViewpointIdentityDecisionRecord
    states = records(packet['context_records']); claim=deepcopy(packet['after_claim'])
    ClaimRecord.model_validate(claim)
    links, candidates, decisions = [], [], []
    for cv in sorted({l['viewpoint_id'] for l in packet['after_links']}):
        selected = [l for l in packet['after_links'] if l['viewpoint_id']==cv]
        old = next(l for l in packet['before_links'] if l['viewpoint_id']==cv)
        previous = states['viewpoint_identity_decisions',old['decision_id']]
        candidate = deepcopy(states['viewpoint_identity_candidates',previous['identity_candidate_id']])
        seed = sha256_json({'policy':VERSION,'packet':sha256_json(packet),'raw':raw['artifact_sha256'],'viewpoint_id':cv})
        candidate.update(revision=1,candidate_claim_ids=[claim['claim_id']],candidate_viewpoint_ids=[cv],
                         proposed_action='match_existing',generation_fingerprint=seed,seed_relation_ids=[],blocker_codes=[])
        identity={'claims':candidate['candidate_claim_ids'],'viewpoints':[cv],'relations':[], 'action':'match_existing',
            'blockers':[], 'coverage_snapshot_id':candidate.get('coverage_snapshot_id'),'generation_fingerprint':seed}
        if candidate.get('scope_manifest_sha256'):identity['scope_manifest_sha256']=candidate['scope_manifest_sha256']
        candidate['identity_candidate_id']='VIC-'+sha256_json(identity)[:20]
        decision=deepcopy(previous)
        decision.update(revision=1,identity_decision_id='VID-'+seed[:20],identity_candidate_id=candidate['identity_candidate_id'],
            decision='match_existing',resolved_viewpoint_id=cv,input_sha256=seed,
            claim_link_decisions=[{'claim_id':claim['claim_id'],'link_type':t} for t in sorted({l['link_type'] for l in selected})],
            review_artifact_sha256=raw['artifact_sha256'],semantic_call_artifact_sha256s=[raw['artifact_sha256']],
            reviewer_model_ids=['claude-opus-5-5'],reviewer_kind='system',reviewer_id=REVIEWER,
            approval_basis='owner_scoped_independent_terminal_review',review_status='system_approved',
            created_at=stable_decided_at(Path(packet['run_root'])),policy_version=VERSION,reason=raw['response']['verdicts'][0]['reason'])
        candidates.append(ViewpointIdentityCandidateRecord.model_validate(candidate).model_dump(mode='json'))
        decisions.append(ViewpointIdentityDecisionRecord.model_validate(decision).model_dump(mode='json'))
        for link in selected:
            links.append(ViewpointClaimLinkRecord.model_validate({**link,'decision_id':decision['identity_decision_id']}).model_dump(mode='json'))
    body={'package_id':'CLAIM-STATEMENT-'+sha256_json(packet)[:20],'claims':[claim],
        'viewpoint_claim_links':sorted(links,key=lambda l:l['viewpoint_claim_link_id']),
        'viewpoint_identity_candidates':candidates,'viewpoint_identity_decisions':decisions}
    root=Path(packet['run_root'])
    return {**body,'source_repair_application':{'schema_version':VERSION,'approval_status':'not_human_approved',
        'run_root':str(root),'package_sha256':sha256_json(body),'review_packet':packet,
        'bindings':[binding(root/n) for n in ('packet.json','raw-opus.json','raw-opus.request-encoding.json','review-report.json','review-reservation.json','review-budget-proof.json')]}}


@validated_history
def validate_package(package):
    app=package['source_repair_application']
    if app.get('schema_version')!=VERSION:raise ValueError('statement authority version mismatch')
    for bound in app['bindings']:verify_file(bound)
    packet,raw=reviewed_inputs(app['run_root'])
    if package!=package_for(packet,raw):raise ValueError('statement store gate rejects unreviewed postimages/provenance')


def validate_plan(plan,package):
    from backend.api.canonical_repository.postgres_store import normalize_package, record_content_sha
    normalized=normalize_package(package)
    wanted={(c,o):p for c,rows in normalized.items() for o,p in rows.items()}
    packet=package['source_repair_application']['review_packet']; before=records(packet['context_records'])
    if len(plan['operations'])!=len(wanted):raise ValueError('statement plan operation denominator drift')
    seen=set()
    for op in plan['operations']:
        key=op['collection'],op['object_id']; old=before.get(key)
        if (key in seen or key not in wanted or record_content_sha(wanted[key])!=record_content_sha(op['payload'])
                or op['operation']!=('update' if old else 'create')
                or op['before_revision']!=(old['revision'] if old else None)
                or op['before_sha256']!=(record_content_sha(old) if old else None)):
            raise ValueError('statement plan exact population/predecessor/postimage drift')
        seen.add(key)


@current_check
def verified_applied(bound,store):
    package=read_artifact(verify_file(bound));validate_package(package)
    app=package['source_repair_application'];root=Path(app['run_root']);packet=app['review_packet']
    if Path(bound['path'])!=root/'effective-package.json':raise ValueError('statement application detached from run')
    plan=read_artifact(root/'dry-run-plan.json');validate_plan(plan,package)
    receipt=read_artifact(root/'apply-receipt.json');verified=read_artifact(root/'verified-result.json')
    if (receipt['status']!='applied' or receipt['change_set_id']!=plan['change_set_id']
            or verified['status']!='applied_readback_verified' or verified['change_set_id']!=plan['change_set_id']
            or verified['pre_fingerprint_sha256']!=packet['pre_fingerprint_sha256']
            or verified['master_data_mutations']!=len(plan['operations'])):
        raise ValueError('statement revision has no verified single application')
    for b in verified['bindings']:verify_file(b)
    before=records(packet['context_records']); keys=set(before)|{(o['collection'],o['object_id']) for o in plan['operations']}
    current=read_current_objects(store,keys);live={k:r['payload'] for k,r in current.items() if not r['retired']}
    expected=deepcopy(before)
    for op in plan['operations']:expected[op['collection'],op['object_id']]={**op['payload'],'revision':op['after_revision']}
    if live!=expected:raise ValueError('statement current reviewed readset drift')
    with store.connect() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        row=conn.execute('SELECT source_kind,source_sha256,fingerprint_sha256,status FROM wang_knowledge.change_sets WHERE change_set_id=%s',(plan['change_set_id'],)).fetchone()
        if tuple(row or ())!=(VERSION,plan['source_sha256'],plan['fingerprint_sha256'],'applied'):
            raise ValueError('statement actual SQL application identity drift')
    return package,before,live


def reviewed_claim_ids(package):
    return {package['claims'][0]['claim_id']}


def selection_row(cid,package):
    if cid not in reviewed_claim_ids(package):raise ValueError('unreviewed statement Claim')
    root=Path(package['source_repair_application']['run_root'])
    return {'claim_id':cid,'review_binding':binding(root/'review-report.json'),'review_input_binding':binding(root/'packet.json'),
            'source_repair_binding':binding(root/'effective-package.json')}


def grouping_physical_transition(manifest,bound):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    package,_,_=verified_applied(bound,PostgresKnowledgeStore())
    auth=package['source_repair_application']['review_packet']['authorization']
    if json.loads(verify_file(auth['accepted_membership']).read_text())!=manifest:
        raise ValueError('statement revision cannot authorize another grouping')
    return {}  # No source bytes changed; original physical guards still apply.


def membership_snapshot(manifest,rows,package,completed_ids,selected_ids):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    packet=package['source_repair_application']['review_packet'];cid=packet['before_claim']['claim_id']
    _,_,live=verified_applied(selection_row(cid,package)['source_repair_binding'],PostgresKnowledgeStore())
    if json.loads(verify_file(packet['authorization']['accepted_membership']).read_text())!=manifest:
        raise ValueError('statement historical membership drift')
    for key,payload in live.items():
        current=rows.get(key)
        if not current or current.get('retired') or current['payload']!=payload or current['revision']!=payload['revision'] or current['content_sha256']!=semantic_record_sha(payload):
            raise ValueError('statement partial readset/current pin drift')
    checked=dict(rows);old=packet['before_claim']
    checked['claims',cid]={'payload':old,'revision':old['revision'],'content_sha256':semantic_record_sha(old),'retired':False}
    return checked,[cid]


def grouping_current_claim_packets(manifest,bound,claim_ids):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    package,_,live=verified_applied(bound,PostgresKnowledgeStore());cid=package['claims'][0]['claim_id']
    if cid not in claim_ids:return {}
    packet=package['source_repair_application']['review_packet'];old=packet['before_claim'];new=live['claims',cid]
    prior=next(r for r in manifest['claim_packets'] if r['claim_id']==cid)
    if prior['claim_revision']!=old['revision'] or prior['claim_content_sha256']!=semantic_record_sha(old):
        raise ValueError('statement projection predecessor drift')
    return {cid:{**prior,'statement':new['statement'],'claim_revision':new['revision'],'claim_content_sha256':semantic_record_sha(new)}}


def bridge(*,claim_id,current_claim,current_evidence,current_fragments,source_repair_binding,store=None):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    package,_,live=verified_applied(source_repair_binding,store or PostgresKnowledgeStore())
    own=package['source_repair_application']['review_packet']['own_source_context']
    if (claim_id not in reviewed_claim_ids(package) or live['claims',claim_id]!=current_claim
            or current_evidence!={e['evidence_step_id']:e for e in own['evidence']}
            or current_fragments!={f['fragment_id']:f for f in own['fragments']}):
        raise ValueError('statement fresh bridge Claim/Evidence/fragment drift')
    value={'schema_version':'wang_historical_claim_review_bridge_v1','status':'verified','claim_id':claim_id,
        'pinned_claim_revision':current_claim['revision'],'claim_revision_sha256':semantic_record_sha(current_claim),
        **{k:v for k,v in selection_row(claim_id,package).items() if k!='claim_id'},
        'review_model_id':'claude-opus-5-5','review_provider':'owner_scoped_independent_terminal_subscription_review',
        'old_review_not_relabelled':True,'master_data_mutations':0,'model_calls_executed':0,'approval_status':'not_human_approved'}
    return {**value,'artifact_sha256':sha256_json(value)}


def verify_application(root,store):
    """Full exact readback/inverse proof. Never apply again to recover a receipt."""
    from backend.pipeline.viewpoint_authorized_source_repair_357 import snapshot
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    root=Path(root);package=read_artifact(root/'effective-package.json');validate_package(package)
    plan=read_artifact(root/'dry-run-plan.json');validate_plan(plan,package)
    packet=package['source_repair_application']['review_packet'];before=records(packet['context_records'])
    actual=snapshot(store);restored=dict(actual)
    with store.connect() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        row=conn.execute('SELECT source_kind,source_sha256,fingerprint_sha256,status FROM wang_knowledge.change_sets WHERE change_set_id=%s',(plan['change_set_id'],)).fetchone()
        if tuple(row or ())!=(VERSION,plan['source_sha256'],plan['fingerprint_sha256'],'applied'):
            raise ValueError('statement application not actually committed')
        for op in plan['operations']:
            key=op['collection'],op['object_id'];expected={**op['payload'],'revision':op['after_revision']}
            if actual.get(key)!=expected:raise ValueError('statement exact full readback failed')
            stored=conn.execute('SELECT payload,change_set_id FROM wang_knowledge.object_versions WHERE collection=%s AND object_id=%s AND revision=%s',(*key,op['after_revision'])).fetchone()
            if not stored or tuple(stored)!=(expected,plan['change_set_id']):raise ValueError('statement ObjectVersion lineage failed')
            if key in before:
                prior=conn.execute('SELECT payload FROM wang_knowledge.object_versions WHERE collection=%s AND object_id=%s AND revision=%s AND content_sha256=%s',(*key,op['before_revision'],op['before_sha256'])).fetchone()
                if not prior or prior[0]!=before[key]:raise ValueError('statement retained historical version failed')
                restored[key]=before[key]
            else:del restored[key]
    if (sorted({c for c,o in restored})!=packet['collections']
            or fingerprint(restored,packet['collections'])!=packet['pre_fingerprint_sha256']):
        raise ValueError('statement unexpected non-target Registry mutation')
    validate_packet(packet)
    receipt={'status':'applied','change_set_id':plan['change_set_id'],'recovered_by_actual_SQL':True}
    if not (root/'apply-receipt.json').exists():write_immutable(root/'apply-receipt.json',receipt)
    result={'status':'applied_readback_verified','change_set_id':plan['change_set_id'],
        'pre_fingerprint_sha256':packet['pre_fingerprint_sha256'],
        'post_fingerprint_sha256':fingerprint(actual,sorted({c for c,o in actual})),
        'master_data_mutations':len(plan['operations']),'full_group_independent_audit_complete':False,
        'bindings':[binding(root/n) for n in ('packet.json','effective-package.json','dry-run-plan.json','backup.json','apply-intent.json','apply-receipt.json')]}
    write_immutable(root/'verified-result.json',result)
    return result


def main():
    import argparse
    import fcntl
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock
    from backend.pipeline.viewpoint_authorized_source_repair_357 import snapshot, review_call
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable, write_derived
    from backend.pipeline.source_reconciliation_runtime import ProductionPorts
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--authorization',type=Path,required=True)
    parser.add_argument('--recovery-lock',type=Path,required=True)
    parser.add_argument('--phase',choices=['prepare','review','apply','verify'],required=True)
    args=parser.parse_args();root=args.root.resolve()
    project=Path(__file__).resolve().parents[2];load_dotenv(project/'.env')
    if project.name!='wkp-357-worktree-per-session' or root.parent!=args.recovery_lock.resolve().parent.parent:
        raise ValueError('explicit #357 workspace and isolated runtime root required')
    authorization=read_artifact(args.authorization);store=PostgresKnowledgeStore();ports=ProductionPorts(store)
    with args.recovery_lock.open('r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.phase=='prepare':
            if root.exists():raise ValueError('immutable statement run already exists')
            packet=prepare(snapshot(store),authorization,root)
            size=adapter(subscription_client('claude','claude-opus-5-5','high')).request_bytes(packet)
            if size>500000:raise ValueError('statement review exceeds frozen 500000 ceiling')
            write_immutable(root/'authorization.json',authorization)
            write_immutable(root/'packet.json',packet)
            write_immutable(root/'preflight.json',{'status':'prepared_not_approved','request_bytes':size,'model_calls':0,'Registry_writes':0})
            print(json.dumps({'status':'prepared_not_approved','request_bytes':size}));return
        packet=read_artifact(root/'packet.json');validate_packet(packet)
        if packet['authorization']!=authorization:raise ValueError('owner statement authorization changed')
        if args.phase=='verify':
            print(json.dumps(verify_application(root,store)));return
        if args.phase=='review':
            ports.before_model_call('statement_terminal_review')
            if (root/'review-reservation.json').exists():raise ValueError('statement review once-only reservation already spent')
            if fingerprint(snapshot(store),packet['collections'])!=packet['pre_fingerprint_sha256']:
                raise ValueError('Registry changed before fresh statement review')
            client=adapter(subscription_client('claude','claude-opus-5-5','high'))
            reserve={'policy':VERSION,'packet_sha256':sha256_json(packet),'prompt_sha256':client.prompt_sha256,
                'config_sha256':client.generation_config_sha256,'model':client.model_id,'backend':client.backend,
                'request_bytes':client.request_bytes(packet),'authorization_id':authorization['authorization_id'],'GPT_new_calls':0,'calls_allowed':1}
            # A shared authorization marker prevents a second root spending the same owner grant.
            marker=args.authorization.parent/('statement-call-'+sha256_json(authorization['authorization_id'])+'.json')
            if marker.exists():raise ValueError('cross-root owner statement review budget already reserved')
            write_immutable(marker,{**reserve,'root':str(root)})
            write_immutable(root/'review-reservation.json',reserve)
            write_immutable(root/'review-budget-proof.json',{'authorization_id':authorization['authorization_id'],'reservation':binding(marker)})
            write_derived(root/'progress.json',{'status':'Opus_reviewing','Registry_writes':0})
            try:
                response,calls,_=review_call(client,packet,root,'opus')
                verdicts=Review.model_validate(response).verdicts
                if len(verdicts)!=1 or verdicts[0].claim_id!=authorization['claim_id'] or not verdicts[0].approved or verdicts[0].problems:
                    raise ValueError('Opus rejected/unresolved statement revision; no automatic correction/review retry')
                write_immutable(root/'review-report.json',{'status':'Opus_approved_not_applied','packet_sha256':sha256_json(packet),
                    'review':response,'raw_binding':binding(root/'raw-opus.json'),'calls_executed':calls})
                write_derived(root/'progress.json',{'status':'Opus_approved_not_applied','Registry_writes':0})
            except Exception as exc:
                write_immutable(root/'failure.json',{'error':str(exc),'Registry_writes':0,'automatic_retry':False});raise
            return
        if (root/'apply-intent.json').exists():raise ValueError('statement application already attempted; verify actual SQL, never reapply')
        ports._environment_guard()
        packet,raw=reviewed_inputs(root);package=package_for(packet,raw)
        write_immutable(root/'effective-package.json',package)
        with exclusive_cvp_run_lock({'global_lock_path':str(root.parent/'registry-cvp.lock')}):
            states=snapshot(store)
            if fingerprint(states,packet['collections'])!=packet['pre_fingerprint_sha256']:
                raise ValueError('full Registry changed since exact statement review')
            plan=store.plan_package(package,source_kind=VERSION)
            document=json.loads(json.dumps(plan.as_dict()));validate_plan(document,package)
            write_immutable(root/'dry-run-plan.json',document)
            backup=ports.backup(root);write_immutable(root/'backup.json',backup)
            verify_file(backup['dump'])
            rebuilt=store.plan_package(package,source_kind=VERSION)
            if json.loads(json.dumps(rebuilt.as_dict()))!=document:raise ValueError('statement dry-run changed before single application')
            write_immutable(root/'apply-intent.json',{'plan':binding(root/'dry-run-plan.json'),'backup':backup,
                'owner_authorization':authorization,'automatic_retry':False})
            receipt=store.apply_plan(rebuilt,metadata={'exact_statement_revision':package['source_repair_application']},
                expected_registry_fingerprint_sha256=packet['pre_fingerprint_sha256'],registry_collections=packet['collections'])
            write_immutable(root/'apply-receipt.json',receipt)
            result=verify_application(root,store)
            write_derived(root/'progress.json',result)
            print(json.dumps(result))


if __name__=='__main__':
    main()
