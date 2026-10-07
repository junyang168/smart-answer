"""Performance release gate, independent of semantic/model approval.

No benchmark is fabricated here. A measured, implementation-bound acceptance
artifact must exist before a recovery can even attempt its old cold preflight.
Routine fallback to full history/table rebuild is not an accepted result.
"""
from pathlib import Path
import json

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import file_sha256

PROJECT = Path(__file__).resolve().parents[2]
SCHEMA = 'wang_cvp_scalability_acceptance_v1'
IMPLEMENTATION = (
    'backend/pipeline/viewpoint_scalability_acceptance.py',
    'backend/pipeline/viewpoint_validation_context.py',
    'backend/pipeline/viewpoint_cvp_campaign_runner.py',
    'backend/pipeline/viewpoint_scope_packet_runner.py',
    'backend/pipeline/viewpoint_current_scope.py',
    'backend/pipeline/viewpoint_cvp_recovery_executor.py',
    'backend/pipeline/viewpoint_startup_handoff.py',
    'backend/pipeline/codex_subscription_preflight.py',
    'backend/pipeline/source_reconciliation.py',
    'backend/pipeline/source_reconciliation_runtime.py',
    'backend/pipeline/source_reconciliation_runner.py',
    'backend/pipeline/source_reconciliation_epoch.py',
    'backend/pipeline/owner_approved_link_correction.py',
    'backend/pipeline/deferred_claim_cvp_continuation.py',
    'backend/pipeline/immutable_proof_reuse.py',
    'backend/pipeline/viewpoint_compiled_readset.py',
    'backend/pipeline/viewpoint_saved_cvp_certificate.py',
    'backend/pipeline/viewpoint_saved_audit_certificate.py',
    'scripts/audit-readset.py',
    'scripts/audit-readset-index.sql',
    'backend/pipeline/viewpoint_resolution_runtime.py',
    'backend/pipeline/viewpoint_batch_resolution_runner.py',
    'backend/pipeline/viewpoint_applied_source_epochs.py',
    'backend/pipeline/viewpoint_grouping_repository.py',
    'backend/pipeline/viewpoint_group_audit_population.py',
    'backend/pipeline/viewpoint_content_blocked_fallback.py',
    'backend/pipeline/viewpoint_s201129_source_continuation.py',
    'backend/api/canonical_repository/viewpoint_production_safety.py',
    'backend/api/canonical_repository/viewpoint_foundation.py',
    'backend/api/canonical_repository/reviewed_candidate_contract.py',
    'backend/api/canonical_repository/postgres_store.py',
    'backend/api/canonical_repository/integrity_journal.py',
    'backend/api/canonical_repository/integrity_baseline.py',
    'backend/api/canonical_repository/integrity_transition.py',
    'backend/api/canonical_repository/integrity_ledger_indexes.sql',
    'backend/api/canonical_repository/integrity_journal_install.sql',
    'scripts/audit-library.py',
)
PHASES = ('startup', 'completion_consumption')
SCALES = (1, 10, 100)


def positive_int(value):
    return type(value) is int and value > 0


def require_scalability_acceptance(path):
    if path is None:
        raise ValueError('scalability acceptance missing; production remains stopped; no cold full-rebuild fallback')
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('absolute scalability acceptance path required')
    value = json.loads(path.read_text(encoding='utf-8'))
    expected = value.get('artifact_sha256')
    body = {k: v for k, v in value.items() if k != 'artifact_sha256'}
    if expected != sha256_json(body) or body.get('schema_version') != SCHEMA:
        raise ValueError('scalability acceptance seal/schema drift')
    if body.get('status') != 'measured_pass' or body.get('production_execution_performed') is not False:
        raise ValueError('scalability acceptance must be measured offline, not a production-start receipt')
    pins = body.get('implementation_bindings', [])
    wanted = {str(PROJECT / p) for p in IMPLEMENTATION}
    if (len(pins) != len(wanted) or {b.get('path') for b in pins} != wanted
            or any(set(b) != {'path','file_sha256'} or file_sha256(Path(b['path'])) != b['file_sha256'] for b in pins)):
        raise ValueError('scalability acceptance implementation changed or denominator incomplete')
    # The benchmark's actual output and driver are physical inputs too. Logic
    # regression reports alone are not measured scalability reports.
    for key in ('measurement_report', 'benchmark_driver'):
        pin = body.get(key, {})
        if (set(pin) != {'path','file_sha256'} or not Path(pin['path']).is_absolute()
                or file_sha256(Path(pin['path'])) != pin['file_sha256']):
            raise ValueError('scalability acceptance benchmark input missing/changed')
    measured = json.loads(Path(body['measurement_report']['path']).read_text(encoding='utf-8'))
    if measured.get('results') != body.get('results'):
        raise ValueError('scalability acceptance differs from actual measurement report')
    results = body.get('results', [])
    required = {(p, m, n, h) for p in PHASES for m in ('cold','warm') for n in SCALES for h in SCALES}
    indexed = {}
    for result in results:
        point = (result.get('phase'), result.get('mode'), result.get('unrelated_records_scale'), result.get('history_scale'))
        if point not in required or point in indexed:
            raise ValueError('scalability benchmark population duplicate/unexpected')
        indexed[point] = result
        if (not positive_int(result.get('process_id')) or result.get('model_calls') != 0
                or result.get('registry_writes') != 0 or result.get('full_history_snapshot_parses') != 0
                or result.get('all_active_payload_reads') != 0 or result.get('full_rebuild_fallbacks') != 0
                or result.get('owned_runtime_startups') != (1 if point[1] == 'cold' else 0)):
            raise ValueError('scalability benchmark used forbidden full rebuild, mutation or double cold start')
        if any(type(result.get(k)) is not int for k in (
                'model_calls','registry_writes','full_history_snapshot_parses',
                'all_active_payload_reads','full_rebuild_fallbacks','owned_runtime_startups')):
            raise ValueError('scalability execution counters must be measured integers')
        for key in ('file_io_bytes','sql_payload_rows','proof_rebuilds','peak_rss_bytes'):
            if type(result.get(key)) is not int or result[key] < 0:
                raise ValueError('scalability counters missing/invalid')
        seconds = result.get('wall_seconds')
        if (type(seconds) not in (int,float) or not 0 <= seconds <= 5
                or result['file_io_bytes'] > 64*1024**2 or result['peak_rss_bytes'] > 512*1024**2):
            raise ValueError('scalability benchmark exceeded routine resource ceiling')
        if (not isinstance(result.get('affected_readset_sha256'), str)
                or len(result['affected_readset_sha256']) != 64
                or any(c not in '0123456789abcdef' for c in result['affected_readset_sha256'])):
            raise ValueError('fixed affected set is not content-bound')
    if set(indexed) != required:
        raise ValueError('scalability benchmark must cover both phases, cold/warm and 1x/10x/100x data/history')
    for phase in PHASES:
        for mode in ('cold','warm'):
            base = indexed[phase,mode,1,1]
            for n in SCALES:
                for h in SCALES:
                    row = indexed[phase,mode,n,h]
                    if row['affected_readset_sha256'] != base['affected_readset_sha256']:
                        raise ValueError('growth benchmark changed the affected set')
                    if (any(row[k] > base[k] for k in ('file_io_bytes','sql_payload_rows','proof_rebuilds'))
                            or row['peak_rss_bytes'] > base['peak_rss_bytes']+32*1024**2):
                        raise ValueError('routine validation grew with unrelated records/history')
    # Each expanded cold workload must actually be a new process, not a warm
    # cache labelled cold. Startup and completion share ONE owned runtime.
    cold_pids = set()
    for n in SCALES:
        for h in SCALES:
            pid = indexed['startup','cold',n,h]['process_id']
            if pid in cold_pids:
                raise ValueError('cold benchmark reused an already warm process')
            cold_pids.add(pid)
            if any(indexed[p,m,n,h]['process_id'] != pid for p in PHASES for m in ('cold','warm')):
                raise ValueError('benchmark used double startup instead of one owned runtime')
    return {'path':str(path),'file_sha256':file_sha256(path),'schema_version':SCHEMA,
            'status':'measured_pass','growth_points':len(results)}
