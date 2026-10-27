"""Scenarios: synchronized bursts (threads released by one Barrier) and
retries that exercise the money paths. Each scenario uses fresh account ids
under a run prefix, so runs never share state."""
from __future__ import annotations

import threading
import uuid


def burst(n, fn):
    """Run fn(i) in n threads released at the same instant; return results."""
    barrier = threading.Barrier(n)
    out = [None] * n

    def worker(i):
        barrier.wait()
        out[i] = fn(i)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return out


class Run:
    def __init__(self, client, prefix=None):
        self.c = client
        self.prefix = prefix or f"lr-{uuid.uuid4().hex[:8]}"
        self.expectations: list[dict] = []
        self.accounts: list[str] = []

    def acct(self, name):
        a = f"{self.prefix}-{name}"
        self.c.create(a)
        self.accounts.append(a)
        return a

    def expect(self, name, ok, detail):
        if not ok:
            self.expectations.append({"expectation": name, "detail": detail})

    # S1: 20 concurrent debits of 10 against a balance of 100
    def s1_concurrent_debits(self):
        a = self.acct("s1")
        self.c.credit(a, 100, key=f"{a}-seed")
        res = burst(20, lambda i: self.c.debit(a, 10, key=f"{a}-d{i}"))
        ok = sum(1 for st, _ in res if st == 200)
        self.expect("E1_no_overdraft_accepted", ok <= 10, f"{ok} of 20 debits of 10 accepted on balance 100")

    # S2: same Idempotency-Key retried 3 times sequentially
    def s2_sequential_retry(self):
        a = self.acct("s2")
        k = f"{a}-k"
        rs = [self.c.credit(a, 25, key=k) for _ in range(3)]
        self.expect("E2_retry_same_response", all(r == rs[0] for r in rs), f"responses differ: {rs}")

    # S3: same key sent 10 times concurrently
    def s3_concurrent_retry(self):
        a = self.acct("s3")
        k = f"{a}-k"
        burst(10, lambda i: self.c.credit(a, 7, key=k))

    # S4: one signed webhook event delivered 3 times (provider retry)
    def s4_webhook_replay(self):
        a = self.acct("s4")
        ev = {"event_id": f"{a}-ev1", "account": a, "amount": 40}
        for _ in range(3):
            self.c.webhook(ev)

    # S5: webhook with a wrong signature
    def s5_forged_webhook(self):
        a = self.acct("s5")
        ev = {"event_id": f"{a}-forged", "account": a, "amount": 1000}
        st, _ = self.c.webhook(ev, signature="00" * 32)
        self.expect("E3_forged_webhook_rejected", st in (400, 401, 403),
                    f"forged webhook answered HTTP {st}")
        st2, body = self.c.balance(a)
        self.expect("E4_forged_webhook_no_effect", st2 == 200 and body.get("balance") == 0,
                    f"balance after forged webhook: {body}")

    # S6: transfer to a destination that does not exist (must roll back)
    def s6_failed_transfer(self):
        a = self.acct("s6")
        self.c.credit(a, 50, key=f"{a}-seed")
        self.c.transfer(a, f"{a}-missing", 30, key=f"{a}-t1")
        st, body = self.c.balance(a)
        self.expect("E5_failed_transfer_rolled_back", body.get("balance") == 50,
                    f"source balance after failed transfer: {body}")

    # S7: 30 concurrent credits with distinct keys
    def s7_concurrent_credits(self):
        a = self.acct("s7")
        burst(30, lambda i: self.c.credit(a, 3, key=f"{a}-c{i}"))
        st, body = self.c.balance(a)
        self.expect("E6_concurrent_credits_add_up", body.get("balance") == 90,
                    f"balance after 30 credits of 3: {body}")

    # S8: non-positive amounts must be rejected
    def s8_non_positive(self):
        a = self.acct("s8")
        st, _ = self.c.debit(a, -50, key=f"{a}-neg")
        st0, _ = self.c.credit(a, 0, key=f"{a}-zero")
        self.expect("E7_non_positive_rejected", st >= 400 and st0 >= 400,
                    f"debit(-50) -> HTTP {st}, credit(0) -> HTTP {st0}")

    def all(self):
        for name in sorted(n for n in dir(self) if n[:1] == "s" and n[1:2].isdigit()):
            getattr(self, name)()
