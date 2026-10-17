from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.cvp_management_capacity import (
    RequestProbe, dependency_clusters, measure_request, plan_work_units,
)


def maps(ids):
    return {c: ['V-shared-readonly', 'V-'+c] for c in ids}, {c: [] for c in ids}


def fake_probe(ids):
    # Fixed prompt/schema overhead matters, even with no material.
    packet = {'claims': list(ids)}
    adapter = SimpleNamespace(generation_config_sha256='config', request_bytes=lambda p: 50 + 30*len(p['claims']))
    return [RequestProbe('proposal', adapter, packet)]


def plan(ids=('C1','C2','C3'), **kwargs):
    candidates, deps = maps(ids)
    return plan_work_units(ids, parent_group_key='g1', candidates_by_claim=candidates,
        mutable_dependencies_by_claim=kwargs.pop('deps', deps), max_request_bytes=kwargs.pop('limit', 110),
        probe=kwargs.pop('probe',fake_probe), **kwargs)


def test_split_complete_claims_with_full_candidates_and_original_denominator():
    p = plan()
    assert [u['claim_ids'] for u in p['units']] == [['C1','C2'],['C3']]
    assert p['denominator'] == {'original_claim_count':3,'ready_claim_count':3,'held_claim_count':0}
    assert p['review_capacity_pending']
    assert all(u['candidates_by_claim'][c] == ['V-shared-readonly','V-'+c] for u in p['units'] for c in u['claim_ids'])
    assert p == plan()


def test_shared_mutable_dependencies_transitively_join_and_hold_without_losing_claims():
    deps = {'C1':[('viewpoint_revisions','H1')],
            'C2':[('viewpoint_revisions','H1'),('viewpoint_claim_links','L2')],
            'C3':[('viewpoint_claim_links','L2')]}
    p = plan(deps=deps)
    assert len(p['clusters']) == len(p['units']) == 1
    assert p['units'][0]['status'] == 'resource_hold'
    assert p['units'][0]['claim_ids'] == ['C1','C2','C3']
    assert p['denominator']['held_claim_count'] == 3


def test_oversized_cluster_does_not_stop_independent_claims():
    p = plan(deps={'C1':[('heads','H')],'C2':[('heads','H')],'C3':[]},limit=90)
    assert [(u['claim_ids'],u['status']) for u in p['units']] == [(['C1','C2'],'resource_hold'),(['C3'],'ready')]
    assert p['denominator']['original_claim_count'] == 3


def test_no_twenty_claim_truncation():
    ids = ['C'+str(i) for i in range(31)]
    p = plan(ids,limit=10000)
    assert p['units'][0]['claim_ids'] == ids
    assert p['denominator']['original_claim_count'] == 31


def test_each_available_actual_phase_enforces_capacity_and_review_not_assumed():
    def both(ids):
        result=fake_probe(ids)
        result.append(RequestProbe('review',SimpleNamespace(generation_config_sha256='r',request_bytes=lambda p: 900),{}))
        return result
    p=plan(('C1',),probe=both,required_phases=('proposal','review'))
    assert not p['review_capacity_pending']
    assert p['units'][0]['status']=='resource_hold'
    assert p['units'][0]['measurements'][1]['request_bytes']==900
    with pytest.raises(ValueError,match='not available'):
        plan(required_phases=('proposal','review'))


def test_probe_exact_material_not_mutated_and_not_remeasured_for_same_subset():
    calls=[]
    def probe(ids):
        assert ids not in calls
        calls.append(ids)
        return fake_probe(ids)
    ids=['C1','C2','C3']; candidates,deps=maps(ids)
    before=deepcopy((ids,candidates,deps))
    plan_work_units(ids,parent_group_key='g',candidates_by_claim=candidates,
        mutable_dependencies_by_claim=deps,max_request_bytes=110,probe=probe)
    assert (ids,candidates,deps)==before


@pytest.mark.parametrize('ids,candidates,deps',[
    (['C1','C1'],{'C1':[]},{'C1':[]}),
    (['C1'],{}, {'C1':[]}),
    (['C1'],{'C1':[]},{}),
    (['C1'],{'C1':['V','V']},{'C1':[]}),
    (['C1'],{'C1':[]},{'C1':['heads/H']}),
])
def test_rejects_incomplete_or_ambiguous_denominator(ids,candidates,deps):
    with pytest.raises(ValueError):
        dependency_clusters(ids,candidates_by_claim=candidates,mutable_dependencies_by_claim=deps)


def test_measurement_uses_real_proposal_and_review_adapter_serialization_without_dispatch():
    from backend.api.canonical_repository import cvp_management as domain
    from backend.pipeline.cvp_management_models import adapter
    from test_cvp_management import request, materials, proposal
    from test_cvp_recall_stages import Client, review_client
    req,b=request(),materials()
    candidate=domain.compile_candidate(req,b,proposal())
    for phase,packet,client in [
        ('proposal',domain.proposal_packet(req,b),Client()),
        ('review',domain.review_packet(req,b,candidate),review_client()),
    ]:
        model=adapter(phase,req,b,packet,client=client)
        before=deepcopy(packet)
        result=measure_request(RequestProbe(phase,model,packet))
        assert result['request_bytes']==model.request_bytes(packet)
        assert result['generation_config_sha256']==model.generation_config_sha256
        assert packet==before
        # The actual schema and system prompt are part of the measurement.
        import json
        assert result['request_bytes'] > len(json.dumps(packet,ensure_ascii=False).encode())


def test_manifest_boundary_rejects_resealed_candidate_loss_missing_claim_and_dependency_split():
    from backend.api.canonical_repository.cvp_management_capacity import validate_work_units
    from backend.api.canonical_repository.viewpoint_foundation import sha256_json
    ids=['C1','C2','C3'];candidates,deps=maps(ids)
    p=plan()
    def check(value):
        validate_work_units(value,claim_ids=ids,parent_group_key='g1',
            candidates_by_claim=candidates,mutable_dependencies_by_claim=deps,max_request_bytes=110)
    check(p)
    for mutate in [lambda p:p['units'].pop(),
                   lambda p:p['units'][0]['candidates_by_claim']['C1'].pop(),
                   lambda p:p['units'][0].update(cluster_indices=[]),
                   lambda p:p['units'][0].update(status='resource_hold')]:
        bad=deepcopy(p);mutate(bad)
        bad['artifact_sha256']=sha256_json({k:v for k,v in bad.items() if k!='artifact_sha256'})
        with pytest.raises(ValueError):check(bad)
