import pytest
from backend.pipeline.exegesis_passage_location_runner import validate, normalize_fields


def packet():
    return {"claims": [{"claim_id": "a", "source_id": "s"}], "source": {"source_id": "s", "paragraphs": [
        {"paragraph_key": "S0001", "text": "馬太福音十六章，這裏說鑰匙。"}]}}


def answer():
    return {"decisions": [{"claim_id": "a", "status": "resolved", "primary": "Matt.16",
                           "evidence": [{"paragraph_key": "S0001", "quote": "馬太福音十六章", "purpose": "章級定位"}],
                           "secondary": [], "reason": "原文只證明章級", "missing": ""}]}


def test_chapter_level_with_verbatim_evidence():
    validate(answer(), packet())


def test_paraphrase_rejected():
    value = answer()
    value["decisions"][0]["evidence"][0]["quote"] = "馬太福音16:19"
    with pytest.raises(ValueError, match="non-verbatim"):
        validate(value, packet())


@pytest.mark.parametrize("ids", [[], ["a", "a"], ["foreign"]])
def test_exact_once_required(ids):
    template = answer()["decisions"][0]
    with pytest.raises(ValueError, match="missing/duplicate/foreign"):
        validate({"decisions": [template | {"claim_id": cid} for cid in ids]}, packet())


def test_unresolved_must_disclose_missing_basis():
    value = answer()
    value["decisions"][0].update(status="unresolved", primary="")
    with pytest.raises(ValueError, match="unresolved"):
        validate(value, packet())
    value["decisions"][0]["missing"] = "缺少能區分兩個經文主對象的原文銜接"
    validate(value, packet())


def test_source_keys_cannot_cross_claim_sources():
    value = packet()
    value["sources"] = [value.pop("source"), {"source_id": "other", "paragraphs": [
        {"paragraph_key": "S0001", "text": "只能在別的來源找到的話"}]}]
    response = answer()
    response["decisions"][0]["evidence"][0]["quote"] = "只能在別的來源找到的話"
    with pytest.raises(ValueError, match="non-verbatim"):
        validate(response, value)


def test_swapped_field_names_preserve_text_and_original():
    value = answer()
    value["decisions"][0]["secondary"] = [{"reference": "John.13", "role": "平行敘述", "relation": "parallel"}]
    normalized, changes = normalize_fields(value)
    assert value["decisions"][0]["secondary"][0]["role"] == "平行敘述"
    assert normalized["decisions"][0]["secondary"][0] == {"reference": "John.13", "role": "parallel", "relation": "平行敘述"}
    assert len(changes) == 1
    validate(normalized, packet())
