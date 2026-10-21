"""The store's short transaction boundary: actual DML, never global snapshots.

All mutating SQL in apply_plan and its helpers uses this cursor. RETURNING
records what SQL actually changed, including a mistaken WHERE clause. A final
comparison rejects missing, additional, or repeated writes before commit.
This module grants no content authority and calls no model or file reader.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import re
from typing import Any, Mapping


CONTRACT = "wang_cvp_sql_transaction_v2"
RETURNING = {
    "objects": "collection, object_id",
    "object_versions": "collection, object_id, revision",
    "edges": "edge_collection, edge_id",
    "change_operations": "change_set_id, operation_index",
    "review_events": "review_event_id",
    "change_sets": "change_set_id",
    "pipeline_runs": "run_id",
}
_DML = re.compile(r"^\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+wang_knowledge\.(\w+)\b", re.I)


@dataclass(frozen=True)
class TransactionValidation:
    """Pins to records actually read and the exact reviewed SQL group state.

    The group row holds immutable package/approval/source/backup identities.
    Passing this object does not replace the existing package validators.
    """

    run_id: str
    reviewed_sha256: str
    read_pins: Mapping[tuple[str, str], tuple[int, str, bool] | None] = field(default_factory=dict)

    def __post_init__(self):
        if not self.run_id or not re.fullmatch(r"[0-9a-f]{64}", self.reviewed_sha256):
            raise ValueError("exact reviewed group identity required")


class WriteTrackingCursor:
    def __init__(self, cursor):
        self.cursor = cursor
        self.writes = {table: Counter() for table in RETURNING}
        self._returned = None

    def execute(self, statement, parameters=()):
        if not isinstance(statement, str):
            raise ValueError("transaction store SQL must be a fixed string")
        self._returned = None
        match = _DML.match(statement)
        if match:
            table = match[1].lower()
            if table not in RETURNING or ";" in statement.rstrip().rstrip(";"):
                raise ValueError("undeclared transaction DML")
            # Helpers do not get to choose a weaker RETURNING projection.
            if re.search(r"\bRETURNING\b", statement, re.I):
                raise ValueError("store cursor owns the complete RETURNING identity")
            statement = statement.rstrip().rstrip(";") + " RETURNING " + RETURNING[table]
            self.cursor.execute(statement, parameters)
            self._returned = list(self.cursor.fetchall())
            self.writes[table].update(tuple(row) for row in self._returned)
        else:
            if not re.match(r"^\s*(SELECT|SET)\b", statement, re.I):
                raise ValueError("undeclared transaction statement")
            self.cursor.execute(statement, parameters)
        return self

    def fetchone(self):
        if self._returned is not None:
            return self._returned.pop(0) if self._returned else None
        return self.cursor.fetchone()

    def fetchall(self):
        if self._returned is not None:
            result, self._returned = self._returned, []
            return result
        return self.cursor.fetchall()

    def verify(self, *, plan, side_operations, run_id, affected_runs=()):
        operations = [*plan.operations, *side_operations]
        expected = {
            "objects": Counter((op.collection, op.object_id) for op in operations),
            "object_versions": Counter((op.collection, op.object_id, op.after_revision) for op in operations),
            "change_operations": Counter((plan.change_set_id, index) for index in range(len(operations))),
            "edges": Counter(),
            "review_events": Counter((event.review_event_id,) for event in plan.review_events),
            "change_sets": Counter({(plan.change_set_id,): 2}),
            "pipeline_runs": Counter((ident,) for ident in (run_id,*affected_runs)),
        }
        from .postgres_store import EDGE_COLLECTIONS
        expected["edges"].update((op.collection, op.object_id) for op in operations if op.collection in EDGE_COLLECTIONS)
        if self.writes != expected:
            differences = {table: {"missing": list((expected[table] - self.writes[table]).elements()),
                                   "extra": list((self.writes[table] - expected[table]).elements())}
                           for table in expected if expected[table] != self.writes[table]}
            raise ValueError("actual transaction write set differs: " + str(differences))

    def verify_no_content_write(self, run_id):
        expected = {table:Counter() for table in RETURNING}
        expected['pipeline_runs'][(run_id,)] = 1
        if self.writes != expected:
            raise ValueError('observation-only transaction changed content or history')


class SideEffectPlanningCursor:
    """Execute dependency reads only, before the first business write.

    The existing deterministic invalidation calculation is shared, not copied.
    Its DML is suppressed in this declaration pass. The actual pass must produce
    exactly the same operations and before/after hashes.
    """
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, statement, parameters=()):
        if _DML.match(statement):
            return self
        if not re.match(r"^\s*SELECT\b", statement, re.I):
            raise ValueError("side-effect declaration must be read-only")
        self.cursor.execute(statement, parameters)
        return self

    def fetchall(self):
        return self.cursor.fetchall()


def reject_untracked_triggers(cursor):
    # RETURNING does not include another row changed by a trigger. No user
    # trigger is part of this transaction contract. Foreign-key constraints are
    # internal triggers and remain enforced. Never silently assume they are safe.
    cursor.execute("""SELECT c.relname,t.tgname FROM pg_trigger t
                      JOIN pg_class c ON c.oid=t.tgrelid
                      JOIN pg_namespace n ON n.oid=c.relnamespace
                      WHERE n.nspname='wang_knowledge' AND NOT t.tgisinternal
                        AND t.tgenabled <> 'D' ORDER BY c.relname,t.tgname""")
    triggers = cursor.fetchall()
    if triggers:
        raise ValueError("untracked database triggers outside transaction contract: " + str(triggers))
