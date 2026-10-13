import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from backend.pipeline import exegesis_passage_source_closeout as closeout
from backend.pipeline import exegesis_passage_unit_job as job
from backend.pipeline.exegesis_grouping_transport import write_new


def fixture(tmp_path):
    source=tmp_path/'original.json'
    source.write_text(json.dumps({'script':[{'text':'before'}, {'text':'所以這一些也是很大的證據。'}, {'text':'after<svg/>separate'}]},ensure_ascii=False))
    frozen=write_new(tmp_path/'packet.json',dict(claims=[dict(claim_id='C'+a,source_id='S',claim_content_sha256='sha'+a,
        evidence_steps=[dict(fragments=[dict(paragraph_key='S0002',verbatim_excerpt='所以這一些也是很大的證據。')])]) for a in ['a','b','c']]))
    claims=[dict(id=a,original_claim_id='C'+a,source_id='S',claim_content_sha256='sha'+a,primary='') for a in ['a','b','c']]
    proposal=dict(units=[dict(unit_id='p000',claim_ids=['a','b'],passage_key='Matt.9.9-Matt.9.13'),dict(unit_id='p001',claim_ids=['c'],passage_key='Matt.9.14-Matt.9.17')],assignments={a:dict(unit_index=0 if a!='c' else 1) for a in ['a','b','c']})
    request=write_new(tmp_path/'initial-review.input.json',dict(stage='passage_membership',sources=[dict(source_id='S',path=str(source),file_sha256=hashlib.sha256(source.read_bytes()).hexdigest())],
        role_packet_path=str(tmp_path/'packet.json'),role_packet_sha256=frozen['artifact_sha256'],claims=claims,targets=proposal['units'],catalog=proposal['units'],context_radius=0,exact_fragments_only=True))
    def finding(ids):return dict(status='pass',reason='specific argument boundary',reviewed_claim_ids=ids,evidence=[dict(source_id='S',location='row:2',quote='所以這一些也是很大的證據。')],moves=[],suggested_passage_key=None,need_more_context='')
    report=write_new(tmp_path/'raw-review.json',dict(response=dict(findings={'p000':finding(['a','b']),'p001':finding(['c'])}),evidence_errors=[],binding=request['artifact_sha256']))
    return source,claims,proposal,request,report


def run(tmp_path,proposal,request,report,claims):
    return job.original_source_closeout(tmp_path,proposal,report,claims,request['sources'],{},SimpleNamespace())


def test_physically_checked_noop_does_not_call_model_or_overwrite_raw(tmp_path,monkeypatch):
    source,claims,proposal,request,report=fixture(tmp_path)
    report['response']['findings']['p000'].update(status='change',moves=[dict(claim_id='a',target_unit_id='p000',new_passage_key=None,reason='retain here')])
    raw=copy.deepcopy(report)
    monkeypatch.setattr(job,'model_stage',lambda *a,**k:pytest.fail('no-op does not need a model'))
    monkeypatch.setattr(job,'review_stage',lambda *a,**k:pytest.fail('unchanged membership does not need re-review'))
    effective,validated,disposition=run(tmp_path,proposal,request,report,claims)
    assert effective==proposal and validated['status']=='pass' and report==raw
    assert disposition['deterministic_dispositions']['p000']['type']=='already_applied'
    context=json.loads((tmp_path/'original-source-context.json').read_text())
    assert '<svg' not in json.dumps(context) and 'before' in json.dumps(context)


def test_sha_drift_stops_before_adjudication(tmp_path):
    source,claims,proposal,request,report=fixture(tmp_path)
    report['response']['findings']['p000']['status']='unresolved'
    source.write_text('changed')
    with pytest.raises(ValueError,match='source drift'):run(tmp_path,proposal,request,report,claims)


def test_quote_repair_preserves_independent_pass_and_raw_failure(tmp_path,monkeypatch):
    source,claims,proposal,request,report=fixture(tmp_path)
    report['response']['findings']['p000']['evidence'][0]['quote']='所以這也是很大的證據。'
    report['evidence_errors']=[dict(key='p000',error='non_verbatim_or_unprovided_physical_evidence',evidence=report['response']['findings']['p000']['evidence'][0])]
    raw=copy.deepcopy(report);calls=[]
    def model(*args):
        calls.append(args)
        return dict(dispositions={'p000':dict(decision='confirm_current',reason='original includes 一些',evidence=[dict(source_id='S',location='row:2',quote='所以這一些也是很大的證據。')],moves=[],passage_key=None,quote_corrections=[dict(evidence_index=0,quote='所以這一些也是很大的證據。')])})
    monkeypatch.setattr(job,'model_stage',model)
    monkeypatch.setattr(job,'review_stage',lambda *a,**k:pytest.fail('citation-only correction does not need semantic review'))
    effective,validated,_=run(tmp_path,proposal,request,report,claims)
    assert len(calls)==1 and len(calls[0][3]['claims'])==3
    assert validated['status']=='pass' and effective==proposal and report==raw


def test_real_member_change_requires_one_whole_book_review_then_stops(tmp_path,monkeypatch):
    source,claims,proposal,request,report=fixture(tmp_path)
    f=report['response']['findings']['p000'];f.update(status='change',moves=[dict(claim_id='a',target_unit_id='p001',new_passage_key=None,reason='original next section')])
    evidence=f['evidence'];calls=[]
    monkeypatch.setattr(job,'model_stage',lambda *a,**k:dict(dispositions={'p000':dict(decision='modify',reason='original next section',evidence=evidence,moves=[dict(claim_id='a',target_unit_id='p001')],passage_key=None,quote_corrections=[])}))
    def review(*args,**kwargs):
        calls.append((args,kwargs));updated=args[2]
        assert sum(len(u['claim_ids']) for u in updated['units'])==3
        assert len(args[3])==3 and len(updated['units'])==2
        # Deliberately leaves a disagreement: never run a second closeout.
        return copy.deepcopy(report)
    monkeypatch.setattr(job,'review_stage',review)
    effective,validated,disposition=run(tmp_path,proposal,request,report,claims)
    assert effective['units'][0]['claim_ids']==['b'] and effective['units'][1]['claim_ids']==['c','a']
    assert len(calls)==1 and disposition['requires_independent_review']
    assert json.loads((tmp_path/'original-source-closeout-result.json').read_text())['needs_human']


@pytest.mark.parametrize('invalid',['fake-quote','foreign-member','primary-conflict','cross-svg','semantic-citation'])
def test_unsupported_dispositions_cannot_release_semantic_changes(tmp_path,invalid):
    source,claims,proposal,request,report=fixture(tmp_path)
    f=report['response']['findings']['p000'];f.update(status='change',moves=[dict(claim_id='a',target_unit_id='p001',new_passage_key=None)])
    reader=closeout.load_reader(job.REVIEWER);cases,_,index=closeout.prepare(report,proposal,request,reader)
    d=dict(decision='modify',reason='specific reason',evidence=f['evidence'],moves=[dict(claim_id='a',target_unit_id='p001')],passage_key=None,quote_corrections=[])
    if invalid=='fake-quote':d['evidence']=[dict(source_id='S',location='row:2',quote='not in original')]
    if invalid=='foreign-member':d['moves'][0]['claim_id']='c'
    if invalid=='primary-conflict':claims[0]['primary']='Matt.9.10'
    if invalid=='cross-svg':d['evidence']=[dict(source_id='S',location='row:3',quote='afterseparate')]
    if invalid=='semantic-citation':d.update(decision='confirm_current',moves=[],quote_corrections=[dict(evidence_index=0,quote='所以這一些也是很大的證據。')])
    with pytest.raises(ValueError):closeout.apply(dict(dispositions={'p000':d}),cases,proposal,report,index,claims,job.locator,job.primary_fits)


def test_explicit_source_gap_survives_another_cases_passing_final_review(tmp_path,monkeypatch):
    source,claims,proposal,request,report=fixture(tmp_path)
    for f in report['response']['findings'].values():f.update(status='unresolved',need_more_context='S row:2 still lacks argument introduction')
    d=lambda decision:dict(decision=decision,reason='specific missing argument introduction',evidence=[dict(source_id='S',location='row:2',quote='所以這一些也是很大的證據。')] if decision!='unresolved' else [],moves=[],passage_key=None,quote_corrections=[])
    monkeypatch.setattr(job,'model_stage',lambda *a,**k:dict(dispositions={'p000':d('unresolved'),'p001':d('confirm_current')}))
    def review(*a,**k):
        result=copy.deepcopy(report)
        for f in result['response']['findings'].values():f.update(status='pass',need_more_context='')
        return result
    monkeypatch.setattr(job,'review_stage',review)
    _,validated,_=run(tmp_path,proposal,request,report,claims)
    assert validated['status']=='needs_resolution'
    assert validated['evidence_errors'][0]['error']=='original_source_unresolved'
    assert json.loads((tmp_path/'original-source-closeout-result.json').read_text())['needs_human']
