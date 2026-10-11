from copy import deepcopy
import pytest
from backend.pipeline.viewpoint_applied_link_repair_continuation import verify_transition
from backend.pipeline.viewpoint_authorized_source_repair_357 import fingerprint

def case():
    old={'viewpoint_claim_link_id':'L','revision':1,'link_type':'equivalent_component','component_locator':{'text':'old'}}
    new=dict(old,revision=2,link_type='supports',component_locator=None)
    before={('viewpoint_claim_links','L'):old,('claims','C'):{'statement':'unchanged'}}
    live=deepcopy(before);live['viewpoint_claim_links','L']=new
    live['viewpoint_identity_candidates','I']={'revision':1,'identity_candidate_id':'I'}
    live['viewpoint_identity_decisions','D']={'revision':1,'identity_decision_id':'D'}
    plan={'operations':[{'collection':c,'object_id':i,'operation':'update' if i=='L' else 'create',
                        'payload':live[c,i],'after_revision':live[c,i]['revision']} for c,i in live if c!='claims']}
    collections=sorted({c for c,i in live})
    return live,{'collections':collections,'pre_fingerprint_sha256':fingerprint(before,collections)}, {'targets':[{'before_link':old}]},plan,before

def test_reverse_exact_applied_transition_no_writes():
    live,before,packet,plan,original=case();copy=deepcopy(live)
    assert verify_transition(live,before,packet,plan)==original and live==copy

@pytest.mark.parametrize('change',['foreign_claim','extra_record','changed_link','dropped_create','extra_op','wrong_operation','duplicate'])
def test_reverse_rejects_any_unproved_registry_mutation(change):
    live,before,packet,plan,_=case()
    if change=='foreign_claim':live['claims','C']['statement']='changed'
    elif change=='extra_record':live['claims','X']={'statement':'new'}
    elif change=='changed_link':live['viewpoint_claim_links','L']['revision']=3
    elif change=='dropped_create':del live['viewpoint_identity_decisions','D']
    elif change=='extra_op':plan['operations'].append(plan['operations'][0])
    elif change=='duplicate':plan['operations'][1]=plan['operations'][0]
    else:plan['operations'][1]['operation']='update'
    with pytest.raises(ValueError):verify_transition(live,before,packet,plan)


@pytest.mark.parametrize('identity',['legacy','shared_text','contextual','foreign','ambiguous'])
def test_saved_supports_review_uses_exact_transport_without_generation(tmp_path,monkeypatch,identity):
    from types import SimpleNamespace
    from backend.pipeline import viewpoint_applied_link_repair_continuation as m
    from backend.pipeline import viewpoint_audit_link_repair as repair
    from backend.pipeline import viewpoint_resolution_runtime as runtime
    adapters={(True,False):SimpleNamespace(prompt_sha256='legacy',generation_config_sha256='old'),
              (False,False):SimpleNamespace(prompt_sha256='shared_text',generation_config_sha256='text'),
              (False,True):SimpleNamespace(prompt_sha256='contextual',generation_config_sha256='records')}
    wanted={'legacy':'old','shared_text':'text','contextual':'records','foreign':'foreign','ambiguous':'old'}
    if identity=='ambiguous':adapters[False,False]=adapters[True,False]
    monkeypatch.setattr(m,'read_artifact',lambda _:dict(prompt_sha256='legacy' if identity=='ambiguous' else identity,
        generation_config_sha256=wanted[identity]))
    monkeypatch.setattr(runtime,'subscription_client',lambda *a:object())
    monkeypatch.setattr(repair,'link_review_adapter',lambda c,p,legacy_serialization,contextual_semantics:
        adapters[legacy_serialization,contextual_semantics])
    calls=[]
    monkeypatch.setattr(runtime,'read_saved_model_response',lambda a,p,r:(calls.append(a) or {'saved':True}))
    if identity in ('foreign','ambiguous'):
        with pytest.raises(ValueError,match='missing or ambiguous'):m.read_supports_review(tmp_path,tmp_path,'opus','claude','M',{})
        assert not calls
    else:
        assert m.read_supports_review(tmp_path,tmp_path,'opus','claude','M',{})=={'saved':True}
        assert len(calls)==1


def test_saved_multiple_target_transport_requires_actual_packet_and_exact_identity(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from backend.pipeline import viewpoint_applied_link_repair_continuation as m
    from backend.pipeline import viewpoint_audit_link_repair as repair
    from backend.pipeline import viewpoint_resolution_runtime as runtime
    monkeypatch.setattr(m,'read_artifact',lambda _:dict(prompt_sha256='multi',generation_config_sha256='multi-config'))
    monkeypatch.setattr(runtime,'subscription_client',lambda *a:object())
    def adapter(c,p,**kw):
        return SimpleNamespace(prompt_sha256='multi' if kw.get('multiple_targets_per_claim') else str(kw),
                               generation_config_sha256='multi-config' if kw.get('multiple_targets_per_claim') else 'old')
    monkeypatch.setattr(repair,'link_review_adapter',adapter)
    calls=[]
    monkeypatch.setattr(runtime,'read_saved_model_response',lambda a,p,r:calls.append(p) or {'saved':True})
    packet={'targets':[{'claim':{'claim_id':'C'}},{'claim':{'claim_id':'C'}}]}
    assert m.read_supports_review(tmp_path,tmp_path,'gpt','codex','M',packet)=={'saved':True}
    assert calls==[packet]
    with pytest.raises(ValueError):m.read_supports_review(tmp_path,tmp_path,'gpt','codex','M',{'targets':[packet['targets'][0]]})
    assert calls==[packet]
