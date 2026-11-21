from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR
from pptx.util import Inches, Pt

from backend.api.ppt_generator import _balance_static_slides


def test_prayer_preserves_words_and_styles_with_separate_heading_and_footer(tmp_path):
    deck = Presentation()
    deck.slide_width, deck.slide_height = Inches(13.333), Inches(7.5)
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    body = slide.shapes.add_textbox(Inches(1.62), Inches(.3), Inches(10.093), Inches(6.43))
    lines = ['主禱文', '', '我們在天上的父、', '願人都尊你的名為聖。願你的國降臨。',
             '願你的旨意行在地上、如同行在天上。', '我們日用的飲食、今日賜給我們。',
             '免我們的債、如同我們免了人的債。', '不叫我們遇見試探．救我們脫離那惡者。',
             '因為國度、權柄、榮耀、全是你的直到永遠、阿們。']
    body.text_frame.text = '\n'.join(lines)
    for index, paragraph in enumerate(body.text_frame.paragraphs):
        for run in paragraph.runs:
            run.font.name = 'DengXian'
            run.font.size = Pt(30)
            run.font.color.rgb = RGBColor(255, 255, 0 if index % 2 else 255)
    before = [(r.text, r.font.name, r.font.size, str(r.font.color.rgb))
              for p in body.text_frame.paragraphs[2:] for r in p.runs]
    footer = slide.shapes.add_textbox(Inches(.65), Inches(6.85), Inches(7.4), Inches(.3))
    footer.text = '請先設定【靜音】，再一起默禱。'
    footer_xml = footer._element.xml
    _balance_static_slides(deck)
    _balance_static_slides(deck)
    path = tmp_path / 'prayer.pptx'
    deck.save(path)
    reopened = Presentation(path)
    shapes = reopened.slides[0].shapes
    body, footer, heading = shapes
    assert heading.text == lines[0]
    assert body.text == '\n'.join(lines[2:])
    assert [(r.text, r.font.name, r.font.size, str(r.font.color.rgb))
            for p in body.text_frame.paragraphs for r in p.runs] == before
    assert footer._element.xml == footer_xml
    assert heading.top + heading.height < body.top
    assert body.top + body.height < footer.top
    assert body.text_frame.vertical_anchor == MSO_ANCHOR.MIDDLE
    # Seven 30pt lines, with breathing room, fit without squeezing the footer.
    assert all(p.line_spacing.pt >= 40 for p in body.text_frame.paragraphs)
    assert sum(p.line_spacing for p in body.text_frame.paragraphs) < body.height


def test_sermon_cover_centers_title_and_speaker_without_changing_runs(tmp_path):
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    body = slide.shapes.add_textbox(Inches(.85), Inches(1.8), Inches(11.63), Inches(4.95))
    body.text = '{sermonTitle}\n\n\n{sermonSpeaker}'
    before = [p._p.xml for p in body.text_frame.paragraphs]
    geometry = (body.left, body.top, body.width, body.height)
    _balance_static_slides(deck)
    path = tmp_path / 'sermon.pptx'
    deck.save(path)
    body = Presentation(path).slides[0].shapes[0]
    assert [p._p.xml for p in body.text_frame.paragraphs] == before
    assert (body.left, body.top, body.width, body.height) == geometry
    assert body.text_frame.vertical_anchor == MSO_ANCHOR.MIDDLE
