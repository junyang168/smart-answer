"""Exact human Claim correction intake; models still propose/review all writes.

This is not a postimage approval, another audit adjudication or a retry scope.
Use the existing Claim issue identity/source action/common SQL transaction.
"""
from copy import deepcopy
from pathlib import Path

from backend.api.canonical_repository.postgres_store import sha256_json,record_content_sha
from backend.pipeline.viewpoint_resolution_runtime import read_artifact,write_immutable

VERSION='wang_owner_claim_completion_direction_v1'
NAME='owner-claim-revision-binding.json'


def validate_direction(direction,value,claim_state):
    from backend.pipeline.cvp_sql_flow import check_binding
    if (direction.get('schema_version')!=VERSION
            or direction.get('artifact_sha256')!=sha256_json({k:v for k,v in direction.items() if k!='artifact_sha256'})
            or direction.get('campaign_sha256')!=value['campaign_sha256']
            or direction.get('run_id')!=value['run_id']
            or direction.get('claim_id')!=claim_state['payload']['claim_id']
            or direction.get('before_claim_pin')!=[claim_state['revision'],claim_state['content_sha256'],False]
            or claim_state['retired'] or record_content_sha(claim_state['payload'])!=claim_state['content_sha256']
            or direction.get('before_statement')!=claim_state['payload']['statement']
            or direction.get('decision')!='propose_source_supported_claim_completion'
            or not direction.get('authorization_quote') or not direction.get('instruction')
            or direction.get('human_content_approval') is not False
            or direction.get('maximum_corrections')!=1
            or direction.get('source_changes')!=0 or direction.get('cvp_core_changes')!=0):
        raise ValueError('owner Claim completion requires exact current source-supported revision direction')
    if direction['claim_id'] not in value['claim_ids']:
        admitted={ (p['collection'],p['object_id']):p['pin'] for p in value['reviewed']['evidence']['read_pins'] }
        if (admitted.get(('claims',direction['claim_id']))!=direction['before_claim_pin']
                or direction.get('existing_dependency_only') is not True
                or direction.get('campaign_membership_changes')!=0):
            raise ValueError('external Claim must be an exact existing audited dependency, never a new group member')
    for key in ('source','original_handoff','policy'):check_binding(direction[key])
    from backend.pipeline.claim_issue_management import read_policy
    policy=read_policy(direction['policy'])
    if (policy.claim_fields!=['statement'] or policy.evidence_fields or policy.cvp_permissions
            or set(policy.link_fields)!={'pinned_claim_revision','component_locator','evidence_bindings'}
            or policy.evidence_eligibility_review):
        raise ValueError('owner completion cannot change source/Evidence/CVP/type/identity or add/delete Claims')


def dependency_issue(issue,value,claim_state):
    """No generic foreign Claim admission; consume only explicit current direction."""
    from backend.pipeline import source_reconciliation as c
    import json
    if issue.origin!='owner':raise ValueError('foreign finder cannot repair an out-of-group Claim')
    direction=json.loads(c.bound_bytes(issue.report))
    validate_direction(direction,value,claim_state)
    if (direction['claim_id']!=issue.claim_id or direction['before_claim_pin'][0]!=issue.claim_revision
            or direction['before_claim_pin'][1]!=issue.claim_sha256):
        raise ValueError('owner dependency issue differs from exact Claim direction')


def inherit(value):
    """Bounded predecessor projection, not full old source/material snapshots."""
    e=value['reviewed']['evidence'];a=e.get('action',{})
    if not e.get('pending_cvp_management_record_keys'):return None
    if not e['approval'].get('pending_link_material'):
        raise ValueError('existing pending CVP facts lack committed management authority')
    # Existing reviewed evidence already carries exact authoritative material
    # hash; projected predecessor needs no 5MB physical snapshot duplication.
    result={k:deepcopy(value[k]) for k in ('run_id','campaign_sha256','claim_ids','change_set_ids','claim_layer_audit')}
    old={k:deepcopy(e[k]) for k in ('approval','package','pending_cvp_management_record_keys','viewpoint_ids')}
    old['action']={'action_id':a['action_id'],'materials_sha256':sha256_json(a['materials']),
                   'materials':{'claim_management_resolution':a['materials']['claim_management_resolution']}}
    result['reviewed']={'plan':{'change_set_id':value['reviewed']['plan']['change_set_id'],
                               'source_kind':value['reviewed']['plan']['source_kind']},'evidence':old}
    return result


def attach_material(store,value,materials,root):
    context=materials.get('claim_issue_context',{})
    reports=[i['report'] for i in context.get('issues',[]) if i['origin']=='owner']
    if not reports:return
    directions=[read_artifact(r['path']) for r in reports]
    if not all(d.get('schema_version')==VERSION for d in directions):return
    if len(directions)!=1:raise ValueError('one exact owner completion direction per Claim action')
    d=directions[0];validate_direction(d,value,store.get_record_state('claims',d['claim_id']))
    previous=inherit(value)
    if previous:
        path=Path(root)/'prior-management.json';write_immutable(path,previous)
        from backend.pipeline.cvp_sql_flow import binding
        materials['prior_cvp_management']=binding(path)
        materials['claim_management_resolution']=previous['reviewed']['evidence']['action']['materials']['claim_management_resolution']
    materials['owner_claim_completion']=reports[0]


def validate_joint(materials,proposal,package):
    """Completion of known Claim omission cannot silently alter link meaning.

    The ordinary proposal/review validator still independently verifies full
    Claims, bound evidence and every affected CVP disposition.
    """
    proof=materials.get('owner_claim_completion')
    if not proof:return False
    from backend.pipeline.cvp_sql_flow import check_binding
    from backend.pipeline import source_reconciliation as c
    check_binding(proof);d=read_artifact(proof['path']);cid=d['claim_id']
    before=c.index(materials['before_records']);after={**before,**c.index(proposal.records)}
    claim=after['claims/'+cid]
    if (claim['statement']==before['claims/'+cid]['statement']
            or any(a.cause!='claim_statement' for a in proposal.claim_assessments)
            or len(proposal.claim_assessments)!=1):
        raise ValueError('owner source omission requires actual minimal Claim completion, not retain/relabel')
    changed={r['collection']+'/'+r['object_id'] for r in proposal.records}
    links={k:p for k,p in before.items() if k.startswith('viewpoint_claim_links/') and p['claim_id']==cid}
    if not changed<={'claims/'+cid,*links}:
        raise ValueError('owner Claim completion changed unrelated knowledge')
    active={k for k,p in links.items() if p['effective_state']=='active'}
    if not active<=changed:
        raise ValueError('Claim completion omitted an active CVP connection revalidation')
    for k in active:
        old,new=links[k],after[k]
        if any(new.get(f)!=old.get(f) for f in ('claim_id','viewpoint_id','link_type','effective_state',
                'validated_against_viewpoint_revision_id','occurrence_refs','supporting_relation_ids')):
            raise ValueError('Claim completion cannot change retained connection meaning/type/target')
        if new['pinned_claim_revision']!=claim['revision']:
            raise ValueError('Claim completion connection lacks new Claim revision')
    # Inactive connections may retain their historical pin. If revalidated,
    # their retired logical meaning and endpoints must remain exactly unchanged.
    for k in changed & set(links):
        if any(after[k].get(f)!=links[k].get(f) for f in ('claim_id','viewpoint_id','link_type','effective_state')):
            raise ValueError('owner completion cannot revive/retire/retarget a link')
    return True


def partial_review(materials,proposal):
    """Review an explicitly incomplete draft, never authorize its graph.

    Exact unchanged owned links can remain pending while the new Claim is
    proposed. Opus sees BOTH versions and must not approve the pending links.
    Only the ordinary single correction can supply complete link afterimages.
    Previously pending unrelated identities remain a separate obligation.
    """
    from backend.pipeline import source_reconciliation as c
    from backend.pipeline.cvp_sql_flow import check_binding
    from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha
    proof=materials.get('owner_claim_completion')
    if not proof:raise ValueError('partial Claim review lacks exact owner direction')
    check_binding(proof);d=read_artifact(proof['path'])
    before=c.index(materials['before_records']);proposed=c.index(proposal.records)
    cid=d['claim_id'];key='claims/'+cid
    pins={(p['collection'],p['object_id']):p['pin'] for p in materials['sql_scope']['read_pins']}
    state={'payload':before[key],'revision':before[key]['revision'],
           'content_sha256':record_content_sha(before[key]),'retired':False}
    validate_direction(d,{'run_id':d['run_id'],'campaign_sha256':d['campaign_sha256'],
        'claim_ids':[],'reviewed':{'evidence':{'read_pins':materials['sql_scope']['read_pins']}}},state)
    if pins.get(('claims',cid))!=d['before_claim_pin'] or key not in proposed or proposed[key]['statement']==before[key]['statement']:
        raise ValueError('partial Claim review requires the exact new statement/current dependency pin')
    permissions={c.key(p['collection'],p['object_id']):set(p['fields']) for p in materials['manifest']['permissions']}
    owned={k for k,p in before.items() if k.startswith('viewpoint_claim_links/') and p['claim_id']==cid
           and k in permissions}
    prior=materials.get('prior_cvp_management')
    pending=set()
    if prior:
        check_binding(prior);previous=read_artifact(prior['path'])
        if previous['run_id']!=d['run_id']:raise ValueError('partial review foreign pending context')
        pending=set(previous['reviewed']['evidence']['pending_cvp_management_record_keys'])
    unresolved={x.record_key for x in proposal.dispositions if x.action=='unresolved'}
    historical={k for k in unresolved if k.startswith(('canonical_viewpoints/','viewpoint_revisions/'))}
    if (not unresolved or not unresolved<=owned|historical or not historical<=pending
            or any(k not in before or k in proposed for k in unresolved)):
        raise ValueError('partial review permits only exact unchanged owned links/pending retired context')
    retired=[p for p in materials['sql_scope']['read_pins'] if p['pin'][2] is True]
    historical_heads={k for k in historical if k.startswith('canonical_viewpoints/')}
    covered=set()
    for k in historical_heads:
        head=before[k];rk=c.key('viewpoint_revisions',head['current_revision_id'])
        candidate=head.get('created_from_candidate_id')
        if (rk not in historical or before[rk]['viewpoint_id']!=head['viewpoint_id']
                or not candidate or ('viewpoint_identity_candidates',candidate) not in pins
                or pins['viewpoint_identity_candidates',candidate][2] is not True):
            raise ValueError('partial review historical context lacks exact retired creation/current CVR proof')
        covered|={k,rk}
    if historical!=covered:
        raise ValueError('partial review historical pending head/revision pairing is incomplete')
    for k in unresolved & owned:
        col,oid=c.split_key(k);p=before[k]
        if pins.get((col,oid))!=[p['revision'],record_content_sha(p),False]:
            raise ValueError('partial review owned connection current pin drift')
    return {'schema_version':'owner_claim_incomplete_joint_review.v1',
        'direction':proof,'claim_id':cid,'proposed_claim_revision':proposed[key]['revision'],
        'proposed_claim_semantic_sha256':semantic_record_sha(proposed[key]),
        'pending_link_keys':sorted(unresolved & owned),'historical_pending_keys':sorted(historical),
        'retired_current_sql_pins':retired,
        'write_authority':False,'another_proposal_allowed':False,'maximum_corrections':1,
        'explanation':'This new Claim draft is source-supported but its owned connection afterimages are incomplete. '
            'The displayed pending connections are CURRENT OLD versions, NOT approved new-version afterimages. '
            'Review the proposed Claim and report the missing complete connections for the existing single correction. '
            'The program already calculates locator Claim SHA BEFORE review from the normalized complete returned Claim; '
            'GPT must return complete links with exact new spans/Evidence and pin, but may use null for locator claim_sha256. '
            'No model must calculate/copy hashes or infer content from a hash. '
            'Historical pending CVPs remain unchanged obligations from the previous action: retired creation Claims '
            'are not missing current members. Evaluate whether THIS Claim completion changes them, not a new '
            'global identity reconstruction. An unchanged THIS-DELTA impact may be retained with exact proof, '
            'while the prior unresolved identity obligation remains separately pending. '
            'No old unresolved decision is changed to pass/retain by this receipt.'}


def execute(store,run_id,direction_path,root,ports):
    from backend.pipeline.claim_issue_management import IssuesDetected,handle_issues
    from backend.pipeline.cvp_sql_flow import binding
    d=read_artifact(direction_path);value=store.get_cvp_group(run_id)
    if d['run_id']!=run_id or d['campaign_sha256']!=value['campaign_sha256']:
        raise ValueError('foreign owner Claim direction')
    # A same identity resume after actual commit must not revalidate its old
    # preimage or replay the issue/model. The original root owns the manifest.
    manifest_paths=list(Path(root).glob('*/manifest.json'))
    for path in manifest_paths:
        from backend.pipeline.cvp_sql_actions import source_action_id,resume_bound_action
        from backend.pipeline.source_reconciliation import Manifest
        from backend.pipeline.source_reconciliation_runner import read,body
        from backend.pipeline import source_reconciliation as c
        manifest=Manifest.model_validate(body(read(path)))
        import json
        context=json.loads(c.bound_bytes(manifest.claim_issue))
        if any(i['report']==binding(direction_path) for i in context['issues']):
            resumed=resume_bound_action(store,run_id,source_action_id(manifest))
            if resumed:return resumed
    state=store.get_record_state('claims',d['claim_id']);validate_direction(d,value,state)
    statement=state['payload']['statement']
    found={'claim_id':d['claim_id'],'spans':[(0,len(statement),statement)],'reason':d['instruction']}
    return handle_issues(store,run_id,IssuesDetected([found],origin='owner',report=binding(direction_path)),
                         root=root,policy_binding=d['policy'],ports=ports)


def history_operations(change_set_id,*,store=None,cursor=None):
    if cursor is None:
        h=store.get_change_history(change_set_id)
        if not h or h['status']!='applied':raise ValueError('Claim management KCS not committed')
        return h['source_kind'],h['operations']
    cursor.execute('SELECT status,source_kind FROM wang_knowledge.change_sets WHERE change_set_id=%s',(change_set_id,))
    state=cursor.fetchone()
    if not state or state[0]!='applied':raise ValueError('Claim management KCS not committed')
    cursor.execute('''SELECT o.operation,o.collection,o.object_id,o.before_revision,o.before_sha256,
        o.after_revision,o.after_sha256,b.payload,a.payload FROM wang_knowledge.change_operations o
        LEFT JOIN wang_knowledge.object_versions b ON
        (b.collection,b.object_id,b.revision)=(o.collection,o.object_id,o.before_revision)
        JOIN wang_knowledge.object_versions a ON
        (a.collection,a.object_id,a.revision)=(o.collection,o.object_id,o.after_revision)
        WHERE o.change_set_id=%s ORDER BY o.operation_index''',(change_set_id,))
    fields=('operation','collection','object_id','before_revision','before_sha256','after_revision','after_sha256','before','after')
    return state[1],[dict(zip(fields,r)) for r in cursor.fetchall()]


def stitch(previous,operations,allowed_keys):
    result=deepcopy(previous)
    links={o['collection']+'/'+o['object_id']:o for o in operations if o['collection']=='viewpoint_claim_links'}
    if not set(links)<=set(allowed_keys):raise ValueError('Claim repair crossed original retained link scope')
    for key,op in links.items():
        old=result.get(key)
        if (not old or op['operation']!='update'
                or (op['before_revision'],op['before_sha256'])!=(old['after_revision'],old['after_sha256'])
                or op['before']!=old['after']
                or record_content_sha(op['after'])!=op['after_sha256']):
            raise ValueError('Claim/link reviewed generations are not an exact contiguous handoff')
        result[key]={**op,'before_revision':old['before_revision'],'before_sha256':old['before_sha256'],'before':old.get('before')}
    return result


def managed_operations(value,*,store=None,cursor=None):
    from backend.pipeline.cvp_sql_flow import check_binding
    from backend.pipeline.cvp_pending_link_management import resolved_pending_operations
    from backend.pipeline.cvp_sql_actions import SOURCE_SCHEMA,validate_source_package
    e=value['reviewed']['evidence'];proof=e['approval']['prior_cvp_management'];check_binding(proof)
    previous=read_artifact(proof['path'])
    if (previous.get('run_id')!=value['run_id'] or previous.get('claim_ids')!=value['claim_ids']
            or previous['reviewed']['evidence']['approval'].get('prior_cvp_management')
            or previous['reviewed']['evidence']['pending_cvp_management_record_keys']!=e['pending_cvp_management_record_keys']
            or not set(previous['change_set_ids'])<=set(value['change_set_ids'])
            or e['action']['materials'].get('prior_cvp_management')!=proof):
        raise ValueError('Claim repair cannot recurse/replace original pending management authority')
    old=resolved_pending_operations(previous,store=store,cursor=cursor)
    package=e['package']['payload'];validate_source_package(package)
    app=package['source_repair_application'];materials=e['action']['materials']
    if not materials.get('owner_claim_completion') or materials['owner_claim_completion']!=e['approval'].get('owner_claim_completion'):
        raise ValueError('Claim repair completion missing exact owner/model scope')
    cid=value['reviewed']['plan']['change_set_id']
    if cid not in value['change_set_ids']:raise ValueError('Claim completion lacks actual KCS')
    kind,ops=history_operations(cid,store=store,cursor=cursor)
    if kind!=SOURCE_SCHEMA:raise ValueError('Claim completion wrong SQL producer')
    direction=read_artifact(materials['owner_claim_completion']['path']);target=direction['claim_id']
    claims=[o for o in ops if o['collection']=='claims']
    if (len(claims)!=1 or claims[0]['object_id']!=target or claims[0]['operation']!='update'
            or any(o['collection'] not in {'claims','viewpoint_claim_links','viewpoint_identity_candidates','viewpoint_identity_decisions'} for o in ops)):
        raise ValueError('owner completion SQL write set crossed Claim/link authority')
    expected={p['claim_id']:p for p in package['claims']}
    if claims[0]['after']!=expected[target] or record_content_sha(claims[0]['after'])!=claims[0]['after_sha256']:
        raise ValueError('new Claim actual history differs from complete reviewed package')
    return stitch(old,ops,set(old))


def finish_dependency_audit(store,run_id):
    """Audit a corrected existing non-campaign Claim without enlarging the group."""
    import os,subprocess
    from backend.pipeline.cvp_sql_flow import PROJECT,binding
    from backend.pipeline.claim_first_management import validate_claim_audit_report
    from backend.pipeline.viewpoint_content_blocked_fallback import verify_saved_full_audit
    value=store.get_cvp_group(run_id);e=value['reviewed']['evidence'];a=e['approval']
    direction=read_artifact(a['owner_claim_completion']['path']);cid=direction['claim_id']
    if cid in value['claim_ids']:return
    proof=a['owner_claim_completion']
    from backend.pipeline.cvp_sql_flow import check_binding
    check_binding(proof)
    state=store.get_record_state('claims',cid);pin=[state['revision'],state['content_sha256'],False]
    root=Path(e['package']['payload']['source_repair_application']['run_root'])/'dependency-claim-audit'
    intent={'schema_version':VERSION,'run_id':run_id,'claim_ids':[cid],'original_group_claim_ids':value['claim_ids'],
            'direction':proof,'pin':pin,'change_set_id':value['reviewed']['plan']['change_set_id'],
            'model':'gemini-3.8-flash','workers':2,'layers':['1','2','3'],'automatic_retry':False}
    path=root/'audit.json'
    if not path.exists():
        if (root/'audit-intent.json').exists():raise ValueError('dependency Claim audit unknown result; no replay')
        write_immutable(root/'audit-intent.json',intent)
        subprocess.run([str(PROJECT/'backend/.venv/bin/python'),'scripts/audit-library.py',
            '--scope','all','--layers','1,2,3','--claim-ids',cid,'--workers','2','--model','gemini-3.8-flash',
            '--no-status-file','--preserve-full-context','--sql-group-scope','--out',str(root)],
            cwd=PROJECT,check=True,env={**os.environ,'KNOWLEDGE_DATABASE_URL':store.database_url})
    if read_artifact(root/'audit-intent.json')!=intent:raise ValueError('dependency Claim audit generation drift')
    audit=read_artifact(path);validate_claim_audit_report(audit,[cid])
    sources=verify_saved_full_audit(path,layers=('3',))
    row=store.get_record_state('claims',cid)
    if [row['revision'],row['content_sha256'],row['retired']]!=pin:raise ValueError('dependency Claim changed after audit')
    receipt={'schema_version':VERSION,'status':'existing_dependency_claim_audited','run_id':run_id,
             'direction':proof,'audit':binding(path),'claim_pin':pin,'claim_id':cid,'source_proofs':sources,
             'original_group_claim_ids':value['claim_ids'],'campaign_membership_changes':0}
    write_immutable(root/'completion.json',receipt)
    return binding(root/'completion.json')


def validate_dependency_completion(value,*,cursor=None):
    from backend.pipeline.cvp_sql_flow import check_binding
    from backend.pipeline.claim_first_management import validate_claim_audit_report
    from backend.pipeline.viewpoint_content_blocked_fallback import verify_saved_full_audit
    e=value['reviewed']['evidence'];proof=e['approval'].get('owner_claim_completion')
    if not proof:return
    check_binding(proof);d=read_artifact(proof['path']);cid=d['claim_id']
    if cid in value['claim_ids']:return
    root=Path(e['package']['payload']['source_repair_application']['run_root'])/'dependency-claim-audit'
    receipt=read_artifact(root/'completion.json')
    if (receipt.get('schema_version')!=VERSION or receipt.get('status')!='existing_dependency_claim_audited'
            or receipt['run_id']!=value['run_id'] or receipt['direction']!=proof or receipt['claim_id']!=cid
            or receipt['original_group_claim_ids']!=value['claim_ids'] or receipt['campaign_membership_changes']!=0):
        raise ValueError('dependency Claim audit cannot alter original group or waive content')
    check_binding(receipt['audit']);audit=read_artifact(receipt['audit']['path']);validate_claim_audit_report(audit,[cid])
    if verify_saved_full_audit(Path(receipt['audit']['path']),layers=('3',))!=receipt['source_proofs']:
        raise ValueError('dependency Claim audit raw/source identity drift')
    if cursor is not None:
        from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
        PostgresKnowledgeStore._assert_transaction_read_pins(cursor,{('claims',cid):tuple(receipt['claim_pin'])})
