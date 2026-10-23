"""Explicit admission can resume at committed checkpoints, not old snapshots."""
import json
import uuid

import pytest

from backend.api.canonical_repository.postgres_store import sha256_json
from backend.pipeline.cvp_sql_admission import resume_admission
from backend.pipeline.cvp_sql_flow import binding
from backend.tests.test_cvp_sql_transaction_postgres import task,apply
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store


def fixture(store,tmp_path):
    plan,context=task(store);apply(store,plan,context)
    files={}
    for name in ('campaign','seed','policy','semantic','old-evidence','pending-evidence'):
        path=tmp_path/name;path.write_text(name);files[name]=binding(path)
    groups=[{'sequence':1,'group_key':'a','claim_ids':['CL-A']},
            {'sequence':2,'group_key':'b','claim_ids':['CL-B']}]
    definition={'accepted_grouping_sha256':uuid.uuid4().hex*2,'groups':groups,
                'cvp_policy':files['policy'],'semantic_contract':files['semantic'],
                'authorization':{'owner_campaign':files['campaign'],'checkpoint_seed':files['seed']}}
    history=store.get_change_history(plan.change_set_id)
    rows=[]
    for group,name in zip(groups,('old-evidence','pending-evidence')):
        rows.append({'group':group,'certificate':{'status':'verified_historical_completion','bindings':[files[name]],
            'change_set_ids':[plan.change_set_id],'viewpoint_ids':[],
            'history':[{'change_set_id':plan.change_set_id,'history_sha256':sha256_json(history)}],
            'revisit_required':True,'independent_audit_complete':False}})
    body={'schema_version':'cvp_sql_admission.v2','definition':definition,'completions':rows}
    path=tmp_path/'preview.json';path.write_text(json.dumps({**body,'artifact_sha256':sha256_json(body)}))
    campaign=store.initialize_cvp_campaign(definition)
    store.admit_cvp_completion(campaign_sha256=campaign,**rows[0])
    args={'preview_sha256':binding(path)['file_sha256'],'campaign_path':files['campaign']['path'],
          'campaign_sha256':files['campaign']['file_sha256'],'seed_sha256':files['seed']['file_sha256']}
    return path,args,files,campaign


def test_saved_preview_skips_only_exact_committed_admission_prefix(postgres_store,tmp_path):
    path,args,files,campaign=fixture(postgres_store,tmp_path)
    # Fixture-only removal proves the completed prefix is not reopened. Real
    # historical files remain preserved; this test never touches them.
    (tmp_path/'old-evidence').unlink()
    result=resume_admission(postgres_store,path,**args)
    assert result['resumed_prefix']==1 and result['admitted_groups']==2
    (tmp_path/'pending-evidence').unlink()
    assert resume_admission(postgres_store,path,**args)['resumed_prefix']==2
    assert all(r['revisit_required'] and not r['independent_audit_complete']
               for r in postgres_store.cvp_campaign_progress(campaign))


@pytest.mark.parametrize('drift',['pending-evidence','preview','seed','campaign'])
def test_pending_or_operator_identity_drift_cannot_admit_more(postgres_store,tmp_path,drift):
    path,args,files,campaign=fixture(postgres_store,tmp_path)
    (path if drift=='preview' else tmp_path/drift).write_text('changed')
    with pytest.raises(ValueError):resume_admission(postgres_store,path,**args)
    assert len(postgres_store.cvp_campaign_progress(campaign))==1
