"""Models receive typed edge constraints without changing frozen intakes."""
import json
from pathlib import Path
from backend.pipeline import claim_management_intake as intake,claim_management_batch as batch
from backend.tests.test_claim_management_intake import claims
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres


def test_writable_knowledge_edges_receive_the_actual_endpoint_contract(claims,monkeypatch):
    store,root=claims
    store.apply_plan(store.plan_package({'package_id':'typed-intake-edge','knowledge_relations':[
        {'relation_id':'ER','from_id':'ED','to_id':'EC','relation_type':'supports','reason':'synthetic source reasoning'}]}))
    prepared=intake.prepare(['C'],root,instruction='Refresh original teaching',store=store)
    _,jobs=batch.load(prepared['manifest']['path'],root/'batch',store=store)
    request,_=next(iter(jobs.values()))
    auth=Path(request.authorization.path).read_bytes()
    questions=json.loads(auth)['review_questions']
    assert questions[0]=='Refresh original teaching'
    text=next(q for q in questions if q.startswith('Program endpoint contract'))
    rules=json.loads(text[text.index('{'):])
    assert rules['all_non_answers']=={'from_collections':['evidence_steps','observations'],'to_collections':['evidence_steps']}
    assert rules['answers']['to_collections']==['evidence_steps','questions']
    from backend.api.canonical_repository.knowledge_models import knowledge_relation_endpoints
    start,end=knowledge_relation_endpoints('opposes')
    assert rules['all_non_answers']=={'from_collections':sorted(start),'to_collections':sorted(end)}
    assert 'position_nodes' not in rules['all_non_answers']['from_collections']
    monkeypatch.setattr(intake,'relation_endpoint_instruction',lambda:(_ for _ in ()).throw(AssertionError('must reuse frozen authority')))
    assert intake.prepare(['C'],root,instruction='Refresh original teaching',store=store)==prepared
    assert Path(request.authorization.path).read_bytes()==auth


def test_no_knowledge_edge_authority_does_not_add_unused_contract(claims):
    store,root=claims
    prepared=intake.prepare(['C'],root,instruction='Refresh original teaching',store=store)
    _,jobs=batch.load(prepared['manifest']['path'],root/'batch',store=store)
    request,_=next(iter(jobs.values()))
    assert not any(p.collection=='knowledge_relations' for p in request.permissions+request.creation_permissions)
    assert json.loads(Path(request.authorization.path).read_text())['review_questions']==['Refresh original teaching']
