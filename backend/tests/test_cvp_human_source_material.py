"""Durable source decisions accompany the exact common material and SQL gates."""
from copy import deepcopy

import pytest

from backend.api.canonical_repository import cvp_management_authority as authority
from backend.api.canonical_repository.postgres_store import ChangeSetConflict, sha256_json
from backend.pipeline import claim_human_source as human
from backend.pipeline import cvp_management_runner as runner
from backend.pipeline.cvp_materials import expand_source
from test_cvp_materials import MaterialStore, material
from test_cvp_management_runner import postgres_store, _temporary_postgres


def fact(sid='S0',key='S0008',text='原话7，保留完整上下文。',file_sha='b'*64):
    value={'schema_version':human.VERSION,'source_id':sid,'kind':'confirmation',
        'artifact':{'path':'/fixture/confirmed.json','file_sha256':'c'*64},
        'record':{'answers':[{'question':'是否录音原话？','answer':'用户补充，并非录音。'}]},
        'source_file_sha256':file_sha,'paragraph_sha256':{key:human.text_sha(text)},'supersedes':[]}
    return {**value,'decision_id':'HSD-'+sha256_json(value)}


def test_full_source_facts_use_complete_spoken_hash_even_outside_selected_sections():
    store=MaterialStore();store.human_source_decisions=[fact(),fact(key='S0004',text='原话3')]
    bundle=material(store)
    source=bundle['material']['sources'][0]
    facts=source['human_source_decisions']
    assert [f['applicability'] for f in facts]==['current','needs_reconfirmation']
    assert all(f['source_version_changed'] and f['claim_content_approval'] is False for f in facts)
    assert source['human_source_decisions_are_metadata'] is True
    assert bundle['human_source_decisions']==store.human_source_decisions
    assert 'S0008' not in {p['paragraph_key'] for s in source['sections'] for p in s['paragraphs']}
    expanded=expand_source(bundle,{'source_ref':source['source_ref'],'section_ref':source['sections'][0]['section_ref'],
        'extent':'adjacent','reason':'检查上下文'},source_loader=store.load_source)
    assert expanded['material']['sources'][0]['human_source_decisions']==facts
    members=material(store,member_support_ids=['C1'])
    assert members['material']['sources'][0]['human_source_decisions']==facts


def test_member_loading_preserves_existing_facts_and_adds_new_source_facts():
    store=MaterialStore();store.human_source_decisions=[fact(),fact(sid='S1')]
    initial=material(store);loaded=material(store,member_support_ids=['C1'])
    assert len(initial['human_source_decisions'])==1
    assert len(loaded['human_source_decisions'])==2
    assert all(len(s['human_source_decisions'])==1 for s in loaded['material']['sources'])


def test_runtime_guard_and_replay_reject_changed_source_facts():
    store=MaterialStore();old=material(store)
    store.human_source_decisions=[fact()]
    with pytest.raises(runner.InputChanged,match='human source decisions changed'):
        runner._guard(store,old)
    new=material(store)
    assert runner._same_material_inputs(new,old) is False
    assert runner._same_material_inputs(new,deepcopy(new)) is True


def test_source_fact_current_check_is_shared_by_sql_admission_completion_and_parent(postgres_store):
    store=postgres_store
    bundle={'sources':[{'source_id':'S0'}],'human_source_decisions':[],
            'claim_input':{'claims':[]},'claim_authority':[],'records':[]}
    with store.connect() as conn,conn.cursor() as cursor:
        authority.validate_claim_authority(cursor,bundle)
        human.register(cursor,[fact()],origin='fixture')
        with pytest.raises(ChangeSetConflict,match='human source decisions changed'):
            authority.validate_claim_authority(cursor,bundle)
        legacy={k:v for k,v in bundle.items() if k!='human_source_decisions'}
        with pytest.raises(ChangeSetConflict,match='human source decisions changed'):
            authority.validate_claim_authority(cursor,legacy)
        bundle['human_source_decisions']=[fact()]
        authority.validate_claim_authority(cursor,bundle)
    assert store.read_human_source_decisions(['S0'])==[fact()]
    assert store.read_human_source_decisions(['unrelated'])==[]
