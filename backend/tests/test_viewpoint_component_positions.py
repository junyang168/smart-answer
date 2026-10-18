from copy import deepcopy
from types import SimpleNamespace
import pytest

from backend.api.canonical_repository.viewpoint_component_positions import raw_claim_text, validated_field_spans
from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha, validate_foundation_change_set, ViewpointFoundationValidationError
from backend.api.canonical_repository.knowledge_models import ViewpointClaimLinkRecord, ClaimRecord
from backend.api.canonical_repository.viewpoint_runtime_projection import ViewpointRuntimeCompiler, ViewpointRuntimeProjectionError
from backend.tests.test_viewpoint_component_roles import audit, graph, findings, independent
from backend.tests.test_viewpoint_admin_projection import _fixture


def locator(claim, path, text=None):
    text = text if text is not None else raw_claim_text(claim,path)
    return {'input_sha256':'a'*64, 'claim_sha256':semantic_record_sha(claim),
            'spans':[{'path':path,'start_char':0,'end_char':len(text),'exact_text':text}]}


def test_raw_unicode_nested_paths_and_path_aware_overlap():
    claim = {'revision':3,'statement':'標題','qualifications':['😀人子','😀人子']}
    loc = locator(claim,['qualifications',0])
    loc['spans'].append(deepcopy(loc['spans'][0]));loc['spans'][1]['path']=['qualifications',1]
    assert len(validated_field_spans(loc,claim,claim_sha=semantic_record_sha)) == 2
    assert validated_field_spans(loc,claim,claim_sha=semantic_record_sha) == audit._field_component_spans(loc,claim)


@pytest.mark.parametrize('fault',['sha','input_sha','path','bool_path','nontext','utf16_offset','text','duplicate'])
def test_both_independent_readers_reject_bad_fields_without_statement_fallback(fault):
    claim={'statement':'😀人子','qualifications':['😀人子'],'revision':1}
    loc=locator(claim,['qualifications',0])
    if fault=='sha':loc['claim_sha256']='b'*64
    elif fault=='input_sha':loc['input_sha256']='unbound'
    elif fault=='path':loc['spans'][0]['path']=['missing']
    elif fault=='bool_path':loc['spans'][0]['path']=['qualifications',True]
    elif fault=='nontext':loc['spans'][0]['path']=['qualifications']
    elif fault=='utf16_offset':loc['spans'][0]['end_char']+=1
    elif fault=='text':loc['spans'][0]['exact_text']='人子'
    else:loc['spans']*=2
    for validate in [lambda:validated_field_spans(loc,claim,claim_sha=semantic_record_sha),lambda:audit._field_component_spans(loc,claim)]:
        with pytest.raises(ValueError):validate()


def test_mixed_component_roles_distinguish_same_offsets_in_different_fields():
    g=graph();links,claims,_,_=g;claim=claims['C'];claim['qualifications']=['同文','同文']
    for i,link in enumerate(links):
        link.pop('component_locator');link['schema_version']='wang_viewpoint_claim_link_v2'
        link['component_position_locator']=locator(claim,['qualifications',i])
    assert findings(g) == independent(g) == []
    links[1]['component_position_locator']=deepcopy(links[0]['component_position_locator'])
    assert findings(g) and independent(g)


def foundation_graph(paths):
    claim={'claim_id':'C','revision':1,'statement':'主張','qualifications':['同文','同文']}
    existing={('claims','C'):{'payload':claim}}
    links={}
    for i,path in enumerate(paths):
        vid=f'V{i}';rid=f'R{i}';did=f'D{i}'
        existing['canonical_viewpoints',vid]={'payload':{'current_revision_id':rid}}
        existing['viewpoint_revisions',rid]={'payload':{'viewpoint_id':vid}}
        existing['viewpoint_identity_decisions',did]={'payload':{'resolved_viewpoint_id':vid,'review_status':'approved',
            'claim_link_decisions':[{'claim_id':'C','link_type':'equivalent_component'}]}}
        links[f'L{i}']={'viewpoint_claim_link_id':f'L{i}','claim_id':'C','viewpoint_id':vid,'pinned_claim_revision':1,
            'validated_against_viewpoint_revision_id':rid,'decision_id':did,'review_status':'approved',
            'effective_state':'active','link_type':'equivalent_component','schema_version':'wang_viewpoint_claim_link_v2',
            'component_position_locator':locator(claim,path)}
    return {'viewpoint_claim_links':links},existing


def test_foundation_component_ownership_includes_field_path():
    changes,existing=foundation_graph([['qualifications',0],['qualifications',1]])
    validate_foundation_change_set(changes,existing)
    changes['viewpoint_claim_links']['L1']['component_position_locator']['spans'][0]['path']=['qualifications',0]
    with pytest.raises(ViewpointFoundationValidationError,match='multiple active viewpoint owners'):
        validate_foundation_change_set(changes,existing)


def test_duplicate_same_field_not_hidden_by_different_packet_sha():
    changes,existing=foundation_graph([['qualifications',0]])
    link=deepcopy(changes['viewpoint_claim_links']['L0']);link['viewpoint_claim_link_id']='L1'
    link['component_position_locator']['input_sha256']='b'*64;changes['viewpoint_claim_links']['L1']=link
    with pytest.raises(ViewpointFoundationValidationError,match='duplicate active Claim links'):
        validate_foundation_change_set(changes,existing)


@pytest.mark.parametrize('fault',[None,'wrong_sha','bad_path','wrong_v1_schema'])
def test_independent_locator_audit_handles_explicit_v2(fault):
    changes,existing=foundation_graph([['qualifications',0]])
    link=changes['viewpoint_claim_links']['L0'];claim=existing['claims','C']['payload']
    if fault=='wrong_sha':link['component_position_locator']['claim_sha256']='b'*64
    elif fault=='bad_path':link['component_position_locator']['spans'][0]['path']=['missing']
    elif fault=='wrong_v1_schema':link['schema_version']='wang_viewpoint_claim_link_v1'
    store=SimpleNamespace(collection=lambda col:[{'object_id':'C','payload':claim}] if col=='claims' else [{'object_id':'L0','payload':link}])
    result=audit._audit_component_locators(store)
    assert bool(result) == (fault is not None)
    if fault:assert result[0]['verdict']=='invalid_field_component_locator'


def test_projection_preserves_field_locator_and_rejects_stale_field_sha():
    store=_fixture();claim=store.records['claims'][0]
    claim=ClaimRecord.model_validate(claim.model_dump(mode='json')|{'qualifications':['限定😀']})
    store.records['claims'][0]=claim
    index=next(i for i,l in enumerate(store.records['viewpoint_claim_links']) if l.claim_id==claim.claim_id)
    old=store.records['viewpoint_claim_links'][index]
    data=old.model_dump(mode='json');data.update(schema_version='wang_viewpoint_claim_link_v2',link_type='equivalent_component',
        component_locator=None,component_position_locator=locator(claim.model_dump(mode='json'),['qualifications',0]))
    store.records['viewpoint_claim_links'][index]=ViewpointClaimLinkRecord.model_validate(data)
    compiler=ViewpointRuntimeCompiler(store.records,store.citations)
    projection=compiler.compile_projection(consumer_kind='qa_answer',coverage_snapshot_id='CVS-16',viewpoint_ids=['CV-PETER'])
    link=next(l for view in projection.viewpoints for l in view.get('claim_links',[]) if l['claim_id']==claim.claim_id)
    assert link['component_position_locator']['spans'][0]['path']==['qualifications',0]
    assert link['link_type']=='equivalent_component' and link['component_locator'] is None
    data['component_position_locator']['claim_sha256']='b'*64
    store.records['viewpoint_claim_links'][index]=ViewpointClaimLinkRecord.model_validate(data)
    with pytest.raises(ViewpointRuntimeProjectionError,match='Claim SHA'):
        ViewpointRuntimeCompiler(store.records,store.citations).compile_projection(consumer_kind='qa_answer',coverage_snapshot_id='CVS-16')


def test_qa_projection_includes_related_field_support_and_its_dependencies():
    store = _fixture()
    compile_projection = lambda: ViewpointRuntimeCompiler(store.records, store.citations).compile_projection(
        consumer_kind='qa_answer', coverage_snapshot_id='CVS-16', viewpoint_ids=['CV-PETER'])
    before = compile_projection()
    # Keep this supporting Claim and its evidence outside every membership.
    source = store.records['source_documents'][0].model_copy(update={'source_id':'SRC-SUPPORT'})
    fragment = store.records['source_fragments'][0].model_copy(update={
        'fragment_id':'FR-SUPPORT', 'source_id':source.source_id})
    evidence = store.records['evidence_steps'][0].model_copy(update={
        'evidence_step_id':'EV-SUPPORT', 'source_fragment_id':fragment.fragment_id})
    claim = ClaimRecord.model_validate(store.records['claims'][0].model_dump(mode='json') | {
        'claim_id':'CL-SUPPORT', 'qualifications':['這是支持理由而不是等價結論'],
        'evidence_step_ids':[evidence.evidence_step_id]})
    for collection, value in [('source_documents',source), ('source_fragments',fragment),
                              ('evidence_steps',evidence), ('claims',claim)]:
        store.records[collection].append(value)
    link = ViewpointClaimLinkRecord.model_validate({
        **store.records['viewpoint_claim_links'][0].model_dump(mode='json'),
        'viewpoint_claim_link_id':'VCL-SUPPORT', 'claim_id':claim.claim_id,
        'schema_version':'wang_viewpoint_claim_link_v2', 'link_type':'supports',
        'component_locator':None,
        'component_position_locator':locator(claim.model_dump(mode='json'), ['qualifications',0]),
        'evidence_bindings':[{'evidence_step_id':evidence.evidence_step_id,
                              'source_fragment_id':fragment.fragment_id}]})
    store.records['viewpoint_claim_links'].append(link)
    after = compile_projection()
    view = after.viewpoints[0]
    output = next(row for row in view['claim_links'] if row['viewpoint_claim_link_id']=='VCL-SUPPORT')
    assert output['link_type']=='supports'
    assert output['component_position_locator']==link.component_position_locator.model_dump(mode='json')
    assert view['snapshot']['member_link_ids']==before.viewpoints[0]['snapshot']['member_link_ids']
    assert view['snapshot']['member_count']==before.viewpoints[0]['snapshot']['member_count']
    assert 'VCL-SUPPORT' in view['snapshot']['related_claim_link_ids']
    assert after.eligibility==before.eligibility
    assert {('viewpoint_claim_links','VCL-SUPPORT'), ('claims','CL-SUPPORT'),
            ('evidence_steps','EV-SUPPORT'), ('source_fragments','FR-SUPPORT'),
            ('source_documents','SRC-SUPPORT')} <= {
                (row.collection,row.record_id) for row in after.dependency_manifest}
    assert any(row['claim_id']=='CL-SUPPORT' for row in after.expanded_claims)
    assert any(row['evidence_step_id']=='EV-SUPPORT' for row in after.expanded_evidence)
    assert any(row['fragment_id']=='FR-SUPPORT' for row in after.expanded_fragments)
    assert any(row['source_id']=='SRC-SUPPORT' for row in after.expanded_sources)


def test_independent_duplicate_coverage_keys_include_raw_field_path_and_ignore_packet_sha():
    changes,existing=foundation_graph([['qualifications',0],['qualifications',1]])
    collections={'claims':{'C':existing['claims','C']['payload']},'viewpoint_claim_links':changes['viewpoint_claim_links']}
    store=SimpleNamespace(collection=lambda col:[{'object_id':k,'payload':v} for k,v in collections.get(col,{}).items()])
    assert audit._audit_component_locators(store)==[]
    changes['viewpoint_claim_links']['L1']['component_position_locator']=deepcopy(changes['viewpoint_claim_links']['L0']['component_position_locator'])
    changes['viewpoint_claim_links']['L1']['component_position_locator']['input_sha256']='b'*64
    assert audit._audit_component_locators(store)[0]['verdict']=='duplicate_field_component_coverage'


def test_independent_semantic_audit_receives_exact_field_boundary_not_whole_claim(tmp_path):
    import random
    changes,existing=foundation_graph([['qualifications',0]])
    claim=existing['claims','C']['payload'];link=changes['viewpoint_claim_links']['L0']
    collections={'claims':{'C':claim},'viewpoint_claim_links':{'L0':link},
        'canonical_viewpoints':{'V0':{'current_revision_id':'R0'}},
        'viewpoint_revisions':{'R0':{'core_proposition':'同文'}}}
    store=SimpleNamespace(collection=lambda col,**kw:[{'object_id':k,'payload':v} for k,v in collections.get(col,{}).items()])
    captured=[]
    def judge(prompt,schema):
        captured.append(prompt)
        return {'verdict':'same','issue':'none','reason':'Synthetic assertion only','quote':''}
    report=audit.audit_viewpoints(store,audit.SourceIndex([],tmp_path),SimpleNamespace(judge=judge),None,random.Random(1),workers=1)
    assert report['judged']==1 and len(captured)==1
    assert '"path": ["qualifications", 0]' in captured[0]
    assert '被歸入的成分：同文' in captured[0]
    assert '不宣稱整條等價' in captured[0] and 'Claim 欄位語境' in captured[0]


@pytest.mark.parametrize('scenario,allowed',[
    ('different_fields',True),('disjoint_same_field',True),('same_field_overlap',False),
    ('mixed_contract_disjoint',True),('mixed_contract_overlap',False),('different_claim_sha',False),
    ('full_claim',False),('equivalent_component',False),
])
def test_sql_edge_ownership_distinguishes_v2_field_paths_and_preserves_support_only_gate(scenario,allowed):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    links,claims,_,_=graph();claim=claims['C'];claim['qualifications']=['完整限定。']
    left,right=deepcopy(links[0]),deepcopy(links[1])
    for row in (left,right):
        row['link_type']='supports';row.pop('component_locator')
        row['schema_version']='wang_viewpoint_claim_link_v2'
    left['component_position_locator']=locator(claim,['statement'],'結論成立')
    right['component_position_locator']=locator(claim,['qualifications',0])
    if scenario in {'disjoint_same_field','same_field_overlap'}:
        span={'path':['statement'],'start_char':5,'end_char':10,'exact_text':claim['statement'][5:10]}
        right['component_position_locator']={**locator(claim,['statement']),'spans':[span]}
        if scenario=='same_field_overlap':right['component_position_locator']=deepcopy(left['component_position_locator'])
    if scenario.startswith('mixed_contract'):
        left.pop('component_position_locator');left['schema_version']='wang_viewpoint_claim_link_v1'
        left['component_locator']={'claim_sha256':semantic_record_sha(claim),'statement_component':claim['statement'][:4],
            'canonical_spans':[{'start_char':0,'end_char':4,'exact_text':claim['statement'][:4]}]}
        start,end=(5,10) if scenario.endswith('disjoint') else (0,4)
        right['component_position_locator']={**locator(claim,['statement']),
            'spans':[{'path':['statement'],'start_char':start,'end_char':end,'exact_text':claim['statement'][start:end]}]}
    if scenario=='different_claim_sha':right['component_position_locator']['claim_sha256']='b'*64
    if scenario=='full_claim':right.pop('component_position_locator')
    if scenario=='equivalent_component':right['link_type']='equivalent_component'
    assert PostgresKnowledgeStore._disjoint_support_components(left,right) is allowed
