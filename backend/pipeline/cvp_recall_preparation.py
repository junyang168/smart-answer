"""Read-only preparation for the first CVP 2.0 stage, on the common SQL CLI."""
from __future__ import annotations

from pathlib import Path
from collections.abc import Mapping
import json

from backend.api.canonical_repository.cvp_recall import (
    assert_current_index, build_index, build_statement_index, STATEMENT_SCOPE_PROFILE, recall_plan, seal, verify, work_groups,
)
from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.api.canonical_repository.cvp_claim_input import compile_input, validate_policy
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.cvp_recall import load_reviewed_index, recall_adapter, validate_clients
from backend.pipeline.viewpoint_cvp_policy import load_cvp_policy
from backend.pipeline.viewpoint_resolution_runtime import subscription_client

VERSION='wang_cvp_recall_preflight_v1'


def prepare_recall(store, request, *, load_index=load_reviewed_index, index=None, allow_unqualified=False, defer_index=False):
    """Never calls a model, writes SQL, generates a summary or creates grouping."""
    verify(request,VERSION)
    fields={'schema_version','claim_ids','grouping','cvp_policy','claim_input_policy','index_root',
            'recall_request_bytes','recall_max_calls','artifact_sha256'}
    if set(request)-{'recall_profile','contextual_summary_root'}!=fields:
        raise ValueError('recall preflight manifest fields differ')
    summary_root=request.get('contextual_summary_root')
    if summary_root is not None and (not isinstance(summary_root,str) or not Path(summary_root).is_absolute()):
        raise ValueError('contextual summary root must be an absolute path')
    profile=request.get('recall_profile')
    if profile not in (None, STATEMENT_SCOPE_PROFILE):
        raise ValueError('unknown recall input profile')
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
    authority=[];cards={};read_pins={};claim_pins={};blocked=[]
    def block(cid,row,q,reason_code,reason,kind='needs_upstream'):
        blocked.append({'claim_id':cid,'status':'blocked','kind':kind,
                        'reason_code':reason_code,'reason':reason,
                        'state_pin':claim_pins[cid],'qualification':q})
    for cid in request['claim_ids']:
        row=store.get_record_state('claims',cid)
        claim_pins[cid]=({'revision':row['revision'],'content_sha256':row['content_sha256'],
                          'retired':bool(row.get('retired'))} if row else None)
        if not row:
            if not allow_unqualified:
                raise ValueError('missing, retired or inconsistent recall Claim: '+cid)
            q=store.claim_management_qualification(cid)
            block(cid,row,q,'claim_missing','Current Claim record is missing.')
            continue
        # Corrupt stored payloads are SQL/data integrity failures, not a
        # qualification disposition. Never continue by trusting their pins.
        if (not isinstance(row.get('payload'),Mapping) or
                record_content_sha(row['payload'])!=row['content_sha256']):
            raise ValueError('missing, retired or inconsistent recall Claim: '+cid)
        if allow_unqualified:
            read_pins['claims',cid]=(row['revision'],row['content_sha256'],bool(row.get('retired')))
        if row.get('retired') or row['payload'].get('claim_id')!=cid:
            if not allow_unqualified:
                raise ValueError('missing, retired or inconsistent recall Claim: '+cid)
            q=store.claim_management_qualification(cid)
            reason='claim_retired' if row.get('retired') else 'claim_identity_mismatch'
            block(cid,row,q,reason,'Current Claim is retired or its identity differs.')
            continue
        value=row['payload'];q=store.claim_management_qualification(cid)
        if (not isinstance(q,Mapping) or q.get('ready') is not True or
                (q.get('revision'),q.get('content_sha256'))!=(row['revision'],row['content_sha256'])):
            if not allow_unqualified:
                raise ValueError('recall Claim lacks exact current qualification: '+cid)
            stale=isinstance(q,Mapping) and q.get('ready') is True and (
                q.get('revision'),q.get('content_sha256'))!=(row['revision'],row['content_sha256'])
            code=('qualification_stale' if stale else 'qualification_missing' if q is None else
                  'qualification_invalid_shape' if not isinstance(q,Mapping) else 'qualification_not_ready')
            block(cid,row,q,code,'Claim lacks exact current qualification.',
                  'superseded_input' if stale else 'needs_upstream')
            continue
        if (value.get('review_status') in {'human_hold','withheld_unreviewed','rejected','superseded'}
                or value.get('review_status')=='human_review_required'
                and q.get('legacy_ai_spot_check_replaced_by_current_review') is not True):
            if not allow_unqualified:
                raise ValueError('recall Claim has a real hold: '+cid)
            block(cid,row,q,'claim_hold','Claim has a real hold or unresolved upstream review.')
            continue
        try:
            projection=compile_input([value],input_policy)['claims'][0]['content']
        except ValueError as exc:
            if not allow_unqualified:raise
            block(cid,row,q,'claim_input_contract',str(exc))
            continue
        authority.append({'claim_id':cid,'qualification':q})
        read_pins['claims',cid]=(row['revision'],row['content_sha256'],False)
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
    if allow_unqualified:
        blocked_ids={r['claim_id'] for r in blocked}
        admitted_groups=[{'group_key':g['group_key'],
            'status':'blocked' if blocked_ids.intersection(g['claim_ids']) else 'ready',
            'blocked_claim_ids':[cid for cid in g['claim_ids'] if cid in blocked_ids]} for g in groups]
        base['admission']={'schema_version':'wang_cvp_claim_admission_v1','claim_pins':claim_pins,
            'blocked_claims':blocked,'groups':admitted_groups,
            'ready_group_keys':[g['group_key'] for g in admitted_groups if g['status']=='ready'],
            'blocked_group_keys':[g['group_key'] for g in admitted_groups if g['status']=='blocked']}
    if profile == STATEMENT_SCOPE_PROFILE:
        expected_index=build_statement_index(heads)
        if index is not None and index != expected_index:
            raise ValueError('statement index differs from exact current heads/profile')
        index=expected_index
    elif index is not None and index.get('input_profile') is not None:
        raise ValueError('index profile differs from bound recall request')
    if index is None and (request['index_root'] is None or defer_index) and heads:
        if store.read_cvp_heads()!=heads:
            raise ValueError('active CVP set or current head changed during preflight')
        store.verify_current_read_pins(read_pins)
        return seal({**base,'status':'index_required','recall_plans':[]})
    if index is not None:
        assert_current_index(index,heads)
    elif heads:
        root=Path(request['index_root'])
        if not root.is_absolute():raise ValueError('reviewed index requires an absolute path')
        index=load_index(heads,proposer=proposer,reviewer=reviewer,output_dir=root)
    else:
        index=build_index([],[])
    assert_current_index(index,heads)
    plans=[]
    for group in groups:
        if allow_unqualified and any(cid not in cards for cid in group['claim_ids']):
            continue
        plan=recall_plan(index,[cards[cid] for cid in group['claim_ids']],max_request_bytes=limit,
            measure=lambda payload:recall_adapter(proposer,payload,max_request_bytes=limit).request_bytes(payload))
        plans.append({'group_key':group['group_key'],'plan':plan})
    if sum(len(p['plan']['shards']) for p in plans)>cap:
        raise ValueError('all groups and CVP shards exceed the configured recall budget')
    assert_current_index(index,store.read_cvp_heads())
    store.verify_current_read_pins(read_pins)
    return seal({**base,'status':'prepared','index_sha256':index['artifact_sha256'],'recall_plans':plans})
