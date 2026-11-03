import pytest
from backend.pipeline.boundary_audio_review import validate_result


def test_actual_previous_tail_is_not_an_acceptable_boundary():
    cases=[{'case_id':'p10-start','candidates':[{'id':'old'},{'id':'fixed'}]}]
    answer={'results':[{'case_id':'p10-start','reference_issue':'none','candidates':[
        {'id':'old','verdict':'wrong_segment','heard_target_phrase':'魔鬼來作王','reason':'上一段尾句'},
        {'id':'fixed','verdict':'clean_boundary','heard_target_phrase':'所以從神的國度來看','reason':'本段開頭完整可聞'}]}]}
    validate_result(answer,cases)
    answer['results'][0]['candidates'][1]['heard_target_phrase']=''
    with pytest.raises(ValueError):validate_result(answer,cases)


def test_no_invented_or_reordered_boundary_candidates():
    cases=[{'case_id':'p10-start','candidates':[{'id':'expected'}]}]
    answer={'results':[{'case_id':'p10-start','reference_issue':'unclear','candidates':[{'id':'invented','verdict':'uncertain','heard_target_phrase':'','reason':'聽不清'}]}]}
    with pytest.raises(ValueError):validate_result(answer,cases)


def test_empty_positive_is_excluded_without_rewriting_or_repeating_saved_answer(tmp_path):
    import json
    from backend.pipeline.boundary_audio_review import control_case,batches_from_plan,saved_verdicts,PROMPT,SCHEMA
    from backend.pipeline.slide_change_pilot import save_json,file_sha,object_sha,read_json
    root=tmp_path;save_json(root/'plan.json',{'source':'fixed'})
    control=control_case(root)
    case={**control,'case_id':'actual-p10','lecture':'1','paragraph_id':'p10'}
    plan={'parent_plan_sha256':file_sha(root/'plan.json'),'cases':[control,case]};save_json(root/'boundary-audio-plan.json',plan)
    for index,cases in enumerate(batches_from_plan(plan)):
        rows=[]
        for c in cases:
            judgments=[]
            for option in c['candidates']:
                verdict='wrong_segment' if index==0 and option['id']=='c0' else 'clean_boundary'
                heard='' if index==1 and option['id']=='c0' else '所以從神的國度來看'
                judgments.append({'id':option['id'],'verdict':verdict,'heard_target_phrase':heard,'reason':'原聲核對'})
            rows.append({'case_id':c['case_id'],'reference_issue':'none','candidates':judgments})
        answer={'results':rows};raw={'candidates':[{'finish_reason':'STOP','content':{'parts':[{'text':json.dumps(answer,ensure_ascii=False)}]}}]}
        material=[{k:v for k,v in c.items() if k!='candidates'}|{'candidates':[{k:v for k,v in p.items() if k!='clip'} for p in c['candidates']]} for c in cases]
        request={'parent_plan_sha256':file_sha(root/'boundary-audio-plan.json'),'cases':material,'prompt':PROMPT,'schema':SCHEMA}
        folder=root/'boundary-calls'/f'b{index:04d}';call=folder/'calls'/'saved';save_json(call/'request.json',request);save_json(call/'response.json',raw)
        save_json(folder/'validated.json',{'answer':answer,'request_sha256':object_sha(request),'raw_response_sha256':object_sha(raw)})
    accepted=saved_verdicts(root)
    assert accepted[('1','p10','start')]['time_ms']==200500
    assert read_json(root/'boundary-calls/b0001/validated.json')['answer']['results'][0]['candidates'][0]['heard_target_phrase']==''
