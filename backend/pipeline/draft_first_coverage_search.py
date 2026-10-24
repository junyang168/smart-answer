"""Selection coverage before writing: did the essay pack take all his teaching? (#439)

The author writes only from the essay pack, and the reviewer's checklist is
built from the same pack, so anything left out when the viewpoints were
chosen can never be written or missed. For 人子 the choice was a keyword
pass (`coverage-inventory.json`: 17 of 30 viewpoints, 26 Claims in no
viewpoint) that nobody reviewed, and Claims created afterwards (CL013,
「人子象徵代表神」) were never in it.

Two steps, both explicit:

  search  a codex agent (gpt-6.1-sol) searches every current viewpoint and
          Claim for teaching on the topic that the selection does not hold,
          and lists candidates. Nothing is approved or written.
  apply   the owner's decision on every candidate — include or adjacent,
          with a reason — is recorded into a new editorial selection;
          included viewpoints join its focal list. A Claim in no viewpoint
          cannot enter the pack by itself: including one is recorded as
          needing CVP work first.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.draft_first_review_agent import CodexReviewAgent

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT = Path(__file__).with_name("prompts") / "draft_first_coverage_search.md"
MODEL = "gpt-6.1-sol"
COLLECTIONS = ("canonical_viewpoints", "viewpoint_revisions", "viewpoint_claim_links", "claims")

SCHEMA: dict[str, Any] = {
    "name": "wang_draft_first_coverage_search_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "searched": {"type": "array", "items": {"type": "string"}},
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "kind": {"type": "string", "enum": ["viewpoint", "claim"]},
                        "id": {"type": "string"},
                        "why_relevant": {"type": "string"},
                        "suggestion": {"type": "string", "enum": ["include", "adjacent"]},
                        "reason": {"type": "string"},
                    },
                    "required": ["kind", "id", "why_relevant", "suggestion", "reason"],
                },
            },
            "summary": {"type": "string"},
        },
        "required": ["searched", "candidates", "summary"],
    },
}

TOOL = '''"""Search the corpus. Run: python3 tools/search.py <command>

  viewpoints <regex>            current viewpoints whose proposition matches
  claims <regex> [--unlinked]   Claims whose statement matches; --unlinked: in no viewpoint
  viewpoint <viewpoint_id>      one viewpoint with its member Claims
"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def rows(name):
    with (ROOT / name).open() as stream:
        for line in stream:
            yield json.loads(line)


def main(args):
    if len(args) >= 2 and args[0] == "viewpoints":
        pattern = re.compile(args[1])
        for v in rows("viewpoints.jsonl"):
            if pattern.search(v["core_proposition"]):
                print(v["viewpoint_id"], v["member_claim_count"], v["core_proposition"], sep="\\t")
    elif len(args) >= 2 and args[0] == "claims":
        pattern, unlinked = re.compile(args[1]), "--unlinked" in args
        for c in rows("claims.jsonl"):
            if pattern.search(c["statement"]) and not (unlinked and c["viewpoint_ids"]):
                print(c["claim_id"], ",".join(c["viewpoint_ids"]) or "-", c["statement"], sep="\\t")
    elif len(args) == 2 and args[0] == "viewpoint":
        for v in rows("viewpoints.jsonl"):
            if v["viewpoint_id"] == args[1]:
                print(v["viewpoint_id"], v["core_proposition"], sep="\\t")
        for c in rows("claims.jsonl"):
            if args[1] in c["viewpoint_ids"]:
                print("  ", c["claim_id"], c["statement"], sep="\\t")
    else:
        sys.exit(__doc__)


main(sys.argv[1:])
'''


def corpus_index(records: Mapping[str, list[Mapping[str, Any]]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Current active viewpoints and live Claims, each Claim with its viewpoints."""

    revisions = {str(r["viewpoint_revision_id"]): r for r in records["viewpoint_revisions"]}
    members: dict[str, list[str]] = {}
    for link in records["viewpoint_claim_links"]:
        if link.get("effective_state", "active") == "active":
            members.setdefault(str(link["claim_id"]), []).append(str(link["viewpoint_id"]))
    viewpoints = []
    for viewpoint in records["canonical_viewpoints"]:
        if viewpoint.get("identity_status") != "active":
            continue
        revision = revisions.get(str(viewpoint.get("current_revision_id"))) or {}
        viewpoints.append(
            {
                "viewpoint_id": viewpoint["viewpoint_id"],
                "viewpoint_revision_id": viewpoint.get("current_revision_id"),
                "core_proposition": str(revision.get("core_proposition") or ""),
                "member_claim_count": sum(viewpoint["viewpoint_id"] in ids for ids in members.values()),
            }
        )
    claims = []
    for claim in records["claims"]:
        if claim.get("review_status") == "superseded":
            continue
        sources = sorted({str(o.get("source_id") or o.get("transcript_id") or "") for o in claim.get("occurrences") or []} - {""})
        claims.append(
            {
                "claim_id": claim["claim_id"],
                "statement": str(claim.get("statement") or ""),
                "viewpoint_ids": sorted(set(members.get(str(claim["claim_id"]), []))),
                "sources": sources,
                "scripture_refs": claim.get("scripture_refs") or [],
            }
        )
    return viewpoints, claims


def prepare_folder(folder: Path, *, scope: Mapping[str, Any], selection: Mapping[str, Any],
                   viewpoints: list[dict[str, Any]], claims: list[dict[str, Any]]) -> None:
    folder.mkdir(parents=True, exist_ok=False)
    by_revision = {v["viewpoint_revision_id"]: v for v in viewpoints}
    selected = [
        {**(by_revision.get(item["viewpoint_revision_id"]) or {"viewpoint_revision_id": item["viewpoint_revision_id"]}),
         "structure_role": item.get("structure_role")}
        for item in selection["focal_viewpoints"]
    ]
    question = {
        "reader_question": scope.get("reader_question"),
        "passage_refs": scope.get("passage_refs") or [],
        "decision_basis": selection.get("decision_basis"),
    }
    (folder / "question.json").write_text(json.dumps(question, ensure_ascii=False, indent=1), encoding="utf-8")
    (folder / "selected.json").write_text(json.dumps(selected, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, rows in (("viewpoints.jsonl", viewpoints), ("claims.jsonl", claims)):
        (folder / name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    (folder / "tools").mkdir()
    (folder / "tools" / "search.py").write_text(TOOL, encoding="utf-8")


def validate_candidates(result: Mapping[str, Any], *, selection: Mapping[str, Any],
                        viewpoints: list[dict[str, Any]], claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Candidates the owner can decide: real, not already selected, text from the record."""

    viewpoint_by_id = {v["viewpoint_id"]: v for v in viewpoints}
    claim_by_id = {c["claim_id"]: c for c in claims}
    selected_revisions = {item["viewpoint_revision_id"] for item in selection["focal_viewpoints"]}
    selected_ids = {v["viewpoint_id"] for v in viewpoints if v["viewpoint_revision_id"] in selected_revisions}
    seen: set[str] = set()
    candidates = []
    for item in result["candidates"]:
        record = (viewpoint_by_id if item["kind"] == "viewpoint" else claim_by_id).get(item["id"])
        if record is None:
            raise ValueError(f"candidate is not a current {item['kind']}: {item['id']}")
        if item["id"] in seen:
            raise ValueError(f"candidate listed twice: {item['id']}")
        seen.add(item["id"])
        if item["kind"] == "viewpoint" and item["id"] in selected_ids:
            raise ValueError(f"candidate is already selected: {item['id']}")
        if item["kind"] == "claim" and set(record["viewpoint_ids"]) & selected_ids:
            raise ValueError(f"candidate Claim already belongs to a selected viewpoint: {item['id']}")
        # The statement shown to the owner comes from the record, never the model.
        text = record["core_proposition"] if item["kind"] == "viewpoint" else record["statement"]
        candidates.append({**item, "text": text, **({"viewpoint_revision_id": record["viewpoint_revision_id"]}
                                                    if item["kind"] == "viewpoint" else {"viewpoint_ids": record["viewpoint_ids"]})})
    return candidates


def apply_decisions(selection: Mapping[str, Any], candidates_record: Mapping[str, Any],
                    decisions: list[Mapping[str, Any]]) -> dict[str, Any]:
    """A new editorial selection carrying the owner's decision on every candidate."""

    candidates = {c["id"]: c for c in candidates_record["candidates"]}
    decided = {str(d["id"]): d for d in decisions}
    if sorted(decided) != sorted(candidates) or len(decided) != len(decisions):
        raise ValueError(f"decide every candidate exactly once; undecided: {sorted(set(candidates) - set(decided))}")
    focal = [dict(item) for item in selection["focal_viewpoints"]]
    recorded = []
    for candidate_id, decision in decided.items():
        candidate = candidates[candidate_id]
        if decision.get("decision") not in ("include", "adjacent"):
            raise ValueError(f"{candidate_id}: decision must be include or adjacent")
        if not str(decision.get("reason") or "").strip():
            raise ValueError(f"{candidate_id}: a decision needs a reason")
        entry = {"id": candidate_id, "kind": candidate["kind"], "decision": decision["decision"],
                 "reason": decision["reason"], "text": candidate["text"]}
        if decision["decision"] == "include" and candidate["kind"] == "viewpoint":
            if not decision.get("structure_role"):
                raise ValueError(f"{candidate_id}: an included viewpoint needs a structure_role")
            focal.append({"viewpoint_revision_id": candidate["viewpoint_revision_id"],
                          "structure_role": decision["structure_role"], "basis_claim_ids": []})
        elif decision["decision"] == "include":
            # The pack is built from viewpoints; a Claim in none must go
            # through CVP work before an essay can carry it.
            entry["needs_cvp_work"] = True
        recorded.append(entry)
    return {
        **selection,
        "focal_viewpoints": focal,
        "coverage_review": {
            "candidates_sha256": sha256_json(candidates_record),
            "decisions": recorded,
        },
    }


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--editorial-selection", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--apply-decisions", type=Path,
                        help="owner decisions JSON; writes editorial-selection.json with coverage_review into --output-dir")
    args = parser.parse_args()
    selection = json.loads(args.editorial_selection.read_text(encoding="utf-8"))
    candidates_path = args.output_dir / "coverage-candidates.json"

    if args.apply_decisions:
        record = json.loads(candidates_path.read_text(encoding="utf-8"))
        decisions = json.loads(args.apply_decisions.read_text(encoding="utf-8"))
        updated = apply_decisions(selection, record, decisions)
        (args.output_dir / "editorial-selection.json").write_text(
            json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"focal_viewpoints": len(updated["focal_viewpoints"]),
                          "decisions": len(updated["coverage_review"]["decisions"])}, ensure_ascii=False))
        return 0

    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore

    scope = json.loads(args.scope.read_text(encoding="utf-8"))
    store = PostgresKnowledgeStore()
    records = {collection: store.list_records(collection) for collection in COLLECTIONS}
    viewpoints, claims = corpus_index(records)
    folder = args.output_dir / "folder"
    if not folder.exists():
        prepare_folder(folder, scope=scope, selection=selection, viewpoints=viewpoints, claims=claims)
    agent = CodexReviewAgent(model=MODEL, reasoning_effort="high")
    result, receipt = agent.review(folder=folder, call_dir=args.output_dir / "call",
                                   prompt=PROMPT.read_text(encoding="utf-8"), schema=SCHEMA)
    candidates = validate_candidates(result, selection=selection, viewpoints=viewpoints, claims=claims)
    record = {
        "schema_version": "wang_draft_first_coverage_candidates_v1",
        "selection_id": selection.get("selection_id"),
        "selection_sha256": sha256_json(selection),
        "corpus": {"viewpoints": len(viewpoints), "claims": len(claims)},
        "searched": result["searched"],
        "summary": result["summary"],
        "candidates": candidates,
        "agent": receipt,
    }
    candidates_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"candidates": len(candidates), "output": str(candidates_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
