"""Direct supporting records: fidelity, source bounds, protected decisions, no crawl."""
from copy import deepcopy

import pytest

from backend.pipeline import claim_refresh as c
from backend.tests.test_claim_refresh import group, Ports, answer
from backend.tests.test_evidence_refresh import sample
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres


class ContextPorts(Ports):
    def __init__(self, *, changes=None, uncertain=False, bad=False):
        super().__init__(repair=False)
        self.changes,self.uncertain,self.bad=changes,uncertain,bad
    def judge(self,request):
        if 'attached_record' in request['questions']:
            self.calls.append('jev:attached_record');self.judgments.append(request)
            col=request['state']['record_type']
            return answer(request,'yes' if col in {'observations','position_nodes'} else 'no')
        if 'relation' in request['questions']:
            self.calls.append('jev:relation');self.judgments.append(request)
            return answer(request,'a_to_b_supports')
        if 'reason_valid' in request['questions']:
            return answer(request,'yes')
        return super().judge(request)
    def repair(self,request):
        if 'attached_record_jobs' not in request['input']:
            return super().repair(request)
        self.calls.append('gpt:context')
        items=[]
        for job in request['input']['attached_record_jobs']:
            col=job['object_key'].split('/')[0]
            field='title' if col=='position_nodes' else 'statement'
            changes=self.changes if self.changes is not None else [{'field':field,'value':'沒有聽從祂的話，顯明沒有真正相信祂。'}]
            if self.bad:changes=[{'field':'answer_claim_ids','value':['UNKNOWN']}]
            items.append({'object_key':job['object_key'],'action':'needs_source' if self.uncertain else 'repair',
                          'changes':[] if self.uncertain else changes,'reason':'按完整原文修正，不改其他欄位。'})
        return {'items':items}


def seed_context(store, *, protected=False):
    claim=deepcopy(store.get_record_state('claims','C')['payload'])
    claim['opposed_position_ids']=['P']
    store.apply_plan(store.plan_package({'package_id':'context-fixture','claims':[claim],
        'observations':[{'observation_id':'O','statement':'沒有明白就等於拒絕明白。',
                         'source_fragment_ids':['F'],'review_status':'human_hold' if protected else 'candidate'}],
        'position_nodes':[{'position_id':'P','title':'拒絕明白代表不相信。','source_fragment_ids':['F']}],
        'knowledge_relations':[{'relation_id':'ER','from_id':'O','to_id':'E',
                              'relation_type':'supports','reason':'這個行為觀察支持沒有真正相信的判斷。'}]}))


def test_direct_observation_position_and_evidence_edge_are_refreshed_atomically(group):
    store,root,_=group;seed_context(store)
    ports=ContextPorts();result=c.run(store,['C'],root,ports,max_new_claims=0,apply=True)
    assert result['applied']
    assert store.get_record_state('observations','O')['payload']['statement']=='沒有聽從祂的話，顯明沒有真正相信祂。'
    assert store.get_record_state('position_nodes','P')['payload']['title']=='沒有聽從祂的話，顯明沒有真正相信祂。'
    assert ports.calls.count('gpt:context')==1
    question=next(q for q in ports.judgments if q['state'].get('record_type')=='questions')
    assert question['state']['answer_claims']['C']['scripture_refs']==[]
    rel=next(q for q in ports.judgments if 'relation' in q['questions'])
    assert rel['state']['A']['record_type']=='observations' and rel['state']['B']['record_type']=='evidence_steps'
    assert not result['new_claim_ids']


def test_context_repair_cannot_change_other_record_fields(group):
    store,root,_=group;seed_context(store)
    original=store.get_record_state('observations','O')
    with pytest.raises(ValueError,match='forbidden'):
        c.run(store,['C'],root,ContextPorts(bad=True),max_new_claims=0,apply=True)
    assert store.get_record_state('observations','O')==original


@pytest.mark.parametrize('protected', [True,False])
def test_context_protection_or_unresolved_source_blocks_claim_transaction(group, protected):
    store,root,_=group;seed_context(store,protected=protected)
    original=store.get_record_state('claims','C')
    result=c.run(store,['C'],root,ContextPorts(uncertain=not protected),max_new_claims=0,apply=True)
    assert result['status']=='needs_attention' and not result['applied']
    assert store.get_record_state('claims','C')==original


def test_redundant_invalid_question_fragment_can_be_removed_without_guessing_source(group):
    store,root,_=group
    bad=deepcopy(store.get_record_state('source_fragments','F')['payload'])
    bad.update(fragment_id='BAD',verbatim_excerpt='什麼？',anchor_state='context_only')
    q=deepcopy(store.get_record_state('questions','Q')['payload']);q.update(source_fragment_id=None,source_fragment_ids=['F','BAD'])
    store.apply_plan(store.plan_package({'package_id':'invalid-extra-fragment','source_fragments':[bad],'questions':[q]}))
    result=c.run(store,['C'],root,ContextPorts(changes=[{'field':'source_fragment_ids','value':['F']}]),max_new_claims=0,apply=True)
    assert result['applied'] and store.get_record_state('questions','Q')['payload']['source_fragment_ids']==['F'],result['updates'].get('questions/Q')
    assert not store.get_record_state('source_fragments','BAD')['retired']


def test_evidence_neighbour_does_not_expand_to_its_other_edges(group):
    store,root,_=group;seed_context(store)
    ev=deepcopy(store.get_record_state('evidence_steps','E')['payload']);ev.update(evidence_step_id='E2',produced_claim_ids=[])
    ev3=deepcopy(ev);ev3['evidence_step_id']='E3'
    store.apply_plan(store.plan_package({'package_id':'second-hop-context','evidence_steps':[ev,ev3],
        'knowledge_relations':[{'relation_id':'ER2','from_id':'E','to_id':'E2','relation_type':'supports'},
                              {'relation_id':'ER3','from_id':'E2','to_id':'E3','relation_type':'supports'}]}))
    result=c.run(store,['C'],root,ContextPorts(),max_new_claims=0,apply=True)
    import json
    frozen=json.loads((root/'before.json').read_text())['records']
    assert result['applied'] and 'evidence_steps/E2' in frozen
    assert 'knowledge_relations/ER3' not in frozen and 'evidence_steps/E3' not in frozen


def test_temporary_relation_labels_are_rendered_without_changing_literal_source_letters():
    state={'A':{'statement':'前提'},'B':{'statement':'結論'}}
    assert c.relations.render_reason('A支持B。',state,[])=='「前提」支持「結論」。'
    assert c.relations.render_reason('A與B相比。',state,[{'original_text_excerpts':['教授以A與B作變數。']}])=='A與B相比。'
