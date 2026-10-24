"""Run the two-way reviewer as a codex agent inside one review folder (#439).

The old reviewers were one-shot calls that judged only the source blocks
handed to them, while the author read as much as it wanted: the checker saw
less than the writer. Here the reviewer is `codex exec` started in a folder
holding the draft, the checklist, the approved structure and every source,
with a read-only browser in tools/. It reads what it needs itself.

`--sandbox read-only` stops writes, not reads, so the program checks the
event log: every command the agent ran is recorded there with its output,
and a command reaching outside the folder voids the review. The event log,
not the reviewer's own account, is the record of what was read — in the
2026-10-24 trial the agent's "sources read" left out a file its own search
had read.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any, Mapping

from backend.pipeline.codex_subscription_client import CodexSubscriptionClient, CodexSubscriptionError
from backend.pipeline.matthew_exposition_authoring import sha256_text

TOOL = '''"""Read-only browser for this review folder. Run: python3 tools/src.py <command>

  list                          every source: id, file, title, block count
  show <source> <a> [b]         blocks a..b (1-based, inclusive) of one source
  search <regex> [source]       blocks matching a regex, with their numbers
  claim <claim_id>              a Claim with its evidence steps
  claims <regex>                Claims whose statement matches
"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = json.loads((ROOT / "sources" / "index.json").read_text())


def blocks(entry):
    return (ROOT / "sources" / entry["file"]).read_text().split("\\n\\n")


def find(source):
    for entry in INDEX:
        if source in (entry["source_id"], entry["file"]):
            return entry
    sys.exit(f"unknown source: {source}")


def main(args):
    if not args or args[0] == "list":
        for e in INDEX:
            print(e["source_id"], e["file"], e["block_count"], e["title"], sep="\\t")
    elif args[0] == "show":
        entry = find(args[1]); a = int(args[2]); b = int(args[3]) if len(args) > 3 else a
        for n, text in enumerate(blocks(entry), 1):
            if a <= n <= b:
                print(f"[{entry['source_id']} #{n}]\\n{text}\\n")
    elif args[0] == "search":
        pattern = re.compile(args[1])
        for entry in ([find(args[2])] if len(args) > 2 else INDEX):
            for n, text in enumerate(blocks(entry), 1):
                if pattern.search(text):
                    print(f"[{entry['source_id']} #{n}]\\n{text}\\n")
    elif args[0] in ("claim", "claims"):
        structure = json.loads((ROOT / "structure.json").read_text())
        steps = {s.get("evidence_step_id"): s for s in structure.get("evidence_steps") or []}
        for claim in structure.get("claims") or []:
            hit = claim.get("claim_id") == args[1] if args[0] == "claim" else re.search(args[1], str(claim.get("statement")))
            if hit:
                print(claim.get("claim_id"), claim.get("statement"), sep="\\t")
                if args[0] == "claim":
                    for sid in claim.get("evidence_step_ids") or []:
                        print("  ", sid, json.dumps(steps.get(sid, {}), ensure_ascii=False)[:600])
    else:
        sys.exit(__doc__)


main(sys.argv[1:])
'''


def _file_name(number: int, source_id: str) -> str:
    return f"{number:02d}-" + re.sub(r"[^0-9A-Za-z_.-]+", "_", source_id)[:60] + ".md"


def prepare_folder(folder: Path, *, manuscript: str, packet: Mapping[str, Any], files: Mapping[str, Any]) -> None:
    """Lay out one review: the draft, the sources, the structure, and `files` as JSON."""

    folder.mkdir(parents=True, exist_ok=False)
    (folder / "draft.md").write_text(manuscript, encoding="utf-8")
    sources = folder / "sources"
    sources.mkdir()
    index = []
    for number, item in enumerate(packet["source_originals"]["originals"], 1):
        name = _file_name(number, str(item["source_id"]))
        content = str(item["content"])
        (sources / name).write_text(content, encoding="utf-8")
        index.append(
            {
                "source_id": item["source_id"],
                "file": name,
                "title": item.get("title"),
                "source_type": item.get("source_type"),
                "block_count": len(content.split("\n\n")),
            }
        )
    (sources / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    structure = {key: value for key, value in packet.items() if key != "source_originals"}
    (folder / "structure.json").write_text(json.dumps(structure, ensure_ascii=False), encoding="utf-8")
    for name, value in files.items():
        (folder / name).write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    (folder / "tools").mkdir()
    (folder / "tools" / "src.py").write_text(TOOL, encoding="utf-8")


ABSOLUTE_PATH = re.compile(r"(?<![\w.$-])/[^\s'\"|;&)<>]+")
SYSTEM_BIN = ("/bin/", "/usr/bin/", "/usr/local/bin/", "/opt/homebrew/bin/", "/dev/null")


def command_escapes(command: str, folder: Path) -> str | None:
    """Why a recorded command reaches outside the review folder, if it does."""

    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    # codex wraps every command as `<shell> -lc '<script>'`; judge the script.
    if len(words) >= 3 and words[1] in ("-lc", "-c"):
        command = words[2]
    roots = {str(folder), str(folder.resolve())}
    roots |= {root.replace("/private/", "/", 1) for root in roots if root.startswith("/private/")}
    for path in ABSOLUTE_PATH.findall(command):
        if path.startswith(SYSTEM_BIN):
            continue
        if not any(path == root or path.startswith(root + "/") for root in roots):
            return f"absolute path outside the review folder: {path}"
    if re.search(r"(^|[\s/'\"=])\.\.(/|\s|$)", command):
        return "parent-directory path"
    if re.search(r"(^|\s)~|\$HOME|\$\{HOME", command):
        return "home-directory path"
    return None


def read_events(stdout: str) -> list[dict[str, Any]]:
    events = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def audit_events(events: list[Mapping[str, Any]], folder: Path) -> dict[str, Any]:
    """The reading record, and every reason the run is not a valid review."""

    commands: list[dict[str, Any]] = []
    violations: list[str] = []
    for event in events:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item") or {}
        kind = item.get("type")
        if kind == "command_execution":
            command = str(item.get("command") or "")
            commands.append({"command": command, "exit_code": item.get("exit_code")})
            reason = command_escapes(command, folder)
            if reason:
                violations.append(f"{reason}: {command[:200]}")
        elif kind not in ("agent_message", "reasoning", "todo_list"):
            violations.append(f"tool outside the review folder's shell: {kind}")
    return {"commands": commands, "violations": violations}


class ReviewAgentError(RuntimeError):
    pass


class CodexReviewAgent:
    """`codex exec` as an agent in one folder; replays a finished call, never retries one."""

    backend = "codex_subscription_agent"

    def __init__(self, *, model: str, reasoning_effort: str = "high", timeout_seconds: float = 3600.0):
        self.client = CodexSubscriptionClient(model=model, reasoning_effort=reasoning_effort, timeout_seconds=timeout_seconds)
        self.model, self.reasoning_effort = model, reasoning_effort

    def command(self, folder: Path, call_dir: Path, schema_path: Path) -> list[str]:
        return [
            self.client.executable, "exec",
            "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
            "--sandbox", "read-only", "--color", "never", "--json",
            "--model", self.model,
            "--config", f'model_reasoning_effort="{self.reasoning_effort}"',
            "--output-schema", str(schema_path),
            "--output-last-message", str(call_dir / "response.json"),
            "--cd", str(folder),
            "-",
        ]

    def review(self, *, folder: Path, call_dir: Path, prompt: str, schema: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        from backend.pipeline.subscription_process import run

        self.client._verify_chatgpt_login()
        call_dir.mkdir(parents=True, exist_ok=True)
        schema_path = call_dir / "response-schema.json"
        schema_text = json.dumps(schema.get("schema", schema), ensure_ascii=False, sort_keys=True)
        if schema_path.exists() and schema_path.read_text(encoding="utf-8") != schema_text:
            raise ReviewAgentError("saved review schema drift")
        schema_path.write_text(schema_text, encoding="utf-8")
        try:
            completed = run(self.command(folder, call_dir, schema_path), input=prompt,
                            env=self.client.environment, timeout=self.client.timeout_seconds,
                            directory=call_dir, cwd=folder)
        except OSError as exc:
            raise CodexSubscriptionError(f"review agent transport failed: {exc}") from exc
        if completed.returncode != 0:
            raise CodexSubscriptionError(f"review agent failed (exit {completed.returncode}): {(completed.stderr or '')[-2000:]}")
        audit = audit_events(read_events(completed.stdout), folder)
        receipt = {
            "model": self.model,
            "backend": self.backend,
            "reasoning_effort": self.reasoning_effort,
            "prompt_sha256": sha256_text(prompt),
            "event_log": str(call_dir / "stdout.log"),
            "commands": len(audit["commands"]),
            "violations": audit["violations"],
        }
        if audit["violations"]:
            raise ReviewAgentError("review agent read outside its folder: " + "; ".join(audit["violations"]))
        try:
            result = json.loads((call_dir / "response.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CodexSubscriptionError(f"review agent returned no structured output: {exc}") from exc
        return result, receipt
