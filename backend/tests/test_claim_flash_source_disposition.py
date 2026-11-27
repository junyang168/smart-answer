import pytest

from backend.api.canonical_repository.postgres_store import ChangeSetConflict

from backend.pipeline import claim_flash_source_disposition as stage, claim_flash_common as f
from backend.pipeline import evidence_refresh as e
from backend.tests.test_claim_flash_evidence import seeded
from backend.tests.test_claim_flash_human_source import fact, save
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres


@pytest.mark.parametrize('drift', [False, True])
def test_owner_approval_retires_claim_and_preserves_evidence(seeded, drift):
    store, sources, plan, _, root = seeded
    group = {**plan['groups'][0], 'claim_ids':['C']}
    save(store, [fact(sources)])
    parent = root/'statements'
    records, sources = f.load_group_source(store, 'S', group)
    e.save(parent/'before.json', {'records':records, 'sources':sources})
    pending = {'version':'claim_flash_statements_v1','claim_ids':['C'], 'pending':[{'object_key':'claims/C','layer':'source','reason':'Wrong speaker'}],
               'updates':{},'outcomes':[], 'new_fragments':{},'status':'needs_attention'}
    e.save(parent/'verification.json', pending)
    result, path = stage.continue_statements(store,'S',group,parent,root/'evidence',None,lambda:pending)
    assert path == parent and result['pending']
    root = parent/'source-disposition'
    proposal = stage.read(root/'proposal.json')
    assert proposal['findings']['claims/C']['source_confirmations']
    assert not store.get_record_state('claims','C')['retired']
    with pytest.raises(ValueError, match='exact owner approval'):
        stage.apply(store,proposal,{},root)
    e.save(root/'owner-approval.json', {'authorized_by':'owner','authorization_quote':'同意退役',
           'decision':'retire','claim_ids':['C'],'proposal_sha256':e.digest(proposal)})

    if drift:
        store.apply_plan(store.plan_package({'package_id':'new-question-after-approval',
            'questions':[{'question_id':'new','question':'新的問題？','answer_claim_ids':['C'],
                          'answer_state':'answered','source_fragment_id':'F0'}]}))
        with pytest.raises(ChangeSetConflict, match='retirement consumers changed'):
            stage.continue_statements(store,'S',group,parent,parent/'evidence',None,lambda:pending)
        assert not store.get_record_state('claims','C')['retired']
        return

    def unexpected():
        pytest.fail('should reuse the stopped statement result')

    result, retained = stage.continue_statements(store,'S',group,parent,root/'evidence',None,unexpected)
    assert result['status']=='verified' and result['claim_ids']==[]
    assert store.get_record_state('claims','C')['retired']
    assert store.get_record_state('evidence_steps','E')['payload']['produced_claim_ids']==[]
    assert stage.continue_statements(store,'S',group,parent,root/'evidence',None,unexpected)==(result,retained)


def test_new_confirmation_rechecks_pending_claim_and_replays(seeded, monkeypatch):
    from backend.pipeline import claim_flash_statements as statements

    store, original_sources, plan, _, root = seeded
    group = {**plan['groups'][0], 'claim_ids':['C']}
    parent = root/'statements'
    records, sources = f.load_group_source(store, 'S', group)
    e.save(parent/'before.json', {'records':records,'sources':sources})
    pending = {'version':statements.VERSION,'claim_ids':['C'],'updates':{},'new_fragments':{},
               'status':'needs_attention','outcomes':[],
               'pending':[{'object_key':'claims/C','layer':'source','reason':'Needs clarification'}]}
    e.save(parent/'verification.json', pending)
    save(store, [fact(original_sources)])
    reviewed = []

    def review(records, sources, targets, *args):
        reviewed.extend(targets)
        assert sources['S']['human_source_decisions']
        return {'pending':[],'updates':{},'outcomes':[{'object_key':'claims/C','task':'claim_fidelity',
                                                     'verdict':'faithful'}]}

    monkeypatch.setattr(statements,'review',review)
    monkeypatch.setattr(statements,'verify_evidence',lambda root, keys, *a: keys == {'evidence_steps/E'} or pytest.fail(keys))
    args = (store,'S',group,parent,root/'evidence',object(),lambda:pending)
    result, successor = stage.continue_statements(*args)
    assert result['status']=='verified' and successor.name=='rechecked'
    assert stage.continue_statements(*args)==(result,successor)
    assert reviewed==['claims/C']
    assert stage.read(parent/'verification.json') == pending
