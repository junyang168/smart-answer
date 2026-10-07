"""Owner-authorized g033 identity correction, never a general content waiver."""
from copy import deepcopy
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict

from backend.api.canonical_repository.knowledge_models import ViewpointComponentLocator, ViewpointPropositionSignature
from backend.api.canonical_repository.viewpoint_foundation import sha256_json, semantic_record_sha
from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock
from backend.pipeline.viewpoint_authorized_source_repair_357 import snapshot, fingerprint, review_call
from backend.pipeline.viewpoint_audit_source_adjudication import build_packet, run as adjudicate, validate_own_source_identity
from backend.pipeline.viewpoint_evidence_source_context import load_context_source
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable, stable_decided_at, subscription_client, call_model
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter

VIEWPOINT = 'CV-d5fe34e3533e03ae0101'
OLD_REVISION = 'CVR-471598815ca1cdf74ca6'
LINKS = {'VCL-369b4b05947095770ef5', 'VCL-e6f66ef9d598ad47132f'}
RELATIONS = {'VREL-4c932217b381b031ea4e', 'VREL-9e8619d30ecc0951c19e', 'VREL-d9e9f558b1cf878479b5'}
HANDOFF_FILE_SHA = 'ad6197b7469df49e16260fa179f878a92469cfc8a645e7b3564dbbaaf76b8f5d'
POLICY = 'wang-g033-owner-identity-repair-v1'

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')

class LinkProposal(Strict):
    link_id: str
    link_type: Literal['equivalent_full', 'equivalent_component', 'supports', 'extends', 'qualifies', 'applies']
    component_locator: ViewpointComponentLocator | None
    reason: str

class RelationProposal(Strict):
    relation_id: str
    relation_type: Literal['generalizes', 'specializes', 'entails', 'extends', 'qualifies', 'applies']
    reason: str

class Proposal(Strict):
    needs_new_scope: bool
    reason: str
    core_proposition: str
    proposition_signature: ViewpointPropositionSignature
    links: list[LinkProposal]
    relations: list[RelationProposal]

PROPOSE_PROMPT = '''负责人仅授权修订这一观点及其两条成员连接、三条关联观点关系，解决原文仲裁确认的「發光即產生影響」无依据同义断言。
依据每条Claim自己的完整原文提议一个可复用的原子命题；不能替教授建构、把并列变同义，也不能把论据/关联强塞等价。
只提议core_proposition/proposition_signature、指定两连接的类型/精确成分定位、指定三关系的类型/reason；不改变范围、端点、Claim、Evidence、来源或其他观点文字，不新增/退役/拆分观点或连接。
每条连接必须独立忠于自身Claim，等价成分定位须精确对应Claim文字及给定claim_semantic_sha256。保留来源中另有的限定，不能用别条Claim补等价。
三条关系需按修订命题重新验证方向及意义，不可仅换版本pin掩盖语义失效；至少一条真正等价成员。
若上述范围不足、任何关系无法忠实保留或需其他改动，needs_new_scope=true并说明，不能为了续跑制造成立关系。只读取给定包，不执行操作。不评价教授神学对错。'''

PROMPT = '''独立核查负责人授权的单一g033观点修订及关联连接精确后像。不是原异议再仲裁。
阅读before/after viewpoint revision、两条before_link/after_link、三条before_relation/after_relation、邻接观点和全部Claim自身完整原文。
逐条核查：移除无依据同义断言，新命题忠于来源、原子且可复用；每条等价/支持/扩展等类型方向真正成立，component定位逐字正确，不能借其他Claim原文补本Claim结论。
三条观点关系须对新命题真正成立，不可机械换pin保留失效语义。Claim/Evidence/source/范围/端点/其他观点文字必须原样，旧修订保留，未漏掉成员或依赖，无新观点/退役/ArgumentRoute操作。
targets每个Claim恰好一个verdict；任何总体命题或关系问题须在对应verdict的problems报告，任一问题都拒绝。只有全部后像忠实、全部依赖有效才approved=true且problems=[]。不做修正，不执行写入，不评价神学对错。'''

def context(campaign, store):
    from backend.pipeline.viewpoint_audit_link_repair import BASE
    from backend.api.canonical_repository.viewpoint_production_safety import file_sha256
    state = read_artifact(campaign/'progress.json')
    if state.get('sequence') != 33 or state.get('group_key') != 'p016:p016:whole' or state.get('status') != 'blocked':
        raise ValueError('owner authorization is only blocked g033')
    group = campaign/'g033'
    path = group/'audit-source-adjudication/gpt-final-handoff.json'
    if file_sha256(path) != HANDOFF_FILE_SHA:
        raise ValueError('owner-approved adjudication drift')
    if not (path.parent/'raw-gpt.json').is_file() or not (path.parent/'packet.json').is_file():
        raise ValueError('saved adjudication missing; new-call fallback forbidden')
    packet = build_packet(group/'independent-audit/audit.json', store)
    if packet != read_artifact(path.parent/'packet.json'):
        raise ValueError('original adjudication packet drift')
    # Validates the saved raw, model/backend, quotes and current Registry; zero new calls.
    report = adjudicate(group/'independent-audit/audit.json', path.parent, store, final_gpt=True)
    upheld = [a for a in report['actions'] if a['verdict'] == 'audit_objection_upheld']
    rejected = [a for a in report['actions'] if a['verdict'] == 'audit_objection_rejected']
    if len(upheld) != 2 or len(rejected) != 2 or any(not a['item_id'].startswith('4:'+VIEWPOINT+':') for a in upheld):
        raise ValueError('authorization verdict population drift')
    states = snapshot(store)
    old = states['viewpoint_revisions', OLD_REVISION]
    cv = states['canonical_viewpoints', VIEWPOINT]
    links = sorted([p for (c,_),p in states.items() if c=='viewpoint_claim_links' and p.get('viewpoint_id')==VIEWPOINT and p.get('effective_state')=='active'], key=lambda p:p['viewpoint_claim_link_id'])
    relations = sorted([p for (c,_),p in states.items() if c=='viewpoint_relations' and VIEWPOINT in {p['source_viewpoint_id'],p['target_viewpoint_id']}], key=lambda p:p['viewpoint_relation_id'])
    if cv['current_revision_id'] != OLD_REVISION or {p['viewpoint_claim_link_id'] for p in links} != LINKS or {p['viewpoint_relation_id'] for p in relations} != RELATIONS:
        raise ValueError('owner target membership/dependency drift')
    for (collection,_), p in states.items():
        if collection in {'argument_routes','argument_route_revisions','argument_route_attestations','viewpoint_structures','product_dependencies'} and (VIEWPOINT in str(p) or OLD_REVISION in str(p)):
            raise ValueError('new dependent scope is not authorized')
    neighbors = sorted({p['source_viewpoint_id'] for p in relations}|{p['target_viewpoint_id'] for p in relations})
    related_links = [p for (c,_),p in states.items() if c=='viewpoint_claim_links' and p.get('viewpoint_id') in neighbors and p.get('effective_state')=='active']
    claim_ids = sorted({p['claim_id'] for p in related_links})
    claims = []; sources = {}
    for cid in claim_ids:
        claim = states['claims',cid]; evidence = []
        for eid in claim['evidence_step_ids']:
            step = states['evidence_steps',eid]
            fids = set(step.get('source_fragment_ids',[])) | ({step['source_fragment_id']} if step.get('source_fragment_id') else set())
            for fid in sorted(fids):
                fragment = states['source_fragments',fid]; sid = fragment['source_id']
                source = states['source_documents',sid]
                validate_own_source_identity(source,{o['source_id'] for o in claim['occurrences']},store)
                if sid not in sources:
                    path, sha, projection = load_context_source(source)
                    sources[sid] = {'source_id':sid,'path':str(path),'file_sha256':sha,'body_sha256':projection.body_sha256,
                        'paragraphs':[{'paragraph_key':f'S{i+1:04d}','text':str(p.get('text') or '')} for i,p in enumerate(projection.spoken_rows)]}
                paras = {p['paragraph_key']:p['text'] for p in sources[sid]['paragraphs']}
                if not fragment['verbatim_excerpt'] or fragment['verbatim_excerpt'] not in paras.get(fragment['paragraph_key'],''):
                    raise ValueError('physical own evidence drift')
                evidence.append({'step':step,'fragment':fragment})
        claims.append({'claim':claim,'claim_semantic_sha256':semantic_record_sha(claim),'evidence':evidence})
    body = {'schema_version':POLICY,'authorization_quote':'同意','authorized_viewpoint_id':VIEWPOINT,
        'before_viewpoint':cv,'before_revision':old,'before_links':links,'before_relations':relations,
        'neighbor_revisions':[states['viewpoint_revisions',states['canonical_viewpoints',v]['current_revision_id']] for v in neighbors if v!=VIEWPOINT],
        'claims':claims,'sources':list(sources.values()),'adjudication':report,
        'adjudication_packet_sha256':packet['artifact_sha256'],'claim_evidence_source_writes':0}
    return states, body

def propose(campaign, root, store, *, storage_review_from=None):
    from backend.pipeline.viewpoint_audit_link_repair import BASE
    if storage_review_from is not None:
        old_root = storage_review_from.resolve()
        if old_root != BASE/'g033-owner-identity-repair-20261004-v1':
            raise ValueError('only exact failed g033 review may be corrected')
        reservation = old_root/'owner-storage-correction-start.json'
        if reservation.exists(): raise ValueError('storage review correction already attempted')
        with exclusive_cvp_run_lock({'global_lock_path':str(BASE/'registry-cvp.lock')}):
            states, packet = prepare(campaign, old_root, store)
            if packet != read_artifact(old_root/'packet.json') or (old_root/'apply-receipt.json').exists():
                raise ValueError('failed review semantic postimage/apply drift')
            from backend.pipeline.viewpoint_audit_link_repair import Review
            adapter=StructuredJsonReviewerAdapter(client=subscription_client('codex','gpt-6.1-sol','high'),
                prompt=PROMPT,response_model=Review,schema_name='audit_link_repair_review_v1',compact_payload=True)
            adapter.max_request_bytes=500000
            if not (old_root/'raw-gpt.json').is_file(): raise ValueError('prior review missing')
            prior,calls,_=call_model(adapter,packet,old_root/'raw-gpt.json')
            if calls or read_artifact(old_root/'raw-gpt.json')['artifact_sha256']!='8edeab4563996dbb1ce0e8ec534906186bab852086cc92d922c8496ab4be72bd':
                raise ValueError('exact owner-authorized mechanical refusal drift')
            storage_proof(packet)
            write_immutable(reservation,{'authorization_quote':'继续。','output_root':str(root),'semantic_packet_sha256':sha256_json(packet),'max_corrected_gpt_calls':1})
            for name in ('proposal-input.json','proposal-before.json','raw-proposal.json','owner-authorization.json','request-preflight.json'):
                write_immutable(root/name,read_artifact(old_root/name))
            write_immutable(root/'mechanical-review-source.json',{'authorization_quote':'继续。','previous_root':str(old_root),
                'semantic_packet_sha256':sha256_json(packet),'previous_refusal':read_artifact(old_root/'raw-gpt.json'),
                'proposal_reused_new_calls':0,'same_semantic_postimage':True,'single_correction_only':True})
        return
    with exclusive_cvp_run_lock({'global_lock_path':str(BASE/'registry-cvp.lock')}):
        states, payload = context(campaign,store)
        cols = sorted({c for c,_ in states})
        write_immutable(root/'proposal-input.json',payload)
        write_immutable(root/'proposal-before.json',{'collections':cols,'pre_fingerprint_sha256':fingerprint(states,cols)})
        write_immutable(root/'owner-authorization.json',{'policy_id':POLICY,'authorization_quote':'同意',
            'campaign_root':str(campaign),'adjudication_file_sha256':HANDOFF_FILE_SHA,
            'viewpoint_id':VIEWPOINT,'old_revision_id':OLD_REVISION,
            'link_ids':sorted(LINKS),'relation_ids':sorted(RELATIONS),
            'claim_evidence_source_writes':0,'automatic_future_identity_repair':False,
            'previous_failure_receipts_and_budgets_preserved':True})
    adapter = proposal_adapter()
    write_immutable(root/'request-preflight.json',{'request_bytes':adapter.request_bytes(payload),'limit':500000,'would_write_registry':False})
    raw,_,_ = review_call(adapter,payload,root,'proposal')
    result = Proposal.model_validate(raw)
    if result.needs_new_scope: raise ValueError('identity proposal requires new scope: '+result.reason)
    validate_proposal(payload,result.model_dump(mode='json'))

def proposal_adapter():
    adapter = StructuredJsonReviewerAdapter(client=subscription_client('codex','gpt-6.1-sol','high'),prompt=PROPOSE_PROMPT,response_model=Proposal,schema_name=POLICY+'_proposal',compact_payload=True)
    adapter.max_request_bytes=500000
    return adapter

def validate_proposal(payload, proposal):
    p = Proposal.model_validate(proposal)
    if p.needs_new_scope or not p.core_proposition.strip() or not p.reason.strip():
        raise ValueError('no bounded identity proposal')
    if len(p.links)!=2 or {i.link_id for i in p.links}!=LINKS or len(p.relations)!=3 or {i.relation_id for i in p.relations}!=RELATIONS:
        raise ValueError('proposal population mismatch')
    own = {i['claim']['claim_id']:i for i in payload['claims']}
    old_links = {i['viewpoint_claim_link_id']:i for i in payload['before_links']}
    for item in p.links:
        claim = own[old_links[item.link_id]['claim_id']]
        if not item.reason.strip(): raise ValueError('missing link reason')
        if item.link_type=='equivalent_component' and item.component_locator is None:
            raise ValueError('missing equivalent component')
        if item.link_type=='equivalent_full' and item.component_locator is not None:
            raise ValueError('whole claim cannot have locator')
        if item.component_locator:
            if item.component_locator.claim_sha256!=claim['claim_semantic_sha256']: raise ValueError('component Claim SHA drift')
            for span in item.component_locator.canonical_spans:
                if claim['claim']['statement'][span.start_char:span.end_char]!=span.exact_text: raise ValueError('component text drift')
    if not any(i.link_type.startswith('equivalent_') for i in p.links):
        raise ValueError('cannot orphan viewpoint identity')
    if any(not i.reason.strip() for i in p.relations): raise ValueError('missing relation reason')
    return p.model_dump(mode='json')

def prepare(campaign, root, store):
    states, payload = context(campaign,store)
    baseline = read_artifact(root/'proposal-before.json')
    if payload!=read_artifact(root/'proposal-input.json') or fingerprint(states,baseline['collections'])!=baseline['pre_fingerprint_sha256']:
        raise ValueError('proposal Registry/source drift')
    cache=root/'raw-proposal.json'
    if not cache.is_file(): raise ValueError('saved proposal missing; new-call fallback forbidden')
    raw,calls,_=call_model(proposal_adapter(),payload,cache)
    if calls: raise ValueError('unexpected proposal replay')
    proposal = validate_proposal(payload,raw)
    rid = 'CVR-'+sha256_json({'policy':POLICY,'old_revision':OLD_REVISION,'proposal':proposal})[:20]
    after = deepcopy(payload['before_revision'])
    after.update(viewpoint_revision_id=rid,revision=1,revision_number=1,supersedes_revision_id=OLD_REVISION,
        core_proposition=proposal['core_proposition'],proposition_signature=proposal['proposition_signature'])
    by_link = {p['link_id']:p for p in proposal['links']}
    targets = []
    for old in payload['before_links']:
        chosen = by_link[old['viewpoint_claim_link_id']]; new=deepcopy(old)
        new.update(revision=old['revision']+1,validated_against_viewpoint_revision_id=rid,
            link_type=chosen['link_type'],component_locator=chosen['component_locator'])
        claim=states['claims',old['claim_id']]
        targets.append({'claim':claim,'before_link':old,'after_link':new,'sources':payload['sources'],
            'reason':chosen['reason']})
    by_relation = {p['relation_id']:p for p in proposal['relations']}
    after_relations=[]
    for old in payload['before_relations']:
        new=deepcopy(old); chosen=by_relation[old['viewpoint_relation_id']]
        new.update(revision=old['revision']+1,relation_type=chosen['relation_type'],reason=chosen['reason'])
        for side in ('source','target'):
            if old[side+'_viewpoint_id']==VIEWPOINT: new['validated_'+side+'_viewpoint_revision_id']=rid
        after_relations.append(new)
    packet = {'schema_version':POLICY,'policy':{'policy_id':POLICY,'authorization_quote':'同意','automatic_future_identity_repair':False},
        'targets':targets,'context':payload,'after_revision':after,'after_relations':after_relations,
        'proposal':proposal,'claim_evidence_source_writes':0}
    if (root/'mechanical-review-source.json').is_file():
        prior=read_artifact(root/'mechanical-review-source.json')
        if sha256_json(packet)!=prior['semantic_packet_sha256']:
            raise ValueError('storage correction changed semantic postimage')
        packet['mechanical_storage_proof']=storage_proof(packet)
    return states, packet

def revision_writer_excerpt(source):
    """Find the unique revision constructor by syntax, never by line numbers."""
    import ast
    tree = ast.parse(source)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == 'compile_cvp_batch_package']
    if len(functions) != 1:
        raise ValueError('mechanical source authority function missing or ambiguous')
    calls = []
    for node in ast.walk(functions[0]):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == 'ViewpointRevisionRecord'):
            continue
        keywords = {k.arg: k.value for k in node.keywords}
        if 'supersedes_revision_id' in keywords:
            calls.append((node, keywords))
    if len(calls) != 1:
        raise ValueError('mechanical source authority constructor missing or ambiguous')
    call, keywords = calls[0]
    counter = keywords.get('revision_number')
    parent = keywords['supersedes_revision_id']
    if (not isinstance(counter, ast.Constant) or type(counter.value) is not int or counter.value != 1
            or not isinstance(parent, ast.Name) or parent.id != 'target_key'):
        raise ValueError('mechanical source authority store counter or supersedes chain drift')
    return ast.get_source_segment(source, call)


def storage_proof(packet):
    """Executable model evidence, not a waiver of independent content review."""
    from backend.api.canonical_repository.knowledge_models import ViewpointRevisionRecord
    from backend.api.canonical_repository.viewpoint_production_safety import file_sha256
    from pydantic import ValidationError
    old=packet['context']['before_revision']; new=packet['after_revision']
    if new['revision']!=1 or new['revision_number']!=1 or new['viewpoint_revision_id']==old['viewpoint_revision_id'] or new['supersedes_revision_id']!=old['viewpoint_revision_id'] or new['viewpoint_id']!=old['viewpoint_id']:
        raise ValueError('new immutable revision identity/store invariant failed')
    ViewpointRevisionRecord.model_validate(new)
    wrong=deepcopy(new); wrong['revision_number']=2
    try: ViewpointRevisionRecord.model_validate(wrong)
    except ValidationError as exc:
        if 'revision_number must equal store revision' not in str(exc): raise
    else: raise ValueError('invalid store counter was not rejected')
    model_path=Path(__file__).parents[1]/'api/canonical_repository/knowledge_models.py'
    writer_path=model_path.with_name('viewpoint_batch_changeset.py')
    excerpt=revision_writer_excerpt(writer_path.read_text())
    return {'schema_version':'wang_immutable_revision_storage_proof_v1',
        'rule':'Each new ViewpointRevision is a new immutable stored object: revision=1 and revision_number=1. Semantic succession uses supersedes_revision_id, not an incrementing store counter.',
        'new_revision_validated':True,'counter_2_rejected':True,'supersedes_chain_validated':True,
        'model_file_sha256':file_sha256(model_path),'model_schema_sha256':sha256_json(ViewpointRevisionRecord.model_json_schema()),
        'writer_file_sha256':file_sha256(writer_path),'writer_excerpt':excerpt,
        'review_boundary':'Mechanical storage evidence only. Independently review all unchanged semantic postimages and full originals; no approval or verdict is overridden.'}

def package_for(states, packet, reviews, root):
    from backend.pipeline.viewpoint_audit_link_repair import package_for as link_package
    base_packet=deepcopy(packet); base_packet['policy']={'policy_id':'wang-audit-link-repair-v1'}
    package=link_package(states,base_packet,reviews,root)
    for link, decision, candidate, target in zip(package['viewpoint_claim_links'],package['viewpoint_identity_decisions'],package['viewpoint_identity_candidates'],packet['targets'],strict=True):
        decision.update(claim_link_decisions=[{'claim_id':link['claim_id'],'link_type':link['link_type']}],policy_version=POLICY,
            reason='Owner-scoped g033 identity correction; exact semantic postimage dual reviewed. '+target['reason'])
        candidate['proposed_proposition_signature']=packet['after_revision']['proposition_signature']
    ids=sorted(d['identity_decision_id'] for d in package['viewpoint_identity_decisions'])
    rev=deepcopy(packet['after_revision'])
    rev.update(approved_by=POLICY,approved_at=stable_decided_at(root),provenance={'basis_identity_decision_ids':ids,'review_artifact_sha256':sha256_json(reviews)})
    cv=deepcopy(packet['context']['before_viewpoint']); cv.update(revision=cv['revision']+1,current_revision_id=rev['viewpoint_revision_id'])
    relations=deepcopy(packet['after_relations'])
    for rel in relations: rel['review_provenance']={'basis_identity_decision_ids':ids,'review_artifact_sha256':sha256_json(reviews)}
    package.update(viewpoint_revisions=[rev],canonical_viewpoints=[cv],viewpoint_relations=relations)
    package['package_id']='G033-IDENTITY-REPAIR-'+sha256_json({'packet':packet,'reviews':reviews})[:20]
    return package
