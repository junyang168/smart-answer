"""No hidden SELECT-all/rebuild fallback in a bounded normal runtime."""
from pathlib import Path
from types import SimpleNamespace
import pytest

from backend.pipeline import viewpoint_validation_context as v


def forbidden(*_args, **_kwargs):
    pytest.fail('legacy full-history body or database read was entered')


@pytest.mark.parametrize('entry', ['snapshot', 'epoch_load', 'predecessor', 'build', 'audit', 'typed'])
@pytest.mark.parametrize('owner', ['phase', 'runtime_outside_phase'])
def test_known_unbounded_paths_are_vetoed_before_io_import_copy_or_database(monkeypatch, entry, owner):
    from backend.pipeline import source_reconciliation_epoch as e
    from backend.pipeline import deferred_claim_cvp_continuation as d
    from backend.pipeline import owner_approved_link_correction as l
    from backend.pipeline.source_reconciliation_runtime import ProductionPorts
    store = SimpleNamespace(connect=forbidden, list_records=forbidden)
    monkeypatch.setattr(e.c, 'verify_file', forbidden)
    monkeypatch.setattr(d, 'read', forbidden)
    monkeypatch.setattr(l, 'read_artifact', forbidden)
    entries = {
        'snapshot': lambda: ProductionPorts(store).snapshot(),
        'epoch_load': lambda: e.load({'path': '/never-read', 'file_sha256': '0'*64},historical=True),
        'predecessor': lambda: e._predecessor_projection({}, {}, store),
        'build': lambda: d.build_proof(Path('/absent'), Path('/campaign'), [], None),
        'audit': lambda: l.historical_audit_proofs('/absent/audit.json', {}),
        'typed': lambda: v.typed_records(store, 'claims', object, 'claim_id'),
    }
    if entry=='snapshot':
        with pytest.raises(RuntimeError,match='runtime is retired'):entries[entry]()
        return
    with pytest.raises(v.ValidationBudgetExceeded) as caught:
        with v.validation_runtime(limits=v.ROUTINE_VALIDATION_LIMITS):
            if owner == 'phase':
                with v.ValidationContext('routine'): entries[entry]()
            else: entries[entry]()
    assert caught.value.detail['metric'] == 'unbounded_algorithm'
    assert caught.value.detail['automatic_fallback'] is False


def test_explicit_legacy_maintenance_gate_grants_no_approval_and_does_not_change_old_snapshot():
    rows = [('claims', 'C', {'claim_id': 'C', 'revision': 1})]
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def cursor(self): return self
        def execute(self, *_): pass
        def fetchall(self): return rows
    from backend.pipeline.source_reconciliation_runtime import ProductionPorts
    with v.ValidationContext('explicit legacy maintenance'):
        with pytest.raises(RuntimeError,match='runtime is retired'):
            ProductionPorts(SimpleNamespace(connect=Connection)).snapshot()


def test_startup_does_not_prewarm_whole_sql_collections(monkeypatch):
    from backend.pipeline import viewpoint_cvp_campaign_runner as runner
    from backend.pipeline import viewpoint_grouping_repository as grouping
    from backend.pipeline import viewpoint_applied_source_epochs as epochs
    from backend.api.canonical_repository import reviewed_candidate_contract as contract
    from backend.api.canonical_repository import viewpoint_production_safety as safety
    from backend.api.canonical_repository import postgres_store
    calls = []
    monkeypatch.setattr(contract, 'validate_store_package_authorization', lambda p: calls.append(('package', p)))
    monkeypatch.setattr(runner, 'read', lambda p: {'bound': str(p)})
    monkeypatch.setattr(epochs, 'compose', lambda p: calls.append(('epochs', p)) or 'epoch')
    monkeypatch.setattr(grouping, 'SharedGroupingRepository', lambda p: SimpleNamespace(
        load=lambda *a, **k: calls.append(('grouping', a, k))))
    monkeypatch.setattr(safety, 'collection_cut', forbidden)
    monkeypatch.setattr(v, 'typed_records', forbidden)
    monkeypatch.setattr(postgres_store, 'PostgresKnowledgeStore', forbidden)
    args = SimpleNamespace(completed_source_repair=['/bound'], grouping_repository='/groups', accepted_grouping_sha='SHA')
    with v.validation_runtime(limits=v.ROUTINE_VALIDATION_LIMITS):
        result = runner.initialize_validation(args)
    assert [row[0] for row in calls] == ['package', 'epochs', 'grouping']
    assert result['model_calls'] == result['Registry_writes'] == 0
    assert 'no whole-collection SQL prewarm' in result['scope']


def test_startup_consumes_completed_source_instead_of_store_write_authorization(monkeypatch):
    from backend.pipeline import viewpoint_cvp_campaign_runner as runner
    from backend.pipeline import viewpoint_grouping_repository as grouping
    from backend.pipeline import viewpoint_applied_source_epochs as epochs
    from backend.pipeline import source_reconciliation_epoch as epoch
    from backend.api.canonical_repository import reviewed_candidate_contract as contract
    package={'source_repair_application':{'schema_version':'wang_source_reconciliation_v1'}}
    calls=[]
    monkeypatch.setattr(runner,'read',lambda _:package)
    monkeypatch.setattr(runner,'binding',lambda p:{'path':p,'file_sha256':'0'*64})
    monkeypatch.setattr(contract,'validate_store_package_authorization',forbidden)
    monkeypatch.setattr(epoch,'load_completed',lambda pin:calls.append(('completed',pin)))
    monkeypatch.setattr(epochs,'compose',lambda paths:'epoch')
    monkeypatch.setattr(grouping,'SharedGroupingRepository',lambda _:SimpleNamespace(
        load=lambda *a,**k:calls.append(('grouping',a,k))))
    args=SimpleNamespace(completed_source_repair=['/completed/effective-package.json'],
        grouping_repository='/groups',accepted_grouping_sha='SHA')
    result=runner.initialize_validation(args)
    assert [row[0] for row in calls]==['completed','grouping']
    assert result['model_calls']==result['Registry_writes']==0
