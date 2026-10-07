"""Audit retained reviewed members as well as newly written group members."""
from pathlib import Path

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_resolution_runtime import read_artifact


def group_audit_targets(plan_path):
    plan_path=Path(plan_path)
    plan=read_artifact(plan_path)
    package=read_artifact(plan_path.parent/'cvp-change-package.json')
    if plan.get('source_sha256')!=sha256_json(package):
        raise ValueError('full-group audit package differs from actual applied plan')
    links=package.get('viewpoint_claim_links',[])
    if len(links)!=len({l['viewpoint_claim_link_id'] for l in links}):
        raise ValueError('full-group audit duplicate member identity')
    targets={l['viewpoint_id'] for l in links if l['effective_state']=='active'}
    written={o['payload']['viewpoint_id'] for o in plan['operations'] if o['collection']=='viewpoint_claim_links'
             and o['operation']!='retire' and o['payload']['effective_state']=='active'}
    if not targets or written-targets:
        raise ValueError('full-group audit omitted written or retained member targets')
    # This receipt is derived from actual SQL head readback, including each
    # newly reviewed target decision even when its existing link was reused.
    receipt=read_artifact(plan_path.parent/'cvp-readback-receipt.json')
    if (receipt.get('readback_status')!='verified'
            or receipt.get('cvp_changeset_id')!=plan['change_set_id']
            or receipt.get('cvp_changeset_sha256')!=plan['fingerprint_sha256']
            or receipt.get('artifact_sha256')!=sha256_json({k:v for k,v in receipt.items() if k!='artifact_sha256'})
            or targets-{r['viewpoint_id'] for r in receipt['committed_viewpoint_revisions']}):
        raise ValueError('full-group retained target has no exact current-revision readback')
    return sorted(targets)
