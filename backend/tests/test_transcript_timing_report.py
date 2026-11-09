import json
from backend.pipeline.slide_change_pilot import save_json
from backend.pipeline.transcript_timing_report import write_report


def test_withheld_report_preserves_text_and_never_creates_editor_approval(tmp_path):
    out=tmp_path/'out';evidence=out/'evidence'
    record=dict(source_sha256='source',paragraphs=[dict(id='w00-p0',text='<script>來源原字</script>',window_id='w00')])
    save_json(out/'master.json',dict(windows=[dict(id='w00',start_ms=0,end_ms=10000)]))
    save_json(evidence/'assessment.json',dict(lectures={'s':dict(paragraphs=[dict(id='w00-p0',status='withheld')])}))
    path=write_report(out,record,evidence);result=json.loads(path.read_text())
    assert result['paragraphs'][0]['text']==record['paragraphs'][0]['text']
    assert result['qualified']==0 and result['withheld']==1
    assert result['model_calls']==0 and not result['editor_imported'] and not result['human_approved']
    page=path.with_name('report.html').read_text()
    assert '&lt;script&gt;來源原字&lt;/script&gt;' in page
    assert '<script>來源原字</script>' not in page
    assert not (out/'handoff').exists()
