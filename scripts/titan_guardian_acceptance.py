"""Deterministic Guardian closure acceptance contract."""
from governance_service import guardian_acceptance_check

def main(conn):
    result = guardian_acceptance_check(conn)
    if not result["ok"]:
        raise SystemExit("GUARDIAN_NOT_CLOSED: " + "; ".join(result["failures"]))
    print("GUARDIAN_ACCEPTANCE_PASS")
    return result
