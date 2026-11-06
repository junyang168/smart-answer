"""Disjoint original text and read-only objects are execution boundaries."""
from types import SimpleNamespace
import json
import subprocess
import sys

import pytest

from backend.pipeline import claim_management as cm, claim_topic_units as topics
from backend.pipeline import source_execution_locks as locks
from backend.pipeline.cvp_sql_commit import binding


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_BASE_DIR', str(tmp_path))
    source = tmp_path / 'source.json'
    source.write_text('{}')
    plan = dict(schema_version=topics.CONTRACT, authorization_quote='Try disjoint topics, at most two per model',
        source_id='S', source_file=binding(source), source_body_sha256='body', units=[
            dict(unit_id=str(i), title='topic ' + str(i), claim_ids=['C' + str(i)],
                 paragraph_keys=['S000' + str(i)], record_keys=['claims/C' + str(i), 'source_fragments/F' + str(i)],
                 legacy_fragment_keys=[]) for i in (1, 2)])
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(plan))
    requests = []
    for i in (1, 2):
        auth = tmp_path / ('auth' + str(i) + '.json')
        auth.write_text(json.dumps({topics.FIELD: {'plan': binding(path), 'unit_id': str(i)}}))
        requests.append(SimpleNamespace(operation='Update', source_bindings={'S': cm.source.FileBinding.model_validate(binding(source))},
            authorization=cm.source.FileBinding.model_validate(binding(auth)), target_keys=['claims/C' + str(i)],
            permissions=[]))
    return plan, requests


@pytest.mark.parametrize('field', ['paragraph_keys', 'record_keys', 'claim_ids'])
def test_even_read_only_overlap_rejects_the_plan(prepared, field):
    plan, _ = prepared
    plan['units'][1][field].append(plan['units'][0][field][0])
    with pytest.raises(ValueError, match='overlap'):
        topics.validate_plan(plan)


def test_source_projection_preserves_identity_and_complete_selected_paragraph(prepared):
    plan, requests = prepared
    sources = {'S': dict(file=plan['source_file'], body_sha256='body', paragraphs={
        'S0001': {'text': 'all first topic'}, 'S0002': {'text': 'all second topic'}})}
    topics.project_sources(requests[0], sources)
    assert sources['S']['file'] == plan['source_file']
    assert sources['S']['paragraphs'] == {'S0001': {'text': 'all first topic'}}
    assert sources['S']['topic_boundary']['whole_sermon_context'] is False
    sources['S']['body_sha256'] = 'changed'
    with pytest.raises(ValueError, match='drift'):
        topics.project_sources(requests[0], sources)


def test_outside_read_context_and_fragment_reject_before_review(prepared):
    _, requests = prepared
    with pytest.raises(ValueError, match='record boundary'):
        topics.assert_records(requests[0], {'claims/C2': {'payload': {}}})
    with pytest.raises(ValueError, match='original paragraphs'):
        topics.assert_records(requests[0], {'source_fragments/F1': {'payload': {
            'source_id': 'S', 'paragraph_key': 'S0002'}}})


def test_new_relation_cannot_cross_the_other_topic(prepared):
    _, requests = prepared
    candidate = SimpleNamespace(records=[cm.source.Row(collection='knowledge_relations', object_id='new', payload={'relation_id':'new','from_id':'C1','to_id':'C2'})])
    with pytest.raises(ValueError, match='different topic'):
        topics.assert_records(requests[0], {}, candidate)


def test_topics_coexist_but_same_topic_legacy_and_source_replacement_do_not(prepared):
    _, requests = prepared
    with locks.claim_execution_locks(requests[0]) as descriptors:
        locks.verify_claim_locks([f.fileno() for f in descriptors], requests[0])
        with locks.claim_execution_locks(requests[1]):
            pass
        with pytest.raises(BlockingIOError):
            with locks.claim_execution_locks(requests[0]): pass
        legacy = SimpleNamespace(source_bindings={'S': {}}, target_keys=[], permissions=[])
        with pytest.raises(BlockingIOError):
            with locks.claim_execution_locks(legacy): pass
        with pytest.raises(BlockingIOError):
            with locks.source_write_locks(['S']): pass


def test_legacy_also_blocks_topics_in_a_separate_process(prepared):
    _, requests = prepared
    legacy = SimpleNamespace(source_bindings={'S': {}}, target_keys=[], permissions=[])
    code = '''
import json,sys
from types import SimpleNamespace
from backend.pipeline import claim_management as c,source_execution_locks as l
r=json.loads(sys.argv[1]);r['authorization']=c.source.FileBinding.model_validate(r['authorization'])
r['source_bindings']={k:c.source.FileBinding.model_validate(v) for k,v in r['source_bindings'].items()}
try:
 with l.claim_execution_locks(SimpleNamespace(**r)): print('acquired')
except BlockingIOError: print('blocked')
'''
    request = requests[0]
    wire = dict(operation=request.operation, target_keys=request.target_keys, permissions=[],
        source_bindings={k:v.model_dump() for k,v in request.source_bindings.items()}, authorization=request.authorization.model_dump())
    with locks.claim_execution_locks(legacy):
        result = subprocess.run([sys.executable, '-c', code, json.dumps(wire)], text=True,
                                capture_output=True, check=True, timeout=10)
    assert result.stdout.strip() == 'blocked'
