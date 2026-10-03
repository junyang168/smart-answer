from copy import deepcopy

import pytest

from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha, sha256_json
from backend.pipeline.viewpoint_357_cross_partition_support import (
    CLAIM, EXEGESIS, role_projection, validate_future_candidate, occurrence_ref,
    mark_proposed, exact_evidence,
    approved_package,
    validate_closure_review,
)


def fixture():
    claim = {'claim_id': CLAIM, 'revision': 1, 'statement': '義是與神建立好關係，並需要具體行動。'}
    exegesis = {'viewpoint_id': EXEGESIS, 'viewpoint_revision_id': 'CVR-exegesis', 'core_proposition': '約翰施洗促進自己與神好關係。'}
    theology = {'viewpoint_id': 'CV-theology', 'viewpoint_revision_id': 'CVR-theology', 'core_proposition': claim['statement']}
    membership = {'claim_id': CLAIM, 'pinned_claim_revision': 1, 'viewpoint_id': 'CV-theology',
                  'validated_against_viewpoint_revision_id': 'CVR-theology', 'effective_state': 'active',
                  'link_type': 'equivalent_full', 'component_locator': None,
                  'review_status': 'system_approved', 'decision_id': 'VID-reviewed'}
    contract = {'claim_id': CLAIM, 'claim_revision': 1, 'claim_semantic_sha256': semantic_record_sha(claim),
                'exegesis_viewpoint_id': EXEGESIS, 'exegesis_revision_id': 'CVR-exegesis',
                'exegesis_revision_sha256': sha256_json(exegesis), 'source_bindings': []}
    return contract, claim, exegesis, theology, membership


def test_future_resolution_is_only_candidate_and_direction_is_application():
    args = fixture(); before = deepcopy(args)
    result = validate_future_candidate(*args)
    assert args == before
    assert result['source_viewpoint_id'] == EXEGESIS
    assert result['target_viewpoint_id'] == 'CV-theology'
    assert result['relation_type'] == 'applies'
    assert result['apply_authorization'] is False
    assert 'requires_semantic_review_and_independent_audit' in result['status']


@pytest.mark.parametrize('bad', ['claim_revision', 'claim_text', 'exegesis', 'partial', 'supports', 'unreviewed', 'retired', 'target', 'target_revision', 'decision'])
def test_future_connection_rejects_drift_or_topic_only_identity(bad):
    contract, claim, exegesis, theology, membership = fixture()
    if bad == 'claim_revision': claim['revision'] = 2
    if bad == 'claim_text': claim['statement'] += '新增内容'
    if bad == 'exegesis': exegesis['core_proposition'] += '变化'
    if bad == 'partial': membership.update(link_type='equivalent_component', component_locator={'statement_component': '義'})
    if bad == 'supports': membership['link_type'] = 'supports'
    if bad == 'unreviewed': membership['review_status'] = 'candidate'
    if bad == 'retired': membership['effective_state'] = 'retired'
    if bad == 'target': membership['viewpoint_id'] = 'CV-unrelated'
    if bad == 'target_revision': membership['validated_against_viewpoint_revision_id'] = 'CVR-old'
    if bad == 'decision': membership['decision_id'] = ''
    with pytest.raises(ValueError): validate_future_candidate(contract, claim, exegesis, theology, membership)


def test_source_drift_blocks_future_resolution(tmp_path):
    from backend.api.canonical_repository.viewpoint_production_safety import file_sha256
    args = list(fixture()); source = tmp_path/'source.json'; source.write_text('original')
    args[0]['source_bindings'] = [{'path': str(source), 'file_sha256': file_sha256(source)}]
    source.write_text('changed')
    with pytest.raises(ValueError, match='physical source changed'): validate_future_candidate(*args)


@pytest.mark.parametrize('dictionary', [True, False])
def test_projection_transfers_unique_owner_and_keeps_history(dictionary):
    rows = [{'claim_id': CLAIM, 'role': 'passage_exegesis', 'primary': {'role': 'passage_exegesis'}}, {'claim_id':'other', 'role':'other'}]
    ledger = {'artifact_sha256':'role-sha', 'decisions':{r['claim_id']:r for r in rows} if dictionary else rows}
    manifest = {'artifact_sha256':'partition-sha', 'owners':{'other':{'partition':'other'}}, 'preserved_relations':[{'relation_id':'edge','effective_state':'candidate'}]}
    amendment = {'bindings':{'role_ledger':{'artifact_sha256':'role-sha'}, 'partition_manifest':{'artifact_sha256':'partition-sha'}}, 'artifact_sha256':'amendment-sha', 'claim_revision':1, 'statement_sha256':'statement-sha'}
    before = deepcopy((ledger, manifest))
    result = role_projection(ledger, manifest, amendment)
    assert (ledger, manifest) == before
    output_rows = list(result['decisions'].values()) if dictionary else result['decisions']
    row = next(r for r in output_rows if r['claim_id'] == CLAIM)
    assert row['role'] == 'other' and row['primary']['role'] == 'passage_exegesis'
    assert result['owners'][CLAIM]['partition'] == 'salvation'
    assert result['preserved_relations'] == manifest['preserved_relations']
    assert result['database_mutations'] == 0 and not result['grouping_authorization']


def test_projection_rejects_duplicate_owner():
    ledger = {'artifact_sha256':'role', 'decisions':{CLAIM:{'claim_id':CLAIM,'role':'passage_exegesis'}}}
    manifest = {'artifact_sha256':'manifest', 'owners':{CLAIM:{'partition':'salvation'}}}
    amendment = {'bindings':{'role_ledger':{'artifact_sha256':'role'}, 'partition_manifest':{'artifact_sha256':'manifest'}}}
    with pytest.raises(ValueError, match='duplicate'): role_projection(ledger, manifest, amendment)


def test_occurrence_uses_canonical_batch_seed_and_deduplicates_pairs():
    _, claim, _, _, _ = fixture()
    bindings = [{'evidence_step_id':'E3','source_fragment_id':'F3'}, {'evidence_step_id':'E6','source_fragment_id':'F6'}]
    expected = 'OCC-'+sha256_json({'claim_id':claim['claim_id'], 'claim_revision_sha256':semantic_record_sha(claim), 'evidence':[('E3','F3'),('E6','F6')]})[:20]
    assert occurrence_ref(claim, bindings+bindings) == expected
    assert occurrence_ref(claim, bindings[:1]) != expected


@pytest.mark.parametrize('action', ['supports','revalidate','retire'])
def test_proposed_decision_never_claims_existing_approval(action):
    link = {'decision_id':'VID-old', 'review_status':'system_approved', 'effective_state':'retired' if action=='retire' else 'active', 'viewpoint_claim_link_id':'VCL-one', 'viewpoint_id':'CV-one', 'claim_id':CLAIM, 'link_type':'supports'}
    plan = mark_proposed(link, action, 'amendment')
    assert link['decision_id'] != 'VID-old'
    assert link['review_status'] == 'candidate'
    assert link['effective_state'] == ('retired' if action=='retire' else 'invalidated')
    assert plan['intended_effective_state_after_review'] == ('retired' if action=='retire' else 'active')


@pytest.mark.parametrize('failure', [None, 'foreign', 'reciprocity', 'excerpt', 'physical'])
def test_expand_previously_unlinked_evidence_with_physical_checks(tmp_path, failure):
    from backend.api.canonical_repository.viewpoint_production_safety import file_sha256
    source = tmp_path/'source.json'; source.write_text('source')
    sources = [{'source_id':'SRC-one','path':str(source),'file_sha256':file_sha256(source),'complete_original':['神差遣約翰為人施洗。']}]
    claim = {'claim_id':CLAIM,'evidence_step_ids':['E6']}
    step = {'evidence_step_id':'E6','produced_claim_ids':[CLAIM],'source_fragment_ids':['F6']}
    fragment = {'fragment_id':'F6','source_id':'SRC-one','paragraph_key':'S0001','verbatim_excerpt':'神差遣約翰為人施洗。'}
    states = {('evidence_steps','E6'):step,('source_fragments','F6'):fragment}
    if failure == 'foreign': claim['evidence_step_ids'] = []
    if failure == 'reciprocity': step['produced_claim_ids'] = []
    if failure == 'excerpt': fragment['verbatim_excerpt'] = '神没有讲过的话'
    if failure == 'physical': source.write_text('changed')
    if failure:
        with pytest.raises(ValueError): exact_evidence(states,claim,sources,['E6'])
    else:
        before = deepcopy(states)
        assert exact_evidence(states,claim,sources,['E6'])[0]['fragment'] == fragment
        assert states == before


@pytest.mark.parametrize('problem', ['unapproved','packet','one_call','findings'])
def test_final_package_rejects_unapproved_or_incomplete_review(problem, tmp_path):
    packet = {'targets':[{'claim':{'claim_id':CLAIM}}]}
    report = {'approved':True,'packet_sha256':sha256_json(packet),'accepted_call_artifact_shas':['a','b'],
              'reviews':[{'verdicts':[{'claim_id':CLAIM,'approved':True,'problems':[]}]}]*2}
    if problem == 'unapproved': report['approved'] = False
    if problem == 'packet': report['packet_sha256'] = 'wrong'
    if problem == 'one_call': report['accepted_call_artifact_shas'] = ['a','a']
    if problem == 'findings': report['reviews'][0]['verdicts'][0]['problems'] = ['unresolved']
    with pytest.raises(ValueError): approved_package({},packet,report,tmp_path)


@pytest.mark.parametrize('bad', [None, 'missing_link', 'duplicate_link', 'missing_dependency', 'extra_dependency'])
def test_whole_group_requires_actual_complete_nonduplicate_review_coverage(bad):
    packet = {'group_closure':{'required_link_ids':['L1','L2'],'required_dependency_ids':['D1']}}
    review = {'checked_link_ids':['L2','L1'],'checked_dependency_ids':['D1'],'closure_problems':[]}
    if bad=='missing_link': review['checked_link_ids']=['L1']
    if bad=='duplicate_link': review['checked_link_ids']=['L1','L2','L2']
    if bad=='missing_dependency': review['checked_dependency_ids']=[]
    if bad=='extra_dependency': review['checked_dependency_ids']+=['D2']
    if bad:
        with pytest.raises(ValueError,match='coverage'): validate_closure_review(review,packet)
    else:
        assert validate_closure_review(review,packet)
        review['closure_problems']=['unresolved neighbouring connection']
        assert not validate_closure_review(review,packet)
