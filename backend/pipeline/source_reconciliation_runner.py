"""Durable, bounded reconciliation orchestration with explicit production ports.

The canonical directory is keyed by source epochs + owner authorization, not by
timestamps, process IDs, or an operator-selected recovery root. Reservations
survive crashes. A missing response is uncertain, never permission to retry.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
from typing import Any, Protocol

from backend.api.canonical_repository.viewpoint_foundation import canonical_json, sha256_json
from backend.pipeline import source_reconciliation as contract
from backend.pipeline.viewpoint_validation_context import validated_history

STAGES = ("proposal", "proposal_schema_recovery", "permission_input_recovery_proposal", "initial_review", "correction", "final_review", "final_review_input_repair", "normalization_recovery_review")


def effective_proposal(frozen, response):
    if frozen['manifest'].get('evidence_binding_repair'):
        from backend.pipeline.claim_evidence_binding import normalized_proposal
        return normalized_proposal(frozen,response)
    if frozen['manifest'].get('anchor_qualification'):
        from backend.pipeline.claim_anchor_qualification import normalized_proposal
        return normalized_proposal(frozen,response)
    if frozen['manifest'].get('viewpoint_scope_repair'):
        from backend.pipeline.cvp_scope_management import effective_proposal as scoped
        return scoped(response)
    if frozen["manifest"].get("claim_issue"):
        from backend.pipeline.claim_issue_management import normalize_proposal
        return normalize_proposal(frozen, response)
    if frozen["manifest"].get("deferred_repair"):
        from backend.pipeline.deferred_claim_repair import normalize_proposal
        return normalize_proposal(frozen, response)
    return contract.Proposal.model_validate(response)


def corrected_proposal(root, frozen, response):
    """Select only the exact already-approved anchor cut, never handwrite it."""
    proposed=effective_proposal(frozen,response)
    if not frozen['manifest'].get('anchor_qualification'):return proposed,None
    from backend.pipeline.claim_anchor_qualification import direction,validate_delta
    manifest=contract.Manifest.model_validate(frozen['manifest']);before=contract.index(frozen['before_records'])
    d=direction(manifest,before,frozen.get('physical_snapshots'));key=contract.key('claims',d['claim_id'])
    row=next(r for r in proposed.records if contract.key(r.collection,r.object_id)==key)
    try:validate_delta(frozen,before[key],row.payload,key)
    except ValueError:
        from backend.pipeline.source_schema_recovery import select
        packet=body(read(root/'proposal.packet.json'))
        first=validate_raw(root,select(root,packet),packet)
        initial=validate_raw(root,'initial_review',body(read(root/'initial_review.packet.json')))
        approved=[v for v in initial['response']['verdicts'] if v['record_key']==key]
        original=effective_proposal(frozen,first['response'])
        kept=next(r for r in original.records if contract.key(r.collection,r.object_id)==key)
        validate_delta(frozen,before[key],kept.payload,key)
        if (len(approved)!=1 or not approved[0]['approved'] or approved[0]['problems']
                or {k:v for k,v in contract._semantic(row.payload).items() if k!='occurrences'}
                    !={k:v for k,v in contract._semantic(kept.payload).items() if k!='occurrences'}):
            raise ValueError('anchor component cannot borrow approval for another Claim meaning')
        proposed.records=[kept if contract.key(r.collection,r.object_id)==key else r for r in proposed.records]
        kept_disposition=next(v for v in original.dispositions if v.record_key==key)
        proposed.dispositions=[kept_disposition if v.record_key==key else v for v in proposed.dispositions]
        return proposed,{'schema_version':'exact_initial_approved_anchor_component_v1','record_key':key,
            'original_proposal_raw_sha256':first['artifact_sha256'],'initial_review_raw_sha256':initial['artifact_sha256'],
            'actual_correction_response_sha256':sha256_json(response),
            'selected_postimage_sha256':sha256_json(kept.payload),'automatic_content_approval':False,
            'explanation':'Correction copied unrelated anchors despite saying it retained the initial cut. '
                'Use the BYTE-EXACT initial Claim postimage and disposition already independently approved; '
                'do not author another cut or edit correction raw. All other candidate records remain the actual correction. '
                'The full joint postimage still requires the original unused independent final approval.'}
    return proposed,None


def initial_review_base(frozen, proposal):
    if frozen['manifest'].get('evidence_binding_repair'):
        try:return contract.review_packet(frozen,proposal)
        except ValueError:return contract.review_packet(frozen,proposal,initial_evidence_draft=True)
    if frozen['manifest'].get('anchor_qualification'):
        missing=set(contract.index(proposal.records))-set(d.record_key for d in proposal.dispositions)
        return contract.review_packet(frozen,proposal,initial_anchor_draft=bool(missing))
    from backend.pipeline.claim_evidence_completion import direction_for, initial_draft_review
    if direction_for(frozen.get('claim_issue_context',{})):
        return initial_draft_review(frozen,proposal)
    if frozen.get('claim_issue_context', {}).get('complete_input_repair'):
        return contract.review_packet(frozen, proposal)
    if frozen.get('owner_claim_completion') and any(d.action=='unresolved' for d in proposal.dispositions):
        if not proposal.records:
            return contract.review_packet(frozen,proposal,initial_claim_draft=True)
        return contract.review_packet(frozen,proposal,review_only=True)
    if not frozen.get('claim_issue_context', {}).get('scope_amendment'):
        if frozen['manifest'].get('claim_issue') and frozen.get('sql_scope'):
            try:
                return contract.review_packet(frozen, proposal)
            except ValueError:
                # The original initial review may evaluate a bounded draft,
                # but cannot approve it for writing or bypass the unique correction.
                return contract.review_packet(frozen, proposal, initial_claim_draft=True)
        return contract.review_packet(frozen, proposal)
    unresolved = any(d.action == 'unresolved' for d in proposal.dispositions)
    return contract.review_packet(frozen, proposal, review_only=unresolved)


def request_limit(adapter, path, *, saved=None):
    """Shared campaign budget; historical answers keep their actual identity."""
    from backend.pipeline.viewpoint_resolution_runtime import effective_request_limit, read_artifact
    if saved is not None:
        budget=saved.get('request_budget')
        limit=getattr(adapter,'max_request_bytes',500000)
    else:
        limit,budget=effective_request_limit(adapter,path)
    if budget is not None:
        from backend.pipeline.cvp_sql_flow import check_binding
        check_binding(budget['authorization'])
        auth=read_artifact(Path(budget['authorization']['path']))
        binding_path=next((p/'request-limit-binding.json' for p in Path(path).resolve().parents
                           if (p/'request-limit-binding.json').exists()),None)
        if (binding_path is None or read_artifact(binding_path)!=budget
                or auth.get('schema_version')!='cvp_sql_request_limit_authorization.v1'
                or auth.get('campaign_sha256')!=budget['campaign_sha256']
                or auth.get('max_request_bytes')!=budget['max_request_bytes']
                or auth.get('authorized_by')!='owner' or not auth.get('owner_direction')):
            raise ValueError('source model campaign request budget drift')
        limit=budget['max_request_bytes']
    return limit or 500000,budget


def stage_adapter(adapter, packet, path, *, saved=None):
    """Select existing lossless wire only before reservation; authenticate saved wire exactly."""
    from backend.pipeline.viewpoint_resolution_runtime import response_adapter
    path = Path(path)
    selected = response_adapter(adapter, saved)
    if saved is not None:
        # Program-bound and legacy stages both retain their actual codec/config.
        candidates = [adapter, response_adapter(adapter)]
        factory = getattr(adapter, 'program_bound_copy', None)
        if callable(factory):
            candidates.append(factory())
        keys = ('model_id', 'backend', 'prompt_sha256', 'generation_config_sha256')
        for candidate in candidates:
            factory = getattr(candidate, 'shared_keys_copy', None)
            if callable(factory) and getattr(candidate, '_shared_record_payload', False):
                smaller = factory()
                if all(saved.get(k) == getattr(smaller, k) for k in keys):
                    return smaller
        return selected  # Existing exact reservation/raw validation rejects drift.
    blockers = [path.with_name(path.name.removesuffix('.raw.json') + suffix)
                for suffix in ('.reservation.json', '.call-intent.json', '.request-encoding.json')]
    if not any(p.exists() for p in blockers):
        limit, _ = request_limit(selected, path)
        if getattr(selected, '_shared_record_payload', False):
            for name in ('shared_keys_copy', 'shared_keys_v2_copy', 'shared_values_copy', 'shared_shapes_copy', 'shared_shapes_v2_copy'):
                if selected.request_bytes(packet) <= limit: break
                factory = getattr(selected, name, None)
                if callable(factory):
                    smaller = factory()
                    if smaller.request_bytes(packet) < selected.request_bytes(packet):
                        selected = smaller
    return selected


class Stop(ValueError):
    def __init__(self, reason: str, *, status: str = "root_cause_required", waiting_for: str = "system"):
        # Stopping production is not automatically a request for human content
        # approval. Source/input/code/flow causes must be checked first; this
        # classification never grants another call, retry or write authority.
        super().__init__(reason)
        self.status, self.waiting_for = status, waiting_for


def seal(body: dict) -> dict:
    return {**body, "artifact_sha256": sha256_json(body)}


@validated_history(bound_files_only=True)
def read(path: Path) -> dict:
    from backend.pipeline.viewpoint_validation_context import historical_context
    ctx = historical_context()
    value = ctx.read(path) if ctx else json.loads(path.read_text(encoding="utf-8"))
    from backend.pipeline.immutable_proof_reuse import restore_snapshot
    value=restore_snapshot(path,value)
    stated = value.get("artifact_sha256")
    if stated != sha256_json({k: v for k, v in value.items() if k != "artifact_sha256"}):
        raise ValueError("artifact seal drift: " + str(path))
    return value


def write(path: Path, body: dict, *, immutable: bool = True) -> dict:
    value = seal(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable and path.exists():
        if read(path) != value:
            raise ValueError("immutable artifact differs: " + str(path))
        return value
    temporary = path.with_suffix(path.suffix + ".partial")
    from backend.pipeline.immutable_proof_reuse import pack_snapshot
    serialized=pack_snapshot(path,value) if immutable else value
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(canonical_json(serialized) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return value


def body(value: dict) -> dict:
    return {k: v for k, v in value.items() if k != "artifact_sha256"}


def task_id(manifest: contract.Manifest, physical_snapshots: dict | None = None) -> str:
    # Changing Registry state/created_at/output directories cannot reset calls.
    authorization = json.loads(contract.bound_bytes(manifest.authorization, physical_snapshots))
    return "SR-" + sha256_json({"version": contract.VERSION, "source_id": manifest.source_id,
        "before": manifest.before_source.file_sha256,
        "after": sorted(b.file_sha256 for b in manifest.current_sources),
        "authorization_id": authorization.get("authorization_id"),
        "membership": manifest.accepted_membership.file_sha256})


@contextmanager
def exclusive(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "worker.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Stop("another worker owns this update", status="busy", waiting_for="system") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class Ports(Protocol):
    """Production implementation lives in source_reconciliation_runtime."""
    def snapshot(self) -> dict[str, dict]: ...
    def adapter(self, stage: str) -> Any: ...
    def plan(self, package: dict) -> dict: ...
    def backup(self, root: Path) -> dict: ...
    def committed(self, plan: dict) -> bool: ...
    def apply(self, package: dict, plan: dict, frozen: dict) -> dict: ...
    def readback(self, plan: dict, expected: dict[str, dict], before: dict[str, dict]) -> dict[str, dict]: ...
    def audit(self, root: Path, claim_ids: list[str], viewpoint_ids: list[str]) -> dict: ...
    def audit_saved(self, root: Path, claim_ids: list[str], viewpoint_ids: list[str]) -> dict: ...
    def validate_audit(self, result: dict, claim_ids: list[str], viewpoint_ids: list[str]) -> None: ...


class Runner:
    def __init__(self, workspace: Path, ports: Ports):
        self.workspace, self.ports = workspace.resolve(), ports
        self.root: Path | None = None

    def state(self, status: str, *, waiting_for: str = "system", next_action: str, **extra) -> dict:
        return write(self.root / "state.json", {"status": status, "waiting_for": waiting_for,
            "next_action": next_action, "production_resumed": False, **extra}, immutable=False)

    def prepare(self, manifest: contract.Manifest) -> dict:
        self.root = self.workspace / task_id(manifest)
        with exclusive(self.root):
            if (self.root / "frozen.json").exists():
                frozen = body(read(self.root / "frozen.json"))
                if frozen["manifest"] != manifest.model_dump():
                    raise Stop("same authorized source update has different input; budget not reset")
                contract.guard_files(frozen)
                return frozen
            frozen = contract.freeze(manifest, self.ports.snapshot())
            write(self.root / "frozen.json", frozen)
            # Exact physical source versions survive later edits to the live
            # transcript. This is a content-addressed archive, not a new source.
            for b in [manifest.before_source, *manifest.current_sources]:
                archive = self.workspace / "source-versions" / (b.file_sha256 + ".json")
                raw = contract.bound_bytes(b, frozen["physical_snapshots"])
                archive.parent.mkdir(parents=True, exist_ok=True)
                if archive.exists():
                    if archive.read_bytes() != raw:
                        raise ValueError("immutable physical source archive drift")
                else:
                    # Exclusive creation prevents overwriting another task's archive.
                    with archive.open("xb") as output:
                        output.write(raw)
                        output.flush()
                        os.fsync(output.fileno())
            self.state("prepared", next_action="run the bounded proposal/review workflow")
            return frozen

    def guard(self, frozen: dict, *, committed: bool = False) -> None:
        contract.guard_files(frozen)
        if not committed and contract.fingerprint(self.ports.snapshot()) != frozen["registry_sha256"]:
            raise Stop("Registry changed since freeze; no model or write replay", status="proof_failure", waiting_for="system")

    def call(self, stage: str, packet: dict) -> dict:
        # The codec's first-occurrence dictionary must see the same key order
        # before persistence and after recovery, not only the same JSON hash.
        packet = json.loads(canonical_json(packet))
        if stage not in STAGES:
            raise ValueError("unknown model stage")
        if stage == 'final_review_input_repair':
            from backend.pipeline.source_review_input_repair import NAME
            direction = read(self.root/NAME)
            if packet.get('input_repair', {}).get('authorization') != direction:
                raise Stop('input repair call lacks exact immutable owner direction')
        if stage == 'normalization_recovery_review':
            from backend.pipeline.source_normalization_recovery import NAME
            if packet.get('normalization_engineering_recovery',{}).get('authorization') != read(self.root/NAME):
                raise Stop('normalization recovery lacks immutable fixed-parent direction')
        deferred = bool(packet.get("manifest", {}).get("deferred_repair"))
        issue = bool(packet.get("manifest", {}).get("claim_issue"))
        scope = bool(packet.get('manifest', {}).get('viewpoint_scope_repair'))
        role = 'proposal' if stage in {'proposal_schema_recovery','permission_input_recovery_proposal'} else 'initial_review' if stage == 'normalization_recovery_review' else 'final_review' if stage == 'final_review_input_repair' else stage
        from backend.pipeline.claim_issue_management import eligibility_profile
        if issue and eligibility_profile(packet):
            adapter = self.ports.claim_issue_adapter(role, evidence_eligibility=True)
        else:
            adapter = self.ports.scope_adapter(role) if scope else self.ports.claim_issue_adapter(role) if issue else self.ports.deferred_adapter(role) if deferred else self.ports.adapter(role)
        from backend.pipeline.claim_first_management import scoped_adapter
        from backend.pipeline.claim_evidence_binding import scoped_adapter as evidence_adapter
        adapter=evidence_adapter(adapter,packet,role)
        from backend.pipeline.source_permission_recovery import adapter as permission_adapter
        adapter=permission_adapter(adapter,packet)
        adapter = scoped_adapter(adapter, packet)
        from backend.pipeline.cvp_pending_link_management import scoped_adapter as link_scoped_adapter
        adapter = link_scoped_adapter(adapter,packet)
        from backend.pipeline.claim_citation_purpose_review import scoped_adapter as citation_scoped_adapter
        adapter = citation_scoped_adapter(adapter,packet)
        from backend.pipeline.source_normalization_recovery import scoped_adapter as recovery_scoped_adapter
        adapter = recovery_scoped_adapter(adapter,packet)
        from backend.pipeline.claim_evidence_completion import scoped_adapter as completion_adapter
        adapter = completion_adapter(adapter,packet)
        from backend.pipeline.claim_source_write_slice import scoped_adapter as slice_adapter
        adapter = slice_adapter(adapter,packet)
        from backend.pipeline.source_schema_recovery import adapter as schema_adapter,select as schema_stage
        if stage=='proposal_schema_recovery' and schema_stage(self.root,packet)!=stage:
            raise Stop('schema recovery lacks fixed-parent rejection proof')
        adapter=schema_adapter(adapter,packet,stage)
        from backend.pipeline.viewpoint_resolution_runtime import response_adapter, bound_model_response
        raw_path = self.root / (stage + '.raw.json')
        adapter = stage_adapter(adapter, packet, raw_path, saved=read(raw_path) if raw_path.exists() else None)
        expected = ("claude-opus-5-5", "claude_subscription") if "review" in stage else ("gpt-6.1-sol", "codex_subscription")
        if (adapter.model_id, adapter.backend) != expected:
            raise Stop("model/backend differs from frozen subscription policy", status="proof_failure", waiting_for="system")
        proof = adapter.request_encoding_proof(packet)
        size = adapter.request_bytes(packet)
        limit,budget=request_limit(adapter,raw_path,saved=read(raw_path) if raw_path.exists() else None)
        if (size > limit or not proof or proof.get("exact_roundtrip_verified") is not True
                or proof["semantic_payload_sha256"] != sha256_json(packet)
                or proof["restored_payload_sha256"] != sha256_json(packet)):
            raise Stop(f"actual request exceeds {limit} or lossless encoding failed", status="proof_failure", waiting_for="system")
        request = {"model_id": adapter.model_id, "backend": adapter.backend,
            "prompt_sha256": adapter.prompt_sha256, "generation_config_sha256": adapter.generation_config_sha256,
            "request_payload_sha256": sha256_json(packet), "request_encoding_proof": proof,
            "request_bytes": size, "stage": stage, "automatic_retry": False}
        if budget is not None:request['request_budget']=budget
        raw_path = self.root / (stage + ".raw.json")
        reservation = self.root / (stage + ".reservation.json")
        write(self.root / (stage + ".packet.json"), packet)
        if raw_path.exists():
            raw = read(raw_path)
            if any(raw.get(k) != v for k, v in request.items()) or raw["response_sha256"] != sha256_json(raw["response"]):
                raise Stop("cached model request/response identity drift", status="proof_failure", waiting_for="system")
            if not reservation.exists() or body(read(reservation)) != request:
                raise Stop("cached model reservation missing", status="proof_failure", waiting_for="system")
            bound_model_response(adapter, packet, raw['response'], raw_path)
            return raw
        if reservation.exists():
            raise Stop("reserved call has no verified reply; do not repeat", status="call_uncertain", waiting_for="system")
        preflight = getattr(self.ports, "before_model_call", None)
        if preflight:
            preflight(stage)
        # Persist the reservation before any side effect. Errors do not reset it.
        write(reservation, request)
        self.state(stage, next_action="await the single subscription response")
        try:
            response = dict(adapter.generate(packet))
        except Exception as exc:
            write(self.root / (stage + ".failure.json"), {"error": str(exc), "automatic_retry": False,
                "provider_raw": getattr(adapter._client, "last_raw_response", None)})
            raise Stop("subscription call failed; original reservation retained", status="model_failed", waiting_for="owner") from exc
        provider_raw = getattr(adapter._client, "last_raw_response", None)
        executable = getattr(adapter._client, "executable", None)
        runtime_identity = getattr(adapter._client, "cli_identity", None)
        if executable:
            import shutil
            actual = Path(shutil.which(executable) or executable).resolve(strict=True)
            runtime_identity = {"executable": str(actual), "file_sha256": contract.file_sha256(actual),
                "model_id": adapter.model_id, "backend": adapter.backend, "reasoning_effort": adapter._client.reasoning_effort,
                "cli_identity": runtime_identity}
        raw = write(raw_path, {**request, "response": response, "response_sha256": sha256_json(response),
            "provider_raw": provider_raw, "provider_raw_sha256": sha256_json(provider_raw), "runtime_identity": runtime_identity,
            "completed_at": datetime.now(timezone.utc).isoformat()})
        bound_model_response(adapter, packet, response, raw_path)
        return raw

    def review_input(self, frozen, response, proposal, stage):
        from backend.pipeline.source_review_input_repair import add_receipt, check_packet
        base = initial_review_base(frozen, proposal) if stage == 'initial_review' else contract.review_packet(frozen, proposal,
            complete_anchor_review=bool(frozen['manifest'].get('anchor_qualification') and stage=='final_review'
                and any(d.action=='unresolved' for d in proposal.dispositions)))
        if stage=='final_review' and frozen['manifest'].get('anchor_qualification'):
            selected,receipt=corrected_proposal(self.root,frozen,response)
            if selected!=proposal:raise ValueError('anchor final component selection drift')
            if receipt:base['program_anchor_component_selection']=receipt
        saved = self.root/(stage+'.packet.json')
        if saved.exists():
            packet = body(read(saved))
            base = disclosed_review_base(frozen, base, saved=packet)
            check_packet(frozen, response, packet, base)
            return packet
        # Saved review packets retain their exact old contract. New reviews
        # receive the same verified read-only adapter proof as the proposer.
        base = disclosed_review_base(frozen, base)
        return add_receipt(frozen, response, base) if frozen['manifest'].get('claim_issue') or frozen['manifest'].get('anchor_qualification') or frozen['manifest'].get('evidence_binding_repair') else base

    def reviewed(self, frozen: dict) -> tuple[contract.Proposal, dict]:
        from backend.pipeline.source_permission_recovery import NAME as PERMISSION_RECOVERY_NAME, reviewed as permission_reviewed
        if self.root is not None and (self.root/PERMISSION_RECOVERY_NAME).exists():
            return permission_reviewed(self,frozen)
        if contract.unresolved_consumers(frozen):
            raise Stop("unknown affected consumer requires a proven schema adapter; no model calls", status="proof_failure", waiting_for="system")
        packet = build_proposal_packet(frozen)
        self.guard(frozen)
        from backend.pipeline.source_schema_recovery import select as proposal_stage
        raw = self.call(proposal_stage(self.root,packet,persist=True), packet)
        proposed = effective_proposal(frozen, raw["response"])
        review_packet = self.review_input(frozen, raw['response'], proposed, 'initial_review')
        self.guard(frozen)
        initial = self.call("initial_review", review_packet)
        if contract.validate_review(review_packet, initial["response"]):
            return proposed, initial
        if frozen.get('claim_issue_context',{}).get('complete_input_repair'):
            from backend.pipeline.source_normalization_recovery import NAME, STAGE, packet as recovery_packet
            if self.root is not None and (self.root/NAME).exists():
                proposed, repaired = recovery_packet(self.root,frozen)
                self.guard(frozen)
                reviewed = self.call(STAGE,repaired)
                if not contract.validate_review(repaired,reviewed['response']):
                    raise Stop('normalization engineering recovery review rejected; no further recovery/correction')
                return proposed,reviewed
            raise Stop('complete-input repair independent review rejected; no extra correction',
                       status='root_cause_required',waiting_for='system')
        # Exactly one correction, never an extra GPT preapproval/review stage.
        correction_packet = {**packet, "initial_proposal": proposed.model_dump(), "Opus_review": initial["response"],
                             "correction_round": 1, "another_correction_allowed": False}
        if 'unresolved_proposal_review' in review_packet:
            correction_packet['unresolved_proposal_review'] = review_packet['unresolved_proposal_review']
        self.guard(frozen)
        correction = self.call("correction", correction_packet)
        proposed,_ = corrected_proposal(self.root,frozen,correction["response"])
        # Diagnose the original correction without dropping auxiliary rows or
        # turning source/relationship pending into approved business consumers.
        # This record is not a review packet and grants no model/write budget.
        from backend.pipeline.claim_evidence_completion import direction_for
        if direction_for(frozen.get('claim_issue_context', {})) or frozen.get('owner_claim_completion'):
            from backend.pipeline.source_consumer_scope import receipt
            write(self.root/'consumer-scope-diagnostic.json', receipt(frozen, proposed))
        if getattr(self, 'claim_first', False):
            from backend.pipeline.claim_first_management import final_packet
            proposed, review_packet = final_packet(self.root, frozen, proposed, correction)
            self.guard(frozen)
            final = self.call('final_review', review_packet)
            if not contract.validate_review(review_packet, final['response']):
                raise Stop('Claim-first final source postimage review rejected; no extra correction')
            return proposed, final
        from backend.pipeline.cvp_membership_management import retirement_handoff
        handoff = retirement_handoff(frozen, proposed, correction)
        if handoff is not None:
            write(self.root / 'cvp-membership-handoff.json', handoff)
            if not hasattr(self, 'store'):
                raise Stop('routine CVP membership revalidation pending; original correction retained',
                           status='cvp_management_pending', waiting_for='system')
            from backend.pipeline.cvp_membership_management import execute, joint_review_packet
            receipt = execute(self, frozen, handoff)
            write(self.root/'membership-resolution.json',receipt)
            proposed,review_packet = joint_review_packet(self.root,frozen,proposed,correction)
        else:
            review_packet = self.review_input(frozen, correction['response'], proposed, 'final_review')
        self.guard(frozen)
        final = self.call("final_review", review_packet)
        if not contract.validate_review(review_packet, final["response"]):
            write(self.root / "review-root-cause-handoff.json", {"reason": "terminal Opus disagreement",
                "review": final, "proposal": proposed.model_dump(), "correction_budget_exhausted": True,
                "waiting_for": "system", "human_decision_required": False,
                "automatic_retry": False, "content_write_authority": False,
                "required_action": "Check actual source/input/normalization/flow causes before any content handoff; preserve all original budgets.",
                "source_update_id": frozen["update_id"]})
            from backend.pipeline.source_review_input_repair import NAME, STAGE, repaired_packet
            if (self.root/NAME).exists():
                repaired = repaired_packet(self.root, frozen, proposed, review_packet, final, correction)
                self.guard(frozen)
                result = self.call(STAGE, repaired)
                if not contract.validate_review(repaired, result['response']):
                    raise Stop('input-repaired terminal Opus review rejected; no further call/correction')
                return proposed, result
            raise Stop("terminal Opus review rejected/unresolved; no second correction")
        return proposed, final

    def run(self, manifest: contract.Manifest, *, apply: bool = False, audit: bool = False) -> dict:
        self.prepare(manifest)
        with exclusive(self.root):
            try:
                frozen = body(read(self.root / "frozen.json"))
                before = contract.index(frozen["before_records"])
                contract.guard_files(frozen)
                if (self.root / "complete.json").exists():
                    result = read(self.root / "complete.json")
                    self.verify_completion(result, frozen)
                    return result
                if (self.root / "effective-package.json").exists():
                    package = body(read(self.root / "effective-package.json"))
                    validate_package(package)
                else:
                    proposed, raw = self.reviewed(frozen)
                    package = contract.compile_package(frozen, proposed, raw)
                    package["source_repair_application"] = {"schema_version": contract.VERSION,
                        "run_root": str(self.root), "terminal_stage": raw["stage"],
                        "frozen_sha256": read(self.root / "frozen.json")["artifact_sha256"],
                        "review_sha256": raw["artifact_sha256"], "proposal_sha256": sha256_json(proposed.model_dump())}
                    write(self.root / "effective-package.json", package)
                intent = self.root / "apply-intent.json"
                if (self.root / "plan.json").exists():
                    plan = body(read(self.root / "plan.json"))
                else:
                    self.guard(frozen)
                    plan = self.ports.plan(package)
                    contract.assert_plan_postimages(package, plan, before)
                    write(self.root / "plan.json", plan)
                post = contract.assert_plan_postimages(package, plan, before)
                committed = self.ports.committed(plan)
                if committed and not intent.exists():
                    raise Stop("database commit exists without this task's apply intent", status="proof_failure", waiting_for="system")
                if not committed:
                    if intent.exists():
                        raise Stop("apply intent exists without proven commit; no blind replay", status="apply_uncertain", waiting_for="system")
                    self.guard(frozen)
                    if not apply:
                        return self.state("reviewed_not_applied", waiting_for="operator", next_action="execute with --apply within existing bounded authorization; no new content approval required")
                    backup = body(read(self.root / "backup.json")) if (self.root / "backup.json").exists() else self.ports.backup(self.root)
                    contract.verify_file(backup["dump"])
                    if backup.get("restore_catalog_nonempty") is not True:
                        raise Stop("backup is not restore-list verified", status="proof_failure", waiting_for="system")
                    write(self.root / "backup.json", backup)
                    self.guard(frozen)
                    if self.ports.plan(package) != plan:
                        raise Stop("dry-run changed before commit", status="proof_failure", waiting_for="system")
                    write(intent, {"plan_sha256": sha256_json(plan), "package_sha256": sha256_json(package),
                                   "backup_sha256": read(self.root / "backup.json")["artifact_sha256"], "single_apply_required": True})
                    self.state("applying", next_action="query actual ChangeSet before any recovery")
                    receipt = self.ports.apply(package, plan, frozen)
                    if receipt.get("status") != "applied":
                        raise Stop("write did not prove a committed ChangeSet", status="apply_uncertain", waiting_for="system")
                    write(self.root / "apply-receipt.json", receipt)
                    if not self.ports.committed(plan):
                        raise Stop("receipt not confirmed by database", status="proof_failure", waiting_for="system")
                # Even after a crash between SQL commit and receipt, never apply again.
                current = self.ports.readback(plan, post, before)
                contract.guard_files(frozen)
                write(self.root / "readback.json", {"change_set_id": plan["change_set_id"],
                    "post_records": contract.rows(current), "post_fingerprint_sha256": contract.fingerprint(current),
                    "source_files_verified": True})
                if not audit:
                    return self.state("committed_waiting_audit", next_action="perform complete affected independent audit")
                jobs = audit_jobs(frozen, current)
                outcomes = []
                for job in jobs:
                    job_root = self.root / "audits" / sha256_json(job)
                    job_root.mkdir(parents=True, exist_ok=True)
                    if (job_root / "result.json").exists():
                        outcome = body(read(job_root / "result.json"))
                    else:
                        # Runtime may consume an already saved complete report, but
                        # this reservation never permits an unknown model replay.
                        fresh = not (job_root / "intent.json").exists()
                        write(job_root / "intent.json", job)
                        self.state("committed_waiting_audit", next_action="await independent audit", audit_job=job)
                        outcome = self.ports.audit(job_root, job["claim_ids"], job["viewpoint_ids"]) if fresh else self.ports.audit_saved(job_root, job["claim_ids"], job["viewpoint_ids"])
                        self.ports.validate_audit(outcome, job["claim_ids"], job["viewpoint_ids"])
                        write(job_root / "result.json", outcome)
                    self.ports.validate_audit(outcome, job["claim_ids"], job["viewpoint_ids"])
                    outcomes.append({"job": job, "result": outcome,
                                     "binding": binding(job_root / "result.json")})
                contract.guard_files(frozen)
                self.ports.readback(plan, post, before)
                result = write(self.root / "complete.json", {"status": "audited_complete",
                    "update_id": frozen["update_id"], "change_set_id": plan["change_set_id"],
                    "source_epoch": [b.model_dump() for b in manifest.current_sources],
                    "audits": outcomes, "affected_group_keys": [j["group_key"] for j in jobs if j["group_key"]],
                    "bindings": [binding(self.root / n) for n in ("frozen.json", "effective-package.json", "plan.json", "apply-intent.json", "backup.json", "readback.json")],
                    "campaign_membership_changed": False, "production_resumed": False,
                    "revisit_required": any(j["revisit_required"] or o["result"].get("revisit_required", False) for j, o in zip(jobs, outcomes)),
                    "independent_audit_complete": all(j["independent_audit_complete"] and o["result"].get("independent_audit_complete", False) for j, o in zip(jobs, outcomes))})
                self.state("audited_complete", waiting_for="none", next_action="completion available; campaign restart is a separate explicit action")
                return result
            except Stop as exc:
                self.state(exc.status, waiting_for=exc.waiting_for, next_action=str(exc))
                raise
            except Exception as exc:
                self.state("proof_failure", waiting_for="system", next_action=str(exc))
                raise

    def verify_completion(self, result: dict, frozen: dict) -> None:
        if result["update_id"] != frozen["update_id"] or result["status"] != "audited_complete":
            raise Stop("completion belongs to another epoch", status="proof_failure", waiting_for="system")
        for b in result["bindings"]:
            contract.verify_file(b)
        plan = body(read(self.root / "plan.json"))
        if not self.ports.committed(plan):
            raise Stop("completed ChangeSet not actually committed", status="proof_failure", waiting_for="system")
        package = body(read(self.root / "effective-package.json"))
        validate_package(package)
        before = contract.index(frozen["before_records"])
        post = contract.assert_plan_postimages(package, plan, before)
        validate_completion_population(result, frozen, post)
        if result.get("authorized_link_corrections"):
            from backend.pipeline.owner_approved_link_correction import source_projection
            from backend.pipeline.source_reconciliation_runtime import expected_invalidations
            projected = source_projection(result,self.ports.snapshot(),self.ports.store)
            if projected != expected_invalidations(before,post,plan):
                raise ValueError("composite source/approved child full Registry boundary drift")
        else:
            self.ports.readback(plan, post, before)
        for outcome in result["audits"]:
            contract.verify_file(outcome["binding"])
            job = outcome["job"]
            self.ports.validate_audit(outcome["result"], job["claim_ids"], job["viewpoint_ids"])


def binding(path: Path) -> dict:
    from backend.pipeline.viewpoint_backup_archive import logical_binding_path
    return {"path": str(logical_binding_path(path)), "file_sha256": contract.file_sha256(path)}


def build_proposal_packet(frozen: dict) -> dict:
    selected = frozen["inventory"]["affected_keys"] + frozen["inventory"]["context_keys"]
    states = contract.index(frozen["before_records"])
    result = {"schema_version": contract.VERSION, "update_id": frozen["update_id"],
        "manifest": frozen["manifest"], "before_source": frozen["before"], "current_sources": frozen["after"],
        "context_sources": frozen.get("context_sources", []), "sentence_inventory": frozen["sentence_inventory"],
        "inventory": frozen["inventory"], "records": contract.rows({k: states[k] for k in selected}),
        **({"deferred_diagnosis": frozen["deferred_diagnosis"],
             "writable_record_shapes": {col: contract.KNOWLEDGE_COLLECTIONS[col][0].model_json_schema()
                 for col in sorted({p["collection"] for p in frozen["manifest"]["permissions"]})}}
            if "deferred_diagnosis" in frozen else {})}
    if "claim_issue_context" in frozen:
        result["claim_issue_context"] = frozen["claim_issue_context"]
        result["writable_record_shapes"] = {col: contract.KNOWLEDGE_COLLECTIONS[col][0].model_json_schema()
            for col in sorted({p["collection"] for p in frozen["manifest"]["permissions"]})}
        if frozen['claim_issue_context'].get('complete_input_repair'):
            from backend.pipeline.claim_complete_input_repair import input_receipt
            result['complete_input_repair_receipt'] = input_receipt(frozen)
        from backend.pipeline.claim_evidence_completion import packet_receipt
        completion=packet_receipt(frozen)
        if completion:result['evidence_completion_receipt']=completion
    if frozen['manifest'].get('anchor_qualification'):
        import json
        result['anchor_qualification']=json.loads(contract.bound_bytes(frozen['manifest']['anchor_qualification']))
        result['writable_record_shapes']={col:contract.KNOWLEDGE_COLLECTIONS[col][0].model_json_schema()
            for col in sorted({p['collection'] for p in frozen['manifest']['permissions']})}
    if frozen['manifest'].get('evidence_binding_repair'):
        import json
        result['evidence_binding_repair']=json.loads(contract.bound_bytes(frozen['manifest']['evidence_binding_repair']))
        result['writable_record_shapes']={col:contract.KNOWLEDGE_COLLECTIONS[col][0].model_json_schema()
            for col in sorted({p['collection'] for p in frozen['manifest']['permissions']})}
    if any(contract.split_key(k)[0]=='knowledge_routes' for k in frozen['inventory']['unknown_keys']):
        result['inventory'] = {**frozen['inventory'], 'unknown_keys': contract.unresolved_consumers(frozen)}
        result['read_only_adapter_receipt'] = {'schema': 'knowledge_route_impact_only_v1',
            'keys': [k for k in frozen['inventory']['unknown_keys'] if contract.split_key(k)[0]=='knowledge_routes'],
            'disposition': 'impact_only', 'content_write_authority': False}
    if 'viewpoint_scope_context' in frozen:
        result['viewpoint_scope_context'] = frozen['viewpoint_scope_context']
        result['writable_record_shapes'] = {col: contract.KNOWLEDGE_COLLECTIONS[col][0].model_json_schema()
            for col in sorted({p['collection'] for p in frozen['manifest']['permissions']})}
    return result


def disclosed_review_base(frozen, base, *, saved=None):
    if saved is not None and 'read_only_adapter_receipt' not in saved:
        return base  # Never upgrade a historical reviewer input.
    disclosed = build_proposal_packet(frozen)
    if 'read_only_adapter_receipt' in disclosed:
        base = {**base, 'inventory': disclosed['inventory'],
                'read_only_adapter_receipt': disclosed['read_only_adapter_receipt']}
    if 'complete_input_repair_receipt' in disclosed:
        base = {**base, 'complete_input_repair_receipt': disclosed['complete_input_repair_receipt']}
    return base


def validate_completion_population(result: dict, frozen: dict, post: dict) -> None:
    jobs = audit_jobs(frozen, post)
    outcomes = result["audits"]
    if ([o["job"] for o in outcomes] != jobs
            or result["affected_group_keys"] != [j["group_key"] for j in jobs if j["group_key"]]
            or result["source_epoch"] != frozen["manifest"]["current_sources"]
            or result["production_resumed"] is not False or result["campaign_membership_changed"] is not False):
        raise ValueError("completion affected audit denominator/source/membership drift")
    required = {"frozen.json", "effective-package.json", "plan.json", "apply-intent.json", "backup.json", "readback.json"}
    if {Path(b["path"]).name for b in result["bindings"]} != required or len(result["bindings"]) != len(required):
        raise ValueError("completion proof denominator drift")
    for outcome in outcomes:
        if body(read(contract.verify_file(outcome["binding"]))) != outcome["result"]:
            raise ValueError("completion audit result differs from its exact artifact")
    revisit = any(j["revisit_required"] or o["result"].get("revisit_required", False) for j, o in zip(jobs, outcomes))
    independent = all(j["independent_audit_complete"] and o["result"].get("independent_audit_complete", False) for j, o in zip(jobs, outcomes))
    if result["revisit_required"] != revisit or result["independent_audit_complete"] != independent:
        raise ValueError("completion cannot upgrade temporary audit exceptions")


def audit_jobs(frozen: dict, current: dict[str, dict]) -> list[dict]:
    changed = {split[1] for k in frozen["inventory"]["affected_keys"]
               if (split := contract.split_key(k))[0] == "claims"}
    jobs, covered = [], set()
    affected_viewpoints = {contract.split_key(k)[1] for k in frozen["inventory"]["affected_keys"] if k.startswith("canonical_viewpoints/")}
    for group in contract.Manifest.model_validate(frozen["manifest"]).groups:
        if not changed.intersection(group.claim_ids) and not affected_viewpoints.intersection(group.viewpoint_ids):
            continue
        viewpoints = set(group.viewpoint_ids) | {p["viewpoint_id"] for k, p in current.items()
            if k.startswith("viewpoint_claim_links/") and p["claim_id"] in group.claim_ids and p.get("effective_state", "active") == "active"}
        jobs.append({"group_key": group.group_key, "claim_ids": sorted(group.claim_ids),
                     "viewpoint_ids": sorted(viewpoints), "revisit_required": group.revisit_required,
                     "independent_audit_complete": group.independent_audit_complete})
        covered.update(group.claim_ids)
    if changed - covered:
        remaining = sorted(changed - covered)
        viewpoints = sorted({p["viewpoint_id"] for k, p in current.items()
                             if k.startswith("viewpoint_claim_links/") and p["claim_id"] in remaining})
        jobs.append({"group_key": None, "claim_ids": remaining, "viewpoint_ids": viewpoints,
                     "revisit_required": False, "independent_audit_complete": True})
    if not jobs:
        raise Stop("source update has no verifiable affected Claim audit population")
    return jobs


@validated_history(bound_files_only=True)
def validate_package(package: dict, *, historical: bool = False) -> None:
    """Store-side reconstruction; supplied approval booleans are not authority."""
    from backend.pipeline.viewpoint_validation_context import historical_context
    ctx = historical_context()
    if ctx:
        # A cached reconstruction must also depend on its compiler/model code,
        # not only the saved rows and this dispatcher's source file.
        project = Path(__file__).resolve().parents[2]
        for relative in (
            'backend/pipeline/source_reconciliation.py',
            'backend/pipeline/source_reconciliation_runtime.py',
            'backend/pipeline/deferred_claim_repair.py',
            'backend/pipeline/claim_citation_purpose_review.py',
            'backend/pipeline/source_normalization_recovery.py',
            'backend/pipeline/claim_issue_management.py',
            'backend/pipeline/source_projection.py',
            'backend/api/canonical_repository/knowledge_models.py',
            'backend/api/canonical_repository/postgres_store.py',
        ):
            ctx.pin(project / relative)
    # Startup reads the saved, sealed artifact; the store receives its body.
    # Authenticate the envelope before comparing exactly the same package body.
    if "artifact_sha256" in package:
        payload = body(package)
        if package["artifact_sha256"] != sha256_json(payload):
            raise ValueError("source reconciliation package artifact seal drift")
        package = payload
    app = package.get("source_repair_application", {})
    if app.get("schema_version") != contract.VERSION:
        raise ValueError("unknown source reconciliation contract")
    root = Path(app.get("run_root", ""))
    if not root.is_absolute():
        raise ValueError("reconciliation proof root must be absolute")
    frozen_artifact = read(root / "frozen.json")
    frozen = body(frozen_artifact)
    if contract.freeze(contract.Manifest.model_validate(frozen["manifest"]), contract.index(frozen["before_records"]),
                       physical_snapshots=frozen["physical_snapshots"]) != frozen:
        raise ValueError("frozen source/inventory/authorization reconstruction drift")
    if root.name != task_id(contract.Manifest.model_validate(frozen["manifest"]), frozen["physical_snapshots"]) or app.get("frozen_sha256") != frozen_artifact["artifact_sha256"]:
        raise ValueError("reconciliation task/input identity drift")
    contract.guard_files(frozen, historical=historical)
    validate_reviewed_records(package, frozen)


def validate_reviewed_records(package: dict, frozen: dict, *, frozen_sha256: str | None = None, provenance_templates: dict | None = None) -> None:
    """Exact saved proposal/review/package checks on their declared readset.

    Not a store authorization entry: validate_package above still reconstructs
    the full freeze and authorization before calling this shared portion.
    Completed epoch consumers can use the SAME checks without rebuilding an
    unrelated whole-library predecessor snapshot.
    """
    app = package['source_repair_application']; root = Path(app['run_root'])
    stage = app.get("terminal_stage")
    if stage not in {"initial_review", "final_review", "final_review_input_repair", "normalization_recovery_review"}:
        raise ValueError("non-terminal stage cannot approve")
    packet = body(read(root / (stage + ".packet.json")))
    raw = read(root / (stage + ".raw.json"))
    expected = validate_raw(root, stage, packet)
    permission_recovery=bool(packet.get('permission_input_recovery'))
    engineering = stage == 'normalization_recovery_review'
    if engineering:
        from backend.pipeline.source_normalization_recovery import packet as recovery_packet
        proposal, exact_packet = recovery_packet(root,frozen)
        if packet != exact_packet:
            raise ValueError('normalization recovery complete input drift')
    elif frozen['manifest'].get('viewpoint_scope_repair'):
        from backend.pipeline.viewpoint_validation_context import historical_context
        ctx=historical_context()
        if ctx:ctx.pin(Path(__file__).with_name('cvp_scope_management.py'))
        from backend.pipeline.cvp_scope_management import Proposal
        proposal=Proposal.model_validate(packet['proposal'])
    else:
        proposal = effective_proposal(frozen, packet["proposal"]) if frozen["manifest"].get("claim_issue") else contract.Proposal.model_validate(packet["proposal"])
    claim_first = bool(packet.get('claim_first_management'))
    base = contract.review_packet(frozen, proposal, claim_first=claim_first,
        complete_anchor_review=bool(packet.get('anchor_create_permission_receipt')))
    if packet.get('program_anchor_component_selection'):
        correction=validate_raw(root,'correction',body(read(root/'correction.packet.json')))
        selected,receipt=corrected_proposal(root,frozen,correction['response'])
        if selected!=proposal or receipt is None:raise ValueError('anchor terminal selected component drift')
        base['program_anchor_component_selection']=receipt
    if not contract.validate_review(packet, raw["response"]):
        raise ValueError("terminal complete postimage review invalid")
    # Authenticate proposal and correction chain. A final reply cannot bypass initial review.
    proposal_packet = body(read(root / "proposal.packet.json"))
    if proposal_packet != build_proposal_packet(frozen):
        raise ValueError("proposal omitted/changed frozen source or consumer context")
    from backend.pipeline.source_schema_recovery import select as proposal_stage
    first = validate_raw(root, proposal_stage(root,proposal_packet), proposal_packet)
    if stage == "initial_review":
        if effective_proposal(frozen, first["response"]) != proposal:
            raise ValueError("initial review is not of the actual proposal")
    elif permission_recovery:
        from backend.pipeline.source_permission_recovery import packet as recovery_packet,STAGE as RECOVERY_STAGE
        recovery_input=recovery_packet(root,frozen)
        recovered=validate_raw(root,RECOVERY_STAGE,body(read(root/(RECOVERY_STAGE+'.packet.json'))))
        if body(read(root/(RECOVERY_STAGE+'.packet.json')))!=recovery_input:
            raise ValueError('permission recovery complete input drift')
        if effective_proposal(frozen,recovered['response'])!=proposal:
            raise ValueError('permission recovery final is not actual new proposal')
        from backend.pipeline.source_permission_recovery import final_packet
        final=final_packet(root,frozen,proposal,recovered,recovery_input)
        if packet!=final:raise ValueError('permission recovery original final complete input drift')
    elif not engineering:
        initial_packet = body(read(root / "initial_review.packet.json"))
        initial = validate_raw(root, "initial_review", initial_packet)
        first_proposal = effective_proposal(frozen, first["response"]).model_dump()
        from backend.pipeline.source_review_input_repair import check_packet
        initial_base = initial_review_base(frozen, effective_proposal(frozen, first['response']))
        check_packet(frozen, first['response'], initial_packet,
                     disclosed_review_base(frozen, initial_base, saved=initial_packet))
        if contract.validate_review(initial_packet, initial["response"]):
            raise ValueError("correction must follow one genuine initial disagreement")
        correction_packet = body(read(root / "correction.packet.json"))
        expected_correction = {**proposal_packet, "initial_proposal": first_proposal, "Opus_review": initial["response"], "correction_round": 1, "another_correction_allowed": False}
        if 'unresolved_proposal_review' in initial_packet:
            expected_correction['unresolved_proposal_review'] = initial_packet['unresolved_proposal_review']
        if correction_packet != expected_correction:
            raise ValueError("correction chain drift")
        correction = validate_raw(root, "correction", correction_packet)
        corrected,_ = corrected_proposal(root,frozen,correction['response'])
        if claim_first:
            if stage != 'final_review':
                raise ValueError('Claim-first cannot mint an additional terminal review')
            from backend.pipeline.claim_first_management import final_packet
            projected, expected_packet = final_packet(root, frozen, corrected, correction)
            if proposal != projected or packet != expected_packet:
                raise ValueError('Claim-first does not bind exact original correction and pending CVP postimages')
        elif packet.get('membership_resolution'):
            if stage != 'final_review':
                raise ValueError('membership cannot mint an extra terminal review budget')
            from backend.pipeline.cvp_membership_management import joint_review_packet
            resolved, joint = joint_review_packet(root,frozen,corrected,correction)
            if proposal != resolved or packet != joint:
                raise ValueError('terminal membership resolution does not bind original correction and full joint postimage')
        elif corrected != proposal:
            raise ValueError("terminal review is not of actual correction")
    from backend.pipeline.source_review_input_repair import check_packet, repaired_packet
    if stage == 'final_review_input_repair':
        old_packet = body(read(root/'final_review.packet.json'))
        old_raw = validate_raw(root, 'final_review', old_packet)
        check_packet(frozen, correction['response'], old_packet,
                     disclosed_review_base(frozen, base, saved=old_packet))
        if contract.validate_review(old_packet, old_raw['response']) or packet != repaired_packet(root, frozen, proposal, old_packet, old_raw, correction):
            raise ValueError('input repair must preserve the exact rejected terminal review')
    elif not engineering and not permission_recovery and not packet.get('membership_resolution') and not claim_first:
        check_packet(frozen, first['response'] if stage=='initial_review' else correction['response'], packet,
                     disclosed_review_base(frozen, base, saved=packet))
    if expected["artifact_sha256"] != app.get("review_sha256") or sha256_json(proposal.model_dump()) != app.get("proposal_sha256"):
        raise ValueError("final review/proposal provenance drift")
    from backend.pipeline.claim_owner_revision import validate_joint
    validate_joint(frozen,proposal,package)
    rebuilt = contract.compile_package(frozen, proposal, raw, frozen_sha256=frozen_sha256, claim_first=claim_first,
                                        provenance_templates=provenance_templates)
    if packet.get('membership_resolution'):
        from backend.pipeline.cvp_membership_management import append_joint_package
        rebuilt = append_joint_package(rebuilt,root,frozen,corrected,correction)
    if {k: v for k, v in package.items() if k != "source_repair_application"} != rebuilt:
        raise ValueError("package differs from exactly reviewed records/provenance")


def validate_raw(root: Path, stage: str, packet: dict) -> dict:
    from backend.pipeline.source_reconciliation_runtime import model_adapter
    from backend.pipeline.claim_issue_management import eligibility_profile
    from backend.pipeline.viewpoint_validation_context import historical_context
    ctx = historical_context()
    if ctx:
        from backend.pipeline.source_reconciliation_runtime import PROMPTS
        from backend.pipeline.viewpoint_semantic_contract import CONTRACT_PATH
        ctx.pin(CONTRACT_PATH)
        ctx.pin(PROMPTS / ('source_reconciliation_review.md'
            if stage in {'initial_review', 'final_review', 'final_review_input_repair', 'normalization_recovery_review'} else 'source_reconciliation_proposal.md'))
        if stage == 'normalization_recovery_review':ctx.pin(Path(__file__).with_name('source_normalization_recovery.py'))
        if packet.get('manifest', {}).get('deferred_repair'):
            ctx.pin(PROMPTS / 'deferred_claim_evidence_repair_v1.md')
        if packet.get('manifest', {}).get('claim_issue'):
            ctx.pin(PROMPTS / 'claim_issue_management_v1.md')
            if eligibility_profile(packet):
                ctx.pin(PROMPTS / 'claim_evidence_eligibility_review_v2.md')
        if packet.get('manifest', {}).get('viewpoint_scope_repair'):
            ctx.pin(PROMPTS / 'cvp_scope_management_v1.md')
        if packet.get('manifest', {}).get('evidence_binding_repair'):
            ctx.pin(Path(__file__).with_name('claim_evidence_binding.py'))
        if packet.get('permission_input_recovery'):
            ctx.pin(Path(__file__).with_name('source_permission_recovery.py'))
    adapter = model_adapter('proposal' if stage in {'proposal_schema_recovery','permission_input_recovery_proposal'} else 'initial_review' if stage=='normalization_recovery_review' else 'final_review' if stage=='final_review_input_repair' else stage, scope=bool(packet.get('manifest', {}).get('viewpoint_scope_repair')),
                            deferred=bool(packet.get("manifest", {}).get("deferred_repair")),
                            issue=bool(packet.get("manifest", {}).get("claim_issue")),
                            evidence_eligibility=eligibility_profile(packet))
    from backend.pipeline.claim_first_management import scoped_adapter
    from backend.pipeline.claim_evidence_binding import scoped_adapter as evidence_adapter
    adapter=evidence_adapter(adapter,packet,'proposal' if stage in {'proposal_schema_recovery','permission_input_recovery_proposal'} else stage)
    from backend.pipeline.source_permission_recovery import adapter as permission_adapter
    adapter=permission_adapter(adapter,packet)
    adapter = scoped_adapter(adapter, packet)
    from backend.pipeline.cvp_pending_link_management import scoped_adapter as link_scoped_adapter
    adapter = link_scoped_adapter(adapter,packet)
    from backend.pipeline.claim_citation_purpose_review import scoped_adapter as citation_scoped_adapter
    adapter = citation_scoped_adapter(adapter,packet)
    from backend.pipeline.source_normalization_recovery import scoped_adapter as recovery_scoped_adapter
    adapter = recovery_scoped_adapter(adapter,packet)
    from backend.pipeline.claim_evidence_completion import scoped_adapter as completion_adapter
    adapter = completion_adapter(adapter,packet)
    from backend.pipeline.claim_source_write_slice import scoped_adapter as slice_adapter
    adapter = slice_adapter(adapter,packet)
    from backend.pipeline.source_schema_recovery import adapter as schema_adapter,select as schema_stage
    if stage=='proposal_schema_recovery' and schema_stage(root,packet)!=stage:
        raise ValueError('schema recovery original rejection proof differs')
    adapter=schema_adapter(adapter,packet,stage)
    raw = read(root / (stage + ".raw.json"))
    from backend.pipeline.viewpoint_resolution_runtime import response_adapter, bound_model_response
    adapter = stage_adapter(adapter, packet, root / (stage + '.raw.json'), saved=raw)
    reservation = body(read(root / (stage + ".reservation.json")))
    proof = adapter.request_encoding_proof(packet)
    expected = {"model_id": adapter.model_id, "backend": adapter.backend,
        "prompt_sha256": adapter.prompt_sha256, "generation_config_sha256": adapter.generation_config_sha256,
        "request_payload_sha256": sha256_json(packet), "request_encoding_proof": proof,
        "request_bytes": adapter.request_bytes(packet), "stage": stage, "automatic_retry": False}
    limit,budget=request_limit(adapter,root/(stage+'.raw.json'),saved=raw)
    if budget is not None:expected['request_budget']=budget
    if expected["request_bytes"] > limit or expected != reservation or any(raw.get(k) != v for k, v in expected.items()) or raw["response_sha256"] != sha256_json(raw["response"]):
        raise ValueError("actual model/backend/prompt/schema/wire/raw binding drift")
    if raw.get("provider_raw_sha256") != sha256_json(raw.get("provider_raw")):
        raise ValueError("original subscription response drift")
    if datetime.fromisoformat(raw["completed_at"]).utcoffset() is None:
        raise ValueError("actual review completion time requires timezone")
    identity = raw.get("runtime_identity")
    if identity is not None and (identity["model_id"] != adapter.model_id or identity["backend"] != adapter.backend
            or identity["reasoning_effort"] != "high" or not identity["file_sha256"]):
        raise ValueError("actual executable/model/effort identity drift")
    bound_model_response(adapter, packet, raw['response'], root / (stage + '.raw.json'), persist=False)
    return raw


def main() -> None:
    from backend.pipeline.cvp_retired_runtime import reject_legacy_execution
    reject_legacy_execution()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()
    from dotenv import load_dotenv
    from backend.pipeline.source_reconciliation_runtime import ProductionPorts, workspace, maintenance_lock
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    manifest = contract.Manifest.model_validate_json(args.manifest.read_text(encoding="utf-8"))
    runner = Runner(workspace(), ProductionPorts())
    with maintenance_lock():
        result = runner.prepare(manifest) if args.prepare_only else runner.run(manifest, apply=args.apply, audit=args.audit)
    print(canonical_json({"task_root": str(runner.root), "status": result.get("status", "prepared"), "production_resumed": False}))


if __name__ == "__main__":
    main()
