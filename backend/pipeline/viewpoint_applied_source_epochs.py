"""Compose separately authenticated source transitions; never ID/status waivers."""
from pathlib import Path
from backend.pipeline import viewpoint_g041_source_epoch as historical
from backend.pipeline import viewpoint_s201129_source_epoch as local

GROUPING_EPOCH_KEY = historical.GROUPING_EPOCH_KEY
VERSION = 'wang_exact_applied_source_epochs_v1'


def implementation(bound):
    path = Path(bound.get('path',''))
    if path == historical.PACKAGE:
        return historical
    if path == local.repair.PACKAGE:
        return local
    if path.is_file():
        from backend.pipeline import source_reconciliation_epoch as generic
        # Route only a recognized generic artifact. Its validator then proves
        # the actual review/apply/completion, not merely this schema label.
        import json
        raw = json.loads(path.read_text(encoding='utf-8'))
        if (raw.get('source_repair_application') or {}).get('schema_version') == 'wang_owner_claim_statement_revision_v1':
            from backend.pipeline import claim_statement_revision as statement
            if statement.binding(path) != bound:
                raise ValueError('statement revision physical package drift')
            statement.validate_package(raw)
            return statement
        if (raw.get('source_repair_application') or {}).get('schema_version') == 'wang_source_reconciliation_v1':
            if generic.file_binding(path) != bound:
                raise ValueError('generic epoch physical package drift')
            generic.load(bound)
            return generic
    # Preserve the legacy entry's own exact physical/path validator; an
    # unknown single binding is not accepted by this routing operation.
    return historical


def members(bound):
    if bound.get('schema_version') != VERSION:
        implementation(bound)
        return [bound]
    if set(bound) != {'schema_version','bindings'} or not bound['bindings']:
        raise ValueError('invalid composite source epoch shape')
    rows = bound['bindings']
    if rows != sorted(rows,key=lambda b:b['path']) or len(rows) != len({r['path'] for r in rows}):
        raise ValueError('duplicate or unordered source epoch proofs')
    for row in rows:
        if set(row) != {'path','file_sha256'}:
            raise ValueError('nested or unbound source epoch')
        routed = implementation(row)
        if Path(row['path']) not in {historical.PACKAGE, local.repair.PACKAGE} and routed is historical:
            raise ValueError('unrecognized composite source epoch package')
    return rows


def compose(paths):
    known = {historical.PACKAGE:historical,local.repair.PACKAGE:local}
    selected = []
    generic_rows = {}
    for raw_path in paths:
        path = Path(raw_path)
        if path in known:
            selected.append(path)
        elif path.is_file():
            from backend.pipeline.source_reconciliation_runner import binding
            bound = binding(path)
            routed = implementation(bound)
            if routed is not historical:
                selected.append(path)
                generic_rows[path] = bound
    if len(selected) != len(set(selected)):
        raise ValueError('duplicate applied source epoch path')
    rows = [known[p].binding() if p in known else generic_rows[p] for p in sorted(selected)]
    if not rows:
        return None
    return rows[0] if len(rows) == 1 else {'schema_version':VERSION,'bindings':rows}


def verified_applied(bound, store):
    return [implementation(row).verified_applied(row,store) for row in members(bound)]


def grouping_physical_transition(manifest, bound):
    transitions = {}
    for row in members(bound):
        current = implementation(row).grouping_physical_transition(manifest,row)
        if transitions.keys() & current.keys():
            raise ValueError('conflicting source physical transitions')
        transitions.update(current)
    return transitions


def bridge(**kwargs):
    return implementation(kwargs['source_repair_binding']).bridge(**kwargs)


def grouping_current_claim_packets(manifest, bound, claim_ids):
    """Only a fully proved transition may replace old content comparison pins."""
    result = {}
    for row in members(bound):
        projector = getattr(implementation(row), 'grouping_current_claim_packets', None)
        if projector is None:
            continue  # No projection authority: the original strict check stays.
        projected = projector(manifest, row, claim_ids)
        if result.keys() & projected.keys() or set(projected) - set(claim_ids):
            raise ValueError('conflicting or foreign source epoch grouping projection')
        result.update(projected)
    return result
