"""One command per topic for CVP 3.0 (#456, solution doc §3).

    start <topic> --claims claims.json   run the agent on a topic's Claims, as a
                                         discovery agent found them; stops only
                                         if the owner has questions to answer
    finish <run-dir>                     after the owner answers: back up, write
                                         relations, links and summaries, and
                                         corrections to existing relations, then
                                         generate argument routes in parallel
    status <run-dir>                     where a run stands

The owner answers in the run's questions.json ("write"/"skip", or "noted" for
merges and open questions); questions.md is the same list to read.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore, database_url_from_env, sha256_json
from backend.pipeline.topic_cvp_pilot import PILOT_ROOT, run as run_agent
from backend.pipeline.topic_cvp_write import load_run, check_refs, settled_inputs, write_part

REPO = Path(__file__).resolve().parents[2]
# Links first: they create the new CVPs that relations may point to.
PARTS = ("links", "relations", "relation-fixes")
ROUTE_WORKERS = 3


def questions_md(questions: list[dict]) -> str:
    lines = [f"# 需要负责人决定的 {len(questions)} 项", "",
             "在 questions.json 里给每项的 answer 填 write（照提议写）或 skip（不写）；合并提议与未决填 noted。", ""]
    for n, q in enumerate(questions, 1):
        lines += [f"{n}. **{q['item']}**（{q['why']}）", f"   - {q['what']}", f"   - 复核意见：{q['reviewer']}"]
        if q["correction"]:
            lines.append(f"   - 复核建议改为：{q['correction']}")
        lines.append("")
    return "\n".join(lines)


def start(topic: str, claim_ids: list[str], run_id: str | None) -> Path:
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = PILOT_ROOT / f"{topic}-{run_id}"
    root.mkdir(parents=True, exist_ok=True)
    (root / "run.json").write_text(json.dumps({"topic": topic, "aliases": [], "claim_ids": sorted(set(claim_ids))},
                                              ensure_ascii=False, indent=1))
    state = run_agent(root, topic, [], claim_ids=sorted(set(claim_ids)))
    if state.get("status") == "awaiting_owner":
        (root / "questions.md").write_text(questions_md(json.loads((root / "questions.json").read_text())))
    elif state.get("status") == "settled":
        finish(root)
    print(json.dumps({"run": str(root), **state}, ensure_ascii=False))
    return root


def backup(root: Path) -> Path:
    label = "before-topic-" + sha256_json(str(root))[:12]
    out = subprocess.run([str(REPO / "scripts" / "backup-registry.sh"), label], capture_output=True, text=True, check=True)
    match = re.search(r"wrote (\S+\.dump)", out.stdout + out.stderr)
    if not match:
        raise RuntimeError("backup did not report its dump path")
    return Path(match.group(1))


def routes(root: Path) -> dict:
    """Queue every CVP whose members this run changed; run workers until the queue is idle."""
    from backend.pipeline.viewpoint_route_cvp_worker import enqueue_current_cvp_routes, route_affected_viewpoints
    package_path = root / "write" / "links-package.json"
    affected = route_affected_viewpoints(json.loads(package_path.read_text())) if package_path.exists() else []
    queue, out = root / "routes-queue", root / "routes"
    store = PostgresKnowledgeStore(database_url_from_env())
    refused = {}
    for viewpoint_id in affected:
        try:
            enqueue_current_cvp_routes(store, [viewpoint_id], queue)
        except ValueError as exc:  # e.g. a member Claim still on hold
            refused[viewpoint_id] = str(exc)[:300]

    def worker(n: int) -> list[str]:
        seen = []
        while True:
            done = subprocess.run([sys.executable, "-m", "backend.pipeline.viewpoint_route_cvp_worker", "run",
                                   "--queue-dir", str(queue), "--output-dir", str(out), "--apply",
                                   "--worker-id", f"{root.name}-w{n}"], cwd=REPO, capture_output=True, text=True)
            if '"status": "idle"' in done.stdout:
                return seen
            seen.append(done.stdout.strip().splitlines()[-1][:200] if done.returncode == 0 else
                        (done.stderr.strip().splitlines() or ["error"])[-1][:300])

    with ThreadPoolExecutor(ROUTE_WORKERS) as pool:
        logs = [line for lines in pool.map(worker, range(1, ROUTE_WORKERS + 1)) for line in lines]
    states = {}
    for state in (queue / "states").glob("*") if (queue / "states").exists() else []:
        value = json.loads(state.read_text())
        states[value.get("viewpoint_id") or state.stem] = value.get("status")
    return {"affected": affected, "refused": refused, "job_states": states, "worker_log": logs}


def finish(root: Path) -> dict:
    proposal, verdicts, decisions, proposal_raw, review_raw = settled_inputs(root)
    dump = backup(root)
    results = {}
    for part in PARTS:
        saved, packet, private = load_run(root)
        check_refs(part, saved, packet)
        results[part] = write_part(root, part, proposal=proposal, verdicts=verdicts, decisions=decisions,
                                   proposal_raw=proposal_raw, review_raw=review_raw,
                                   decisions_path=root / "decisions.json", proposal_path=root / "final-proposal.json",
                                   backup=dump, execute=True, saved=saved, packet=packet, private=private)
    results["routes"] = routes(root)
    noted = [q for q in decisions["answers"] if q["choices"] == ["noted"]]
    results["for_the_owner"] = [f"{q['item']}：{q['what']}" for q in noted]
    (root / "finish.json").write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    state = json.loads((root / "state.json").read_text())
    state["status"] = "written"
    (root / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in results.items() if k != "routes"} | {"routes": {k: results["routes"][k] for k in ("affected", "refused", "job_states")}},
                     ensure_ascii=False, default=str))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("start")
    s.add_argument("topic")
    s.add_argument("--claims", type=Path, required=True, help="JSON list of Claim ids")
    s.add_argument("--run-id")
    f = sub.add_parser("finish")
    f.add_argument("run", type=Path)
    t = sub.add_parser("status")
    t.add_argument("run", type=Path)
    args = parser.parse_args()
    if args.command == "start":
        start(args.topic, json.loads(args.claims.read_text()), args.run_id)
    elif args.command == "finish":
        finish(args.run)
    else:
        print((args.run / "state.json").read_text())


if __name__ == "__main__":
    main()
