"""Compile the complete saved normal-CVP review transcript, not SQL admission.

Explicit offline compilation reconstructs every actual request and immutable
intermediate through the existing validators with a read-only artifact sink.
Consumption authenticates the bounded certificate and its exact dependencies;
it never replays the runner, scans snapshots, contacts a provider or falls back.
Current Claim/source/hold, applied SQL, non-target continuity, whole independent
audit and group completion authorities remain separate REQUIRED gates.
"""
from pathlib import Path

from backend.api.canonical_repository.viewpoint_foundation import sha256_json, canonical_json
from backend.pipeline import source_reconciliation as c
from backend.pipeline.source_reconciliation_runner import body, seal
from backend.pipeline.viewpoint_validation_context import ValidationContext, ValidationLimits
from backend.pipeline.viewpoint_resolution_runtime import (
    read_artifact, saved_artifact_validation, stable_decided_at, write_immutable,
)
from backend.pipeline.viewpoint_compiled_readset import runtime_identity

SCHEMA = 'wang_saved_normal_cvp_review_certificate_v1'
PROJECT = Path(__file__).resolve().parents[2]
LIMITS = ValidationLimits(file_io_bytes=64*1024**2, wall_seconds=5)
MAX_CERTIFICATE_BYTES = 2*1024**2


def implementation_paths():
    # Pin the entire local validator implementation, including indirect helper
    # imports. This is a fixed CODE cut, not a data/history dependency list.
    return sorted({p.resolve() for directory, pattern in (
        ('backend/pipeline', '*.py'), ('backend/api/canonical_repository', '*.py'),
        ('backend/pipeline/prompts', '*'), ('config', 'wang_cvp*.md'),
    ) for p in (PROJECT/directory).glob(pattern) if p.is_file()})


def _bound(ctx, pin):
    path = Path(pin['path'])
    if not path.is_absolute() or set(pin) != {'path', 'file_sha256'}:
        raise ValueError('absolute exact file binding required')
    if ctx.pin(path) != pin['file_sha256']:
        raise ValueError('saved CVP physical input drift: '+str(path))
    return path


def _artifact(ctx, pin):
    value = ctx.read(_bound(ctx, pin))
    unsigned = body(value)
    if value.get('artifact_sha256') != sha256_json(unsigned):
        raise ValueError('saved CVP artifact seal drift')
    return unsigned


def _configuration(ctx, pin):
    # campaign.json predates sealed artifacts and is physically SHA-bound by
    # its caller. This exception is ONLY the configuration input, never packet,
    # raw model response, package or certificate. A stated seal must be valid.
    value = ctx.read(_bound(ctx, pin))
    if 'artifact_sha256' in value:
        return _artifact(ctx, pin)
    if not isinstance(value.get('cvp_policy'), dict):
        raise ValueError('saved normal CVP configuration lacks exact policy binding')
    return value


def compile_saved_review(*, packet_binding, configuration_binding, package_binding,
                         decision_time_binding, review_directory, output):
    """No automatic invocation or directory discovery; only exact saved input.

    The caller must separately authenticate any owner once-reissue authority,
    source/group membership and SQL state. This certifies the actual review of
    this exact packet/package; it does not imply those other gates passed.
    """
    from backend.pipeline.deferred_claim_cvp_continuation import adapters, resolved_report
    from backend.pipeline.viewpoint_batch_resolution_runner import run_batch
    from backend.api.canonical_repository.viewpoint_resolution import ReviewClaim
    from backend.api.canonical_repository.viewpoint_batch_changeset import compile_cvp_batch_package
    directory = Path(review_directory)
    if not directory.is_absolute() or not Path(output).is_absolute():
        raise ValueError('explicit absolute saved review/output paths required')
    declaration = dict(packet=packet_binding, configuration=configuration_binding,
                       package=package_binding, decision_time=decision_time_binding,
                       review_directory=str(directory))
    with ValidationContext('compile saved CVP review', limits=LIMITS) as ctx:
        ctx.depth = 1
        # Named absences are tracked below; no parent-directory history scan.
        ctx.receipt_policy_stack.append(False)
        packet = _artifact(ctx, packet_binding)
        cfg = _configuration(ctx, configuration_binding)
        expected_package = _artifact(ctx, package_binding)
        time_path = _bound(ctx, decision_time_binding)
        if time_path.name != 'decision-time.json':
            raise ValueError('exact stable decision-time binding required')
        policy_path = _bound(ctx, cfg['cvp_policy'])
        ctx.read(policy_path)
        paths = implementation_paths()
        for path in paths: ctx.pin(path)
        for stage in ('proposal', 'consolidation', 'review', 'reconsideration', 'final-review',
                      'review-format-repair', 'final-review-format-repair'):
            for suffix in ('.json', '.call-intent.json', '.request-encoding.json', '.call-failure.json'):
                ctx.optional_file_exists(directory/('raw-'+stage+suffix))
        with saved_artifact_validation():
            models = adapters(cfg, cached_only=True)
            report = run_batch(batch_id=packet['batch_id'], scope_label=packet['scope_label'],
                claims=[ReviewClaim.model_validate(x) for x in packet['claims']],
                registry_context=packet['registry_context'], pending_candidates=[],
                output_dir=directory, current_claim_links=packet['existing_links'], **models)
            resolved_report(report, [x['claim_id'] for x in packet['claims']])
            final = report['_final_review'] is not None
            proposal = read_artifact(directory/('raw-reconsideration.json' if final else 'raw-proposal.json'))
            review = read_artifact(directory/('raw-final-review.json' if final else 'raw-review.json'))
            reconstructed = compile_cvp_batch_package(proposal=report['_effective_proposal'],
                review=report['_review'], deterministic_validation_sha256=report['_effective_validation']['artifact_sha256'],
                scope_manifest_sha256=packet['current_claim_manifest_sha256'],
                claims=[ReviewClaim.model_validate(x) for x in packet['claims']],
                registry_context=packet['registry_context'], revision_dependents=report['_revision_dependents'],
                proposal_artifact_sha256=proposal['artifact_sha256'], review_artifact_sha256=review['artifact_sha256'],
                proposer_model_id=proposal['model_id'], reviewer_model_id=review['model_id'],
                decided_at=stable_decided_at(time_path.parent), current_claim_links=packet['existing_links'])
        if reconstructed != expected_package:
            raise ValueError('actual saved raw/request/review/package mismatch')
        if paths != implementation_paths():
            raise ValueError('validator implementation population changed')
        executed = {name: data for name, data in report['recorded_model_executions']['stages'].items()
                    if data.get('calls_recorded', 0) or data.get('artifact_sha256')}
        if not {'proposal', 'review'} <= set(executed):
            raise ValueError('saved initial proposal/review transcript incomplete')
        value = dict(schema_version=SCHEMA, status='saved_review_verified_not_registry_admission',
            declaration=declaration, declaration_sha256=sha256_json(declaration),
            package=reconstructed, package_sha256=sha256_json(reconstructed),
            claim_ids=sorted(x['claim_id'] for x in packet['claims']),
            terminal_review_sha256=report['review_sha256'],
            effective_proposal_sha256=report['effective_proposal_sha256'],
            runtime_identity=runtime_identity(),
            model_executions=executed,
            implementation_paths=[str(p) for p in paths],
            dependencies=[{'path':str(p), 'file_sha256':pin} for p, (_, pin) in sorted(ctx.files.items())],
            absent_dependencies=sorted(str(p) for p in ctx.absent_files),
            provider_calls=0, registry_writes=0, sql_admission_granted=False,
            independent_audit_complete=False)
    # Publish only after every physical dependency and absence is reverified.
    if len(canonical_json(value).encode()) > MAX_CERTIFICATE_BYTES:
        raise ValueError('saved review certificate exceeded bounded size')
    write_immutable(Path(output), seal(value), require_new=True)
    return value, ctx.metrics


def consume_saved_review(certificate_binding, *, declaration):
    """Cold-process consumption; no historical runner/cache/provider fallback."""
    with ValidationContext('consume saved CVP review', limits=LIMITS) as ctx:
        path = Path(certificate_binding['path'])
        if path.stat().st_size > MAX_CERTIFICATE_BYTES:
            raise ValueError('oversized saved review certificate')
        value = _artifact(ctx, certificate_binding)
        if (value.get('schema_version') != SCHEMA
                or value.get('status') != 'saved_review_verified_not_registry_admission'
                or value.get('declaration') != declaration
                or value.get('declaration_sha256') != sha256_json(declaration)
                or value.get('package_sha256') != sha256_json(value['package'])
                or value.get('sql_admission_granted') is not False
                or value.get('independent_audit_complete') is not False
                or value.get('provider_calls') != 0 or value.get('registry_writes') != 0):
            raise ValueError('saved review certificate identity/authority drift')
        if value.get('runtime_identity') != runtime_identity():
            raise ValueError('saved review Python/schema runtime drift')
        paths = [str(p) for p in implementation_paths()]
        if value['implementation_paths'] != paths:
            raise ValueError('saved review validator population drift')
        pins = value['dependencies']
        names = [p['path'] for p in pins]
        required = set(paths) | {declaration[k]['path'] for k in ('packet', 'configuration', 'package', 'decision_time')}
        stages = value.get('model_executions', {})
        filenames = {'proposal':'proposal', 'consolidation':'consolidation', 'review':'review',
                     'reconsideration':'reconsideration', 'final_review':'final-review'}
        if not {'proposal','review'} <= set(stages) or not set(stages) <= set(filenames):
            raise ValueError('saved model execution population drift')
        for stage in stages:
            base = Path(declaration['review_directory'])/('raw-'+filenames[stage])
            required.add(str(base.with_suffix('.json')))
        required.add(str(Path(declaration['review_directory'])/'batch-packet.json'))
        if len(names) != len(set(names)) or not required <= set(names):
            raise ValueError('saved review dependency population incomplete')
        by_path = {p['path']:p for p in pins}
        if any(by_path.get(declaration[k]['path']) != declaration[k]
               for k in ('packet','configuration','package','decision_time')):
            raise ValueError('saved review declared physical binding differs from read dependency')
        for pin in pins: _bound(ctx, pin)
        for absent in value['absent_dependencies']:
            if absent in names or not Path(absent).is_absolute() or ctx.optional_file_exists(absent):
                raise ValueError('saved review optional stage appeared')
    return value, ctx.metrics
