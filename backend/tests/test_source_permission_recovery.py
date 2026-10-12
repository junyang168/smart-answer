from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
import pytest
from backend.pipeline import source_permission_recovery as r
from backend.pipeline.cvp_sql_flow import binding
from backend.pipeline.source_reconciliation_runner import write,read,body


def fixture(tmp_path,monkeypatch):
    frozen={'manifest':{'source_id':'S','evidence_binding_repair':{'exact':True},
        'permissions':[{'create':True,'collection':'source_fragments','parent_id':'S'}]},'sql_scope':{'run_id':'R'}}
    write(tmp_path/'scoped-materials.json',frozen)
    for stage in ('proposal','initial_review','correction'):
        write(tmp_path/(stage+'.raw.json'),{'response':{'original':stage}})
        write(tmp_path/(stage+'.packet.json'),{'full':'own source'})
    d={'schema_version':r.VERSION,'authorized_by':'owner_engineering','authorization_quote':'fixed code input',
       'human_content_approval':False,'new_proposal_calls':1,'additional_corrections':0,
       'review_stage':'original_unused_final_review','action_root':str(tmp_path),'run_id':'R',
       'materials':binding(tmp_path/'scoped-materials.json'),
       'original_leaves':{stage:binding(tmp_path/(stage+'.raw.json')) for stage in ('proposal','initial_review','correction')}}
    write(tmp_path/'direction.json',d);write(tmp_path/r.NAME,{'authorization':binding(tmp_path/'direction.json')})
    monkeypatch.setattr(r,'validate_raw',lambda root,stage,pkt:{'response':{'original':stage},'artifact_sha256':stage})
    monkeypatch.setattr(r,'build_proposal_packet',lambda f:{'full':'own source'})
    from backend.pipeline import source_reconciliation as c
    monkeypatch.setattr(c,'guard_files',lambda f:None)
    return frozen,d


def test_input_repair_preserves_three_original_answers_and_scope(tmp_path,monkeypatch):
    f,d=fixture(tmp_path,monkeypatch);before={p:p.read_bytes() for p in tmp_path.iterdir()}
    pkt=r.packet(tmp_path,f)
    receipt=pkt['permission_input_recovery']
    assert receipt['human_content_approval'] is False
    assert receipt['new_object_count_limit'] is None
    assert [x['stage'] for x in receipt['original_answers']]==['proposal','initial_review','correction']
    assert all(p.read_bytes()==b for p,b in before.items())


@pytest.mark.parametrize('fault',['calls','correction','scope','parent','no_parent','leaf','material','final'])
def test_fixed_parent_recovery_negative_gates(tmp_path,monkeypatch,fault):
    f,d=fixture(tmp_path,monkeypatch)
    if fault=='calls':d['new_proposal_calls']=2
    elif fault=='correction':d['additional_corrections']=1
    elif fault=='scope':d['action_root']='/foreign'
    elif fault=='parent':f['manifest']['permissions'][0]['parent_id']='foreign'
    elif fault=='no_parent':f['manifest']['permissions'][0].pop('parent_id')
    elif fault=='leaf':d['original_leaves']['proposal']['file_sha256']='f'*64
    elif fault=='material':f['manifest']['source_id']='changed'
    elif fault=='final':write(tmp_path/'final_review.reservation.json',{'intent':True})
    write(tmp_path/'direction.json',d,immutable=False)
    # A changed direction does not get hidden behind an old physical binding.
    write(tmp_path/r.NAME,{'authorization':binding(tmp_path/'direction.json')},immutable=False)
    with pytest.raises(ValueError):r.packet(tmp_path,f)


def test_adapter_identity_isolated_old_contract_untouched():
    original=SimpleNamespace(_prompt='old',_base_prompt='old',generation_config_sha256='old')
    assert r.adapter(original,{}) is original
    updated=r.adapter(original,{'permission_input_recovery':{'exact':True}})
    assert original._prompt=='old' and updated._prompt!='old'
    assert updated.generation_config_sha256!='old'


def provenance_fixture(monkeypatch):
    from backend.pipeline import source_reconciliation_runner as runner
    proposal=SimpleNamespace(records=[],dispositions=[SimpleNamespace(record_key='claims/C',action='unresolved')],
        claim_assessments=[SimpleNamespace(cause='claim_statement')])
    monkeypatch.setattr(runner,'effective_proposal',lambda frozen,response:proposal)
    frozen={'manifest':{'claim_issue':{'exact':True},'permissions':[
        {'create':False,'collection':'claims','object_id':'C','fields':['statement']}]},
        'before_records':[{'collection':'claims','object_id':'C','payload':{'claim_id':'C','review_status':'ai_consensus_reviewed'}}]}
    initial={'provenance_contract':{'old_approval_is_history_only':True,
        'deterministic_after_review_changes':['fresh review fields']}}
    return frozen,proposal,initial


def test_provenance_contract_is_program_owned_not_content_approval(monkeypatch):
    f,p,initial=provenance_fixture(monkeypatch)
    value=r.provenance_contract(f,[{'response':{}}],initial,{})
    assert value['content_permission_expanded'] is False
    assert value['protected_review_states_not_lifted'] is True
    assert set(value['program_owned_fields'])=={'review_status','reviewed_at','reviewed_by','review_note','source_reconciliation_review_sha256'}


@pytest.mark.parametrize('fault',['already_sent','missing_initial','held','writable','records','multiple','source','not_issue'])
def test_provenance_contract_rejects_nondefects_or_new_content(monkeypatch,fault):
    f,p,initial=provenance_fixture(monkeypatch); correction={}
    if fault=='already_sent':correction['provenance_contract']=initial['provenance_contract']
    elif fault=='missing_initial':initial={}
    elif fault=='held':f['before_records'][0]['payload']['review_status']='human_review_required'
    elif fault=='writable':f['manifest']['permissions'][0]['fields']=[]
    elif fault=='records':p.records=[{'new':'content'}]
    elif fault=='multiple':p.dispositions*=2
    elif fault=='source':p.claim_assessments[0].cause='source_unresolved'
    elif fault=='not_issue':f['manifest']['claim_issue']=None
    with pytest.raises(ValueError):r.provenance_contract(f,[{'response':{}}],initial,correction)


def test_unknown_final_intent_never_grants_permission_recovery(tmp_path,monkeypatch):
    f,d=fixture(tmp_path,monkeypatch)
    (tmp_path/'final_review.call-intent.json').write_text('{"outcome":"unknown"}')
    with pytest.raises(ValueError,match='already issued'):r.packet(tmp_path,f)


@pytest.mark.parametrize('approved',[True,False])
def test_recovery_only_one_proposal_then_unused_final_no_correction(monkeypatch,tmp_path,approved):
    from backend.pipeline import source_reconciliation as c,source_reconciliation_runner as runner
    calls=[];candidate=object()
    monkeypatch.setattr(r,'packet',lambda *a,**k:{'permission_input_recovery':{'schema_version':r.PROVENANCE_VERSION}})
    monkeypatch.setattr(runner,'effective_proposal',lambda *a:candidate)
    monkeypatch.setattr(r,'final_packet',lambda *a:{'exact':'full independent final'})
    monkeypatch.setattr(c,'validate_review',lambda *a:approved)
    stages=SimpleNamespace(root=tmp_path,guard=lambda *a:None,
        call=lambda stage,packet:calls.append(stage) or {'response':{},'artifact_sha256':stage})
    if approved:assert r.reviewed(stages,{})[0] is candidate
    else:
        with pytest.raises(ValueError,match='zero further correction'):r.reviewed(stages,{})
    assert calls==[r.STAGE,'final_review']


def test_new_action_discloses_contract_without_upgrading_saved_packet(monkeypatch):
    from backend.pipeline.source_reconciliation_runner import build_proposal_packet
    frozen={'update_id':'U','manifest':{},'inventory':{'affected_keys':[],'context_keys':[],'unknown_keys':[]},
        'before_records':[],'before':{},'after':[],'context_sources':[],'sentence_inventory':{}}
    old=build_proposal_packet(frozen)
    assert 'provenance_contract' not in old
    frozen['program_provenance_contract']=r.program_contract()
    assert build_proposal_packet(frozen)['provenance_contract']==r.program_contract()
    frozen['program_provenance_contract']['protected_review_states_not_lifted']=False
    with pytest.raises(ValueError,match='contract drift'):build_proposal_packet(frozen)


def test_actual_g149_saved_provenance_recovery_readonly(tmp_path):
    import os,json
    if os.environ.get('WKP_G149_READONLY')!='1':pytest.skip('explicit saved SQL/source readonly acceptance')
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.cvp_sql_actions import ScopedSourceModelStages,decoded_pins
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-a85c3d6eb392f97e945d88a18fbf33a76ed136eefefbb4554759e82e0e26deb2/claim-issues/c10ca41b59907553e2d5e889de766d755c22402fa25bd34444b9a19ade492256/action/CVPA-2a5ec68080812cfefbc8721693925c22a7f353b1e11bf8e7159ef03184499db2')
    frozen=body(read(root/'scoped-materials.json'));store=PostgresKnowledgeStore()
    group=store.get_cvp_group(frozen['sql_scope']['run_id'])
    files={p:p.read_bytes() for p in root.iterdir() if p.is_file()}
    direction={'schema_version':r.PROVENANCE_VERSION,'authorized_by':'owner_engineering',
        'authorization_quote':'Engineering input/contract repair; no content approval.',
        'human_content_approval':False,'new_proposal_calls':1,'additional_corrections':0,
        'review_stage':'original_unused_final_review','action_root':str(root),'run_id':frozen['sql_scope']['run_id'],
        'materials':binding(root/'scoped-materials.json'),
        'original_leaves':{s:binding(root/(s+'.raw.json')) for s in ('proposal','initial_review','correction')}}
    write(tmp_path/'direction.json',direction)
    stages=ScopedSourceModelStages(root,None,store,decoded_pins(frozen['sql_scope']['read_pins']))
    stages.guard(frozen)
    packet=r.packet(root,frozen,authorization=binding(tmp_path/'direction.json'))
    assert packet['permission_input_recovery']['actual_program_provenance_contract']['pending_claim_key'].endswith('CL006')
    assert len(packet['current_sources'][0]['paragraphs'])==len(frozen['after'][0]['paragraphs'])
    changed=deepcopy(frozen);changed['manifest']['permissions'][0]['fields'].remove('statement')
    with pytest.raises(ValueError):r.packet(root,changed,authorization=binding(tmp_path/'direction.json'))
    assert store.get_cvp_group(frozen['sql_scope']['run_id'])==group
    assert all(p.read_bytes()==b for p,b in files.items())
