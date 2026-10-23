from copy import deepcopy
import pytest
from backend.pipeline.viewpoint_deferred_source_check import validate_findings, run
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable

def packet():
    return {'artifact_sha256':'test','items':[{'item_id':'c:1','claim_id':'c',
        'claim_revision_sha256':'pin','statement':'这是安全的选择。','source_binding':{'path':'test'},'evidence':[
        {'evidence_step_id':'e','paragraph_key':'S0001','verbatim_excerpt':'这是实际的选择。'}],
        'source_paragraphs':[{'paragraph_key':'S0001','text':'这是实际的选择。也是相对安全的选择。','text_sha256':'paragraph-pin'}]}]}

def answer(kind='source_support_without_bound_evidence',quote='相对安全的选择'):
    return {'findings':[{'item_id':'c:1','diagnosis':kind,'evidence_step_ids':['e'],
        'paragraph_key':'S0001','exact_quote':quote,'reason':'原文有此说明，但未收入引文。'}]}

def test_source_support_is_not_bound_evidence():
    assert validate_findings(answer(),packet())['c:1']['diagnosis']=='source_support_without_bound_evidence'
    with pytest.raises(ValueError,match='silently'):
        validate_findings(answer('existing_evidence_sufficient'),packet())

@pytest.mark.parametrize('mutation',['foreign_evidence','foreign_paragraph','forged_quote','duplicate','missing'])
def test_forged_or_incomplete_answers_fail(mutation):
    raw=answer()
    if mutation=='foreign_evidence': raw['findings'][0]['evidence_step_ids']=['other']
    elif mutation=='foreign_paragraph': raw['findings'][0]['paragraph_key']='S0099'
    elif mutation=='forged_quote': raw['findings'][0]['exact_quote']='未经来源支持'
    elif mutation=='duplicate': raw['findings']*=2
    else: raw['findings']=[]
    with pytest.raises(ValueError): validate_findings(raw,packet())

def test_existing_evidence_can_be_reconsidered_without_promoting_context():
    assert validate_findings(answer('existing_evidence_sufficient','实际的选择'),packet())

class Adapter:
    model_id='test';backend='subscription_test';prompt_sha256='test';generation_config_sha256='test'
    def __init__(self,raw): self.raw=raw;self.calls=0;self.payloads=[]
    def generate(self,payload):
        self.calls+=1;self.payloads.append(deepcopy(payload));return self.raw

def test_blind_bounded_calls_preserve_source_handoff_not_completion(tmp_path,monkeypatch):
    from backend.pipeline import viewpoint_deferred_source_check as module
    monkeypatch.setattr(module,'build_packet',lambda *args:packet())
    write_immutable(tmp_path/'input.json',{})
    adapters=[Adapter(answer()),Adapter(answer())]
    result=run(tmp_path/'input.json',tmp_path/'input.json',tmp_path/'out',object(),adapters)
    assert result['group_complete'] is False and result['master_data_mutations']==0
    assert result['actions'][0]['next_action']=='claim_evidence_repair_authorization_required'
    assert len(result['repair_proposals'])==1
    assert result['repair_proposals'][0]['claim_or_evidence_writes_executed'] is False
    assert adapters[0].payloads==adapters[1].payloads
    run(tmp_path/'input.json',tmp_path/'input.json',tmp_path/'out',object(),adapters)
    assert [a.calls for a in adapters]==[1,1]

def test_invalid_answer_kept_without_retry_or_skip(tmp_path,monkeypatch):
    from backend.pipeline import viewpoint_deferred_source_check as module
    monkeypatch.setattr(module,'build_packet',lambda *args:packet())
    write_immutable(tmp_path/'input.json',{})
    adapters=[Adapter(answer('existing_evidence_sufficient')),Adapter(answer())]
    with pytest.raises(ValueError):
        run(tmp_path/'input.json',tmp_path/'input.json',tmp_path/'out',object(),adapters)
    assert (tmp_path/'out/raw-gpt.json').exists()
    assert read_artifact(tmp_path/'out/failure.json')['status']=='failed'
    assert [a.calls for a in adapters]==[1,0]
    assert not (tmp_path/'out/handoff.json').exists()

def test_disagreement_never_overwrites_hold(tmp_path,monkeypatch):
    from backend.pipeline import viewpoint_deferred_source_check as module
    monkeypatch.setattr(module,'build_packet',lambda *args:packet())
    write_immutable(tmp_path/'input.json',{})
    other=answer('needs_human_judgment')
    result=run(tmp_path/'input.json',tmp_path/'input.json',tmp_path/'out',object(),[Adapter(answer()),Adapter(other)])
    assert result['actions'][0]['next_action']=='human_judgment_required'
    assert result['group_complete'] is False
