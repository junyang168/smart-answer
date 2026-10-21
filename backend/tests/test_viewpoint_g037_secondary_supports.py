from copy import deepcopy
import pytest
from backend.pipeline import viewpoint_g037_secondary_supports as m
from backend.pipeline.viewpoint_authorized_source_repair_357 import fingerprint
from backend.api.canonical_repository.postgres_store import record_content_sha


def target():
    old={'viewpoint_claim_link_id':m.LINK,'claim_id':m.CLAIM,'viewpoint_id':m.CV,
        'revision':1,'link_type':'equivalent_component','component_locator':{'exact_text':'appearance'},
        'evidence_bindings':[{'evidence_step_id':'E'}]}
    new=deepcopy(old);new.update(revision=2,link_type='supports',component_locator=None)
    return {'targets':[{'claim':{'claim_id':m.CLAIM,'revision':1},'before_link':old,'after_link':new}]}


@pytest.mark.parametrize('mutation',[None,'target','claim_version','endpoint','retire','source','population'])
def test_authority_only_changes_one_connection(mutation):
    p=target();t=p['targets'][0]
    if mutation=='target':t['before_link']['viewpoint_claim_link_id']='foreign'
    if mutation=='claim_version':t['claim']['revision']=2
    if mutation=='endpoint':t['after_link']['viewpoint_id']='foreign'
    if mutation=='retire':t['after_link']['effective_state']='retired'
    if mutation=='source':t['after_link']['evidence_bindings']=[]
    if mutation=='population':p['targets'].append(deepcopy(t))
    if mutation:
        with pytest.raises(ValueError):m.validate_target(p)
    else:m.validate_target(p)


def transition(monkeypatch):
    from backend.api.canonical_repository import postgres_store
    monkeypatch.setattr(postgres_store,'normalize_package',lambda pkg:pkg)
    old={'claims':{'C':{'claim_id':'C','statement':'old','revision':2}},
        'viewpoint_claim_links':{f'L{n}':{'link_id':f'L{n}','revision':1} for n in range(6)},
        'viewpoint_identity_candidates':{},'viewpoint_identity_decisions':{}}
    original={(c,k):v for c,rows in old.items() for k,v in rows.items()}
    original['viewpoint_identity_candidates','old']={'id':'old','revision':1}
    original['viewpoint_identity_decisions','old']={'id':'old','revision':1}
    live=deepcopy(original);pkg={c:{} for c in old};ops=[]
    for (c,k),v in original.items():
        if k=='old':continue
        new=dict(v,revision=v['revision']+1)
        if c=='claims':new['statement']='new'
        live[c,k]=new;pkg[c][k]=new
        ops.append({'collection':c,'object_id':k,'operation':'update','before_sha256':record_content_sha(v),'after_revision':new['revision'],'payload':new})
    for c in ('viewpoint_identity_candidates','viewpoint_identity_decisions'):
        for n in range(6):
            k=f'N{n}';v={'id':k,'revision':1};live[c,k]=v;pkg[c][k]=v
            ops.append({'collection':c,'object_id':k,'operation':'create','after_revision':1,'payload':v})
    packet={'claim_revision':{'before':original['claims','C']},'targets':[{'before_link':dict(original['viewpoint_claim_links',f'L{n}'],viewpoint_claim_link_id=f'L{n}')} for n in range(6)]}
    # Use the same persisted predecessor fields.
    for t in packet['targets']:
        k=('viewpoint_claim_links',t['before_link']['viewpoint_claim_link_id']);original[k]=t['before_link']
        next(o for o in ops if (o['collection'],o['object_id'])==k)['before_sha256']=record_content_sha(t['before_link'])
    before={'collections':sorted(old),'pre_fingerprint_sha256':fingerprint(original,sorted(old))}
    return deepcopy(live),deepcopy(before),deepcopy(packet),deepcopy({'operations':ops}),deepcopy(pkg),deepcopy(original)


@pytest.mark.parametrize('mutation',[None,'unrelated','missing','postimage','duplicate','package','predecessor','before','collection'])
def test_exact_applied_19ops_and_whole_registry_reverse(monkeypatch,mutation):
    live,before,p,plan,pkg,original=transition(monkeypatch)
    if mutation=='unrelated':live['claims','extra']={'id':'extra'}
    if mutation=='missing':plan['operations'].pop()
    if mutation=='postimage':live['claims','C']['statement']='tampered'
    if mutation=='duplicate':plan['operations'][-1]=deepcopy(plan['operations'][-2])
    if mutation=='package':pkg['claims']['C']=dict(pkg['claims']['C'],statement='tampered')
    if mutation=='predecessor':p['claim_revision']['before']['statement']='tampered'
    if mutation=='before':before['pre_fingerprint_sha256']='tampered'
    if mutation=='collection':live['foreign','X']={}
    if mutation:
        with pytest.raises(ValueError):m.verify_transition(live,before,p,plan,pkg)
    else:assert m.verify_transition(live,before,p,plan,pkg)==original


def test_separate_single_use_reservation_does_not_reset_history(tmp_path):
    source=tmp_path/'old';source.mkdir();history=source/'old-reservation.json';history.write_text('frozen')
    m.reserve(tmp_path/'new',source)
    assert history.read_text()=='frozen'
    with pytest.raises(ValueError,match='already reserved'):m.reserve(tmp_path/'repeat',source)


def test_missing_cached_adjudication_never_calls_model(monkeypatch,tmp_path):
    from backend.pipeline import viewpoint_audit_source_adjudication as a
    monkeypatch.setattr(a,'build_packet',lambda audit,store:{})
    monkeypatch.setattr(m,'read_artifact',lambda path:{})
    monkeypatch.setattr(a,'source_review_adapter',lambda *args:pytest.fail('must not construct fresh model'))
    with pytest.raises(ValueError,match='no saved adjudication'):m.consume_report(tmp_path/'audit',tmp_path,object())
