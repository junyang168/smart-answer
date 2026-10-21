"""Engineering recovery never alters saved answers or grants content approval."""
from copy import deepcopy
import json
import os
from pathlib import Path
import pytest
from backend.pipeline import source_normalization_recovery as x
from backend.pipeline import source_reconciliation_runner as r, source_reconciliation as c


def proposal():
    return c.Proposal.model_validate({'records':[{'collection':'claims','object_id':'claim',
        'payload':{'statement':'exact','occurrences':[{'anchors':[{'quote':'unchanged','evidence_independence':{'basis_group_id':'old'}}]}]}}],
        'dispositions':[]})


@pytest.mark.parametrize('fault',['none','statement','quote','record','denominator','reason','no_delta'])
def test_delta_only_independence(fault):
    old=proposal();new=old.model_copy(deep=True)
    new.records[0].payload['occurrences'][0]['anchors'][0]['evidence_independence']={'basis_group_id':'new'}
    if fault=='statement':new.records[0].payload['statement']='changed'
    if fault=='quote':new.records[0].payload['occurrences'][0]['anchors'][0]['quote']='changed'
    if fault=='record':new.records[0].object_id='foreign'
    if fault=='denominator':new.records.append(new.records[0])
    if fault=='reason':new.unadmitted_candidates=[{'reason':'changed'}]
    if fault=='no_delta':new=old
    if fault=='none':assert len(x.prove_delta(old,new))==1
    else:
        with pytest.raises(ValueError):x.prove_delta(old,new)


@pytest.fixture
def actual():
    if os.environ.get('WKP357_REAL_SAVED_READONLY')!='1':pytest.skip('real saved readonly opt-in')
    path=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/owner-g103-basis-normalization-engineering-recovery.json')
    d=json.loads(path.read_text());root=Path(d['action_root'])
    return d,root,r.body(r.read(root/'scoped-materials.json'))


@pytest.mark.parametrize('fault',[None,'materials','root','proposal_budget','review_budget','correction_budget','content','delta','leaf','direction','foreign_run'])
def test_actual_full_recovery_packet_zero_calls_writes(actual,monkeypatch,fault):
    d,root,f=actual;d=deepcopy(d)
    if fault=='materials':d['materials_sha256']='0'*64
    if fault=='root':d['action_root']='/foreign'
    if fault=='proposal_budget':d['additional_proposal_limit']=1
    if fault=='review_budget':d['additional_review_limit']=2
    if fault=='correction_budget':d['additional_correction_limit']=1
    if fault=='content':d['human_content_approval']=True
    if fault=='delta':d['delta_sha256']='0'*64
    if fault=='leaf':d['original_files']['proposal.raw']['file_sha256']='0'*64
    if fault=='direction':d['engineering_direction']=''
    if fault=='foreign_run':d['run_id']='foreign'
    d=r.seal(r.body(d));read=r.read
    monkeypatch.setattr(r,'read',lambda p:d if p==root/x.NAME else read(p))
    def forbidden(*a,**kw):raise AssertionError('no writes/calls in saved preflight')
    monkeypatch.setattr(r,'write',forbidden)
    before={p:r.contract.file_sha256(p) for p in root.iterdir() if p.is_file()}
    if fault:
        with pytest.raises(ValueError):x.packet(root,f)
    else:
        proposed,packet=x.packet(root,f)
        assert len(packet['normalization_engineering_recovery']['exact_flag_delta'])==3
        assert len(proposed.records)==7
        c.validate_proposal(f,proposed)
        assert packet['normalization_engineering_recovery']['another_recovery_allowed'] is False
        assert packet['normalization_engineering_recovery']['original_review']['verdicts']
        assert len(packet['claim_citation_purpose_review']['items'])==1
    assert before=={p:r.contract.file_sha256(p) for p in root.iterdir() if p.is_file()}


@pytest.mark.parametrize('accepted',[True,False])
def test_recovery_stage_once_without_proposal_or_correction_budget(monkeypatch,tmp_path,accepted):
    calls=[];cache={}
    monkeypatch.setattr(c,'unresolved_consumers',lambda f:[])
    monkeypatch.setattr(r,'build_proposal_packet',lambda f:{})
    monkeypatch.setattr(r,'effective_proposal',lambda *a:proposal())
    monkeypatch.setattr(c,'validate_review',lambda p,v:v.get('approved',False))
    monkeypatch.setattr(x,'packet',lambda *a:(proposal(),{'proof':'exact'}))
    r.write(tmp_path/x.NAME,{'bound':'direction'})
    class Runner(r.Runner):
        def guard(self,*a,**kw):pass
        def review_input(self,*a):return {}
        def call(self,stage,packet):
            if stage not in cache:
                calls.append(stage)
                cache[stage]={'response':{'approved':accepted if stage==x.STAGE else False}}
            return cache[stage]
    runner=Runner(tmp_path,None);runner.root=tmp_path
    f={'claim_issue_context':{'complete_input_repair':{'correction_limit':0}}}
    for _ in range(2):
        if accepted:assert runner.reviewed(f)[1]['response']['approved']
        else:
            with pytest.raises(r.Stop,match='no further recovery'):runner.reviewed(f)
    assert calls==['proposal','initial_review',x.STAGE]


def test_actual_full_package_reconstructs_corrected_proposal_not_legacy(actual,monkeypatch):
    d,root,f=actual;read=r.read;validate=r.validate_raw
    monkeypatch.setattr(r,'read',lambda p:d if p==root/x.NAME else read(p))
    proposed,packet=x.packet(root,f)
    # Isolated verifier test ONLY; never persists this fabricated test review.
    response={'verdicts':[{'record_key':v,'approved':True,'reason':'isolated test','problems':[]}
        for v in [p['record_key'] for p in packet['proposal']['dispositions']]+[i['record_key'] for i in packet['claim_citation_purpose_review']['items']]]}
    raw=r.seal({'response':response,'completed_at':'2026-10-10T19:00:00+00:00','stage':x.STAGE})
    package=c.compile_package(f,proposed,raw,frozen_sha256=r.sha256_json(f))
    package['source_repair_application']={'run_root':str(root),'terminal_stage':x.STAGE,
        'review_sha256':raw['artifact_sha256'],'proposal_sha256':r.sha256_json(proposed.model_dump())}
    def read_test(p):
        if p==root/x.NAME:return d
        if p==root/(x.STAGE+'.packet.json'):return r.seal(packet)
        if p==root/(x.STAGE+'.raw.json'):return raw
        return read(p)
    monkeypatch.setattr(r,'read',read_test)
    monkeypatch.setattr(r,'validate_raw',lambda path,stage,p:raw if stage==x.STAGE else validate(path,stage,p))
    r.validate_reviewed_records(package,f,frozen_sha256=r.sha256_json(f))
    package['claims'][0]['statement']='unapproved text'
    with pytest.raises(ValueError):r.validate_reviewed_records(package,f,frozen_sha256=r.sha256_json(f))
