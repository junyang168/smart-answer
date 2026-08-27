"""Queue Route work for already-committed viewpoints under the current policy.

Route jobs are enqueued once, by the CVP batch that committed the viewpoints,
and an applied batch is never re-run.  So when the route policy changes -- a
prompt edit, a new deterministic rule -- the viewpoints already in the Registry
have no way back to the route stage, and the improvement only ever reaches
whatever is committed next.  #220 recorded the same hole from the other side: a
revised viewpoint voids its un-executed route job with no operation to enqueue
it again at the current version.

Nothing here is fabricated.  The receipt is the one the CVP batch wrote, and the
job's idempotency key already includes the policy fingerprint, so re-enqueuing
under an unchanged policy is a no-op on the same job id rather than a duplicate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from backend.api.canonical_repository.viewpoint_batch_resolution import (
    CvpBatchReadbackReceipt,
    build_route_resolution_job,
)
from backend.api.canonical_repository.viewpoint_route_queue import (
    FileRouteResolutionQueue,
)
from backend.pipeline.viewpoint_route_policy import (
    DEFAULT_ROUTE_POLICY_PATH,
    load_route_policy,
    route_policy_fingerprint,
    route_policy_prompt_sha256s,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--receipt",
        type=Path,
        required=True,
        help="cvp-readback-receipt.json written by the batch that committed them",
    )
    parser.add_argument(
        "--packet",
        type=Path,
        required=True,
        help="the scope packet the route worker will be given",
    )
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument("--route-policy", type=Path, default=DEFAULT_ROUTE_POLICY_PATH)
    args = parser.parse_args()

    receipt = CvpBatchReadbackReceipt.model_validate(
        json.loads(args.receipt.read_text(encoding="utf-8"))
    )
    if receipt.readback_status != "verified":
        raise SystemExit("route work may only be queued for a verified readback")
    packet = json.loads(args.packet.read_text(encoding="utf-8"))
    policy = load_route_policy(args.route_policy)
    job = build_route_resolution_job(
        receipt=receipt,
        evidence_scope_sha256=str(packet["packet_sha256"]),
        route_policy_fingerprint_sha256=route_policy_fingerprint(
            policy,
            prompt_sha256s=route_policy_prompt_sha256s(policy, prompt_dir=PROMPT_DIR),
        ),
    )
    FileRouteResolutionQueue(args.queue_dir).enqueue(job)
    print(
        json.dumps(
            {
                "job_id": job.job_id,
                "viewpoint_count": len(job.logical_viewpoint_ids),
                "route_policy_fingerprint_sha256": job.route_policy_fingerprint_sha256,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
