from copy import deepcopy
import pytest
from backend.pipeline import viewpoint_historical_review_bridge as m
from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import CvpProductionBlocked


def graph(monkeypatch):
    claim = {'claim_id':'C1','revision':1,'statement':'same conclusion','evidence_step_ids':[]}
    final = {'claims':{'C1':deepcopy(claim)}}
    live = deepcopy(claim); live.update(revision=2,review_status='ai_consensus_reviewed')
    versions = [{'revision':c['revision'],'payload':c,'content_sha256':record_content_sha(c),
                 'status':'applied','source_sha256':sha256_json(final),'change_set_id':'K'+str(c['revision']),
                 'source_kind':'knowledge_package' if c['revision']==1 else 'legacy_adjudicated_auto_applied_review_reconciliation_v1'}
                for c in [claim,live]]
    monkeypatch.setattr(m,'_normalize_records',lambda p:(p,None))
    return final,live,versions


def test_exact_applied_legacy_final_is_reused_not_initial_pass(monkeypatch):
    final,live,versions = graph(monkeypatch)
    assert m.verify_legacy_adjudicated_graph('C1',final,live,versions,{}, {})['revision']==2


@pytest.mark.parametrize('case',['content','history_content','history_hash','not_applied','wrong_import','foreign_migration','not_current'])
def test_legacy_graph_does_not_license_review_or_content_drift(monkeypatch,case):
    final,live,versions=graph(monkeypatch)
    if case=='content':live['statement']='different'
    elif case=='history_content':versions[0]['payload']['statement']='unreviewed';versions[0]['content_sha256']=record_content_sha(versions[0]['payload'])
    elif case=='history_hash':versions[0]['content_sha256']='bad'
    elif case=='not_applied':versions[0]['status']='draft'
    elif case=='wrong_import':versions[0]['source_sha256']='bad'
    elif case=='foreign_migration':versions[1]['source_kind']='other'
    elif case=='not_current':versions.pop()
    with pytest.raises(CvpProductionBlocked):m.verify_legacy_adjudicated_graph('C1',final,live,versions,{}, {})


def bundle_fixture(monkeypatch):
    from backend.pipeline import legacy_candidate_reconciliation as inspector
    from backend.pipeline import legacy_auto_applied_review_reconciliation as replay
    bundle={'reason':None,'review_sha256':'review','package_sha256':'input',
        'adjudication_structurally_validated':True,
        'review_rows':{'C1':{'decision':'changes_suggested','reviewer_fingerprint':'fingerprint'}},
        'adjudication_results':{'C1':{'status':'auto_applied','openai':{'decision':'accept','patch':{'excluded_claim_relation_ids':['R1']}}}},
        'reviewer_id':'claude-sonnet-5','adjudication_path':'/tmp/adjudication.json','adjudication_sha256':'adj'}
    final={'consensus_application':{'removed_claim_relation_ids':['R1']},'claim_relations':[]}
    monkeypatch.setattr(m,'_read_binding',lambda b:final)
    monkeypatch.setattr(inspector,'inspect_legacy_bundle',lambda p:bundle)
    monkeypatch.setattr(replay,'verify_historical_replay',lambda p:{'proof':'replayed'})
    return bundle,final


@pytest.mark.parametrize('case',['review_sha','input_sha','source','human','sample','unresolved','reject','missing_removal','retained_relation','failed_replay'])
def test_legacy_adjudication_cannot_bypass_real_findings(monkeypatch,case):
    bundle,final=bundle_fixture(monkeypatch)
    if case=='review_sha':bundle['review_sha256']='wrong'
    elif case=='input_sha':bundle['package_sha256']='wrong'
    elif case=='source':bundle['adjudication_structurally_validated']=False
    elif case=='human':bundle['review_rows']['C1']['human_review_reason']='needs human'
    elif case=='sample':bundle['review_rows']['C1']['spot_check_selected']=True
    elif case=='unresolved':bundle['adjudication_results']['C1']['status']='pending'
    elif case=='reject':bundle['adjudication_results']['C1']['openai']['decision']='reject'
    elif case=='missing_removal':final['consensus_application']['removed_claim_relation_ids']=[]
    elif case=='retained_relation':final['claim_relations']=[{'claim_relation_id':'R1'}]
    else:
        from backend.pipeline import legacy_auto_applied_review_reconciliation as replay
        monkeypatch.setattr(replay,'verify_historical_replay',lambda p:None)
    with pytest.raises(CvpProductionBlocked):
        m._legacy_adjudicated_bridge(claim_id='C1',current_claim={},versions=[],current_evidence={},current_fragments={},
            review_binding={'file_sha256':'review'},review_input_binding={'file_sha256':'input'},
            legacy_reviewed_candidate_binding={'path':'/tmp/candidate.json'})


@pytest.mark.parametrize('active',[False,True])
def test_removed_relation_must_also_be_absent_from_live_database(monkeypatch,active):
    bundle,final=bundle_fixture(monkeypatch)
    monkeypatch.setattr(m,'verify_legacy_adjudicated_graph',lambda *a:{'revision':2,'change_set_id':'K2'})
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,sql,parameters=None):
            assert sql=='SET TRANSACTION READ ONLY' or "retired_at IS NULL" in sql
            if parameters:assert parameters==(['R1'],)
            return self
        def fetchall(self):return [('R1',)] if active else []
    from backend.api.canonical_repository import postgres_store
    monkeypatch.setattr(postgres_store.PostgresKnowledgeStore,'connect',lambda self:Connection())
    kwargs=dict(claim_id='C1',current_claim={'revision':2},versions=[],current_evidence={},current_fragments={},
        review_binding={'file_sha256':'review'},review_input_binding={'file_sha256':'input'},
        legacy_reviewed_candidate_binding={'path':'/tmp/candidate.json'})
    if active:
        with pytest.raises(CvpProductionBlocked):m._legacy_adjudicated_bridge(**kwargs)
    else:
        proof=m._legacy_adjudicated_bridge(**kwargs)
        assert proof['independent_review_decision']=='changes_suggested'
        assert proof['adjudication_status']=='auto_applied'
        assert proof['approval_status']=='not_human_approved'
        assert proof['master_data_mutations']==proof['model_calls_executed']==0
