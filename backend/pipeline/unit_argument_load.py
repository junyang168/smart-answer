"""Load a reviewed cross-lecture unit (``unit_argument_candidate_v1``) into ``ARGUMENT_DATABASE_URL``.

Each unit directory holds ``unit-argument.json`` plus its verification artifacts
(``fidelity_summary.json``, ``roundtrip_diff.json``, optional ``roundtrip_attribution.json``).
Before writing a ChangeSet into ``wang_knowledge``, this loader enforces:
1. Owner acceptance ruling (``verdict == "approved"``) in ``series-unit-rulings.json``.
2. ``fidelity_summary.json`` present with zero unresolved ``changed`` / ``added`` / ``findings``.
3. ``roundtrip_diff.json`` present, and if ``roundtrip_attribution.json`` is present its
   ``diff_sha256`` matches the SHA-256 of ``roundtrip_diff.json``.
4. Full package validation via ``validate_merged_package(package, native_relations=True)``
   and ``normalize_package(package)``.
5. Transactional insert into ``wang_knowledge.{change_sets, objects, object_versions}``
   with read-back verification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from psycopg.types.json import Jsonb

from backend.api.canonical_repository.postgres_store import normalize_package, record_content_sha
from backend.pipeline import argument_review
from backend.pipeline.knowledge_package_merge import validate_merged_package
from backend.pipeline.series_unit_reader import RULINGS_REL

DEFAULT_DATA_BASE_DIR = Path("/opt/homebrew/var/www/church/web/data")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unit_key(series_id: str, unit_id: str) -> str:
    return f"{series_id}/{unit_id}"


def _namespace(series_id: str, unit_id: str) -> str:
    return f"UA-{series_id}-{unit_id}"


def _latest_unit_ruling(
    data_base_dir: Path,
    series_id: str,
    unit_id: str,
    rulings_path: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    path = rulings_path or (Path(data_base_dir) / RULINGS_REL)
    if not path.exists():
        return None
    rows = json.loads(path.read_text(encoding="utf-8"))
    latest: Optional[dict[str, Any]] = None
    for row in rows:
        if row.get("series_id") == series_id and row.get("unit_id") == unit_id:
            latest = row
    return latest


def verify_unit_gates(
    unit_dir: Path,
    *,
    series_id: str,
    unit_id: str,
    data_base_dir: Path = DEFAULT_DATA_BASE_DIR,
    rulings_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Verify owner ruling, fidelity summary, and round-trip artifacts for one unit."""
    unit_arg_path = unit_dir / "unit-argument.json"
    if not unit_arg_path.exists():
        raise ValueError(f"missing unit-argument.json in {unit_dir}")

    ruling = _latest_unit_ruling(data_base_dir, series_id, unit_id, rulings_path=rulings_path)
    if not ruling or ruling.get("verdict") != "approved":
        verdict = ruling.get("verdict") if ruling else None
        raise ValueError(f"unit {_unit_key(series_id, unit_id)} is not approved (verdict={verdict!r})")

    fidelity_path = unit_dir / "fidelity_summary.json"
    if not fidelity_path.exists():
        raise ValueError(f"missing fidelity_summary.json in {unit_dir}")
    fidelity = json.loads(fidelity_path.read_text(encoding="utf-8"))
    total_nodes = int(fidelity.get("total_nodes") or 0)
    faithful = int(fidelity.get("faithful") or 0)
    changed = int(fidelity.get("changed") or 0)
    added = int(fidelity.get("added") or 0)
    findings = fidelity.get("findings") or []
    if total_nodes <= 0 or changed != 0 or added != 0 or faithful != total_nodes or findings:
        raise ValueError(
            f"unit {_unit_key(series_id, unit_id)} has unresolved fidelity items "
            f"(faithful={faithful}/{total_nodes}, changed={changed}, added={added}, findings={len(findings)})"
        )

    diff_path = unit_dir / "roundtrip_diff.json"
    if not diff_path.exists():
        raise ValueError(f"missing roundtrip_diff.json in {unit_dir}")
    diff_sha = file_sha256(diff_path)

    attr_path = unit_dir / "roundtrip_attribution.json"
    attr_sha: Optional[str] = None
    if attr_path.exists():
        attribution = json.loads(attr_path.read_text(encoding="utf-8"))
        if attribution.get("diff_sha256") != diff_sha:
            raise ValueError(
                f"stale roundtrip_attribution.json for {_unit_key(series_id, unit_id)}: "
                f"expected diff_sha256={diff_sha}, got {attribution.get('diff_sha256')!r}"
            )
        attr_sha = file_sha256(attr_path)

    return {
        "unit_argument_path": str(unit_arg_path),
        "unit_argument_sha256": file_sha256(unit_arg_path),
        "ruling": ruling,
        "fidelity_sha256": file_sha256(fidelity_path),
        "fidelity": fidelity,
        "roundtrip_diff_sha256": diff_sha,
        "roundtrip_attribution_sha256": attr_sha,
    }


def unit_argument_to_package(
    unit_data: dict[str, Any],
    *,
    series_id: str,
    unit_id: str,
) -> dict[str, Any]:
    """Convert a ``unit_argument_candidate_v1`` payload into a validated canonical package."""
    if unit_data.get("version") != "unit_argument_candidate_v1":
        raise ValueError(f"unsupported unit version: {unit_data.get('version')!r}")

    ns = _namespace(series_id, unit_id)
    sources = unit_data.get("sources") or []
    if not sources:
        raise ValueError("unit-argument.json has no sources")

    source_documents: list[dict[str, Any]] = []
    source_body_sha_by_id: dict[str, str] = {}
    source_idx_by_id: dict[str, int] = {}
    for idx, src in enumerate(sources, start=1):
        sid = str(src["source_id"])
        body_sha = str(src.get("body_sha256") or "")
        source_body_sha_by_id[sid] = body_sha
        source_idx_by_id[sid] = idx
        source_documents.append(
            {
                "source_id": sid,
                "source_type": "sermon_transcript",
                "transcript_id": sid,
                "title": sid,
                "source_sha256": body_sha,
                "source_body_sha256": body_sha,
                "source_file_sha256": str(src.get("file_sha256") or ""),
                "locator_space": "spoken_body_v1",
                "review_status": "approved",
            }
        )

    # Group sentences by (source_id, paragraph) into deterministic SourceFragments.
    sentences = unit_data.get("sentences") or []
    sent_by_id: dict[str, dict[str, Any]] = {}
    para_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for s in sentences:
        sent_id = str(s["sentence_id"])
        sent_by_id[sent_id] = s
        sid = str(s["source_id"])
        para = str(s.get("paragraph") or "P0")
        para_groups.setdefault((sid, para), []).append(s)

    source_fragments: list[dict[str, Any]] = []
    frag_id_by_para: dict[tuple[str, str], str] = {}
    first_frag_by_source: dict[str, str] = {}
    for (sid, para), items in para_groups.items():
        sidx = source_idx_by_id.get(sid, 0)
        frag_id = f"{ns}-FR-{sidx}-{para}"
        frag_id_by_para[(sid, para)] = frag_id
        first_frag_by_source.setdefault(sid, frag_id)
        excerpt = "".join(str(it.get("text") or "") for it in items).strip() or " "
        source_fragments.append(
            {
                "fragment_id": frag_id,
                "source_id": sid,
                "paragraph_key": para,
                "verbatim_excerpt": excerpt,
                "source_sha256": source_body_sha_by_id.get(sid, ""),
                "anchor_state": "resolved",
                "review_status": "approved",
            }
        )

    default_source_id = str(sources[0]["source_id"])
    default_frag_id = first_frag_by_source.get(default_source_id)
    if not default_frag_id:
        default_frag_id = f"{ns}-FR-1-P0"
        source_fragments.append(
            {
                "fragment_id": default_frag_id,
                "source_id": default_source_id,
                "paragraph_key": "P0",
                "verbatim_excerpt": " ",
                "source_sha256": source_body_sha_by_id.get(default_source_id, ""),
                "anchor_state": "unresolved",
                "review_status": "approved",
            }
        )

    def _fragments_for_at(at_list: Any, fallback_source: Optional[str] = None) -> list[str]:
        found: list[str] = []
        seen: set[str] = set()
        for raw_sid in at_list or []:
            s = sent_by_id.get(str(raw_sid))
            if not s:
                continue
            key = (str(s["source_id"]), str(s.get("paragraph") or "P0"))
            fid = frag_id_by_para.get(key)
            if fid and fid not in seen:
                seen.add(fid)
                found.append(fid)
        if not found:
            fb = first_frag_by_source.get(str(fallback_source or default_source_id)) or default_frag_id
            found.append(fb)
        return found

    questions: list[dict[str, Any]] = []
    position_nodes: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    evidence_steps_by_id: dict[str, dict[str, Any]] = {}
    claims_by_id: dict[str, dict[str, Any]] = {}

    q_state_map = {
        str(qs.get("question_id")): str(qs.get("state") or "unanswered")
        for qs in (unit_data.get("question_states") or [])
    }

    node_target_id: dict[str, str] = {}
    node_collection: dict[str, str] = {}

    raw_claims: dict[str, dict[str, Any]] = unit_data.get("claims") or {}
    raw_nodes: dict[str, dict[str, Any]] = unit_data.get("nodes") or {}

    for cid, cdata in raw_claims.items():
        full_id = f"{ns}-{cid}"
        node_target_id[cid] = full_id
        node_collection[cid] = "claims"
        frag_ids = _fragments_for_at(cdata.get("at"), cdata.get("source_id"))
        claims_by_id[full_id] = {
            "claim_id": full_id,
            "statement": str(cdata.get("statement") or ""),
            "claim_type": str(cdata.get("claim_kind") or "interpretive_judgment"),
            "attribution": str(cdata.get("attribution") or "professor"),
            "scripture_refs": list(cdata.get("scripture_refs") or []),
            "topic_ids": [],
            "topic_terms": list(cdata.get("topic_terms") or []),
            "evidence_step_ids": [],
            "opposed_position_ids": [],
            "source_fragment_ids": frag_ids,
            "review_status": "approved",
            "maturity": "approved",
        }

    for nid, ndata in raw_nodes.items():
        if nid in raw_claims:
            continue
        full_id = f"{ns}-{nid}"
        kind = str(ndata.get("kind") or "evidence")
        statement = str(ndata.get("statement") or "")
        frag_ids = _fragments_for_at(ndata.get("at"))
        node_target_id[nid] = full_id

        if kind == "question":
            node_collection[nid] = "questions"
            questions.append(
                {
                    "question_id": full_id,
                    "text": statement,
                    "questioner": "professor",
                    "question_type": "open_question",
                    "answer_state": q_state_map.get(nid, "unanswered"),
                    "answer_claim_ids": [],
                    "source_fragment_id": frag_ids[0],
                    "source_fragment_ids": frag_ids,
                    "review_status": "approved",
                }
            )
        elif kind == "position":
            node_collection[nid] = "position_nodes"
            position_nodes.append(
                {
                    "position_id": full_id,
                    "title": statement,
                    "attribution": "external_position",
                    "source_fragment_ids": frag_ids,
                    "review_status": "approved",
                }
            )
        elif kind == "observation":
            node_collection[nid] = "observations"
            observations.append(
                {
                    "observation_id": full_id,
                    "statement": statement,
                    "observation_type": "text_observation",
                    "source_fragment_id": frag_ids[0],
                    "source_fragment_ids": frag_ids,
                    "review_status": "approved",
                }
            )
        elif kind == "claim":
            node_collection[nid] = "claims"
            claims_by_id[full_id] = {
                "claim_id": full_id,
                "statement": statement,
                "claim_type": "local_claim",
                "attribution": "professor",
                "evidence_step_ids": [],
                "opposed_position_ids": [],
                "source_fragment_ids": frag_ids,
                "review_status": "approved",
                "maturity": "approved",
            }
        else:
            # 'evidence' or 'example'
            node_collection[nid] = "evidence_steps"
            evidence_steps_by_id[full_id] = {
                "evidence_step_id": full_id,
                "statement": statement,
                "step_type": kind,
                "speaker": "professor",
                "stance": "asserted",
                "source_fragment_id": frag_ids[0],
                "source_fragment_ids": frag_ids,
                "produced_claim_ids": [],
                "review_status": "approved",
            }

    questions_by_id = {q["question_id"]: q for q in questions}
    knowledge_relations: list[dict[str, Any]] = []
    claim_relations: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str, str]] = set()
    kr_idx = 0
    cr_idx = 0

    for edge in unit_data.get("edges") or []:
        raw_from = str(edge.get("from_id") or "")
        raw_to = str(edge.get("to_id") or "")
        rel_type = str(edge.get("relation") or "").strip()
        if raw_from not in node_target_id or raw_to not in node_target_id:
            continue
        from_id = node_target_id[raw_from]
        to_id = node_target_id[raw_to]
        if from_id == to_id or not rel_type:
            continue

        from_col = node_collection[raw_from]
        to_col = node_collection[raw_to]

        # Maintain reciprocal claim/evidence bindings for supports (evidence -> claim).
        if rel_type == "supports" and from_col == "evidence_steps" and to_col == "claims":
            if from_id not in claims_by_id[to_id]["evidence_step_ids"]:
                claims_by_id[to_id]["evidence_step_ids"].append(from_id)
            if to_id not in evidence_steps_by_id[from_id]["produced_claim_ids"]:
                evidence_steps_by_id[from_id]["produced_claim_ids"].append(to_id)

        # Track answers -> questions answer_claim_ids and refutes -> opposed_position_ids.
        if rel_type == "answers" and from_col == "claims" and to_col == "questions":
            if from_id not in questions_by_id[to_id]["answer_claim_ids"]:
                questions_by_id[to_id]["answer_claim_ids"].append(from_id)
        if rel_type == "refutes" and from_col == "claims" and to_col == "position_nodes":
            if to_id not in claims_by_id[from_id]["opposed_position_ids"]:
                claims_by_id[from_id]["opposed_position_ids"].append(to_id)

        if from_col == "claims" and to_col == "claims":
            sig = ("claim_relations", from_id, to_id, rel_type)
            if sig in seen_edges:
                continue
            seen_edges.add(sig)
            cr_idx += 1
            claim_relations.append(
                {
                    "claim_relation_id": f"{ns}-CR{cr_idx:04d}",
                    "from_id": from_id,
                    "to_id": to_id,
                    "relation_type": rel_type,
                    "reason": str(edge.get("reason") or ""),
                    "review_status": "approved",
                }
            )
        else:
            sig = ("knowledge_relations", from_id, to_id, rel_type)
            if sig in seen_edges:
                continue
            seen_edges.add(sig)
            kr_idx += 1
            knowledge_relations.append(
                {
                    "relation_id": f"{ns}-KR{kr_idx:04d}",
                    "from_id": from_id,
                    "to_id": to_id,
                    "relation_type": rel_type,
                    "reason": str(edge.get("reason") or ""),
                    "review_status": "approved",
                }
            )

    package = {
        "schema_version": "wang_shared_knowledge_v1.2",
        "package_id": f"UNIT-{series_id}-{unit_id}",
        "source_documents": source_documents,
        "source_fragments": source_fragments,
        "questions": questions,
        "position_nodes": position_nodes,
        "observations": observations,
        "evidence_steps": list(evidence_steps_by_id.values()),
        "claims": list(claims_by_id.values()),
        "knowledge_relations": knowledge_relations,
        "claim_relations": claim_relations,
    }
    validate_merged_package(package, native_relations=True)
    return package


def unit_holder(conn: Any, series_id: str, unit_id: str) -> Optional[str]:
    """Return the active ChangeSet ID holding ``series_id/unit_id``, if any."""
    row = conn.execute(
        """
        SELECT change_set_id FROM wang_knowledge.change_sets
         WHERE status = 'applied'
           AND metadata->>'unit_key' = %s
           AND NOT EXISTS (
               SELECT 1 FROM wang_knowledge.change_sets r
                WHERE r.status = 'applied'
                  AND r.metadata->>'replaces' = wang_knowledge.change_sets.change_set_id
           )
         ORDER BY applied_at DESC
         LIMIT 1
        """,
        (_unit_key(series_id, unit_id),),
    ).fetchone()
    return str(row[0]) if row else None


def _retire_unit(conn: Any, prior_cs: str, incoming: dict[str, dict[str, dict[str, Any]]], new_cs: str) -> dict[str, int]:
    """Retire old unit-scoped objects from ``prior_cs`` not present in ``incoming``."""
    retired: dict[str, int] = {}
    rows = conn.execute(
        """
        SELECT DISTINCT o.collection, o.object_id, o.revision, o.payload
          FROM wang_knowledge.object_versions v
          JOIN wang_knowledge.objects o USING (collection, object_id)
         WHERE v.change_set_id = %s
           AND o.collection <> 'source_documents'
           AND o.review_status <> 'retired'
        """,
        (prior_cs,),
    ).fetchall()
    for col, oid, rev, payload in rows:
        if oid in incoming.get(col, {}):
            continue
        new_rev = int(rev) + 1
        retired_payload = {**dict(payload), "review_status": "retired", "revision": new_rev}
        sha = record_content_sha(retired_payload)
        conn.execute(
            """
            UPDATE wang_knowledge.objects
               SET revision = %s, review_status = 'retired', content_sha256 = %s,
                   payload = %s, updated_at = now()
             WHERE collection = %s AND object_id = %s
            """,
            (new_rev, sha, Jsonb(retired_payload), col, oid),
        )
        conn.execute(
            """
            INSERT INTO wang_knowledge.object_versions
                (collection, object_id, revision, content_sha256, payload, change_set_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (col, oid, new_rev, sha, Jsonb(retired_payload), new_cs),
        )
        retired[col] = retired.get(col, 0) + 1
    return retired


def load_unit(
    conn: Any,
    unit_dir: Path,
    *,
    series_id: str,
    unit_id: str,
    data_base_dir: Path = DEFAULT_DATA_BASE_DIR,
    rulings_path: Optional[Path] = None,
    replaces: Optional[str] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Verify, convert, and transactionally load one unit into ``wang_knowledge``."""
    unit_dir = Path(unit_dir)
    gates = verify_unit_gates(
        unit_dir,
        series_id=series_id,
        unit_id=unit_id,
        data_base_dir=data_base_dir,
        rulings_path=rulings_path,
    )
    unit_data = json.loads((unit_dir / "unit-argument.json").read_text(encoding="utf-8"))
    package = unit_argument_to_package(unit_data, series_id=series_id, unit_id=unit_id)
    records = normalize_package(package)
    counts = {col: len(vals) for col, vals in records.items() if vals}

    if dry_run:
        return {
            "dry_run": True,
            "unit_key": _unit_key(series_id, unit_id),
            "package_id": package["package_id"],
            "counts": counts,
            "gates": gates,
        }

    current = unit_holder(conn, series_id, unit_id)
    if current != replaces:
        if current and not replaces:
            raise ValueError(f"{_unit_key(series_id, unit_id)} already held by {current}; pass replaces={current}")
        if replaces and current != replaces:
            raise ValueError(f"expected {_unit_key(series_id, unit_id)} held by {replaces}, currently {current}")

    source_sha = gates["unit_argument_sha256"]
    cs_id = f"unit-argument-{series_id}-{unit_id}-{source_sha[:12]}"
    meta = {
        "unit_key": _unit_key(series_id, unit_id),
        "series_id": series_id,
        "unit_id": unit_id,
        "unit_plan_sha256": unit_data.get("unit_plan_sha256"),
        "source_ids": [s["source_id"] for s in unit_data.get("sources") or []],
        "ruling": gates["ruling"],
        "fidelity_sha256": gates["fidelity_sha256"],
        "roundtrip_diff_sha256": gates["roundtrip_diff_sha256"],
        "roundtrip_attribution_sha256": gates["roundtrip_attribution_sha256"],
    }
    if replaces:
        meta["replaces"] = replaces

    written: dict[str, int] = {}
    retired: dict[str, int] = {}
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO wang_knowledge.change_sets
                (change_set_id, fingerprint_sha256, package_id, source_kind, source_sha256, status, metadata, applied_at)
            VALUES (%s, %s, %s, 'unit_argument_v1', %s, 'applied', %s, now())
            """,
            (cs_id, source_sha, package["package_id"], source_sha, Jsonb(meta)),
        )
        for col, vals in records.items():
            for oid, payload in vals.items():
                existing = conn.execute(
                    "SELECT revision, content_sha256 FROM wang_knowledge.objects WHERE collection=%s AND object_id=%s",
                    (col, oid),
                ).fetchone()
                rev = (int(existing[0]) + 1) if existing else 1
                row_payload = {**payload, "revision": rev}
                sha = record_content_sha(row_payload)
                if existing and col == "source_documents" and existing[1] == sha:
                    # Shared lecture source_document already present with identical content.
                    written[col] = written.get(col, 0) + 1
                    continue
                if existing:
                    conn.execute(
                        """
                        UPDATE wang_knowledge.objects
                           SET revision=%s, review_status=%s, visibility=%s, content_sha256=%s,
                               payload=%s, updated_at=now()
                         WHERE collection=%s AND object_id=%s
                        """,
                        (rev, row_payload["review_status"], row_payload["visibility"], sha, Jsonb(row_payload), col, oid),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO wang_knowledge.objects
                            (collection, object_id, revision, review_status, visibility, content_sha256, payload)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (col, oid, rev, row_payload["review_status"], row_payload["visibility"], sha, Jsonb(row_payload)),
                    )
                conn.execute(
                    """
                    INSERT INTO wang_knowledge.object_versions
                        (collection, object_id, revision, content_sha256, payload, change_set_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (col, oid, rev, sha, Jsonb(row_payload), cs_id),
                )
                written[col] = written.get(col, 0) + 1

        if replaces:
            retired = _retire_unit(conn, replaces, records, cs_id)

    # Read-back verification outside the write loop.
    stored_cs = conn.execute(
        "SELECT status, package_id FROM wang_knowledge.change_sets WHERE change_set_id=%s",
        (cs_id,),
    ).fetchone()
    if not stored_cs or stored_cs[0] != "applied":
        raise RuntimeError(f"read-back failed for change_set {cs_id}")
    stored_versions = conn.execute(
        "SELECT count(*) FROM wang_knowledge.object_versions WHERE change_set_id=%s",
        (cs_id,),
    ).fetchone()[0]

    return {
        "change_set_id": cs_id,
        "unit_key": _unit_key(series_id, unit_id),
        "package_id": package["package_id"],
        "written": written,
        "retired": retired,
        "stored_versions": int(stored_versions),
        "read_back": "matches",
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--series", required=True, help="Series ID, e.g. 2014-nysc-matthew-4")
    parser.add_argument("--unit", required=True, help="Unit ID, e.g. U04")
    parser.add_argument("--data-base-dir", type=Path, default=DEFAULT_DATA_BASE_DIR)
    parser.add_argument("--unit-dir", type=Path, default=None)
    parser.add_argument("--rulings-path", type=Path, default=None)
    parser.add_argument("--replaces", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    unit_dir = args.unit_dir or (args.data_base_dir / "unit_argument_series" / args.series / "units" / args.unit)
    if args.dry_run:
        res = load_unit(
            None,
            unit_dir,
            series_id=args.series,
            unit_id=args.unit,
            data_base_dir=args.data_base_dir,
            rulings_path=args.rulings_path,
            replaces=args.replaces,
            dry_run=True,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    with argument_review.connect() as conn:
        res = load_unit(
            conn,
            unit_dir,
            series_id=args.series,
            unit_id=args.unit,
            data_base_dir=args.data_base_dir,
            rulings_path=args.rulings_path,
            replaces=args.replaces,
            dry_run=False,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
