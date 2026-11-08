"""Counterexamples for single-purpose coverage; source and claims stay untouched."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest
from backend.pipeline import claim_flash_coverage as coverage, claim_update_flash as flash
from backend.pipeline import evidence_refresh as e
from backend.tests.test_claim_flash_source import fixture


class Ports:
    identity={'gpt':{'backend':'test','model':'test','effort':'medium'},'jev':e.MODEL}
    def __init__(self, units=None, citations=None, verdict='covered', disposition='already_recorded', fail=None):
        self.units=units;self.citations=citations or [];self.verdict=verdict;self.disposition=disposition
        self.calls=[];self.requests=[];self.fail=fail
    def judge(self, request):
        self.calls.append('jev');self.requests.append(request)
        if self.fail=='jev':raise TimeoutError('unknown Jev result')
        return {'model':e.MODEL,'answers':{key:{'type':'choice','choice':self.verdict,
            'probabilities':{c:float(c==self.verdict) for c in spec['criteria']}}
            for key,spec in request['questions'].items()}}
    def repair(self, request):
        task=request['input']['task'];self.calls.append(task);self.requests.append(request)
        if self.fail==task:raise TimeoutError('unknown GPT result')
        items=[]
        for job in request['input']['jobs']:
            if task=='source_preservation_inventory':
                pk=next(iter(job['paragraphs']));text=job['paragraphs'][pk]
                items.append({'job_id':job['job_id'],'units':self.units if self.units is not None else [
                    {'paragraph_key':pk,'quote':text,'statement':text}], 'citations':self.citations})
            else:
                findings=[]
                for candidate in job['candidates']:
                    src=candidate['source_item']
                    findings.append({'candidate_ids':[candidate['candidate_id']], 'paragraph_key':src['paragraph_key'],
                        'quote':src['quote'],'statement':src.get('statement',''),'reference':src.get('reference',''),
                        'verdict':self.disposition,'record_refs':[next(iter(request['input']['all_sermon_statements']))],
                        'reason':'同一內容已有保存。','source_notes':[]})
                items.append({'job_id':job['job_id'],'findings':findings})
        return {'items':items}


def plan(sources):
    return {'source_id':'S','source_body_sha256':sources['S']['body_sha256'],
            'groups':[{'group_id':'G','title':'本題','paragraph_keys':list(sources['S']['paragraphs'])}]}


def test_whole_group_source_is_shared_but_each_unit_has_own_question(tmp_path):
    records,sources=fixture(tmp_path,['第一個意思。','第二個意思。'],[('S0001','第一個意思。')])
    units=[{'paragraph_key':pk,'quote':p['text'],'statement':p['text']} for pk,p in sources['S']['paragraphs'].items()]
    ports=Ports(units);before=deepcopy(records)
    result=coverage.check(records,sources,'S',plan(sources),tmp_path/'run',ports)
    assert result['units']==2 and result['database_writes']==0 and records==before
    assert ports.calls==['source_preservation_inventory','jev']
    source_request=ports.requests[0]
    assert len(source_request['input']['jobs'][0]['paragraphs'])==2
    assert 'all_sermon_statements' not in source_request['input']
    assert len(ports.requests[1]['questions'])==2
    assert 'claims' not in ports.requests[1]['state']


def test_no_missing_material_does_not_force_gpt_review(tmp_path):
    records,sources=fixture(tmp_path);ports=Ports()
    result=coverage.check(records,sources,'S',plan(sources),tmp_path/'run',ports)
    assert result['status']=='checked' and result['findings']==[]
    replay=Ports(fail='source_preservation_inventory')
    assert coverage.check(records,sources,'S',plan(sources),tmp_path/'run',replay)==result
    assert replay.calls==[]


def test_position_and_full_bound_quotes_are_supplied_before_claiming_omission(tmp_path):
    records,sources=fixture(tmp_path)
    records['position_nodes/P']={'retired':False,'payload':{'position_id':'P','title':'反方的立場。',
        'attribution':'一些神學家','source_fragment_ids':['F0']}}
    ports=Ports(verdict='partial');result=coverage.check(records,sources,'S',plan(sources),tmp_path/'run',ports)
    job=next(x for x in ports.requests if x.get('input',{}).get('task')=='reverse_coverage_adjudication')
    assert job['input']['position_attributions']
    detail=next(iter(job['input']['jobs'][0]['existing_details'].values()))
    assert detail['bindings'][0]['quote']=='教授原話。' and detail['bindings'][0]['binding_verified']
    assert result['findings'][0]['findings'][0]['existing_record_keys']
    assert not result.get('updates') and result['database_writes']==0


def test_only_preview_can_have_no_units_and_no_calls_to_next_layers(tmp_path):
    records,sources=fixture(tmp_path);ports=Ports(units=[])
    result=coverage.check(records,sources,'S',plan(sources),tmp_path/'run',ports)
    assert result['units']==0 and result['status']=='checked'
    assert ports.calls==['source_preservation_inventory']
    assert result['claim_qualification']=='not_granted'


def test_material_changes_reuse_source_inventory_but_not_old_semantic_answer(tmp_path):
    records,sources=fixture(tmp_path);ports=Ports()
    first=coverage.check(records,sources,'S',plan(sources),tmp_path/'run',ports)
    records['evidence_steps/E']['payload']['statement']='另一個意思。'
    changed=Ports(verdict='missing',disposition='missing_material')
    second=coverage.check(records,sources,'S',plan(sources),tmp_path/'run',changed)
    assert 'source_preservation_inventory' not in changed.calls
    assert second['status']=='needs_attention' and first['report_path']!=second['report_path']
    assert Path(first['report_path']).exists()
    records['evidence_steps/E']['payload']['revision']=99
    records['evidence_steps/E']['payload']['produced_claim_ids']=['NEW']
    replay=Ports(fail='jev')
    assert coverage.check(records,sources,'S',plan(sources),tmp_path/'run',replay)==second
    assert not replay.calls


def test_unknown_call_blocks_even_changed_material_in_same_root(tmp_path):
    records,sources=fixture(tmp_path)
    with pytest.raises(TimeoutError):coverage.check(records,sources,'S',plan(sources),tmp_path/'run',Ports(fail='jev'))
    records['evidence_steps/E']['payload']['statement']='不能藉材料變更重發。'
    ports=Ports()
    with pytest.raises(ValueError,match='no saved reply'):
        coverage.check(records,sources,'S',plan(sources),tmp_path/'run',ports)
    assert not ports.calls


@pytest.mark.parametrize('change',['missing','duplicate','wrong_version','wrong_source','order'])
def test_invalid_grouping_never_reaches_models(tmp_path,change):
    records,sources=fixture(tmp_path,['教授原話。','第二段。'],[('S0001','教授原話。')]);p=plan(sources)
    if change=='missing':p['groups'][0]['paragraph_keys'].pop()
    elif change=='duplicate':p['groups'][0]['paragraph_keys'].append('S0001')
    elif change=='wrong_version':p['source_body_sha256']='other'
    elif change=='wrong_source':p['source_id']='OTHER'
    else:p['groups'][0]['paragraph_keys'].reverse()
    ports=Ports()
    with pytest.raises(ValueError):coverage.check(records,sources,'S',p,tmp_path/'run',ports)
    assert not ports.calls


def test_citation_shorthand_and_distributed_verse_ranges_do_not_make_false_omissions():
    assert [r.osis for r in coverage.expand_reference('約翰福音 6:51、53')[0]]==['John.6.51','John.6.53']
    assert len(coverage.expand_reference('馬可福音 14:24；馬太福音 26:28')[0])==2
    records={'A':{'scripture_refs':['以弗所書 4:1']},'B':{'scripture_refs':['以弗所書 4:2-3']}}
    assert coverage.covering_records(records,coverage.expand_reference('以弗所書 4:1-3')[0][0])==['A','B']
    assert coverage.expand_reference('馬太福音29–34節（章未明示）')[0]==[]


def test_citation_is_checked_even_when_content_is_covered(tmp_path):
    records,sources=fixture(tmp_path)
    cites=[{'paragraph_key':'S0001','quote':'教授原話。','reference':'提摩太前書 4:10'}]
    ports=Ports(citations=cites,disposition='missing_scripture_reference')
    result=coverage.check(records,sources,'S',plan(sources),tmp_path/'run',ports)
    assert result['candidate_count']==1 and result['status']=='needs_attention'
    records['evidence_steps/E']['payload']['scripture_refs']=['提前 4:10']
    second=Ports(citations=cites)
    result=coverage.check(records,sources,'S',plan(sources),tmp_path/'run',second)
    assert result['candidate_count']==0 and 'source_preservation_inventory' not in second.calls


@pytest.mark.parametrize('bad',['record','quote','candidate','omitted','wrong_paragraph'])
def test_invalid_model_disposition_is_not_accepted_or_written(tmp_path,bad):
    records,sources=fixture(tmp_path,['教授原話。','另一段。'],[('S0001','教授原話。')])
    class Bad(Ports):
        def repair(self,request):
            reply=super().repair(request)
            if request['input']['task']=='reverse_coverage_adjudication':
                item=reply['items'][0]['findings'][0]
                if bad=='record':item['record_refs']=['NONEXISTENT']
                elif bad=='quote':item['quote']='不在原文中的話'
                elif bad=='candidate':item['candidate_ids']=['NONEXISTENT']
                elif bad=='omitted':reply['items'][0]['findings']=[]
                else:item['paragraph_key']='S0002';item['quote']='另一段。'
            return reply
    before=deepcopy(records)
    with pytest.raises(ValueError):coverage.check(records,sources,'S',plan(sources),tmp_path/'run',Bad(verdict='missing'))
    assert records==before and not list((tmp_path/'run').glob('reviews/*/result.json'))


def test_claim_relation_lead_is_reported_without_a_relation_review(tmp_path):
    records,sources=fixture(tmp_path);ports=Ports(verdict='partial',disposition='claim_layer_out_of_scope')
    result=coverage.check(records,sources,'S',plan(sources),tmp_path/'run',ports)
    assert result['status']=='checked'
    assert all(req.get('input',{}).get('task') in {None,'source_preservation_inventory','reverse_coverage_adjudication'} for req in ports.requests)


@pytest.mark.parametrize('extra',[['--apply'],['--groups','ids'],['--verify-source'],['--plan-groups']])
def test_cli_rejects_mixed_mutating_modes_before_database(tmp_path,monkeypatch,extra):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    monkeypatch.setattr(PostgresKnowledgeStore,'__init__',lambda *a,**kw:pytest.fail('database created'))
    monkeypatch.setattr(sys,'argv',['flash','--source-id','S','--root',str(tmp_path),'--reverse-coverage','--capacity-file','missing',*extra])
    with pytest.raises(SystemExit) as exc:flash.main()
    assert exc.value.code==2


def test_important_concept_removal_cannot_hide_behind_another_unit_in_same_paragraph(tmp_path):
    text='我們有雙重國籍。該撒的東西歸給該撒。'
    records,sources=fixture(tmp_path,[text],[('S0001',text)])
    records['evidence_steps/E']['payload']['statement']='該撒的東西歸給該撒。'
    units=[{'paragraph_key':'S0001','quote':'我們有雙重國籍。','statement':'我們有雙重國籍。'},
           {'paragraph_key':'S0001','quote':'該撒的東西歸給該撒。','statement':'該撒的東西歸給該撒。'}]
    class PerUnit(Ports):
        def judge(self,request):
            self.calls.append('jev');self.requests.append(request)
            # Deterministic counterexample oracle, not a replacement for model calibration.
            answers={}
            saved=[v['statement'] for v in request['state']['records'].values()]
            for key,spec in request['questions'].items():
                target=spec['instructions'].split('意思：')[-1]
                choice='covered' if target in saved else 'missing'
                answers[key]={'type':'choice','choice':choice,'probabilities':{k:float(k==choice) for k in spec['criteria']}}
            return {'model':e.MODEL,'answers':answers}
    ports=PerUnit(units=units,disposition='missing_material')
    result=coverage.check(records,sources,'S',plan(sources),tmp_path/'run',ports)
    assert result['candidate_count']==1 and result['status']=='needs_attention'
    finding=result['findings'][0]['findings'][0]
    assert finding['quote']=='我們有雙重國籍。'
    assert 'source_context' not in next(req for req in ports.requests if 'questions' in req)['state']


def test_s0021_keeps_burden_and_reference_without_demanding_unrelated_clause(tmp_path):
    text='我們勞苦努力，因為指望永生的神。祂是所有人的救主，更是信實忠心人的救主。'
    quote='祂是所有人的救主，更是信實忠心人的救主。'
    records,sources=fixture(tmp_path,[text],[('S0001',quote)])
    records['evidence_steps/E']['payload'].update(statement=quote,scripture_refs=['提摩太前書 4:10'])
    ports=Ports(units=[{'paragraph_key':'S0001','quote':quote,'statement':quote}],citations=[
        {'paragraph_key':'S0001','quote':quote,'reference':'提摩太前書 4:10'}])
    result=coverage.check(records,sources,'S',plan(sources),tmp_path/'run',ports)
    assert result['candidate_count']==0 and result['citation_records']==1
    assert '勞苦努力' in coverage.INVENTORY_PROMPT
    assert '只預告下講' in coverage.INVENTORY_PROMPT


def test_source_reference_discrepancy_is_reported_without_changing_source(tmp_path):
    text='以賽亞書四十一章七十四節。'
    records,sources=fixture(tmp_path,[text],[('S0001',text)])
    records['evidence_steps/E']['payload']['scripture_refs']=['以賽亞書 41:14']
    class Notes(Ports):
        def repair(self,request):
            reply=super().repair(request)
            if request['input']['task']=='reverse_coverage_adjudication':
                for group in reply['items']:
                    for item in group['findings']:item['source_notes']=['原文節次與現有引用不一致，尚未核對錄音。']
            return reply
    ports=Notes(units=[],citations=[{'paragraph_key':'S0001','quote':text,'reference':'以賽亞書 41:74'}])
    before=deepcopy(records)
    result=coverage.check(records,sources,'S',plan(sources),tmp_path/'run',ports)
    assert result['findings'][0]['findings'][0]['source_notes'] and records==before


def test_nonadjacent_groups_can_cover_source_once_without_becoming_hard_boundaries(tmp_path):
    records,sources=fixture(tmp_path,['教授原話。','另一題。','回到原題。'],[('S0001','教授原話。')])
    p=plan(sources);p['groups']=[{'group_id':'A','title':'原題','paragraph_keys':['S0001','S0003']},
                              {'group_id':'B','title':'另題','paragraph_keys':['S0002']}]
    coverage.validate_groups(p,'S',sources['S'])


def test_cli_dispatches_readonly_coverage_with_subscription_medium_and_existing_groups(tmp_path,monkeypatch,capsys):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
    from backend.pipeline.claim_model_capacity import ClaimModelCapacity
    from backend.pipeline import claim_refresh
    config=tmp_path/'capacity.json';config.write_text('{}')
    groups=tmp_path/'groups.json';groups.write_text('{"source_id":"S"}')
    seen={}
    monkeypatch.setattr(PostgresKnowledgeStore,'__init__',lambda *a,**kw:None)
    monkeypatch.setattr(ClaimModelCapacity,'__init__',lambda *a,**kw:None)
    def client_init(self,**kw):seen.update(kw)
    monkeypatch.setattr(CodexSubscriptionClient,'__init__',client_init)
    monkeypatch.setattr(claim_refresh,'Models',lambda *a,**kw:object())
    def run(store,sid,root,ports,**kw):
        assert sid=='S' and kw['plan']=={'source_id':'S'}
        return {'stage':'reverse_coverage','database_writes':0}
    monkeypatch.setattr(coverage,'run',run)
    monkeypatch.setattr(sys,'argv',['flash','--source-id','S','--root',str(tmp_path/'run'),
        '--reverse-coverage','--capacity-file',str(config),'--shared-groups',str(groups)])
    flash.main()
    assert json.loads(capsys.readouterr().out)['database_writes']==0
    assert seen['model']=='gpt-6.1-sol' and seen['reasoning_effort']=='medium' and seen['max_output_tokens']==10000


def test_new_record_elsewhere_does_not_renumber_or_recheck_unchanged_group(tmp_path):
    records,sources=fixture(tmp_path,['教授原話。','另一題。'],[('S0001','教授原話。'),('S0002','另一題。')])
    records['evidence_steps/E']['payload']['source_fragment_ids']=['F0']
    p=plan(sources);p['groups']=[{'group_id':'A','title':'甲','paragraph_keys':['S0001']},
                              {'group_id':'B','title':'乙','paragraph_keys':['S0002']}]
    ports=Ports();coverage.check(records,sources,'S',p,tmp_path/'run',ports)
    records['observations/Z']={'retired':False,'payload':{'observation_id':'Z','statement':'另一題。',
        'source_fragment_ids':['F1'],'scripture_refs':[]}}
    later=Ports();coverage.check(records,sources,'S',p,tmp_path/'run',later)
    assert later.calls==['jev']
    assert len(later.requests[0]['questions'])==1
    assert next(iter(later.requests[0]['state']['records'].values()))['statement']=='另一題。'


def test_prompt_upgrade_does_not_reopen_saved_root(tmp_path,monkeypatch):
    records,sources=fixture(tmp_path)
    coverage.check(records,sources,'S',plan(sources),tmp_path/'run',Ports())
    monkeypatch.setattr(coverage,'INVENTORY_PROMPT',coverage.INVENTORY_PROMPT+'新判準。')
    ports=Ports()
    with pytest.raises(ValueError,match='saved input differs'):
        coverage.check(records,sources,'S',plan(sources),tmp_path/'run',ports)
    assert not ports.calls


def test_alias_collision_is_rejected_before_spending_any_model_calls(tmp_path,monkeypatch):
    records,sources=fixture(tmp_path)
    records['observations/O']={'retired':False,'payload':{'observation_id':'O','statement':'另一項。','source_fragment_ids':['F0']}}
    monkeypatch.setattr(coverage.e,'digest',lambda _: 'x'*64)
    ports=Ports()
    with pytest.raises(ValueError,match='reference collision'):
        coverage.check(records,sources,'S',plan(sources),tmp_path/'run',ports)
    assert not ports.calls


def test_corrupt_saved_response_cannot_be_bypassed_by_material_change(tmp_path):
    records,sources=fixture(tmp_path);root=tmp_path/'run'
    coverage.check(records,sources,'S',plan(sources),root,Ports())
    reply=next(root.glob('coverage/G/jev/*/response.json'));reply.write_text('{')
    records['evidence_steps/E']['payload']['statement']='新材料。'
    ports=Ports()
    with pytest.raises(ValueError,match='saved reply is unreadable'):
        coverage.check(records,sources,'S',plan(sources),root,ports)
    assert not ports.calls


def test_empty_material_catalogue_is_not_accepted_as_covered(tmp_path):
    records,sources=fixture(tmp_path);del records['evidence_steps/E']
    class Missing(Ports):
        def repair(self,request):
            if request['input']['task']=='source_preservation_inventory':return super().repair(request)
            self.calls.append('reverse_coverage_adjudication')
            items=[]
            for job in request['input']['jobs']:
                c=job['candidates'][0];u=c['source_item']
                items.append({'job_id':job['job_id'],'findings':[{'candidate_ids':[c['candidate_id']],
                    'paragraph_key':u['paragraph_key'],'quote':u['quote'],'statement':u['statement'],
                    'reference':'','record_refs':[],'verdict':'missing_material','reason':'尚無材料保存此內容。','source_notes':[]}]})
            return {'items':items}
    ports=Missing()
    result=coverage.check(records,sources,'S',plan(sources),tmp_path/'run',ports)
    assert result['status']=='needs_attention' and 'jev' not in ports.calls


def test_joint_disposition_preserves_each_candidates_own_source_and_replays(tmp_path):
    records,sources=fixture(tmp_path,['在前的要在後。','許多在前的要在後，並非所有。'],[('S0001','在前的要在後。')])
    units=[{'paragraph_key':pk,'quote':p['text'],'statement':p['text']} for pk,p in sources['S']['paragraphs'].items()]
    class Joint(Ports):
        def repair(self,request):
            reply=super().repair(request)
            if request['input']['task']=='reverse_coverage_adjudication':
                for row in reply['items']:
                    a,b=row['findings'];b['candidate_ids']=a['candidate_ids']+b['candidate_ids']
                    row['findings']=[b]
            return reply
    ports=Joint(units=units,verdict='partial');root=tmp_path/'run'
    result=coverage.check(records,sources,'S',plan(sources),root,ports)
    finding=result['findings'][0]['findings'][0]
    assert finding['paragraph_key']=='S0002'
    assert finding['candidate_sources']==[{'candidate_id':'G-U'+str(i),**u} for i,u in enumerate(units)]
    replay=Ports(fail='source_preservation_inventory')
    assert coverage.check(records,sources,'S',plan(sources),root,replay)==result
    assert replay.calls==[]
