from backend.pipeline.matthew_exposition_provenance import normalize_provenance


def test_moves_key_value_provenance_before_block_and_converts_to_json():
    source = "段落。\n<!-- provenance: attribution=professor; claims=DK-A,DK-B -->\n"
    result = normalize_provenance(source)
    assert result.startswith(
        '<!-- provenance: {"attribution":"professor","claim_ids":["DK-A","DK-B"]} -->\n段落。'
    )


def test_mixed_attribution_becomes_hidden_editorial_synthesis():
    source = "材料之間有張力。\n<!-- provenance: attribution=professor,editor; claims=DK-A,DK-B -->"
    result = normalize_provenance(source)
    assert '"attribution":"editorial_synthesis"' in result
    assert '"synthesis_note":"跨來源材料的歸屬、張力或神學收束"' in result


def test_unlabelled_editor_intro_with_scripture_becomes_scripture():
    source = "本段從身分問題開始。\n<!-- provenance: attribution=editor; scripture=Matt.16.13-20 -->"
    result = normalize_provenance(source)
    assert '"attribution":"scripture"' in result
    assert '"scripture_refs":["Matt.16.13-20"]' in result
