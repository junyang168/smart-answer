"""Zero-call evaluation safeguards; synthetic content is never a quality result."""
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('cvp_quality_preflight', Path(__file__).parents[2] / 'scripts/cvp-quality-preflight.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def row(collection, oid, payload, revision=1, retired=False):
    return dict(kind='object', collection=collection, object_id=oid, payload=payload,
                revision=revision, retired=retired)


def test_random_sampling_is_order_independent_without_replacement():
    units = [dict(viewpoint_id=f'CV-{i:03}', stratum='rare' if i < 2 else 'common') for i in range(30)]
    a = MODULE.stratified_sample(units, 429, 12, 'viewpoint_id')
    assert a == MODULE.stratified_sample(list(reversed(units)), 429, 12, 'viewpoint_id')
    assert len({u['viewpoint_id'] for u in a['selected']}) == 12
    for stratum in a['strata']:
        assert stratum['inclusion_probability'] == stratum['n'] / stratum['population']
    with pytest.raises(ValueError):
        MODULE.stratified_sample(units, 429, 1, 'viewpoint_id')


def test_all_identity_members_and_roles_are_distinct():
    rows = [row('canonical_viewpoints', 'CV-a', dict(identity_status='active', current_revision_id='CVR-a')),
            row('viewpoint_revisions', 'CVR-a', dict(viewpoint_id='CV-a'))]
    for n, role in enumerate(['equivalent_full', 'equivalent_component', 'supports']):
        rows += [row('claims', f'CL-{n}', dict(occurrences=[dict(source_id=f'SRC-{n}')])),
                 row('viewpoint_claim_links', f'VCL-{n}', dict(viewpoint_id='CV-a', claim_id=f'CL-{n}',
                       effective_state='active', link_type=role, pinned_claim_revision=1,
                       validated_against_viewpoint_revision_id='CVR-a'))]
    sources = [dict(source_id=f'SRC-{n}', path=f'/source{n}', source_type='sermon_transcript') for n in range(3)]
    units, findings = MODULE.inventory(rows, sources)
    assert units[0]['identity_claim_ids'] == ['CL-0', 'CL-1']
    assert units[0]['active_link_ids'] == ['VCL-0', 'VCL-1', 'VCL-2']
    assert units[0]['identity_source_keys'] == ['/source0', '/source1']
    assert 'non_identity_role' in units[0]['risk_flags']
    assert all(f['semantic_verdict'] is None for f in findings)
    # A missing final member stays in the manifest and makes the unit risky.
    units, _ = MODULE.inventory([r for r in rows if r['object_id'] != 'CL-1'], sources)
    assert 'CL-1' in units[0]['identity_claim_ids']
    assert 'missing_or_retired_claim' in units[0]['risk_flags']


def test_aliases_do_not_inflate_source_count_and_versions_are_not_rebound():
    rows = [row('canonical_viewpoints', 'CV-a', dict(identity_status='active', current_revision_id='CVR-a')),
            row('viewpoint_revisions', 'CVR-a', dict(viewpoint_id='CV-a')),
            row('claims', 'CL-a', dict(occurrences=[dict(source_id='same')]), revision=2),
            row('viewpoint_claim_links', 'VCL-a', dict(viewpoint_id='CV-a', claim_id='CL-a',
                effective_state='active', link_type='equivalent_full', pinned_claim_revision=1,
                validated_against_viewpoint_revision_id='CVR-a'))]
    sources = [dict(source_id=f'SRC-{n}', transcript_id='same', path='/same', source_type='sermon_transcript') for n in range(2)]
    unit = MODULE.inventory(rows, sources)[0][0]
    assert unit['identity_source_keys'] == ['/same']
    assert 'single_source' in unit['stratum']
    assert 'claim_version_mismatch' in unit['risk_flags']


def test_physical_and_project_metadata_drift_and_editorial_exclusions(tmp_path):
    manuscript = tmp_path / 'final.md'
    manuscript.write_text('complete source')
    metadata = tmp_path / 'meta.json'
    metadata.write_text(json.dumps(dict(project_type='transcript')))
    docs = [row('source_documents', 'SRC-a', dict(source_type='notes_manuscript', source_path=str(manuscript)))]
    sources = MODULE.physical_inventory(docs, tmp_path)
    assert not sources[0]['eligible']
    assert sources[0]['eligibility_reason'] == 'editorial_view'
    assert MODULE.verify_sources(sources) == []
    metadata.write_text(json.dumps(dict(project_type='notes')))
    assert MODULE.verify_sources(sources)
    sources = MODULE.physical_inventory(docs, tmp_path)
    manuscript.write_text('source changed')
    assert MODULE.verify_sources(sources)


def test_immutable_outputs_preserve_existing_evidence(tmp_path):
    path = tmp_path / 'baseline.json'
    MODULE.write_new(path, dict(call_count=0))
    with pytest.raises(FileExistsError):
        MODULE.write_new(path, dict(call_count=1))
    assert json.loads(path.read_text()) == dict(call_count=0)


def test_risk_sample_keeps_missing_members_ahead_of_common_role_labels():
    units = [dict(viewpoint_id='CV-complex', risk_flags=['component_role', 'non_identity_role'], identity_claim_ids=['CL-a']),
             dict(viewpoint_id='CV-empty', risk_flags=['no_identity_members'], identity_claim_ids=[])]
    assert MODULE.risk_sample(units, 1)[0]['viewpoint_id'] == 'CV-empty'


def test_stored_semantic_sha_ignores_storage_revision_but_rejects_content_change():
    r = row('claims', 'CL-a', dict(revision=2, statement='original'))
    r['content_sha256'] = MODULE.sha(dict(statement='original'))
    MODULE.verify_content_pins([r])
    r['payload']['statement'] = 'changed'
    with pytest.raises(ValueError, match='semantic content SHA'):
        MODULE.verify_content_pins([r])


def test_database_reader_enforces_single_read_only_snapshot(monkeypatch):
    class Result:
        returncode = 0
        stdout = '{"kind":"claim_frame"}\n'
    observed = {}
    def run(command, **kwargs):
        observed.update(command=command, sql=kwargs['input'])
        return Result()
    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    assert MODULE.query_snapshot('test-url') == [dict(kind='claim_frame')]
    assert '-XAtq' in observed['command']
    assert observed['command'][-2:] == ['-f', '-']
    assert 'REPEATABLE READ READ ONLY' in observed['sql']
    for mutation in ('INSERT ', 'UPDATE ', 'DELETE ', 'CREATE ', 'ALTER '):
        assert mutation not in observed['sql']
