from copy import deepcopy
from pathlib import Path
import pytest
from backend.pipeline.viewpoint_semantic_calibration import blind_dataset, reserve, reserve_call, Answers
from backend.pipeline.viewpoint_resolution_runtime import read_artifact
from backend.pipeline.viewpoint_semantic_eval import review_cases, score

FIXTURES=Path(__file__).parent/'fixtures'


def test_blinding_removes_semantic_names_and_expected_labels():
    dataset=read_artifact(FIXTURES/'cvp_contextual_semantics_v2_cases.json')
    original=deepcopy(dataset)
    blind,mapping=blind_dataset(dataset)
    assert dataset==original
    assert list(mapping)==[f'C{n:03d}' for n in range(1,14)]
    payload=review_cases(blind)
    assert all('expected' not in row and row['id'].startswith('C') for row in payload['cases'])
    assert set(mapping.values())=={r['id'] for r in dataset['cases']}


def test_interrupted_call_reservation_never_resets(tmp_path):
    path=tmp_path/'once.json'
    reserve(path,{'payload':'frozen'})
    with pytest.raises(FileExistsError):
        reserve(path,{'payload':'frozen'})
    assert read_artifact(path)=={'payload':'frozen'}


def test_runner_repair_cannot_grant_another_calibration_call(tmp_path):
    reserve_call(tmp_path,{'provider':'gpt','payload_sha256':'same','runner_sha256':'old'})
    with pytest.raises(FileExistsError):
        reserve_call(tmp_path,{'provider':'gpt','payload_sha256':'same','runner_sha256':'new'})
    assert len(list(tmp_path.glob('*.json')))==1


def test_combined_holdout_coverage_and_score_do_not_approve_production():
    dataset=read_artifact(FIXTURES/'cvp_contextual_semantics_v2_cases.json')
    dataset['cases'].extend(read_artifact(FIXTURES/'cvp_contextual_semantics_holdout_v1.json')['cases'])
    blind,_=blind_dataset(dataset)
    rows=[{'id':r['id'],'verdict':r['expected'],'quote':r['source'],'reason':'仅测试评分器'} for r in blind['cases']]
    assert len(rows)==21
    validated=Answers.model_validate({'answers':rows}).model_dump()['answers']
    result=score(blind,validated)
    assert result['passed'] and result['production_approval'] is False
