import numpy as np
import pytest

from backend.pipeline.projected_slide_region import rectify, validate_quad
from backend.pipeline.slide_change_pilot import file_sha, object_sha, read_json, save_json
from backend.pipeline.source_projection import project_script, validate_visual_source_attestations, VisualSourceAttestationError
from backend.pipeline.video_transcript_handoff import export_handoff
from backend.tests.test_slide_visual_evidence import content


def test_projection_proposals_require_ordered_finite_screen_corners():
    assert validate_quad(None) is None
    points=[[100,100],[900,110],[900,900],[100,900]]
    assert validate_quad(sum(points,[])) == points
    for bad in ([[0,0]]*4, [[0,0],[1001,0],[1000,1000],[0,1000]], list(reversed(points))):
        with pytest.raises(ValueError): validate_quad(bad)
    image=np.zeros((100,100,3),dtype=np.uint8); image[10:90,10:90]=127
    warped=rectify(image,[[10,10],[89,10],[89,89],[10,89]])
    assert warped.shape == (79,79,3) and np.all(warped==127)


def fixture(tmp_path):
    image=tmp_path/'original.png';image.write_bytes(b'fixture pixels')
    c=content(False);digest=file_sha(image);c['asset_id']='a'+digest
    asset={'asset_id':'a'+digest,'original_image':str(image),'image_sha256':digest,
           'source_video_sha256':'b'*64,
           'content_sha256':object_sha(c),'content':c,'image_width':800,'image_height':600,
           'review_status':'unreviewed_model_candidate'}
    event={'id':'e0','paragraph_id':'p0','slide_sha256':digest,'time_ms':1000,'capture_ms':1500}
    record={'lecture_id':'測試講道','source_sha256':'b'*64,'visual_assets':[asset],
            'paragraphs':[{'id':'p0','text':'教授的口述。','start_ms':500,'end_ms':5000}],
            'slides':[event,{**event,'id':'e1','paragraph_id':None,'time_ms':6000}],
            'visibility':{'spans':[{'start_ms':7000,'end_ms':9000,'state':'no_slide','image':None}]}}
    save_json(tmp_path/'full_transcript.json',record)
    return record


def test_existing_source_projection_consumes_text_only_visuals_without_promoting_them_to_speech(tmp_path):
    original=fixture(tmp_path)
    result=export_handoff(tmp_path)
    payload=read_json(tmp_path/'handoff/測試講道.json')
    projection=project_script(payload['script'])
    assert [r['text'].strip() for r in projection.spoken_rows] == ['教授的口述。','']
    assert len(projection.visual_blocks)==2
    assert result['visibility']==original['visibility']
    assert result['visual_source_attestations']==[] and result['store_writes']==0
    assert result['visual_bindings'][0]['uncertain_region_ids']==['r2']
    assert all(block.readable for block in projection.visual_blocks)
    from backend.pipeline.detailed_knowledge_extraction_runner import _section_prompt_body,section_sentences
    from backend.pipeline.extraction_sections import Section
    section=Section(index=1,start=0,end=2,title='')
    sentences=section_sentences(payload,section)
    prompt=_section_prompt_body(payload,section,sentences)
    assert '甲 ζηλοῦτε' in prompt and '教授的口述。' in prompt
    assert sum(s.source_modality=='visual' for s in sentences)==2
    assert not any(f['tag']=='line' for block in projection.visual_blocks for f in block.facts)
    with pytest.raises(VisualSourceAttestationError,match='not attested'):
        validate_visual_source_attestations(projection, result['visual_source_attestations'])
    # Independent admission supplies its own exact version decision. The bridge
    # itself never generates this approval or writes a source to the repository.
    validate_visual_source_attestations(projection,{b.locator:b.raw_sha256 for b in projection.visual_blocks})
    image=tmp_path/'original.png';image.write_bytes(b'changed pixels')
    with pytest.raises(ValueError,match='changed'): export_handoff(tmp_path)
