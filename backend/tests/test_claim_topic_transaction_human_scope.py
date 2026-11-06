"""Both SQL commit paths preserve disjoint human-source evidence boundaries."""
import json
from pathlib import Path
import pytest
from backend.pipeline import claim_management as cm, claim_management_intake as intake, claim_management_batch as batch, claim_human_source as human, claim_topic_units as topics
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_commit import binding
from backend.tests.test_claim_management_intake import claims, pass_review
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
from backend.tests.test_claim_review_units import policy_file
from backend.tests.test_claim_human_source_decisions import save

@pytest.mark.parametrize('write', [False, True])
@pytest.mark.parametrize('local_drift', [False, True])
def test_topic_commit_ignores_other_confirmation_but_blocks_changed_own_fact(claims, write, local_drift):
    store,root=claims;root.mkdir(parents=True)
    sd=store.get_record_state('source_documents','S')['payload'];path=Path(sd['source_path'])
    path.write_text(json.dumps({'script':[{'text':'甲是假设，不是断言。'},{'text':'乙是另一主題。'}]}))
    sd.update(source_file_sha256=binding(path)['file_sha256'])
    fd=store.get_record_state('source_fragments','FD')['payload'];fd.update(paragraph_key='S0002',verbatim_excerpt='乙是另一主題。')
    store.apply_plan(store.plan_package({'package_id':'separate-test-topics','source_documents':[sd],'source_fragments':[fd]}))
    snap=cm.source.source_snapshot(cm.source.FileBinding.model_validate(binding(path)))
    units=[]
    for i,cid in enumerate(['C','D'],1):
        rows=store.read_claim_context([('claims',cid)])
        units.append(dict(unit_id=str(i),title='topic '+cid,claim_ids=[cid],paragraph_keys=['S000'+str(i)],record_keys=sorted(cm.source.key(*k)for k in rows if k[0]!='source_documents'),legacy_fragment_keys=[]))
    plan=dict(schema_version=topics.CONTRACT,authorization_quote='Synthetic disjoint topics with all readonly boundaries',source_id='S',source_file=binding(path),source_body_sha256=snap['body_sha256'],units=units)
    topics.validate_plan(plan);planpath=root/'topics.json';planpath.write_text(json.dumps(plan))
    def confirmation(pkey):
        text=snap['paragraphs'][pkey]['text'];record={'schema_version':'wang_claim_human_source_verification_v1','verified_by':'user','human_content_approval':False,'source_bindings':{'S':binding(path)},'user_confirmation':'synthetic owner confirms intended meaning','answers':[{'finding_id':'meaning-'+pkey,'question':'What is meant?','answer':'Synthetic confirmed meaning '+pkey,'citation':{'source_id':'S','paragraph_key':pkey,'exact_quote':text}}]}
        f=root/('human-'+pkey+'.json');f.write_text(json.dumps(record));return human.confirmation_entries(binding(f),record['source_bindings'],{'S':snap})
    save(store,confirmation('S0002'))
    pol=policy_file(root)
    if write:
        p=cm.Policy.model_validate_json(pol.read_text());p.initial_candidate='proposal';pol.write_text(p.model_dump_json())
    prepared=intake.prepare(['C'],root/'update',instruction='Refresh C only; D is a disjoint topic',groups=[['C']],creation_count=1,policy=pol,store=store,disjoint_topic_unit={'plan':binding(planpath),'unit_id':'1'})
    _,jobs=batch.load(prepared['manifest']['path'],root/'batch',store=store);request,execution=next(iter(jobs.values()))
    before=store.get_record_state('claims','C')
    def propose(packet):
        return {'changes':[{'record_key':'claims/C','fields_json':json.dumps({'statement':'甲是假设，不是断言。'}),'reason':'synthetic own source correction','citations':[{'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}]}],'creations':[],'retirements':[],'findings':[],'finding_resolutions':[]}
    def review(packet):
        assert not packet['sources']['S'].get('human_source_decisions')
        if local_drift:save(store,confirmation('S0001'))
        return pass_review(packet)
    ports=SyntheticPorts(store,execution,proposal=propose,opus=review)
    result=execute_claim_management(store,request,root=execution,ports=ports)
    if local_drift:
        assert result['status']=='paused' and 'human source decisions changed' in result['detail'],result
        assert store.get_record_state('claims','C')==before
    else:
        assert result['status']=='completed',result
        assert store.claim_management_qualification('C')['ready']
        assert (store.get_record_state('claims','C')!=before)==write
    assert ports.calls==(['gpt','opus']if write else ['opus'])
