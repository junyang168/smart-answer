"""Exact full-source correction inputs; old answers never acquire a new contract.

The repair is an engineering generation, not another normal correction round.
Its fixed parent contains all old answers and the structurally missing input.
Only the original unused independent final can approve a resulting postimage.
"""
from copy import copy
from pathlib import Path

from backend.api.canonical_repository.postgres_store import sha256_json
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable

VERSION = 'wang_cvp_correction_related_source_context_v1'
REPAIR = 'wang_cvp_correction_related_source_input_repair_v1'
PROFILE = 'correction-source-input-profile.json'
BINDING = 'correction-source-input-repair-binding.json'
RAW = 'raw-correction-source-input-repair.json'


def profile(root, packet, *, complete_initial):
    root = Path(root)
    path = root / PROFILE
    if complete_initial and not any(root.glob('raw-proposal*')):
        write_immutable(path, {'schema_version': VERSION,
            'packet_sha256': packet['packet_sha256'], 'batch_id': packet['batch_id'],
            'old_answers_upgraded': False})
    if not path.exists():
        return None
    value = read_artifact(path)
    if value != {'schema_version': VERSION, 'packet_sha256': packet['packet_sha256'],
                 'batch_id': packet['batch_id'], 'old_answers_upgraded': False}:
        raise ValueError('correction full-source profile identity drift')
    return value


def adapter(original):
    result = copy(original)
    if hasattr(result, '_base_prompt'):
        suffix = ('\n纠正阶段仅附本次待改项及其完整候选成员、结构／关系参与者相关的完整自身正文，'
                  '与初审材料同源同版本。未列入修改范围的原提案项保持原样。区分 Claim 绑定引文与完整'
                  '讲道上下文；全文可核实论证作用，但不得改 Claim、增删原文或扩大写权限。'
                  '不预定接受或反驳任何发现；真正不确定保留未决。')
        result._base_prompt = original._base_prompt + suffix
        result._prompt = original._prompt + suffix
        result.prompt_sha256 = sha256_json({'prompt': result._prompt})
    return result


def related_claim_ids(packet):
    """Exact judgment scope, not keyword retrieval or a shortened source body.

    A component finding owns its Claim. A changed structure/relation owns ALL
    candidate participants, including passing co-members. Unknown references
    fail closed rather than silently dropping a necessary source.
    """
    proposal, review = packet['proposal'], packet['review']
    selected = {r['claim_id'] for r in review['change_reviews'] if r['decision'] != 'pass'}
    selected.update(review.get('novelty_review', {}).get('missed_claim_ids', []))
    if any(r['decision'] != 'pass' for r in review.get('revision_reviews', [])):
        raise ValueError('correction existing revision requires complete management member scope')
    refs = set()
    def references(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('local_key', 'viewpoint_revision_id', 'source_local_key',
                           'target_local_key', 'source_viewpoint_revision_id',
                           'target_viewpoint_revision_id') and item:
                    refs.add(item)
                else:
                    references(item)
        elif isinstance(value, list):
            for item in value:
                references(item)
    for r in review.get('structure_reviews', []):
        if r['decision'] != 'pass':
            structure = proposal['structures'][r['structure_index']]
            if any(f.get('viewpoint_revision_id') for f in structure['focal']):
                raise ValueError('existing structure participant needs complete current management members')
            references(structure)
    for r in review.get('relation_reviews', []):
        if r['decision'] != 'pass':
            refs.update((r['source_ref'], r['target_ref']))
    for decision in proposal['claim_decisions']:
        if decision['claim_id'] in selected:
            for component in decision['components']:
                ref = component.get('local_new_viewpoint_key') or component.get('target_viewpoint_revision_id')
                if ref:
                    refs.add(ref)
    found = set()
    for decision in proposal['claim_decisions']:
        for component in decision['components']:
            for key in ('local_new_viewpoint_key', 'target_viewpoint_revision_id'):
                ref = component.get(key)
                if ref in refs:
                    found.add(ref)
                    selected.add(decision['claim_id'])
    if found != refs:
        raise ValueError('correction related identity/member source scope is incomplete')
    claims = {c['claim_id']: c for c in packet['packet']['claims']}
    if not selected or not selected <= claims.keys():
        raise ValueError('correction related Claim scope missing/foreign')
    return sorted(selected)


def validate_scope_preservation(original, revised, review, related):
    """No unrelated edit can borrow the focused source approval."""
    before = {d['claim_id']: d for d in original['claim_decisions']}
    after = {d['claim_id']: d for d in revised['claim_decisions']}
    if before.keys() != after.keys():
        raise ValueError('correction changed original Claim denominator')
    writable = {r['claim_id'] for r in review['change_reviews'] if r['decision'] != 'pass'}
    writable.update(review.get('novelty_review', {}).get('missed_claim_ids', []))
    if not writable <= set(related) or any(after[c] != before[c] for c in before if c not in writable):
        raise ValueError('correction changed an unrelated Claim component')
    for r in review.get('structure_reviews', []):
        if r['decision'] == 'pass' and (
                revised['structures'][r['structure_index']] != original['structures'][r['structure_index']]):
            raise ValueError('correction changed a passing unrelated structure')
    if original.get('viewpoint_revisions') != revised.get('viewpoint_revisions'):
        raise ValueError('correction added existing CVP revision scope')
    keys = {c.get('local_new_viewpoint_key') for d in original['claim_decisions']
            if d['claim_id'] in related for c in d['components']}
    old_candidates = {c['local_key']: c for c in original['new_viewpoint_candidates']}
    new_candidates = {c['local_key']: c for c in revised['new_viewpoint_candidates']}
    if any(new_candidates.get(k) != c for k, c in old_candidates.items() if k not in keys):
        raise ValueError('correction changed an unrelated candidate')
    if all(r['decision'] == 'pass' for r in review.get('relation_reviews', [])) and (
            original.get('viewpoint_relations') != revised.get('viewpoint_relations')):
        raise ValueError('correction changed a passing relation scope')


def complete_packet(packet, sources, receipt):
    claims = {c['claim_id']: c for c in packet['packet']['claims']}
    related = related_claim_ids(packet)
    ids = sorted({claims[c]['source_id'] for c in related})
    all_ids = sorted({c['source_id'] for c in claims.values()})
    if sorted(s['source_id'] for s in sources) != all_ids or len(sources) != len(all_ids):
        raise ValueError('correction complete own-source denominator drift')
    if any(not s.get('paragraph_texts') or not s.get('source_file_sha256')
           or not s.get('source_body_sha256') for s in sources):
        raise ValueError('correction complete own-source proof missing')
    selected = [s for s in sources if s['source_id'] in ids]
    return {**packet, 'own_source_bodies': selected,
            'correction_source_context': {**receipt,
                'related_claim_ids': related, 'related_source_ids': ids,
                'complete_related_sources_sha256': sha256_json(selected),
                'unrelated_source_ids_not_attached': sorted(set(all_ids) - set(ids)),
                'source_bodies_truncated': False}}


def repair_packet(root, *, original, review_packet, correction_packet,
                  sources, reconsideration, proposal, review):
    """Called only AFTER normal runtime authenticates the original three leaves."""
    from backend.pipeline.cvp_sql_commit import check_binding, binding
    root = Path(root)
    if not (root / BINDING).exists():
        return None
    auth = read_artifact(root / BINDING)['authorization']
    check_binding(auth)
    d = read_artifact(auth['path'])
    if (d.get('schema_version') != REPAIR or d.get('authorized_by') != 'owner_engineering'
            or not d.get('authorization_quote') or d.get('human_content_approval') is not False
            or d.get('new_engineering_calls') != 1 or d.get('additional_corrections') != 0
            or d.get('review_stage') != 'original_unused_final_review'
            or d.get('artifact_sha256') != sha256_json({k:v for k,v in d.items() if k!='artifact_sha256'})
            or Path(d['normal_root']).resolve() != root.resolve()
            or d.get('packet_sha256') != correction_packet['packet']['packet_sha256']
            or d.get('batch_id') != correction_packet['batch_id']):
        raise ValueError('correction source repair fixed-parent direction drift')
    for name in ('raw-proposal.json', 'raw-review.json', 'raw-reconsideration.json'):
        if d['original_leaves'].get(name) != binding(root / name):
            raise ValueError('correction source repair original leaf drift')
        if original[name] != read_artifact(root / name):
            raise ValueError('correction source repair authenticated response drift')
    if not (root / RAW).exists() and any(root.glob('raw-final-review*')):
        raise ValueError('correction source repair final already issued')
    if (correction_packet.get('own_source_bodies') or correction_packet.get('correction_source_context')
            or review_packet.get('own_source_bodies') != sources
            or review_packet['packet'] != correction_packet['packet']
            or review_packet['proposal'] != correction_packet['proposal']
            or review_packet['proposal_sha256'] != correction_packet['proposal_sha256']):
        raise ValueError('correction source repair requires exact omitted complete input')
    if (original['raw-review.json']['request_payload_sha256'] != sha256_json(review_packet)
            or original['raw-reconsideration.json']['request_payload_sha256'] != sha256_json(correction_packet)):
        raise ValueError('correction source repair original actual request differs')
    # Repair this input domain only. A rejection of a changed existing link,
    # source/Claim issue or already terminal postimage cannot acquire a retry.
    findings = [r for r in review.change_reviews if r.decision != 'pass']
    dispositions = reconsideration.finding_dispositions
    if (len(findings) != 1 or len(dispositions) != 1
            or dispositions[0].disposition != 'rebutted'
            or (findings[0].claim_id, findings[0].component_index) !=
               (dispositions[0].claim_id, dispositions[0].component_index)):
        raise ValueError('correction source repair requires one unresolved new component')
    finding = findings[0]
    if finding.decision != 'correct':
        raise ValueError('correction source repair cannot retry a rejected postimage')
    decisions = [x for x in proposal.claim_decisions if x.claim_id == finding.claim_id]
    if len(decisions) != 1:
        raise ValueError('correction source repair component identity missing')
    component = decisions[0].components[finding.component_index]
    if component.disposition != 'new_viewpoint' or component.target_viewpoint_revision_id:
        raise ValueError('correction source repair cannot reopen retained target')
    if (any(r.decision != 'pass' for r in review.revision_reviews)
            or any(r.decision != 'pass' for r in review.relation_reviews)
            or review.novelty_review.status != 'pass'
            or any(x.disposition != 'accepted' for x in reconsideration.structure_dispositions)):
        raise ValueError('correction source repair has another unresolved domain')
    receipt = {'schema_version': REPAIR, 'authorization': auth,
        'original_answers': original, 'complete_initial_source_sha256': sha256_json(sources),
        'original_correction_had_complete_source': False,
        'new_engineering_calls': 1, 'additional_corrections': 0,
        'human_content_approval': False, 'original_verdicts_unchanged': True,
        'write_authority': False}
    return complete_packet(correction_packet, sources, receipt)


def enable(store, authorization, execution, campaign):
    from backend.pipeline.cvp_sql_commit import check_binding
    check_binding(authorization)
    d = read_artifact(authorization['path'])
    run = store.get_cvp_group(d['run_id'])
    expected = Path(execution) / d['run_id'] / 'normal-review'
    if (not run or run['campaign_sha256'] != campaign or d.get('campaign_sha256') != campaign
            or Path(d['normal_root']).resolve() != expected.resolve()
            or d.get('materials_sha256') != sha256_json(run['materials'])
            or (run['change_set_ids'] and not (expected / BINDING).exists())):
        raise ValueError('correction source repair SQL parent/root/prewrite drift')
    if not run['change_set_ids']:
        from backend.pipeline.cvp_sql_commit import decoded_pins
        with store.connect() as conn, conn.cursor() as cursor:
            store._assert_transaction_read_pins(cursor, decoded_pins(run['materials']['read_pins']))
    write_immutable(expected / BINDING, {'authorization': authorization})
    write_immutable(Path(execution) / BINDING, {'authorization': authorization})
