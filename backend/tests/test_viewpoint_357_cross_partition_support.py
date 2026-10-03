from copy import deepcopy

import pytest

from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha, sha256_json
from backend.pipeline.viewpoint_357_cross_partition_support import (
    CLAIM, EXEGESIS, role_projection, validate_future_candidate, occurrence_ref,
    mark_proposed, exact_evidence,
    approved_package,
    validate_closure_review,
    repair_purpose_locator, PURPOSE_LINK,
    verify_review_artifacts,
    add_major_qualifier,
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
    link = {'decision_id':'VID-old', 'revision':2, 'review_status':'system_approved', 'effective_state':'retired' if action=='retire' else 'active', 'viewpoint_claim_link_id':'VCL-one', 'viewpoint_id':'CV-one', 'claim_id':CLAIM, 'link_type':'supports'}
    plan = mark_proposed(link, action, 'amendment')
    assert link['decision_id'] != 'VID-old'
    assert link['review_status'] == 'candidate'
    assert link['effective_state'] == ('retired' if action=='retire' else 'invalidated')
    assert plan['intended_effective_state_after_review'] == ('retired' if action=='retire' else 'active')
    assert plan['supersedes_decision_id']=='VID-old'


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


@pytest.mark.parametrize('bad',[None,'claim_text','claim_sha','old_locator','revision','claim_id'])
def test_purpose_locator_restores_original_connective_without_other_semantic_edits(bad):
    claim={'claim_id':'DK-61d92ac2a5fb-P03-CL003','revision':3,
           'statement':'耶穌接受約翰的洗，不是因為祂需要悔改，而是為了盡諸般的義並與人類站在一起。'}
    link={'viewpoint_claim_link_id':PURPOSE_LINK,'claim_id':claim['claim_id'],'revision':1,
          'pinned_claim_revision':3,'link_type':'equivalent_component','effective_state':'active',
          'evidence_bindings':[{'evidence_step_id':'E004','source_fragment_id':'F004'}],
          'component_locator':{'claim_sha256':semantic_record_sha(claim),
            'statement_component':'耶穌接受約翰的洗與人類站在一起。',
            'canonical_spans':[{'start_char':0,'end_char':8,'exact_text':'耶穌接受約翰的洗'},
                               {'start_char':29,'end_char':37,'exact_text':'與人類站在一起。'}]}}
    if bad=='claim_text': claim['statement']+='新增'
    if bad=='claim_sha': link['component_locator']['claim_sha256']='wrong'
    if bad=='old_locator': link['component_locator']['canonical_spans'][0]['end_char']=7
    if bad=='revision': link['revision']=2
    if bad=='claim_id': claim['claim_id']='another'
    if bad:
        with pytest.raises(ValueError): repair_purpose_locator(link,claim)
        return
    before=deepcopy((link,claim)); after=repair_purpose_locator(link,claim)
    assert (link,claim)==before
    assert after['component_locator']['statement_component']=='耶穌接受約翰的洗為了與人類站在一起。'
    assert after['component_locator']['canonical_spans'][1]=={'start_char':21,'end_char':23,'exact_text':'為了'}
    assert {k:v for k,v in after.items() if k not in {'revision','component_locator'}}=={k:v for k,v in link.items() if k not in {'revision','component_locator'}}


@pytest.mark.parametrize('bad',[None,'raw_sha','response','model','request','accepted'])
def test_apply_binds_actual_independent_raw_reviews(tmp_path,bad):
    import json
    packet={'exact_packet':'source-bound'}; reviews=[{'verdicts':[]},{'verdicts':[]}]
    hashes=[]
    for label,backend,model,review in zip(['gpt-review','opus-review'],['codex','claude'],['gpt-6.1-sol','claude-opus-5-5'],reviews):
        raw={'backend':backend,'model_id':model,'response':review,'response_sha256':sha256_json(review),
             'request_payload_sha256':sha256_json(packet)}
        raw['artifact_sha256']=sha256_json(raw); hashes.append(raw['artifact_sha256'])
        if label=='opus-review':
            if bad=='raw_sha': raw['artifact_sha256']='wrong'
            if bad=='response': raw['response']={'verdicts':['invented']}
            if bad=='model': raw['model_id']='different'
            if bad=='request': raw['request_payload_sha256']='different'
        (tmp_path/('raw-'+label+'.json')).write_text(json.dumps(raw))
    report={'reviews':reviews,'accepted_call_artifact_shas':hashes}
    if bad=='accepted': report['accepted_call_artifact_shas']=['a','b']
    if bad:
        with pytest.raises(ValueError): verify_review_artifacts(packet,report,tmp_path)
    else: verify_review_artifacts(packet,report,tmp_path)


def test_major_qualifier_preserves_positive_scope_without_inserting_negation():
    claim={'claim_id':'DK-b4223cee7ee7-P02-CL002','revision':2,
           'statement':'「義」主要不是指沒有罪，而是好關係以及促進好關係的行為。'}
    link={'viewpoint_claim_link_id':'VCL-8457e53525f0eb1f35c3','revision':1,'claim_id':claim['claim_id'],
          'pinned_claim_revision':2,'component_locator':{'claim_sha256':semantic_record_sha(claim)}}
    before=deepcopy((link,claim)); after=add_major_qualifier(link,claim)
    assert (link,claim)==before
    locator=after['component_locator']; assert locator['statement_component']=='「義」主要是好關係以及促進好關係的行為。'
    assert '不是' not in locator['statement_component']
    assert [(s['start_char'],s['end_char']) for s in locator['canonical_spans']]==[(0,5),(13,28)]
    for s in locator['canonical_spans']: assert claim['statement'][s['start_char']:s['end_char']]==s['exact_text']


def test_composite_identity_has_one_joint_decision_not_fake_full_members(tmp_path):
    from backend.pipeline.viewpoint_357_cross_partition_support import OLD_LINK,COMPOSITE_VIEWPOINT,COMPOSITE_LINKS,bind_joint_identity_proposal
    claims=['C1','C2']; lids=sorted(COMPOSITE_LINKS)
    links=[{'viewpoint_claim_link_id':lid,'claim_id':cid,'viewpoint_id':COMPOSITE_VIEWPOINT,'decision_id':'new-'+cid,
            'revision':2,'link_type':'supports','validated_against_viewpoint_revision_id':'R1'} for lid,cid in zip(lids,claims)]
    plans=[{'link_id':p['viewpoint_claim_link_id'],'action':'revalidate_with_type_change',
            'intended_effective_state_after_review':'active'} for p in links]
    packet={'targets':[{'claim':{'claim_id':cid}} for cid in claims],'proposed_links':links,'proposed_decisions':plans,
            'composite_identity':{'viewpoint_id':COMPOSITE_VIEWPOINT,'joint_link_ids':lids,'joint_claim_ids':claims,
                                  'no_split':True,'core_and_signature_unchanged':True}}
    states={('viewpoint_claim_links',OLD_LINK):{'decision_id':'old'},
            ('viewpoint_identity_decisions','old'):{'identity_candidate_id':'old-candidate'},
            ('viewpoint_identity_candidates','old-candidate'):{},('viewpoint_revisions','R1'):{'viewpoint_revision_id':'R1'}}
    for cid,lid in zip(claims,lids):
        states['claims',cid]={'claim_id':cid,'revision':1}
        states['viewpoint_claim_links',lid]={'decision_id':'old'}
    packet['sources']=[]
    bind_joint_identity_proposal(states,packet)
    reviewed_ids=[p['decision_id'] for p in packet['proposed_links']]
    assert len(set(reviewed_ids))==1
    reviews=[{'verdicts':[{'claim_id':cid,'approved':True,'problems':[]} for cid in claims]} for _ in range(2)]
    report={'approved':True,'packet_sha256':sha256_json(packet),'accepted_call_artifact_shas':['a','b'],'reviews':reviews}
    package=approved_package(states,packet,report,tmp_path)
    assert len(package['viewpoint_claim_links'])==2
    assert len(package['viewpoint_identity_candidates'])==len(package['viewpoint_identity_decisions'])==1
    decision=package['viewpoint_identity_decisions'][0]
    assert decision['identity_decision_id']==reviewed_ids[0]
    assert package['viewpoint_identity_candidates'][0]==packet['composite_identity']['joint_candidate']
    assert all(p['decision_id']==decision['identity_decision_id'] for p in package['viewpoint_claim_links'])
    assert {d['claim_id'] for d in decision['claim_link_decisions']}==set(claims)
    assert all(d['link_type']=='supports' for d in decision['claim_link_decisions'])
    assert package['viewpoint_identity_candidates'][0]['candidate_claim_ids']==claims
    assert not {'canonical_viewpoints','viewpoint_revisions'} & set(package)
    packet['proposed_decisions'][0]['decision_id']='unreviewed-replacement'
    report['packet_sha256']=sha256_json(packet)
    with pytest.raises(ValueError,match='joint identity drift'):
        approved_package(states,packet,report,tmp_path)
