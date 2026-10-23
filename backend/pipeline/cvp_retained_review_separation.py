"""Separate immutable existing membership from this batch's proposed writes.

An incomplete local review is not a new decision about a committed link.
Current SQL approval is disclosed with the original decision identity and full
own bodies; the unused final review still judges every NEW proposed write.
No old reject/raw is changed, no registry mutation or new correction is allowed.
"""
from copy import deepcopy
from pathlib import Path

from backend.api.canonical_repository.postgres_store import record_content_sha, sha256_json

VERSION = 'wang_cvp_retained_review_separation_v1'
NAME = 'retained-review-separation-binding.json'
HUMAN_NAME = 'retained-content-disposition-binding.json'
HUMAN_ADDITIONS = 'retained-content-dispositions'
HUMAN_SCOPES = 'retained-content-scopes'
PROMPT = '''本次新增提案审核与已提交连接维护分开。
retained_review_boundary 中的现有连接是 SQL 已提交、原独立审核批准的当前事实，不是本次新增/修改的后像。
原始拒绝与纠正保留；旧原文语境可消解指代、省略，不要求每条摘要复述CVP全部内容，不能把同一教导的语境解释误当另一独立断言。
对保留项检查 ID/版本/定位/绑定/current CVR/hold 和完整自身正文；pass表示本次没有改动且沿用精确既有批准，不宣称重新批准原生成判定。
真正新内容、新连接、新关系及结构后像仍完整逐项独立语义审核，不沿用旧批准。
若完整语境仍证明旧连接实质错误，明确返回该既有ID及原话，保留未决；交独立既有连接维护，不自动降supports、改Claim或CVP来让新组通过。
component_locator=null 的保留项按既有表示消费，不把新提案自相矛盾的spans当旧数据库必须修改的理由。
原纠正预算已消耗，本次仅原尚未调用的终审；拒绝后不得再纠正。完整Gemini及全部既有pending影响仍必须验收。
'''


def validate_policy(policy):
    expected = {'schema_version':VERSION, 'human_content_approval':False,
        'preserve_current_approved_links':True, 'new_registry_write_permissions':[],
        'additional_gpt_corrections':0, 'use_only_unreserved_final_review':True,
        'independent_affected_audit_required':True}
    if any(type(policy.get(k)) is not type(v) or policy.get(k) != v for k,v in expected.items()) or not policy.get('authorization_quote'):
        raise ValueError('retained review separation cannot grant content changes/retries/audit waiver')


def policy_for(root):
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    from backend.pipeline.cvp_sql_commit import check_binding
    path = next((p/NAME for p in Path(root).resolve().parents if (p/NAME).exists()), None)
    if path is None: return None
    binding = read_artifact(path); check_binding(binding['policy'])
    validate_policy(read_artifact(binding['policy']['path']))
    extra_dir = path.parent / 'retained-generation-leaves'
    leaves = list(binding.get('generation_artifacts', []))
    for leaf_path in sorted(extra_dir.glob('*.json')):
        leaf = read_artifact(leaf_path)
        if generation_artifacts([leaf['binding']['path']])[0] != leaf:
            raise ValueError('additional exact generation leaf drift')
        if leaf not in leaves: leaves.append(leaf)
    return {**binding, 'generation_artifacts':leaves}


def persist_generation_binding(path, value):
    """Append exact raw leaves without upgrading the original policy receipt."""
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable
    path = Path(path)
    if not path.exists():
        write_immutable(path, value)
        return
    old = read_artifact(path)
    if ({k:v for k,v in old.items() if k!='generation_artifacts'} !=
            {k:v for k,v in value.items() if k!='generation_artifacts'}):
        raise ValueError('retained generation policy/campaign drift')
    leaves = value['generation_artifacts']
    prior = list(old['generation_artifacts'])
    for p in sorted((path.parent/'retained-generation-leaves').glob('*.json')):
        leaf = read_artifact(p)
        if leaf not in prior: prior.append(leaf)
    if any(leaf not in leaves for leaf in prior):
        raise ValueError('original retained generation leaves omitted/changed')
    for leaf in leaves:
        if leaf not in old['generation_artifacts']:
            write_immutable(path.parent/'retained-generation-leaves'/(leaf['artifact_sha256']+'.json'),leaf)


def human_bindings(root):
    """Legacy receipt remains immutable; later groups have independent leaves."""
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    root=Path(root)
    result=([read_artifact(root/HUMAN_NAME)['human_disposition']]
            if (root/HUMAN_NAME).exists() else [])
    result += [read_artifact(p)['human_disposition']
               for p in sorted((root/HUMAN_ADDITIONS).glob('*.json'))]
    if len({b['path'] for b in result})!=len(result):
        raise ValueError('duplicate retained content disposition binding')
    return result


def validate_human_direction(direction):
    wanted={'schema_version':'wang_cvp_human_retained_content_disposition_v1',
        'human_exact_disposition':True,'decision':'retain_existing_contextual_equivalence_unchanged',
        'registry_operations':[],'additional_model_calls_authorized':0,
        'group_completion_granted':False,'other_pending_impacts_resolved':False}
    if (any(type(direction.get(k)) is not type(v) or direction.get(k)!=v for k,v in wanted.items())
            or not direction.get('owner_reply') or not direction.get('group_id')
            or not direction.get('campaign_sha256') or not direction.get('items')
            or len({i['link_id'] for i in direction['items']})!=len(direction['items'])):
        raise ValueError('human retain direction cannot grant writes/retries/completion')


def persist_human_bindings(root, explicit, *, campaign, store, limit):
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact,write_immutable
    from backend.pipeline.cvp_sql_commit import check_binding
    old=human_bindings(root)
    if len({b['path'] for b in explicit})!=len(explicit) or any(b not in explicit for b in old):
        raise ValueError('every original exact retained content direction must remain explicit')
    groups={}
    # Validate the entire set before writing any receipt, including the first.
    for b in explicit:
        check_binding(b);d=read_artifact(b['path']);validate_human_direction(d)
        g=store.get_cvp_group(d['group_id'])
        if (d['campaign_sha256']!=campaign or not g or g['campaign_sha256']!=campaign
                or (limit is not None and g['sequence']>limit)):
            raise ValueError('human retention foreign campaign/group or outside execution boundary')
        groups.setdefault(d['group_id'],[]).append(b)
    merged={group:merge_human_directions(bs) for group,bs in groups.items() if len(bs)>1}
    for b in explicit:
        if b not in old:
            write_immutable(Path(root)/HUMAN_ADDITIONS/(sha256_json(b)+'.json'),{'human_disposition':b})
    for group,d in merged.items():
        write_immutable(Path(root)/HUMAN_SCOPES/group/(sha256_json(d)+'.json'),d)


def merge_human_directions(bindings):
    """One-level exact disjoint decisions; never merge different review rounds."""
    from backend.pipeline.cvp_sql_commit import check_binding
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    values=[];seen=set()
    identity=('schema_version','campaign_sha256','group_id','decision',
              'complete_context_final_raw_file_sha256','complete_context_final_raw_artifact_sha256')
    for b in sorted(bindings,key=lambda b:b['path']):
        check_binding(b);d=read_artifact(b['path']);validate_human_direction(d)
        if d.get('source_decision_bindings'):
            raise ValueError('human disposition cannot recursively merge decision bundles')
        if values and any(d.get(k)!=values[0].get(k) for k in identity):
            raise ValueError('human decision bundle different campaign/group/final review')
        ids={i['link_id'] for i in d['items']}
        if ids&seen:raise ValueError('overlapping/conflicting human retained content decisions')
        seen|=ids;values.append(d)
    body={k:v for k,v in values[0].items() if k in (*identity,'human_exact_disposition',
        'registry_operations','additional_model_calls_authorized','group_completion_granted','other_pending_impacts_resolved')}
    body.update(owner_reply='\n'.join(d['owner_reply'] for d in values),
        items=sorted([i for d in values for i in d['items']],key=lambda i:i['link_id']),
        source_decision_bindings=sorted(bindings,key=lambda b:b['path']))
    return body


def human_binding_for(root):
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    from backend.pipeline.cvp_sql_commit import check_binding, binding
    root=Path(root).resolve()
    parent=next((p for p in root.parents
                 if (p/HUMAN_NAME).exists() or (p/HUMAN_ADDITIONS).exists()),None)
    if parent is None:return None
    matches=[]
    for b in human_bindings(parent):
        check_binding(b);d=read_artifact(b['path']);validate_human_direction(d)
        if d['group_id'] in root.parts:matches.append(b)
    if len(matches)>1:
        body=merge_human_directions(matches)
        group=body['group_id'];path=parent/HUMAN_SCOPES/group/(sha256_json(body)+'.json')
        if not path.exists() or read_artifact(path)!=body:
            raise ValueError('human partial decisions lack their exact complete scope receipt')
        return binding(path)
    return matches[0] if matches else None


def generation_artifacts(paths):
    """Bound leaf artifacts only; no directory search or recursive history proof."""
    from backend.pipeline.cvp_sql_commit import binding
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    result = []
    for path in paths:
        raw = read_artifact(Path(path))
        if (raw.get('artifact_sha256') != sha256_json({k:v for k,v in raw.items() if k!='artifact_sha256'})
                or raw.get('response_sha256') != sha256_json(raw.get('response'))
                or any(not raw.get(k) for k in ('model_id','backend','prompt_sha256',
                                               'generation_config_sha256','request_payload_sha256'))):
            raise ValueError('original generation leaf raw identity/SHA incomplete')
        result.append({'binding':binding(Path(path)), 'artifact_sha256':raw['artifact_sha256']})
    if len({r['artifact_sha256'] for r in result}) != len(result):
        raise ValueError('duplicate original generation artifact')
    return result


def prior_review_context(decision, policy, claim_id):
    from backend.pipeline.cvp_sql_commit import check_binding
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    leaves = {r['artifact_sha256']:r for r in policy.get('generation_artifacts',[])}
    shas = decision['semantic_call_artifact_sha256s']
    if not set(shas) <= set(leaves) or decision['review_artifact_sha256'] not in shas:
        raise ValueError('original generation review missing; preserve link and separate pending maintenance')
    result = []
    for sha in shas:
        leaf = leaves[sha]; check_binding(leaf['binding'])
        raw = read_artifact(Path(leaf['binding']['path']))
        if generation_artifacts([leaf['binding']['path']])[0] != leaf:
            raise ValueError('original generation leaf binding drift')
        if raw['model_id'] not in decision['reviewer_model_ids']:
            raise ValueError('original generation model foreign to committed decision')
        response = raw['response']
        # Exact raw verdict text, explicitly an excerpt of the bound original,
        # not a modified/reconstructed model answer. Non-batch selection reviews
        # are small and retained in full.
        context = ({'change_reviews':[r for r in response['change_reviews'] if r['claim_id']==claim_id]}
                   if 'change_reviews' in response else response)
        if 'claim_decisions' in response:
            keys = {c.get('local_new_viewpoint_key') for d in response['claim_decisions']
                    if d['claim_id']==claim_id for c in d['components']}
            context = {'claim_decisions':[d for d in response['claim_decisions'] if d['claim_id']==claim_id],
                       'new_viewpoint_candidates':[c for c in response.get('new_viewpoint_candidates',[]) if c['local_key'] in keys]}
        elif 'finding_dispositions' in response:
            context = {k:response[k] for k in ('schema_version','proposal_sha256','review_sha256') if k in response}
            context.update({k:[v for v in response.get(k,[]) if v.get('claim_id')==claim_id]
                            for k in ('finding_dispositions','component_patches')})
        result.append({'raw_binding':leaf['binding'],'raw_artifact_sha256':sha,
            'model_id':raw['model_id'],'backend':raw['backend'],
            'response_sha256':raw['response_sha256'],'exact_original_context_excerpt':context})
    return result


def guard_other_findings(review, reconsideration, revised=None):
    """Existing-link separation is never a bypass for unresolved NEW content."""
    novelty = review['novelty_review']
    if novelty['status'] != 'pass':
        missed = novelty.get('missed_claim_ids', [])
        accepted = {r['claim_id'] for r in reconsideration.get('finding_dispositions', [])
                    if r['disposition'] == 'accepted'}
        new_claims = {r['claim_id'] for r in (revised or {}).get('claim_decisions', [])
                      if any(p['disposition'] == 'new_viewpoint' for p in r['components'])}
        # The ordinary reconsideration validator already requires the actual
        # patched proposal. This only admits its resolved novelty to the still
        # unused independent final review; it is not a novelty pass or approval.
        if (novelty['status'] != 'missed_novelty' or not missed
                or len(missed) != len(set(missed))
                or not set(missed) <= accepted & new_claims):
            raise ValueError('new novelty finding cannot be separated as an existing connection')
    groups = [('revision_reviews','revision_dispositions'),('structure_reviews','structure_dispositions'),
              ('relation_reviews','relation_dispositions')]
    for reviews, dispositions in groups:
        if any(r['decision'] != 'pass' for r in review.get(reviews,[])):
            answers = reconsideration.get(dispositions,[])
            if not answers or any(a['disposition'] != 'accepted' for a in answers):
                raise ValueError('unresolved new structure/revision/relation cannot use retained separation')


def match_retained(proposal, review, links):
    """Typed identity and exact spans, not strings scraped from reviewer prose."""
    decisions = {d['claim_id']:d['components'] for d in proposal['claim_decisions']}
    result = []
    allowed = {'member_target_adds_load_bearing_content','evidence_does_not_entail',
               'existing_link_locator_null_conflict','reason_contradicts_spans'}
    for item in review['change_reviews']:
        if item['decision'] == 'pass': continue
        codes = set(item.get('finding_codes', []))
        explicit_retained_doubt = codes == {'retained_link_equivalence_doubt',
                                            'proposal_disposition_contradicts_reason'}
        if not codes or (not codes <= allowed and not explicit_retained_doubt):
            raise ValueError('new/source/unknown finding cannot be resolved as retained coverage')
        cid, index = item['claim_id'], item['component_index']
        part = decisions[cid][index]
        found = [l for l in links if l['claim_id'] == cid and l.get('effective_state') == 'active'
            and l['validated_against_viewpoint_revision_id'] == part['target_viewpoint_revision_id']]
        if 'existing_link_locator_null_conflict' not in codes:
            found = [l for l in found if (l.get('component_locator') or {}).get('canonical_spans', []) == part['spans']]
        if len(found) != 1: raise ValueError('retained finding missing/ambiguous exact approved connection')
        result.append({'claim_id':cid,'component_index':index,'link':deepcopy(found[0]),'original_finding':deepcopy(item)})
    if not result or len({r['link']['viewpoint_claim_link_id'] for r in result}) != len(result):
        raise ValueError('empty/duplicate retained review handoff')
    return result


def reflect_current_links(proposal, matched, claims):
    """Program projection of stored representation, not an invented correction.

    Reasons and all non-retained proposal objects remain original. The final
    model sees both the original proposal and every reflected field difference.
    """
    result = deepcopy(proposal); rows = {d['claim_id']:d for d in result['claim_decisions']}
    claim_rows = {c['claim_id']:c for c in claims}; changes = []
    for match in matched:
        link = match['link']; cid = match['claim_id']; index = match['component_index']
        part = rows[cid]['components'][index]; before = deepcopy(part)
        claim = claim_rows[cid]
        if link['pinned_claim_revision'] != claim['pinned_claim_revision']:
            raise ValueError('retained reflection stale Claim version')
        bindings = link['evidence_bindings']
        evidence = {(e['evidence_step_id'],e['source_fragment_id']) for e in claim['evidence']}
        if any((b['evidence_step_id'],b['source_fragment_id']) not in evidence for b in bindings):
            raise ValueError('retained reflection evidence not in current Claim packet')
        part.update(disposition='support_existing' if link['link_type']=='supports' else 'member_existing',
            target_viewpoint_revision_id=link['validated_against_viewpoint_revision_id'],
            spans=deepcopy(link['component_locator']['canonical_spans']) if link.get('component_locator') else deepcopy(part['spans']),
            evidence_step_ids=sorted({b['evidence_step_id'] for b in bindings}),
            source_fragment_ids=sorted({b['source_fragment_id'] for b in bindings}))
        if part != before: changes.append({'claim_id':cid,'component_index':index,'before':before,'after':deepcopy(part)})
    return result, changes


def passed_null_locator_retention(root, store, materials, report, source_loader):
    """A fresh retained-profile pass preserves SQL null, not display spans.

    The disagreement/final-review boundary is a different path. A normal
    initial pass already reviewed the stored representation with full own
    bodies; requiring a prior rejection to preserve it is a compiler defect.
    No reviewer prose is interpreted and no proposal/verdict is rewritten.
    """
    from backend.pipeline.cvp_sql_commit import binding, decoded_pins
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable
    from backend.pipeline.cvp_link_target_selection import _approved
    root = Path(root)
    profile_path = root/'retained-review-input-profile.json'
    if (not profile_path.exists() or report.get('_final_review') is not None
            or not any(l['link_type']=='supports' and l.get('component_locator') is None
                       for l in materials['packet']['existing_links'])):
        return None
    policy = policy_for(root)
    profile = read_artifact(profile_path)
    packet = read_artifact(root/'batch-packet.json')
    if (policy is None or profile != {'schema_version':VERSION,
            'batch_id':packet['batch_id'], 'packet_sha256':packet['packet_sha256'],
            'policy':policy['policy'], 'old_answers_upgraded':False}):
        raise ValueError('passed null retention requires original retained review profile')
    proposal = report['_effective_proposal'].model_dump(mode='json')
    review = report['_review'].model_dump(mode='json')
    if report['approval_outcome'] != 'pass' or report['escalations']:
        raise ValueError('passed null retention cannot resolve rejected content')
    current = materials['packet']['existing_links']
    claims = {c['claim_id']:c for c in packet['claims']}
    decisions = {d['claim_id']:d['components'] for d in proposal['claim_decisions']}
    judgments = {(r['claim_id'],r['component_index']):r for r in review['change_reviews']}
    expected = decoded_pins(materials['read_pins'])
    pins = []; selected = []
    for link in current:
        if link['link_type'] != 'supports' or link.get('component_locator') is not None:
            continue
        cid = link['claim_id']
        alternatives = [i for i,p in enumerate(decisions.get(cid,[]))
            if p['disposition']=='support_existing'
            and p['target_viewpoint_revision_id']==link['validated_against_viewpoint_revision_id']
            and sorted(p['evidence_step_ids'])==sorted({b['evidence_step_id'] for b in link['evidence_bindings']})
            and sorted(p['source_fragment_ids'])==sorted({b['source_fragment_id'] for b in link['evidence_bindings']})]
        if len(alternatives)!=1:
            raise ValueError('passed null retention missing/ambiguous exact component')
        judgment = judgments.get((cid,alternatives[0]))
        if not judgment or judgment['decision']!='pass':
            raise ValueError('passed null retention requires independent component pass')
        claim = claims.get(cid)
        if not claim or claim['pinned_claim_revision']!=link['pinned_claim_revision']:
            raise ValueError('passed null retention stale Claim')
        rows = {}
        for col,oid in [('viewpoint_claim_links',link['viewpoint_claim_link_id']),
                        ('viewpoint_identity_decisions',link['decision_id']),
                        ('canonical_viewpoints',link['viewpoint_id']),
                        ('viewpoint_revisions',link['validated_against_viewpoint_revision_id'])]:
            row = store.get_record_state(col,oid)
            if (not row or row.get('retired') or row['content_sha256']!=record_content_sha(row['payload'])
                    or expected.get((col,oid))!=(row['revision'],row['content_sha256'],False)):
                raise ValueError('passed null retention SQL/read pin drift')
            _approved(row['payload']); rows[col]=row['payload']
            pins.append({'collection':col,'object_id':oid,'revision':row['revision'],
                         'content_sha256':row['content_sha256']})
        d=rows['viewpoint_identity_decisions']
        if (rows['viewpoint_claim_links']!=link
                or rows['canonical_viewpoints']['current_revision_id']!=link['validated_against_viewpoint_revision_id']
                or rows['viewpoint_revisions']['viewpoint_id']!=link['viewpoint_id']
                or d['resolved_viewpoint_id']!=link['viewpoint_id']
                or d.get('approval_basis')!='dual_model_consensus'
                or not d.get('review_artifact_sha256')
                or len(d.get('semantic_call_artifact_sha256s',[]))<2
                or {'claim_id':cid,'link_type':'supports'} not in d['claim_link_decisions']):
            raise ValueError('passed null retention lacks exact current independent approval')
        selected.append(link['viewpoint_claim_link_id'])
    if not selected:return None
    if len(set(selected))!=len(selected):
        raise ValueError('passed null retention duplicate stored link')
    sources=source_loader(sorted({c['source_id'] for c in claims.values()}))
    if {s['source_id'] for s in sources}!={c['source_id'] for c in claims.values()}:
        raise ValueError('passed null retention missing full own source')
    receipt={'schema_version':'wang_cvp_passed_null_locator_retention_v1',
        'original_input_profile':binding(profile_path), 'original_review':binding(root/'raw-review.json'),
        'proposal_sha256':sha256_json(proposal),'review_sha256':sha256_json(review),
        'retained_null_locator_links':sorted(selected),'current_read_pins':pins,
        'own_source_bodies':sources,'registry_mutations':0,'model_verdicts_changed':False,
        'display_spans_are_storage_authority':False}
    write_immutable(root/'passed-null-locator-retention.json',receipt)
    return receipt


def build_boundary(root, store, *, original, revised, review, packet, source_loader, reconsideration):
    from backend.pipeline.cvp_sql_commit import binding, check_binding
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable
    from backend.pipeline.cvp_link_target_selection import _approved
    from backend.api.canonical_repository.viewpoint_batch_resolution import CanonicalViewpointProposalResponse
    root = Path(root)
    policy = policy_for(root)
    if policy is None: return None
    guard_other_findings(review,reconsideration,revised)
    # Only the original still unused final review. Existing final intent/raw is
    # consumed below through the immutable receipt, never a replacement packet.
    matched = match_retained(original, review, packet['retained_existing_claim_links'])
    records = []; prior = []
    for match in matched:
        link = match['link']; lid = link['viewpoint_claim_link_id']
        current = store.get_record_state('viewpoint_claim_links',lid)
        if (not current or current.get('retired') or current['payload'] != link
                or current['content_sha256'] != record_content_sha(link)):
            raise ValueError('retained link current SQL generation drift')
        decision = store.get_record_state('viewpoint_identity_decisions',link['decision_id'])
        head = store.get_record_state('canonical_viewpoints',link['viewpoint_id'])
        cvr = store.get_record_state('viewpoint_revisions',link['validated_against_viewpoint_revision_id'])
        if (not decision or decision.get('retired') or not head or head.get('retired')
                or not cvr or cvr.get('retired') or cvr['payload']['viewpoint_id'] != link['viewpoint_id']
                or head['payload']['current_revision_id'] != link['validated_against_viewpoint_revision_id']):
            raise ValueError('retained link missing/retired/foreign current decision/CVR')
        for row in (decision,head,cvr):
            if row['content_sha256'] != record_content_sha(row['payload']):
                raise ValueError('retained approval current content SHA drift')
        d = decision['payload']; _approved(link); _approved(d); _approved(head['payload']); _approved(cvr['payload'])
        if (d['resolved_viewpoint_id'] != link['viewpoint_id'] or d.get('approval_basis') != 'dual_model_consensus'
                or not d.get('review_artifact_sha256') or len(d.get('semantic_call_artifact_sha256s',[])) < 2
                or {'claim_id':link['claim_id'],'link_type':link['link_type']} not in d['claim_link_decisions']):
            raise ValueError('retained classification lacks exact committed independent decision')
        for col, oid, row in [('viewpoint_claim_links',lid,current),
                             ('viewpoint_identity_decisions',link['decision_id'],decision),
                             ('canonical_viewpoints',link['viewpoint_id'],head),
                             ('viewpoint_revisions',link['validated_against_viewpoint_revision_id'],cvr)]:
            records.append({'collection':col,'object_id':oid,'revision':row['revision'],'content_sha256':row['content_sha256']})
        prior.append({'link_id':lid,'claim_id':match['claim_id'],'component_index':match['component_index'],
                      'original_decision':deepcopy(d),
                      'original_generation_context':prior_review_context(d,policy,link['claim_id'])})
    derived, differences = reflect_current_links(revised, matched, packet['claims'])
    source_ids = sorted({c['source_id'] for c in packet['claims']})
    bodies = source_loader(source_ids)
    if {s['source_id'] for s in bodies} != set(source_ids): raise ValueError('retained final missing complete own source')
    names = ('raw-proposal.json','raw-consolidation.json','raw-review.json','raw-reconsideration.json')
    if not (root/'raw-consolidation.json').exists():
        # The shared runner has no identity call when there are no new candidates.
        # Never mistake an unanswered consolidation intent for that skipped role.
        if (original.get('new_viewpoint_candidates') or packet.get('pending_candidates')
                or any((root/('raw-consolidation'+suffix)).exists() for suffix in
                    ('.call-intent.json','.request-encoding.json','.request-budget.json'))):
            raise ValueError('missing/unknown required consolidation')
        names = tuple(n for n in names if n!='raw-consolidation.json')
    if not all((root/name).is_file() for name in names):
        raise ValueError('retained boundary missing original closed model stages')
    originals = [binding(root/name) for name in names]
    for b in originals: check_binding(b)
    receipt = {'schema_version':VERSION,'policy':policy,'original_responses':originals,
        'prior_current_approvals':prior,'current_read_pins':records,'original_findings':[m['original_finding'] for m in matched],
        # The final request already contains the COMPLETE effective proposal.
        # Bind that exact object instead of embedding a redundant second copy
        # in its auxiliary receipt. No source/Claim/decision is omitted.
        'representation_projection':differences,'effective_proposal_sha256':sha256_json(derived),
        'own_source_bodies':bodies,'registry_mutations':0,'additional_correction_calls':0,
        'old_raw_verdicts_changed':False,'all_pending_impacts_preserved':True}
    receipt['retained_null_locator_links'] = [m['link']['viewpoint_claim_link_id'] for m in matched
        if m['link']['link_type'] == 'supports' and m['link'].get('component_locator') is None]
    receipt['null_locator_display_note'] = 'Proposal spans identify the reviewed discussion only; SQL retains the original null locator. No new storage locator is authorized.'
    path = root/'retained-review-boundary.json'
    if not path.exists() and any((root/('raw-final-review'+suffix)).exists() for suffix in
            ('.json','.call-intent.json','.request-encoding.json','.request-budget.json')):
        raise ValueError('retained boundary cannot replace reserved/returned final review')
    write_immutable(path,receipt)
    if read_artifact(path) != receipt: raise ValueError('retained final boundary differs from immutable original')
    return CanonicalViewpointProposalResponse.model_validate(derived), receipt


def pending_maintenance(root, receipt, final_review):
    """A real complete-context refusal is a separate human content handoff.

    This never changes the verdict, clears completion gates, or authorizes a
    second proposal/review. New-content refusals stay in the normal workflow.
    """
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    from backend.pipeline.cvp_sql_commit import binding
    keys={(p['claim_id'],p['component_index']):p['link_id'] for p in receipt['prior_current_approvals']}
    items=[{'link_id':keys[r['claim_id'],r['component_index']],'final_finding':deepcopy(r)}
           for r in final_review['change_reviews']
           if r['decision']!='pass' and (r['claim_id'],r['component_index']) in keys]
    if not items:return
    write_immutable(Path(root)/'retained-connection-maintenance-handoff.json',{
        'schema_version':VERSION,'status':'requires_human_content_judgment',
        'complete_context_final_review':binding(Path(root)/'raw-final-review.json'),
        'retained_boundary':binding(Path(root)/'retained-review-boundary.json'),
        'items':items,'registry_mutations':0,'additional_calls_allowed':0,
        'group_completion_granted':False})


def write_scope_authority(root, store, materials, report, *, human_binding=None):
    """Consume an exact human NO-CHANGE decision, never manufacture model pass.

    Physical final/raw, old approval and full-source receipts remain separate
    from this human disposition. This admits new model-passed writes only;
    group completion and every other pending impact still require resolution.
    """
    from backend.pipeline.cvp_sql_commit import binding, check_binding, decoded_pins
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact,write_immutable
    from backend.api.canonical_repository.viewpoint_batch_changeset import (
        RETAINED_WRITE_SCOPE_VERSION,retained_write_components)
    from backend.api.canonical_repository.viewpoint_resolution import ReviewClaim
    root=Path(root).resolve()
    if human_binding is None:
        human_binding=human_binding_for(root)
        if human_binding is None:return None
    saved={'human_disposition':human_binding}
    check_binding(saved['human_disposition'])
    direction=read_artifact(saved['human_disposition']['path'])
    if direction.get('source_decision_bindings'):
        if merge_human_directions(direction['source_decision_bindings'])!=direction:
            raise ValueError('human disposition merged scope drift')
    run_id=direction['group_id']
    # Scope to the exact current group; later groups do not inherit this content decision.
    if run_id not in root.parts:return None
    group=store.get_cvp_group(run_id)
    if not group or group['campaign_sha256']!=direction['campaign_sha256']:
        raise ValueError('human retention foreign SQL campaign/group')
    validate_human_direction(direction)
    receipt=report.get('_retained_review_boundary')
    if receipt is None or report.get('_final_review') is None:
        raise ValueError('human retention lacks actual complete-context final review')
    final_path=root/'raw-final-review.json';raw=read_artifact(final_path)
    if (binding(final_path)['file_sha256']!=direction['complete_context_final_raw_file_sha256']
            or raw['artifact_sha256']!=direction['complete_context_final_raw_artifact_sha256']):
        raise ValueError('human retention final raw drift')
    handoff=read_artifact(root/'retained-connection-maintenance-handoff.json')
    check_binding(handoff['complete_context_final_review']);check_binding(handoff['retained_boundary'])
    if (handoff['complete_context_final_review']!=binding(final_path)
            or handoff['retained_boundary']!=binding(root/'retained-review-boundary.json')):
        raise ValueError('human retention conflicts with maintenance handoff')
    human=direction['items'];lids=[i['link_id'] for i in human]
    if len(set(lids))!=len(lids) or set(lids)!={i['link_id'] for i in handoff['items']}:
        raise ValueError('human retention omits/adds an unresolved existing connection')
    expected=decoded_pins(materials['read_pins'])
    with store.connect() as conn,conn.cursor() as cursor:
        store._assert_transaction_read_pins(cursor,expected)
    links=materials['packet']['existing_links'];by_id={l['viewpoint_claim_link_id']:l for l in links}
    pins=[]
    for lid,link in sorted(by_id.items()):
        state=store.get_record_state('viewpoint_claim_links',lid)
        if (not state or state['retired'] or state['payload']!=link
                or expected.get(('viewpoint_claim_links',lid))!=(state['revision'],state['content_sha256'],False)):
            raise ValueError('human retention current SQL/readset drift')
        pins.append({'collection':'viewpoint_claim_links','object_id':lid,
                     'revision':state['revision'],'content_sha256':state['content_sha256']})
    for item in human:
        link=by_id.get(item['link_id'])
        pin=next((p for p in pins if p['object_id']==item['link_id']),None)
        if (not link or item['link_type']!='equivalent_component' or link['link_type']!=item['link_type']
                or item['viewpoint_revision_id']!=link['validated_against_viewpoint_revision_id']
                or item['storage_revision']!=pin['revision'] or item['content_sha256']!=pin['content_sha256']):
            raise ValueError('human no-change disposition is stale or different postimage')
    p=report['_effective_proposal'];r=report['_review']
    authority={'schema_version':RETAINED_WRITE_SCOPE_VERSION,
        'proposal_sha256':sha256_json(p.model_dump(mode='json')),
        'review_sha256':sha256_json(r.model_dump(mode='json')),
        'human_disposition':saved['human_disposition'],'human_retained_link_ids':sorted(lids),
        'retained_link_pins':pins,'original_final_review':binding(final_path),
        'retained_boundary':binding(root/'retained-review-boundary.json'),
        'model_verdicts_changed':False,'all_pending_impacts_preserved':True}
    # Ordinary groups have no Claim-first receipt. Pin the actual reviewed
    # Claim generations directly; never derive an old generation from a later audit.
    if not (root/'retained-write-scope-authority.json').exists():
        cids={by_id[lid]['claim_id'] for lid in lids}
        authority['human_retained_claim_pins']=[{'collection':'claims','object_id':cid,
            'pin':list(expected['claims',cid])} for cid in sorted(cids)]
    else:
        old=read_artifact(root/'retained-write-scope-authority.json')
        if 'human_retained_claim_pins' in old:
            authority['human_retained_claim_pins']=old['human_retained_claim_pins']
    retained_write_components(p,r,[ReviewClaim.model_validate(c)
        for c in materials['packet']['claims']],links,authority,
        retained_null_link_ids=receipt['retained_null_locator_links'])
    write_immutable(root/'retained-write-scope-authority.json',authority)
    return authority


def final_adapter(adapter, *, initial=False):
    from copy import copy
    obj = copy(adapter)
    prompt = PROMPT if not initial else PROMPT.split('原纠正预算已消耗')[0] + (
        '这是新组的首次审核。仅审核新增/修改后像，现有连接属于不变事实；'
        '完整自身正文用于理解教授语境。旧连接的疑问与新内容问题分开标识，不自动改连接。'
        '正常有界纠正预算与完整受影响审计不变。')
    obj._base_prompt = adapter._base_prompt + '\n' + prompt
    obj._prompt = adapter._prompt + '\n' + prompt
    obj.prompt_sha256 = sha256_json({'prompt':obj._prompt})
    return obj
