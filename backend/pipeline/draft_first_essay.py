"""One resumable command for a draft-first essay, from scope to the owner's final review.

Every stage already had a command; what was missing was the order, the memory
of where a run stands, and the places it must stop for the owner. On the 人子
essay (#439) those were carried by hand: scope files written in the session,
coverage decisions made in conversation, an owner-note rerun assembled from
flags (the first attempt stopped on a stale "passed" flag).

A run directory holds the owner's inputs and every stage's output:

    essay.json                review_id and passage label for registration
    scope.json                reader question and passages (the owner's)
    editorial-selection.json  the viewpoints the owner chose
    coverage-decisions.json   include/adjacent for every coverage candidate
    owner-notes/NN.json       the owner's own notes, one file per extra round
    state.json                written here: where the run stands

Running the command does every stage it can and stops where the owner must
decide (exit status 3), writing what is to be decided to `state.json` and
stdout. Run it again after deciding; finished stages are not repeated unless
an input they read has changed. Model calls happen only in stages that call a
model, through the existing commands, which keep their own replayable records.

    python -m backend.pipeline.draft_first_essay --run-dir <dir>
    python -m backend.pipeline.draft_first_essay --run-dir <dir> --status
    python -m backend.pipeline.draft_first_essay --run-dir <dir> --redo coverage
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGES = ("scope", "coverage", "decisions", "cvp_gap",
          "packet", "author", "review", "binding", "blurb", "register", "final_review")
STOPPED = 3

Runner = Callable[[str, list[str], Path], None]


@dataclass
class Stop(Exception):
    """The run cannot go on until the owner decides `needs`."""

    stage: str
    needs: str
    detail: dict[str, Any] = field(default_factory=dict)


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def run_command(name: str, argv: list[str], log: Path) -> None:
    """Run one existing command in the project, its output kept in the run's logs."""

    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as handle:
        done = subprocess.run([sys.executable, *argv], cwd=PROJECT_ROOT, stdout=handle,
                              stderr=subprocess.STDOUT, check=False)
    if done.returncode:
        raise RuntimeError(f"{name} failed (exit {done.returncode}); see {log}")


class EssayRun:
    def __init__(self, run_dir: Path, runner: Runner = run_command):
        self.dir = run_dir.resolve()
        self.runner = runner
        self.state_path = self.dir / "state.json"
        self.state = _json(self.state_path) if self.state_path.exists() else {"stages": {}, "history": []}

    # -- paths ---------------------------------------------------------------
    def p(self, *parts: str) -> Path:
        return self.dir.joinpath(*parts)

    @property
    def packet(self) -> Path:
        return self.p("packet", "theological-evidence-packet.json")

    def review_dirs(self) -> list[Path]:
        """The base review, then one directory per owner-note round, in order."""

        dirs = [self.p("review")]
        dirs += sorted(self.p().glob("review-owner-*"))
        return [d for d in dirs if (d / "review-run.json").exists()]

    # -- bookkeeping ---------------------------------------------------------
    def _save(self) -> None:
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _fresh(self, stage: str, inputs: list[Path], outputs: list[Path]) -> bool:
        """Done before, from the same inputs, and its outputs are still there."""

        entry = self.state["stages"].get(stage)
        return bool(entry and entry.get("status") == "done"
                    and entry.get("inputs") == {str(p): _sha(p) for p in inputs}
                    and all(p.exists() for p in outputs))

    def _done(self, stage: str, inputs: list[Path], outputs: list[Path], **detail: Any) -> None:
        missing = [str(p) for p in outputs if not p.exists()]
        if missing:
            raise RuntimeError(f"{stage} left no output: {', '.join(missing)}")
        self.state["stages"][stage] = {
            "status": "done", "at": datetime.now(timezone.utc).isoformat(),
            "inputs": {str(p): _sha(p) for p in inputs},
            "outputs": {str(p): _sha(p) for p in outputs}, **detail,
        }
        self.state["history"].append({"stage": stage, "status": "done", "at": self.state["stages"][stage]["at"]})
        self._save()

    def redo(self, stage: str) -> None:
        """Forget `stage` and every stage after it; their outputs are rebuilt."""

        for name in STAGES[STAGES.index(stage):]:
            self.state["stages"].pop(name, None)
        self.state["history"].append({"stage": stage, "status": "redo", "at": datetime.now(timezone.utc).isoformat()})
        self._save()

    def _run(self, stage: str, argv: list[str]) -> None:
        self.runner(stage, argv, self.p("logs", f"{stage}-{len(self.state['history']):03d}.log"))

    # -- stages --------------------------------------------------------------
    def scope(self) -> None:
        required = [self.p("essay.json"), self.p("scope.json"), self.p("editorial-selection.json")]
        missing = [p.name for p in required if not p.exists()]
        if missing:
            raise Stop("scope", "the reader question, passages and chosen viewpoints", {"missing": missing})
        essay = _json(self.p("essay.json"))
        for key in ("review_id", "passage"):
            if not str(essay.get(key) or "").strip():
                raise Stop("scope", f"essay.json needs {key}")
        if not str(_json(self.p("scope.json")).get("reader_question") or "").strip():
            raise Stop("scope", "scope.json needs the reader question")
        self._done("scope", required, required)

    def coverage(self) -> None:
        inputs = [self.p("scope.json"), self.p("editorial-selection.json")]
        out = self.p("coverage", "coverage-candidates.json")
        if self._fresh("coverage", inputs, [out]):
            return
        self._run("coverage", ["-m", "backend.pipeline.draft_first_coverage_search",
                               "--scope", str(inputs[0]), "--editorial-selection", str(inputs[1]),
                               "--output-dir", str(self.p("coverage"))])
        self._done("coverage", inputs, [out])

    def decisions(self) -> None:
        candidates, decisions = self.p("coverage", "coverage-candidates.json"), self.p("coverage-decisions.json")
        out = self.p("coverage", "editorial-selection.json")
        if not decisions.exists():
            rows = _json(candidates)["candidates"]
            raise Stop("decisions", "include or adjacent, with a reason, for every coverage candidate", {
                "write": str(decisions),
                "candidates": [{k: c.get(k) for k in ("id", "kind", "suggestion", "text", "why_relevant", "reason")} for c in rows],
            })
        inputs = [self.p("scope.json"), self.p("editorial-selection.json"), candidates, decisions]
        if self._fresh("decisions", inputs, [out]):
            return
        self._run("decisions", ["-m", "backend.pipeline.draft_first_coverage_search",
                                "--scope", str(inputs[0]), "--editorial-selection", str(inputs[1]),
                                "--output-dir", str(self.p("coverage")), "--apply-decisions", str(decisions)])
        self._done("decisions", inputs, [out])

    def cvp_gap(self) -> None:
        selection = self.p("coverage", "editorial-selection.json")
        gaps = [d for d in _json(selection).get("coverage_review", {}).get("decisions", []) if d.get("needs_cvp_work")]
        if gaps:
            raise Stop("cvp_gap", "CVP work for included Claims that belong to no viewpoint, then --redo coverage", {
                "claims": [{"id": d["id"], "text": d["text"]} for d in gaps],
            })
        self._done("cvp_gap", [selection], [selection])

    def build_packet(self) -> None:
        inputs = [self.p("scope.json"), self.p("coverage", "editorial-selection.json")]
        if self._fresh("packet", inputs, [self.packet]):
            return
        self._run("packet", ["-m", "backend.pipeline.theological_evidence_packet_runner",
                             "--scope", str(inputs[0]), "--editorial-selection", str(inputs[1]),
                             "--source-delivery", "source_library", "--output-dir", str(self.p("packet"))])
        self._done("packet", inputs, [self.packet])

    def author(self) -> None:
        draft = self.p("author", "draft.md")
        if self._fresh("author", [self.packet], [draft, self.p("author", "self-check.json")]):
            return
        question = _json(self.p("scope.json"))["reader_question"]
        self._run("author", ["-m", "backend.pipeline.draft_first_author_runner", "--packet", str(self.packet),
                             "--reader-question", question, "--output-dir", str(self.p("author"))])
        self._done("author", [self.packet], [draft, self.p("author", "self-check.json")])

    def review(self) -> None:
        draft = self.p("author", "draft.md")
        base = self.p("review", "review-run.json")
        if not self._fresh("review", [draft, self.packet], [base]):
            self._run("review", ["-m", "backend.pipeline.draft_first_review_runner", "--draft", str(draft),
                                 "--packet", str(self.packet), "--output-dir", str(self.p("review"))])
            self._done("review", [draft, self.packet], [base])
        # Each owner-note file is one extra round on the latest draft: a
        # revision against the notes (and any open reviewer findings), then
        # one full review. Only the owner adds a round.
        consumed = self.state.setdefault("owner_notes", {})
        for note in sorted(self.p("owner-notes").glob("*.json")) if self.p("owner-notes").exists() else []:
            if consumed.get(note.name) == _sha(note):
                continue
            latest = self.review_dirs()[-1]
            target = self.p(f"review-owner-{note.stem}")
            notes = _json(note)
            argv = ["-m", "backend.pipeline.draft_first_review_runner", "--draft", str(latest / "final.md"),
                    "--packet", str(self.packet), "--continue-from", str(latest), "--output-dir", str(target)]
            if notes.get("notes"):
                note_file = self.p("logs", f"owner-notes-{note.stem}.json")
                note_file.parent.mkdir(parents=True, exist_ok=True)
                note_file.write_text(json.dumps(notes["notes"], ensure_ascii=False, indent=2), encoding="utf-8")
                argv += ["--owner-findings", str(note_file)]
            self._run(f"owner-round-{note.stem}", argv)
            consumed[note.name] = _sha(note)
            self._save()
        latest = self.review_dirs()[-1]
        run = _json(latest / "review-run.json")
        if run["status"] != "review_passed":
            last_round = max(latest.glob("round-*/outcome.json"), key=lambda path: path.parent.name)
            open_findings = _json(last_round)["blocking_findings"]
            raise Stop("review", "how to handle what the review could not settle: owner notes or an extra round", {
                "review_dir": str(latest), "status": run["status"],
                "open": [{k: f.get(k) for k in ("finding_id", "gate", "kind", "anchor", "summary")} for f in open_findings],
                "write": str(self.p("owner-notes", f"{len(consumed) + 1:02d}.json")),
            })
        self.state["current_review"] = str(latest)
        self._save()

    def binding(self) -> None:
        latest = Path(self.state["current_review"])
        out = latest / "source-bindings.json"
        inputs = [latest / "final.md", latest / "final-self-check.json", self.packet]
        if self._fresh("binding", inputs, [out]):
            return
        self._run("binding", ["-m", "backend.pipeline.draft_first_source_binding", "--review-dir", str(latest),
                              "--packet", str(self.packet), "--output", str(out)])
        self._done("binding", inputs, [out])

    def blurb(self) -> None:
        latest = Path(self.state["current_review"])
        inputs, out = [latest / "final.md"], latest / "reader-blurb.json"
        if self._fresh("blurb", inputs, [out]):
            return
        self._run("blurb", ["-m", "backend.pipeline.draft_first_reader_blurb",
                            "--manuscript", str(inputs[0]), "--output", str(out)])
        self._done("blurb", inputs, [out])

    def register(self) -> None:
        latest = Path(self.state["current_review"])
        inputs = [latest / "final.md", latest / "source-bindings.json", latest / "reader-blurb.json",
                  self.packet, self.p("essay.json")]
        if self._fresh("register", inputs, inputs):
            return
        essay = _json(self.p("essay.json"))
        self._run("register", ["scripts/register-draft-first-review.py", "--review-id", essay["review_id"],
                               "--passage", essay["passage"], "--product-kind", "theological_topic_essay",
                               "--review-dir", str(latest),
                               "--bindings", str(latest / "source-bindings.json"), "--packet", str(self.packet),
                               "--reader-blurb", str(latest / "reader-blurb.json")])
        self._done("register", inputs, inputs)

    def final_review(self) -> None:
        review_id = _json(self.p("essay.json"))["review_id"]
        raise Stop("final_review", "the owner's final review and publish decision on the review page", {
            "page": f"/admin/wang/operations/articles/reviews/{review_id}",
        })

    # -- driver --------------------------------------------------------------
    def advance(self) -> dict[str, Any]:
        steps = [self.scope, self.coverage, self.decisions, self.cvp_gap,
                 self.build_packet, self.author, self.review, self.binding, self.blurb, self.register, self.final_review]
        try:
            for step in steps:
                step()
        except Stop as stop:
            self.state["stopped"] = {"stage": stop.stage, "needs": stop.needs, **stop.detail,
                                     "at": datetime.now(timezone.utc).isoformat()}
            self._save()
            return {"status": "stopped", **self.state["stopped"]}
        return {"status": "complete"}


def init_run(run_dir: Path, *, review_id: str, title: str, question: str,
             passages: list[str], decision_basis: str) -> None:
    """Write a new run's owner inputs, so no session assembles them by hand.

    The selection starts with no viewpoints: the coverage search proposes
    them, and the owner's decisions put them in.
    """

    from backend.pipeline.theological_evidence_packet import make_editorial_scope

    run_dir.mkdir(parents=True, exist_ok=True)
    targets = {name: run_dir / name for name in ("essay.json", "scope.json", "editorial-selection.json")}
    if any(path.exists() for path in targets.values()):
        raise SystemExit(f"{run_dir} already has a run; continue it instead of starting again")
    selection_id = f"editorial-selection-{review_id}"
    scope = make_editorial_scope(
        scope_id=f"TES-{review_id}", working_title=title, reader_question=question,
        passage_refs=passages, structure_revision_id=selection_id,
        publication_profile_id="WQ-theological-topic-essay-v1",
    )
    files = {
        "essay.json": {"review_id": review_id, "passage": "；".join(passages)},
        "scope.json": scope,
        "editorial-selection.json": {"selection_id": selection_id, "attribution": "church_editor",
                                     "decision_basis": decision_basis, "focal_viewpoints": [],
                                     "unresolved_items": []},
    }
    for name, payload in files.items():
        targets[name].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def adopt_existing(stage: str, argv: list[str], log: Path) -> None:
    """Run nothing: a stage whose output already exists is recorded as done.

    For a run made before this command (the 人子 essay), and for replaying a
    saved run with zero model calls. A missing output fails the stage.
    """


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--status", action="store_true", help="print where the run stands; run nothing")
    parser.add_argument("--redo", choices=STAGES, help="forget this stage and every later one, then run")
    parser.add_argument("--adopt", action="store_true",
                        help="record outputs that already exist as done; run no command and call no model")
    init = parser.add_argument_group("starting a new essay (--init)")
    init.add_argument("--init", action="store_true", help="write the run's owner inputs, then stop")
    init.add_argument("--review-id", help="lowercase id for the review page, e.g. ransom-for-many-v1")
    init.add_argument("--title", help="working title")
    init.add_argument("--question", help="the reader question, as the owner settled it")
    init.add_argument("--passage", action="append", default=[], help="a passage, e.g. 太20:28; repeat")
    init.add_argument("--decision-basis", help="what the owner decided the essay is about, in the owner's words")
    args = parser.parse_args()
    if args.init:
        missing = [flag for flag in ("review_id", "title", "question", "decision_basis") if not getattr(args, flag)]
        if missing or not args.passage:
            parser.error("--init needs --review-id, --title, --question, --passage and --decision-basis")
        init_run(args.run_dir, review_id=args.review_id, title=args.title, question=args.question,
                 passages=args.passage, decision_basis=args.decision_basis)
        print(json.dumps({"status": "initialised", "run_dir": str(args.run_dir)}, ensure_ascii=False))
        return 0
    run = EssayRun(args.run_dir, runner=adopt_existing if args.adopt else run_command)
    if args.status:
        print(json.dumps({"stages": {k: v.get("status") for k, v in run.state["stages"].items()},
                          "stopped": run.state.get("stopped")}, ensure_ascii=False, indent=2))
        return 0
    if args.redo:
        run.redo(args.redo)
    result = run.advance()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return STOPPED if result["status"] == "stopped" else 0


if __name__ == "__main__":
    raise SystemExit(main())
