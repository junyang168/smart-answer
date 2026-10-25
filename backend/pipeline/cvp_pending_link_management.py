"""Finish an explicitly authorized deferred link write, not a Claim retry.

The original source-owned transaction deliberately did NOT approve/write its
CVP collateral. Reuse the exact unchanged GPT candidate; never replay the
source proposal/correction or treat a partial Opus answer as write authority.
The remaining CVP-domain postimage gets its own single independent review.
"""
from copy import deepcopy
from pathlib import Path

from backend.api.canonical_repository.postgres_store import sha256_json,record_content_sha
from backend.pipeline import source_reconciliation as c
from backend.pipeline.source_reconciliation_runner import body,read,validate_raw,effective_proposal
from backend.pipeline.viewpoint_resolution_runtime import read_artifact

VERSION='wang_cvp_deferred_authorized_link_management_v1'


def candidate_projection(frozen,initial_packet,initial_raw,corrected,*,pending_keys,protected_ids):
    """Only already proposed, per-record approved but deliberately unwritten links.

    An actual semantic rejection of one of these writes is NOT a new budget.
    No CVP core/identity/source/Claim can become a target of this projection.
    """
    c.validate_proposal(frozen,corrected,review_only=True)
    initial=c.index(initial_packet['proposal']['records']);after=c.index(corrected.records)
    before=c.index(frozen['before_records'])
    permissions={p.key:p for p in c.Manifest.model_validate(frozen['manifest']).permissions}
    verdicts=initial_raw['response']['verdicts']
    if len({v['record_key'] for v in verdicts})!=len(verdicts):raise ValueError('duplicate original review verdict')
    reviews={v['record_key']:v for v in verdicts}
    selected={k:p for k,p in after.items() if k.startswith('viewpoint_claim_links/') and k in pending_keys}
    if not selected:return {}
    for key,new in selected.items():
        old=before.get(key);permission=permissions.get(key);verdict=reviews.get(key)
        fields=set(permission.fields) if permission else set()
        if (not old or not permission or permission.create or not fields
                or not fields<={'component_locator','evidence_bindings','effective_state'}
                or key.split('/',1)[1] in protected_ids
                or initial.get(key)!=new or not verdict or verdict['approved'] is not True or verdict['problems']
                or old.get('effective_state')!='active'
                or any(new.get(k)!=old.get(k) for k in ('claim_id','viewpoint_id','link_type',
                    'pinned_claim_revision','validated_against_viewpoint_revision_id','occurrence_refs','supporting_relation_ids'))):
            raise ValueError('deferred link cannot reopen rejected/new/foreign/identity/endpoint content')
        if 'effective_state' in fields and (old['link_type']!='supports' or new.get('effective_state')!='retired'):
            raise ValueError('deferred state permission is exact wrong-support withdrawal only')
    return selected


def prepare_material(store,run_id):
    from backend.pipeline.cvp_retained_impact_resolution import claim_authority,protected_link_ids
    from backend.pipeline.cvp_sql_commit import binding, decoded_pins, pins, pin_rows, check_binding
    from backend.pipeline.source_review_input_repair import check_packet
    from backend.pipeline.source_reconciliation_runner import initial_review_base,build_proposal_packet
    value=store.get_cvp_group(run_id);evidence=value['reviewed']['evidence']
    context,receipt=claim_authority(value)
    root=Path(context['source_terminal_packet']['path']).parent
    frozen=body(read(root/'scoped-materials.json'));c.guard_files(frozen)
    proposal_packet=body(read(root/'proposal.packet.json'))
    if proposal_packet!=build_proposal_packet(frozen):raise ValueError('original source proposal completeness drift')
    first=validate_raw(root,'proposal',proposal_packet)
    initial_packet=body(read(root/'initial_review.packet.json'))
    initial=validate_raw(root,'initial_review',initial_packet)
    check_packet(frozen,first['response'],initial_packet,initial_review_base(frozen,effective_proposal(frozen,first['response'])))
    correction_packet=body(read(root/'correction.packet.json'))
    correction=validate_raw(root,'correction',correction_packet)
    corrected=effective_proposal(frozen,correction['response'])
    selected=candidate_projection(frozen,initial_packet,initial,corrected,
        pending_keys=evidence['pending_cvp_management_record_keys'],protected_ids=protected_link_ids(value))
    if not selected:return None
    old_pins=decoded_pins(receipt['read_pins'])
    targets=sorted({p['viewpoint_id'] for p in selected.values()})
    claims=sorted({p['claim_id'] for p in selected.values()})
    current=store.read_cvp_context(claims,viewpoint_ids=targets)
    for key in selected:
        col,oid=key.split('/',1);row=current.get((col,oid))
        if (not row or row['retired'] or (row['revision'],row['content_sha256'],False)!=old_pins.get((col,oid))
                or row['payload']!=c.index(frozen['before_records'])[key]):
            raise ValueError('deferred candidate current preimage changed; no new budget')
    records={c.key(col,oid):r['payload'] for (col,oid),r in current.items() if not r['retired']}
    manifest=deepcopy(frozen['manifest'])
    # This is the deferred CVP write domain, not another Claim issue or source
    # migration. Permission is an exact subset of the saved owner amendment.
    manifest.pop('claim_issue',None)
    manifest['permissions']=[p for p in manifest['permissions']
        if c.key(p['collection'],p['object_id']) in selected]
    proposal=c.Proposal.model_validate({'records':c.rows(selected),
        'dispositions':[d.model_dump() for d in corrected.dispositions if d.record_key in selected],
        'sentence_dispositions':[],'unadmitted_candidates':[]})
    scoped={**deepcopy(frozen),'manifest':manifest,'before_records':c.rows(records),
        'inventory':{'affected_keys':sorted(selected),'context_keys':sorted(set(records)-set(selected)),
                     'unknown_keys':[]},'sentence_inventory':[]}
    scoped.pop('claim_issue_context',None)
    scoped['update_id']='CVP-PENDING-LINK-'+sha256_json({'run_id':run_id,
        'parent':context['management_action_id'],'keys':sorted(selected)})[:24]
    scoped['sql_scope']={**scoped['sql_scope'],'read_pins':pin_rows(pins(current))}
    source_id=manifest['source_id']
    if c.key('source_documents',source_id) not in records:raise ValueError('complete own source missing')
    c.validate_proposal(scoped,proposal)
    packet=c.review_packet(scoped,proposal)
    origin={n:binding(root/(n+'.raw.json')) for n in ('proposal','initial_review','correction','final_review')}
    for b in origin.values():check_binding(b)
    packet['pending_link_management']={'schema_version':VERSION,'run_id':run_id,
        'source_parent_action_id':context['management_action_id'],'original_responses':origin,
        'original_candidate_sha256':sha256_json(c.rows(selected)),
        'original_claim_layer_completion':context['claim_layer_completion'],
        'target_record_keys':sorted(selected),'other_pending_record_keys':sorted(set(evidence['pending_cvp_management_record_keys'])-set(selected)),
        'additional_gpt_calls':0,'maximum_independent_reviews':1,'correction_calls':0,
        'original_partial_review_is_not_write_authority':True,'human_content_approval':False,
        'instructions':'Independently review ONLY these four or otherwise exact authorized link postimages against complete own bodies and all current target members. '
            'Do not alter Claim/source/CVP core/identity/endpoints or other retained links; other pending facts remain separately pending. '
            'The source-first terminal explicitly deferred these links, not approved them. Original GPT candidates are immutable; any substantive refusal stops, no correction/retry.'}
    return {'schema_version':VERSION,'run_id':run_id,'parent_context':context,
        'original_source_material':binding(root/'scoped-materials.json'),
        'claim_management_resolution':context,'frozen':scoped,'proposal':proposal.model_dump(),'packet':packet,
        'read_pins':pin_rows(pins(current)),'target_record_keys':sorted(selected),
        'viewpoint_ids':evidence['viewpoint_ids'],'original_disposition':evidence.get('disposition')}


PROMPT='''這是 source-first 後尚未執行的 CVP 連接管理，不是第二次 Claim 纠正。
實際寫入僅 proposal.records 與 pending_link_management.target_record_keys，原 GPT 候選原樣提供；來源、Claim、CVP 核心／身份／範圍／端點全不改。
完整自身正文、全部當前目標成員及後像須獨立審查，不能以字段允許或舊部分批准替代語義審查。
同時擴完整等價成分並撤回誤標的並列 supports 時，檢查新有效區間／Evidence 及論證作用；不能將並列結論當論據。
其他 pending 記錄不屬寫入後像，不宣稱已解決、不要求順帶改它們；人已裁定不改的其他連接不得降類。
對本次每個精確 target_record_key 返回 approved/problems/reason，拒絕就停止；零新 GPT、零追加纠正或終審重試。
'''


def scoped_adapter(adapter,packet):
    if not packet.get('pending_link_management'):return adapter
    from copy import copy
    if packet['pending_link_management'].get('schema_version')!=VERSION:
        raise ValueError('unknown pending link review contract')
    result=copy(adapter);result._base_prompt=adapter._base_prompt+'\n'+PROMPT
    result._prompt=adapter._prompt+'\n'+PROMPT
    result.prompt_sha256=sha256_json({'prompt':result._prompt})
    return result


def compile_package(material,raw,root):
    packet=material['packet'];proposal=c.Proposal.model_validate(material['proposal'])
    if not c.validate_review(packet,raw['response']):
        raise ValueError('pending CVP link actual independent refusal; no new correction/retry')
    package=c.compile_package(material['frozen'],proposal,raw)
    package['source_repair_application']={'schema_version':VERSION,'run_root':str(root),
        'materials_sha256':sha256_json(material),'review_sha256':raw['artifact_sha256'],
        'source_parent_action_id':material['parent_context']['management_action_id']}
    return package


def validate_package(package):
    from backend.pipeline.cvp_sql_commit import check_binding
    app=package['source_repair_application'];root=Path(app['run_root'])
    material=body(read(root/'materials.json'))
    if (app.get('schema_version')!=VERSION or sha256_json(material)!=app['materials_sha256']
            or app['source_parent_action_id']!=material['parent_context']['management_action_id']):
        raise ValueError('pending link package authority/material scope drift')
    for b in material['packet']['pending_link_management']['original_responses'].values():check_binding(b)
    check_binding(material['original_source_material'])
    parent=Path(material['parent_context']['source_terminal_packet']['path']).parent
    original=body(read(parent/'scoped-materials.json'))
    initial_packet=body(read(parent/'initial_review.packet.json'))
    initial=validate_raw(parent,'initial_review',initial_packet)
    correction_packet=body(read(parent/'correction.packet.json'))
    corrected=effective_proposal(original,validate_raw(parent,'correction',correction_packet)['response'])
    terminal_packet=body(read(parent/'final_review.packet.json'))
    pending=terminal_packet['source_first_pending_cvp']['pending_record_keys']
    projected=candidate_projection(original,initial_packet,initial,corrected,pending_keys=pending,protected_ids=set())
    proposal=c.Proposal.model_validate(material['proposal'])
    required_permissions=[p for p in original['manifest']['permissions']
        if c.key(p['collection'],p['object_id']) in projected]
    meta=material['packet']['pending_link_management']
    if (c.index(proposal.records)!=projected or material['target_record_keys']!=sorted(projected)
            or meta['target_record_keys']!=sorted(projected)
            or meta['other_pending_record_keys']!=sorted(set(pending)-set(projected))
            or material['frozen']['manifest']['permissions']!=required_permissions
            or meta['additional_gpt_calls']!=0 or meta['maximum_independent_reviews']!=1
            or meta['correction_calls']!=0 or meta['original_partial_review_is_not_write_authority'] is not True
            or material['packet']!={**c.review_packet(material['frozen'],proposal),'pending_link_management':meta}):
        raise ValueError('pending link domain invented/omitted/changed the exact saved candidate or owner scope')
    c.guard_files(material['frozen'])
    raw=validate_raw(root/'model-stages','initial_review',material['packet'])
    if raw['artifact_sha256']!=app['review_sha256'] or package!=compile_package(material,raw,root):
        raise ValueError('pending link package differs from exact complete independent postimage')


def execute(store,run_id,*,root,ports):
    from backend.pipeline.cvp_retained_impact_resolution import claim_authority
    from backend.pipeline.cvp_sql_commit import binding, decoded_pins, commit_package, verified_group_backup
    from backend.pipeline.cvp_sql_actions import resume_bound_action
    from backend.pipeline.source_reconciliation_runner import Runner,write
    saved=store.get_cvp_group(run_id);context,_=claim_authority(saved)
    aid='CVPA-'+sha256_json({'kind':VERSION,'parent':context['management_action_id']})
    root=Path(root)/aid
    store.bind_cvp_execution_root(run_id,identity=aid,root=root)
    if resume_bound_action(store,run_id,aid):return True
    previous=store.get_cvp_action(run_id,aid)
    material=previous['materials'] if previous else prepare_material(store,run_id)
    if material is None:return False
    auth={'schema_version':VERSION,'parent_context':material['parent_context'],
          'source_parent_material':material['original_source_material'],'no_new_gpt':True,
          'one_independent_pending_domain_review':True,'no_correction_or_retry':True}
    action=store.prepare_cvp_action(run_id,action_id=aid,kind='owner_link_correction',
                                    authorization=auth,materials=material)
    write(root/'materials.json',material)
    current_pins=decoded_pins(material['read_pins'])
    with store.connect() as conn,conn.cursor() as cursor:store._assert_transaction_read_pins(cursor,current_pins)
    stages=Runner(root,ports);stages.root=root/'model-stages'
    raw=stages.call('initial_review',material['packet'])
    package=compile_package(material,raw,root);validate_package(package)
    write(root/'reviewed-package.json',package)
    approval={**saved['reviewed']['evidence']['approval'],
        'pending_link_material':binding(root/'materials.json'),
        'pending_link_review':binding(stages.root/'initial_review.raw.json')}
    sources=[{'path':b['path'],'file_sha256':b['file_sha256']} for b in
        [material['frozen']['after'][0]['file'],*[s['file'] for s in material['frozen'].get('context_sources',[])]]]
    commit_package(store,run_id,package=package,approval=approval,authorization=auth,
        backup=verified_group_backup(root,package,ports),sources=sources,read_pins=current_pins,
        reason='complete independent CVP-domain review of originally deferred authorized link postimages; no Claim retry',
        actor='system',source_kind=VERSION,viewpoint_ids=material['viewpoint_ids'],
        disposition=material['original_disposition'],action=action)
    return True


def resolved_pending_operations(value,store=None,cursor=None):
    """Exact changed subset for completion; never a generic pending-clear bit."""
    evidence=value['reviewed']['evidence'];approval=evidence['approval']
    if approval.get('prior_cvp_management'):
        from backend.pipeline.claim_owner_revision import managed_operations
        return managed_operations(value,store=store,cursor=cursor)
    if not approval.get('pending_link_material'):return {}
    from backend.pipeline.cvp_sql_commit import check_binding
    check_binding(approval['pending_link_material']);check_binding(approval['pending_link_review'])
    material=body(read(Path(approval['pending_link_material']['path'])))
    package=evidence['package']['payload'];validate_package(package)
    action=evidence['action']
    if (evidence['action']['materials']!=material and action.get('materials_sha256')!=sha256_json(material)) or material['run_id']!=value['run_id']:
        raise ValueError('pending link action/resolution foreign material')
    cid=value['reviewed']['plan']['change_set_id']
    if cid not in value['change_set_ids'] or value['reviewed']['plan']['source_kind']!=VERSION:
        raise ValueError('pending link actual KCS missing')
    if cursor is not None:
        cursor.execute('SELECT status,source_kind FROM wang_knowledge.change_sets WHERE change_set_id=%s',(cid,))
        if cursor.fetchone()!=('applied',VERSION):raise ValueError('pending link SQL transaction not committed')
        cursor.execute('''SELECT o.operation,o.collection,o.object_id,o.before_revision,o.before_sha256,
            o.after_revision,o.after_sha256,a.payload FROM wang_knowledge.change_operations o
            JOIN wang_knowledge.object_versions a ON
            (a.collection,a.object_id,a.revision)=(o.collection,o.object_id,o.after_revision)
            WHERE o.change_set_id=%s ORDER BY o.operation_index''',(cid,))
        fields=('operation','collection','object_id','before_revision','before_sha256','after_revision','after_sha256','after')
        ops=[dict(zip(fields,r)) for r in cursor.fetchall()]
    else:
        history=store.get_change_history(cid)
        if not history or history['status']!='applied' or history['source_kind']!=VERSION:
            raise ValueError('pending link actual history missing')
        ops=history['operations']
    selected={op['collection']+'/'+op['object_id']:op for op in ops if op['collection']=='viewpoint_claim_links'}
    if set(selected)!=set(material['target_record_keys']) or any(op['operation']!='update' for op in selected.values()):
        raise ValueError('pending link actual write denominator drift')
    expected={p['viewpoint_claim_link_id']:p for p in package['viewpoint_claim_links']}
    if any(op['after']!=expected[op['object_id']] or record_content_sha(op['after'])!=op['after_sha256'] for op in selected.values()):
        raise ValueError('pending link actual afterimage/history differs from independent review')
    return selected


def guard_repeated_semantic_repair(value,packet,report):
    """A newly reviewed/applied link rejected again is a content stop, not a loop."""
    if 'reviewed' not in value:
        certificate=value.get('import_certificate',{})
        if (value.get('materials')=={'origin':'explicit_historical_import'}
                and certificate.get('status')=='verified_historical_completion'
                and certificate.get('change_set_ids')==value.get('change_set_ids')):
            # A verified historical import has no new pending-link management
            # write domain. This hook does not resolve its current objection;
            # ordinary adjudication/repair/completion gates still apply.
            return
        raise ValueError('semantic repair guard missing reviewed SQL authority')
    approval=value['reviewed']['evidence']['approval']
    if not approval.get('pending_link_material'):return
    from backend.pipeline.cvp_sql_commit import check_binding
    check_binding(approval['pending_link_material'])
    material=body(read(Path(approval['pending_link_material']['path'])))
    targets={k.split('/',1)[1] for k in material['target_record_keys']}
    actions={a['item_id']:a for a in report['actions']}
    blocked=[i['item_id'] for i in packet['items']
             if actions[i['item_id']]['verdict']!='audit_objection_rejected'
             and targets & {p['viewpoint_claim_link_id'] for p in i.get('connections',[])}]
    if blocked:
        raise ValueError('current independently reviewed/applied link has a fresh upheld/unresolved content objection; '
                         'no second automatic link repair: '+', '.join(blocked))
