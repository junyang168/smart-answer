from copy import deepcopy
from types import SimpleNamespace
import pytest
from backend.pipeline import claim_evidence_binding as b,source_reconciliation as c
from backend.api.canonical_repository.postgres_store import record_content_sha,sha256_json


def scenario(monkeypatch):
    ev={'evidence_step_id':'E','revision':2,'statement':'own statement','produced_claim_ids':['C'],
        'speaker':'professor','stance':'endorsed','support_eligibility':'eligible','review_status':'candidate','source_fragment_id':'old'}
    states={'evidence_steps/E':ev,'claims/C':{'claim_id':'C','evidence_step_ids':['E']}}
    d={'schema_version':b.VERSION,'authorized_by':'owner','authorization_quote':'fix own binding','human_content_approval':False,
        'maximum_corrections':1,'source_id':'S','evidence_id':'E','claim_ids':['C'],'before_pin':[2,record_content_sha(ev),False]}
    def bind():return {**d,'artifact_sha256':sha256_json(d)}
    import json
    monkeypatch.setattr(c,'bound_bytes',lambda *a:json.dumps(bind()).encode())
    m=c.Manifest.model_construct(source_id='S',evidence_binding_repair=c.FileBinding(path='/scope',file_sha256='a'*64),
        permissions=[c.Permission(collection='evidence_steps',object_id='E',fields=sorted(b.FIELDS)),
        c.Permission(collection='source_fragments',object_id='new',fields=sorted(b.FRAGMENT_FIELDS),create=True,parent_id='S')])
    return m,states,d


def test_scope_only_binding_no_content_approval(monkeypatch):
    m,s,d=scenario(monkeypatch);assert b.direction(m,s)['human_content_approval'] is False
    before=deepcopy(s['evidence_steps/E']);after={**before,'source_fragment_ids':['old','new']}
    monkeypatch.setattr(c.Manifest,'model_validate',lambda *a:m)
    b.validate_delta({'manifest':{},'before_records':c.rows(s)},before,after,'evidence_steps/E')
    assert before==s['evidence_steps/E']


@pytest.mark.parametrize('fault',['held','context','pin','owner','permission','foreign','speaker'])
def test_scope_fail_closed(monkeypatch,fault):
    m,s,d=scenario(monkeypatch)
    if fault=='held':s['evidence_steps/E']['review_status']='human_hold'
    elif fault=='context':s['evidence_steps/E']['support_eligibility']='context_only'
    elif fault=='pin':d['before_pin'][0]=1
    elif fault=='owner':s['claims/C']['evidence_step_ids']=[]
    elif fault=='permission':m.permissions[0].fields.append('statement')
    elif fault=='foreign':d['source_id']='other'
    else:s['evidence_steps/E']['speaker']='editor'
    with pytest.raises(ValueError):b.direction(m,s)


@pytest.mark.parametrize('fault',['words','qualification','old_binding','relation','oldfragment','foreignfragment'])
def test_postimage_cannot_cross_scope(monkeypatch,fault):
    m,s,d=scenario(monkeypatch);monkeypatch.setattr(c.Manifest,'model_validate',lambda *a:m)
    old=deepcopy(s['evidence_steps/E']);new={**old,'source_fragment_ids':['old','new']};key='evidence_steps/E'
    if fault=='words':new['statement']='changed'
    elif fault=='qualification':new['support_eligibility']='eligible_with_label'
    elif fault=='old_binding':new['source_fragment_id']='new';new['source_fragment_ids']=['new']
    elif fault=='relation':key='knowledge_relations/R'
    elif fault=='oldfragment':key='source_fragments/F';old={'source_id':'S'};new={'source_id':'S'}
    else:key='source_fragments/F';old=None;new={'source_id':'foreign'}
    with pytest.raises(ValueError):b.validate_delta({'manifest':{},'before_records':c.rows(s)},old,new,key)


def test_legacy_manifest_and_adapter_identity_are_unchanged(monkeypatch):
    original=SimpleNamespace(_prompt='old',_base_prompt='old',generation_config_sha256='old')
    assert b.scoped_adapter(original,{'manifest':{}},'proposal') is original
    new=b.scoped_adapter(original,{'manifest':{'evidence_binding_repair':{'path':'exact'}}},'proposal')
    assert new is not original and original._prompt=='old'
    assert new._response_model.__name__=='JsonProposal'
    assert new.generation_config_sha256!=original.generation_config_sha256


def test_inventory_keeps_all_shared_owners_and_relations_readonly(monkeypatch):
    m,s,d=scenario(monkeypatch)
    s['knowledge_relations/R']={'relation_id':'R','from_id':'E','to_id':'other','relation_type':'supports'}
    inv=b.inventory(m,s)
    assert set(inv['affected_keys'])=={'evidence_steps/E','claims/C','knowledge_relations/R'}
    assert [p.key for p in m.permissions if not p.create]==['evidence_steps/E']


@pytest.mark.parametrize('fault',[None,'foreign_write','word_change','drop_old_binding'])
def test_real_saved_initial_draft_no_approval_or_file_mutation(fault):
    import os
    from pathlib import Path
    from backend.pipeline.source_reconciliation_runner import read,body,validate_raw,effective_proposal,initial_review_base
    name=os.environ.get('WKP_EVIDENCE_BINDING_SAVED_ROOT')
    if not name:pytest.skip('explicit actual readonly fixture')
    root=Path(name);original={p:p.read_bytes() for p in root.iterdir() if p.is_file()}
    frozen=body(read(root/'scoped-materials.json'))
    raw=validate_raw(root,'proposal',body(read(root/'proposal.packet.json')))
    proposal=effective_proposal(frozen,raw['response'])
    if fault:
        ev=next(r for r in proposal.records if r.collection=='evidence_steps')
        if fault=='word_change':ev.payload['statement']='foreign words'
        elif fault=='drop_old_binding':ev.payload['source_fragment_ids']=[];ev.payload['source_fragment_id']='new'
        else:
            claim=next(r for r in frozen['before_records'] if r['collection']=='claims')
            proposal.records.append(c.Row.model_validate(claim))
        with pytest.raises(ValueError):initial_review_base(frozen,proposal)
    else:
        packet=initial_review_base(frozen,proposal)
        assert packet['unresolved_proposal_review']['content_write_authority'] is False
        assert packet['unresolved_proposal_review']['mandatory_original_unique_correction'] is True
        with pytest.raises(ValueError):c.validate_proposal(frozen,proposal)
    assert {p:p.read_bytes() for p in original}==original
