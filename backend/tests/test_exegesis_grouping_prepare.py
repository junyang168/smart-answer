import copy
import pytest
from backend.pipeline import exegesis_grouping_prepare as prep


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(prep,'FORMAL_LEDGER','L');monkeypatch.setattr(prep,'FORMAL_PACKET','P')
    claims=[dict(claim_id=f'C{i}',statement=f'Claim {i}',claim_content_sha256=f'sha{i}') for i in range(3843)]
    ledger=dict(artifact_sha256='L',packet_sha256='P',decisions=[dict(claim_id=c['claim_id'],role='passage_exegesis',
        passage_identity_status='disputed' if i<624 else 'agreed',interpreted_passage_keys=['Matt.16.28-Matt.17.8']) for i,c in enumerate(claims)])
    reviewed=[]
    for i,c in enumerate(claims[:624]):
        resolved=i<516
        decision=dict(primary='Matt.16.28-Matt.17.8',reason='应许与实现')
        reviewed.append(dict(claim={**c,'derived_source_positions':[1]},status='source_verified_independent_agreement' if resolved else 'unresolved',
            primary=decision['primary'] if resolved else '',original_primary_review=decision,
            independent_review=decision.copy(),arbitration=None,missing='' if resolved else '缺少唯一主要范围',secondary_relations={'primary':[]}))
    return ledger,dict(artifact_sha256='P',claims=claims),dict(rows=reviewed)


def test_scope_partition_and_no_automatic_key_promotion(frozen):
    rows=prep.reconcile(*frozen)
    assert len(rows)==3843
    assert sum(r['preparation_status'].startswith('primary_confirmed') for r in rows)==516
    assert sum(r['preparation_status']=='unresolved_primary' for r in rows)==108
    remaining=[r for r in rows if r['preparation_status'].startswith('existing_keys')]
    assert len(remaining)==3219 and all(not r['primary'] for r in remaining)
    assert rows[0]['primary']=='Matt.16.28-Matt.17.8'
    assert rows[0]['claim']==frozen[1]['claims'][0]  # derived audit fields do not replace frozen graph


def test_reject_changed_claim_not_derived_audit_fields(frozen):
    frozen[2]['rows'][0]['claim']['statement']='changed'
    with pytest.raises(ValueError,match='Claim graph drift'):prep.reconcile(*frozen)


def test_reject_candidate_disagreement_and_duplicate_scope(frozen):
    altered=copy.deepcopy(frozen)
    altered[2]['rows'][0]['independent_review']['primary']='Matt.16.28'
    with pytest.raises(ValueError,match='reviewed primary differs'):prep.reconcile(*altered)
    altered=copy.deepcopy(frozen);altered[2]['rows'].append(altered[2]['rows'][0])
    with pytest.raises(ValueError,match='624 exception scope'):prep.reconcile(*altered)
