from copy import deepcopy
import json
from types import SimpleNamespace
import pytest
from backend.pipeline import claim_flash_evidence as eo, evidence_refresh as e
from backend.tests.test_claim_flash_source import fixture


def setup(tmp_path):
    records,sources=fixture(tmp_path)
    ports=SimpleNamespace(identity={'gpt':{'backend':'test','model':'test','effort':'medium'}})
    key='evidence_steps/E';p=records[key]['payload']
    v=e.material({**p,'evidence_step_id':key,'statement':p['statement']},records,sources)
    material={key:{'record':{k:p[k] for k in ('statement','title','speaker','stance','attribution','scripture_refs') if k in p},'sources':v['fragments']}}
    job={'job_id':'U0','unit':{'paragraph_key':'S0001','quote':'教授原話。','statement':'此觀點已有保存。'},'required_fragment_ids':['F0'],'jev':'partial'}
    old=tmp_path/'old';req={'input':{'task':'evidence_coverage_repair','jobs':[job]}}
    reply={'items':[{'job_id':'G','items':[{'job_id':'U0','action':'handoff'}]}]}
    e.save(old/'request.json',req);e.save(old/'response.json',reply)
    root=tmp_path/'run';folder=root/'handoff-resolutions'/e.digest(job)
    context={'source':sources['S']['file'],'unit':job,'material':material,
             'original_request':{'path':str(old/'request.json'),'sha256':e.digest(req)},'original_reply_sha256':e.digest(reply)}
    e.save(folder/'input.json',context)
    verdict={'job_id':'U0','action':'covered','record_keys':[key],'reason':'已保存，無須補卡。'}
    e.save(folder/'resolution.json',{'verdict':verdict,'input_sha256':e.digest(context),'database_writes':0})
    successor={'identity':ports.identity['gpt'],'system':'Focused coverage disposition',
               'input':{'task':'evidence_coverage_handoff_disposition','jobs':[{'job_id':'U0','unit':job['unit'],'existing_records':material}]}}
    path=folder/'gpt'/e.digest(successor)
    e.save(path/'request.json',successor);e.save(path/'response.json',{'items':[verdict]})
    return root,job,records,sources,ports,path


@pytest.mark.parametrize('fault',[None,'material','source','original_reply','missing_model_reply','invented_ref'])
def test_handoff_resolution_consumes_only_exact_saved_judgment_and_current_material(tmp_path,fault):
    root,job,records,sources,ports,path=setup(tmp_path);before=deepcopy(records)
    folder=root/'handoff-resolutions'/e.digest(job)
    if fault=='material':records['evidence_steps/E']['payload']['statement']='改過的意思。'
    if fault=='source':sources['S']['file']['file_sha256']='changed'
    if fault=='original_reply':(tmp_path/'old/response.json').write_text('{}')
    if fault=='missing_model_reply':(path/'response.json').unlink()
    if fault=='invented_ref':
        value=json.loads((folder/'resolution.json').read_text());value['verdict']['record_keys']=['evidence_steps/OTHER']
        (folder/'resolution.json').write_text(json.dumps(value))
    if fault:
        with pytest.raises(ValueError):eo.covered_handoff(root,job,records,sources,ports)
    else:
        result=eo.covered_handoff(root,job,records,sources,ports)
        assert result['action']=='covered' and result['record_keys']==['evidence_steps/E']
        assert records==before
