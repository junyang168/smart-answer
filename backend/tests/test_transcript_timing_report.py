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


def test_comparison_reports_new_playback_readiness_without_upgrading_old_strict_verdict(tmp_path):
    from backend.pipeline.slide_change_pilot import file_sha
    out=tmp_path/'out';evidence=out/'evidence'
    record=dict(source_sha256='source',paragraphs=[dict(id='w00-p0',text='保留原話',window_id='w00',start_ms=100,end_ms=1000)])
    save_json(out/'master.json',dict(windows=[dict(id='w00',start_ms=0,end_ms=2000)]))
    save_json(evidence/'plan.json',{})
    save_json(evidence/'assessment.json',dict(lectures={'source':dict(qualified=0,paragraphs=[dict(id='w00-p0',status='withheld')])}))
    save_json(evidence/'playback-readiness/plan.json',dict(cases=[]))
    playback=dict(policy='complete-paragraph-playback.v1',source_plan_sha256=file_sha(evidence/'plan.json'),ready=1,blocked=0,warnings=1,
                  paragraphs={'source':[dict(id='w00-p0',status='ready_with_context',strict_status='withheld',start_ms=100,end_ms=1000,reason='內容完整，保留少量前後文提醒')]})
    save_json(evidence/'playback-readiness/assessment.json',playback)
    path=write_report(out,record,evidence,playback=playback);r=json.loads(path.read_text())
    assert r['qualified']==1 and r['withheld']==0 and r['strict_qualified']==0
    assert r['paragraphs'][0]['strict_status']=='withheld' and r['paragraphs'][0]['status']=='ready_with_context'
    assert not r['human_approved'] and not r['editor_imported']
    page=path.with_name('report.html').read_text()
    assert '原嚴格邊界結果保留' in page and '可用・含少量上下文提醒' in page
    assert r['paragraphs'][0]['text']=='保留原話'
