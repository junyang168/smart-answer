import hashlib
import json

import pytest

from backend.pipeline.legacy_candidate_reconciliation import authenticate_batched_review
from backend.pipeline.claim_layer_review_batch import merge_review_artifacts, split_claim_layer_package
from backend.pipeline.corpus_ai_review_runner import _review_artifact_sha256
from backend.tests.test_claim_layer_review_batch import _package, _artifact


def fixture(tmp_path):
    package = _package()
    hashes = {}
    for source in package['source_documents']:
        p = tmp_path / (source['source_id'] + '.json')
        p.write_text('{"script": []}')
        source.update(source_path=str(p), source_file_sha256=hashlib.sha256(p.read_bytes()).hexdigest())
        hashes[source['source_id']] = source['source_file_sha256']
    path = tmp_path / 'input.json'
    path.write_text(json.dumps(package))
    review_path = tmp_path / 'test.independent-review.json'
    directory = tmp_path / 'test.independent-review-batches' / 'reviews'
    directory.mkdir(parents=True)
    artifacts = []
    for index, batch in enumerate(split_claim_layer_package(package, batch_size=3), 1):
        artifact = _artifact(batch, index)
        artifact['source']['transcript_sha256'] = dict(hashes)
        artifact['reviewer']['artifact_sha256'] = _review_artifact_sha256(artifact)
        (directory / f'batch-{index:02}.independent-review.json').write_text(json.dumps(artifact))
        artifacts.append(artifact)
    review = merge_review_artifacts(artifacts, source_package=package, source_package_path=path)
    return package, path, review_path, review, directory


def test_reconstructs_batched_review_and_binds_every_physical_source(tmp_path):
    package, path, review_path, review, _ = fixture(tmp_path)
    hashes, paths = authenticate_batched_review(review_path, review, path, package)
    assert set(hashes) == set(paths) == {'SOURCE-1', 'SOURCE-2', 'SOURCE-3'}


@pytest.mark.parametrize('case', ['aggregate', 'batch', 'missing_batch', 'source_bytes', 'source_sha', 'relative_source', 'conflicting_hashes'])
def test_batched_proof_rejects_drift(tmp_path, case):
    package, path, review_path, review, directory = fixture(tmp_path)
    if case == 'aggregate': review['claim_reviews'][0]['decision'] = 'human_review_required'
    elif case == 'batch':
        p = directory / 'batch-01.independent-review.json'
        value = json.loads(p.read_text()); value['claim_reviews'][0]['rationale'] = 'tampered'
        p.write_text(json.dumps(value))
    elif case == 'missing_batch': (directory / 'batch-02.independent-review.json').unlink()
    elif case == 'source_bytes':
        from pathlib import Path
        Path(package['source_documents'][0]['source_path']).write_text('changed')
    elif case in {'source_sha', 'relative_source'}:
        field = 'source_file_sha256' if case == 'source_sha' else 'source_path'
        # Changing the source package is itself rejected before source checks.
        package['source_documents'][0][field] = 'wrong'
    else:
        p = directory / 'batch-02.independent-review.json'
        value = json.loads(p.read_text()); value['source']['transcript_sha256']['SOURCE-1'] = 'wrong'
        value['reviewer']['artifact_sha256'] = _review_artifact_sha256(value)
        p.write_text(json.dumps(value))
        artifacts = [json.loads(p.read_text()) for p in sorted(directory.glob('*.json'))]
        review = merge_review_artifacts(artifacts, source_package=package, source_package_path=path)
    with pytest.raises(ValueError): authenticate_batched_review(review_path, review, path, package)


def test_sealed_passing_recipient_uses_exact_merge_not_initial_pass(tmp_path, monkeypatch):
    from backend.tests.test_viewpoint_legacy_merge_target_bridge import fixture as merge_fixture
    from backend.tests.test_viewpoint_historical_review_bridge import write_binding
    from backend.pipeline import viewpoint_historical_review_bridge as bridge
    from backend.pipeline import viewpoint_source_attestation_runner as lineage
    args, bundle = merge_fixture(tmp_path, monkeypatch)
    final_binding = args.pop('legacy_reviewed_candidate_binding')
    from pathlib import Path
    final = json.loads(Path(final_binding['path']).read_text())
    review = json.loads(Path(args['review_binding']['path']).read_text())
    review['reviewer']['provider'] = 'anthropic_batched'
    args['review_binding'] = write_binding(Path(args['review_binding']['path']), review)
    bundle['review_sha256'] = args['review_binding']['file_sha256']
    package = json.loads(Path(args['review_input_binding']['path']).read_text())
    item = {'path': Path(final_binding['path']), 'payload': final,
        'review_payload': review, 'review_input_payload': package,
        'application': {'review_resolutions': [{'claim_id': 'C1',
            'target_review_status': 'ai_consensus_reviewed', 'adjudication_status': 'not_required',
            'independent_review_decision': 'pass', 'reviewer_id': 'independent-batch-model'}]}}
    args['sealed_lineage_binding'] = write_binding(tmp_path / 'lineage.json', {'claims': [{'claim_id': 'C1'}]})
    monkeypatch.setattr(lineage, '_validated_lineage_inputs', lambda **kw: {'C1': item})
    proof = bridge.verify_historical_review_bridge(**args)
    assert proof['legacy_merge_authority']['merged_claim_ids'] == ['C2']
    assert proof['legacy_reviewed_candidate_binding'] == final_binding
    assert proof['review_provider'] == 'anthropic_batched'
    assert proof['model_calls_executed'] == proof['master_data_mutations'] == 0
    bundle['adjudication_results']['C2']['status'] = 'pending'
    with pytest.raises(bridge.CvpProductionBlocked): bridge.verify_historical_review_bridge(**args)
