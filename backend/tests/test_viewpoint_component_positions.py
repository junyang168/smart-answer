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
