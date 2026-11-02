import pytest
from pptx import Presentation
from pptx.util import Inches
from pydantic import ValidationError

from backend.api.models import SundayServiceEntry
from backend.api.ppt_generator import generate_presentation_from_template
from backend.sunday_assistant.models import ServiceChange
from backend.sunday_video import youtube_url


@pytest.mark.parametrize('url',[
    'https://youtu.be/5IIV-vdSq7c?t=30',
    'https://www.youtube.com/watch?v=5IIV-vdSq7c&start=30',
    'https://youtube.com/shorts/5IIV-vdSq7c',
])
def test_video_url_preserves_exact_source_and_roundtrips(url):
    row=SundayServiceEntry(date='2026-10-04',responseHymn='合神心意',responseHymnYoutubeUrl=url)
    assert SundayServiceEntry.model_validate_json(row.model_dump_json(by_alias=True)).response_hymn_youtube_url==url
    assert youtube_url(url)==url


@pytest.mark.parametrize('url',[
    'https://youtube.com.evil.test/watch?v=5IIV-vdSq7c',
    'javascript:alert(1)', 'http://youtu.be/5IIV-vdSq7c',
    'https://youtu.be/short', 'https://user@youtu.be/5IIV-vdSq7c',
    'https://www.youtube.com/playlist?list=abc',
])
def test_invalid_or_non_video_urls_rejected(url):
    with pytest.raises(ValidationError):
        ServiceChange(date='2026-10-04',field='responseHymnYoutubeUrl',value=url,evidence=url)


def test_ppt_video_slide_has_clickable_link_and_no_lyrics(tmp_path):
    url='https://youtu.be/5IIV-vdSq7c?t=30'
    deck=Presentation()
    slide=deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1),Inches(1),Inches(8),Inches(1)).text='{hymn2Title}'
    slide.shapes.add_textbox(Inches(1),Inches(2),Inches(8),Inches(4)).text='{responseHymnLyrics}'
    template=tmp_path/'template.pptx';deck.save(template)
    output=tmp_path/'output.pptx'
    generate_presentation_from_template(template,{'hymn2Title':'合神心意'},output,
        section_configs={'responseHymnLyrics':{'style':'lyrics','sections':[{'lines':['播放 YouTube 影片'],'video_url':url}]}})
    generated=Presentation(output)
    assert len(generated.slides)==1
    text='\n'.join(shape.text for shape in generated.slides[0].shapes if shape.has_text_frame)
    assert text=='合神心意\n播放 YouTube 影片'
    links=[shape.click_action.hyperlink.address for shape in generated.slides[0].shapes
           if shape.click_action.hyperlink.address]
    assert links==[url]
    assert url in generated.slides[0].notes_slide.notes_text_frame.text


def test_response_video_uses_the_same_configured_group_as_response_hymn():
    from backend.sunday_assistant.models import Settings
    config=Settings(availability_group='111@g.us',service_group='222@g.us',songs_group='333@g.us',
                    response_hymn_group='222@g.us',collect_cross_group=False)
    assert config.field_destinations()['responseHymnYoutubeUrl']=='222@g.us'
    assert 'responseHymnYoutubeUrl' in config.fields_for_group('222@g.us')
