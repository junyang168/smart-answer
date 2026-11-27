import pytest
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt
from backend.api.ppt_generator import generate_presentation_from_template
from backend.ppt_text_layout import PptTextOverflowError


def template(tmp_path):
    deck=Presentation();deck.slide_width=Inches(13.333);deck.slide_height=Inches(7.5)
    slide=deck.slides.add_slide(deck.slide_layouts[6])
    for text,top,height in [('歡迎來賓\n{announcements}',1.2,1.6),
                            ('身體健康代禱：{healthPrayer}',3,.4),
                            ('事工支持奉獻：\n支持資料\n郵寄資料',3.55,.8)]:
        shape=slide.shapes.add_textbox(Inches(.65),Inches(top),Inches(12.03),Inches(height))
        shape.text=text
        for p in shape.text_frame.paragraphs:
            p.space_after=Pt(6)
            for r in p.runs:
                r.font.size=Pt(18);r.font.name='DengXian';r.font.bold=True
                r.font.color.rgb=RGBColor(255,255,0)
    slide.shapes.add_table(3,2, Inches(.65),Inches(4.6),Inches(12.03),Inches(2.48))
    path=tmp_path/'template.pptx';deck.save(path)
    return path


@pytest.mark.parametrize('report',['一行報告','較長家事報告。'*18,'第一項報告\n第二項報告\n第三項報告'])
def test_blocks_follow_rendered_content_without_gaps_or_overlap(tmp_path,report):
    path=generate_presentation_from_template(template(tmp_path),{'announcements':report,'healthPrayer':'代禱名單、'*9},tmp_path/'result.pptx')
    shapes=list(Presentation(path).slides[0].shapes)
    for first,second in zip(shapes[:2],shapes[1:3]):
        assert second.top-first.top-first.height==Pt(8)
    assert shapes[2].top+shapes[2].height+Pt(8)<=shapes[3].top
    assert shapes[3].top==Inches(4.6)
    assert report in shapes[0].text
    assert '代禱名單、'*9 in shapes[1].text
    for shape in shapes[:3]:
        for p in shape.text_frame.paragraphs:
            for r in p.runs:
                assert r.font.size==Pt(18) and r.font.name=='DengXian'
                assert r.font.bold and r.font.color.rgb==RGBColor(255,255,0)


def test_long_reports_do_not_silently_overlap_schedule_or_replace_existing_file(tmp_path):
    output=tmp_path/'existing.pptx';output.write_bytes(b'previously generated file')
    with pytest.raises(PptTextOverflowError,match='排班表'):
        generate_presentation_from_template(template(tmp_path),{'announcements':'長報告\n'*40,'healthPrayer':'代禱'},output)
    assert output.read_bytes()==b'previously generated file'
