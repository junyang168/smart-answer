"""Build a reproducible, source-anchored detailed knowledge package for one sermon."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.pipeline.corpus_survey_runner import (
    PROJECT_ROOT,
    _load,
    _slug,
    _transcript_for_prompt,
)
from backend.pipeline.detailed_knowledge_extraction import (
    DETAILED_RESPONSE_SCHEMA,
    DetailedExtractionValidationError,
    extraction_identity,
    validate_response,
)
from backend.pipeline.stage1 import Stage1OpenAIClient


DEFAULT_TRANSCRIPT_DIR = Path("/opt/homebrew/var/www/church/web/data/script_published")
DEFAULT_OUTPUT_DIR = Path("output/claim-layer/detailed-extractions")
PROMPT_PATH = Path("backend/pipeline/prompts/detailed_knowledge_extraction.md")
VALIDATION_ATTEMPTS = 4


def _archive(path: Path) -> None:
    if not path.is_file():
        return
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
        fingerprint = str((old.get("extraction") or {}).get("fingerprint_sha256") or "legacy")[:12]
    except (OSError, json.JSONDecodeError):
        fingerprint = "unreadable"
    archive = path.parent / "generations" / f"{path.stem}.{fingerprint}.json"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        shutil.copy2(path, archive)


def _validation_feedback(
    error: DetailedExtractionValidationError,
    transcript: dict[str, Any],
) -> str:
    message = str(error)
    segment_rows: list[str] = []
    for locator in dict.fromkeys(re.findall(r"\bS\d{4}\b", message)):
        ordinal = int(locator[1:]) - 1
        script = transcript.get("script") or []
        if 0 <= ordinal < len(script):
            segment_rows.append(f"[{locator}]\n{str(script[ordinal].get('text') or '')}")
    detail = (
        "\n涉及段落的完整原文如下：\n" + "\n\n".join(segment_rows)
        if segment_rows else ""
    )
    return (
        f"上一版未通过机械验证：{message}。{detail}\n"
        "请保留上一版中其余有效对象，只修复所有同类机械错误，再重新输出完整 JSON。"
        "每个 verbatim_excerpt 必须从对应段落连续逐字复制；不能改字、补标点或拼接。"
    )


def _archive_rejected_candidate(
    *, output_dir: Path, transcript_id: str, attempt: int, candidate: dict[str, Any],
    error: DetailedExtractionValidationError,
) -> None:
    target = output_dir / "rejected-generations" / _slug(transcript_id) / f"attempt-{attempt:02d}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = target.with_name(f"attempt-{attempt:02d}-{timestamp}.json")
    target.write_text(
        json.dumps(
            {"validation_error": str(error), "candidate": candidate},
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def _anchored_fragment(
    *,
    fragment_id: str,
    source_id: str,
    anchor: dict[str, Any],
    transcript: dict[str, Any],
    source_sha256: str,
) -> dict[str, Any]:
    ordinal = int(anchor["segment_index"][1:]) - 1
    paragraph = transcript["script"][ordinal]
    paragraph_text = str(paragraph.get("text") or "")
    excerpt = anchor["verbatim_excerpt"]
    return {
        "fragment_id": fragment_id,
        "source_id": source_id,
        "verbatim_excerpt": excerpt,
        "paragraph_key": anchor["segment_index"],
        "source_segment_index": paragraph.get("index"),
        "media_time": paragraph.get("start_time"),
        "media_end_time": paragraph.get("end_time"),
        "source_sha256": source_sha256,
        "paragraph_text_sha256": hashlib.sha256(paragraph_text.encode("utf-8")).hexdigest(),
        "verbatim_excerpt_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
        "anchor_state": "source_version_bound",
        "review_status": "candidate",
    }


def compile_package(
    *, transcript_id: str, transcript_path: Path, transcript: dict[str, Any], raw: bytes,
    response: dict[str, Any], extraction: dict[str, Any],
) -> dict[str, Any]:
    # Model-facing IDs are intentionally short so the JSON remains tractable.
    # Namespace them here before a package can ever be merged with another
    # sermon.  A 200-sermon corpus cannot safely contain 200 different CL001s.
    namespace = f"DK-{hashlib.sha256(transcript_id.encode('utf-8')).hexdigest()[:12]}"
    response = json.loads(json.dumps(response, ensure_ascii=False))
    id_maps = {
        "question": {row["question_id"]: f"{namespace}-{row['question_id']}" for row in response["questions"]},
        "position": {row["position_id"]: f"{namespace}-{row['position_id']}" for row in response["positions"]},
        "observation": {row["observation_id"]: f"{namespace}-{row['observation_id']}" for row in response["observations"]},
        "evidence": {row["evidence_step_id"]: f"{namespace}-{row['evidence_step_id']}" for row in response["evidence_steps"]},
        "claim": {row["claim_id"]: f"{namespace}-{row['claim_id']}" for row in response["claims"]},
        "evidence_relation": {row["relation_id"]: f"{namespace}-{row['relation_id']}" for row in response["evidence_relations"]},
        "claim_relation": {row["claim_relation_id"]: f"{namespace}-{row['claim_relation_id']}" for row in response["claim_relations"]},
    }
    for row in response["questions"]:
        row["question_id"] = id_maps["question"][row["question_id"]]
        row["answer_claim_ids"] = [id_maps["claim"][value] for value in row["answer_claim_ids"]]
    for row in response["positions"]:
        row["position_id"] = id_maps["position"][row["position_id"]]
    for row in response["observations"]:
        row["observation_id"] = id_maps["observation"][row["observation_id"]]
    for row in response["evidence_steps"]:
        row["evidence_step_id"] = id_maps["evidence"][row["evidence_step_id"]]
        row["produced_claim_ids"] = [id_maps["claim"][value] for value in row["produced_claim_ids"]]
    for row in response["claims"]:
        row["claim_id"] = id_maps["claim"][row["claim_id"]]
        row["evidence_step_ids"] = [id_maps["evidence"][value] for value in row["evidence_step_ids"]]
        row["opposed_position_ids"] = [id_maps["position"][value] for value in row["opposed_position_ids"]]
    for row in response["evidence_relations"]:
        row["relation_id"] = id_maps["evidence_relation"][row["relation_id"]]
        row["from_id"] = id_maps["evidence"][row["from_id"]]
        row["to_id"] = id_maps["evidence"][row["to_id"]]
    for row in response["claim_relations"]:
        row["claim_relation_id"] = id_maps["claim_relation"][row["claim_relation_id"]]
        row["from_id"] = id_maps["claim"][row["from_id"]]
        row["to_id"] = id_maps["claim"][row["to_id"]]

    source_id = f"SRC-{_slug(transcript_id)}"
    source_sha256 = hashlib.sha256(raw).hexdigest()
    fragments: list[dict[str, Any]] = []
    fragment_by_anchor: dict[tuple[str, str], str] = {}

    def fragment_for(owner_id: str, anchor: dict[str, Any], position: int) -> str:
        key = (anchor["segment_index"], anchor["verbatim_excerpt"])
        existing = fragment_by_anchor.get(key)
        if existing:
            return existing
        fragment_id = f"FR-{_slug(transcript_id)}-{owner_id}-{position + 1:02d}"
        fragment_by_anchor[key] = fragment_id
        fragments.append(_anchored_fragment(
            fragment_id=fragment_id, source_id=source_id, anchor=anchor,
            transcript=transcript, source_sha256=source_sha256,
        ))
        return fragment_id

    questions = []
    for row in response["questions"]:
        item = dict(row)
        item["source_fragment_ids"] = [fragment_for(row["question_id"], anchor, i) for i, anchor in enumerate(item.pop("anchors"))]
        item["review_status"] = "candidate"
        questions.append(item)
    positions = []
    for row in response["positions"]:
        item = dict(row)
        item["source_fragment_ids"] = [fragment_for(row["position_id"], anchor, i) for i, anchor in enumerate(item.pop("anchors"))]
        item["review_status"] = "candidate"
        positions.append(item)
    observations = []
    for row in response["observations"]:
        item = dict(row)
        item["source_fragment_ids"] = [fragment_for(row["observation_id"], anchor, i) for i, anchor in enumerate(item.pop("anchors"))]
        item["review_status"] = "candidate"
        observations.append(item)
    evidence_steps = []
    evidence_anchor_snapshots: dict[str, list[dict[str, Any]]] = {}
    for row in response["evidence_steps"]:
        item = dict(row)
        anchors = item.pop("anchors")
        evidence_anchor_snapshots[row["evidence_step_id"]] = anchors
        item["source_fragment_ids"] = [fragment_for(row["evidence_step_id"], anchor, i) for i, anchor in enumerate(anchors)]
        item["review_status"] = "candidate"
        evidence_steps.append(item)
    claims = []
    for row in response["claims"]:
        item = dict(row)
        item["title"] = item.pop("statement")
        item["claim_type"] = item.pop("claim_kind")
        item["extraction_fingerprints"] = [extraction["fingerprint_sha256"]]
        anchors = []
        for evidence_id in item["evidence_step_ids"]:
            evidence = next(step for step in evidence_steps if step["evidence_step_id"] == evidence_id)
            for anchor in evidence_anchor_snapshots[evidence_id]:
                anchors.append({
                    "paragraph_key": anchor["segment_index"],
                    "media_time": anchor["start_time"],
                    "evidence_id": evidence_id,
                    "evidence_type": evidence["step_type"],
                    "speaker": evidence["speaker"],
                    "stance": evidence["stance"],
                    "discourse_role": evidence["discourse_role"],
                    "assertive": evidence["speaker"] == "professor" and evidence["stance"] == "asserted",
                    "proposed_highlight": {"text": anchor["verbatim_excerpt"], "status": "proposed"},
                })
        item["occurrences"] = [{
            "transcript_id": transcript_id,
            "lecture": transcript.get("metadata", {}).get("title", transcript_id),
            "anchors": anchors,
        }]
        item["maturity"] = "candidate"
        claims.append(item)

    package = {
        "schema_version": "wang_shared_knowledge_v1.2",
        "package_id": f"DETAILED-{_slug(transcript_id)}",
        "source_documents": [{
            "source_id": source_id,
            "source_type": "sermon_transcript",
            "transcript_id": transcript_id,
            "title": transcript.get("metadata", {}).get("title", transcript_id),
            "source_sha256": source_sha256,
            "source_path": str(transcript_path),
            "review_status": "candidate",
        }],
        "source_fragments": fragments,
        "questions": questions,
        "position_nodes": positions,
        "observations": observations,
        "evidence_steps": evidence_steps,
        "claims": claims,
        "knowledge_relations": response["evidence_relations"],
        "claim_relations": response["claim_relations"],
        "extraction": {**extraction, "generated_at": datetime.now(timezone.utc).isoformat()},
        "summary": {
            "source_fragment_count": len(fragments),
            "question_count": len(questions),
            "position_count": len(positions),
            "observation_count": len(observations),
            "evidence_step_count": len(evidence_steps),
            "claim_count": len(claims),
            "evidence_relation_count": len(response["evidence_relations"]),
            "claim_relation_count": len(response["claim_relations"]),
        },
    }
    return package


def run_one(
    transcript_path: Path, *, output_dir: Path, client: Stage1OpenAIClient,
    prompt: str, reasoning_effort: str, force: bool,
) -> tuple[str, Path]:
    transcript, raw = _load(transcript_path)
    transcript_id = transcript_path.stem
    identity = extraction_identity(
        source_sha256=hashlib.sha256(raw).hexdigest(), prompt=prompt,
        model_id=client.model, reasoning_effort=reasoning_effort,
        max_output_tokens=client.max_output_tokens,
    )
    output_path = output_dir / f"{_slug(transcript_id)}.detailed-knowledge.json"
    if output_path.is_file() and not force:
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        if (existing.get("extraction") or {}).get("fingerprint_sha256") == identity["fingerprint_sha256"]:
            return "skipped", output_path
    user_input = (
        f"逐字稿 ID：{transcript_id}\n标题：{transcript.get('metadata', {}).get('title', transcript_id)}\n\n"
        "以下是完整逐字稿。S 编号是唯一定位码。请输出完整 JSON。\n\n"
        + _transcript_for_prompt(transcript)
    )
    last_error: DetailedExtractionValidationError | None = None
    last_candidate: dict[str, Any] | None = None
    response = None
    for attempt in range(1, VALIDATION_ATTEMPTS + 1):
        current = user_input
        if last_error and last_candidate:
            current += (
                "\n\n===== 上一版 JSON（必须以此为基础修复）=====\n"
                + json.dumps(last_candidate, ensure_ascii=False)
                + "\n\n===== 机械验证反馈 =====\n"
                + _validation_feedback(last_error, transcript)
            )
        candidate = client.generate_json(prompt, current, DETAILED_RESPONSE_SCHEMA)
        try:
            validate_response(candidate, transcript)
            response = candidate
            break
        except DetailedExtractionValidationError as exc:
            last_error = exc
            last_candidate = candidate
            _archive_rejected_candidate(
                output_dir=output_dir,
                transcript_id=transcript_id,
                attempt=attempt,
                candidate=candidate,
                error=exc,
            )
    if response is None:
        raise last_error or DetailedExtractionValidationError("detailed extraction validation failed")
    package = compile_package(
        transcript_id=transcript_id, transcript_path=transcript_path,
        transcript=transcript, raw=raw, response=response, extraction=identity,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    _archive(output_path)
    output_path.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return "created", output_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript-dir", type=Path, default=DEFAULT_TRANSCRIPT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--ids", nargs="+", required=True)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high"], default="medium")
    parser.add_argument("--max-output-tokens", type=int, default=32000)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    paths = [args.transcript_dir / f"{transcript_id}.json" for transcript_id in args.ids]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        parser.error("missing transcripts: " + ", ".join(missing))
    if args.dry_run:
        print(json.dumps({
            "transcripts": args.ids, "model": args.model,
            "reasoning_effort": args.reasoning_effort,
            "max_output_tokens": args.max_output_tokens,
            "would_call_openai": False,
        }, ensure_ascii=False))
        return 0
    load_dotenv(PROJECT_ROOT / ".env")
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    client = Stage1OpenAIClient(
        model=args.model, reasoning_effort=args.reasoning_effort,
        timeout_seconds=600, max_retries=3, max_output_tokens=args.max_output_tokens,
    )
    counts = {"created": 0, "skipped": 0, "failed": 0}
    for path in paths:
        try:
            status, output = run_one(
                path, output_dir=args.output_dir, client=client, prompt=prompt,
                reasoning_effort=args.reasoning_effort, force=args.force,
            )
            counts[status] += 1
            print(f"{status}: {path.name} -> {output}")
        except (DetailedExtractionValidationError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            counts["failed"] += 1
            print(f"FAILED: {path.name}: {exc}")
    print(json.dumps(counts, ensure_ascii=False))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
