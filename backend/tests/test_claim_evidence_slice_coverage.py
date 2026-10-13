from copy import deepcopy
import os
from pathlib import Path
import pytest

from backend.pipeline import claim_first_management as flow


@pytest.fixture(params=['evidence', 'claim_source'])
def saved(request):
    if os.getenv('WKP357_SAVED_SLICE_COVERAGE_TEST') != '1':
        pytest.skip('explicit actual saved SQL readonly opt-in')
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.cvp_sql_flow import binding
    load_dotenv('.env')
    store=PostgresKnowledgeStore()
    run_id = ('CVPG-b9bdf23b7ec4f63b20fa5d69818109697a8fb808138b18557dffe2e225369031'
              if request.param == 'evidence' else
              'CVPG-4c6083d8d5036f316dd620945c37974f4d6b3daf128752776490dec2b2f21cd4')
    value=store.get_cvp_group(run_id)
    if value['checkpoint'] != 'applied':
        pytest.skip('historical slice already completed; live handoff requires applied generation')
    root=Path(value['reviewed']['evidence']['package']['payload']['source_repair_application']['run_root'])
    files={str(p):binding(p) for p in root.rglob('*') if p.is_file()}
    before=deepcopy(value)
    yield store,value
    assert store.get_cvp_group(value['run_id'])==before
    assert files=={str(p):binding(p) for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('fault',[None,'membership','checkpoint','marker','receipt','pending',
    'duplicate_pending','review_sha','fingerprint','source_sha','foreign_run','approval','write_set','postimage'])
def test_actual_source_slice_coverage_requires_complete_source_and_keeps_pending(saved,monkeypatch,fault):
    store,original=saved;value=deepcopy(original);group={'claim_ids':list(value['claim_ids'])}
    if fault=='membership':group['claim_ids'].pop()
    elif fault=='checkpoint':value['checkpoint']='prepared'
    elif fault=='marker':value['claim_layer_audit']['cvp_management_complete']=True
    elif fault=='receipt':value['claim_layer_audit']['receipt']['file_sha256']='0'*64
    elif fault=='pending':value['reviewed']['evidence']['pending_cvp_management_record_keys']=[]
    elif fault=='duplicate_pending':value['reviewed']['evidence']['pending_cvp_management_record_keys']*=2
    elif fault=='review_sha':value['reviewed_sha256']='0'*64
    elif fault in {'fingerprint','source_sha','foreign_run','approval','write_set','postimage'}:
        old=store.get_change_history
        def changed(cid):
            result=deepcopy(old(cid))
            if fault=='fingerprint':result['fingerprint_sha256']='0'*64
            elif fault=='source_sha':result['source_sha256']='0'*64
            elif fault=='foreign_run':result['metadata']['run_id']='foreign'
            elif fault=='approval':result['metadata']['approval']={}
            elif fault=='write_set':result['operations']=result['operations'][:-1]
            else:result['operations'][0]['after']['statement']='foreign'
            return result
        monkeypatch.setattr(store,'get_change_history',changed)
    if fault:
        with pytest.raises((ValueError,KeyError)):
            flow.coverage_context(store,value,group)
    else:
        result=flow.coverage_context(store,value,group)
        assert result['schema_version']==flow.REPROPOSAL_VERSION
        assert result['source_slice_mode']==original['reviewed']['evidence']['package']['payload']['source_repair_application']['source_write_slice']['mode']
        assert result['pending_existing_cvp_record_keys']==original['reviewed']['evidence']['pending_cvp_management_record_keys']
        assert result['existing_cvp_content_approved'] is False
        assert result['old_proposals_and_budgets_preserved'] is True


def test_actual_current_source_prepares_all_original_claims_without_model_or_writes(saved):
    from backend.pipeline.cvp_sql_flow import prepare_group
    store,value=saved;definition={**store.get_cvp_campaign(value['campaign_sha256']),
                                 'campaign_sha256':value['campaign_sha256']}
    group=next(g for g in definition['groups'] if g['sequence']==value['sequence'])
    material=prepare_group(store,definition,group)
    assert len(material['packet']['claims'])==len(value['claim_ids'])
    assert {r['claim_id'] for r in material['packet']['claims']}==set(value['claim_ids'])
    assert material['metrics']['historical_snapshot_reads']==0
