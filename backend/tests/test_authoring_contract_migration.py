import json

import pytest

from backend.pipeline.authoring_contract_migration import (
    AuthoringContractMigrationError,
    build_migration_package,
    load_contract,
    merge_contract_into_plan,
    verify_contract,
)


MANUSCRIPT = """## 三、彌賽亞身分與性質的兩階段教導

太 16:21 以「從此」作為轉折，標誌著耶穌對門徒教導進入第二個階段。

彼得並非不認識耶穌是彌賽亞，他的問題在於他不認識彌賽亞的性質。
"""


def _contract(tmp_path, *, excerpt="太 16:21 以「從此」作為轉折", source_id="notes:16"):
    manuscript = tmp_path / "final.md"
    manuscript.write_text(MANUSCRIPT, encoding="utf-8")
    return {
        "contract_id": "BMC-test-v1",
        "schema_version": "matthew-exposition-base-contract.v1",
        "passage": "Matt.16.21-Matt.16.23",
        "authoring_mode": "verified_manuscript_integration",
        "composition_plan": {"plan_id": "CP-test", "sha256": "abc"},
        "base_source": {
            "source_id": "notes:16",
            "path": str(manuscript),
            "sha256": "abc",
            "section_anchor": "## 三、彌賽亞身分與性質的兩階段教導",
        },
        "sections": [
            {
                "section_id": "reader-sec-01",
                "reader_heading": "第一課與第二課",
                "decision_ids": ["CD-1"],
                "required_argument_steps": [
                    {
                        "step_id": "S01-A",
                        "statement": "說明『從此』是轉折標誌。",
                        "source_id": source_id,
                        "source_excerpt": excerpt,
                    }
                ],
                "allowed_operations": ["preserve"],
                "ineligible_operations": ["invent_word_study"],
            }
        ],
        "global_rules": ["母本是文章主論證"],
        "status": "editor_confirmed",
    }


def _plan():
    return {
        "plan_id": "CP-test",
        "product_type": "passage_lecture",
        "title": "太16 測試",
        "decision_ids": ["CD-1"],
    }


def test_verification_passes_when_every_excerpt_is_verbatim(tmp_path):
    check = verify_contract(_contract(tmp_path), tmp_path / "contract.json")
    assert check.ok
    assert (check.verified_steps, check.step_total) == (1, 1)


def test_verification_fails_when_excerpt_is_not_in_the_manuscript(tmp_path):
    contract = _contract(tmp_path, excerpt="教授從未說過這句話")
    check = verify_contract(contract, tmp_path / "contract.json")
    assert not check.ok
    assert "逐字子字串" in check.failures[0]


def test_verification_fails_when_step_cites_an_undeclared_source(tmp_path):
    contract = _contract(tmp_path, source_id="notes:unknown")
    check = verify_contract(contract, tmp_path / "contract.json")
    assert not check.ok
    assert "不在 base_source" in check.failures[0]


def test_migration_refuses_a_contract_naming_decisions_absent_from_the_plan(tmp_path):
    contract = _contract(tmp_path)
    contract["sections"][0]["decision_ids"] = ["CD-1", "CD-UNKNOWN"]
    with pytest.raises(AuthoringContractMigrationError, match="CD-UNKNOWN"):
        merge_contract_into_plan(
            _plan(), contract, confirmed_by="editor", confirmed_at="2026-08-17T00:00:00Z"
        )


def test_merge_carries_the_contract_and_records_who_confirmed_it(tmp_path):
    merged = merge_contract_into_plan(
        _plan(),
        _contract(tmp_path),
        confirmed_by="junyang168",
        confirmed_at="2026-08-17T00:00:00Z",
    )
    assert merged["contract_id"] == "BMC-test-v1"
    assert merged["contract_schema_version"] == "matthew-exposition-base-contract.v1"
    assert merged["passage"] == "Matt.16.21-Matt.16.23"
    assert merged["contract_confirmed_by"] == "junyang168"
    assert merged["contract_confirmed_at"] == "2026-08-17T00:00:00Z"
    step = merged["authoring_sections"][0]["required_argument_steps"][0]
    assert step["source_excerpt"] == "太 16:21 以「從此」作為轉折"
    assert merged["authoring_sections"][0]["ineligible_operations"] == ["invent_word_study"]


def test_merge_leaves_existing_plan_identity_and_decisions_untouched(tmp_path):
    plan = _plan()
    merged = merge_contract_into_plan(
        plan, _contract(tmp_path), confirmed_by="e", confirmed_at="t"
    )
    assert merged["plan_id"] == "CP-test"
    assert merged["decision_ids"] == ["CD-1"]
    assert plan == _plan(), "來源 payload 不應被就地修改"


def _decision():
    return {
        "decision_id": "CD-1",
        "plan_id": "CP-test",
        "decision_type": "main_section",
        "decision": "以『從此』連接身分認信與受苦使命。",
        "claim_ids": ["DK-1"],
        "revision": 1,
    }


def test_package_carries_existing_decisions_verbatim(tmp_path):
    """Regression: the importer recomputes decision_ids from `decisions`.

    An earlier version passed an empty list here to 'leave decisions alone',
    which erased decision_ids on every migrated plan.
    """
    package = build_migration_package(
        [(_plan(), [_decision()], _contract(tmp_path))],
        package_id="PKG-1",
        confirmed_by="e",
        confirmed_at="t",
    )
    entry = package["product_plans"][0]
    assert entry["decision_ids"] == ["CD-1"]
    assert entry["decisions"] == [_decision()], "既有 decision 必須原樣帶過去"


def test_package_refuses_when_carried_decisions_do_not_match_decision_ids(tmp_path):
    with pytest.raises(AuthoringContractMigrationError, match="CD-1"):
        build_migration_package(
            [(_plan(), [], _contract(tmp_path))],
            package_id="PKG-1",
            confirmed_by="e",
            confirmed_at="t",
        )


def test_load_contract_unwraps_the_generation_envelope(tmp_path):
    body = _contract(tmp_path)
    path = tmp_path / "enveloped.json"
    path.write_text(
        json.dumps({"schema_version": "x", "generation": {}, "result": body}),
        encoding="utf-8",
    )
    assert load_contract(path)["contract_id"] == "BMC-test-v1"


def test_load_contract_reads_a_bare_contract(tmp_path):
    path = tmp_path / "bare.json"
    path.write_text(json.dumps(_contract(tmp_path)), encoding="utf-8")
    assert load_contract(path)["contract_id"] == "BMC-test-v1"
