import copy
import pytest
from backend.pipeline.slide_change_pilot import save_json,read_json
from backend.pipeline.transcript_source_observations import record_unintelligible,load_observations


def test_noisy_source_is_bound_to_exact_unconfirmed_text_without_becoming_approval(tmp_path):
    source=dict(records={'source':dict(source_sha256='original-audio',paragraphs=[dict(id='p2',text='未確認的粗稿')])})
    save_json(tmp_path/'plan.json',source)
    path=record_unintelligible(tmp_path,'p2','owner','噪音過大，無法可靠辨清')
    assert record_unintelligible(tmp_path,'p2','owner','噪音過大，無法可靠辨清')==path
    observation=load_observations(tmp_path)['p2']
    assert observation['text_confirmed'] is False and observation['inferred_replacement_allowed'] is False
    assert observation['automatic_model_retry_allowed'] is False
    assert read_json(tmp_path/'plan.json')==source
    changed=copy.deepcopy(source);changed['records']['source']['paragraphs'][0]['text']='另一份文字'
    save_json(tmp_path/'plan.json',changed)
    with pytest.raises(ValueError,match='does not match'):load_observations(tmp_path)
    with pytest.raises(ValueError):record_unintelligible(tmp_path,'unknown','owner','noise')
