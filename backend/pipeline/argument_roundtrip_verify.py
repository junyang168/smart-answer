"""Layer 2 Calibrated Round-Trip Verification for the Wang Knowledge Platform argument layer.

Three-stage cross-vendor verification (calibrated on Gate 0: 11/11 recall, 0 High/Medium FP):
  1. Stage 1 (Blind Reconstructor, GPT): reconstructs Prof. Wang's argument from a strictly
     transcript-free blind graph grouped by undirected connected components, marking typed
     [GAP] tags where logic breaks or edges conflict/reverse.
  2. Stage 2 (Black-box Judge, Claude Opus 5.5): compares the reconstruction + reported GAPs
     directly against the full numbered transcript (NO raw argument JSON) across 3 FineSurE /
     Arguinas axes (core omissions, stance/logic distortions, non-argument noise).
  3. Stage 3 (Post-hoc Root-Cause Attribution, Claude Opus 5.5, optional): after Stage 2 is
     frozen, attributes each reported finding against the blind graph to separate true
     extraction_defect from reconstructor_compression or schema_convention.
"""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any

from backend.pipeline.claim_unit_argument import blind_reading

VERSION = "argument_roundtrip_verify_v2"

FORBIDDEN_BLIND_KEYS = frozenset({
    "source_fragments",
    "verbatim_excerpt",
    "proposed_highlight",
    "transcript",
    "transcript_spoken_rows",
    "media_time",
    "media_end_time",
    "paragraph_key",
    "occurrences",
    "sentences",
    "at",
})


def sha256_json(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def short_id(raw_id: str) -> str:
    parts = raw_id.split("-")
    if len(parts) >= 3 and parts[-2].startswith("P") and parts[-1].startswith("ST"):
        return f"{parts[-2]}-{parts[-1]}"
    return parts[-1]


def compute_components(nodes: dict[str, Any], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group nodes and edges into undirected connected components."""
    adj: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        adj[e["from_id"]].add(e["to_id"])
        adj[e["to_id"]].add(e["from_id"])

    visited: set[str] = set()
    comps: list[dict[str, Any]] = []
    for nid in nodes:
        if nid in visited:
            continue
        q = deque([nid])
        visited.add(nid)
        comp_nodes = []
        while q:
            cur = q.popleft()
            comp_nodes.append(cur)
            for nxt in adj.get(cur, ()):
                if nxt in nodes and nxt not in visited:
                    visited.add(nxt)
                    q.append(nxt)
        node_set = set(comp_nodes)
        comp_edges = [e["id"] for e in edges if e["from_id"] in node_set and e["to_id"] in node_set]
        comps.append(
            {
                "component_id": f"COMP{len(comps) + 1:02d}",
                "node_ids": comp_nodes,
                "edge_ids": comp_edges,
                "claim_ids": [x for x in comp_nodes if nodes[x].get("kind") == "claim"],
            }
        )
    return comps


def assert_no_transcript_leakage(obj: Any, path: str = "$") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in FORBIDDEN_BLIND_KEYS:
                raise AssertionError(f"Transcript leakage detected at {path}.{k}")
            assert_no_transcript_leakage(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            assert_no_transcript_leakage(item, f"{path}[{i}]")


def project_blind_graph(data: dict[str, Any]) -> dict[str, Any]:
    """Project unit_argument_candidate_v1, reader_input fixture, or v2 DB export into a transcript-free graph."""
    if data.get("version") == "unit_argument_candidate_v1" and "nodes" in data and "edges" in data:
        ri = blind_reading(data)
        nodes = {k: {"kind": v["kind"], "statement": v["statement"]} for k, v in ri["nodes"].items()}
        edges = [
            {"id": eid, **{k: v[k] for k in ("from_id", "to_id", "relation", "reason", "attribution")}}
            for eid, v in ri["edges"].items()
        ]
        blind = {
            "format": "unit_argument_blind_v2",
            "title": data.get("title", ""),
            "components": compute_components(nodes, edges),
            "nodes": nodes,
            "edges": edges,
            "question_states": ri.get("question_states", []),
            "gaps": ri.get("gaps", []),
        }
    elif "reader_input" in data:
        ri = data["reader_input"]
        nodes = {k: {"kind": v["kind"], "statement": v["statement"]} for k, v in ri["nodes"].items()}
        edges = [
            {"id": eid, **{k: v[k] for k in ("from_id", "to_id", "relation", "reason", "attribution")}}
            for eid, v in ri["edges"].items()
        ]
        blind = {
            "format": "unit_argument_blind_v2",
            "title": data.get("question", data.get("case", "")),
            "components": compute_components(nodes, edges),
            "nodes": nodes,
            "edges": edges,
            "question_states": ri.get("question_states", []),
            "gaps": ri.get("gaps", []),
        }
    elif "records" in data and "claims" in data["records"]:
        recs = data["records"]
        id_map: dict[str, str] = {}
        nodes: dict[str, dict[str, Any]] = {}

        def reg(raw_id: str, node: dict[str, Any]) -> str:
            sid = short_id(raw_id)
            if sid in nodes:
                sid = raw_id
            id_map[raw_id] = sid
            nodes[sid] = node
            return sid

        for c in recs.get("claims", []):
            reg(
                c["claim_id"],
                {
                    "kind": "claim",
                    "claim_type": c.get("claim_type") or "",
                    "attribution": c.get("attribution") or "professor",
                    "statement": c.get("statement", ""),
                    "scripture_refs": list(c.get("scripture_refs") or []),
                },
            )
        for e in recs.get("evidence_steps", []):
            reg(
                e["evidence_step_id"],
                {
                    "kind": "evidence_step",
                    "step_type": e.get("step_type") or "",
                    "speaker": e.get("speaker") or "",
                    "stance": e.get("stance") or "",
                    "statement": e.get("statement", ""),
                    "scripture_refs": list(e.get("scripture_refs") or []),
                },
            )
        for o in recs.get("observations", []):
            reg(
                o["observation_id"],
                {
                    "kind": "observation",
                    "observation_type": o.get("observation_type") or "",
                    "argument_role": o.get("argument_role") or "",
                    "statement": o.get("statement", ""),
                    "scripture_refs": list(o.get("scripture_refs") or []),
                },
            )
        for p in recs.get("position_nodes", []):
            reg(
                p["position_id"],
                {
                    "kind": "position",
                    "attribution": p.get("attribution") or "external",
                    "statement": p.get("title", ""),
                },
            )
        for q in recs.get("questions", []):
            reg(
                q["question_id"],
                {
                    "kind": "question",
                    "questioner": q.get("questioner") or "",
                    "answer_state": q.get("answer_state") or "unanswered",
                    "statement": q.get("text", ""),
                },
            )

        edges: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()

        def add_edge(src_raw: str, dst_raw: str, relation: str, reason: str, edge_source: str) -> None:
            src = id_map.get(src_raw)
            dst = id_map.get(dst_raw)
            if not src or not dst:
                return
            key = (src, dst, relation)
            if key in seen:
                return
            seen.add(key)
            edges.append(
                {
                    "id": f"E{len(edges) + 1:03d}",
                    "from_id": src,
                    "to_id": dst,
                    "relation": relation,
                    "reason": reason,
                    "edge_source": edge_source,
                }
            )

        for col in ("knowledge_relations", "claim_relations"):
            for r in recs.get(col, []):
                add_edge(r["from_id"], r["to_id"], r.get("relation_type", "supports"), r.get("reason", ""), col)
        for e in recs.get("evidence_steps", []):
            warrant = (e.get("discourse_role") or "").strip()
            for cid in e.get("produced_claim_ids") or []:
                add_edge(e["evidence_step_id"], cid, "supports", warrant, "evidence_step.produced_claim_ids")
        for c in recs.get("claims", []):
            for pid in c.get("opposed_position_ids") or []:
                add_edge(
                    c["claim_id"],
                    pid,
                    "refutes",
                    "该主张及其连入的支持步骤共同反驳此外部立场（v2 schema 以 opposed_position_ids 关联）。",
                    "claim.opposed_position_ids",
                )
        for q in recs.get("questions", []):
            for cid in q.get("answer_claim_ids") or []:
                add_edge(
                    cid,
                    q["question_id"],
                    "answers",
                    "该主张回答此问题（v2 schema 以 answer_claim_ids 关联）。",
                    "question.answer_claim_ids",
                )

        blind = {
            "format": "wang_knowledge_v2_blind_v2",
            "title": data.get("title", ""),
            "components": compute_components(nodes, edges),
            "nodes": nodes,
            "edges": edges,
        }
    else:
        raise ValueError("Unsupported input JSON format for round-trip verification")

    assert_no_transcript_leakage(blind)
    return blind


def extract_transcript_text(data: dict[str, Any]) -> str:
    """Build numbered paragraph transcript text for Stage 2 black-box diff."""
    if data.get("version") == "unit_argument_candidate_v1" and data.get("sentences"):
        para_texts: list[str] = []
        cur_key: tuple[str, str] | None = None
        cur_buf: list[str] = []
        for row in data["sentences"]:
            k = (row["source_id"], row["paragraph"])
            if k != cur_key:
                if cur_key is not None:
                    para_texts.append(f"[{cur_key[0][-6:]} {cur_key[1]}] {''.join(cur_buf)}")
                cur_key = k
                cur_buf = [row["text"]]
            else:
                cur_buf.append(row["text"])
        if cur_key is not None:
            para_texts.append(f"[{cur_key[0][-6:]} {cur_key[1]}] {''.join(cur_buf)}")
        return "\n".join(para_texts)
    transcript_rows = data.get("transcript_spoken_rows") or []
    if transcript_rows:
        return "\n\n".join(f"[{r['id']}] {r['text']}" for r in transcript_rows)
    raise ValueError("Input does not contain sentences or transcript_spoken_rows for Stage 2 diff")


RECONSTRUCTOR_SYSTEM = """你是一位严谨且忠实的论证重构审查员（Blind Reconstructor）。
你将**只**收到一份从讲道中抽取出的结构化论证层数据（包含连通分量 `components`、节点 `nodes` 和显式有向边 `edges`），你**没有**看过教授的原始逐字稿。

你的任务：严格依据输入的显式节点与显式边，按 `components`（连通模块）逐一重构教授的论证过程。

### 核心规则
1. **忠实保留节点里的承重推理机制（严禁过度压缩）**：
   - 每个节点里明确写出的关键依据、推理机制、限定条件与反方内容必须完整保留，**绝不能把具体推论压扁成空洞概括**。
2. **零外部脑补**：
   - 严禁动用你的圣经知识、神学常识或猜测去补出输入里没有的经文、结论或推理桥梁。
3. **准确区分「独立子话题」与「真正缺口 `[GAP]`」**：
   - 不要在不同 `component` 之间强行要求连接，也不要因此打 `[GAP]`；按独立分节叙述即可。
   - 只有以下情形才在原位标 `[GAP: 简述缺口]` 并在 `gaps` 中登记：
     - `missing_claim_bridge`：**同一个 `component` 内部**有多条共享支撑材料或主题紧扣的 Claim，但彼此之间完全没有显式关系边；
     - `missing_warrant`：边两端存在明显逻辑跳跃，且节点文字和边 `reason` 都没交代它们为何相关（若节点或 `reason` 已写明是举例说明或方法示范，即使边标签是 `supports` 也照实叙述，不因此打 `[GAP]`）；
     - `missing_premise_or_qualification`：结论里的核心限定、比较级判断或前提在连入节点中完全找不到支撑；
     - `unresolved_question_or_position`：问题无回答边，或反方立场没有任何主张/依据去回应；
     - `conflicting_or_reversed_edge`：同一对节点存在方向相反的边，或边的方向与边自己的 `reason` / 节点立场明显矛盾（**务必显式指出，不可悄悄绕过**）；
     - `isolated_node`：完全没有任何连边的节点。
4. **严格区分立场与角色**：
   - `position` 节点是教授引述或反驳的外部看法，绝不可写成教授主张。
   - 故事/例证保留其说明或佐证的要点，不可把故事中人物的言行写成教授的教导。
5. **全程溯源**：每一节必须列出实际引用的 `cited_nodes` 和 `cited_edges`。
"""

RECONSTRUCTOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "overall_structure_summary",
        "sections",
        "full_narrative",
        "gaps",
        "isolated_nodes",
    ],
    "properties": {
        "overall_structure_summary": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["component_id", "section_title", "narrative_with_gaps", "cited_nodes", "cited_edges"],
                "properties": {
                    "component_id": {"type": "string"},
                    "section_title": {"type": "string"},
                    "narrative_with_gaps": {"type": "string"},
                    "cited_nodes": {"type": "array", "items": {"type": "string"}},
                    "cited_edges": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "full_narrative": {"type": "string"},
        "gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["gap_id", "gap_type", "location", "description", "involved_ids"],
                "properties": {
                    "gap_id": {"type": "string"},
                    "gap_type": {
                        "type": "string",
                        "enum": [
                            "missing_claim_bridge",
                            "missing_warrant",
                            "missing_premise_or_qualification",
                            "unresolved_question_or_position",
                            "conflicting_or_reversed_edge",
                            "isolated_node",
                        ],
                    },
                    "location": {"type": "string"},
                    "description": {"type": "string"},
                    "involved_ids": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "isolated_nodes": {"type": "array", "items": {"type": "string"}},
    },
}

JUDGE_SYSTEM = """你是一位独立的论证质量裁判模型（Black-box Structured Diff Judge）。
你将只收到两份材料：
1. **教授原始讲道逐字稿**（带段号，这是唯一权威标准）；
2. **盲测重构叙述**（由另一模型仅凭「论证层结构化数据」在没看过原文的情况下复述出来的叙述，其中包含 `[GAP: ...]`）。

你**不看**论证层原始 JSON，采用黑盒端到端的方式，按 FineSurE / Arguinas 细粒度拆解思路评估重构叙述对教授论证的忠实度与完整度。

### 必须遵守的讲道评测口径（防止误报）
- **口语讲道不是法律条文**：措辞、语序、详略不同不算错误；只抓实质判断、承重推理、限定条件或立场方向的真实偏差。
- **非承重生活应用只需简提**：顺带提及的生活应用除非与本单元核心论证强关联（承担推论前提或限定条件），否则论证层只需简提甚至不展开，不得因此报为中高危缺失。
- **支持观点的故事不算噪音**：教授用来说明或佐证某个观点的经历、比喻、生活例子整段保留是合法的，不得判成 `non_argument_noise`。只有 `digression`（无经文无推理的题外话）、`housekeeping_or_filler`（纯场务寒暄）、`ungrounded_addition`（原文无据脑补）才算噪音。
- **举例说明 / 方法示范不算逻辑扭曲**：只要没有用例子顶替承重经文依据、也没有把故事角色言行抬升成教授教导，就不要仅因边标签叫 `supports` 判成 `stance_and_logic_distortion`。
- **真正高危的扭曲是指**：`stance_inversion`、`direction_reversal`、`scope_or_modality_drift`、`miswired_support`、`example_as_claim`。

### 工作流程
1. **原子论证单元拆解（`original_units`）**；
2. **三维度结构化比对**：`core_argument_omissions`、`stance_and_logic_distortions`、`non_argument_noise`；
3. **`gap_diagnoses`**：逐一判定 `true_extraction_gap` / `true_source_openness` / `false_alarm`；
4. **汇总指标与可执行建议**。每条结论必须锚定逐字稿段号。
"""

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "original_units",
        "core_argument_omissions",
        "stance_and_logic_distortions",
        "non_argument_noise",
        "gap_diagnoses",
        "summary_metrics",
        "top_actionable_fixes",
    ],
    "properties": {
        "original_units": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["unit_id", "role", "importance", "transcript_refs", "summary", "coverage", "coverage_note"],
                "properties": {
                    "unit_id": {"type": "string"},
                    "role": {
                        "type": "string",
                        "enum": [
                            "core_claim",
                            "load_bearing_premise",
                            "qualification",
                            "opposed_position",
                            "key_question",
                            "key_illustration",
                            "application",
                        ],
                    },
                    "importance": {"type": "string", "enum": ["core", "supporting"]},
                    "transcript_refs": {"type": "array", "items": {"type": "string"}},
                    "summary": {"type": "string"},
                    "coverage": {"type": "string", "enum": ["covered", "partial", "missing"]},
                    "coverage_note": {"type": "string"},
                },
            },
        },
        "core_argument_omissions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "omission_id",
                    "severity",
                    "category",
                    "transcript_refs",
                    "original_excerpt",
                    "manifested_as_gap",
                    "explanation",
                ],
                "properties": {
                    "omission_id": {"type": "string"},
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    "category": {
                        "type": "string",
                        "enum": [
                            "missing_claim",
                            "missing_premise",
                            "missing_qualification",
                            "missing_logical_bridge",
                            "missing_question_or_position",
                        ],
                    },
                    "transcript_refs": {"type": "array", "items": {"type": "string"}},
                    "original_excerpt": {"type": "string"},
                    "manifested_as_gap": {"type": "boolean"},
                    "explanation": {"type": "string"},
                },
            },
        },
        "stance_and_logic_distortions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "distortion_id",
                    "severity",
                    "subtype",
                    "reconstructed_quote",
                    "transcript_refs",
                    "original_excerpt",
                    "explanation",
                ],
                "properties": {
                    "distortion_id": {"type": "string"},
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    "subtype": {
                        "type": "string",
                        "enum": [
                            "stance_inversion",
                            "direction_reversal",
                            "scope_or_modality_drift",
                            "miswired_support",
                            "example_as_claim",
                        ],
                    },
                    "reconstructed_quote": {"type": "string"},
                    "transcript_refs": {"type": "array", "items": {"type": "string"}},
                    "original_excerpt": {"type": "string"},
                    "explanation": {"type": "string"},
                },
            },
        },
        "non_argument_noise": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "noise_id",
                    "severity",
                    "subtype",
                    "reconstructed_quote",
                    "transcript_refs",
                    "explanation",
                ],
                "properties": {
                    "noise_id": {"type": "string"},
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    "subtype": {
                        "type": "string",
                        "enum": [
                            "digression",
                            "housekeeping_or_filler",
                            "ungrounded_addition",
                        ],
                    },
                    "reconstructed_quote": {"type": "string"},
                    "transcript_refs": {"type": "array", "items": {"type": "string"}},
                    "explanation": {"type": "string"},
                },
            },
        },
        "gap_diagnoses": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["gap_id", "verdict", "transcript_refs", "explanation"],
                "properties": {
                    "gap_id": {"type": "string"},
                    "verdict": {
                        "type": "string",
                        "enum": ["true_extraction_gap", "true_source_openness", "false_alarm"],
                    },
                    "transcript_refs": {"type": "array", "items": {"type": "string"}},
                    "explanation": {"type": "string"},
                },
            },
        },
        "summary_metrics": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "total_original_units",
                "core_units_total",
                "core_units_covered",
                "core_units_partial",
                "core_units_missing",
                "omission_count",
                "silent_omission_count",
                "distortion_count",
                "noise_count",
                "true_extraction_gap_count",
                "overall_assessment",
            ],
            "properties": {
                "total_original_units": {"type": "integer"},
                "core_units_total": {"type": "integer"},
                "core_units_covered": {"type": "integer"},
                "core_units_partial": {"type": "integer"},
                "core_units_missing": {"type": "integer"},
                "omission_count": {"type": "integer"},
                "silent_omission_count": {"type": "integer"},
                "distortion_count": {"type": "integer"},
                "noise_count": {"type": "integer"},
                "true_extraction_gap_count": {"type": "integer"},
                "overall_assessment": {"type": "string"},
            },
        },
        "top_actionable_fixes": {"type": "array", "items": {"type": "string"}},
    },
}

ATTRIBUTION_SYSTEM = """你是论证校验流水线的「事后归因核查员」（Post-hoc Root-Cause Attributor）。
Stage 2 的黑盒裁判已经完成并封存了它的全部 findings（缺失 / 扭曲 / 噪音）。
现在把这些 findings 和 Stage 1 实际收到的「论证层盲测图 JSON」一起交给你。

你的唯一任务：逐条核对每条 finding，判断问题究竟出在哪里，避免冤枉抽取层：
- `extraction_defect`：原论证层 JSON 中确实缺失该节点/边/限定/理由，或边方向/立场本身就是错的；
- `reconstructor_compression`：原论证层 JSON 中其实已经保留了该内容或推论，是 Stage 1 重构模型在叙述时擅自压缩、遗漏或改偏了；
- `mixed`：原图写得不够完整，且 Stage 1 重构时又进一步压损；
- `schema_convention`：属于原图 Schema 的有意约定（如举例/方法示范统一用 supports 边），并非实质性语义错误。

每条归因必须引用具体节点 ID 或边 ID 作为证据。"""

ATTRIBUTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["attributions", "true_extraction_defect_count", "reconstructor_error_count"],
    "properties": {
        "attributions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["finding_id", "root_cause", "evidence_ids", "explanation"],
                "properties": {
                    "finding_id": {"type": "string"},
                    "root_cause": {
                        "type": "string",
                        "enum": [
                            "extraction_defect",
                            "reconstructor_compression",
                            "mixed",
                            "schema_convention",
                        ],
                    },
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "explanation": {"type": "string"},
                },
            },
        },
        "true_extraction_defect_count": {"type": "integer"},
        "reconstructor_error_count": {"type": "integer"},
    },
}


def run_verification(
    sample_path: Path,
    out_dir: Path,
    *,
    dry_run: bool = False,
    attribute: bool = True,
    reconstructor_client: Any = None,
    judge_client: Any = None,
    attributor_client: Any = None,
    transcript_text_override: str | None = None,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = json.loads(sample_path.read_text(encoding="utf-8"))
    blind_graph = project_blind_graph(raw)
    (out_dir / "01_blind_graph_input.json").write_text(
        json.dumps(blind_graph, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    stats = {
        "version": VERSION,
        "title": blind_graph["title"],
        "format": blind_graph["format"],
        "component_count": len(blind_graph["components"]),
        "node_count": len(blind_graph["nodes"]),
        "edge_count": len(blind_graph["edges"]),
        "blind_graph_sha256": sha256_json(blind_graph),
        "leakage_check": "PASS",
    }
    if dry_run:
        return stats

    if reconstructor_client is None or judge_client is None:
        from dotenv import load_dotenv
        from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
        from backend.pipeline.codex_subscription_client import CodexSubscriptionClient

        load_dotenv(".env")
        if reconstructor_client is None:
            reconstructor_client = CodexSubscriptionClient(model="gpt-6.1-sol", reasoning_effort="medium")
            reconstructor_client.capture_usage_events = True
        if judge_client is None:
            judge_client = ClaudeSubscriptionClient(model="claude-opus-5-5", reasoning_effort="high")
        if attributor_client is None:
            attributor_client = ClaudeSubscriptionClient(model="claude-opus-5-5", reasoning_effort="medium")
    elif attributor_client is None:
        attributor_client = judge_client

    # Stage 1: Blind Reconstruction
    t0 = time.time()
    if hasattr(reconstructor_client, "call_artifacts"):
        reconstructor_client.call_artifacts = out_dir / "stage1_reconstructor_artifacts"
    stage1_user = (
        "以下是唯一提供给你的论证层结构化数据（不含原文，已按无向连通分量分组）。"
        "请严格按照防脑补且不压缩承重推论的规则重构教授的论证过程：\n\n"
        + json.dumps(blind_graph, ensure_ascii=False, indent=2)
    )
    reconstruction = reconstructor_client.generate_json(
        RECONSTRUCTOR_SYSTEM, stage1_user, RECONSTRUCTOR_SCHEMA
    )
    t1 = time.time()
    (out_dir / "02_blind_reconstruction.json").write_text(
        json.dumps(reconstruction, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Stage 2: Black-box Structured Diff (NO raw JSON!)
    transcript_text = transcript_text_override or extract_transcript_text(raw)
    stage2_payload = {
        "lecture_title": blind_graph["title"],
        "reconstructed_narrative": reconstruction["full_narrative"],
        "reconstructor_reported_gaps": reconstruction["gaps"],
        "original_transcript": transcript_text,
    }
    if hasattr(judge_client, "call_artifacts"):
        judge_client.call_artifacts = out_dir / "stage2_judge_artifacts"
    stage2_user = (
        "请对以下「盲测重构叙述」与「教授原始讲道逐字稿」执行 FineSurE / Arguinas 风格的原子拆解与三维度结构化比对：\n\n"
        + json.dumps(stage2_payload, ensure_ascii=False, indent=2)
    )
    diff_report = judge_client.generate_json(JUDGE_SYSTEM, stage2_user, JUDGE_SCHEMA)
    t2 = time.time()
    (out_dir / "04_structured_diff_report.json").write_text(
        json.dumps(diff_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Stage 3: Post-hoc Root-Cause Attribution (only if there are findings)
    attribution_report = None
    t3 = t2
    all_findings = {
        "core_argument_omissions": diff_report["core_argument_omissions"],
        "stance_and_logic_distortions": diff_report["stance_and_logic_distortions"],
        "non_argument_noise": diff_report["non_argument_noise"],
    }
    if attribute and any(all_findings.values()):
        if hasattr(attributor_client, "call_artifacts"):
            attributor_client.call_artifacts = out_dir / "stage3_attribution_artifacts"
        stage3_user = (
            "以下是 Stage 2 黑盒裁判封存的 findings，以及 Stage 1 实际收到的论证层盲测图 JSON。"
            "请逐条核查每条 finding 的真正根因：\n\n"
            + json.dumps({"findings": all_findings, "blind_graph": blind_graph}, ensure_ascii=False, indent=2)
        )
        attribution_report = attributor_client.generate_json(
            ATTRIBUTION_SYSTEM, stage3_user, ATTRIBUTION_SCHEMA
        )
        t3 = time.time()
        (out_dir / "05_root_cause_attribution.json").write_text(
            json.dumps(attribution_report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    summary = {
        **stats,
        "stage1_seconds": round(t1 - t0, 1),
        "stage1_gaps": len(reconstruction["gaps"]),
        "stage2_seconds": round(t2 - t1, 1),
        "stage3_seconds": round(t3 - t2, 1),
        "metrics": diff_report["summary_metrics"],
        "attribution_summary": (
            {
                "true_extraction_defect_count": attribution_report["true_extraction_defect_count"],
                "reconstructor_error_count": attribution_report["reconstructor_error_count"],
            }
            if attribution_report
            else None
        ),
    }
    (out_dir / "00_run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    p = argparse.ArgumentParser(description="Layer 2 calibrated round-trip verification")
    p.add_argument("--sample", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-attribute", action="store_true")
    args = p.parse_args()
    res = run_verification(args.sample, args.out, dry_run=args.dry_run, attribute=not args.no_attribute)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
