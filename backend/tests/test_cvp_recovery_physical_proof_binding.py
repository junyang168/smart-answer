import pytest
from backend.pipeline import viewpoint_cvp_recovery_executor as m


def test_markdown_proof_is_checked_byte_for_byte_not_parsed_as_json(tmp_path):
    path=tmp_path/'repair-record.md'; path.write_text('# Real immutable provenance\n')
    b=m.binding(path)
    assert m.verify_binding(b,parse_json=False) is None
    path.write_text('# Altered\n')
    with pytest.raises(ValueError,match='SHA drift'): m.verify_binding(b,parse_json=False)


def test_required_json_consumers_remain_strict(tmp_path):
    path=tmp_path/'audit.json'; path.write_text('not JSON')
    with pytest.raises(ValueError): m.verify_binding(m.binding(path))
    path.write_text('{"actual": "audit"}')
    assert m.verify_binding(m.binding(path))=={'actual':'audit'}


def test_distinct_repair_roots_with_same_handoff_name_do_not_collide(tmp_path):
    root=tmp_path/'campaign'
    a=tmp_path/'repair-v1'/'claim-repair-proof-resume'
    b=tmp_path/'repair-v2'/'claim-repair-proof-resume'
    assert m.continuation_root(root,a)!=m.continuation_root(root,b)
    assert m.continuation_root(root,a)==m.continuation_root(root,a)
