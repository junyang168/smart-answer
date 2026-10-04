from copy import deepcopy
import pytest
from backend.pipeline.viewpoint_audit_link_repair import select_repairs, launch_repair, package_for


def case():
    link = {'viewpoint_claim_link_id':'L', 'claim_id':'C', 'viewpoint_id':'V',
            'revision':1, 'link_type':'equivalent_component', 'component_locator':{'span':'all'},
            'evidence_step_ids':['E'], 'source_fragment_ids':['F'], 'claim_revision':1}
    packet = {'items':[{'item_id':'4:V:C', 'connections':[link], 'claim':{'claim_id':'C'},
                        'viewpoint':{'viewpoint_id':'V'}, 'evidence':['E'], 'sources':['S']}]}
    report = {'actions':[{'item_id':'4:V:C', 'verdict':'audit_objection_upheld', 'gpt':{'reason':'source'}}]}
    return packet, report


def test_only_three_fields_change_and_inputs_stay_immutable():
    packet, report = case(); original=deepcopy(packet)
    item=select_repairs(packet,report)[0]
    old=item['before_link']; new=item['after_link']
    assert {k for k in old if old[k]!=new[k]}=={'revision','link_type','component_locator'}
    assert new['link_type']=='supports' and new['component_locator'] is None
    assert packet==original
    assert new['evidence_step_ids']==['E'] and new['claim_revision']==1


@pytest.mark.parametrize('verdict',['unresolved','audit_objection_rejected'])
def test_other_verdict_cannot_launch(verdict, monkeypatch):
    packet, report=case(); report['actions'][0]['verdict']=verdict
    monkeypatch.setattr('subprocess.run',lambda *a,**k:pytest.fail('must not start process'))
    with pytest.raises(ValueError): launch_repair(None,packet,report)


@pytest.mark.parametrize('mutation',['ambiguous','foreign','already_supports','wrong_layer','missing'])
def test_scope_refusals(mutation):
    packet,report=case(); item=packet['items'][0]
    if mutation=='ambiguous': item['connections'].append(deepcopy(item['connections'][0]))
    if mutation=='foreign': item['connections'][0]['viewpoint_id']='OTHER'
    if mutation=='already_supports': item['connections'][0]['link_type']='supports'
    if mutation=='wrong_layer': item['item_id']='3:C'; report['actions'][0]['item_id']='3:C'
    if mutation=='missing': report['actions']=[]
    with pytest.raises(ValueError): select_repairs(packet,report)


def test_package_pins_reviewed_postimage_and_fresh_authority(tmp_path):
    import json
    packet, report=case(); old=packet['items'][0]['connections'][0]; old['decision_id']='D'
    states={('viewpoint_identity_decisions','D'):{'identity_candidate_id':'I'},
            ('viewpoint_identity_candidates','I'):{'seed_relation_ids':[]}}
    targets=select_repairs(packet,report)
    for label in ('gpt','opus'): (tmp_path/f'raw-{label}.json').write_text(json.dumps({'artifact_sha256':label}))
    before=deepcopy(states)
    package=package_for(states,{'targets':targets},{'gpt':{},'opus':{}},tmp_path)
    assert states==before
    link=package['viewpoint_claim_links'][0]
    assert {k:v for k,v in link.items() if k!='decision_id'}=={k:v for k,v in targets[0]['after_link'].items() if k!='decision_id'}
    decision=package['viewpoint_identity_decisions'][0]
    assert decision['input_sha256']==package['viewpoint_identity_candidates'][0]['generation_fingerprint']
    assert decision['reviewer_model_ids']==sorted(decision['reviewer_model_ids'])
    assert decision['reviewer_kind']=='system' and decision['approval_basis']=='dual_model_consensus'
