"""Measure ledger-race against the bundled demo wallet.

    python -m lab.measure              # 8 seeded bugs x 5 runs, fixed wallet x 20 runs
    python -m lab.measure --bug-runs 1 --clean-runs 5

A bug counts as detected in a run only if the finding that names it shows
up (EXPECTED below), not just any FAIL. Criteria fixed before running:
>= 7/8 bugs detected, 0 false positives on the fixed wallet over 20 runs."""
from __future__ import annotations

import argparse
import json
import sys

from demo.wallet import BUGS
from ledgerrace.cli import demo

EXPECTED = {
    "debit_no_lock": {"E1_no_overdraft_accepted", "I1_balance_equals_entries", "I2_no_negative_balance"},
    "idem_ignored": {"I3_one_entry_per_idempotency_key"},
    "idem_race": {"I3_one_entry_per_idempotency_key"},
    "webhook_replay": {"I4_one_entry_per_webhook_event"},
    "webhook_unsigned": {"E3_forged_webhook_rejected", "E4_forged_webhook_no_effect"},
    "transfer_partial": {"I5_transfers_balance_to_zero", "E5_failed_transfer_rolled_back"},
    "balance_drift": {"I1_balance_equals_entries", "E6_concurrent_credits_add_up"},
    "negative_amount": {"E7_non_positive_rejected", "I6_entry_sign_matches_kind"},
}


def run_lab(bug_runs=5, clean_runs=20, verbose=False):
    bugs = {}
    for b in BUGS:
        hits, extra = 0, set()
        for _ in range(bug_runs):
            r = demo([b])
            v = set(r["violated"])
            if v & EXPECTED[b]:
                hits += 1
            extra |= v - EXPECTED[b]
        bugs[b] = {"detected_runs": hits, "runs": bug_runs, "other_findings": sorted(extra)}
        if verbose:
            print(f"bug {b:18} detected {hits}/{bug_runs}  other findings: {sorted(extra)}", flush=True)
    clean = []
    for i in range(clean_runs):
        r = demo([])
        clean.append({"verdict": r["verdict"], "violated": r["violated"], "transport_errors": r["transport_errors"]})
    fp = sum(c["verdict"] != "PASS" for c in clean)
    detected_always = sum(v["detected_runs"] == v["runs"] for v in bugs.values())
    detected_any = sum(v["detected_runs"] > 0 for v in bugs.values())
    res = {"bugs": bugs, "detected_all_runs": detected_always, "detected_any_run": detected_any,
           "total_bugs": len(BUGS), "clean_runs": clean_runs, "false_positives": fp,
           "clean_non_pass": [c for c in clean if c["verdict"] != "PASS"],
           "criterion_met": detected_always >= 7 and fp == 0}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bug-runs", type=int, default=5)
    ap.add_argument("--clean-runs", type=int, default=20)
    ap.add_argument("--window", type=float, help="override the demo race window in seconds (default 0.005)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.window is not None:
        import demo.wallet
        demo.wallet.WINDOW = a.window
    res = run_lab(a.bug_runs, a.clean_runs, verbose=not a.json)
    detected_always, detected_any, fp = res["detected_all_runs"], res["detected_any_run"], res["false_positives"]
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"bugs detected in every run: {detected_always}/{len(BUGS)} (in at least one run: {detected_any}/{len(BUGS)}); "
              f"false positives on fixed wallet: {fp}/{a.clean_runs}; criterion met: {res['criterion_met']}")
    return 0 if res["criterion_met"] else 1


if __name__ == "__main__":
    sys.exit(main())
