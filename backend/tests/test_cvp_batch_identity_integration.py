from copy import deepcopy
import hashlib
import pytest
from backend.api.canonical_repository.viewpoint_batch_resolution import CanonicalViewpointProposalResponse
from backend.pipeline.cvp_batch_identity_integration import VERSION,parse_and_apply,adapter
from backend.pipeline.viewpoint_batch_resolution_runner import build_consolidation_packet
from backend.api.canonical_repository.viewpoint_resolution import EvidenceSourceContext
from test_viewpoint_batch_resolution import _proposal,_candidate,_claim,_component,ROCK_STATEMENT


def original():
    return _proposal(new_viewpoint_candidates=[_candidate('A'),_candidate('B')],claim_decisions=[
        {'claim_id':cid,'components':[_component(ROCK_STATEMENT,ROCK_STATEMENT,'new_viewpoint',
           claim_id=cid,local_new_viewpoint_key=key)]} for cid,key in [('C1','A'),('C2','B')]])


def answer():
    return {'schema_version':VERSION,'same_batch_groups':[{'member_local_keys':['A','B'],
        'merged_candidate':_candidate('A'),'reason':'自身语境是同一观点'}],
        'verdicts':[{'local_key':'A','verdict':'new','reason':'Registry未收录'}], 'unresolved_items':[]}


def test_same_batch_merge_preserves_all_claim_evidence_and_spans():
    p=original();before=p.model_dump(mode='json');r,q=parse_and_apply(answer(),p,[])
    assert p.model_dump(mode='json')==before
    assert [x.local_key for x in q.new_viewpoint_candidates]==['A']
    expected=deepcopy(before['claim_decisions'])
    expected[1]['components'][0]['local_new_viewpoint_key']='A'
    assert q.model_dump(mode='json')['claim_decisions']==expected
    assert len(q.claim_decisions)==2 and not q.viewpoint_revisions


@pytest.mark.parametrize('change',[
    lambda a:a['same_batch_groups'][0].update(member_local_keys=['A','A']),
    lambda a:a['same_batch_groups'][0].update(member_local_keys=['A','FOREIGN']),
    lambda a:a['same_batch_groups'].append(deepcopy(a['same_batch_groups'][0])),
    lambda a:a['same_batch_groups'][0]['merged_candidate'].update(local_key='NEW'),
    lambda a:a['verdicts'].append({'local_key':'B','verdict':'new','reason':'duplicate'}),
    lambda a:a.update(unresolved_items=[{'local_key':'A','reason':'contradictory'}]),
    lambda a:a['verdicts'][0].update(verdict='matches_existing',target_viewpoint_revision_id='FOREIGN'),
])
def test_group_identity_and_exact_dispositions_fail_closed(change):
    a=answer();change(a)
    with pytest.raises(ValueError):parse_and_apply(a,original(),[])


def test_unresolved_is_not_coerced_to_new_or_pass():
    a={'schema_version':VERSION,'same_batch_groups':[], 'verdicts':[],
       'unresolved_items':[{'local_key':k,'reason':'原文真矛盾'} for k in ('A','B')]}
    r,q=parse_and_apply(a,original(),[])
    assert len(r.unresolved_items)==2 and not r.verdicts and q==original()


def test_group_may_match_one_existing_identity_without_committed_revision():
    a=answer();a['verdicts'][0].update(verdict='matches_existing',target_viewpoint_revision_id='CVR-EXACT')
    r,q=parse_and_apply(a,original(),['CVR-EXACT'])
    from backend.api.canonical_repository.viewpoint_batch_resolution import apply_consolidation,IdentityConsolidationResponse
    result=apply_consolidation(consolidation=IdentityConsolidationResponse(verdicts=r.verdicts),proposal=q)
    assert not result.new_viewpoint_candidates and not result.viewpoint_revisions
    assert all(c.components[0].target_viewpoint_revision_id=='CVR-EXACT' for c in result.claim_decisions)


def test_new_context_keeps_full_paragraph_and_legacy_packet_unchanged():
    c=_claim('C1',ROCK_STATEMENT);p=_proposal()
    text='教授实际限定。逐字片段。后续说明。';quote='逐字片段'
    c.evidence[0].source_context=EvidenceSourceContext(source_id='S1',source_path='/exact/source.json',
        source_file_sha256='a'*64,source_body_sha256='b'*64,paragraph_key='S0001',
        paragraph_text=text,paragraph_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        excerpt_start_char=text.index(quote),excerpt_end_char=text.index(quote)+len(quote))
    legacy=build_consolidation_packet(proposal=p,claims=[c],registry_context=[])
    assert 'source_context' not in legacy['new_viewpoint_candidates'][0]['attested_by'][0]
    current=build_consolidation_packet(proposal=p,claims=[c],registry_context=[],include_source_context=True)
    ctx=current['new_viewpoint_candidates'][0]['attested_by'][0]['source_context'][0]
    assert ctx['paragraph_text']==text and ctx['role']=='resolve_excerpt_context_not_new_evidence'
    assert build_consolidation_packet(proposal=p,claims=[c],registry_context=[])==legacy


def test_new_adapter_keeps_model_backend_and_old_prompt():
    from types import SimpleNamespace
    from backend.pipeline.viewpoint_batch_resolution_runner import _compact_adapter
    from backend.api.canonical_repository.viewpoint_batch_resolution import ContextualIdentityConsolidationResponse
    client=SimpleNamespace(model='claude-opus-5-5',backend='claude_subscription',reasoning_effort='high')
    old=_compact_adapter(client=client,prompt='## 统一语义规则：wang_cvp_contextual_semantics_v2\n保留限定\n## 本阶段任务与输出约束\n旧身份任务',
        response_model=ContextualIdentityConsolidationResponse,schema_name='old')
    original=old.prompt_sha256;new=adapter(old)
    assert new.model_id==old.model_id and new.backend==old.backend
    assert old.prompt_sha256==original and new.prompt_sha256!=original
    assert '旧身份任务' not in new._base_prompt and '保留限定' in new._base_prompt


def flow(tmp_path,monkeypatch,*,unresolved=False,refuse=False,still_unresolved=False):
    import backend.pipeline.cvp_batch_identity_integration as integration
    from backend.pipeline.viewpoint_batch_resolution_runner import run_batch
    from backend.api.canonical_repository.viewpoint_foundation import sha256_json
    from test_viewpoint_batch_resolution import _StubAdapter,_SequenceStubAdapter
    p=original();first={'schema_version':VERSION,'same_batch_groups':[],'verdicts':[],
        'unresolved_items':[{'local_key':key,'reason':'需要完整自身正文确认限定'} for key in ('A','B')]}
    corrected=first if still_unresolved else answer()
    proposer=_SequenceStubAdapter([p.model_dump(mode='json'),corrected])
    consolidator=_StubAdapter(first if unresolved else answer())
    _,q=parse_and_apply(answer(),p,[])
    review={'schema_version':'wang_canonical_viewpoint_review_v1',
        'proposal_sha256':sha256_json(q.model_dump(mode='json')),
        'change_reviews':[{'claim_id':cid,'component_index':0,'decision':'correct' if refuse else 'pass',
            'reason':'完整正文仍有实质分歧' if refuse else '完整自身正文同一观点',
            'finding_codes':['over_merge'] if refuse else [],
            **({'correction':'保留原文真值条件，不能强合'} if refuse else {})} for cid in ('C1','C2')],
        'novelty_review':{'status':'pass','reason':'无遗漏','missed_claim_ids':[]}}
    reviewer=_StubAdapter(review);reconsiderer=_StubAdapter({})
    monkeypatch.setattr(integration,'adapter',lambda a:a)
    kwargs=dict(batch_id=p.batch_id,scope_label='scope',claims=[_claim(cid,ROCK_STATEMENT) for cid in ('C1','C2')],
        registry_context=[],pending_candidates=[],output_dir=tmp_path,
        proposer=proposer,consolidator=consolidator,reviewer=reviewer,reconsiderer=reconsiderer,
        identity_source_loader=lambda ids:[{'source_id':'S1','paragraph_texts':['完整自身正文，不是标题']}])
    return integration,run_batch,kwargs,proposer,consolidator,reviewer,reconsiderer


def test_full_flow_same_batch_merge_empty_registry_and_zero_replay(tmp_path,monkeypatch):
    mod,run,kwargs,p,c,r,cor=flow(tmp_path,monkeypatch)
    token=mod.ENABLED.set(True)
    try:
        result=run(**kwargs);assert result['outcome']=='pass'
        assert [x.local_key for x in result['_effective_proposal'].new_viewpoint_candidates]==['A']
        run(**kwargs)
        assert (p.calls,c.calls,r.calls,cor.calls)==(1,1,1,0)
        assert r.payloads[0]['same_batch_identity_integration']['own_source_bodies'][0]['paragraph_texts']
    finally:mod.ENABLED.reset(token)


def test_identity_unresolved_consumes_only_shared_single_correction(tmp_path,monkeypatch):
    mod,run,kwargs,p,c,r,cor=flow(tmp_path,monkeypatch,unresolved=True,refuse=True)
    token=mod.ENABLED.set(True)
    try:
        with pytest.raises(ValueError,match='no second correction'):run(**kwargs)
        assert (p.calls,c.calls,r.calls,cor.calls)==(2,1,1,0)
        assert p.payloads[1]['own_source_bodies'][0]['paragraph_texts']
        with pytest.raises(ValueError,match='no second correction'):run(**kwargs)
        assert (p.calls,c.calls,r.calls,cor.calls)==(2,1,1,0)
    finally:mod.ENABLED.reset(token)


def test_still_unresolved_stops_before_full_review_and_writes(tmp_path,monkeypatch):
    mod,run,kwargs,p,c,r,cor=flow(tmp_path,monkeypatch,unresolved=True,still_unresolved=True)
    token=mod.ENABLED.set(True)
    try:
        with pytest.raises(ValueError,match='after sole correction'):run(**kwargs)
        assert (p.calls,c.calls,r.calls,cor.calls)==(2,1,0,0)
        assert not (tmp_path/'proposal.json').exists()
    finally:mod.ENABLED.reset(token)
