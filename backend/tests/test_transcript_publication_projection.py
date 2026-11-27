from copy import deepcopy
import pytest
from backend.pipeline import transcript_publication_projection as publication
from backend.pipeline import transcript_evidence_apply as evidence
from backend.pipeline import transcript_claim_migration as m


def wrapper(sha='a' * 64, svg_sha=None):
    return (f'<figure data-transcript-slide="v1" data-source-image-sha256="{sha}">\n'
        '<img src="/api/slide.png" alt="投影片（非口述）" />\n'
        '<figcaption>投影片（非口述）</figcaption>\n'
        '<details><summary>投影片文字（機器重建，非口述）</summary>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" data-source-image-sha256="{svg_sha or sha}" width="100" height="100"><text x="1" y="20">OCR（非口述）</text></svg>\n'
        '</details>\n</figure>')


def test_exact_multiple_editorial_wrappers_preserve_raw_speech_clocks_and_file_binding(tmp_path):
    original = {'script': [{'index': '1', 'text': '\n教授原話~~刪除內容~~。', 'start_time': 3, 'end_time': 8}]}
    published = deepcopy(original)
    published['script'][0]['text'] = wrapper() + '\n\n' + wrapper('b' * 64) + '\n\n' + original['script'][0]['text']
    assert publication.spoken_document(published) == (original, True)
    before = tmp_path / 'source.json';after = tmp_path / 'published.json'
    m.immutable(before, original);m.immutable(after, published)
    a, b = evidence.snap(before), evidence.snap(after)
    assert {k: v for k, v in a.items() if k != 'file'} == {k: v for k, v in b.items() if k != 'file'}
    assert a['file']['file_sha256'] != b['file']['file_sha256']
    assert 'OCR' not in str(b['paragraphs']) and '刪除內容' not in str(b['paragraphs'])


@pytest.mark.parametrize('text', [wrapper(svg_sha='b' * 64) + '\n\n口述',
    wrapper().replace('data-transcript-slide="v1"', 'data-transcript-slide="v2"') + '\n\n口述',
    wrapper() + '\n口述'])
def test_unbound_or_unknown_publisher_format_rejected(text):
    with pytest.raises(ValueError):publication.spoken_document({'script': [{'index': '1', 'text': text}]})


def test_bare_svg_still_requires_visual_adapter(tmp_path):
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>不能當口述</text></svg>'
    for i, text in enumerate([svg + '口述', wrapper() + '\n\n' + svg + '口述']):
        path = tmp_path / f'{i}.json';m.immutable(path, {'script': [{'index': '1', 'text': text}]})
        with pytest.raises(ValueError, match='visual adapter'):evidence.snap(path)
