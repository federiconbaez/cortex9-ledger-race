"""Accounting invariants, evaluated with SQL on a read-only connection to
the ledger. Table and column names follow the default schema documented in
the README; other schemas need a mapping (not implemented in the MVP)."""
from __future__ import annotations

import sqlite3

CHECKS = {
    "I1_balance_equals_entries": """
        SELECT a.id, a.balance, COALESCE(SUM(e.amount), 0) AS s
        FROM accounts a LEFT JOIN entries e ON e.account = a.id
        GROUP BY a.id HAVING a.balance != s""",
    "I2_no_negative_balance": "SELECT id, balance FROM accounts WHERE balance < 0",
    "I3_one_entry_per_idempotency_key": """
        SELECT idem_key, kind, COUNT(*) FROM entries WHERE idem_key IS NOT NULL
        GROUP BY idem_key, kind HAVING COUNT(*) > 1""",
    "I4_one_entry_per_webhook_event": """
        SELECT event_id, COUNT(*) FROM entries WHERE event_id IS NOT NULL
        GROUP BY event_id HAVING COUNT(*) > 1""",
    "I5_transfers_balance_to_zero": """
        SELECT transfer_id, SUM(amount), COUNT(*) FROM entries WHERE transfer_id IS NOT NULL
        GROUP BY transfer_id HAVING SUM(amount) != 0 OR COUNT(*) != 2""",
    "I6_entry_sign_matches_kind": """
        SELECT id, account, kind, amount FROM entries
        WHERE (kind IN ('credit','webhook','transfer_in') AND amount <= 0)
           OR (kind IN ('debit','transfer_out') AND amount >= 0)""",
}


def check(db_path: str, accounts: list[str] | None = None) -> list[dict]:
    """Return one violation dict per offending row. If `accounts` is given,
    only rows touching those accounts are reported (so a shared ledger with
    unrelated traffic can be checked)."""
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    try:
        out = []
        for name, sql in CHECKS.items():
            for row in c.execute(sql):
                out.append({"invariant": name, "row": list(row)})
    finally:
        c.close()
    if accounts is None:
        return out
    acc = set(accounts)
    return [v for v in out if any(str(x) in acc or any(str(x).startswith(a) for a in acc) for x in v["row"])]
