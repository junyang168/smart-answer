from copy import deepcopy
import json

import pytest

from backend.api.canonical_repository import cvp_management as domain
from backend.api.canonical_repository.cvp_recall import seal
from backend.pipeline import cvp_management_models as models
from backend.pipeline.viewpoint_resolution_runtime import call_model, read_saved_model_response
from test_cvp_management import request,materials,proposal,passing
from test_cvp_recall_stages import Client,review_client


def envelope(result,*,candidate=None,review=None,contexts=(),members=()):
    return {'result':result,**({'review':review} if result=='review' else {'candidate':candidate}),
            **({'review':review} if review is not None and result!='review' else {}),
            **({'candidate':candidate} if candidate is not None and result=='review' else {}),
            'context_requests':list(contexts),'member_requests':list(members)}


def scoped_bundle():
    b=materials()
    b['material']['sources']=[
        {'source_ref':'s1','expansion_level':0,'sections':[{'section_ref':'p1'}]},
        {'source_ref':'s2','expansion_level':1,'sections':[{'section_ref':'p2'}]},
        {'source_ref':'s3','expansion_level':2,'sections':[{'section_ref':'p3'}]},
    ]
    b['material']['claims'].extend([
        {'claim_ref':'c2','target':False,'support_loaded':False},
        {'claim_ref':'c3','target':False,'support_loaded':True}])
    return seal({k:v for k,v in b.items() if k!='artifact_sha256'})


def test_adapter_exact_role_packet_schema_identity_and_semantic_field_shapes():
    req,b=request(),materials();packet=domain.proposal_packet(req,b)
    client=Client();original=deepcopy(packet)
    a=models.adapter('proposal',req,b,packet,client=client)
    answer=a._response_model.model_validate(envelope('candidate',candidate=proposal()))
    assert models.validate_response('proposal',answer.model_dump(mode='json'))['result']=='candidate'
    assert packet==original
    assert a.model_id=='gpt-6.1-sol' and a.backend=='codex_subscription'
    assert a._client.max_output_tokens==req.budget.max_output_tokens
    assert a.max_request_bytes==req.budget.max_request_bytes
    assert 'proposition_signature={subject,predicate,object' in a._prompt
    assert '程序补齐record IDs' in a._prompt
    assert 'summary' in a._prompt and '150 Unicode' in a._prompt
    assert 'prior_findings' in models.adapter('review',req,b,
        domain.review_packet(req,b,domain.compile_candidate(req,b,proposal())),client=review_client())._prompt
    altered=deepcopy(packet);altered['instructions']='different'
    with pytest.raises(ValueError,match='exact domain packet'):
        models.adapter('proposal',req,b,altered,client=client)
    with pytest.raises(ValueError,match='bound previous'):
        models.adapter('correction',req,b,packet,client=client)
    with pytest.raises(ValueError,match='subscription role'):
        models.adapter('review',req,b,domain.review_packet(req,b,domain.compile_candidate(req,b,proposal())),client=client)


def test_incremental_summary_prompt_checks_all_old_and_new_members():
    from backend.api.canonical_repository.cvp_recall import STATEMENT_SCOPE_PROFILE
    req=request(input=seal({'schema_version':'wang_cvp_recall_preflight_v1',
                            'recall_profile':STATEMENT_SCOPE_PROFILE}))
    bundle=materials()
    proposer=models.adapter('proposal',req,bundle,domain.proposal_packet(req,bundle),client=Client())
    assert '最近的材料挤掉旧成员的必要限定' in proposer._prompt
    assert '不能只凭新Claim未提到就删掉' in proposer._prompt
    assert '具体核对能否用extends／qualifies连接到旧CVP' in proposer._prompt
    assert '本次修改权限不能单独证明新身份' in proposer._prompt
    assert '仅作supports时也不自动扩大旧scope' in proposer._prompt
    assert '旧CVP经忠实修订仍不能承载的独立真值条件' in proposer._prompt
    assert '普通新连接所读取的候选旧成员是只读比较材料' in proposer._prompt
    assert '把仅因该连接产生的summary改动放进discard_refs' in proposer._prompt
    assert '所以就是说' in proposer._prompt
    candidate=domain.compile_candidate(req,bundle,proposal())
    reviewer=models.adapter('review',req,bundle,domain.review_packet(req,bundle,candidate),client=review_client())
    assert '若摘要偏向最新Claim而遗漏旧成员' in reviewer._prompt
    assert '无依据删掉旧summary中仍有来源支持的承重信息' in reviewer._prompt
    assert '若仅因范围措辞不同、旧scope或本次权限不能修改' in reviewer._prompt
    assert '旧scope或本次权限不能修改' in reviewer._prompt
    assert '新候选具有旧CVP不能承载且有原文依据的独立真值条件' in reviewer._prompt
    assert '只能引用target_refs包含该项ref的finding' in reviewer._prompt


@pytest.mark.parametrize('bad', ['claim','text','component','creation','target','summary','member'])
def test_scoped_proposal_schema_rejects_foreign_component_object_and_creation_refs(bad):
    req,b=request(),materials();a=models.adapter('proposal',req,b,domain.proposal_packet(req,b),client=Client())
    raw=proposal()
    if bad=='claim':raw['components']=[{'claim_ref':'foreign','spans':[{'text_ref':'t1','start_char':0,'end_char':1}]}]
    elif bad=='text':raw['components']=[{'claim_ref':'c1','spans':[{'text_ref':'foreign','start_char':0,'end_char':1}]}]
    elif bad=='component':raw['component_dispositions'][0]['component_ref']='k9999'
    elif bad=='creation':raw['changes'][0]['ref']='nv999'
    elif bad=='target':raw['component_dispositions'][0]['target_ref']='e1'
    elif bad=='summary':raw['summaries'][0]['ref']='nr1'
    else:raw['member_dispositions']=[{'member_ref':'l99','disposition':'retained','reason':'原因'}]
    with pytest.raises(ValueError):domain.compile_candidate(req,b,raw)


def test_component_plan_can_split_within_exact_unicode_upper_bound():
    req,b=request(),materials();a=models.adapter('proposal',req,b,domain.proposal_packet(req,b),client=Client())
    raw=proposal();raw['component_dispositions'][0]['component_ref']='k2'
    assert a._response_model.model_validate(envelope('candidate',candidate=raw)).candidate.component_dispositions[0].component_ref=='k2'
    # Schema permits potential new components; pure compiler still checks the
    # actual selected positions and exact component denominator.
    with pytest.raises(ValueError,match='foreign component'):
        domain.compile_candidate(req,b,raw)


def test_context_requests_select_only_current_source_section_gradient_and_unloaded_member():
    req,b=request(),scoped_bundle();a=models.adapter('proposal',req,b,domain.proposal_packet(req,b),client=Client())
    valid=envelope('context_insufficient',contexts=[{'source_ref':'s1','section_ref':'p1','extent':'adjacent','reason':'需讀前後承接'}],
                   members=[{'member_ref':'c2','reason':'需讀本成員自身證據'}])
    assert a._response_model.model_validate(valid).result=='context_insufficient'
    from backend.pipeline.cvp_materials import validate_material_requests
    for changes in [dict(source_ref='s2'),dict(section_ref='p2'),dict(extent='full'),dict(source_ref='s3',section_ref='p3')]:
        bad=deepcopy(valid);bad['context_requests'][0].update(changes)
        with pytest.raises(ValueError):validate_material_requests(b,bad)
    for member in ['c1','c3','foreign']:
        bad=deepcopy(valid);bad['member_requests'][0]['member_ref']=member
        with pytest.raises(ValueError):validate_material_requests(b,bad)
    full=envelope('context_insufficient',contexts=[{'source_ref':'s2','section_ref':'p2','extent':'full','reason':'仍缺完整論證'}])
    assert a._response_model.model_validate(full).context_requests[0].extent=='full'


@pytest.mark.parametrize('raw', [
    envelope('candidate',candidate=proposal(),review={'component_reviews':[],'object_reviews':[],
        'prior_findings':[],'findings':[],'novelty_approved':True}),
    envelope('candidate',candidate=proposal(),members=[{'member_ref':'c2','reason':'更多證據'}]),
    envelope('context_insufficient'),
    envelope('context_insufficient',candidate=proposal(),members=[{'member_ref':'c2','reason':'更多證據'}]),
    envelope('context_insufficient',members=[{'member_ref':'c2','reason':'   '}]),
])
def test_response_modes_cannot_mix_material_requests_and_semantic_decisions(raw):
    with pytest.raises(ValueError):models.validate_response('proposal',raw)


def test_review_finite_component_object_prior_refs_and_local_findings():
    req,b=request(),materials();candidate=domain.compile_candidate(req,b,proposal())
    packet=domain.review_packet(req,b,candidate)
    a=models.adapter('review',req,b,packet,client=review_client())
    raw=passing(candidate)
    assert models.validate_response('review',envelope('review',review=raw))['review']['novelty_approved']
    for field in ['component_reviews','object_reviews']:
        bad=deepcopy(raw);bad[field][0]['ref']='foreign'
        with pytest.raises(ValueError):domain.validate_review(req,b,candidate,bad)
    bad=deepcopy(raw);bad['prior_findings']=[{'ref':'pf999','disposition':'resolved','reason':'消解'}]
    with pytest.raises(ValueError):domain.validate_review(req,b,candidate,bad)
    bad=deepcopy(raw);bad['component_reviews'][0].update(decision='revise',finding_refs=['f99'])
    with pytest.raises(ValueError,match='finding'):
        domain.validate_review(req,b,candidate,bad)
    with pytest.raises(ValueError):a._response_model.model_validate(envelope('candidate',candidate=proposal()))
    with pytest.raises(ValueError,match='another semantic role'):
        models.validate_response('proposal',envelope('review',review=raw))


def test_shared_runtime_payload_cache_schema_and_output_limit_identity(tmp_path):
    req,b=request(),materials();packet=domain.proposal_packet(req,b)
    c=Client(lambda p:envelope('candidate',candidate=proposal()))
    a=models.adapter('proposal',req,b,packet,client=c)
    raw,count,_=call_model(a,packet,tmp_path/'proposal.json')
    assert count==1 and models.validate_response('proposal',raw)['candidate']['changes']
    from backend.pipeline.cvp_model_packet import project
    assert json.loads(c.requests[0]['user_prompt'])==project(packet)
    assert read_saved_model_response(a,packet,tmp_path/'proposal.json')==raw
    bigger=req.model_copy(update={'budget':req.budget.model_copy(update={'max_output_tokens':32000})})
    other=models.adapter('proposal',bigger,b,domain.proposal_packet(bigger,b),client=c)
    assert other.generation_config_sha256!=a.generation_config_sha256
    # Even unchanged payload cannot reinterpret original raw with a new schema.
    a.generation_config_sha256='0'*64
    with pytest.raises(ValueError,match='generation config'):
        read_saved_model_response(a,packet,tmp_path/'proposal.json')
    assert len(c.requests)==1


def test_unknown_management_dispatch_is_never_automatically_repeated(tmp_path):
    req,b=request(),materials();packet=domain.proposal_packet(req,b)
    def timeout(p):raise TimeoutError('unknown semantic outcome')
    c=Client(timeout);a=models.adapter('proposal',req,b,packet,client=c)
    with pytest.raises(TimeoutError):call_model(a,packet,tmp_path/'proposal.json')
    with pytest.raises(ValueError,match='unknown'):call_model(a,packet,tmp_path/'proposal.json')
    assert len(c.requests)==1


def test_correction_can_discard_only_previous_uncommitted_writes():
    req,b=request(),materials()
    first=domain.compile_candidate(req,b,proposal())
    initial=models.adapter('proposal',req,b,domain.proposal_packet(req,b),client=Client())
    correction=models.adapter('proposal',req,b,domain.proposal_packet(req,b,previous=first),client=Client())
    raw={'discard_refs':['nv1','nr1','nl1'], 'component_dispositions':[
        {'component_ref':'k1','disposition':'background','reason':'复核确认仅为背景。'}]}
    parsed=correction._response_model.model_validate(envelope('candidate',candidate=raw))
    after=domain.compile_candidate(req,b,parsed.candidate,previous=first)
    assert after['writes']==[]
    assert after['component_plan']==first['component_plan']
    for bad in [raw, {'discard_refs':['c1']}, {'discard_refs':['nv99']}]:
        with pytest.raises(ValueError):
            domain.compile_candidate(req,b,bad)
    for ref in ['c1','nv99']:
        with pytest.raises(ValueError):
            domain.compile_candidate(req,b,{'discard_refs':[ref]},previous=first)


def test_pending_wording_rebase_schema_requires_explicit_positions_and_all_old_mappings():
    from backend.api.canonical_repository.cvp_claim_input import compile_input
    from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha
    req,old=request(),materials();first=domain.compile_candidate(req,old,proposal())
    new=deepcopy(old);new.pop('artifact_sha256')
    claim=new['records'][0]['payload'];claim.update(statement='在此語境中，人子是神。',revision=3)
    new['records'][0].update(revision=3,content_sha256=semantic_record_sha(claim))
    new['claim_input']=compile_input([claim],old['claim_input']['policy'],claim_refs={'C1':'c1'})
    new=seal(new);frame=domain.prepare_rebase_frame(req,old,new,first)
    packet=domain.proposal_packet(req,new,previous=frame)
    adapter=models.adapter('proposal',req,new,packet,client=Client())
    for raw in ({}, {'components':[],'component_mappings':[]},
                {'components':[{'claim_ref':'c1','spans':[{'text_ref':'t1','start_char':0,'end_char':1}]}],
                 'component_mappings':[]}):
        with pytest.raises(ValueError):domain.compile_candidate(req,new,raw,previous=frame)
    raw={'components':[{'claim_ref':'c1','spans':[{'text_ref':'t1','start_char':0,'end_char':len(claim['statement'])}]}],
         'component_mappings':[{'old_ref':'k1','new_refs':['k1']}]}
    assert adapter._response_model.model_validate(envelope('candidate',candidate=raw)).candidate.components
    assert '旧候选尚不适用于当前Claim' in adapter._prompt


def test_empty_domains_use_empty_arrays_without_impossible_child_schemas():
    req,b=request(),materials()
    model=models.adapter('proposal',req,b,domain.proposal_packet(req,b),client=Client())._response_model
    schema=model.model_json_schema()
    def check(node):
        if isinstance(node,dict):
            assert node.get('minLength',0)<=node.get('maxLength',float('inf'))
            for child in node.values():check(child)
        elif isinstance(node,list):
            for child in node:check(child)
    check(schema)
    good=envelope('candidate',candidate=proposal())
    model.model_validate(good)
    for field in ('component_mappings','discard_refs','member_dispositions'):
        bad=deepcopy(good);bad['candidate'][field]=['not-allowed']
        with pytest.raises(ValueError):domain.compile_candidate(req,b,bad['candidate'])
    bad=deepcopy(good);bad['member_requests']=['not-allowed']
    with pytest.raises(ValueError):model.model_validate(bad)
