"""Pure final CVP2 authority/readback checks shared with the SQL transaction."""
from __future__ import annotations

from . import cvp_management as domain
from .cvp_recall import seal, verify
from .viewpoint_foundation import sha256_json

VERSION=domain.VERSION


def validate_relationship_scope(cursor,original,bundle):
    """Current packets bind every candidate's direct relations, not neighbours' relations."""
    scope=bundle.get('relationship_scope')
    if scope not in (None,'candidate_one_hop_v1'):
        raise ValueError('unknown CVP2 relationship material scope')
    primary=bundle.get('candidate_viewpoint_ids',[oid for col,oid in original if col=='canonical_viewpoints'])
    def current(vids):
        return {r[0] for r in cursor.execute("""SELECT object_id FROM wang_knowledge.objects
            WHERE collection='viewpoint_relations' AND retired_at IS NULL
            AND payload->>'effective_state'='active'
            AND (payload->>'source_viewpoint_id'=ANY(%s) OR payload->>'target_viewpoint_id'=ANY(%s))""",
            (vids,vids)).fetchall()}
    expected={oid for (col,oid),row in original.items() if col=='viewpoint_relations'
              and row['payload'].get('effective_state')=='active'}
    if expected==current(primary):return
    # Historical unversioned inputs used maintenance-only relationships.
    # Also accept their already complete candidate cut, without upgrading it.
    if scope is None and expected==current(bundle.get('maintenance_viewpoint_ids',[])):return
    raise ValueError('CVP2 necessary candidate relationship scope changed')


def _stored_package_records(package):
    """Use the same typed postimage that the generic ChangeSet importer stores.

    Historical packages may omit newly optional CVP summary fields; the
    importer materializes those defaults without changing the approved input.
    """
    return {(col,row[domain.KNOWLEDGE_COLLECTIONS[col][1]]):
            domain.KNOWLEDGE_COLLECTIONS[col][0].model_validate(row).model_dump(mode='json')
            for col,rows in package.items() if col in domain.KNOWLEDGE_COLLECTIONS for row in rows}


def validate_evidence(evidence):
    required={'request','bundle','candidate','approval','proposal_raw','review_raw','previous',
              'prior_findings','provenance','call_keys','round_id'}
    if set(evidence) not in (required, required | {'proposal_origin'}):
        raise ValueError('complete CVP2 approval evidence required')
    request=domain.Request.model_validate(evidence['request'])
    verify(evidence['bundle'],domain.MATERIAL_VERSION)
    verify(evidence['candidate'],VERSION+'_candidate')
    verify(evidence['approval'],VERSION+'_review')
    proposal=evidence['proposal_raw'];review=evidence['review_raw']
    if (proposal.get('result')!='candidate' or review.get('result')!='review' or
            proposal.get('candidate') is None or review.get('review') is None or
            proposal.get('context_requests') or proposal.get('member_requests') or
            review.get('context_requests') or review.get('member_requests')):
        raise ValueError('context requests are not candidate approval')
    origin=evidence.get('proposal_origin')
    if origin is None:
        domain.revalidate_candidate(request,evidence['bundle'],evidence['candidate'],proposal['candidate'],
            previous=evidence['previous'],prior_findings=evidence['prior_findings'])
    else:
        if (set(origin)!={'step_id','proposal_path','bundle','previous','prior_findings'} or
                origin['prior_findings']!=evidence['prior_findings']):
            raise ValueError('expanded review lacks exact original proposal lineage')
        domain.verify(origin['bundle'],domain.MATERIAL_VERSION)
        old=origin['bundle'];new=evidence['bundle']
        current={(row['collection'],row['object_id']):row for row in new['records']}
        current_sources={row['source_id']:row for row in new['sources']}
        if (old['claim_input']!=new['claim_input'] or
                any(current.get((row['collection'],row['object_id']))!=row for row in old['records']) or
                any(old.get(field)!=new.get(field) for field in
                    ('human_constraints','quality_findings','maintenance_viewpoint_ids')) or
                any(current_sources.get(row['source_id'],{}).get('file_sha256')!=row['file_sha256']
                    for row in old['sources'])):
            raise ValueError('direct review requires only read-only comparison expansion')
        original=domain.compile_candidate(request,origin['bundle'],proposal['candidate'],
            previous=origin['previous'],prior_findings=origin['prior_findings'])
        rebound=domain.rebind_candidate(request,origin['bundle'],evidence['bundle'],original,
            prior_findings=evidence['prior_findings'])
        if rebound!=evidence['candidate'] or evidence['previous']!=rebound:
            raise ValueError('expanded review changed the original proposal without GPT revision')
    approval=domain.validate_review(request,evidence['bundle'],evidence['candidate'],review['review'],
                                    prior_findings=evidence['prior_findings'])
    if approval!=evidence['approval'] or approval.get('approved') is not True:
        raise ValueError('CVP2 lacks exact complete final Opus approval')
    package=domain.finalize_package(request,evidence['bundle'],evidence['candidate'],approval,evidence['provenance'])
    package['cvp_management_application']={'schema_version':VERSION,'round_id':evidence['round_id'],
        'request_sha256':sha256_json(request.model_dump(mode='json')),
        'candidate_sha256':evidence['candidate']['artifact_sha256'],
        'approval_sha256':approval['artifact_sha256']}
    return package


def validate_sql_review(store,cursor,step,evidence,plan):
    """No file/model IO: match actual durable call facts and current SQL pins."""
    if step.get('kind')!='cvp_management_round' or step.get('schema_version')!=VERSION:
        raise ValueError('CVP2 approval cannot borrow a historical group')
    proof=evidence['cvp_management']
    from .cvp_management_store import _load
    parent=_load(cursor,step['parent_run_id'])
    if parent['result'].get('status')=='superseded':
        raise ValueError('superseded CVP2 task cannot review or write')
    if (proof['round_id'] not in parent['units'][step['group_key']]['steps'] or
            proof['request']!=parent['request'] or proof['bundle']!=step['materials']['bundle']):
        raise ValueError('CVP2 review changed original task or prepared material')
    expected_round='CVPMR-'+sha256_json({'parent':step['parent_run_id'],'group':step['group_key'],
        'generation':step['generation'],'ordinal':step['ordinal']})
    if (proof['round_id']!=expected_round or
            proof['previous']!=step['materials']['previous'] or
            proof['prior_findings']!=step['materials']['prior_findings'] or
            evidence['authorization']!=parent['request']['authorization']):
        raise ValueError('CVP2 review changed its frozen lineage or scoped authority')
    origin=proof.get('proposal_origin')
    if origin!=step['materials'].get('proposal_origin'):
        raise ValueError('CVP2 expanded review changed its frozen proposal origin')
    if origin is not None:
        if origin['step_id'] not in parent['units'][step['group_key']]['steps']:
            raise ValueError('CVP2 expanded review borrowed a foreign proposal step')
        source=_load(cursor,origin['step_id'])
        if (source['parent_run_id']!=step['parent_run_id'] or
                source['group_key']!=step['group_key'] or
                source['generation']!=step['generation'] or
                source['ordinal']>=step['ordinal'] or
                source['materials']['bundle']!=origin['bundle'] or
                source['materials']['previous']!=origin['previous'] or
                source['materials']['prior_findings']!=origin['prior_findings'] or
                # A completed task may have an authenticated trial proposal
                # imported at its original path rather than step_root.
                origin['proposal_path']!=source['materials'].get(
                    'proposal_path', source['materials']['step_root']+'/proposal.json')):
            raise ValueError('CVP2 expanded review has no exact earlier proposal')
    for field in ('human_constraints','quality_findings'):
        facts=parent.get(field,[])
        if field=='human_constraints':facts=[c for c in facts if c['group_key']==step['group_key']]
        else:
            from .cvp_management_store import _quality_findings
            facts=_quality_findings(cursor,[(r['collection'],r['object_id']) for r in proof['bundle']['records']])
        if proof['bundle'].get(field,[])!=facts:
            raise ValueError('CVP2 approval omitted a current task constraint/finding')
    package=validate_evidence(proof)
    domain.validate_summary_afterimage(proof['request'],proof['bundle'],proof['candidate'],package)
    if (package!=evidence['package']['payload'] or sha256_json(package)!=plan.source_sha256 or
            evidence['package']['sha256']!=plan.source_sha256):
        raise ValueError('CVP2 reviewed package is not the complete approved afterimage')
    proposal_material=origin['bundle'] if origin is not None else proof['bundle']
    proposal_previous=origin['previous'] if origin is not None else proof['previous']
    proposal_findings=origin['prior_findings'] if origin is not None else proof['prior_findings']
    packets={'gpt':domain.proposal_packet(proof['request'],proposal_material,previous=proposal_previous,
                                        prior_findings=proposal_findings),
             'opus':domain.review_packet(proof['request'],proof['bundle'],proof['candidate'],
                                        prior_findings=proof['prior_findings'])}
    for role,name in [('gpt','proposal_raw'),('opus','review_raw')]:
        slot=parent['call_reservations'].get(proof['call_keys'][role])
        if (not slot or slot['state']!='returned' or slot['role']!=role or
                slot['returned']['response']!=proof[name] or
                slot['intent']['request_payload_sha256']!=sha256_json(packets[role])):
            raise ValueError('CVP2 approval lacks the exact actually returned role response')
        if role=='gpt' and origin is not None and slot['intent']['cache_path']!=origin['proposal_path']:
            raise ValueError('CVP2 expanded review borrowed a different proposal dispatch')
        if (role=='gpt' and origin is not None and
                origin['proposal_path']!=source['materials']['step_root']+'/proposal.json' and
                slot.get('origin',{}).get('kind')!='verified_trial_import'):
            raise ValueError('CVP2 external proposal lacks verified trial provenance')
        model,backend=(('gpt-6.1-sol','codex_subscription') if role=='gpt' else
                       ('claude-opus-5-5','claude_subscription'))
        if (slot['intent'].get('model_id'),slot['intent'].get('backend'))!=(model,backend):
            raise ValueError('CVP2 approval borrowed a different model/backend role')
        if proof['provenance']['proposal_model' if role=='gpt' else 'review_model']!=model:
            raise ValueError('CVP2 provenance does not disclose the actual required role')
        if slot['returned']['artifact_sha256']!=proof['provenance'][
                'proposal_artifact_sha256' if role=='gpt' else 'review_artifact_sha256']:
            raise ValueError('CVP2 model provenance differs from its durable call')
    # Equality with a recompiled package prevents model-derived postimages from
    # changing after review. Store plan validation still owns exact DML/history.
    from .postgres_store import record_content_sha,stored_operation_payload
    expected=_stored_package_records(package)
    operations={(op.collection,op.object_id):op for op in plan.operations}
    required=set()
    for key,payload in expected.items():
        actual=cursor.execute("SELECT content_sha256,retired_at FROM wang_knowledge.objects WHERE collection=%s AND object_id=%s",key).fetchone()
        if not actual or actual[0]!=record_content_sha(payload) or actual[1] is not None:required.add(key)
    if set(operations)!=required:
        raise ValueError('CVP2 transaction omitted or added an approved semantic write')
    for key,op in operations.items():
        if (op.operation not in {'create','update'} or
                record_content_sha(stored_operation_payload(op))!=record_content_sha(expected[key])):
            raise ValueError('CVP2 transaction postimage differs from the approved afterimage')
        if op.collection in {'claims','evidence_steps','source_documents','source_fragments'}:
            raise ValueError('CVP2 cannot modify Claim, Evidence or professor sources')
    original={(r['collection'],r['object_id']):r for r in proof['bundle']['records']}
    for key,row in original.items():
        actual=cursor.execute('''SELECT revision,content_sha256,retired_at FROM wang_knowledge.objects
                                WHERE collection=%s AND object_id=%s''',key).fetchone()
        if not actual or (actual[0],actual[1],actual[2] is not None)!=(row['revision'],row['content_sha256'],bool(row.get('retired'))):
            raise ValueError('CVP2 selected input or necessary member changed')
    wanted={tuple(key) for key in original}
    link_ids={oid for (col,oid),row in original.items() if col=='viewpoint_claim_links'
              and row['payload'].get('effective_state')=='active'}
    vids=proof['bundle'].get('candidate_viewpoint_ids',[oid for col,oid in wanted if col=='canonical_viewpoints'])
    targets=[r['claim_id'] for r in proof['bundle']['claim_input']['claims']]
    rows=cursor.execute('''SELECT o.object_id FROM wang_knowledge.edges e JOIN wang_knowledge.objects o
        ON o.collection=e.edge_collection AND o.object_id=e.edge_id
        WHERE e.edge_collection='viewpoint_claim_links' AND e.retired_at IS NULL AND o.retired_at IS NULL
        AND o.payload->>'effective_state'='active' AND (e.from_id=ANY(%s) OR e.to_id=ANY(%s))''',
        (vids,targets)).fetchall()
    if {r[0] for r in rows}!=link_ids:
        raise ValueError('CVP2 membership scope changed outside complete reviewed material')
    validate_relationship_scope(cursor,original,proof['bundle'])
    validate_claim_authority(cursor,proof['bundle'],package=package)



def validate_readback(evidence,readback,observed):
    verify(readback,VERSION+'_readback')
    package=validate_evidence(evidence)
    if (readback['candidate_sha256']!=evidence['candidate']['artifact_sha256'] or
            readback['approval_sha256']!=evidence['approval']['artifact_sha256'] or
            readback.get('pending_impacts') or readback['independent_audit']!='not_performed'):
        raise ValueError('CVP2 readback lacks complete current impact closure')
    expected=_stored_package_records(package)
    from .postgres_store import record_content_sha
    for key,payload in expected.items():
        row=observed.get(key)
        if (not row or row.get('retired') or row.get('retired_at') or
                record_content_sha(row['payload'])!=record_content_sha(payload)):
            raise ValueError('CVP2 actual committed record differs from approved afterimage')
    snapshots={(r['collection'],r['object_id']):r['payload'] for r in readback['actual_records']}
    if snapshots!={key:row['payload'] for key,row in observed.items()}:
        raise ValueError('CVP2 readback observation/pins differ')


def validate_claim_authority(cursor,bundle,*,package=None):
    """Read SQL qualification facts only; physical artifacts were bound before SQL."""
    from backend.pipeline.claim_human_source import assert_current
    assert_current(cursor,[s['source_id'] for s in bundle.get('sources',[])],
                   bundle.get('human_source_decisions',[]))
    target_ids={r['claim_id'] for r in bundle['claim_input']['claims']}
    authorities={r['claim_id']:r['qualification'] for r in bundle.get('claim_authority',[])}
    if set(authorities)!=target_ids:raise ValueError('CVP2 omitted current Claim qualification authority')
    changed_links=[r for r in (package or {}).get('viewpoint_claim_links',[]) if r.get('effective_state')=='active']
    members={r['claim_id']:r['qualification'] for r in bundle.get('member_claim_authority',[])}
    originals={(r['collection'],r['object_id']):r for r in bundle['records']}
    for link in changed_links:
        cid=link['claim_id']
        if cid in target_ids or cid in authorities:
            continue
        qualification=members.get(cid)
        if isinstance(qualification,dict) and qualification.get('ready') is True:
            authorities[cid]=qualification
            continue
        # A CVP revision mechanically repins every retained member link. An
        # unchanged legacy member is already part of the approved old CVP; it
        # does not become a newly admitted Claim merely by changing the head.
        old=originals.get(('viewpoint_claim_links',link.get('viewpoint_claim_link_id')))
        claim=originals.get(('claims',cid))
        mechanical={'revision','decision_id','schema_version',
                    'validated_against_viewpoint_revision_id'}
        before=old['payload'] if old else None
        if (qualification is not None or not before or not claim
                or before.get('effective_state')!='active'
                or before.get('pinned_claim_revision')!=claim['revision']
                or {k:v for k,v in before.items() if k not in mechanical}!=
                   {k:v for k,v in link.items() if k not in mechanical}):
            raise ValueError('needs_upstream: changed member Claim lacks current qualification')
    for cid,q in authorities.items():
        before=originals['claims',cid]
        if (q.get('ready') is not True or (q.get('revision'),q.get('content_sha256'))!=
                (before['revision'],before['content_sha256']) or not q.get('run_id')):
            raise ValueError('CVP2 Claim authority is not its exact current version')
        found=cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s',(q['run_id'],)).fetchone()
        if not found:raise ValueError('CVP2 Claim qualification round is missing')
        value=found[0]
        independent=q.get('validation_kind')!='opus_and_program_readback'
        revalidation=value.get('source_revalidations',{}).get('claims/'+cid) if q.get('content_audit') else None
        if revalidation:
            proof_ok=(revalidation.get('impact_audit')==q['audit'] and
                      revalidation.get('content_audit')==q['content_audit'] and
                      revalidation.get('question_scope')==q.get('question_scope'))
            qualification_pins=revalidation.get('current_pins',[])
        else:
            proof_ok=value.get('stage_results',{}).get('audit' if independent else 'readback')==q.get('audit')
            qualification_pins=value.get('audited_pins',[])
        if (value.get('kind')!='claim_management_round' or value.get('checkpoint') not in {'applied','audited'}
                or value.get('outcome')!='completed' or value.get('revisit_required')
                or not value.get('independent_audit_complete' if independent else 'program_readback_complete') or not proof_ok):
            raise ValueError('CVP2 Claim current qualification was withdrawn or misrepresented')
        claim_pin=next((row.get('pin') for row in qualification_pins
                        if (row.get('collection'),row.get('object_id'))==('claims',cid)),None)
        if claim_pin!=[before['revision'],before['content_sha256'],False]:
            raise ValueError('CVP2 Claim qualification did not observe this actual current version')
        if not independent and q.get('independent_audit_complete') is not False:
            raise ValueError('CVP2 cannot label program readback as independent audit')
        blockers=cursor.execute("""SELECT r.metadata FROM wang_knowledge.pipeline_runs r
            JOIN wang_knowledge.pipeline_runs q ON q.run_id=%s
            WHERE r.metadata->>'kind'='claim_management_round'
            AND r.metadata->'blocked_current_claim_ids' ? %s
            AND (r.run_id=q.run_id OR (r.started_at,r.run_id)>(q.started_at,q.run_id))""",(q['run_id'],cid)).fetchall()
        for (blocked,) in blockers:
            old=blocked.get('materials',{}).get('before',{}).get('claims/'+cid)
            if old and (old['revision'],old['content_sha256'])==(before['revision'],before['content_sha256']):
                raise ValueError('CVP2 Claim has a current formal upstream finding')


def validate_completion_scope(store,conn,cursor,evidence,observed):
    """Recompute actual memberships, relations and reverse owners before completion.

    Invalidated downstream products stay invalidated; completing this operation
    cannot make an old article or an independent quality audit current again.
    """
    from .postgres_store import record_content_sha
    package=validate_evidence(evidence)
    if evidence['bundle'].get('update') is not None:
        unavailable=set(evidence['bundle']['update']['unavailable_claim_ids'])
        store._assert_transaction_read_pins(cursor,{(row['collection'],row['object_id']):
            (row['revision'],row['content_sha256'],bool(row.get('retired')))
            for row in evidence['bundle']['records'] if row['collection']=='claims' and row['object_id'] in unavailable})
    keys={(col,row[domain.KNOWLEDGE_COLLECTIONS[col][1]]) for col,rows in package.items()
          if col in domain.KNOWLEDGE_COLLECTIONS for row in rows}
    closure=store._affected_cvp_context(conn,sorted(keys)) if keys else {}
    if not set(closure)<=set(observed):raise ValueError('CVP2 completion omitted a necessary reverse owner/dependency')
    for key,row in closure.items():
        actual=observed[key]
        if (row['revision'],row['content_sha256'],bool(row.get('retired')))!=(
                actual['revision'],actual['content_sha256'],bool(actual.get('retired_at'))):
            raise ValueError('CVP2 completion reverse dependency changed')
    expected={(r['collection'],r['object_id']):r['payload'] for r in evidence['candidate']['records']}
    expected.update({(col,row[domain.KNOWLEDGE_COLLECTIONS[col][1]]):row
                     for col,rows in package.items() if col in domain.KNOWLEDGE_COLLECTIONS for row in rows})
    vids=sorted(set(evidence['bundle'].get('candidate_viewpoint_ids',[])) |
                {row['viewpoint_id'] for row in package.get('canonical_viewpoints',[])})
    target_ids=[c['claim_id'] for c in evidence['bundle']['claim_input']['claims']]
    links=cursor.execute("""SELECT object_id,payload FROM wang_knowledge.objects
        WHERE collection='viewpoint_claim_links' AND retired_at IS NULL AND payload->>'effective_state'='active'
        AND (payload->>'viewpoint_id'=ANY(%s) OR payload->>'claim_id'=ANY(%s))""",(vids,target_ids)).fetchall()
    expected_links={oid for (col,oid),row in expected.items() if col=='viewpoint_claim_links'
                    and row.get('effective_state')=='active'}
    if {oid for oid,_ in links}!=expected_links:
        raise ValueError('CVP2 completion current owned/member link population differs')
    for oid,link in links:
        key=('viewpoint_claim_links',oid)
        if key not in observed or record_content_sha(observed[key]['payload'])!=record_content_sha(link):
            raise ValueError('CVP2 completion omitted a current owned/member link')
        claim=observed.get(('claims',link['claim_id']))
        view=observed.get(('canonical_viewpoints',link['viewpoint_id']))
        if (not claim or not view or claim.get('retired_at') or view.get('retired_at') or
                claim['revision']!=link['pinned_claim_revision'] or
                view['payload'].get('identity_status')!='active' or
                view['payload'].get('current_revision_id')!=link['validated_against_viewpoint_revision_id']):
            raise ValueError('CVP2 completion contains a stale owned/member link')
        changed_link=any(row['viewpoint_claim_link_id']==oid for row in package.get('viewpoint_claim_links',[]))
        qualification=next((q['qualification'] for q in evidence['bundle'].get('claim_authority',[])
                            if q['claim_id']==link['claim_id']),{})
        claim_held=domain._held(claim['payload']) and not (
            claim['payload'].get('review_status')=='human_review_required' and
            qualification.get('legacy_ai_spot_check_replaced_by_current_review') is True)
        if changed_link and (claim_held or domain._held(view['payload'])):
            raise ValueError('CVP2 completion added or changed a held Claim/viewpoint connection')
    maintenance=evidence['bundle'].get('maintenance_viewpoint_ids',[])
    expected_relations={oid for (col,oid),row in expected.items() if col=='viewpoint_relations'
                        and row.get('effective_state')=='active'}
    relations=cursor.execute("""SELECT object_id,payload FROM wang_knowledge.objects
        WHERE collection='viewpoint_relations' AND retired_at IS NULL AND payload->>'effective_state'='active'
        AND (payload->>'source_viewpoint_id'=ANY(%s) OR payload->>'target_viewpoint_id'=ANY(%s)
             OR object_id=ANY(%s))""",(maintenance,maintenance,sorted(expected_relations))).fetchall()
    if {oid for oid,_ in relations}!=expected_relations:
        raise ValueError('CVP2 completion omitted a necessary current relationship')
    for oid,row in relations:
        if ('viewpoint_relations',oid) not in observed:
            raise ValueError('CVP2 completion omitted current relationship pins')
    # Current source/Evidence and all upstream qualification facts are checked
    # again independently of the submitting runner's empty pending declaration.
    validate_claim_authority(cursor,evidence['bundle'],package=package)


def validate_parent_current(store,cursor,parent):
    """Final current business obligations across sequentially committed units.

    A later unit may legitimately replace an earlier head. Its actual approved
    afterimage wins in sequence; an unrelated external edit never does.
    """
    expected={};target_pins={};written_versions={}
    from .cvp_management_store import _load
    for group in parent.get('work_groups',parent['groups']):
        unit=parent['units'][group['group_key']]
        step=_load(cursor,unit['completed_step'],lock=False)
        if not step.get('program_readback_complete'):
            raise ValueError('CVP2 parent still lacks a unit program readback')
        proof=step['reviewed']['evidence']['cvp_management']
        if (step.get('group_key')!=group['group_key'] or
                [c['claim_id'] for c in proof['bundle']['claim_input']['claims']]!=group['claim_ids']):
            raise ValueError('CVP2 parent unit lost its complete original Claim denominator')
        package=validate_evidence(proof)
        for row in proof['bundle']['records']:
            key=row['collection'],row['object_id']
            # Readonly historical context is not an instruction to reopen all
            # earlier provenance. Current business afterimages are retained.
            if key[0] in domain.COLLECTIONS:expected.setdefault(key,row['payload'])
            if key[0]=='claims' and key[1] in group['claim_ids']:
                target_pins[key]=(row['revision'],row['content_sha256'],False)
        for key,payload in _stored_package_records(package).items():
            if key[0] not in domain.COLLECTIONS:continue
            # A held early group may commit after a later group. Object
            # revisions from their audited SQL postimages, not plan order or
            # timestamps, identify the latest approved write to each object.
            revision=payload['revision']
            old=written_versions.get(key)
            if old is None or revision>old:
                expected[key]=payload;written_versions[key]=revision
            elif revision==old and expected[key]!=payload:
                raise ValueError('CVP2 parent has conflicting approved object versions')
        validate_claim_authority(cursor,proof['bundle'],package=package)
    if {oid for col,oid in target_pins}!=set(parent['request']['input']['claim_ids']):
        raise ValueError('CVP2 parent original Claim denominator is incomplete')
    store._assert_transaction_read_pins(cursor,target_pins)
    from .postgres_store import record_content_sha
    for key,payload in expected.items():
        row=cursor.execute('SELECT payload,content_sha256,retired_at FROM wang_knowledge.objects WHERE collection=%s AND object_id=%s',key).fetchone()
        if not row or row[2] is not None or row[1]!=record_content_sha(payload):
            raise ValueError(f'CVP2 parent has an unapproved current business dependency change: {key[0]}/{key[1]}')
