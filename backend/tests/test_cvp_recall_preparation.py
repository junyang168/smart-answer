from copy import deepcopy
from pathlib import Path

import pytest

from backend.api.canonical_repository.cvp_recall import seal
from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.pipeline.cvp_recall_preparation import VERSION, prepare_recall
from test_cvp_recall import heads, index

POLICY=Path(__file__).resolve().parents[1]/'pipeline/policies/wang_cvp_resolution_policy_v6.json'
INPUT_POLICY=POLICY.parent/'wang_cvp_claim_input_v1.json'


class Store:
    def __init__(self,n=21,views=3):
        self.rows={}
        self.quals={}
        self.heads=heads(views)
        self.verified=[]
        for i in range(n):
            cid=f'C{i}'
            payload={'claim_id':cid,'statement':'人子的判断，附带完整限定。',
                     'review_status':'ai_reviewed','conditions':['未到时候'],
                     'limitations':{'keep':'完整'},'evidence_step_ids':['E1']}
            self.rows[cid]={'payload':payload,'revision':2,'content_sha256':record_content_sha(payload),
                            'retired':False}
            self.quals[cid]={'ready':True,'revision':2,'content_sha256':record_content_sha(payload),
                            'validation_kind':'opus_and_program_readback',
                            'independent_audit_complete':False}
    def get_record_state(self,collection,cid):
        assert collection=='claims'
        return deepcopy(self.rows.get(cid))
    def claim_management_qualification(self,cid):return deepcopy(self.quals.get(cid))
    def read_cvp_heads(self):return deepcopy(self.heads)
    def read_cvp_links(self,**kwargs):return {}
    def verify_current_read_pins(self,pins):
        self.verified.append(pins)
        for (_,cid),pin in pins.items():
            row=self.rows[cid]
            assert pin==(row['revision'],row['content_sha256'],row['retired'])


def manifest(store,**updates):
    ids=list(store.rows)
    groups=[{'group_key':f'g{i//20}','claim_ids':ids[i:i+20],'rationale':'现有分组'}
            for i in range(0,len(ids),20)]
    return seal({'schema_version':VERSION,'claim_ids':ids,
        'grouping':{'scope_label':'人子','groups':groups},'cvp_policy':str(POLICY),
        'claim_input_policy':str(INPUT_POLICY),
        'index_root':None,'recall_request_bytes':100000,'recall_max_calls':100,**updates})


def test_complete_topic_without_index_stays_explicitly_unready_and_readonly():
    store=Store()
    def forbidden(*args,**kwargs):raise AssertionError('must not manufacture an index')
    result=prepare_recall(store,manifest(store),load_index=forbidden)
    assert result['status']=='index_required'
    assert [len(g['claim_ids']) for g in result['groups']]==[20,1]
    assert result['model_calls']==result['semantic_writes']==0
    assert result['recall_plans']==[] and len(store.verified)==1
    q=result['claim_authority'][0]['qualification']
    assert q['validation_kind']=='opus_and_program_readback' and not q['independent_audit_complete']


def test_every_group_uses_full_index_and_preserves_evolving_claim_qualifiers(tmp_path):
    store=Store()
    result=prepare_recall(store,manifest(store,index_root=str(tmp_path)),
                          load_index=lambda *args,**kwargs:index())
    assert result['status']=='prepared' and len(result['recall_plans'])==2
    assert len(store.verified)==1
    for p in result['recall_plans']:
        assert len(p['plan']['viewpoint_refs'])==3
        for shard in p['plan']['shards']:
            for claim in shard['claims']:
                assert claim['conditions']==['未到时候']
                assert claim['limitations']=={'keep':'完整'}
                assert 'evidence_step_ids' not in claim


@pytest.mark.parametrize('kind',['stale','missing','not_ready','held','legacy_flag_only','corrupt','foreign_identity'])
def test_missing_or_stale_qualification_and_real_holds_block_recall(kind):
    store=Store(1)
    if kind=='stale':store.quals['C0']['revision']=1
    if kind=='missing':store.quals.pop('C0')
    if kind=='not_ready':store.quals['C0']['ready']=False
    if kind in {'held','legacy_flag_only'}:
        p=store.rows['C0']['payload'];p['review_status']='human_hold' if kind=='held' else 'human_review_required'
        store.rows['C0']['content_sha256']=store.quals['C0']['content_sha256']=record_content_sha(p)
    if kind=='corrupt':store.rows['C0']['payload']['statement']='changed without SHA'
    if kind=='foreign_identity':
        p=store.rows['C0']['payload'];p['claim_id']='foreign'
        store.rows['C0']['content_sha256']=store.quals['C0']['content_sha256']=record_content_sha(p)
    with pytest.raises(ValueError):prepare_recall(store,manifest(store))
    assert store.verified==[]


def test_legacy_spot_check_flag_requires_actual_current_replacement():
    store=Store(1)
    row=store.rows['C0'];row['payload']['review_status']='human_review_required'
    row['content_sha256']=store.quals['C0']['content_sha256']=record_content_sha(row['payload'])
    store.quals['C0']['legacy_ai_spot_check_replaced_by_current_review']=True
    assert prepare_recall(store,manifest(store))['status']=='index_required'


def test_group_and_shard_costs_are_one_cumulative_recall_cap(tmp_path):
    store=Store()
    with pytest.raises(ValueError,match='budget'):
        prepare_recall(store,manifest(store,index_root=str(tmp_path),recall_max_calls=1),
                       load_index=lambda *args,**kwargs:index())


def test_empty_registry_has_zero_recall_calls_but_still_complete_group_plan():
    store=Store(21,views=0)
    result=prepare_recall(store,manifest(store,recall_max_calls=0))
    assert result['status']=='prepared'
    assert len(result['recall_plans'])==2
    assert all(p['plan']['shards']==[] for p in result['recall_plans'])


def test_current_head_drift_blocks_even_an_index_required_result():
    store=Store(1)
    original=store.read_cvp_heads
    count=0
    def changed():
        nonlocal count
        count+=1
        return original() if count==1 else []
    store.read_cvp_heads=changed
    with pytest.raises(ValueError,match='changed'):prepare_recall(store,manifest(store))


def test_cli_preflight_needs_no_old_campaign_and_rejects_execute(tmp_path,monkeypatch):
    from backend.pipeline import cvp_sql_flow as flow
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact,write_immutable
    store=Store(1)
    monkeypatch.setattr(flow,'PostgresKnowledgeStore',lambda _:store)
    request=tmp_path/'request.json';write_immutable(request,manifest(store))
    args=['--action','recall-preflight','--manifest',str(request),'--output-root',str(tmp_path/'out')]
    assert flow.main(args)==0
    assert read_artifact(tmp_path/'out/recall-preflight.json')['status']=='index_required'
    with pytest.raises(ValueError,match='never permits'):flow.main([*args,'--execute'])
