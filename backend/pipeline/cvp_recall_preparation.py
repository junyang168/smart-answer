"""Read-only preparation for the first CVP 2.0 stage, on the common SQL CLI."""
from __future__ import annotations

from pathlib import Path
import json

from backend.api.canonical_repository.cvp_recall import (
    assert_current_index, build_index, recall_plan, seal, verify, work_groups,
)
from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.api.canonical_repository.cvp_claim_input import compile_input, validate_policy
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.cvp_recall import load_reviewed_index, recall_adapter, validate_clients
from backend.pipeline.viewpoint_cvp_policy import load_cvp_policy
from backend.pipeline.viewpoint_resolution_runtime import subscription_client

VERSION='wang_cvp_recall_preflight_v1'


def prepare_recall(store, request, *, load_index=load_reviewed_index):
    """Never calls a model, writes SQL, manufactures a summary or creates grouping."""
    verify(request,VERSION)
    fields={'schema_version','claim_ids','grouping','cvp_policy','claim_input_policy','index_root',
            'recall_request_bytes','recall_max_calls','artifact_sha256'}
    if set(request)!=fields:
        raise ValueError('recall preflight manifest fields differ')
    groups=work_groups(request['grouping'],request['claim_ids'])
    limit=request['recall_request_bytes'];cap=request['recall_max_calls']
    if type(limit) is not int or limit<1 or type(cap) is not int or cap<0:
        raise ValueError('explicit recall byte/call budgets required')
    if (not isinstance(request['cvp_policy'],str) or not isinstance(request['claim_input_policy'],str) or
            request['index_root'] is not None and not isinstance(request['index_root'],str)):
        raise ValueError('policy and reviewed index paths must be strings')
    policy_path=Path(request['cvp_policy'])
    if not policy_path.is_absolute():raise ValueError('recall policy requires an absolute path')
    policy=load_cvp_policy(policy_path)
    input_path=Path(request['claim_input_policy'])
    if not input_path.is_absolute():raise ValueError('Claim input policy requires an absolute path')
    input_policy=json.loads(input_path.read_text())
    validate_policy(input_policy)
    if limit>policy['max_request_bytes']:
        raise ValueError('recall capacity cannot exceed the bound role policy')
    # Client construction is lazy. Cache validation never performs login or a
    # fallback model dispatch; only actual generate() verifies runtime access.
    p=policy['proposal'];r=policy['review']
    proposer=subscription_client(p['provider'],p['model'],p['effort'])
    reviewer=subscription_client(r['provider'],r['model'],r['effort'])
    validate_clients(proposer,reviewer)
    authority=[];cards={};read_pins={}
    for cid in request['claim_ids']:
        row=store.get_record_state('claims',cid)
        if (not row or row.get('retired') or row['payload'].get('claim_id')!=cid or
                record_content_sha(row['payload'])!=row['content_sha256']):
            raise ValueError('missing, retired or inconsistent recall Claim: '+cid)
        value=row['payload'];q=store.claim_management_qualification(cid)
        if (not q or q.get('ready') is not True or
                (q.get('revision'),q.get('content_sha256'))!=(row['revision'],row['content_sha256'])):
            raise ValueError('recall Claim lacks exact current qualification: '+cid)
        if (value.get('review_status') in {'human_hold','withheld_unreviewed','rejected','superseded'}
                or value.get('review_status')=='human_review_required'
                and q.get('legacy_ai_spot_check_replaced_by_current_review') is not True):
            raise ValueError('recall Claim has a real hold: '+cid)
        authority.append({'claim_id':cid,'qualification':q})
        read_pins['claims',cid]=(row['revision'],row['content_sha256'],False)
        projection=compile_input([value],input_policy)['claims'][0]['content']
        cards[cid]={'claim_id':cid,**projection}
    heads=store.read_cvp_heads()
    link_records=store.read_cvp_links(claim_ids=request['claim_ids'])
    for key,row in link_records.items():
        if row.get('retired') or record_content_sha(row['payload'])!=row['content_sha256']:
            raise ValueError('existing Claim connection is retired or inconsistent')
        read_pins[key]=(row['revision'],row['content_sha256'],False)
    existing_links=[row['payload'] for row in link_records.values()
                    if row['payload'].get('effective_state')=='active']
    if any(l['viewpoint_id'] not in {h['viewpoint_id'] for h in heads} for l in existing_links):
        raise ValueError('existing active connection has no active CVP head')
    base={'schema_version':VERSION,'request_sha256':request['artifact_sha256'],
          'groups':groups,'claim_authority':authority,'heads':heads,'claim_input_policy':input_policy,
          'role_policy_sha256':sha256_json(policy),
          'claim_projection_fields':{cid:sorted(c) for cid,c in cards.items()},
          'existing_links':existing_links,'model_calls':0,'semantic_writes':0}
    if request['index_root'] is None and heads:
        if store.read_cvp_heads()!=heads:
            raise ValueError('active CVP set or current head changed during preflight')
        store.verify_current_read_pins(read_pins)
        return seal({**base,'status':'index_required','recall_plans':[]})
    if heads:
        root=Path(request['index_root'])
        if not root.is_absolute():raise ValueError('reviewed index requires an absolute path')
        index=load_index(heads,proposer=proposer,reviewer=reviewer,output_dir=root)
    else:
        index=build_index([],[])
    assert_current_index(index,heads)
    plans=[]
    for group in groups:
        plan=recall_plan(index,[cards[cid] for cid in group['claim_ids']],max_request_bytes=limit,
            measure=lambda payload:recall_adapter(proposer,payload,max_request_bytes=limit).request_bytes(payload))
        plans.append({'group_key':group['group_key'],'plan':plan})
    if sum(len(p['plan']['shards']) for p in plans)>cap:
        raise ValueError('all groups and CVP shards exceed the configured recall budget')
    assert_current_index(index,store.read_cvp_heads())
    store.verify_current_read_pins(read_pins)
    return seal({**base,'status':'prepared','index_sha256':index['artifact_sha256'],'recall_plans':plans})
