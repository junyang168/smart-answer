"""Maintenance reaches real temporary SQL via the same runner and fake providers."""
from copy import deepcopy
import hashlib
import json

from backend.api.canonical_repository import cvp_management as domain
from backend.api.canonical_repository.cvp_recall import seal
from backend.pipeline import cvp_management_runner as runner
from backend.pipeline.cvp_recall import maintain_reviewed_index
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable
from test_cvp_management_runner import setup, postgres_store, _temporary_postgres
from test_cvp_management_models import envelope
from test_cvp_management import passing


def _initial(setup):
    store, request, root, clients, source = setup
    result = runner.run(store, request, root=root, execute=True, clients=clients)
    assert result['status'] == 'completed'
    heads = store.read_cvp_heads()
    assert len(heads) == 1
    view = heads[0]
    links = store.read_cvp_links(claim_ids=['C1'])
    link = next(iter(links.values()))['payload']
    proofs = [read_artifact(path) for path in root.rglob('summary-proofs/summary-*.json')]
    assert len(proofs) == 1
    index_root = root.parent / 'committed-current-index'
    _, metrics = maintain_reviewed_index(heads, proposer=clients['gpt'], reviewer=clients['opus'],
        output_dir=index_root, max_request_bytes=500000, max_calls=0, approved_card_proofs=proofs)
    assert metrics['model_calls'] == 0
    return store, request, root, clients, source, view, link, index_root


def _request(original, root, *, name, index_root, view_id, permissions, creation_slots):
    value = deepcopy(original)
    value.update(action_id=name, mode='maintain', maintenance_viewpoint_ids=[view_id],
                 permissions=permissions, creation_slots=creation_slots)
    value['input'] = seal({**{k: v for k, v in original['input'].items() if k != 'artifact_sha256'},
                           'index_root': str(index_root)})
    authority = root.parent / (name + '-authorization.json')
    direction = {'schema_version': 'wang_cvp_management_authorization_v1', 'authorized_by': 'owner',
        **{key: value[key] for key in ['action_id', 'mode', 'maintenance_viewpoint_ids', 'permissions', 'creation_slots', 'budget']},
        'input_sha256': value['input']['artifact_sha256']}
    write_immutable(authority, direction)
    value['authorization'] = {'path': str(authority), 'file_sha256': hashlib.sha256(authority.read_bytes()).hexdigest()}
    return value


def _ref(packet, collection, ident):
    return next(ref for ref, value in packet['context']['references'].items()
                if value == {'collection': collection, 'object_id': ident})


def _change(ref, fields):
    return {'ref': ref, 'fields_json': json.dumps(fields, ensure_ascii=False)}


def _behavior(view, link, mode):
    def produce(packet):
        if not packet.get('schema_version', '').endswith('_proposal_packet'):
            return None
        vref = _ref(packet, 'canonical_viewpoints', view['viewpoint_id'])
        lref = _ref(packet, 'viewpoint_claim_links', link['viewpoint_claim_link_id'])
        if mode == 'retire':
            return envelope('candidate', candidate={
                'component_dispositions': [{'component_ref': 'k1', 'disposition': 'background', 'reason': '此身份在本次明確退役。'}],
                'member_dispositions': [{'member_ref': lref, 'disposition': 'background', 'reason': '同事務撤回原連接。'}],
                'changes': [_change(vref, {'identity_status': 'retired'}), _change(lref, {'effective_state': 'retired'})]})
        target = vref if mode == 'scope' else 'nv1'
        revision = {key: deepcopy(view[key]) for key in ['core_proposition', 'proposition_signature', 'scope']}
        revision['viewpoint_id'] = target
        if mode == 'scope':
            revision = {'scope': {'scripture_scope': ['太9:6']}}
        changes = [_change(target, {'current_revision_id': 'nr1'}), _change('nr1', revision)]
        if mode == 'merge':
            changes.extend([_change(vref, {'identity_status': 'merged', 'redirect_to_viewpoint_id': 'nv1'}),
                            _change(lref, {'viewpoint_id': 'nv1'})])
        return envelope('candidate', candidate={
            'component_dispositions': [{'component_ref': 'k1', 'disposition': 'connected',
                'target_ref': target, 'link_ref': lref, 'reason': '完整原文支持同一判斷。'}],
            'member_dispositions': [{'member_ref': lref, 'disposition': 'retained' if mode == 'scope' else 'changed',
                                    'reason': '重核新版本。' if mode == 'scope' else '原連接移至保留身份。'}],
            'changes': changes, 'summaries': [{'ref': target, 'summary': '此處人子稱號表達神性，並非普通人的稱呼。'}]})
    return produce


def _detail(store, name):
    parent = store.get_cvp_group('CVPM-' + runner.sha256_json(name))
    return parent['units']['g1'].get('detail')


def test_new_revision_scope_maintenance_commits_successor_and_program_member_repin(setup):
    store, initial, root, clients, source, view, link, index_root = _initial(setup)
    before_source, before_claim = source.read_bytes(), store.get_record_state('claims', 'C1')
    old_revision = store.get_record_state('viewpoint_revisions', view['viewpoint_revision_id'])
    permissions = [
        {'collection': 'canonical_viewpoints', 'object_id': view['viewpoint_id'], 'fields': ['current_revision_id', 'summary']},
        {'collection': 'viewpoint_claim_links', 'object_id': link['viewpoint_claim_link_id'],
         'fields': ['validated_against_viewpoint_revision_id']}]
    request = _request(initial, root, name='scope-maintenance', index_root=index_root,
        view_id=view['viewpoint_id'], permissions=permissions, creation_slots={'viewpoint_revisions': 1})
    clients['gpt'].behavior = _behavior(view, link, 'scope')
    result = runner.run(store, request, root=root.parent / 'scope-action', execute=True, clients=clients)
    assert result['status'] == 'completed', _detail(store, request['action_id'])
    current = store.read_cvp_heads()[0]
    assert current['viewpoint_id'] == view['viewpoint_id']
    assert current['viewpoint_revision_id'] != view['viewpoint_revision_id']
    revision = store.get_record('viewpoint_revisions', current['viewpoint_revision_id'])
    assert revision['supersedes_revision_id'] == view['viewpoint_revision_id']
    assert revision['core_proposition'] == view['core_proposition']
    assert revision['proposition_signature'] == view['proposition_signature']
    assert revision['scope']['scripture_scope'] == ['太9:6']
    assert revision['revision_number'] == revision['revision'] == 1
    assert store.get_record_state('viewpoint_revisions', view['viewpoint_revision_id']) == old_revision
    assert store.get_record('viewpoint_claim_links', link['viewpoint_claim_link_id'])['validated_against_viewpoint_revision_id'] == current['viewpoint_revision_id']
    assert source.read_bytes() == before_source and store.get_record_state('claims', 'C1') == before_claim
    counts = len(clients['gpt'].requests), len(clients['opus'].requests)
    assert runner.run(store, request, root=root.parent / 'scope-action', execute=True, clients=clients) == result
    assert (len(clients['gpt'].requests), len(clients['opus'].requests)) == counts


def test_logical_retirement_withdraws_member_in_same_real_transaction(setup):
    store, initial, root, clients, _, view, link, index_root = _initial(setup)
    permissions = [
        {'collection': 'canonical_viewpoints', 'object_id': view['viewpoint_id'], 'fields': ['identity_status']},
        {'collection': 'viewpoint_claim_links', 'object_id': link['viewpoint_claim_link_id'], 'fields': ['effective_state']}]
    request = _request(initial, root, name='retire-maintenance', index_root=index_root,
        view_id=view['viewpoint_id'], permissions=permissions, creation_slots={})
    clients['gpt'].behavior = _behavior(view, link, 'retire')
    result = runner.run(store, request, root=root.parent / 'retire-action', execute=True, clients=clients)
    assert result['status'] == 'completed', _detail(store, request['action_id'])
    assert store.read_cvp_heads() == []
    assert store.get_record('canonical_viewpoints', view['viewpoint_id'])['identity_status'] == 'retired'
    assert store.get_record('viewpoint_claim_links', link['viewpoint_claim_link_id'])['effective_state'] == 'retired'


def test_merge_retains_old_identity_history_and_moves_existing_member_id(setup):
    store, initial, root, clients, _, view, link, index_root = _initial(setup)
    permissions = [
        {'collection': 'canonical_viewpoints', 'object_id': view['viewpoint_id'], 'fields': ['identity_status', 'redirect_to_viewpoint_id']},
        {'collection': 'viewpoint_claim_links', 'object_id': link['viewpoint_claim_link_id'],
         'fields': ['viewpoint_id', 'validated_against_viewpoint_revision_id']}]
    request = _request(initial, root, name='merge-maintenance', index_root=index_root,
        view_id=view['viewpoint_id'], permissions=permissions,
        creation_slots={'canonical_viewpoints': 1, 'viewpoint_revisions': 1})
    clients['gpt'].behavior = _behavior(view, link, 'merge')
    result = runner.run(store, request, root=root.parent / 'merge-action', execute=True, clients=clients)
    assert result['status'] == 'completed', _detail(store, request['action_id'])
    old = store.get_record('canonical_viewpoints', view['viewpoint_id'])
    current = store.read_cvp_heads()
    assert len(current) == 1 and current[0]['viewpoint_id'] != view['viewpoint_id']
    assert old['identity_status'] == 'merged' and old['redirect_to_viewpoint_id'] == current[0]['viewpoint_id']
    current_link = store.get_record('viewpoint_claim_links', link['viewpoint_claim_link_id'])
    assert current_link['viewpoint_id'] == current[0]['viewpoint_id']
    assert current_link['viewpoint_claim_link_id'] == link['viewpoint_claim_link_id']


def test_withdrawal_after_qualified_claim_change_preserves_historical_bindings(setup, monkeypatch):
    store, initial, root, clients, _, view, link, index_root = _initial(setup)
    from backend.api.canonical_repository.postgres_store import canonical_json, record_content_sha
    changed = deepcopy(store.get_record('claims', 'C1'))
    changed.update(statement='在此語境中，人子是神。', revision=2)
    audit = root / 'qualified-claim-v2.json'
    write_immutable(audit, {'fixture': 'independently reviewed substantive upstream change'})
    binding = {'path': str(audit), 'file_sha256': hashlib.sha256(audit.read_bytes()).hexdigest()}
    qualification = {'kind': 'claim_management_round', 'checkpoint': 'audited', 'outcome': 'completed',
        'program_readback_complete': True, 'revisit_required': False, 'stage_results': {'readback': binding},
        'audited_pins': [{'collection': 'claims', 'object_id': 'C1', 'pin': [2, record_content_sha(changed), False]}]}
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.objects SET revision=2,content_sha256=%s,payload=%s::jsonb WHERE collection='claims' AND object_id='C1'", (record_content_sha(changed), canonical_json(changed)))
        conn.execute("INSERT INTO wang_knowledge.pipeline_runs(run_id,subject_kind,subject_id,stage,trigger,status,metadata) VALUES('qualified-new-claim','batch','C1','cvp_group','cli','succeeded',%s::jsonb)", (canonical_json(qualification),))
    monkeypatch.setattr(store, 'claim_management_qualification', lambda cid: {'ready': True,
        'run_id': 'qualified-new-claim', 'revision': 2, 'content_sha256': record_content_sha(changed),
        'audit': binding, 'validation_kind': 'opus_and_program_readback', 'independent_audit_complete': False})
    permissions = [
        {'collection': 'canonical_viewpoints', 'object_id': view['viewpoint_id'], 'fields': ['identity_status']},
        {'collection': 'viewpoint_claim_links', 'object_id': link['viewpoint_claim_link_id'], 'fields': ['effective_state']}]
    request = _request(initial, root, name='withdraw-stale-claim', index_root=index_root,
        view_id=view['viewpoint_id'], permissions=permissions, creation_slots={})
    clients['gpt'].behavior = _behavior(view, link, 'retire')
    result = runner.run(store, request, root=root.parent / 'withdraw-stale', execute=True, clients=clients)
    assert result['status'] == 'completed', _detail(store, request['action_id'])
    withdrawn = store.get_record('viewpoint_claim_links', link['viewpoint_claim_link_id'])
    assert withdrawn['effective_state'] == 'retired'
    for field in ('pinned_claim_revision', 'component_locator', 'component_position_locator', 'evidence_bindings', 'claim_id', 'viewpoint_id', 'link_type'):
        assert withdrawn.get(field) == link.get(field)
    assert store.get_record('claims', 'C1') == changed
    assert withdrawn['schema_version'] == 'wang_viewpoint_claim_link_v2'
    import importlib.util
    import pytest
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('withdrawal_independent_audit',
        Path(__file__).parents[2] / 'scripts/audit-library.py')
    audit_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit_module)
    audit_store = audit_module.Store(store.database_url)
    assert audit_module._audit_component_locators(audit_store) == []
    row = next(r for r in audit_store.collection('viewpoint_claim_links') if r['object_id'] == link['viewpoint_claim_link_id'])
    from types import SimpleNamespace
    archive = SimpleNamespace(collection=audit_store.collection)
    with pytest.raises(ValueError, match='direct SQL history'):
        audit_module._certified_historical_withdrawal(archive, row)
    tampered = deepcopy(row)
    tampered['payload']['pinned_claim_revision'] = 2
    import pytest
    with pytest.raises(ValueError, match='source binding'):
        audit_module._certified_historical_withdrawal(audit_store, tampered)

    from backend.api.canonical_repository.viewpoint_foundation import validate_foundation_change_set, ViewpointFoundationValidationError
    with store.connect() as conn:
        existing = {(col, oid): {'payload': payload, 'retired_at': retired}
                    for col, oid, payload, retired in conn.execute(
                        'SELECT collection,object_id,payload,retired_at FROM wang_knowledge.objects').fetchall()}
    for field, value, expected in [('pinned_claim_revision', 2, 'historical source binding'),
                                   ('effective_state', 'active', 'pinned Claim revision mismatch'),
                                   ('schema_version', 'wang_viewpoint_claim_link_v1', 'pinned Claim revision mismatch')]:
        forged = deepcopy(withdrawn)
        forged[field] = value
        with pytest.raises(ViewpointFoundationValidationError, match=expected):
            validate_foundation_change_set({'viewpoint_claim_links': {link['viewpoint_claim_link_id']: forged}}, existing)
