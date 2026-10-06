from copy import deepcopy
import pytest
from pydantic import ValidationError
from backend.pipeline import viewpoint_s201129_link_support_apply as repair
from backend.api.canonical_repository.knowledge_models import ViewpointIdentityDecisionRecord
from backend.api.canonical_repository.reviewed_candidate_contract import validate_store_package_authorization


def decision():
    return dict(identity_decision_id='VID-test',identity_candidate_id='VIC-test',
        decision='match_existing',resolved_viewpoint_id=repair.review.CV,
        reviewer_kind='system',reviewer_id=repair.REVIEWER,
        approval_basis='owner_scoped_independent_terminal_review',reason='exact support review',
        input_sha256='a'*64,review_artifact_sha256=repair.RAW,policy_version=repair.VERSION,
        reviewer_model_ids=['claude-opus-5-5'],semantic_call_artifact_sha256s=[repair.RAW],
        created_at='2026-10-06T00:00:00Z',review_status='system_approved')


@pytest.mark.parametrize('mutation',[None,'reviewer','policy','model','basis','raw','human'])
def test_single_opus_provenance_requires_exact_scoped_pair(mutation):
    d=decision()
    edits={'reviewer':('reviewer_id','s201129_owner_scoped_opus_terminal_review'),
        'policy':('policy_version','arbitrary'), 'model':('reviewer_model_ids',['gpt-6.1-sol']),
        'basis':('approval_basis','dual_model_consensus'),'raw':('semantic_call_artifact_sha256s',[]),
        'human':('review_status','human_approved')}
    if mutation:
        k,v=edits[mutation];d[k]=v
        with pytest.raises(ValidationError):ViewpointIdentityDecisionRecord.model_validate(d)
    else:assert ViewpointIdentityDecisionRecord.model_validate(d).reviewer_model_ids==['claude-opus-5-5']


def test_gate_reconstructs_entire_package_instead_of_partial_pass(monkeypatch):
    expected={'viewpoint_claim_links':[{'exact':'supports'}],
        'source_repair_application':{'schema_version':repair.VERSION}}
    monkeypatch.setattr(repair,'reviewed_inputs',lambda:({},{}))
    monkeypatch.setattr(repair,'parent_snapshot',lambda:{})
    monkeypatch.setattr(repair,'package_for',lambda *a:deepcopy(expected))
    validate_store_package_authorization(expected)
    for field in ['viewpoint_claim_links','claims','canonical_viewpoints','viewpoint_identity_decisions']:
        bad=deepcopy(expected);bad[field]=[{'unreviewed':True}]
        with pytest.raises(ValueError):validate_store_package_authorization(bad)


def operations():
    return [{'collection':'viewpoint_claim_links','operation':'update','object_id':repair.review.LINK},
        {'collection':'viewpoint_identity_candidates','operation':'create','object_id':'VIC-test'},
        {'collection':'viewpoint_identity_decisions','operation':'create','object_id':'VID-test'}]


@pytest.mark.parametrize('mutation',[None,'extra','omit','claim','foreign','delete','duplicate'])
def test_exact_three_operations_only(mutation):
    ops=operations()
    if mutation=='extra':ops.append({'collection':'claims','operation':'update','object_id':'DK-x'})
    elif mutation=='omit':ops.pop()
    elif mutation=='claim':ops[0]['collection']='claims'
    elif mutation=='foreign':ops[0]['object_id']='VCL-other'
    elif mutation=='delete':ops[0]['operation']='delete'
    elif mutation=='duplicate':ops[2]=deepcopy(ops[1])
    if mutation:
        with pytest.raises(ValueError):repair.verify_operations({'operations':ops})
    else:repair.verify_operations({'operations':ops})


def test_absent_child_receipt_cannot_waive_parent_guard(monkeypatch,tmp_path):
    monkeypatch.setattr(repair,'ROOT',tmp_path)
    rows={('viewpoint_claim_links',repair.review.LINK):{'payload':{'unreviewed':'drift'}}}
    assert repair.restore_parent_records(rows) is rows
    assert repair.applied_child_object_ids() == set()


@pytest.mark.parametrize('mutation',[None,'extra','omit','foreign','payload','duplicate'])
def test_partial_parent_readset_includes_all_exact_child_identities(monkeypatch,tmp_path,mutation):
    monkeypatch.setattr(repair,'ROOT',tmp_path)
    (tmp_path/'apply-receipt.json').touch()
    ops=operations()
    for i,op in enumerate(ops):
        op.update(payload={'kind':str(i)},after_revision=3 if i==0 else 1)
    expected={(o['collection'],o['object_id']):deepcopy(o['payload']) for o in ops}
    monkeypatch.setattr(repair,'read_artifact',lambda p:{'operations':ops} if p.name=='dry-run-plan.json' else {'reviewed':True})
    from backend.api.canonical_repository import postgres_store
    monkeypatch.setattr(postgres_store,'normalize_package',lambda p:{
        c:{oid:payload for (col,oid),payload in expected.items() if col==c}
        for c,_ in expected})
    if mutation=='extra':ops.append({'collection':'claims','operation':'update','object_id':'DK-foreign'})
    elif mutation=='omit':ops.pop()
    elif mutation=='foreign':ops[1]['object_id']='VIC-foreign'
    elif mutation=='payload':ops[1]['payload']['kind']='unreviewed'
    elif mutation=='duplicate':ops[2]=deepcopy(ops[1])
    if mutation:
        with pytest.raises(ValueError):repair.applied_child_object_ids()
    else:
        ids=repair.applied_child_object_ids()
        assert ids == {repair.review.LINK,'VIC-test','VID-test'}
        # A parent-only partial snapshot reproduces the real omission failure;
        # adding the exact child read set fixes loading without weakening SHA,
        # payload, revision, retirement or missing-row postimage checks.
        records={(c,oid):{'payload':{**payload,'revision':3 if c=='viewpoint_claim_links' else 1},
            'revision':3 if c=='viewpoint_claim_links' else 1,
            'content_sha256':repair.source.semantic_record_sha({**payload,'revision':3 if c=='viewpoint_claim_links' else 1}),
            'retired':False} for (c,oid),payload in expected.items()}
        partial={k:v for k,v in records.items() if k[0]=='viewpoint_claim_links'}
        with pytest.raises(ValueError,match='child exact postimage drift'):
            repair.restore_exact_child_postimages(partial,{'operations':ops},{'revision':2})
        assert repair.restore_exact_child_postimages(records,{'operations':ops},{'revision':2})[
            'viewpoint_claim_links',repair.review.LINK]['payload']=={'revision':2}


def test_apply_never_replays_an_existing_root(monkeypatch,tmp_path):
    monkeypatch.setattr(repair,'ROOT',tmp_path)
    with pytest.raises(ValueError,match='already attempted'):repair.run()


@pytest.mark.parametrize('mutation',[None,'link_payload','link_revision','candidate','sha','retired'])
def test_child_proof_composes_exact_postimages_not_ignore_link_guard(mutation):
    ops=operations()
    for index,o in enumerate(ops):o.update(payload={'kind':str(index)},after_revision=3 if index==0 else 1)
    records={}
    for o in ops:
        payload={**o['payload'],'revision':o['after_revision']}
        records[o['collection'],o['object_id']]={'payload':payload,'revision':payload['revision'],
            'content_sha256':repair.source.semantic_record_sha(payload),'retired':False}
    records['claims','unrelated']={'payload':{'statement':'unchanged'}}
    old={'revision':2,'link_type':'equivalent_component'}
    if mutation=='link_payload':records['viewpoint_claim_links',repair.review.LINK]['payload']['kind']='drift'
    elif mutation=='link_revision':records['viewpoint_claim_links',repair.review.LINK]['revision']=4
    elif mutation=='candidate':records.pop(('viewpoint_identity_candidates','VIC-test'))
    elif mutation=='sha':records['viewpoint_claim_links',repair.review.LINK]['content_sha256']='wrong'
    elif mutation=='retired':records['viewpoint_claim_links',repair.review.LINK]['retired']=True
    original=deepcopy(records)
    if mutation:
        with pytest.raises(ValueError):repair.restore_exact_child_postimages(records,{'operations':ops},old)
    else:
        result=repair.restore_exact_child_postimages(records,{'operations':ops},old)
        assert result['viewpoint_claim_links',repair.review.LINK]['payload']==old
        assert result['claims','unrelated']==records['claims','unrelated']
        assert ('viewpoint_identity_candidates','VIC-test') not in result
    assert records==original


def test_composed_continuation_never_restarts_existing_audit(monkeypatch,tmp_path):
    from backend.pipeline import viewpoint_s201129_link_support_continuation as continuation
    monkeypatch.setattr(continuation,'ROOT',tmp_path)
    with pytest.raises(ValueError,match='already attempted'):continuation.run()
