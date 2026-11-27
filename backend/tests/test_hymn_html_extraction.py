"""The hymn site's Word-export layout tables must retain visual lyric lines."""
import pytest
from bs4 import BeautifulSoup
import requests

from backend.api.webpage_extractor import _element_to_markdown, _flatten_tables, fetch_lyrics_text


def extract(html):
    soup = BeautifulSoup(html, 'lxml')
    _flatten_tables(soup)
    return _element_to_markdown(soup.body or soup)


def lines(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


WORD_TABLE = '''<table>
<tr><td><p>1</p></td><td><p><span>第一行，</span><span><br>
</span><span>第二行，</span><span>祂<span>愛世人。</span></span><br>第三行。</p></td></tr>
<tr><td>&nbsp;</td><td>&nbsp;</td></tr>
<tr><td><p>2</p></td><td><p>第四行。<br>第五行。</p></td></tr>
</table>'''


def test_word_table_retains_br_and_stanzas_without_invented_span_spaces():
    text = extract(WORD_TABLE)
    assert lines(text) == ['1 第一行，', '第二行，祂愛世人。', '第三行。', '2 第四行。', '第五行。']
    assert '第三行。\n\n2' in text


def test_paragraphs_and_inline_word_boundaries_are_preserved():
    text = extract('<table><tr><td><p>Grace <span>and</span> peace.</p><p>第二段</p></td></tr></table>')
    assert lines(text) == ['Grace and peace.', '第二段']
    assert 'peace.\n\n第二段' in text


def test_nested_table_does_not_repeat_inner_rows():
    text = extract('<table><tr><td>歌名<table><tr><td>歌詞甲<br>歌詞乙</td></tr></table></td></tr></table>')
    assert lines(text) == ['歌名', '歌詞甲', '歌詞乙']


def test_fetch_pipeline_keeps_non_utf8_lyrics_and_line_breaks(monkeypatch):
    response = requests.Response()
    response.status_code = 200
    response.encoding = 'big5'
    response._content = ('<html><body><nav>導覽</nav><script>壞指令</script>' + WORD_TABLE + '</body></html>').encode('big5')
    session = requests.Session()
    monkeypatch.setattr(session, 'get', lambda *args, **kwargs: response)
    text = fetch_lyrics_text('https://fcccc.net/hymns/test.htm', session=session)
    assert lines(text) == lines(extract(WORD_TABLE))
    assert '導覽' not in text and '壞指令' not in text


def test_html_to_saved_lyrics_to_ppt_preserves_blank_slide_boundaries(tmp_path, monkeypatch):
    from backend.api.models import HymnMetadata
    from backend.sunday_assistant import hymn_import as h
    from backend.sunday_assistant.grounding import GroundingVerdict
    from backend.api.service import _split_lyrics_sections
    from backend.api.ppt_generator import generate_presentation_from_template
    from pptx import Presentation
    from pptx.util import Inches

    raw = extract(WORD_TABLE)
    source_lines = raw.splitlines()
    # The formatter must not override already-structured source lines/slides.
    monkeypatch.setattr(h, 'complete', lambda schema, *_: h.LyricLayout(
        start_line=0, end_line=len(source_lines)-1, lyrics='第一行，第二行，祂愛世人。第三行。第四行。第五行。')
        if schema == h.LyricLayout else GroundingVerdict(supported=True))
    saved = h.prepare_lyrics(HymnMetadata(index=17, title='測試歌'), raw)
    assert saved['layout_source'] == 'source_verses'
    assert saved['lyrics'] == '第一行，\n第二行，祂愛世人。\n第三行。\n\n第四行。\n第五行。'
    sections = _split_lyrics_sections(saved['lyrics'])
    assert list(map(len, sections)) == [3, 2]

    template = Presentation()
    slide = template.slides.add_slide(template.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(4)).text = '{hymnLyrics}'
    template_path = tmp_path / 'template.pptx'
    template.save(template_path)
    output = generate_presentation_from_template(template_path, {}, tmp_path / 'output.pptx',
        section_configs={'hymnLyrics': {'sections': sections, 'style': 'lyrics'}})
    actual = Presentation(output)
    assert len(actual.slides) == 2
    assert [[p.text for shape in slide.shapes if shape.has_text_frame for p in shape.text_frame.paragraphs]
            for slide in actual.slides] == sections


@pytest.mark.parametrize("separator", ["\n\n", "\r\n\r\n", "\n \t\n"])
def test_manual_blank_line_starts_slide_while_single_newline_does_not(separator):
    from backend.api.service import _split_lyrics_sections
    assert _split_lyrics_sections(separator.join(['第一行\n第二行', '下一張', '最後一張\n末行'])) == [
        ['第一行', '第二行'], ['下一張'], ['最後一張', '末行']]


def hymn_404_source():
    from pathlib import Path
    return (Path(__file__).parent / 'fixtures/hymns/404-word.html').read_text()


def test_word_paragraph_lines_do_not_manufacture_stanza_breaks():
    text = extract(hymn_404_source())
    from opencc import OpenCC
    body = OpenCC('s2t').convert(text[text.index('主領我'):])
    from backend.api.service import _split_lyrics_sections
    sections = _split_lyrics_sections(body)
    assert list(map(len, sections)) == [4, 8, 4]
    assert sections[0] == sections[2]
    assert sections[1][3] == '有主與我同行(有主與我同行)。'
    assert sections[1][-1] == '步隨主行(步隨主行)。'
    assert sections[0][-1] == '我是主的羊。'


def test_word_source_wrapping_keeps_english_word_spaces_and_explicit_empty_paragraph():
    text = extract('<table><tr><td>'
        '<p class="MsoNormal">Grace\n and <span>peace.</span><br><br>Second line</p>'
        '<p class="MsoNormal">Third line<br></p>'
        '<p class="MsoNormal">Fourth line</p>'
        '<p class="MsoNormal">&nbsp;</p>'
        '<p class="MsoNormal">Last line</p></td></tr></table>')
    assert lines(text) == ['Grace and peace.', 'Second line', 'Third line', 'Fourth line', 'Last line']
    from backend.api.service import _split_lyrics_sections
    assert _split_lyrics_sections(text) == [['Grace and peace.'], ['Second line', 'Third line', 'Fourth line'], ['Last line']]


def test_ordinary_html_paragraphs_keep_stanzas_even_when_br_is_present():
    from backend.api.service import _split_lyrics_sections
    text = extract('<table><tr><td><p>第一段<br>第二行<br><br>第三行</p><p>下一段<br>末行</p></td></tr></table>')
    assert _split_lyrics_sections(text) == [['第一段', '第二行'], ['第三行'], ['下一段', '末行']]


def test_hymn_404_import_ignores_model_repagination_and_round_trips_three_ppt_slides(tmp_path, monkeypatch):
    from backend.api.models import HymnMetadata
    from backend.sunday_assistant import hymn_import as h
    from backend.sunday_assistant.grounding import GroundingVerdict
    from backend.api.service import _split_lyrics_sections
    from backend.api.ppt_generator import generate_presentation_from_template
    from pptx import Presentation
    from pptx.util import Inches

    raw = extract(hymn_404_source())
    source_lines = raw.splitlines()
    start = next(i for i, line in enumerate(source_lines) if line.startswith('主領我'))
    monkeypatch.setattr(h, 'complete', lambda schema, *_: h.LyricLayout(
        start_line=start, end_line=len(source_lines)-1,
        lyrics='\n\n'.join(line.strip() for line in source_lines[start:] if line.strip()))
        if schema == h.LyricLayout else GroundingVerdict(supported=True))
    saved = h.prepare_lyrics(HymnMetadata(index=404, title='我是主羊'), raw)
    assert saved['layout_source'] == 'source_lines'
    assert h.normalize_lyric_words(saved['raw_source'][saved['raw_source'].index('主領我'):]) == h.normalize_lyric_words(saved['lyrics'])
    sections = _split_lyrics_sections(saved['lyrics'])
    assert list(map(len, sections)) == [4, 8, 4]
    template = Presentation()
    slide = template.slides.add_slide(template.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(4)).text = '{hymnLyrics}'
    path = tmp_path / 'template.pptx'
    template.save(path)
    output = generate_presentation_from_template(path, {}, tmp_path / 'output.pptx',
        section_configs={'hymnLyrics': {'sections': sections, 'style': 'lyrics'}})
    actual = Presentation(output)
    assert len(actual.slides) == 3
    assert [[p.text for shape in slide.shapes if shape.has_text_frame for p in shape.text_frame.paragraphs]
            for slide in actual.slides] == sections
