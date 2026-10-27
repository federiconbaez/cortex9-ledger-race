"""ledger-race: run the scenarios against a wallet API you are authorized to
test, then check the accounting invariants on its ledger.

    python -m ledgerrace check --url http://127.0.0.1:8080 --db ledger.sqlite --secret-env WEBHOOK_SECRET
    python -m ledgerrace demo --bug idem_race          # self-contained demo against the bundled wallet
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

from . import invariants
from .client import Client
from .scenarios import Run


def check(url, db, secret: bytes, prefix=None) -> dict:
    client = Client(url, secret)
    run = Run(client, prefix)
    run.all()
    viol = invariants.check(db, accounts=[run.prefix])
    findings = [{"kind": "invariant", **v} for v in viol] + \
               [{"kind": "expectation", **e} for e in run.expectations]
    verdict = "FAIL" if findings else "PASS"
    if client.transport_errors and not findings:
        verdict = "ERROR"  # some requests never got an answer: cannot claim PASS
    return {"verdict": verdict, "prefix": run.prefix, "transport_errors": client.transport_errors,
            "findings": findings,
            "violated": sorted({f.get("invariant") or f.get("expectation") for f in findings})}


def demo(bugs, secret=b"lab-secret") -> dict:
    from demo.wallet import Wallet, serve_in_thread
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "ledger.sqlite")
        srv, url = serve_in_thread(Wallet(db, bugs=bugs, secret=secret))
        try:
            return check(url, db, secret)
        finally:
            srv.shutdown()
            srv.server_close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ledger-race")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--url", required=True)
    c.add_argument("--db", required=True, help="SQLite ledger, opened read-only")
    c.add_argument("--secret-env", default="WEBHOOK_SECRET", help="env var holding the webhook HMAC secret")
    c.add_argument("--json", action="store_true")
    d = sub.add_parser("demo")
    d.add_argument("--bug", action="append", default=[])
    d.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "check":
        secret = os.environ.get(a.secret_env, "").encode()
        if not secret:
            print(f"set {a.secret_env}", file=sys.stderr)
            return 2
        res = check(a.url, a.db, secret)
    else:
        res = demo(a.bug)
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"{res['verdict']} ({res['prefix']}, transport errors: {res['transport_errors']})")
        for f in res["findings"]:
            print(f"  {f.get('invariant') or f.get('expectation')}: {f.get('row') or f.get('detail')}")
    return {"PASS": 0, "FAIL": 1, "ERROR": 3}[res["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
