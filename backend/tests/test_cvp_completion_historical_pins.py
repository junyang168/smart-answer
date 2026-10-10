import pytest
from backend.api.canonical_repository.postgres_store import record_content_sha,canonical_json,ChangeSetConflict
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store


def graph(store, *, current_old=False, bad_sha=False):
    rows={('viewpoint_revisions','R'):{'revision':1,'current_decision_id':'ACTIVE',
             'provenance':{'basis_identity_decision_ids':['OLD']}},
          ('viewpoint_identity_decisions','ACTIVE'):{'revision':1,'identity_candidate_id':'NEW-CAND'},
          ('viewpoint_identity_candidates','NEW-CAND'):{'revision':1},
          ('viewpoint_identity_decisions','OLD'):{'revision':1,'identity_candidate_id':'OLD-CAND'},
          ('viewpoint_identity_candidates','OLD-CAND'):{'revision':1}}
    if current_old:rows['viewpoint_revisions','R']['another_current_decision_id']='OLD'
    with store.connect() as c:
        for (col,oid),p in rows.items():
            sha='wrong' if bad_sha and oid=='OLD' else record_content_sha(p)
            c.execute('INSERT INTO wang_knowledge.objects(collection,object_id,revision,review_status,visibility,content_sha256,payload) VALUES(%s,%s,1,%s,%s,%s,%s::jsonb)',
                (col,oid,'candidate','internal',sha,canonical_json(p)))
    return rows


def test_completion_pins_historical_decision_without_ancestor_candidate(postgres_store):
    rows=graph(postgres_store);s=postgres_store
    with s.connect() as c:
        full=s._existing_cvp_context(c,{'viewpoint_revisions':{'R':rows['viewpoint_revisions','R']}})
        scoped=s._existing_cvp_context(c,{'viewpoint_revisions':{'R':rows['viewpoint_revisions','R']}},historical_pins_only=True)
    assert set(full)==set(rows)
    assert set(scoped)==set(rows)-{('viewpoint_identity_candidates','OLD-CAND')}
    assert scoped['viewpoint_identity_decisions','OLD']==full['viewpoint_identity_decisions','OLD']
    assert ('viewpoint_identity_candidates','NEW-CAND') in scoped


def test_business_reference_always_expands_even_when_also_historical(postgres_store):
    rows=graph(postgres_store,current_old=True)
    with postgres_store.connect() as c:
        scoped=postgres_store._existing_cvp_context(c,{'viewpoint_revisions':{'R':rows['viewpoint_revisions','R']}},historical_pins_only=True)
    assert set(scoped)==set(rows)


def test_historical_pin_still_checks_actual_payload_sha(postgres_store):
    rows=graph(postgres_store,bad_sha=True)
    with postgres_store.connect() as c,pytest.raises(ChangeSetConflict,match='SHA drift'):
        postgres_store._existing_cvp_context(c,{'viewpoint_revisions':{'R':rows['viewpoint_revisions','R']}},historical_pins_only=True)


def test_reference_projection_does_not_remove_business_or_origin_ids():
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore as S
    p={'decision_id':'CURRENT','created_from_candidate_id':'ORIGIN','provenance':{'basis_identity_decision_ids':['OLD']}}
    assert S._record_reference_ids(p)=={'CURRENT','ORIGIN','OLD'}
    assert S._record_reference_ids(p,exclude_provenance=True)=={'CURRENT','ORIGIN'}
