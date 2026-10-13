from copy import deepcopy
import json
from types import SimpleNamespace
import pytest
from backend.pipeline import viewpoint_357_claim_qualifier_repair as m


def live():
    claim={'claim_id':m.CLAIM,'revision':1,'statement':'ψυχή應當理解為「魂」，不應譯成「靈魂」。',
           'evidence_step_ids':['E1','E3'],'scripture_refs':['路加福音 12:20','馬太福音 2:20'],'occurrences':[]}
    link={'claim_id':m.CLAIM,'revision':2,'viewpoint_claim_link_id':m.LINK,'validated_against_viewpoint_revision_id':m.CVR,
          'decision_id':'D1','link_type':'equivalent_component','pinned_claim_revision':1,'component_locator':{},'evidence_bindings':[]}
    s={('claims',m.CLAIM):claim,('viewpoint_claim_links',m.LINK):link,
       ('source_documents','SRC-2019_2_15_mp4-3399771e82cb'):{'source_id':'SRC-2019_2_15_mp4-3399771e82cb'},
       ('viewpoint_revisions',m.CVR):{'core_proposition':'local Luke'},('viewpoint_identity_decisions','D1'):{'decision':'match_existing'}}
    for e in ['E1','E3']:
        s['evidence_steps',e]={'produced_claim_ids':[m.CLAIM],'source_fragment_ids':[e+'F']}
        s['source_fragments',e+'F']={'source_id':'SRC-2019_2_15_mp4-3399771e82cb','paragraph_key':'S0001','verbatim_excerpt':'quote'}
    return s


def setup(monkeypatch):
    monkeypatch.setattr(m,'load_context_source',lambda *a: ('/original',m.SOURCE_SHA,SimpleNamespace(body_sha256='body',spoken_rows=[{'text':'original quote'}])))


def test_minimum_repair_preserves_all_evidence_and_does_not_alias(monkeypatch,tmp_path):
    setup(monkeypatch); s=live(); original=deepcopy(s)
    p,packet=m.prepare(s,tmp_path)
    assert s==original
    new=p['claims'][0]
    assert new['statement']==m.PREFIX+original['claims',m.CLAIM]['statement']
    for field in ['evidence_step_ids','scripture_refs','occurrences']: assert new[field]==original['claims',m.CLAIM][field]
    p['source_repair_application']={'review_packet':packet}
    json.dumps(p)  # authority must not alias/circularly include its own packet
    assert 'source_repair_application' not in packet['proposed_package']


@pytest.mark.parametrize('change',['claim','link','foreign','original','quote','reciprocity','approved_relation'])
def test_drift_or_unplanned_dependency_stops_before_model(monkeypatch,tmp_path,change):
    setup(monkeypatch); s=live()
    if change=='claim': s['claims',m.CLAIM]['revision']=2
    elif change=='link': s['viewpoint_claim_links',m.LINK]['validated_against_viewpoint_revision_id']='other'
    elif change=='foreign': s['argument_route_attestations','A']={'claim_ids':[m.CLAIM]}
    elif change=='original': monkeypatch.setattr(m,'SOURCE_SHA','changed')
    elif change=='quote': s['source_fragments','E1F']['verbatim_excerpt']='invented'
    elif change=='reciprocity': s['evidence_steps','E1']['produced_claim_ids']=[]
    else: s['claim_relations','R']={'from_id':m.CLAIM,'review_status':'system_approved'}
    if change=='original':
        monkeypatch.setattr(m,'load_context_source',lambda *a:('/original','prior',None))
    with pytest.raises(ValueError): m.prepare(s,tmp_path)
