"""Synthetic sources only; no professor-content fixture or production writes."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.pipeline import source_reconciliation as c
from backend.pipeline import source_reconciliation_runner as r
from backend.pipeline import source_reconciliation_runtime as runtime
from backend.api.canonical_repository.postgres_store import build_change_set_plan, normalize_package, record_content_sha
from backend.api.canonical_repository.reviewed_candidate_contract import validate_store_package_authorization


def put(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return r.binding(path)


@pytest.fixture
def scenario(tmp_path):
    before_file = put(tmp_path / "old.json", {"script": [{"text": "甲是假设。"}]})
    new_file = put(tmp_path / "new.json", {"script": [{"type": "subtitle", "text": "编辑标题"}, {"text": "甲是假设，不是断言。"}]})
    old = c.source_snapshot(before_file)
    new = c.source_snapshot(new_file)
    payload = {"source_documents": [dict(source_id="SRC-test", source_type="sermon_transcript", transcript_id="test",
        locator_space="spoken_body_v1", source_path=new_file["path"], source_file_sha256=before_file["file_sha256"],
        source_body_sha256=old["body_sha256"], source_sha256=old["body_sha256"])],
        "source_fragments": [dict(fragment_id="FR-test", source_id="SRC-test", paragraph_key="S0001",
            verbatim_excerpt="甲是假设。", source_sha256=old["body_sha256"])],
        "evidence_steps": [dict(evidence_step_id="E-test", source_fragment_ids=["FR-test"], produced_claim_ids=["CL-test"], statement="甲是假设。")],
        "claims": [dict(claim_id="CL-test", statement="甲是假设。", claim_type="exegesis", evidence_step_ids=["E-test"])],
        "questions": [dict(question_id="Q-test", text="甲是断言吗？", source_fragment_id="FR-test", answer_claim_ids=["CL-test"])],
        "observations": [dict(observation_id="OBS-test", statement="甲是假设。", source_fragment_id="FR-test")]}
    normalized = normalize_package(payload)
    states = {c.key(col, oid): p for col, by_id in normalized.items() for oid, p in by_id.items()}
    permissions = [c.Permission(collection=col, object_id=oid, fields=sorted(p.keys())) for col, by_id in normalized.items() for oid, p in by_id.items()]
    membership = put(tmp_path / "membership.json", {"groups": [{"group_key": "g1", "claim_ids": ["CL-test"]}]})
    authorization = put(tmp_path / "authorization.json", {"source_id": "SRC-test", "authorization_id": "owner-test", "authorization_quote": "同意修订这一段", "human_content_approval": False,
        "permissions": [p.model_dump() for p in permissions]})
    manifest = c.Manifest(source_id="SRC-test", before_source=before_file, current_sources=[new_file], authorization=authorization,
        authorized_paragraph_keys=["S0001"], permissions=permissions,
        groups=[c.Group(group_key="g1", claim_ids=["CL-test"])], campaign_claim_ids=["CL-test"],
        accepted_membership=membership, created_at="2026-10-06T00:00:00Z")
    return manifest, states, new


def proposal(frozen, new):
    states = c.index(frozen["before_records"])
    proposed = deepcopy(states)
    statement = new["paragraphs"]["S0001"]["text"]
    for k, p in proposed.items():
        p["revision"] += 1
        p["review_status"] = "candidate"
        if k.startswith("source_documents/"):
            p.update(source_file_sha256=new["file"]["file_sha256"], source_body_sha256=new["body_sha256"], source_sha256=new["body_sha256"])
        elif k.startswith("source_fragments/"):
            p.update(verbatim_excerpt=statement, source_sha256=new["body_sha256"])
        elif k.startswith("questions/"):
            p.update(answer_state="answered", text="甲只是一个假设吗？")
        else:
            p["statement"] = statement
    changed = {k: p for k, p in proposed.items() if c._semantic(p) != c._semantic(states[k])}
    return c.Proposal(records=[c.Row.model_validate(row) for row in c.rows(changed)],
        dispositions=[c.Disposition(record_key=k, action="update" if k in changed else "retain", reason="精确新来源修订或保留") for k in frozen["inventory"]["affected_keys"]],
        sentence_dispositions=[c.SentenceDisposition(sentence_id=s["sentence_id"], action="represented", record_keys=["claims/CL-test"], reason="已保留完整条件")
                               for s in frozen.get("sentence_inventory", [])])


class FakePorts:
    def __init__(self, manifest, states, new, tmp_path):
        self.manifest, self.states, self.new = manifest, deepcopy(states), new
        self.tmp_path = tmp_path
        self.calls, self.applies, self.audits = [], 0, 0
        self.commit = False
        self.refuse_initial = self.refuse_final = self.fail_call = self.crash_after_commit = False

    def snapshot(self):
        return deepcopy(self.states)

    def current_snapshot(self,keys):
        return {k:deepcopy(self.states[k]) for k in keys}

    def consume_completed_audit(self,outcome,complete,states):
        assert outcome in complete['audits']
        self.validate_audit(outcome['result'],outcome['job']['claim_ids'],outcome['job']['viewpoint_ids'])

    def adapter(self, stage):
        adapter = runtime.model_adapter(stage)
        def generate(packet):
            self.calls.append(stage)
            if self.fail_call:
                raise RuntimeError("quota/transport error")
            if "review" in stage:
                approved = not (self.refuse_initial if stage == "initial_review" else self.refuse_final)
                return {"proposal_sha256": packet["proposal_sha256"], "verdicts": [
                    {"record_key": d["record_key"], "approved": approved, "reason": "逐字原文核验", "problems": [] if approved else ["真实未决"]}
                    for d in packet["proposal"]["dispositions"]]}
            if stage == "correction":
                return deepcopy(packet["initial_proposal"])
            return proposal({"before_records": packet["records"], "inventory": packet["inventory"], "sentence_inventory": packet["sentence_inventory"]}, self.new).model_dump()
        adapter.generate = generate
        return adapter

    def plan(self, package):
        existing = {c.split_key(k): {"payload": p, "revision": p["revision"], "content_sha256": record_content_sha(p), "retired": False}
                    for k, p in self.states.items()}
        return json.loads(json.dumps(build_change_set_plan(package, existing, source_kind=c.VERSION).as_dict()))

    def backup(self, root):
        b = put(self.tmp_path / "backup.dump", {"synthetic": True})
        return {"dump": b, "restore_catalog_nonempty": True}

    def committed(self, plan):
        return self.commit

    def apply(self, package, plan, frozen):
        self.applies += 1
        before = c.index(frozen["before_records"])
        post = c.assert_plan_postimages(package, plan, before)
        self.states = runtime.expected_invalidations(before, post, plan)
        self.commit = True
        if self.crash_after_commit:
            self.crash_after_commit = False
            raise RuntimeError("crash after COMMIT, before receipt")
        return {"status": "applied", "change_set_id": plan["change_set_id"]}

    def readback(self, plan, expected, before):
        assert self.states == runtime.expected_invalidations(before, expected, plan)
        return self.snapshot()

    def audit(self, root, claim_ids, viewpoint_ids):
        self.audits += 1
        return {"claim_ids": claim_ids, "viewpoint_ids": viewpoint_ids, "independent_audit_complete": True, "revisit_required": False}

    def audit_saved(self, root, claim_ids, viewpoint_ids):
        raise r.Stop("unfinished reserved audit", status="call_uncertain", waiting_for="system")

    def validate_audit(self, result, claim_ids, viewpoint_ids):
        assert result["claim_ids"] == claim_ids and result["viewpoint_ids"] == viewpoint_ids


def setup_runner(tmp_path, scenario):
    manifest, states, new = scenario
    ports = FakePorts(manifest, states, new, tmp_path)
    runner = r.Runner(tmp_path / "runtime", ports)
    return runner, ports, manifest


def test_full_reverse_inventory_includes_questions_and_observations(scenario):
    manifest, states, _ = scenario
    inv = c.inventory(states, manifest.source_id)
    assert {"questions/Q-test", "observations/OBS-test", "claims/CL-test", "evidence_steps/E-test"} <= set(inv["affected_keys"])


def test_added_heading_does_not_shift_spoken_locators(scenario):
    manifest, states, _ = scenario
    frozen = c.freeze(manifest, states)
    assert list(frozen["after"][0]["paragraphs"]) == ["S0001"]
    assert frozen["changed_paragraph_keys"] == ["S0001"]


@pytest.mark.parametrize("mutation", ["omit_consumer", "foreign_consumer", "ambiguous_quote", "stale_epoch", "reverse_pointer", "extra_field", "extra_claim", "old_approval", "scope", "unknown", "same_revision", "hold"])
def test_fail_closed_counterexamples(scenario, mutation):
    manifest, states, new = scenario
    if mutation == "scope":
        manifest = manifest.model_copy(update={"authorized_paragraph_keys": []})
        with pytest.raises(ValueError, match="exceeds authorized"):
            c.freeze(manifest, states)
        return
    if mutation == "unknown":
        states["future_collection/UNK"] = {"reference": "FR-test"}
    if mutation == "hold":
        states["claims/CL-test"]["review_status"] = "human_review_required"
    frozen = c.freeze(manifest, states)
    if mutation == "unknown":
        p = c.Proposal(records=[], dispositions=[c.Disposition(record_key=k, action="unresolved", reason="unknown")
                                                for k in frozen["inventory"]["affected_keys"]])
        with pytest.raises(ValueError, match="unknown/unresolved"):
            c.validate_proposal(frozen, p)
        return
    p = proposal(frozen, new)
    if mutation == "omit_consumer":
        p.dispositions = [d for d in p.dispositions if d.record_key != "questions/Q-test"]
    elif mutation == "foreign_consumer":
        p.dispositions.append(c.Disposition(record_key="claims/OTHER", action="retain", reason="not in input"))
    elif mutation == "ambiguous_quote":
        frozen["after"][0]["paragraphs"]["S0001"]["text"] *= 2
    elif mutation == "stale_epoch":
        next(row for row in p.records if row.collection == "source_fragments").payload["source_sha256"] = "a" * 64
    elif mutation == "reverse_pointer":
        next(row for row in p.records if row.collection == "evidence_steps").payload["produced_claim_ids"] = []
    elif mutation == "extra_field":
        # Narrow permission is actually bound at freeze, not read from model prose.
        frozen["manifest"]["permissions"][0]["fields"] = []
    elif mutation == "extra_claim":
        p.records.append(c.Row(collection="claims", object_id="NEW", payload={"claim_id": "NEW"}))
    elif mutation == "old_approval":
        next(row for row in p.records if row.collection == "claims").payload["review_status"] = "ai_consensus"
    elif mutation == "same_revision":
        p.records[0].payload["revision"] = 1
    with pytest.raises((ValueError, KeyError)):
        c.validate_proposal(frozen, p)


def test_source_and_registry_drift_before_model(scenario, tmp_path):
    runner, ports, manifest = setup_runner(tmp_path, scenario)
    runner.prepare(manifest)
    ports.states["claims/CL-test"]["statement"] = "concurrent change"
    with pytest.raises(r.Stop, match="Registry changed"):
        runner.run(manifest)
    assert ports.calls == [] and ports.applies == 0
    ports.states = deepcopy(scenario[1])
    Path(manifest.current_sources[0].path).write_text("{}")
    with pytest.raises(ValueError, match="changed"):
        runner.run(manifest)
    assert ports.calls == []


def test_full_workflow_and_second_run_zero_calls_zero_writes(scenario, tmp_path):
    runner, ports, manifest = setup_runner(tmp_path, scenario)
    result = runner.run(manifest, apply=True, audit=True)
    assert result["status"] == "audited_complete" and not result["production_resumed"]
    assert ports.calls == ["proposal", "initial_review"]
    assert ports.applies == 1 and ports.audits == 1
    assert runner.run(manifest, apply=True, audit=True) == result
    assert ports.calls == ["proposal", "initial_review"] and ports.applies == ports.audits == 1


def test_exactly_one_correction_and_terminal_opus(scenario, tmp_path):
    runner, ports, manifest = setup_runner(tmp_path, scenario)
    ports.refuse_initial = True
    assert runner.run(manifest, apply=True, audit=True)["status"] == "audited_complete"
    assert ports.calls == ["proposal", "initial_review", "correction", "final_review"]


def test_terminal_refusal_stops_no_partial_apply_or_second_correction(scenario, tmp_path):
    runner, ports, manifest = setup_runner(tmp_path, scenario)
    ports.refuse_initial = ports.refuse_final = True
    with pytest.raises(r.Stop, match="terminal Opus"):
        runner.run(manifest, apply=True, audit=True)
    count = list(ports.calls)
    with pytest.raises(r.Stop, match="terminal Opus"):
        runner.run(manifest, apply=True, audit=True)
    assert ports.calls == count and len(count) == 4 and ports.applies == 0
    assert r.read(runner.root / "state.json")["waiting_for"] == "owner"


def test_reserved_uncertain_model_never_repeats_or_resets_by_timestamp(scenario, tmp_path):
    runner, ports, manifest = setup_runner(tmp_path, scenario)
    ports.fail_call = True
    with pytest.raises(r.Stop, match="subscription call failed"):
        runner.run(manifest)
    ports.fail_call = False
    with pytest.raises(r.Stop, match="do not repeat"):
        runner.run(manifest)
    changed = manifest.model_copy(update={"created_at": "2026-10-07T00:00:00Z"})
    assert r.task_id(manifest) == r.task_id(changed)
    with pytest.raises(r.Stop, match="budget not reset"):
        runner.run(changed)
    assert ports.calls == ["proposal"]


def test_sql_commit_before_receipt_recovers_without_apply_or_models(scenario, tmp_path):
    runner, ports, manifest = setup_runner(tmp_path, scenario)
    ports.crash_after_commit = True
    with pytest.raises(RuntimeError, match="COMMIT"):
        runner.run(manifest, apply=True, audit=True)
    assert not (runner.root / "apply-receipt.json").exists()
    assert runner.run(manifest, apply=True, audit=True)["status"] == "audited_complete"
    assert ports.applies == 1 and ports.calls == ["proposal", "initial_review"]


def test_approved_and_committed_are_not_completed(scenario, tmp_path):
    runner, ports, manifest = setup_runner(tmp_path, scenario)
    assert runner.run(manifest)["status"] == "reviewed_not_applied"
    assert ports.applies == ports.audits == 0
    assert runner.run(manifest, apply=True)["status"] == "committed_waiting_audit"
    assert ports.applies == 1 and ports.audits == 0 and not (runner.root / "complete.json").exists()


def test_tampered_review_or_package_never_authorizes_store(scenario, tmp_path):
    runner, ports, manifest = setup_runner(tmp_path, scenario)
    runner.run(manifest)
    package = r.body(r.read(runner.root / "effective-package.json"))
    validate_store_package_authorization(package)
    package["claims"][0]["statement"] = "unreviewed"
    with pytest.raises(ValueError, match="differs"):
        validate_store_package_authorization(package)
    raw = r.body(r.read(runner.root / "initial_review.raw.json"))
    raw["model_id"] = "wrong"
    r.write(runner.root / "initial_review.raw.json", raw, immutable=False)
    with pytest.raises(ValueError, match="binding drift"):
        validate_store_package_authorization(r.body(r.read(runner.root / "effective-package.json")))


def test_invalidations_preserve_all_causes_not_only_first(scenario):
    _, before, _ = scenario
    before["product_dependencies/DEP"] = {"dependency_id": "DEP", "claim_id": "CL-test", "revision": 4,
        "status": "current", "dependency_manifest": [{"collection": "source_fragments", "record_id": "FR-test"}]}
    ops = [dict(collection="claims", object_id="CL-test", before_revision=1, after_revision=2),
           dict(collection="source_fragments", object_id="FR-test", before_revision=1, after_revision=2)]
    post = runtime.expected_invalidations(before, before, {"change_set_id": "KCS-test", "operations": ops})
    assert post["product_dependencies/DEP"]["revision"] == 5
    assert len(post["product_dependencies/DEP"]["invalidation_event_ids"]) == 2
    assert len([k for k in post if k.startswith("impact_events/")]) == 2


def test_unchanged_store_rows_keep_real_revision(scenario):
    _, before, _ = scenario
    package = {"package_id": "x", "claims": [before["claims/CL-test"]]}
    post = c.assert_plan_postimages(package, {"operations": []}, before)
    assert post == before
    altered = deepcopy(package)
    altered["claims"][0]["statement"] = "different"
    with pytest.raises(ValueError, match="omitted"):
        c.assert_plan_postimages(altered, {"operations": []}, before)


def test_audit_jobs_full_group_not_only_changed_claim(scenario):
    manifest, before, _ = scenario
    frozen = c.freeze(manifest, before)
    frozen["manifest"]["groups"][0]["claim_ids"].append("CL-context")
    frozen["manifest"]["groups"][0].update(revisit_required=True, independent_audit_complete=False)
    jobs = r.audit_jobs(frozen, before)
    assert jobs[0]["claim_ids"] == ["CL-context", "CL-test"]
    assert jobs[0]["revisit_required"] and not jobs[0]["independent_audit_complete"]


def test_store_apply_boundary_requires_reviewed_package_before_sql():
    with pytest.raises(ValueError, match="exact reviewed package"):
        runtime.validate_apply_plan(None, None)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unresolved", "unknown_record"])
def test_sentence_denominator_is_source_not_extraction_output(scenario, mutation):
    manifest, before, new = scenario
    frozen = c.freeze(manifest, before)
    p = proposal(frozen, new)
    if mutation == "missing":
        p.sentence_dispositions.clear()
    elif mutation == "duplicate":
        p.sentence_dispositions.append(p.sentence_dispositions[0])
    elif mutation == "unresolved":
        p.sentence_dispositions[0].action = "unresolved"
    else:
        p.sentence_dispositions[0].record_keys = ["claims/unknown"]
    with pytest.raises(ValueError, match="sentence"):
        c.validate_proposal(frozen, p)


@pytest.mark.parametrize("mutation", [None, "missing_group", "two_representatives", "different_group", "not_count_once", "chain_overlap"])
def test_duplicate_and_overlapping_anchors_count_one_basis(mutation):
    def anchor(rep):
        return {"evidence_independence": {"basis_group_id": "basis-a", "count_once_per_basis_group": True, "is_basis_representative": rep}}
    spans = [("S0001", 0, 10, anchor(True)), ("S0001", 3, 7, anchor(False))]
    if mutation == "missing_group":
        spans[1][3].clear()
    elif mutation == "two_representatives":
        spans[1][3]["evidence_independence"]["is_basis_representative"] = True
    elif mutation == "different_group":
        spans[1][3]["evidence_independence"]["basis_group_id"] = "other"
    elif mutation == "not_count_once":
        spans[1][3]["evidence_independence"]["count_once_per_basis_group"] = False
    elif mutation == "chain_overlap":
        spans.append(("S0001", 9, 12, anchor(True)))
    if mutation:
        with pytest.raises(ValueError, match="overlapping"):
            c.validate_basis_groups(spans)
    else:
        c.validate_basis_groups(spans)


@pytest.mark.parametrize("mutation", [None, "removed_evidence", "wrong_paragraph", "paraphrase", "wrong_subject_source"])
def test_actual_nested_occurrence_anchor_chain(scenario, mutation):
    manifest, before, new = scenario
    before["claims/CL-test"]["occurrences"] = [{"source_id": "SRC-test", "anchors": [{
        "evidence_id": "E-test", "paragraph_key": "S0001", "proposed_highlight": {"text": "甲是假设。", "status": "exact"}}]}]
    # occurrences is specifically authorized, not an extra field added by the model.
    for p in manifest.permissions:
        if p.collection == "claims":
            p.fields.append("occurrences")
    put(Path(manifest.authorization.path), {"source_id": "SRC-test", "authorization_id": "owner-test", "authorization_quote": "同意", "human_content_approval": False,
        "permissions": [p.model_dump() for p in manifest.permissions]})
    manifest.authorization.file_sha256 = r.binding(Path(manifest.authorization.path))["file_sha256"]
    frozen = c.freeze(manifest, before)
    p = proposal(frozen, new)
    claim = next(row.payload for row in p.records if row.collection == "claims")
    a = claim["occurrences"][0]["anchors"][0]
    a["proposed_highlight"]["text"] = "甲是假设，不是断言。"
    if mutation == "removed_evidence":
        a["evidence_id"] = "E-other"
    elif mutation == "wrong_paragraph":
        a["paragraph_key"] = "S0002"
    elif mutation == "paraphrase":
        a["proposed_highlight"]["text"] = "unquoted"
    elif mutation == "wrong_subject_source":
        next(row.payload for row in p.records if row.collection == "source_fragments")["source_id"] = "SRC-other"
    if mutation:
        with pytest.raises(ValueError, match="anchor|source"):
            c.validate_proposal(frozen, p)
    else:
        c.validate_proposal(frozen, p)


def test_current_epoch_archive_retains_exact_bytes_for_future_updates(scenario, tmp_path):
    runner, _, manifest = setup_runner(tmp_path, scenario)
    frozen = runner.prepare(manifest)
    Path(manifest.current_sources[0].path).write_text("{\"script\": [{\"text\": \"又一版本\"}]}")
    with pytest.raises(ValueError):
        c.guard_files(frozen)
    c.guard_files(frozen, historical=True)
    rebuilt = c.freeze(manifest, c.index(frozen["before_records"]), physical_snapshots=frozen["physical_snapshots"])
    assert rebuilt == frozen


def test_current_epoch_archive_rejects_tampered_raw_bytes(scenario):
    manifest, before, _ = scenario
    frozen = c.freeze(manifest, before)
    lookup = manifest.current_sources[0].path + ":" + manifest.current_sources[0].file_sha256
    frozen["physical_snapshots"][lookup] = b"fake".hex()
    with pytest.raises(ValueError, match="SHA drift"):
        c.guard_files(frozen, historical=True)


def add_viewpoint(scenario):
    manifest, before, new = scenario
    signature = dict(subject="甲", predicate="是", object="假设", polarity="affirmed", modality="asserted")
    extra = {"canonical_viewpoints": [dict(viewpoint_id="CV-test", current_revision_id="CVR-old", created_from_candidate_id="VIC-old", review_status="system_approved")],
        "viewpoint_revisions": [dict(viewpoint_revision_id="CVR-old", viewpoint_id="CV-test", revision_number=1, core_proposition="甲是假设。",
            proposition_signature=signature, scope={}, provenance={"basis_identity_decision_ids": ["VID-old"], "review_artifact_sha256": "a"*64})],
        "viewpoint_identity_candidates": [dict(identity_candidate_id="VIC-old", candidate_claim_ids=["CL-test"], candidate_viewpoint_ids=["CV-test"],
            proposed_action="match_existing", scope_manifest_sha256="b"*64, generation_fingerprint="c"*64)],
        "viewpoint_identity_decisions": [dict(identity_decision_id="VID-old", identity_candidate_id="VIC-old", decision="match_existing", resolved_viewpoint_id="CV-test",
            reviewer_kind="system", reviewer_id="historical", approval_basis="dual_model_consensus", reason="historical", input_sha256="d"*64, created_at="2026-01-01")],
        "viewpoint_claim_links": [dict(viewpoint_claim_link_id="VCL-test", viewpoint_id="CV-test", validated_against_viewpoint_revision_id="CVR-old",
            claim_id="CL-test", pinned_claim_revision=1, link_type="equivalent_component", component_locator={"claim_sha256": c.semantic_record_sha(before["claims/CL-test"]),
                "statement_component": "甲是假设。", "canonical_spans": [{"start_char":0,"end_char":5,"exact_text":"甲是假设。"}]}, decision_id="VID-old", review_status="system_approved")]}
    for collection, records in normalize_package(extra).items():
        for oid, payload in records.items():
            before[c.key(collection, oid)] = payload
    manifest.permissions.append(c.Permission(collection="viewpoint_claim_links", object_id="VCL-test", fields=list(before["viewpoint_claim_links/VCL-test"])))
    put(Path(manifest.authorization.path), {"source_id": "SRC-test", "authorization_id": "owner-test", "authorization_quote": "同意", "human_content_approval": False,
        "permissions": [p.model_dump() for p in manifest.permissions]})
    manifest.authorization.file_sha256 = r.binding(Path(manifest.authorization.path))["file_sha256"]
    frozen = c.freeze(manifest, before)
    base = deepcopy(frozen)
    base["before_records"] = c.rows({k: p for k, p in before.items() if c.split_key(k)[0] not in extra})
    base["inventory"]["affected_keys"] = sorted(k for k in before if c.split_key(k)[0] not in extra)
    p = proposal(base, new)
    claim = next(row.payload for row in p.records if row.collection == "claims")
    link = deepcopy(before["viewpoint_claim_links/VCL-test"])
    link.update(revision=2, review_status="candidate", pinned_claim_revision=2, link_type="supports",
        component_locator={"claim_sha256": c.semantic_record_sha(claim), "statement_component": claim["statement"],
                           "canonical_spans": [{"start_char":0,"end_char":len(claim["statement"]),"exact_text":claim["statement"]}]})
    p.records.append(c.Row(collection="viewpoint_claim_links", object_id="VCL-test", payload=link))
    used = {d.record_key for d in p.dispositions}
    for k in frozen["inventory"]["affected_keys"]:
        if k not in used:
            action = "update" if k == "viewpoint_claim_links/VCL-test" else "impact_only" if c.split_key(k)[0] in c.IMPACT_ONLY else "retain"
            p.dispositions.append(c.Disposition(record_key=k, action=action, reason="已核验当前目标和历史审核边界"))
    return frozen, p


def review_raw(frozen, proposed):
    packet = c.review_packet(frozen, proposed)
    return {"stage": "initial_review", "artifact_sha256": "e"*64, "completed_at": "2026-10-06T12:00:00Z", "response": {"proposal_sha256":packet["proposal_sha256"],
        "verdicts":[dict(record_key=d.record_key, approved=True, reason="自身原文支持", problems=[]) for d in proposed.dispositions]}}


def test_claim_final_metadata_hash_rebinds_fresh_support_not_old_approval(scenario):
    frozen, p = add_viewpoint(scenario)
    result = c.compile_package(frozen, p, review_raw(frozen, p))
    claim, link = result["claims"][0], result["viewpoint_claim_links"][0]
    assert link["component_locator"]["claim_sha256"] == c.semantic_record_sha(claim)
    assert link["link_type"] == "supports" and link["decision_id"] != "VID-old"
    decision = result["viewpoint_identity_decisions"][0]
    assert decision["reviewer_model_ids"] == ["claude-opus-5-5"]
    assert decision["approval_basis"] == "owner_scoped_independent_terminal_review"
    assert "canonical_viewpoints" not in result and "viewpoint_revisions" not in result


@pytest.mark.parametrize("mutation", ["old_claim_pin", "wrong_locator", "missing_link", "overlap_binding"])
def test_cvp_connection_counterexamples(scenario, mutation):
    frozen, p = add_viewpoint(scenario)
    link = next(row.payload for row in p.records if row.collection == "viewpoint_claim_links")
    if mutation == "old_claim_pin":
        link["pinned_claim_revision"] = 1
    elif mutation == "wrong_locator":
        link["component_locator"]["canonical_spans"][0]["exact_text"] = "dropped subject"
    elif mutation == "missing_link":
        p.records = [row for row in p.records if row.collection != "viewpoint_claim_links"]
        next(d for d in p.dispositions if d.record_key == "viewpoint_claim_links/VCL-test").action = "retain"
    else:
        link["evidence_bindings"] = [{"evidence_step_id":"E-test","source_fragment_id":"FR-test"}]*2
    with pytest.raises(ValueError, match="Claim|locator|duplicate/overlapping"):
        c.validate_proposal(frozen, p)


def test_unrecognized_epoch_cannot_be_routed_by_schema_name(tmp_path):
    from backend.pipeline import viewpoint_applied_source_epochs as epochs
    path = tmp_path / "fake.json"
    put(path, {"source_repair_application": {"schema_version": c.VERSION}})
    with pytest.raises((ValueError, KeyError)):
        epochs.implementation(r.binding(path))


def test_new_cvp_revision_has_new_provenance_and_preserved_old_revision(scenario):
    frozen, p = add_viewpoint(scenario)
    before = c.index(frozen["before_records"])
    cv = deepcopy(before["canonical_viewpoints/CV-test"])
    cv.update(revision=2, current_revision_id="CVR-next", review_status="candidate")
    rev = deepcopy(before["viewpoint_revisions/CVR-old"])
    rev.update(viewpoint_revision_id="CVR-next", supersedes_revision_id="CVR-old", revision=1, revision_number=1,
               core_proposition="甲是假设，不是断言。", review_status="candidate")
    frozen["manifest"]["permissions"] += [c.Permission(collection="canonical_viewpoints",object_id="CV-test",fields=list(cv)).model_dump(),
        c.Permission(collection="viewpoint_revisions",object_id="new-revision-allocation",parent_id="CV-test",create=True,fields=list(rev)).model_dump()]
    p.records += [c.Row(collection="canonical_viewpoints",object_id="CV-test",payload=cv), c.Row(collection="viewpoint_revisions",object_id="CVR-next",payload=rev)]
    next(d for d in p.dispositions if d.record_key=="canonical_viewpoints/CV-test").action = "update"
    p.dispositions.append(c.Disposition(record_key="viewpoint_revisions/CVR-next",action="update",reason="保留来源的重要否定条件"))
    link=next(row.payload for row in p.records if row.collection=="viewpoint_claim_links")
    link["validated_against_viewpoint_revision_id"]="CVR-next"
    result = c.compile_package(frozen,p,review_raw(frozen,p))
    revision=result["viewpoint_revisions"][0]
    assert revision["revision"]==revision["revision_number"]==1 and revision["supersedes_revision_id"]=="CVR-old"
    assert revision["provenance"]["review_artifact_sha256"]=="e"*64
    assert revision["provenance"]["basis_identity_decision_ids"] != ["VID-old"]
    assert "viewpoint_revisions/CVR-old" in c.index(frozen["before_records"])


def test_generic_epoch_composes_two_applied_updates_without_writes(scenario,tmp_path,monkeypatch):
    from backend.pipeline import source_reconciliation_epoch as epoch
    runner,ports,manifest=setup_runner(tmp_path,scenario)
    runner.run(manifest,apply=True,audit=True)
    first=r.binding(runner.root/"effective-package.json")
    original=deepcopy(scenario[1])
    previous=c.FileBinding.model_validate(r.binding(runner.workspace/"source-versions"/(manifest.current_sources[0].file_sha256+".json")))
    current=put(Path(manifest.current_sources[0].path),{"script":[{"text":"甲是一种假设，不是确定断言。"}]})
    auth=put(tmp_path/"authorization-next.json",{"authorization_id":"owner-next","source_id":"SRC-test","authorization_quote":"同意新版本迁移",
        "human_content_approval":False,"permissions":[p.model_dump() for p in manifest.permissions]})
    second_manifest=manifest.model_copy(update={"before_source":previous,"current_sources":[c.FileBinding.model_validate(current)],
        "authorization":c.FileBinding.model_validate(auth),"prior_updates":[c.FileBinding.model_validate(first)]})
    new=c.source_snapshot(current)
    second_ports=FakePorts(second_manifest,ports.states,new,tmp_path)
    second_runner=r.Runner(runner.workspace,second_ports)
    second_runner.run(second_manifest,apply=True,audit=True)
    second_package=r.body(r.read(second_runner.root/"effective-package.json"))
    calls=[]
    monkeypatch.setattr(epoch,"_assert_database",lambda plan,store:calls.append(plan["change_set_id"]))
    indexed_from=[];actual_index=c.index
    def counted_index(rows):
        import sys
        caller=sys._getframe(1).f_code.co_name
        if caller in {"_retained_readset","_predecessor_projection"}: indexed_from.append(caller)
        return actual_index(rows)
    monkeypatch.setattr(c,"index",counted_index)
    projected=epoch._predecessor_projection(second_package,second_ports.states,object())
    assert projected==original and len(calls)==2
    assert indexed_from==["_retained_readset"]*2
    assert ports.applies==second_ports.applies==1
    assert second_ports.calls==["proposal","initial_review"]


def test_changing_authorization_path_or_json_format_cannot_reset_budget(scenario,tmp_path):
    manifest,_,_=scenario
    same=json.loads(Path(manifest.authorization.path).read_text())
    moved=put(tmp_path/"moved-authorization.json",same)
    changed=manifest.model_copy(update={"authorization":c.FileBinding.model_validate(moved)})
    assert r.task_id(manifest)==r.task_id(changed)


@pytest.mark.parametrize("mutation", ["missing_job", "partial_group", "wrong_result", "false_flag", "missing_proof", "resumed"])
def test_completed_epoch_cannot_omit_audit_or_upgrade_flags(scenario, tmp_path, mutation):
    from backend.pipeline import source_reconciliation_epoch as epoch
    runner, ports, manifest = setup_runner(tmp_path, scenario)
    runner.run(manifest, apply=True, audit=True)
    complete = r.body(r.read(runner.root / "complete.json"))
    if mutation == "missing_job":
        complete["audits"] = []
    elif mutation == "partial_group":
        complete["audits"][0]["job"]["claim_ids"] = []
    elif mutation == "wrong_result":
        complete["audits"][0]["result"]["claim_ids"] = []
    elif mutation == "false_flag":
        complete["revisit_required"] = True
    elif mutation == "missing_proof":
        complete["bindings"].pop()
    else:
        complete["production_resumed"] = True
    r.write(runner.root / "complete.json", complete, immutable=False)
    with pytest.raises(ValueError, match="completion"):
        epoch.load(r.binding(runner.root / "effective-package.json"))
    assert ports.applies == 1 and ports.calls == ["proposal", "initial_review"]


@pytest.mark.parametrize('corrected',[False,True])
def test_completed_epoch_uses_original_complete_review_readset_not_whole_freeze(scenario,tmp_path,monkeypatch,corrected):
    from backend.pipeline import source_reconciliation_epoch as epoch
    runner,ports,manifest=setup_runner(tmp_path,scenario)
    ports.refuse_initial=corrected
    runner.run(manifest,apply=True,audit=True)
    whole=r.body(r.read(runner.root/'frozen.json'))
    expected_package=r.body(r.read(runner.root/'effective-package.json'))
    original_plan=r.body(r.read(runner.root/'plan.json'))
    original_completion=r.read(runner.root/'complete.json')
    selected=set(whole['inventory']['affected_keys'])|set(whole['inventory']['context_keys'])
    expected={k:p for k,p in c.index(whole['before_records']).items() if k in selected}
    actual_read=epoch.read
    def bounded_read(path):
        if Path(path).name=='frozen.json':pytest.fail('completed epoch parsed full frozen snapshot')
        return actual_read(path)
    monkeypatch.setattr(epoch,'read',bounded_read)
    monkeypatch.setattr(epoch,'validate_package',lambda *a,**k:pytest.fail('completed epoch entered strict full-freeze reconstruction'))
    monkeypatch.setattr(c,'freeze',lambda *a,**k:pytest.fail('completed epoch rebuilt original graph'))
    package,frame,plan,complete=epoch.load(r.binding(runner.root/'effective-package.json'))
    assert package==expected_package and plan==original_plan and complete==original_completion
    assert frame['completed_readset_projection'] is True
    assert c.index(frame['before_records'])==expected
    assert frame['manifest']==whole['manifest'] and frame['inventory']==whole['inventory']
    assert frame['before']==whole['before'] and frame['after']==whole['after']
    assert ports.applies==ports.audits==1
    assert ports.calls==(['proposal','initial_review','correction','final_review'] if corrected else ['proposal','initial_review'])


@pytest.mark.parametrize('file',['proposal.raw.json','initial_review.raw.json','proposal.packet.json',
                               'initial_review.reservation.json','frozen.json'])
def test_completed_epoch_does_not_replace_actual_raw_or_original_freeze_with_status(scenario,tmp_path,file):
    from backend.pipeline import source_reconciliation_epoch as epoch
    runner,ports,manifest=setup_runner(tmp_path,scenario)
    runner.run(manifest,apply=True,audit=True)
    path=runner.root/file
    value=r.body(r.read(path))
    if file.endswith('.raw.json'):
        value['request_payload_sha256']='0'*64
    else:
        value['unexpected']='not the original saved input'
    r.write(path,value,immutable=False)
    with pytest.raises(ValueError):epoch.load(r.binding(runner.root/'effective-package.json'))
    assert ports.applies==1 and ports.calls==['proposal','initial_review']


def test_frozen_identity_is_preserved_when_completed_readset_omits_unrelated_rows(scenario):
    frozen,p=add_viewpoint(scenario)
    raw=review_raw(frozen,p)
    expected=c.compile_package(frozen,p,raw)
    # The exact original freeze seal, not a new digest of a smaller frame.
    selected=set(frozen['inventory']['affected_keys'])|set(frozen['inventory']['context_keys'])
    frame={**frozen,'before_records':[row for row in frozen['before_records']
        if c.key(row['collection'],row['object_id']) in selected]}
    assert c.compile_package(frame,p,raw,frozen_sha256=c.sha256_json(frozen))==expected
    for bad in ['','not-a-sha','g'*64]:
        with pytest.raises(ValueError):c.compile_package(frame,p,raw,frozen_sha256=bad)


def test_unchanged_reviewed_context_is_not_an_eternal_approval(scenario):
    from backend.pipeline import source_reconciliation_epoch as epoch
    manifest, before, _ = scenario
    frozen = c.freeze(manifest, before)
    drifted = deepcopy(before)
    drifted["questions/Q-test"]["text"] = "unreviewed question"
    with pytest.raises(ValueError, match="retained review context"):
        epoch._retained_readset(frozen, {"operations": []}, drifted)


@pytest.mark.parametrize("count", [1, 1000])
def test_epoch_reuses_only_this_invocations_checked_index(monkeypatch, count):
    from backend.pipeline import source_reconciliation_epoch as epoch
    from backend.pipeline import owner_approved_link_correction as child
    before = {f"source_documents/S{i}": {"source_id": f"S{i}", "revision": 1,
        "source_type": "sermon_transcript", "transcript_id": f"sermon-{i}",
        "nested": {"text": "完整原文"}} for i in range(count)}
    before["claims/C"] = {"claim_id": "C", "revision": 1, "statement": "旧版"}
    current = deepcopy(before)
    current["claims/C"] = {"claim_id": "C", "revision": 2, "statement": "已审核版本"}
    plan = {"operations": [{"collection": "claims", "object_id": "C",
        "payload": {"claim_id": "C", "statement": "已审核版本"}, "after_revision": 2}]}
    frozen = {"before_records": c.rows(before), "inventory": {
        "affected_keys": ["claims/C"], "context_keys": list(before)}}
    package = {"identity": "unchanged-package"}
    audit = {"result": {"unchanged": True}, "job": {"claim_ids": ["C"], "viewpoint_ids": []}}
    monkeypatch.setattr(epoch, "load", lambda bound: (package, frozen, plan, {"audits": [audit]}))
    checks = []
    monkeypatch.setattr(epoch, "_assert_database", lambda plan, store: checks.append("SQL"))
    monkeypatch.setattr(child, "source_projection", lambda complete, states, store: states)
    class Ports:
        def __init__(self, store): pass
        def current_snapshot(self,keys):
            checks.append("current")
            return {k:deepcopy(current[k]) for k in keys}
        def validate_audit(self, result, claims, views):
            assert (result, claims, views) == (audit["result"], ["C"], [])
            checks.append("audit")
        def consume_completed_audit(self,outcome,complete,states):
            self.validate_audit(outcome['result'],outcome['job']['claim_ids'],outcome['job']['viewpoint_ids'])
    monkeypatch.setattr(epoch, "ProductionPorts", Ports)
    actual_index = c.index
    indexed = []
    def counted_index(rows):
        indexed.append(len(rows))
        return actual_index(rows)
    monkeypatch.setattr(c, "index", counted_index)
    expected = (package, {c.split_key(k): p for k, p in before.items()},
        {c.split_key(k): p for k, p in current.items()})
    assert epoch.verified_applied({}, object()) == expected
    assert indexed == [count + 1]
    assert checks == ["SQL", "current", "audit"]
    # Returned mutable projections cannot mutate the frozen input or live state.
    result = epoch.verified_applied({}, object())
    result[1]["source_documents", "S0"]["nested"]["text"] = "changed by caller"
    assert frozen["before_records"] == c.rows(before)
    assert current["source_documents/S0"]["nested"]["text"] == "完整原文"
    assert indexed == [count + 1, count + 1]
    assert checks == ["SQL", "current", "audit"] * 2
    # A later invocation MUST recheck both written and retained current values.
    current["claims/C"]["revision"] = 3
    with pytest.raises(ValueError, match="postimage"):
        epoch.verified_applied({}, object())
    current["claims/C"]["revision"] = 2
    current["source_documents/S0"]["revision"] = 2
    with pytest.raises(ValueError, match="retained review context"):
        epoch.verified_applied({}, object())


def test_parent_reconstruction_reuses_history_not_sql_or_current_boundary(scenario, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from backend.pipeline import owner_approved_link_correction as child
    from backend.pipeline import immutable_proof_reuse
    from backend.pipeline.viewpoint_validation_context import ValidationContext
    runner, ports, manifest = setup_runner(tmp_path, scenario)
    runner.run(manifest, apply=True, audit=True)
    parent = runner.root
    frozen = r.body(r.read(parent/"frozen.json"))
    plan = r.body(r.read(parent/"plan.json"))
    package = r.body(r.read(parent/"effective-package.json"))
    old = c.index(frozen["before_records"])
    post = c.assert_plan_postimages(package, plan, old)
    expected = runtime.expected_invalidations(old, post, plan)
    identity = "LR-"+c.sha256_json({"parent":r.binding(parent/"effective-package.json"),
        "before_link":{"revision":1},"after_link":{"revision":2}})
    root = parent/"authorized-link-corrections"/identity
    adjudication = tmp_path/"audit/own-source-adjudication"
    r.write(root/"packet.json", {"source_root":str(parent), "before_link":{"revision":1},
        "after_link":{"revision":2}, "adjudication_root":str(adjudication),
        "original_audit":{"path":str(adjudication.parent/"independent-audit/audit.json")},
        "prior_bindings":[], "old_audit_source_proofs":[]})
    r.write(root/"before.json", {"records":c.rows(expected)})
    # Isolate parent history/current checks from the separately tested child
    # target and historical audit validators; no real SQL/provider is called.
    monkeypatch.setattr(child, "validate_target", lambda packet: None)
    monkeypatch.setattr(immutable_proof_reuse, "verify_once", lambda *args: None)
    reconstructed=[];actual=c.assert_plan_postimages
    def counted(*args):
        reconstructed.append(1)
        return actual(*args)
    monkeypatch.setattr(c,"assert_plan_postimages",counted)
    sql=[];applied=[True]
    def committed(p):
        assert p==plan
        sql.append(1)
        return applied[0]
    production=SimpleNamespace(committed=committed,store=object())
    with ValidationContext("same parent stage"):
        assert child.validate_parent(root,expected,production)==(frozen,old,post,plan)
        assert child.validate_parent(root,expected,production)==(frozen,old,post,plan)
        applied[0]=False
        with pytest.raises(ValueError,match="not actually applied"):
            child.validate_parent(root,expected,production)
        applied[0]=True
        drifted=deepcopy(expected);drifted["questions/Q-test"]["text"]="unreviewed"
        with pytest.raises(ValueError,match="exact entire applied source boundary"):
            child.validate_parent(root,drifted,production)
        assert reconstructed==[1] and sql==[1]*4
    # Standalone contexts do not retain approval beyond their closing boundary.
    with ValidationContext("new parent stage"):
        assert child.validate_parent(root,expected,production)==(frozen,old,post,plan)
    assert reconstructed==[1,1] and sql==[1]*5
    assert ports.applies==1 and ports.calls==["proposal","initial_review"]


@pytest.mark.parametrize("name", ["frozen.json","effective-package.json","plan.json",
    "proposal.raw.json","initial_review.packet.json","initial_review.raw.json","source"])
def test_parent_frame_reuse_cannot_ignore_changed_physical_dependency(scenario,tmp_path,name):
    from backend.pipeline import owner_approved_link_correction as child
    from backend.pipeline.viewpoint_validation_context import ValidationContext
    runner,ports,manifest=setup_runner(tmp_path,scenario)
    runner.run(manifest,apply=True,audit=True)
    target=Path(manifest.current_sources[0].path) if name=="source" else runner.root/name
    with pytest.raises(ValueError,match="drift"):
        with ValidationContext("changed parent input"):
            child._parent_frame(runner.root)
            target.write_bytes(target.read_bytes()+b" ")
            child._parent_frame(runner.root)


def test_initial_model_packet_cannot_drop_frozen_context(scenario, tmp_path):
    runner, _, manifest = setup_runner(tmp_path, scenario)
    runner.run(manifest)
    packet = r.body(r.read(runner.root / "proposal.packet.json"))
    packet["context_sources"] = [{"not": "actual full context"}]
    r.write(runner.root / "proposal.packet.json", packet, immutable=False)
    with pytest.raises(ValueError, match="frozen source or consumer context"):
        r.validate_package(r.body(r.read(runner.root / "effective-package.json")))


def test_saved_sealed_package_has_the_same_authority_as_store_body(scenario, tmp_path):
    from backend.api.canonical_repository.reviewed_candidate_contract import validate_store_package_authorization
    runner, _, manifest = setup_runner(tmp_path, scenario)
    runner.run(manifest)
    artifact = r.read(runner.root / "effective-package.json")
    validate_store_package_authorization(artifact)
    validate_store_package_authorization(r.body(artifact))
    tampered = deepcopy(artifact)
    tampered["package_id"] = "unreviewed"
    with pytest.raises(ValueError, match="artifact seal drift"):
        validate_store_package_authorization(tampered)
    with pytest.raises(ValueError, match="exactly reviewed records"):
        validate_store_package_authorization(r.seal(r.body(tampered)))


def test_original_provider_response_is_sha_bound(scenario, tmp_path):
    runner, _, manifest = setup_runner(tmp_path, scenario)
    runner.run(manifest)
    raw = r.body(r.read(runner.root / "initial_review.raw.json"))
    raw["provider_raw"] = {"stdout": "different original"}
    r.write(runner.root / "initial_review.raw.json", raw, immutable=False)
    with pytest.raises(ValueError, match="original subscription response"):
        r.validate_package(r.body(r.read(runner.root / "effective-package.json")))


def test_sql_boundary_forces_complete_frozen_registry_guard(monkeypatch):
    from types import SimpleNamespace
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    monkeypatch.setattr(runtime, "validate_apply_plan", lambda plan, metadata: "a" * 64)
    store = PostgresKnowledgeStore.__new__(PostgresKnowledgeStore)
    plan = SimpleNamespace(source_kind=c.VERSION)
    with pytest.raises(ValueError, match="Registry guard differs"):
        store.apply_plan(plan, expected_registry_fingerprint_sha256="b" * 64)


@pytest.mark.parametrize("mutation", [None, "foreign_source", "missing_sentence", "ambiguous_quote"])
def test_unadmitted_candidate_is_bound_to_its_own_exact_sentence(scenario, mutation):
    manifest, before, new = scenario
    frozen = c.freeze(manifest, before)
    p = proposal(frozen, new)
    p.sentence_dispositions[0].action = "unadmitted_candidate"
    sid = p.sentence_dispositions[0].sentence_id
    p.unadmitted_candidates = [dict(candidate_id="UN-test", statement="明确新条件", source_id="SRC-test", sentence_ids=[sid],
        citations=[dict(paragraph_key="S0001", verbatim_excerpt=new["paragraphs"]["S0001"]["text"])])]
    if mutation == "foreign_source":
        p.unadmitted_candidates[0]["source_id"] = "other-sermon"
    elif mutation == "missing_sentence":
        p.unadmitted_candidates[0]["sentence_ids"] = []
    elif mutation == "ambiguous_quote":
        p.unadmitted_candidates[0]["citations"][0]["verbatim_excerpt"] = "not said"
    if mutation is None:
        assert set(c.validate_proposal(frozen, p)) == set(before)
    else:
        with pytest.raises(ValueError, match="candidate"):
            c.validate_proposal(frozen, p)


def test_store_projection_preserves_omitted_fields_not_new_schema_defaults(scenario):
    _, before, _ = scenario
    package = {"package_id": "x", "questions": [{"question_id": "Q-test", "text": "新的问题"}]}
    payload = {**before["questions/Q-test"], "text": "新的问题"}
    plan = {"operations": [dict(operation="update", collection="questions", object_id="Q-test", before_revision=1, after_revision=2, payload=payload)]}
    post = c.assert_plan_postimages(package, plan, before)
    assert post["questions/Q-test"]["answer_claim_ids"] == ["CL-test"]


def test_epoch_membership_validates_full_proof_but_preserves_partial_caller_population(scenario, tmp_path, monkeypatch):
    from backend.pipeline import source_reconciliation_epoch as epoch
    runner, ports, manifest = setup_runner(tmp_path, scenario)
    runner.run(manifest, apply=True, audit=True)
    ports.store = object()
    monkeypatch.setattr(epoch, "ProductionPorts", lambda store=None: ports)
    monkeypatch.setattr(epoch, "_assert_database", lambda plan, store: None)
    package = r.body(r.read(runner.root / "effective-package.json"))
    partial = {("claims", "CL-test"): {"payload": ports.states["claims/CL-test"], "revision": 2,
        "content_sha256": record_content_sha(ports.states["claims/CL-test"]), "retired": False}}
    membership = json.loads(Path(manifest.accepted_membership.path).read_text())
    restored, changed = epoch.membership_snapshot(membership, partial, package, set(), {"CL-test"})
    assert set(restored) == set(partial) and changed == ["CL-test"]
    assert restored["claims", "CL-test"]["payload"] == scenario[1]["claims/CL-test"]
    assert ports.applies == 1 and ports.calls == ["proposal", "initial_review"]


def test_completed_predecessor_entire_caller_projection_without_whole_history(scenario,tmp_path,monkeypatch):
    from backend.pipeline import source_reconciliation_epoch as epoch
    runner,ports,manifest=setup_runner(tmp_path,scenario)
    runner.run(manifest,apply=True,audit=True)
    package=r.body(r.read(runner.root/'effective-package.json'))
    current={**ports.states,'claims/unrelated_future':{'claim_id':'unrelated_future','revision':1,'statement':'preserve exactly'}}
    original=deepcopy(current)
    actual_read=epoch.read
    def no_snapshot(path):
        if Path(path).name in {'frozen.json','before.json','readback.json'}:
            pytest.fail('completed predecessor parsed full historical snapshot')
        return actual_read(path)
    monkeypatch.setattr(epoch,'read',no_snapshot)
    monkeypatch.setattr(epoch,'_assert_database',lambda *a:None)
    monkeypatch.setattr(epoch,'_predecessor_projection',lambda *a,**k:pytest.fail('legacy reconstruction'))
    expected={**scenario[1],'claims/unrelated_future':current['claims/unrelated_future']}
    assert epoch._completed_predecessor_projection(package,current,object())==expected
    assert current==original and ports.applies==1 and ports.calls==['proposal','initial_review']
    current['claims/CL-test']['statement']='unapproved current state'
    with pytest.raises(ValueError,match='postimage'):
        epoch._completed_predecessor_projection(package,current,object())


@pytest.mark.parametrize('drift',[None,'retired','revision','SHA','identity','missing'])
def test_current_source_snapshot_reads_only_declared_real_rows_and_storage_metadata(monkeypatch,drift):
    from backend.pipeline import viewpoint_validation_context as context
    payload={'claim_id':'C','revision':2,'statement':'actual reviewed value'}
    state={'payload':deepcopy(payload),'revision':2,'content_sha256':record_content_sha(payload),'retired':False}
    if drift=='retired':state['retired']=True
    if drift=='revision':state['revision']=3
    if drift=='SHA':state['content_sha256']='0'*64
    if drift=='identity':state['payload']['claim_id']='OTHER'
    observed={('claims','C'):state} if drift!='missing' else {}
    calls=[];store=object()
    monkeypatch.setattr(context,'read_current_objects',lambda s,k:calls.append((s,k)) or observed)
    ports=runtime.ProductionPorts(store)
    if drift:
        with pytest.raises(ValueError):ports.current_snapshot({'claims/C'})
    else:
        assert ports.current_snapshot({'claims/C'})=={'claims/C':payload}
    assert calls==[(store,{('claims','C')})]


@pytest.mark.parametrize('drift',[None,'detached','result','raw_or_source','duplicate'])
def test_completed_audit_consumes_original_result_and_all_physical_bindings_not_old_prompts(tmp_path,monkeypatch,drift):
    audit=put(tmp_path/'audit.json',{'original':'Gemini report'})
    cache=put(tmp_path/'cache.json',{'original':'saved raw judgement'})
    result={'claim_ids':['C'],'viewpoint_ids':['V'],'audit':audit,'original_audit':audit,
        'source_proofs':[audit,cache],'resolution':None,'revisit_required':False,'independent_audit_complete':True}
    bound=r.write(tmp_path/'result.json',result)
    outcome={'job':{'claim_ids':['C'],'viewpoint_ids':['V']},'result':result,'binding':r.binding(tmp_path/'result.json')}
    complete={'status':'audited_complete','audits':[deepcopy(outcome)]}
    if drift=='detached':complete['audits']=[]
    if drift=='result':result['claim_ids']=['OTHER']
    if drift=='raw_or_source':put(tmp_path/'cache.json',{'changed':'not original'})
    if drift=='duplicate':result['source_proofs'].append(cache)
    ports=runtime.ProductionPorts(object());calls=[]
    monkeypatch.setattr(ports,'snapshot',lambda *a:pytest.fail('completed audit SELECT-all'))
    monkeypatch.setattr(ports,'validate_audit',lambda *a:pytest.fail('completed audit prompt reconstruction'))
    monkeypatch.setattr(ports,'_validate_audit_population',lambda *a:calls.append(a))
    if drift:
        with pytest.raises(ValueError):ports.consume_completed_audit(outcome,complete,{})
        assert calls==[]
    else:
        ports.consume_completed_audit(outcome,complete,{'related':'only'})
        assert calls==[(result,['C'],['V'],{'related':'only'})]


def test_frozen_registry_fingerprint_matches_sql_including_empty_collections(scenario):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    _, states, _ = scenario
    class Cursor:
        def execute(self, sql, parameters):
            self.collection = parameters[0]
        def fetchall(self):
            return [(p,) for k, p in states.items() if c.split_key(k)[0] == self.collection]
    PostgresKnowledgeStore._assert_registry_fingerprint(Cursor(), expected_sha256=c.fingerprint(states), collections=list(c.KNOWLEDGE_COLLECTIONS))
    changed = deepcopy(states)
    changed["product_dependencies/DEP-new"] = {"dependency_id": "DEP-new", "revision": 1}
    assert c.fingerprint(changed) != c.fingerprint(states)


def test_production_environment_failure_is_before_any_model_reservation(scenario, tmp_path):
    runner, ports, manifest = setup_runner(tmp_path, scenario)
    def preflight(stage):
        raise ValueError("dirty worktree or missing SSD")
    ports.before_model_call = preflight
    with pytest.raises(ValueError, match="dirty worktree"):
        runner.run(manifest)
    assert ports.calls == [] and ports.applies == 0
    assert not (runner.root / "proposal.reservation.json").exists()
