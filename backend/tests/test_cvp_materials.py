from copy import deepcopy
from pathlib import Path

import pytest

from backend.api.canonical_repository.cvp_recall import build_index, merge_recall, seal
from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.cvp_materials import expand_source, prepare_materials
from backend.pipeline.cvp_recall_preparation import prepare_recall
from backend.pipeline.source_projection import project_script
from test_cvp_recall_preparation import Store, manifest


class MaterialStore(Store):
    def __init__(self,members=2,headings=True):
        super().__init__(1,0)
        self.records={('claims',cid):row for cid,row in self.rows.items()}
        self.reads=[]
        for i in range(members+1):
            cid=f'C{i}'
            if cid not in self.rows:
                self.rows[cid]=self.state({'claim_id':cid,'statement':f'候选成员{i}的完整限定',
                                          'attribution':'professor','evidence_step_ids':[f'E{i}']})
                self.records['claims',cid]=self.rows[cid]
            else:
                self.rows[cid]['payload']['evidence_step_ids']=[f'E{i}']
                self.rows[cid]['content_sha256']=self.quals[cid]['content_sha256']=record_content_sha(self.rows[cid]['payload'])
            self.records['evidence_steps',f'E{i}']=self.state({'evidence_step_id':f'E{i}',
                'statement':'论据','produced_claim_ids':[cid],'source_fragment_ids':[f'F{i}'],
                'support_eligibility':'eligible','citation_ids':['B1']})
            self.records['source_fragments',f'F{i}']=self.state({'fragment_id':f'F{i}','source_id':f'S{i}',
                'paragraph_key':'S0004','verbatim_excerpt':'原话3'})
            self.records['source_documents',f'S{i}']=self.state({'source_id':f'S{i}',
                'source_path':f'/fixture/source-{i}.json','locator_space':'spoken_body_v1'})
            if i:
                self.records['viewpoint_claim_links',f'L{i}']=self.state({'viewpoint_claim_link_id':f'L{i}',
                    'claim_id':cid,'viewpoint_id':'V1','link_type':'member','effective_state':'active'})
        self.records['citations','B1']=self.state({'citation_id':'B1','text':'太9:6'})
        self.records['canonical_viewpoints','V1']=self.state({'viewpoint_id':'V1','identity_status':'active','current_revision_id':'R1'})
        self.records['viewpoint_revisions','R1']=self.state({'viewpoint_revision_id':'R1','viewpoint_id':'V1',
            'core_proposition':'完整判断','scope':{},'proposition_signature':{}})
        view=self.records['canonical_viewpoints','V1'];rev=self.records['viewpoint_revisions','R1']
        self.heads=[{'viewpoint_id':'V1','viewpoint_revision_id':'R1',
            'head_sha256':sha256_json({'viewpoint':[2,view['content_sha256']],'revision':[2,rev['content_sha256']]}),
            **{k:rev['payload'][k] for k in ('core_proposition','scope','proposition_signature')}}]
        script=[]
        for i in range(8):
            if headings and i%2==0:script.append({'text':f'## 小节{i//2}','type':'subtitle'})
            script.append({'text':f'原话{i}，保留完整上下文。','index':i})
        self.projection=project_script(script)

    @staticmethod
    def state(payload):return {'revision':2,'content_sha256':record_content_sha(payload),'payload':payload,'retired':False}

    def read_current_records(self,keys):
        keys=set(keys);self.reads.append(keys)
        return {k:deepcopy(self.records[k]) for k in keys if k in self.records}

    def read_cvp_links(self,*,claim_ids=(),viewpoint_ids=()):
        return {k:deepcopy(v) for k,v in self.records.items() if k[0]=='viewpoint_claim_links' and
                (v['payload']['viewpoint_id'] in viewpoint_ids or v['payload']['claim_id'] in claim_ids)}

    def verify_current_read_pins(self,pins):
        self.verified.append(pins)
        for key,pin in pins.items():
            row=self.records[key]
            if pin!=(row['revision'],row['content_sha256'],row['retired']):raise ValueError('current dependency drift')

    def load_source(self,doc):return Path(doc['source_path']),'a'*64,self.projection


def prepared(store):
    request=manifest(store,claim_ids=['C0'],grouping={'scope_label':'人子','groups':[
        {'group_key':'g','claim_ids':['C0'],'rationale':'existing'}]},index_root='/fixture/index')
    idx=build_index(store.heads,[{**h,'summary':'摘要'} for h in store.heads])
    preflight=prepare_recall(store,request,load_index=lambda *args,**kwargs:idx)
    plan=preflight['recall_plans'][0]['plan']
    recall=merge_recall(plan,[{'claim_candidates':[{'claim_ref':'c1','cvp_refs':['v1']}]}],
                        existing_links=preflight['existing_links'])
    return preflight,recall


def material(store,**kwargs):
    preflight,recall=prepared(store)
    return prepare_materials(store,preflight,'g',recall,source_loader=store.load_source,**kwargs)


def test_candidate_members_have_complete_wording_and_roles_without_their_evidence():
    store=MaterialStore(members=25);bundle=material(store)
    packet=bundle['material']
    assert len(packet['claims'])==26 and len(packet['links'])==25
    assert len(bundle['claim_input']['claims'])==1
    assert bundle['claim_input']['claims'][0]['claim_id']=='C0'
    assert [r['content']['evidence_step_id'] for r in packet['evidence']]==['E0']
    assert len(packet['sources'])==1
    assert not any(('evidence_steps','E1') in keys for keys in store.reads)
    assert packet['claims'][0]['content']['limitations']=={'keep':'完整'}
    source=packet['sources'][0]
    assert source['headings_are_metadata'] is True and source['expansion_level']==0
    assert [(s['start'],s['end']) for s in source['sections']]==[(2,4)]
    assert [p['paragraph_key'] for p in source['sections'][0]['paragraphs']]==['S0003','S0004']


def test_explicit_member_support_loads_only_that_members_own_support():
    store=MaterialStore();bundle=material(store,member_support_ids=['C1'])
    assert {r['content']['evidence_step_id'] for r in bundle['material']['evidence']}=={'E0','E1'}
    assert len(bundle['material']['sources'])==2
    with pytest.raises(ValueError,match='not a current'):material(store,member_support_ids=['foreign'])


def test_maintenance_context_includes_all_members_without_twenty_claim_cutoff():
    store=MaterialStore(25);bundle=material(store,maintenance_viewpoint_ids=['V1'])
    assert len(bundle['material']['evidence'])==26
    assert len(bundle['material']['sources'])==26
    assert len(bundle['material']['claims'])==26


@pytest.mark.parametrize('headings',[True,False])
def test_typed_source_expansion_is_adjacent_then_full_and_preserves_bound_original(headings):
    store=MaterialStore(headings=headings);original=material(store);before=deepcopy(original)
    source=original['material']['sources'][0]
    request={'source_ref':source['source_ref'],'section_ref':source['sections'][0]['section_ref'],
             'extent':'adjacent','reason':'需要前文确认人称及论证对象'}
    adjacent=expand_source(original,request,source_loader=store.load_source)
    assert original==before
    first=source['sections'][0]
    assert first['kind']==('heading_section' if headings else 'paragraph_range')
    assert first['end']-first['start']==(2 if headings else 1)
    assert len(adjacent['material']['sources'][0]['sections'])==3
    assert adjacent['previous_material_sha256']==original['artifact_sha256']
    with pytest.raises(ValueError,match='repeated'):expand_source(adjacent,request,source_loader=store.load_source)
    full=expand_source(adjacent,{**request,'extent':'full'},source_loader=store.load_source)
    assert sum(len(s['paragraphs']) for s in full['material']['sources'][0]['sections'])==8
    assert full['material']['sources'][0]['expansion_level']==2
    with pytest.raises(ValueError,match='repeated'):expand_source(full,{**request,'extent':'full'},source_loader=store.load_source)
    with pytest.raises(ValueError,match='repeated'):expand_source(full,{**request,'extent':None},source_loader=store.load_source)


@pytest.mark.parametrize('kind',['foreign_source','foreign_section','skip_full','missing_reason','extra'])
def test_context_expansion_rejects_unsupported_or_ambiguous_request(kind):
    store=MaterialStore();bundle=material(store)
    request={'source_ref':'s1','section_ref':'p2','extent':'adjacent','reason':'具体依据'}
    if kind=='foreign_source':request['source_ref']='s999'
    if kind=='foreign_section':request['section_ref']='p999'
    if kind=='skip_full':request['extent']='full'
    if kind=='missing_reason':request['reason']=' '
    if kind=='extra':request['free_text']='更多全文'
    with pytest.raises(ValueError):expand_source(bundle,request,source_loader=store.load_source)


@pytest.mark.parametrize('kind',['head','claim','qualification','fragment','missing_evidence'])
def test_stale_or_missing_dependencies_fail_before_material_is_prepared(kind):
    store=MaterialStore();preflight,recall=prepared(store)
    if kind=='head':store.records['canonical_viewpoints','V1']=store.state({'viewpoint_id':'V1','identity_status':'active','current_revision_id':'new'})
    if kind=='claim':
        row=store.records['claims','C0'];row['payload']['statement']='changed';row['content_sha256']=record_content_sha(row['payload'])
    if kind=='qualification':store.quals['C0']['ready']=False
    if kind=='fragment':
        row=store.records['source_fragments','F0'];row['payload']['verbatim_excerpt']='not original';row['content_sha256']=record_content_sha(row['payload'])
    if kind=='missing_evidence':store.records.pop(('evidence_steps','E0'))
    with pytest.raises(ValueError):prepare_materials(store,preflight,'g',recall,source_loader=store.load_source)


def test_recall_cannot_drop_existing_active_connection():
    store=MaterialStore()
    link=store.state({'viewpoint_claim_link_id':'L0','claim_id':'C0','viewpoint_id':'V1',
                      'link_type':'support','effective_state':'active'})
    store.records['viewpoint_claim_links','L0']=link
    preflight,recall=prepared(store)
    assert preflight['existing_links']==[link['payload']]
    bad=seal({k:v for k,v in recall.items() if k not in {'artifact_sha256','claim_candidates'}}|
             {'claim_candidates':[{'claim_id':'C0','viewpoint_ids':[]}]})
    with pytest.raises(ValueError,match='omitted an existing'):
        prepare_materials(store,preflight,'g',bad,source_loader=store.load_source)


def test_material_expansion_rejects_changed_physical_source():
    store=MaterialStore();bundle=material(store)
    request={'source_ref':'s1','section_ref':'p2','extent':'adjacent','reason':'具体依据'}
    def changed(doc):return Path(doc['source_path']),'b'*64,store.projection
    with pytest.raises(ValueError,match='source changed'):expand_source(bundle,request,source_loader=changed)
