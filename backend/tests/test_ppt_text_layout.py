from pathlib import Path

import pytest
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_AUTO_SIZE
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches, Pt

from backend import ppt_text_layout as layout
from backend.api.ppt_generator import _populate_lyrics_text_frame, _populate_scripture_text_frame


@pytest.fixture(autouse=True)
def deterministic_metrics(monkeypatch):
    # Unit tests exercise the conservative no-installed-font path. Actual Office
    # DengXian metrics and render are verified separately against the real deck.
    monkeypatch.setattr(layout, '_font', lambda family,size: None)


def frame(width=9,height=4.95,family='DengXian'):
    deck=Presentation();slide=deck.slides.add_slide(deck.slide_layouts[6])
    shape=slide.shapes.add_textbox(Inches(.5),Inches(1.75),Inches(width),Inches(height))
    tf=shape.text_frame;tf.text='{placeholder}'
    run=tf.paragraphs[0].runs[0];run.font.name='Calibri';run.font.size=Pt(44)
    east=OxmlElement('a:ea');east.set('typeface',family);run._r.get_or_add_rPr().append(east)
    return deck,shape


def body_size(tf):
    return tf.paragraphs[0].runs[-1].font.size.pt


@pytest.mark.parametrize('render,lines',[
    (_populate_lyrics_text_frame,['第一行詩歌','第二行詩歌','第三行詩歌']),
    (_populate_scripture_text_frame,['1:1 第一節的經文','1:2 第二節的經文'])])
def test_large_readable_type_is_explicit_capped_and_template_chinese_font_is_preserved(render,lines,tmp_path):
    deck,shape=frame(family='模板中文字型')
    old_geometry=(shape.left,shape.top,shape.width,shape.height)
    render(shape.text_frame,lines)
    assert body_size(shape.text_frame)==30
    assert shape.text_frame.auto_size==MSO_AUTO_SIZE.NONE
    assert shape.text_frame.word_wrap==(render is _populate_scripture_text_frame)
    assert (shape.left,shape.top,shape.width,shape.height)==old_geometry
    for paragraph in shape.text_frame.paragraphs:
        assert paragraph.font.size.pt==30
        for run in paragraph.runs:
            assert run.font.size.pt<=30 and run.font.name=='模板中文字型'
            assert run._r.xpath('./a:rPr/a:ea')[0].get('typeface')=='模板中文字型'
    path=tmp_path/'verified.pptx';deck.save(path)
    reopened=Presentation(path).slides[0].shapes[0].text_frame
    assert body_size(reopened)==30 and reopened.text==shape.text_frame.text


def test_dense_scripture_shrinks_and_retains_every_verse_and_alternating_colors():
    deck,shape=frame()
    lines=[f'1:{n} '+('這是完整經文，應保留原文。'*3) for n in range(1,8)]
    _populate_scripture_text_frame(shape.text_frame,lines)
    size=body_size(shape.text_frame)
    assert 18<=size<30
    assert [p.text for p in shape.text_frame.paragraphs]==[line.removeprefix('1:') for line in lines]
    for index,paragraph in enumerate(shape.text_frame.paragraphs):
        number,body=paragraph.runs
        assert number._r.get_or_add_rPr().get('baseline')=='30000'
        assert number.font.size.pt==size
        assert body.font.size.pt==size
        assert body.font.color.rgb==(RGBColor(255,255,255) if index%2==0 else RGBColor(255,255,0))


def test_dense_lyrics_fit_without_changing_lines_or_inserting_a_slide():
    deck,shape=frame(width=5,height=3)
    lines=['詩歌的完整文字必須全部保留。']*6
    _populate_lyrics_text_frame(shape.text_frame,lines)
    assert 18<=body_size(shape.text_frame)<30
    assert [p.text for p in shape.text_frame.paragraphs]==lines
    assert len(deck.slides)==1


def test_impossible_content_reports_overflow_before_erasing_template():
    deck,shape=frame(width=1,height=.2)
    original=shape.text_frame.text
    with pytest.raises(layout.PptTextOverflowError,match='空行分頁'):
        _populate_lyrics_text_frame(shape.text_frame,['非常長的歌詞'*100])
    assert shape.text_frame.text==original


def test_margins_and_full_english_lyric_lines_are_included_in_fit():
    deck,wide=frame(width=5,height=3)
    deck,narrow=frame(width=5,height=3)
    narrow.text_frame.margin_left=Inches(1)
    narrow.text_frame.margin_right=Inches(1)
    lines=['Grace peace']*3
    _populate_lyrics_text_frame(wide.text_frame,lines)
    _populate_lyrics_text_frame(narrow.text_frame,lines)
    assert body_size(narrow.text_frame)<body_size(wide.text_frame)


def test_stale_template_paragraph_font_and_end_style_cannot_enlarge_first_line():
    deck,shape=frame()
    p=shape.text_frame.paragraphs[0];p.font.size=Pt(70)
    ending=OxmlElement('a:endParaRPr');ending.set('sz','9900');p._p.append(ending)
    _populate_scripture_text_frame(shape.text_frame,['1:1 第一節','1:2 第二節'])
    assert all(p.font.size.pt==30 for p in shape.text_frame.paragraphs)
    assert not shape.text_frame.paragraphs[0]._p.xpath('./a:endParaRPr')


def test_long_lyric_line_shrinks_instead_of_automatically_wrapping():
    deck,shape=frame(width=6.5,height=4.95)
    lines=['一行較長的歌詞需要縮小字號保持完整顯示。', '第二行']
    _populate_lyrics_text_frame(shape.text_frame,lines)
    assert shape.text_frame.word_wrap is False
    assert body_size(shape.text_frame)<30
    assert [p.text for p in shape.text_frame.paragraphs]==lines
    assert all(len(p.runs)==1 for p in shape.text_frame.paragraphs)


def test_table_cell_and_merged_cell_keep_existing_renderer_support():
    deck=Presentation();slide=deck.slides.add_slide(deck.slide_layouts[6])
    table=slide.shapes.add_table(2,2,0,0,Inches(8),Inches(4)).table
    cell=table.cell(0,0)
    _populate_lyrics_text_frame(cell.text_frame,['第一行','第二行'])
    assert body_size(cell.text_frame)==30
    table.cell(1,0).merge(table.cell(1,1))
    merged=table.cell(1,0)
    _populate_scripture_text_frame(merged.text_frame,['1:1 經文內容'])
    assert layout.frame_extent(merged.text_frame)==(Inches(8),Inches(2))
    assert body_size(merged.text_frame)==30
