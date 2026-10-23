"""Subtitles are metadata, not source (owner, 2026-10-23).

#436 inserted subtitle rows into the 2017 NYSC 4 and S 200524 transcripts after
they were registered. Body, spoken text and coordinates were unchanged, yet
load_authority compared whole-file SHAs and refused every Claim action on
both sermons. It now accepts a moved file only through #436's proof chain.
"""
import copy
import json
from pathlib import Path

import pytest

from backend.pipeline import claim_management as cm
from backend.pipeline import source_editorial_transition as transition
from backend.pipeline.source_projection import project_script
from backend.pipeline.source_reconciliation_runner import binding
from backend.tests.test_claim_management import policy


def encode(value):
    return json.dumps(value, ensure_ascii=False).encode()


@pytest.fixture
def sermon(tmp_path, monkeypatch):
    original = {'metadata': {'title': '人子'}, 'script': [
        {'index': 1, 'text': '你看耶穌每次講人子都加上一個定冠詞。', 'type': 'content'},
        {'index': 2, 'text': '耶穌從來沒有說a son of man。'}]}
    path = tmp_path / 'sermon.json'
    path.write_bytes(encode(original))
    document = {'source_id': 'S', 'source_type': 'sermon_transcript', 'locator_space': 'spoken_body_v1',
                'source_path': str(path), 'source_file_sha256': transition.file_binding(path)['file_sha256'],
                'source_body_sha256': project_script(original['script']).body_sha256}
    monkeypatch.setattr(transition, 'transition_root', lambda root=None: tmp_path / 'proofs')
    return original, path, document, tmp_path


def with_subtitle(original):
    after = copy.deepcopy(original)
    after['script'].insert(0, {'index': 'subtitle-t1', 'type': 'subtitle', 'text': '## 那個人子', 'user_id': 'editor'})
    return after


def insert_subtitle(original, path, document, *, receipt=True):
    after = with_subtitle(original)
    if receipt:
        transition.publish_transition(source=document, before_raw=path.read_bytes(), after_raw=encode(after),
                                      authorization='User authorized source headings for #436')
    path.write_bytes(encode(after))
    return after


def test_subtitle_insertion_with_its_proof_is_the_same_source(sermon):
    original, path, document, _ = sermon
    insert_subtitle(original, path, document)
    assert cm.subtitle_only_generation(document, transition.file_binding(path))


def test_moved_file_without_a_proof_is_still_drift(sermon):
    original, path, document, _ = sermon
    insert_subtitle(original, path, document, receipt=False)
    assert not cm.subtitle_only_generation(document, transition.file_binding(path))


def test_spoken_text_edited_after_the_proof_is_still_drift(sermon):
    original, path, document, _ = sermon
    after = insert_subtitle(original, path, document)
    after['script'][1]['text'] = '你看耶穌有時講人子加上一個定冠詞。'
    path.write_bytes(encode(after))
    assert not cm.subtitle_only_generation(document, transition.file_binding(path))


def test_a_spoken_text_edit_cannot_obtain_a_proof(sermon):
    original, path, document, _ = sermon
    after = with_subtitle(original)
    after['script'][1]['text'] += '改了'
    from backend.pipeline.sermon_subtitle_persistence import SubtitleBodyMutationError
    with pytest.raises(SubtitleBodyMutationError):
        transition.publish_transition(source=document, before_raw=path.read_bytes(), after_raw=encode(after),
                                      authorization='User authorized source headings for #436')


def review_request(tmp_path, path):
    policy_file = tmp_path / 'policy.json'
    policy_file.write_text(policy().model_dump_json())
    auth = tmp_path / 'direction.json'
    request = cm.Request(action_id='subtitle-review', operation='Review', allowed_operations=['Review'],
        target_keys=['claims/C'], source_bindings={'S': binding(path)}, policy=binding(policy_file),
        authorization={'path': str(auth), 'file_sha256': 'a' * 64})
    auth.write_text(json.dumps({'schema_version': cm.VERSION, 'authorized_by': 'owner', 'authorization_quote': 'test',
        'human_content_approval': False, 'request': request.model_dump(mode='json', exclude={'authorization', 'policy'}),
        'policy': request.policy.model_dump()}))
    request.authorization = cm.source.FileBinding.model_validate(binding(auth))
    return request


@pytest.mark.parametrize('receipt', [True, False])
def test_load_authority_admits_only_the_proved_subtitle_generation(sermon, receipt):
    original, path, document, tmp_path = sermon
    insert_subtitle(original, path, document, receipt=receipt)
    request = review_request(tmp_path, path)
    if receipt:
        cm.load_authority(request, source_documents={'S': document})
    else:
        with pytest.raises(ValueError, match='registered source physical version drift'):
            cm.load_authority(request, source_documents={'S': document})
