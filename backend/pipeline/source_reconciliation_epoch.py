"""Consume completed generic source epochs without sermon/root whitelists.

Historical membership is a read-only projection. Every changed current record
still needs the new exact source/Opus/apply/audit proof. Generic predecessor
epochs compose backwards; missing legacy transitions remain fail-closed.
"""
from copy import deepcopy
import json
from pathlib import Path

from backend.pipeline import source_reconciliation as c
from backend.pipeline.source_reconciliation_runner import body, read, binding as file_binding, validate_package, validate_completion_population, validate_reviewed_records
from backend.pipeline.source_reconciliation_runtime import ProductionPorts
from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.pipeline.viewpoint_validation_context import validated_history, require_explicit_history_maintenance


@validated_history(bound_files_only=True)
def load(bound, *, historical=False):
    if not historical:
        return load_completed(bound)
    require_explicit_history_maintenance('source epoch full frozen-package reconstruction')
    path = c.verify_file(bound)
    package = body(read(path))
    validate_package(package, historical=historical)
    root = Path(package["source_repair_application"]["run_root"])
    if path != root / "effective-package.json":
        raise ValueError("epoch package is detached from its actual run")
    frozen = body(read(root / "frozen.json"))
    plan = body(read(root / "plan.json"))
    complete = read(root / "complete.json")
    if (complete["status"] != "audited_complete" or complete["update_id"] != frozen["update_id"]
            or complete["change_set_id"] != plan["change_set_id"] or complete["campaign_membership_changed"]):
        raise ValueError("source epoch has no exact applied-and-audited completion")
    for b in complete["bindings"]:
        c.verify_file(b)
    post = c.assert_plan_postimages(package, plan, c.index(frozen["before_records"]))
    validate_completion_population(complete, frozen, post)
    for outcome in complete["audits"]:
        c.verify_file(outcome["binding"])
        for b in [outcome["result"].get("audit"), outcome["result"].get("original_audit"), outcome["result"].get("resolution")]:
            if b:
                c.verify_file(b)
    return package, frozen, plan, complete


def _snapshot_header_value(path, field):
    # Executor JSON is key-sorted: the seal precedes before_records. Read its
    # small header, never decode/skip the giant records array on a miss.
    from backend.pipeline.viewpoint_compiled_readset import _Stream, CompileLimits
    import time
    metrics={'input_bytes':0,'decoded_values':0}
    stream=_Stream(path,CompileLimits(bytes=8*1024**2,seconds=2),metrics,time.perf_counter())
    try:
        stream.expect('{')
        while True:
            name=stream.value(); stream.expect(':')
            if name==field:return stream.value()
            if name in {'before_records','post_records','records'}:
                raise ValueError('completed snapshot header missing: '+field)
            stream.skip();stream.expect(',')
    finally:stream.close()


def _frozen_seal(path):
    return _snapshot_header_value(path,'artifact_sha256')


@validated_history(bound_files_only=True)
def load_completed(bound):
    """Consume a completed source epoch using its ORIGINAL scoped packet.

    No new snapshot/certificate/cache protocol. The executor already saved the
    complete affected/context rows and source metadata in proposal.packet.json.
    Its complete binding authenticates the old full freeze without parsing it.
    Store writes retain validate_package's full authorization reconstruction.
    Current postimages, retained versions, SQL and audit gates stay separate.
    """
    path=c.verify_file(bound); package=body(read(path))
    app=package.get('source_repair_application',{}); root=Path(app.get('run_root',''))
    if (app.get('schema_version')!=c.VERSION or not root.is_absolute()
            or path!=root/'effective-package.json'):
        raise ValueError('completed epoch package identity mismatch')
    complete=read(root/'complete.json');plan=body(read(root/'plan.json'))
    if (complete.get('status')!='audited_complete'
            or complete.get('change_set_id')!=plan['change_set_id']
            or complete.get('campaign_membership_changed') is not False):
        raise ValueError('source epoch has no exact applied-and-audited completion')
    required={str(root/n) for n in ('frozen.json','effective-package.json','plan.json',
                                   'apply-intent.json','backup.json','readback.json')}
    pins=complete['bindings']
    if len(pins)!=len(required) or {b['path'] for b in pins}!=required or bound not in pins:
        raise ValueError('completion proof denominator drift')
    for pin in pins:c.verify_file(pin)
    if app.get('frozen_sha256')!=_frozen_seal(root/'frozen.json'):
        raise ValueError('completed epoch original frozen seal mismatch')
    packet=body(read(root/'proposal.packet.json'))
    frozen={'update_id':packet['update_id'],'manifest':packet['manifest'],
        'inventory':packet['inventory'],'before':packet['before_source'],
        'after':packet['current_sources'],'context_sources':packet.get('context_sources',[]),
        'sentence_inventory':packet['sentence_inventory'],'before_records':packet['records'],
        'physical_snapshots':None,'completed_readset_projection':True}
    if 'deferred_diagnosis' in packet:frozen['deferred_diagnosis']=packet['deferred_diagnosis']
    selected=set(frozen['inventory']['affected_keys'])|set(frozen['inventory']['context_keys'])
    before=c.index(frozen['before_records'])
    from backend.pipeline.source_reconciliation_runner import task_id
    manifest=c.Manifest.model_validate(frozen['manifest'])
    if (set(before)!=selected or complete['update_id']!=frozen['update_id']
            or root.name!=task_id(manifest)):
        raise ValueError('completion scoped packet/member identity mismatch')
    # All original raw/config/wire/one-correction and exact package comparisons
    # are shared with the strict store validator, not replaced by a pass flag.
    validate_reviewed_records(package,frozen,frozen_sha256=app['frozen_sha256'])
    c.guard_files(frozen)
    post=c.assert_plan_postimages(package,plan,before)
    validate_completion_population(complete,frozen,post)
    for outcome in complete['audits']:
        c.verify_file(outcome['binding'])
        for pin in [outcome['result'].get(k) for k in ('audit','original_audit','resolution')]:
            if pin:c.verify_file(pin)
    return package,frozen,plan,complete


def _assert_database(plan, store):
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute("SELECT source_kind,source_sha256,fingerprint_sha256,status FROM wang_knowledge.change_sets WHERE change_set_id=%s", (plan["change_set_id"],))
        if tuple(cursor.fetchone() or ()) != (c.VERSION, plan["source_sha256"], plan["fingerprint_sha256"], "applied"):
            raise ValueError("epoch has no matching actual applied ChangeSet")


def _postimages(plan, states):
    for op in plan["operations"]:
        expected = {**op["payload"], "revision": op["after_revision"]}
        if states.get(c.key(op["collection"], op["object_id"])) != expected:
            raise ValueError("source epoch current postimage drift: " + op["object_id"])


def _retained_readset(frozen, plan, states, *, before=None):
    written = {c.key(o["collection"], o["object_id"]) for o in plan["operations"]}
    before = c.index(frozen["before_records"]) if before is None else before
    for k in frozen["inventory"]["affected_keys"] + frozen["inventory"]["context_keys"]:
        if k not in written and c.split_key(k)[0] in c.WRITABLE and states.get(k) != before[k]:
            raise ValueError("source epoch retained review context/version drift: " + k)
    return before


def verified_applied(bound, store, *, current_keys=()):
    package, frozen, plan, complete = load(bound)
    _assert_database(plan, store)
    ports = ProductionPorts(store)
    before=c.index(frozen['before_records'])
    keys=set(before)|set(current_keys)
    keys.update(c.key(o['collection'],o['object_id']) for o in plan['operations'])
    # The child original complete review context is part of this readset;
    # do not discover it by loading its historical whole-library snapshot.
    for pin in complete.get('authorized_link_corrections',[]):
        from backend.pipeline.owner_approved_link_correction import consume_completed_child
        _,packet,child_plan,_=consume_completed_child(pin,complete)
        keys.update(c.index(packet['context_records']))
        keys.update(c.key(o['collection'],o['object_id']) for o in child_plan['operations'])
    for outcome in complete['audits']:
        keys.update(c.key('claims',cid) for cid in outcome['job']['claim_ids'])
        keys.update(c.key('canonical_viewpoints',vid) for vid in outcome['job']['viewpoint_ids'])
    current=ports.current_snapshot(keys)
    # Expand the actual audit Claims' complete own Evidence/fragment/source
    # denominator. Each round reads only newly declared IDs, not collections.
    while True:
        needed=set()
        for key,p in current.items():
            col=c.split_key(key)[0]
            if col=='claims':needed.update(c.key('evidence_steps',eid) for eid in p.get('evidence_step_ids',[]))
            elif col=='evidence_steps':
                fids=set(p.get('source_fragment_ids',[]))|({p['source_fragment_id']} if p.get('source_fragment_id') else set())
                needed.update(c.key('source_fragments',fid) for fid in fids)
            elif col=='source_fragments':needed.add(c.key('source_documents',p['source_id']))
        missing=needed-current.keys()
        if not missing:break
        current.update(ports.current_snapshot(missing))
    from backend.pipeline.owner_approved_link_correction import source_projection
    projected = source_projection(complete, current, store)
    _postimages(plan, projected)
    before = _retained_readset(frozen, plan, projected,before=before)
    for outcome in complete["audits"]:
        job = outcome["job"]
        ports.consume_completed_audit(outcome,complete,current)
    return package, {c.split_key(k): p for k, p in before.items()}, {c.split_key(k): p for k, p in current.items()}


def binding(path):
    return file_binding(Path(path))


def _predecessor_projection(package, states, store, *, seen=None):
    """Restore only exact proven postimages in a local copy, never the database."""
    require_explicit_history_maintenance('recursive source epoch predecessor reconstruction')
    seen = set() if seen is None else seen
    root = Path(package["source_repair_application"]["run_root"])
    bound = file_binding(root / "effective-package.json")
    if bound["file_sha256"] in seen:
        raise ValueError("source epoch predecessor cycle")
    seen.add(bound["file_sha256"])
    _, frozen, plan, complete = load(bound, historical=True)
    _assert_database(plan, store)
    from backend.pipeline.owner_approved_link_correction import source_projection
    states = source_projection(complete, states, store)
    _postimages(plan, states)
    before = _retained_readset(frozen, plan, states)
    restored = deepcopy(states)
    for op in plan["operations"]:
        k = c.key(op["collection"], op["object_id"])
        if k in before:
            restored[k] = before[k]
        else:
            del restored[k]
    parents = frozen["manifest"].get("prior_updates", [])
    if len(parents) > 1:
        raise ValueError("one source has one chronological predecessor, not competing approvals")
    for parent in parents:
        previous, previous_frozen, _, _ = load(parent, historical=True)
        if previous_frozen["manifest"]["source_id"] != frozen["manifest"]["source_id"]:
            raise ValueError("source epoch predecessor belongs to another source")
        previous_after = previous_frozen["after"][0]
        if previous_after["body_sha256"] != frozen["before"]["body_sha256"]:
            raise ValueError("source epoch chronology/body transition gap")
        restored = _predecessor_projection(previous, restored, store, seen=seen)
    return restored


def _completed_predecessor_projection(package, states, store):
    """Reverse already completed operations on this exact caller readset only.

    Normal consumers never materialize an entire predecessor Registry. Each
    original package/SQL epoch is authenticated; only provided identities are
    projected, and unrelated current records preserve their exact values.
    Missing older archived inputs stop with no full-freeze reconstruction.
    """
    restored=deepcopy(states); seen=set(); expected_source=None; expected_after=None
    while True:
        root=Path(package['source_repair_application']['run_root'])
        bound=file_binding(root/'effective-package.json')
        if bound['file_sha256'] in seen:raise ValueError('source epoch predecessor cycle')
        seen.add(bound['file_sha256'])
        checked,frozen,plan,complete=load(bound)
        if checked!=package:raise ValueError('completed predecessor package drift')
        sid=frozen['manifest']['source_id']
        if expected_source is not None and (sid!=expected_source or frozen['after'][0]['body_sha256']!=expected_after):
            raise ValueError('source epoch chronology/body transition gap')
        _assert_database(plan,store)
        from backend.pipeline.owner_approved_link_correction import source_projection
        restored=source_projection(complete,restored,store)
        before=c.index(frozen['before_records'])
        for op in plan['operations']:
            key=c.key(op['collection'],op['object_id'])
            if key not in restored:continue
            expected={**op['payload'],'revision':op['after_revision']}
            if restored[key]!=expected:raise ValueError('completed predecessor related postimage drift')
            if key in before:restored[key]=deepcopy(before[key])
            else:del restored[key]
        parents=frozen['manifest'].get('prior_updates',[])
        if len(parents)>1:raise ValueError('one source has one chronological predecessor')
        if not parents:return restored
        expected_source,expected_after=sid,frozen['before']['body_sha256']
        package,_,_,_=load(parents[0])


def grouping_physical_transition(manifest, bound):
    package, frozen, _, _ = load(bound)
    original = json.loads(c.bound_bytes(frozen["manifest"]["accepted_membership"], frozen["physical_snapshots"]))
    if original != manifest:
        raise ValueError("source epoch cannot rewrite a different accepted grouping")
    store = ProductionPorts().store
    _, _, live = verified_applied(bound, store)
    restored = _completed_predecessor_projection(package, {c.key(col, oid): p for (col, oid), p in live.items()}, store)
    sid = frozen["manifest"]["source_id"]
    old = restored[c.key("source_documents", sid)]
    new = live["source_documents", sid]
    selected = [s for s in manifest["sources"] if s["source_id"] == sid]
    if len(selected) != 1:
        raise ValueError("accepted source predecessor is not unique")
    row = selected[0]
    if (row["path"] != old["source_path"] or row["file_sha256"] != old["source_file_sha256"]
            or row["frozen_file_sha256"] != old["source_file_sha256"] or row["source_revision"] != old["revision"]
            or row["source_content_sha256"] != c.semantic_record_sha(old) or row["source_body_sha256"] != old["source_body_sha256"]):
        raise ValueError("accepted predecessor source pins do not match authenticated history")
    return {row["path"]: (old["source_file_sha256"], new["source_file_sha256"])}


def reviewed_claim_ids(package):
    root = Path(package["source_repair_application"]["run_root"])
    _, frozen, _, _ = load(file_binding(root / 'effective-package.json'))
    # Includes unchanged Claim text explicitly independently reviewed against
    # the new source; no inherited approval, no new write just for status.
    return {c.split_key(k)[1] for k in frozen["inventory"]["affected_keys"] if k.startswith("claims/")}


def selection_row(cid, package):
    if cid not in reviewed_claim_ids(package):
        raise ValueError("new-source review does not cover this Claim")
    root = Path(package["source_repair_application"]["run_root"])
    stage = package["source_repair_application"]["terminal_stage"]
    return {"claim_id": cid, "review_binding": file_binding(root / (stage + ".raw.json")),
            "review_input_binding": file_binding(root / (stage + ".packet.json")),
            "source_repair_binding": file_binding(root / "effective-package.json")}


def membership_snapshot(manifest, records, package, completed_ids, selected_ids):
    root = Path(package["source_repair_application"]["run_root"])
    _, frozen, _, _ = load(file_binding(root / 'effective-package.json'))
    if json.loads(c.bound_bytes(frozen["manifest"]["accepted_membership"], frozen["physical_snapshots"])) != manifest:
        raise ValueError("historical membership manifest differs from authorized accepted grouping")
    ports = ProductionPorts()
    _, _, live = verified_applied(file_binding(root / "effective-package.json"), ports.store,
        current_keys={c.key(col,oid) for col,oid in records})
    current = {c.key(col, oid): payload for (col, oid), payload in live.items()}
    for (col, oid), row in records.items():
        if (row.get("retired") or row["payload"] != current.get(c.key(col, oid))
                or row["revision"] != row["payload"].get("revision")
                or row["content_sha256"] != record_content_sha(row["payload"])):
            raise ValueError("source epoch caller snapshot differs from actual current Registry")
    restored = _completed_predecessor_projection(package, current, ports.store)
    result = deepcopy(records)
    for k in current.keys() - restored.keys():
        if c.split_key(k)[0] in {"source_documents", "source_fragments", "evidence_steps", "claims"}:
            result.pop(c.split_key(k), None)
    changed = []
    for k, old in restored.items():
        if (c.split_key(k) not in result or k not in current or current[k] == old
                or c.split_key(k)[0] not in {"source_documents", "source_fragments", "evidence_steps", "claims"}):
            continue
        col, oid = c.split_key(k)
        result[col, oid] = {"payload": old, "revision": old["revision"], "content_sha256": record_content_sha(old), "retired": False}
        if col == "claims":
            changed.append(oid)
    return result, changed


def bridge(*, claim_id, current_claim, current_evidence, current_fragments, source_repair_binding, store=None):
    store = store or ProductionPorts().store
    package, _, live = verified_applied(source_repair_binding, store)
    if claim_id not in reviewed_claim_ids(package) or current_claim != live.get(("claims", claim_id)) or current_claim.get("review_status") in c.HUMAN_HOLDS:
        raise ValueError("current Claim not covered or carries a human hold")
    if set(current_evidence) != set(current_claim.get("evidence_step_ids", [])):
        raise ValueError("current Evidence denominator drift")
    fids = set()
    for eid, p in current_evidence.items():
        if p != live.get(("evidence_steps", eid)):
            raise ValueError("current Evidence version drift")
        fids.update(p.get("source_fragment_ids", []))
        if p.get("source_fragment_id"):
            fids.add(p["source_fragment_id"])
    if set(current_fragments) != fids or any(p != live.get(("source_fragments", fid)) for fid, p in current_fragments.items()):
        raise ValueError("current fragment population/version drift")
    selected = selection_row(claim_id, package)
    value = {"schema_version": "wang_historical_claim_review_bridge_v1", "status": "verified",
        "claim_id": claim_id, "pinned_claim_revision": current_claim["revision"], "claim_revision_sha256": c.semantic_record_sha(current_claim),
        **{k: v for k, v in selected.items() if k != "claim_id"}, "review_model_id": "claude-opus-5-5",
        "review_provider": "owner_scoped_independent_terminal_subscription_review", "old_review_not_relabelled": True,
        "master_data_mutations": 0, "model_calls_executed": 0, "approval_status": "not_human_approved"}
    return {**value, "artifact_sha256": c.sha256_json(value)}
