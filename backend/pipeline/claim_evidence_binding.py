"""One eligible Evidence's missing own-source bindings, ordinary source stages.

An explicit repair direction is scope, never approval of generated postimages.
Claims, qualification, relationships, old fragments and all words stay unchanged.
"""
import json
from backend.pipeline import source_reconciliation as c
from backend.pipeline.claim_anchor_qualification import FRAGMENT_FIELDS
from backend.api.canonical_repository.postgres_store import sha256_json, record_content_sha

VERSION='wang_claim_evidence_binding_v1'
FIELDS={'source_fragment_id','source_fragment_ids'}


def normalized_proposal(frozen,response):
    from backend.pipeline.claim_anchor_qualification import normalized_proposal as normalize_fragments
    proposal=normalize_fragments(frozen,response)
    before=c.index(frozen['before_records']);dispositions={d.record_key:d.action for d in proposal.dispositions}
    # A full readonly before row is redundant model presentation, NOT a write.
    # Preserve the original raw, exact retain verdict/reason and full before
    # packet. Only byte-equal current rows with explicit retain are projected.
    proposal.records=[r for r in proposal.records if not (
        dispositions.get(c.key(r.collection,r.object_id))=='retain'
        and r.payload==before.get(c.key(r.collection,r.object_id)))]
    return proposal


def scoped_adapter(original,packet,stage):
    if not packet.get('manifest',{}).get('evidence_binding_repair'):return original
    from copy import copy
    from backend.pipeline.deferred_claim_repair import JsonProposal
    result=copy(original)
    suffix='\n本次只补一条既有 eligible Evidence 的自身来源绑定。读取完整自身正文及全部共享 Claims，保留证据正文、资格、Claim、关系、旧片段及所有旧绑定。不得重开其他未决管理。对全部 affected_keys 精确 retain/update/impact_only；新增片段各有 disposition。只有新增自身精确片段和该 Evidence 的 source_fragment_id(s) 可写。独立审核须确认新增引用充分支持该 Evidence 全部复合内容；用户授权不是后像批准。'
    if stage in {'proposal','correction'}:
        result._response_model=JsonProposal;result._schema_name='eligible_evidence_binding_json_records_v1'
        suffix+=' 完整后像用 records.payload_json JSON字符串无损返回，unadmitted_candidates_json 为 []。'
    result._base_prompt=original._base_prompt+suffix;result._prompt=original._prompt+suffix
    result.prompt_sha256=sha256_json({'prompt':result._prompt})
    result.generation_config_sha256=sha256_json({'base':original.generation_config_sha256,'binding_profile':VERSION,'stage':stage})
    return result


def prepare_manifest(store,run_id,evidence_id,quote,root):
    """Seal explicit repair scope, never a proposed Evidence postimage."""
    from pathlib import Path
    from backend.pipeline.source_reconciliation_runner import write, binding
    from backend.pipeline.cvp_sql_actions import read_source_manifest
    root=Path(root);path=root/'manifest.json'
    if path.exists():return read_source_manifest(path)
    group=store.get_cvp_group(run_id);row=store.get_record_state('evidence_steps',evidence_id)
    if not group or not row or row['retired']:raise ValueError('Evidence binding target/group missing')
    ev=row['payload'];fragment=store.get_record('source_fragments',ev['source_fragment_id'])
    sid=fragment['source_id'];doc=store.get_record('source_documents',sid)
    source=c.FileBinding(path=doc['source_path'],file_sha256=doc['source_file_sha256'])
    c.source_snapshot(source,source=doc)
    permissions=[c.Permission(collection='evidence_steps',object_id=evidence_id,fields=sorted(FIELDS)),
        c.Permission(collection='source_fragments',object_id='FR-OWN-'+sha256_json({'evidence':evidence_id,'pin':row['content_sha256']})[:20],
                     fields=sorted(FRAGMENT_FIELDS),create=True,parent_id=sid)]
    scope={'schema_version':VERSION,'authorized_by':'owner','authorization_quote':quote,'human_content_approval':False,
           'maximum_corrections':1,'source_id':sid,'evidence_id':evidence_id,'claim_ids':ev['produced_claim_ids'],
           'before_pin':[row['revision'],row['content_sha256'],False]}
    write(root/'direction.json',scope)
    write(root/'authorization.json',{'source_id':sid,'authorization_id':'eligible-evidence-binding-'+sha256_json(scope),
        'authorization_quote':quote,'human_content_approval':False,'permissions':[p.model_dump() for p in permissions]})
    definition=store.get_cvp_campaign(group['campaign_sha256'])
    progress={r['group_key']:r for r in store.cvp_campaign_progress(group['campaign_sha256'])}
    groups=[c.Group(group_key=g['group_key'],claim_ids=g['claim_ids'],
        revisit_required=progress.get(g['group_key'],{}).get('revisit_required',False),
        independent_audit_complete=progress.get(g['group_key'],{}).get('independent_audit_complete',True)) for g in definition['groups']]
    write(root/'membership.json',{'groups':[{'group_key':g.group_key,'claim_ids':g.claim_ids} for g in groups]})
    manifest=c.Manifest(source_id=sid,before_source=source,current_sources=[source],authorization=binding(root/'authorization.json'),
        authorized_paragraph_keys=[],permissions=permissions,groups=groups,campaign_claim_ids=[i for g in groups for i in g.claim_ids],
        accepted_membership=binding(root/'membership.json'),created_at='owner-authorized-evidence-binding',
        evidence_binding_repair=binding(root/'direction.json'))
    states={c.key(col,oid):r['payload'] for (col,oid),r in store.read_affected_context([('evidence_steps',evidence_id)]).items() if not r['retired']}
    direction(manifest,states)
    write(path,manifest.model_dump())
    return manifest.model_dump()


def finish_claim_audit(store,run_id):
    """Audit all shared owners, not a replacement for group/CVP completion."""
    import os,subprocess
    from pathlib import Path
    from backend.pipeline.cvp_sql_flow import PROJECT,check_binding,binding
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact,write_immutable
    from backend.pipeline.claim_first_management import validate_claim_audit_report
    from backend.pipeline.viewpoint_content_blocked_fallback import verify_saved_full_audit
    from backend.pipeline.source_reconciliation_runner import read,body
    value=store.get_cvp_group(run_id);e=value['reviewed']['evidence'];app=e['package']['payload']['source_repair_application']
    check_binding(app['materials']);material=body(read(Path(app['materials']['path'])))
    m=c.Manifest.model_validate(material['manifest']);d=direction(m,c.index(material['before_records']),material.get('physical_snapshots'))
    c.guard_files(material);ids=sorted(d['claim_ids']);claim_pins={}
    for cid in ids:
        r=store.get_record_state('claims',cid)
        if r['retired'] or r['payload']!=c.index(material['before_records'])[c.key('claims',cid)]:
            raise ValueError('Evidence audit original shared owner drift')
        claim_pins[cid]=[r['revision'],r['content_sha256'],False]
    root=Path(app['run_root'])/'shared-owner-claim-audit';path=root/'audit.json'
    intent={'schema_version':VERSION,'run_id':run_id,'claim_ids':ids,'claim_pins':claim_pins,
        'change_set_id':value['reviewed']['plan']['change_set_id'],'materials':app['materials'],
        'model':'gemini-3.8-flash','workers':2,'layers':['1','2','3'],'automatic_retry':False}
    if not path.exists():
        if (root/'audit-intent.json').exists():raise ValueError('Evidence owner audit outcome unknown; no retry')
        write_immutable(root/'audit-intent.json',intent)
        subprocess.run([str(PROJECT/'backend/.venv/bin/python'),'scripts/audit-library.py','--scope','all','--layers','1,2,3',
            '--claim-ids',','.join(ids),'--workers','2','--model','gemini-3.8-flash','--no-status-file',
            '--preserve-full-context','--sql-group-scope','--out',str(root)],cwd=PROJECT,check=True,
            env={**os.environ,'KNOWLEDGE_DATABASE_URL':store.database_url})
    if read_artifact(root/'audit-intent.json')!=intent:raise ValueError('Evidence audit intent drift')
    validate_claim_audit_report(read_artifact(path),ids)
    proofs=verify_saved_full_audit(path,layers=('3',))
    with store.connect() as conn,conn.cursor() as cursor:
        store._assert_transaction_read_pins(cursor,{('claims',cid):tuple(pin) for cid,pin in claim_pins.items()})
    receipt={**intent,'status':'source_binding_shared_owners_audited_cvp_pending','audit':binding(path),'source_proofs':proofs,
             'group_complete':False,'campaign_membership_changes':0}
    write_immutable(root/'completion.json',receipt)
    return binding(root/'completion.json')


def coverage_context(store,saved,group):
    """Hand an applied, audited Evidence repair to ordinary CVP coverage.

    Shared owners are impact dependencies, not new campaign members. The
    original group roster and every existing pending impact remain intact.
    This authenticates source approval; it grants no CVP content approval.
    """
    from pathlib import Path
    from backend.pipeline.cvp_sql_flow import binding,check_binding,decoded_pins
    from backend.pipeline.cvp_sql_actions import validate_source_package
    from backend.pipeline.source_reconciliation_runner import read,body
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    from backend.pipeline.claim_first_management import validate_claim_audit_report
    from backend.pipeline.viewpoint_content_blocked_fallback import verify_saved_full_audit
    evidence=saved['reviewed']['evidence'];package=evidence['package']['payload']
    app=package.get('source_repair_application',{})
    if not app:return None
    check_binding(app['materials']);material=body(read(Path(app['materials']['path'])))
    if not material['manifest'].get('evidence_binding_repair'):return None
    if (saved['checkpoint']!='applied' or app.get('run_id')!=saved['run_id']
            or saved['claim_ids']!=group['claim_ids']):
        raise ValueError('Evidence coverage original group/commit drift')
    validate_source_package(package)
    manifest=c.Manifest.model_validate(material['manifest'])
    d=direction(manifest,c.index(material['before_records']),material.get('physical_snapshots'))
    pending_ids=sorted(set(group['claim_ids'])|set(d['claim_ids']))
    if sorted(evidence.get('pending_cvp_claim_ids',[]))!=pending_ids:
        raise ValueError('Evidence coverage pending shared-owner denominator drift')
    root=Path(app['run_root']);receipt_path=root/'shared-owner-claim-audit/completion.json'
    receipt=read_artifact(receipt_path)
    plan=saved['reviewed']['plan'];history=store.get_change_history(plan['change_set_id'])
    if (not history or history['status']!='applied'
            or plan['change_set_id'] not in saved['change_set_ids']
            or history['fingerprint_sha256']!=plan['fingerprint_sha256']
            or history['source_sha256']!=plan['source_sha256']
            or len(history['operations'])!=len(plan['operations'])
            or receipt.get('run_id')!=saved['run_id']
            or receipt.get('change_set_id')!=plan['change_set_id']
            or receipt.get('materials')!=app['materials']
            or receipt.get('claim_ids')!=sorted(d['claim_ids'])
            or receipt.get('status')!='source_binding_shared_owners_audited_cvp_pending'
            or receipt.get('group_complete') is not False):
        raise ValueError('Evidence coverage lacks exact committed source/owner audit')
    expected=decoded_pins(material['sql_scope']['read_pins'])
    for op,h in zip(plan['operations'],history['operations']):
        key=op['collection'],op['object_id']
        live=store.get_record_state(*key)
        if (op['collection'] not in {'evidence_steps','source_fragments'}
                or not live or live['retired']
                or h['collection']!=key[0] or h['object_id']!=key[1]
                or h['after']!=op['payload'] or live['payload']!=op['payload']
                or h['after_revision']!=op['after_revision'] or live['revision']!=op['after_revision']
                or h['after_sha256']!=op['after_sha256'] or live['content_sha256']!=op['after_sha256']
                or record_content_sha(live['payload'])!=op['after_sha256']):
            raise ValueError('Evidence coverage actual current/history postimage drift')
        expected[key]=(live['revision'],live['content_sha256'],False)
    for cid in d['claim_ids']:
        row=store.get_record_state('claims',cid)
        if (not row or row['retired'] or receipt['claim_pins'].get(cid)!=
                [row['revision'],row['content_sha256'],False]):
            raise ValueError('Evidence coverage shared Claim audit version drift')
    dependencies=[]
    for cid in sorted(set(d['claim_ids'])-set(group['claim_ids'])):
        context=store.read_cvp_context([cid])
        links=[row for (col,_),row in context.items() if col=='viewpoint_claim_links'
               and not row['retired'] and row['payload'].get('claim_id')==cid
               and row['payload'].get('effective_state')=='active']
        if links:
            raise ValueError('shared owner has existing CVP impacts; requires complete link management')
        dependencies.append({'claim_id':cid,'claim_pin':receipt['claim_pins'][cid],
            'active_cvp_links':[],'source_audited':True,'new_coverage_authority':False})
    with store.connect() as conn,conn.cursor() as cursor:
        store._assert_transaction_read_pins(cursor,expected)
    check_binding(receipt['audit']);audit=read_artifact(receipt['audit']['path'])
    validate_claim_audit_report(audit,d['claim_ids'])
    verify_saved_full_audit(Path(receipt['audit']['path']),layers=('3',))
    return {'schema_version':'wang_sequential_source_cvp_handoff_v1',
        'management_action_id':evidence['action']['action_id'],
        'prior_reviewed_sha256':saved['reviewed_sha256'],
        'source_package_sha256':evidence['package']['sha256'],
        'source_terminal_review':evidence['approval']['terminal_review'],
        'shared_owner_audit':binding(receipt_path),'source_change_set_id':plan['change_set_id'],
        'original_group_claim_ids':list(group['claim_ids']),
        'coverage_claim_ids':list(group['claim_ids']),'audited_dependency_impacts':dependencies,
        'pending_existing_cvp_record_keys':evidence.get('pending_cvp_management_record_keys',[]),
        'existing_cvp_content_approved':False,'campaign_membership_changes':0,
        'old_proposals_and_budgets_preserved':True,
        'instructions':'Use the current approved source graph for normal coverage of original Claims. '
            'Exact shared-owner dependencies have completed source audits and no active CVP links. '
            'They are not added to the campaign roster or required to produce new CVPs by this handoff. '
            'Existing impacts remain pending, not modified or approved by this source audit.'}


def direction(manifest,states,snapshots=None):
    d=json.loads(c.bound_bytes(manifest.evidence_binding_repair,snapshots))
    if (d.get('schema_version')!=VERSION or d.get('authorized_by')!='owner'
            or not d.get('authorization_quote') or d.get('human_content_approval') is not False
            or d.get('maximum_corrections')!=1 or d.get('source_id')!=manifest.source_id
            or d.get('artifact_sha256')!=sha256_json({k:v for k,v in d.items() if k!='artifact_sha256'})):
        raise ValueError('Evidence binding scope/seal drift')
    k=c.key('evidence_steps',d['evidence_id']);ev=states[k]
    if (ev.get('support_eligibility') not in {'eligible','eligible_with_label'}
            or ev.get('review_status') in c.HUMAN_HOLDS|{'human_hold','rejected','withheld_unreviewed'}
            or ev.get('stance')!='endorsed' or ev.get('speaker')!='professor'
            or d['before_pin']!=[ev['revision'],record_content_sha(ev),False]
            or sorted(ev['produced_claim_ids'])!=sorted(d['claim_ids'])):
        raise ValueError('Evidence binding foreign/held/unqualified/stale generation')
    for cid in d['claim_ids']:
        claim=states[c.key('claims',cid)]
        if d['evidence_id'] not in claim['evidence_step_ids']:
            raise ValueError('Evidence binding ownership is not reciprocal')
    updates=[p for p in manifest.permissions if not p.create]
    creates=[p for p in manifest.permissions if p.create]
    if (len(updates)!=1 or updates[0].key!=k or set(updates[0].fields)!=FIELDS
            or len(creates)!=1 or creates[0].collection!='source_fragments'
            or creates[0].parent_id!=manifest.source_id or set(creates[0].fields)!=FRAGMENT_FIELDS):
        raise ValueError('Evidence binding changed words/qualification/ownership/graph permission')
    return d


def inventory(manifest,states,snapshots=None):
    d=direction(manifest,states,snapshots)
    _,reverse=c.dependency_index(states)
    affected={c.key('evidence_steps',d['evidence_id'])}
    # Direct business consumers are explicitly reviewed without granting writes.
    affected.update(reverse[c.key('evidence_steps',d['evidence_id'])])
    return {'affected_keys':sorted(affected),'context_keys':sorted(set(states)-affected),'unknown_keys':[]}


def validate_delta(frozen,old,new,key):
    m=c.Manifest.model_validate(frozen['manifest']);d=direction(m,c.index(frozen['before_records']),frozen.get('physical_snapshots'))
    if key==c.key('evidence_steps',d['evidence_id']):
        if ({k:v for k,v in c._semantic(old).items() if k not in FIELDS}
                !={k:v for k,v in c._semantic(new).items() if k not in FIELDS}):
            raise ValueError('Evidence binding altered content or qualification')
        before=set(old.get('source_fragment_ids',[]))|({old['source_fragment_id']} if old.get('source_fragment_id') else set())
        after=set(new.get('source_fragment_ids',[]))|({new['source_fragment_id']} if new.get('source_fragment_id') else set())
        if not before<after:raise ValueError('Evidence binding must add own quotes and retain every old binding')
    elif key.startswith('source_fragments/'):
        if old or new.get('source_id')!=m.source_id:raise ValueError('Evidence binding cannot change old/foreign fragments')
    else:raise ValueError('Evidence binding cannot modify other business records')


def validate_closure(frozen,proposal,post):
    m=c.Manifest.model_validate(frozen['manifest']);d=direction(m,c.index(frozen['before_records']),frozen.get('physical_snapshots'))
    ev=post[c.key('evidence_steps',d['evidence_id'])]
    ids=set(ev.get('source_fragment_ids',[]))|({ev['source_fragment_id']} if ev.get('source_fragment_id') else set())
    created={r.object_id for r in proposal.records if r.collection=='source_fragments'}
    if not created or not created<=ids:raise ValueError('Evidence binding has orphan/missing own fragments')
    src=frozen['after'][0]
    for fid in created:
        p=post[c.key('source_fragments',fid)]
        text=src['paragraphs'].get(p.get('paragraph_key'),{}).get('text','')
        if len(c.containing_anchor_fragments([p],p.get('verbatim_excerpt',''),text,m.source_id,p.get('paragraph_key'),src['body_sha256']))!=1:
            raise ValueError('new Evidence fragment lacks exact physical own quote proof')
