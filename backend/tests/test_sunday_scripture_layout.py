from pptx import Presentation
from pptx.util import Inches,Pt
from backend.sunday_scripture_layout import paginate,verse_line
from backend import ppt_text_layout as layout


def frame():
    deck=Presentation();slide=deck.slides.add_slide(deck.slide_layouts[6])
    shape=slide.shapes.add_textbox(Inches(.65),Inches(1.55),Inches(12.03),Inches(5.1))
    shape.text_frame.text='{scriptureVerses}'
    return shape.text_frame


def test_actual_density_controls_pagination_and_keeps_complete_order():
    short=[dict(chapter=1,verse=n,text='經文') for n in range(1,19)]
    long=[dict(v,text='這是完整經文，閱讀需要舒適行距。'*4) for v in short]
    tf=frame();a=paginate(short,tf);b=paginate(long,tf)
    assert len(b)>len(a)
    assert [v for page in b for v in page]==long
    assert all(layout.fits(tf,[verse_line(v) for v in page],layout.template_font(tf),30,scripture=True) for page in b)


def test_scripture_spacing_serializes_without_affecting_song_spacing():
    from backend.api.ppt_generator import _populate_scripture_text_frame,_populate_lyrics_text_frame
    tf=frame();_populate_scripture_text_frame(tf,['1:1 第一節','1:2 第二節'])
    for p in tf.paragraphs:
        assert p.line_spacing==Pt(30*1.28)
        assert p.space_after==Pt(6)
    tf=frame();_populate_lyrics_text_frame(tf,['第一行','第二行'])
    assert tf.paragraphs[0].line_spacing==Pt(30*1.5)


def test_handover_only_on_last_page_of_reader(tmp_path):
    from backend.api.ppt_generator import generate_presentation_from_template
    deck=Presentation();sl=deck.slides.add_slide(deck.slide_layouts[6])
    sl.shapes.add_textbox(Inches(.65),Inches(1.55),Inches(12.03),Inches(5.1)).text='{scriptureVerses}'
    sl.shapes.add_textbox(Inches(8.7),Inches(6.85),Inches(4),Inches(.3)).text='{reader}'
    template=tmp_path/'template.pptx';out=tmp_path/'output.pptx';deck.save(template)
    sections=[{'lines':['1:1 經文'],'reader':name} for name in ['甲','甲','刘纳','刘纳','丙','丙']]
    generate_presentation_from_template(template,{},out,section_configs={'scriptureVerses':{'sections':sections,'style':'scripture'}})
    cues=[s.shapes[1].text for s in Presentation(out).slides]
    assert cues==['','⇉讀經 by 劉納','','⇉讀經 by 丙','','']


def test_cached_chinese_scripture_avoids_network_and_keeps_chinese_label(tmp_path,monkeypatch):
    import json
    from backend.api import service
    chapter=tmp_path/'1KI'/'19.json';chapter.parent.mkdir()
    chapter.write_text(json.dumps({'1':'第一節經文','2':'第二節經文'},ensure_ascii=False))
    monkeypatch.setattr('backend.bible_study.paths.cuv_dir',lambda:tmp_path)
    monkeypatch.setattr(service,'BIBLE_API_TRANSLATION_ZH','cuv')
    def no_network(*args,**kwargs):raise AssertionError('local scripture must not fetch network')
    monkeypatch.setattr(service.httpx,'get',no_network)
    label,verses,book=service._fetch_single_scripture('1ki-19-1-2')
    assert label==book+' 19:1-2' and book=='列王記上'
    assert [v['text'] for v in verses]==['第一節經文','第二節經文']
