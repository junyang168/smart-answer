"""No-write retention of an already approved, exact-version CVP relation.

The incoming package and its model provenance stay immutable. This is a SQL
plan derivation, not permission to revise/merge/retire a relation or its reasons.
"""
from dataclasses import replace

from .postgres_store import (
    ChangeSetConflict, operation_fingerprint_rows,
    record_content_sha, review_event_fingerprint_rows, sha256_json,
)

POLICY = "cvp_exact_existing_relation_retention_v1"
NON_SEMANTIC = {"viewpoint_relation_id", "revision", "reason", "review_provenance", "review_status"}
# Different approval origins are not different relation meanings. Both must be
# approved independently; retention never replaces the old status/provenance.
APPROVED = {"system_approved", "approved", "human_approved"}


def derive(cursor, plan, read_pins):
    """Only indexed current edges; compare every non-provenance field exactly."""
    pins = dict(read_pins)
    retained = []
    removed = set()
    for op in plan.operations:
        if op.collection != "viewpoint_relations" or op.operation != "create":
            continue
        p = op.payload
        endpoint = (p["source_viewpoint_id"], p["target_viewpoint_id"], p["relation_type"])
        rows = cursor.execute(
            """SELECT edge_id,revision,payload FROM wang_knowledge.edges
               WHERE edge_collection='viewpoint_relations' AND from_id=%s
                 AND to_id=%s AND relation_type=%s AND retired_at IS NULL
               ORDER BY edge_id FOR UPDATE""", endpoint).fetchall()
        foreign = [r for r in rows if r[0] != op.object_id]
        if not foreign:
            continue
        if len(rows) != 1 or op.before_revision is not None:
            raise ChangeSetConflict("ambiguous existing CVP relation retention")
        oid, edge_revision, edge_payload = foreign[0]
        row = cursor.execute(
            """SELECT revision,content_sha256,payload,retired_at,review_status,visibility
               FROM wang_knowledge.objects WHERE collection='viewpoint_relations'
                 AND object_id=%s FOR UPDATE""", (oid,)).fetchone()
        if (not row or row[3] is not None or row[0] != edge_revision
                or row[2] != edge_payload or record_content_sha(row[2]) != row[1]
                or row[4] != row[2].get("review_status")
                or row[5] != row[2].get("visibility")):
            raise ChangeSetConflict("existing CVP relation edge/object proof drift")
        current = row[2]
        history = cursor.execute("""SELECT v.change_set_id,v.content_sha256,v.payload,
                   o.after_sha256,o.after_revision,c.status
            FROM wang_knowledge.object_versions v
            JOIN wang_knowledge.change_sets c ON c.change_set_id=v.change_set_id
            JOIN wang_knowledge.change_operations o ON o.change_set_id=v.change_set_id
              AND o.collection=v.collection AND o.object_id=v.object_id
              AND o.after_revision=v.revision
            WHERE v.collection='viewpoint_relations' AND v.object_id=%s AND v.revision=%s""",
            (oid,row[0])).fetchall()
        if (len(history) != 1 or history[0][1:] != (row[1],current,row[1],row[0],'applied')):
            raise ChangeSetConflict("retained relation lacks exact committed current history")
        if (current.get("schema_version") != "wang_viewpoint_relation_v1"
                or current.get("effective_state") != "active"
                or current.get("review_status") not in APPROVED
                or p.get("review_status") not in APPROVED
                or not current.get("review_provenance")
                or not p.get("review_provenance")
                or {k:v for k,v in current.items() if k not in NON_SEMANTIC}
                   != {k:v for k,v in p.items() if k not in NON_SEMANTIC}):
            raise ChangeSetConflict("existing CVP relation is held or differs in semantics/version")
        key = ("viewpoint_relations", oid)
        pin = (int(row[0]), str(row[1]), False)
        if key in pins and pins[key] != pin:
            raise ChangeSetConflict("retained CVP relation read pin drift")
        for side in ("source", "target"):
            vid = p[side+"_viewpoint_id"]
            rid = p["validated_"+side+"_viewpoint_revision_id"]
            head = cursor.execute("""SELECT revision,content_sha256,payload,retired_at
                FROM wang_knowledge.objects WHERE collection='canonical_viewpoints'
                  AND object_id=%s FOR UPDATE""", (vid,)).fetchone()
            revision = cursor.execute("""SELECT revision,content_sha256,payload,retired_at
                FROM wang_knowledge.objects WHERE collection='viewpoint_revisions'
                  AND object_id=%s FOR UPDATE""", (rid,)).fetchone()
            for collection, ident, fact in (("canonical_viewpoints",vid,head),
                                            ("viewpoint_revisions",rid,revision)):
                if (not fact or fact[3] is not None or record_content_sha(fact[2]) != fact[1]
                        or pins.get((collection,ident)) != (int(fact[0]),str(fact[1]),False)
                        or fact[2].get("review_status") not in {"system_approved","approved","human_approved"}):
                    raise ChangeSetConflict("retained relation endpoint lacks current approved read proof")
            if head[2].get("current_revision_id") != rid or head[2].get('identity_status') != 'active':
                raise ChangeSetConflict("retained relation endpoint current revision changed")
        # A proposed relation with other consumers cannot simply disappear.
        for other in plan.operations:
            if other is not op and op.object_id in _strings(other.payload):
                raise ChangeSetConflict("new relation ID has a consumer; cannot retain another ID")
        if any(op.object_id in _strings(e.as_dict() if hasattr(e, "as_dict") else vars(e))
               for e in plan.review_events):
            raise ChangeSetConflict("new relation ID has a review event")
        if pins.get((op.collection, op.object_id), "missing") is not None:
            raise ChangeSetConflict("new relation absence proof missing or drifted")
        pins[key] = pin
        removed.add(op.object_id)
        retained.append({"incoming_id":op.object_id, "incoming_payload_sha256":op.after_sha256,
                         "retained_id":oid, "current_pin":list(pin),
                         "current_history_change_set_id":history[0][0]})
    if not retained:
        return plan, pins, None
    operations = tuple(op for op in plan.operations
                       if not (op.collection == "viewpoint_relations" and op.object_id in removed))
    receipt = {"policy":POLICY, "original_plan_sha256":sha256_json(plan.as_dict()),
               "retained":retained, "relation_writes":0}
    fingerprint = sha256_json({"planner_schema":POLICY, "source_kind":plan.source_kind,
        "source_sha256":plan.source_sha256, "package_id":plan.package_id,
        "operations":operation_fingerprint_rows(operations),
        "review_events":review_event_fingerprint_rows(plan.review_events), "retention":receipt})
    return replace(plan, operations=operations, unchanged=plan.unchanged+len(retained),
                   change_set_id="KCS-"+fingerprint[:20], fingerprint_sha256=fingerprint), pins, receipt


def _strings(value):
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return set().union(*(_strings(v) for v in value.values())) if value else set()
    if isinstance(value, (list, tuple)):
        return set().union(*(_strings(v) for v in value)) if value else set()
    return set()
