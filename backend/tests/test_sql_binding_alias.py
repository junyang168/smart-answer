"""A staging directory alias preserves bound bytes, never content or authority changes."""
import hashlib
import pytest
from backend.pipeline.cvp_sql_commit import binding,check_binding


def test_absolute_directory_alias_keeps_original_literal_binding(tmp_path):
    actual=tmp_path/'actual';actual.mkdir();file=actual/'approval.json';file.write_bytes(b'{"approved":true}\n')
    alias=tmp_path/'staging';alias.symlink_to(actual,target_is_directory=True)
    saved={'path':str(alias/'approval.json'),'file_sha256':hashlib.sha256(file.read_bytes()).hexdigest()}
    assert binding(saved['path'])['path'] != saved['path']
    check_binding(saved)
    assert saved['path']==str(alias/'approval.json')
    file.write_bytes(b'{"approved":false}\n')
    with pytest.raises(ValueError,match='binding drift'):check_binding(saved)


@pytest.mark.parametrize('defect',['hash','relative','extra'])
def test_binding_does_not_relax_hash_absolute_path_or_shape(tmp_path,monkeypatch,defect):
    file=tmp_path/'input.json';file.write_text('{}');saved=binding(file)
    if defect=='hash':saved['file_sha256']='0'*64
    elif defect=='relative':monkeypatch.chdir(tmp_path);saved['path']='input.json'
    else:saved['approval']=True
    with pytest.raises(ValueError,match='binding drift'):check_binding(saved)
