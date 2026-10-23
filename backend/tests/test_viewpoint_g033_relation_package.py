from copy import deepcopy
import json
import pytest
from backend.tests.test_viewpoint_g033_identity_repair import case, storage_case
from backend.pipeline import viewpoint_g033_relation_package as subject
from backend.pipeline import viewpoint_g033_identity_repair as identity
from backend.pipeline import viewpoint_g033_relation_review as relations

def fixture():
    context,proposal=case()
    context.update(before_revision=storage_case()['context']['before_revision'],
        before_viewpoint={'viewpoint_id':identity.VIEWPOINT,'revision':1,'current_revision_id':identity.OLD_REVISION})
    targets=[]; states={}
    for old,chosen in zip(context['before_links'],proposal['links'],strict=True):
        old.update(revision=1,viewpoint_id=identity.VIEWPOINT,decision_id='D'+old['claim_id'])
        after=deepcopy(old); after.update(revision=2,link_type=chosen['link_type'],component_locator=chosen['component_locator'],validated_against_viewpoint_revision_id='OLD-PLANNED')
        targets.append({'before_link':old,'after_link':after,'reason':chosen['reason']})
        states['viewpoint_identity_decisions',old['decision_id']]={'identity_candidate_id':'I'+old['claim_id']}
        states['viewpoint_identity_candidates','I'+old['claim_id']]={'seed_relation_ids':[]}
    old={'context':context,'proposal':proposal,'targets':targets,'after_revision':storage_case()['after_revision'],
        'after_relations':[{'viewpoint_relation_id':r['relation_id'],'source_viewpoint_id':'OTHER','target_viewpoint_id':identity.VIEWPOINT,'relation_type':r['relation_type'],'reason':r['reason']} for r in proposal['relations']]}
    selected={'relations':[{'relation_id':rid,'disposition':'proposed_relation','relation_type':'specializes','reason':'Saved model reason '+rid,'quotes':[]} for rid in sorted(relations.RELATIONS)]}
    return old,selected,states

def test_compile_exact_semantics_and_fresh_pending_metadata():
    old,selected,_=fixture(); before=deepcopy(old)
    packet=subject.compile_packet(old,selected,[])
    assert old==before
    assert packet['after_revision']['review_status']=='candidate'
    assert packet['after_revision']['approved_at'] is None
    assert packet['after_revision']['provenance']['review_artifact_sha256']==subject.PENDING
    assert packet['proposal']['links']==old['proposal']['links']
    assert packet['proposal']['core_proposition']==old['proposal']['core_proposition']
    for row in packet['proposal']['relations']:
        original=next(r for r in old['proposal']['relations'] if r['relation_id']==row['relation_id'])
        if row['relation_id'] in relations.RELATIONS: assert row['relation_type']=='specializes'
        else: assert row==original
    assert packet==subject.compile_packet(old,selected,[])

@pytest.mark.parametrize('change',['missing','duplicate','deferred','null'])
def test_scope_and_hold_fail_closed(change):
    old,selected,_=fixture()
    if change=='missing': selected['relations'].pop()
    if change=='duplicate': selected['relations'][1]=deepcopy(selected['relations'][0])
    if change=='deferred': selected['relations'][0]['disposition']='needs_new_scope'
    if change=='null': selected['relations'][0]['relation_type']=None
    with pytest.raises(ValueError): subject.compile_packet(old,selected,[])

def test_package_exact_postimage_and_new_real_review_binding(tmp_path):
    old,selected,states=fixture(); packet=subject.compile_packet(old,selected,[])
    for name in ('gpt','opus'): (tmp_path/f'raw-{name}.json').write_text(json.dumps({'artifact_sha256':name}))
    reviews={'gpt':{'approved':True},'opus':{'approved':True}}
    package=subject.package_for(states,packet,reviews,tmp_path)
    revision=package['viewpoint_revisions'][0]
    assert revision['review_status']=='system_approved'
    assert revision['approved_by']==subject.POLICY
    assert revision['provenance']['review_artifact_sha256']==subject.sha256_json(reviews)
    assert package['viewpoint_claim_links']==[t['after_link'] for t in packet['targets']]
    assert sorted(d['identity_decision_id'] for d in package['viewpoint_identity_decisions'])==packet['metadata_materialization_contract']['new_decision_ids']

def test_missing_cache_never_invokes_model(tmp_path,monkeypatch):
    from backend.pipeline.viewpoint_audit_link_repair import BASE
    monkeypatch.setattr(BASE.__class__,'resolve',lambda self,*a,**kw: BASE/'g033-owner-relation-reconsideration-runtime-20261004-v1')
    monkeypatch.setattr(subject,'call_model',lambda *a,**k: pytest.fail('new model fallback'))
    with pytest.raises(ValueError,match='cache missing'): subject.load_seed(tmp_path,tmp_path,None)
