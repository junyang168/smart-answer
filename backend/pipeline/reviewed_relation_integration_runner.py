"""Build an auditable candidate snapshot from reviewed cross-source relations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.pipeline.reviewed_relation_integration import (
    build_reviewed_relation_integration,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge", required=True, type=Path)
    parser.add_argument("--reviewed-relations", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    knowledge = json.loads(args.knowledge.read_text(encoding="utf-8"))
    reviewed = json.loads(args.reviewed_relations.read_text(encoding="utf-8"))
    result = build_reviewed_relation_integration(reviewed, knowledge)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "report": args.output_dir / "integration-report.json",
        "increment": args.output_dir / "incremental-package.json",
        "snapshot": args.output_dir / "candidate-snapshot.json",
    }
    payloads = {
        "report": result,
        "increment": result["incremental_package"],
        "snapshot": result["candidate_snapshot"],
    }
    for name, path in paths.items():
        path.write_text(
            json.dumps(payloads[name], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "status": result["status"],
                **result["summary"],
                "outputs": {name: str(path) for name, path in paths.items()},
            },
            ensure_ascii=False,
        )
    )
    return 1 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
