"""Withheld unanchored records remain pinned and disclosed, never usable support."""
from copy import deepcopy

import pytest

from backend.api.canonical_repository.knowledge_models import EvidenceStepRecord
from backend.api.canonical_repository.postgres_store import canonical_json,record_content_sha
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_route_materials import prepare_cvp_route_materials,verify_cvp_route_materials
from backend.pipeline.viewpoint_route_resolution import build_registry_route_packet
from backend.api.canonical_repository.viewpoint_resolution import ReviewClaim
from test_cvp_management_runner import setup,postgres_store,_temporary_postgres
from test_cvp_management_maintenance import _initial


def packet(material):
    return build_registry_route_packet(scope_label='test-current-cvp',
        approved_viewpoints=[material['revision']],
        claims=[ReviewClaim.model_validate(c) for c in material['claims']],
        viewpoint_claim_links=material['links'],existing_routes=material['existing_routes'],
        source_sections=material['source_sections'],existing_attestations=material['existing_attestations'],
        complete_cvp=True,unavailable_evidence=material.get('unavailable_evidence'))


def add_unavailable(store,**fields):
    evidence=EvidenceStepRecord.model_validate({'evidence_step_id':'E-withheld',
        'statement':'UNANCHORED TEXT MUST NOT BECOME MODEL EVIDENCE',
        'produced_claim_ids':['C1'],'support_eligibility':'withheld_missing_anchor',
        'anchor_quality':'missing',**fields}).model_dump(mode='json')
    claim=deepcopy(store.get_record('claims','C1'));claim['evidence_step_ids'].append('E-withheld')
    with store.connect() as conn:
        conn.execute('''INSERT INTO wang_knowledge.objects(collection,object_id,revision,content_sha256,payload,review_status,visibility)
            VALUES('evidence_steps','E-withheld',1,%s,%s::jsonb,%s,%s)''',
            (record_content_sha(evidence),canonical_json(evidence),evidence['review_status'],evidence['visibility']))
        conn.execute("UPDATE wang_knowledge.objects SET content_sha256=%s,payload=%s::jsonb WHERE collection='claims' AND object_id='C1'",
            (record_content_sha(claim),canonical_json(claim)))
    return evidence


def test_material_and_model_packet_disclose_withheld_without_losing_members_or_upgrading_evidence(setup):
    store,_,_,_,_,view,_,_=_initial(setup);vid=view['viewpoint_id']
    original=prepare_cvp_route_materials(store,vid)
    assert 'unavailable_evidence' not in original and 'unavailable_evidence' not in packet(original)
    assert packet(original | {'unavailable_evidence':[]})==packet(original)
    withheld=add_unavailable(store)
    material=prepare_cvp_route_materials(store,vid)
    assert len(material['claims'])==len(original['claims'])==1
    assert material['claims'][0]['evidence']==original['claims'][0]['evidence']
    assert ('evidence_steps','E-withheld') in {(r['collection'],r['object_id']) for r in material['record_pins']}
    assert material['unavailable_evidence']==[{'claim_id':'C1','evidence_step_id':'E-withheld',
        'support_eligibility':'withheld_missing_anchor','anchor_quality':'missing','usable_for_route_binding':False}]
    actual=packet(material)
    assert actual['unavailable_evidence']==material['unavailable_evidence']
    assert actual['membership_ledger']['out_of_scope_members']==[]
    assert actual['approved_viewpoints'][0]['attesting_source_roster']==['S1']
    assert all('E-withheld' not in c['evidence_step_ids'] for c in actual['claim_components'])
    assert 'UNANCHORED TEXT' not in canonical_json(actual)
    assert actual['packet_sha256']==sha256_json({k:v for k,v in actual.items() if k!='packet_sha256'})
    for fields in ({'claim_id':'foreign'}, {'evidence_step_id':'E1'},
                   {'usable_for_route_binding':True}, {'statement':'unanchored prose'}):
        invalid=deepcopy(material);invalid['unavailable_evidence'][0].update(fields)
        with pytest.raises(ValueError,match='disclosure is inconsistent'):packet(invalid)
    duplicate=deepcopy(material);duplicate['unavailable_evidence']*=2
    with pytest.raises(ValueError,match='disclosure is inconsistent'):packet(duplicate)
    assert store.get_record('evidence_steps','E-withheld')==withheld
    verify_cvp_route_materials(store,material)
    changed=deepcopy(withheld);changed['support_eligibility']='withheld_unreviewed'
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.objects SET content_sha256=%s,payload=%s::jsonb WHERE collection='evidence_steps' AND object_id='E-withheld'",
            (record_content_sha(changed),canonical_json(changed)))
    with pytest.raises(Exception,match='changed|pin|drift'):
        verify_cvp_route_materials(store,material)


@pytest.mark.parametrize('fields',[
    {'produced_claim_ids':['foreign-claim']},
    {'anchor_quality':'uncertain'},
    {'source_fragment_id':'F1'},
    {'support_eligibility':'eligible'},
])
def test_unavailable_bridge_rejects_foreign_owner_wrong_anchor_fragments_and_unmarked_missing(setup,fields):
    store,_,_,_,_,view,_,_=_initial(setup)
    add_unavailable(store,**fields)
    with pytest.raises(ValueError,match='Route evidence incomplete'):
        prepare_cvp_route_materials(store,view['viewpoint_id'])


def test_claim_without_any_anchored_source_still_blocks_route_materials(setup):
    store,_,_,_,_,view,_,_=_initial(setup);add_unavailable(store)
    claim=store.get_record('claims','C1');claim['evidence_step_ids']=['E-withheld']
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.objects SET content_sha256=%s,payload=%s::jsonb WHERE collection='claims' AND object_id='C1'",
            (record_content_sha(claim),canonical_json(claim)))
    with pytest.raises(ValueError,match='not source-local'):
        prepare_cvp_route_materials(store,view['viewpoint_id'])
