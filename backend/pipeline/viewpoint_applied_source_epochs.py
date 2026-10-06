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
        if Path(row['path']) not in {historical.PACKAGE, local.repair.PACKAGE}:
            raise ValueError('unrecognized composite source epoch package')
        implementation(row)
    return rows


def compose(paths):
    known = {historical.PACKAGE:historical,local.repair.PACKAGE:local}
    selected = [Path(p) for p in paths if Path(p) in known]
    if len(selected) != len(set(selected)):
        raise ValueError('duplicate applied source epoch path')
    rows = [known[p].binding() for p in sorted(selected)]
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
