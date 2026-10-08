"""Fail closed at retired #357 execution entry points, before any side effect.

Evidence readers and pure content validators remain usable by explicit historical
admission. This is not a compatibility switch: no environment flag enables the
old runtime in the #357 worktree.
"""
def legacy_runtime_is_retired():
    # The retirement is a code contract, not a working-directory or environment
    # switch. Moving/merging the checkout must never re-enable the old runner.
    return True


def reject_legacy_execution():
    if legacy_runtime_is_retired():
        raise RuntimeError(
            "#357 snapshot/proof-chain runtime is retired. Use cvp_sql_flow with "
            "an explicitly admitted SQL campaign; no legacy fallback is allowed."
        )
