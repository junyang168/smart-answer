from copy import deepcopy
from contextlib import contextmanager

import pytest

from backend.api.canonical_repository.cvp_recall import (
    assert_current_index, build_index, merge_recall, recall_plan, work_groups,
)
from backend.api.canonical_repository.postgres_store import (
    ChangeSetConflict, PostgresKnowledgeStore, record_content_sha,
)


def heads(n=3):
    return [{'viewpoint_id':f'CV-{i}','viewpoint_revision_id':f'CVR-{i}',
             'head_sha256':str(i)*64,'core_proposition':f'观点{i}',
             'scope':{'scripture_scope':['太9']},'proposition_signature':{'subject':'人子'}}
            for i in range(1,n+1)]


def index(n=3):
    rows=heads(n)
    return build_index(rows,[{**{k:r[k] for k in ('viewpoint_id','viewpoint_revision_id','head_sha256')},
                             'summary':f'摘要{r["viewpoint_id"]}'} for r in rows])


def claims(n=2):
    return [{'claim_id':f'CL-{i}','statement':f'判断{i}，包含必要限定。','attribution':'professor'}
            for i in range(n)]


def replies(plan):
    return [{'claim_candidates':[{'claim_ref':c,'cvp_refs':[p['viewpoints'][0]['cvp_ref']]}
                                 for c in plan['claim_refs']]} for p in plan['shards']]


def test_existing_grouping_keeps_accepted_order_and_has_exact_coverage():
    grouping={'scope_label':'人子','groups':[
        {'group_key':'second-name','claim_ids':['B','A'],'rationale':'比较'},
        {'group_key':'first-name','claim_ids':['C'],'rationale':'其他'}]}
    assert work_groups(grouping,['A','B','C'])==[
        {'group_key':'second-name','claim_ids':['B','A']},
        {'group_key':'first-name','claim_ids':['C']}]


@pytest.mark.parametrize('kind',['missing','duplicate','foreign','oversize'])
def test_grouping_rejects_bad_cohort_or_more_than_twenty(kind):
    ids=['C'+str(i) for i in range(21)] if kind=='oversize' else ['A','B']
    grouped=list(ids)
    if kind=='missing':grouped.pop()
    if kind=='duplicate':grouped.append(ids[0])
    if kind=='foreign':grouped[-1]='Z'
    with pytest.raises(ValueError):
        work_groups({'scope_label':'s','groups':[{'group_key':'g','claim_ids':grouped,'rationale':'r'}]},ids)


@pytest.mark.parametrize('kind',['missing','foreign','stale','too_long','empty','duplicate'])
def test_index_requires_all_heads_and_valid_bound_summaries(kind):
    hs=heads(2);summaries=[{**h,'summary':'摘要'} for h in hs]
    if kind=='missing':summaries.pop()
    if kind=='foreign':summaries[0]['viewpoint_id']='foreign'
    if kind=='stale':summaries[0]['head_sha256']='changed'
    if kind=='too_long':summaries[0]['summary']='字'*150
    if kind=='empty':summaries[0]['summary']='  '
    if kind=='duplicate':summaries.append(summaries[0])
    with pytest.raises(ValueError):build_index(hs,summaries)


@pytest.mark.parametrize('kind',['new','retire','revision','statement','scope'])
def test_current_index_detects_every_material_head_change(kind):
    hs=heads();changed=deepcopy(hs)
    if kind=='new':changed.extend(heads(4)[3:])
    if kind=='retire':changed.pop()
    if kind=='revision':changed[0]['head_sha256']='new'
    if kind=='statement':changed[0]['core_proposition']='new'
    if kind=='scope':changed[0]['scope']={'scripture_scope':['太16']}
    assert_current_index(index(),hs)
    with pytest.raises(ValueError):assert_current_index(index(),changed)


def test_recall_shards_keep_full_claims_and_union_without_cutoff():
    original=claims();plan=recall_plan(index(5),original,max_request_bytes=2,
        measure=lambda p:len(p['viewpoints']))
    assert [len(s['viewpoints']) for s in plan['shards']]==[2,2,1]
    assert all([c['statement'] for c in s['claims']]==[c['statement'] for c in original]
               for s in plan['shards'])
    result=merge_recall(plan,replies(plan),existing_links=[
        {'claim_id':'CL-0','viewpoint_id':'CV-2','effective_state':'active'},
        {'claim_id':'CL-1','viewpoint_id':'CV-4','effective_state':'retired'}])
    assert result['claim_candidates'][0]['viewpoint_ids']==['CV-1','CV-2','CV-3','CV-5']
    assert result['claim_candidates'][1]['viewpoint_ids']==['CV-1','CV-3','CV-5']
    assert result['identity_decided'] is False


def test_empty_registry_needs_zero_models_but_preserves_claim_rows():
    plan=recall_plan(index(0),claims(),max_request_bytes=100)
    assert plan['shards']==[]
    assert merge_recall(plan,[])['claim_candidates']==[
        {'claim_id':'CL-0','viewpoint_ids':[]},{'claim_id':'CL-1','viewpoint_ids':[]}]


@pytest.mark.parametrize('kind',['missing_shard','missing_claim','duplicate_claim','cross_shard',
                                 'foreign_claim','duplicate_cvp','extra_field','missing_existing'])
def test_incomplete_or_invalid_recall_cannot_be_new_viewpoint_permission(kind):
    plan=recall_plan(index(),claims(),max_request_bytes=1,measure=lambda p:len(p['viewpoints']))
    raw=replies(plan);links=[]
    if kind=='missing_shard':raw.pop()
    if kind=='missing_claim':raw[0]['claim_candidates'].pop()
    if kind=='duplicate_claim':raw[0]['claim_candidates'].append(raw[0]['claim_candidates'][0])
    if kind=='cross_shard':raw[0]['claim_candidates'][0]['cvp_refs']=['v2']
    if kind=='foreign_claim':raw[0]['claim_candidates'][0]['claim_ref']='other'
    if kind=='duplicate_cvp':raw[0]['claim_candidates'][0]['cvp_refs']=['v1','v1']
    if kind=='extra_field':raw[0]['claim_candidates'][0]['approve']=True
    if kind=='missing_existing':links=[{'claim_id':'CL-0','viewpoint_id':'absent','effective_state':'active'}]
    with pytest.raises(ValueError):merge_recall(plan,raw,existing_links=links)


def test_recall_preserves_extra_bound_qualifications_and_cannot_override_short_reference():
    cs=claims();cs[0].update(conditions=['除非不是'],claim_ref='forged')
    plan=recall_plan(index(),cs,max_request_bytes=10000)
    assert plan['shards'][0]['claims'][0]['conditions']==['除非不是']
    assert plan['shards'][0]['claims'][0]['claim_ref']=='c1'


def test_one_oversized_card_or_claim_group_fails_before_a_partial_plan():
    with pytest.raises(ValueError,match='capacity'):
        recall_plan(index(),claims(),max_request_bytes=1)
    with pytest.raises(ValueError,match='20'):
        recall_plan(index(),claims(21),max_request_bytes=100000)


def test_large_index_does_not_reencode_every_growing_prefix():
    measured=[]
    def measure(p):
        measured.append(len(p['viewpoints']))
        return len(p['viewpoints'])
    plan=recall_plan(index(2000),claims(),max_request_bytes=800,measure=measure)
    assert [len(s['viewpoints']) for s in plan['shards']]==[800,800,400]
    assert len(measured)<70


def test_public_head_reader_uses_one_current_select_and_rejects_foreign_heads():
    view={'viewpoint_id':'CV-1','identity_status':'active','current_revision_id':'CVR-1'}
    head={'viewpoint_id':'CV-1','viewpoint_revision_id':'CVR-1','core_proposition':'命题',
          'scope':{},'proposition_signature':{}}
    rows=[('CV-1',view,1,record_content_sha(view),'CVR-1',head,1,record_content_sha(head),None)]
    queries=[]
    class Store:
        @contextmanager
        def connect(self):yield self
        def execute(self,sql):
            queries.append(sql);return self
        def fetchall(self):return rows
    result=PostgresKnowledgeStore.read_cvp_heads(Store())
    assert len(queries)==1 and 'object_versions' not in queries[0]
    assert result[0]['core_proposition']=='命题'
    head['viewpoint_id']='foreign'
    with pytest.raises(ChangeSetConflict):PostgresKnowledgeStore.read_cvp_heads(Store())
