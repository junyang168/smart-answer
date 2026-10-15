"""Retain a real target review across independently reviewed peer anchor pruning.

Admission is explicit and transactional. Normal reads consume one direct proof;
source/target/evidence/relationship changes never enter this mechanical rule.
"""
from copy import deepcopy
from pathlib import Path


def anchor_pruning(before, after):
    if {k:v for k,v in before.items() if k not in {'revision','occurrences'}} != {k:v for k,v in after.items() if k not in {'revision','occurrences'}}:
        return False
    old, new = before.get('occurrences', []), after.get('occurrences', [])
    if len(old) != len(new):return False
    removed = 0
    for a,b in zip(old,new):
        if {k:v for k,v in a.items() if k!='anchors'} != {k:v for k,v in b.items() if k!='anchors'}:return False
        aa,bb=a.get('anchors',[]),b.get('anchors',[])
        if aa and not bb:return False
        pos=0
        for anchor in aa:
            if pos<len(bb) and anchor==bb[pos]:pos+=1
        if pos!=len(bb):return False
        removed+=len(aa)-len(bb)
    return removed>0


def protected_keys(value):
    return set(value['materials']['original_goal']['target_keys']) | {
        d['record_key'] for d in value['candidate']['dispositions'] if d['action']!='retain'}


def complete(value):
    from backend.pipeline.claim_management import Policy
    return (value.get('kind')=='claim_management_round' and value.get('outcome')=='completed'
        and value.get('program_readback_complete') and value.get('opus_approved')
        and not value.get('revisit_required')
        and not Policy.model_validate(value['materials']['policy']).independent_audit_required
        and value['materials']['original_goal']['operation']=='Update')


def round_value(cursor,rid):
    row=cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s FOR UPDATE',(rid,)).fetchone()
    if not row:raise ValueError('missing qualification review')
    return deepcopy(row[0])


def evidence(value):
    from backend.pipeline.cvp_sql_flow import check_binding
    from backend.pipeline.source_reconciliation_runner import read,body
    if not complete(value):raise ValueError('reuse requires an existing completed target review and fresh peer review')
    for bound in value['materials']['request']['source_bindings'].values():check_binding(bound)
    bound=value['stage_results']['readback'];check_binding(bound)
    opus=value['stage_results']['opus'];check_binding(opus)
    approval=value['reviewed']['evidence']['approval'];check_binding(approval)
    raw=body(read(Path(approval['path'])))['response']
    return bound,raw


def current_pins(store,cursor,value):
    """Cheap direct proof consumption, with no historical reconstruction."""
    from backend.pipeline.cvp_sql_flow import decoded_pins,check_binding
    from backend.api.canonical_repository.postgres_store import sha256_json
    reuse=value.get('reviewed_context_reuse')
    if not reuse:return decoded_pins(value['audited_pins'])
    core={k:v for k,v in reuse.items() if k!='event_ids'}
    if (core['original_pins_sha256']!=sha256_json(value['audited_pins'])
            or core['original_review']!=value['stage_results']['readback']
            or core['original_approval']!=value['reviewed']['evidence']['approval']
            or set(decoded_pins(core['current_pins']))!=set(decoded_pins(value['audited_pins']))):
        raise ValueError('qualification reuse original proof drift')
    check_binding(core['original_approval'])
    peer=round_value(cursor,core['peer_round_id'])
    if not complete(peer) or peer['stage_results']['readback']!=core['peer_review']:
        raise ValueError('qualification reuse peer review invalidated')
    check_binding(core['peer_review'])
    if peer['reviewed']['evidence']['approval']!=core['peer_approval']:raise ValueError('peer approval identity drift')
    check_binding(core['peer_approval'])
    for key,eid in reuse['event_ids'].items():
        event=cursor.execute('SELECT artifact,decision FROM wang_knowledge.review_events WHERE review_event_id=%s',(eid,)).fetchone()
        if not event or event[0]!=core or event[1]!='review_context_revalidated':raise ValueError('qualification reuse event drift')
    if set(reuse['event_ids'])!={k for k in protected_keys(value) if k.startswith('claims/')}:
        raise ValueError('qualification reuse omitted an original Claim')
    return decoded_pins(core['current_pins'])


def admit(store,cursor,original_id,peer_id):
    """One bounded admission. Caller holds the common transaction lock."""
    from backend.pipeline.cvp_sql_flow import decoded_pins,pin_rows
    from backend.api.canonical_repository.postgres_store import record_content_sha,sha256_json,canonical_json
    old=round_value(cursor,original_id);peer=round_value(cursor,peer_id)
    original_review,_=evidence(old);peer_review,review=evidence(peer)
    if (review['findings'] or any(r['disposition']!='resolved' for r in review['prior_findings'])
            or any(not v['approved'] or v['problems'] for v in review['verdicts'])):
        raise ValueError('peer review retains objections')
    if not set(old['materials']['original_goal']['required_questions'])<=set(peer['materials']['original_goal']['required_questions']):
        raise ValueError('peer review capabilities cannot support reuse')
    original=decoded_pins(old['audited_pins']);fresh=decoded_pins(peer['audited_pins'])
    if not set(original)<=set(fresh):raise ValueError('peer review omitted original dependency scope')
    current=store._assert_transaction_read_pins(cursor,fresh)
    store._assert_claim_management_scope(cursor,fresh,records=current)
    protected=protected_keys(old);retained={d['record_key'] for d in peer['candidate']['dispositions'] if d['action']=='retain'}
    if not {k for k in protected if k.startswith('claims/')}<=retained:raise ValueError('original target changed in peer operation')
    verdicts={v['record_key'] for v in review['verdicts'] if v['approved'] and not v['problems']}
    if not {k for k in protected if k.startswith('claims/')}<=verdicts:raise ValueError('peer did not inspect retained target')
    updated={key:fresh[key] for key in original};changes=[]
    for key,pin in original.items():
        if pin==fresh[key]:continue
        skey='/'.join(key)
        if (key[0]!='claims' or skey in protected or skey not in protected_keys(peer)
                or not pin or pin[2] or fresh[key][2]):raise ValueError('review basis changed beyond readonly Claim anchors')
        row=cursor.execute('SELECT payload,content_sha256 FROM wang_knowledge.object_versions WHERE collection=%s AND object_id=%s AND revision=%s',(*key,pin[0])).fetchone()
        if not row or row[1]!=pin[1] or record_content_sha(row[0])!=pin[1] or not anchor_pruning(row[0],current[key]['payload']):
            raise ValueError('peer update is not pure anchor pruning')
        changes.append(skey)
    if not changes:raise ValueError('qualification has no context drift to revalidate')
    store._assert_claim_management_scope(cursor,updated,records={key:current[key] for key in updated})
    for key in protected:
        if key.startswith('claims/') and cursor.execute("""SELECT 1 FROM wang_knowledge.pipeline_runs
            WHERE metadata->'blocked_current_claim_ids' ? %s AND started_at>
              (SELECT started_at FROM wang_knowledge.pipeline_runs WHERE run_id=%s) LIMIT 1""",(key.split('/',1)[1],original_id)).fetchone():
            raise ValueError('a later formal finding requires its own target review')
    core={'schema_version':'wang_claim_review_context_reuse_v1','original_round_id':original_id,
        'original_pins_sha256':sha256_json(old['audited_pins']),'original_review':original_review,
        'original_approval':old['reviewed']['evidence']['approval'],
        'peer_round_id':peer_id,'peer_review':peer_review,'peer_approval':peer['reviewed']['evidence']['approval'],'changed_readonly_claims':changes,'current_pins':pin_rows(updated),
        'basis':'unchanged target, sources, evidence and relations; reviewed readonly peer anchor pruning'}
    events={key:'CMCTX-'+sha256_json({'proof':core,'target':key})[:32] for key in protected if key.startswith('claims/')}
    proof={**core,'event_ids':events}
    if old.get('reviewed_context_reuse')==proof:return proof
    for key,eid in events.items():
        cursor.execute("""INSERT INTO wang_knowledge.review_events
            (review_event_id,collection,object_id,object_revision,reviewer_kind,reviewer_id,decision,reason,artifact)
            VALUES (%s,'claims',%s,%s,'system','claim-management-context-reuse','review_context_revalidated',%s,%s::jsonb)
            ON CONFLICT (review_event_id) DO NOTHING""",(eid,key.split('/',1)[1],current[tuple(key.split('/',1))]['revision'],core['basis'],canonical_json(core)))
    old['reviewed_context_reuse']=proof
    cursor.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s',(canonical_json(old),original_id))
    return proof


def after_completion(store,cursor,peer_id,peer):
    """Only existing full qualifications may be retained; no bystander upgrade."""
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    if not complete(peer):return []
    readonly={d['record_key'].split('/',1)[1] for d in peer['candidate']['dispositions'] if d['action']=='retain' and d['record_key'].startswith('claims/')} - {
        k.split('/',1)[1] for k in peer['materials']['original_goal']['target_keys']}
    restored=[]
    for cid in sorted(readonly):
        row=cursor.execute("""SELECT run_id FROM wang_knowledge.pipeline_runs
            WHERE metadata->>'kind'='claim_management_round' AND metadata->>'outcome'='completed'
              AND metadata->'materials'->'original_goal'->'target_keys' ? %s
            ORDER BY started_at DESC,run_id DESC LIMIT 1""",( 'claims/'+cid,)).fetchone()
        if not row:continue
        try:admit(store,cursor,row[0],peer_id)
        except (ValueError,ChangeSetConflict):continue
        restored.append('claims/'+cid)
    return restored

