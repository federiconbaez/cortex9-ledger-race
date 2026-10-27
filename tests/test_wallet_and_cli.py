import os
import tempfile
import time
import unittest

from demo.wallet import BUGS, ApiError, Wallet, serve_in_thread
from ledgerrace.cli import check, demo
from ledgerrace.client import Client
from ledgerrace.scenarios import burst


class WalletTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.w = Wallet(os.path.join(self.d.name, "l.sqlite"))
        self.w.create_account("a")
        self.w.create_account("b")

    def tearDown(self):
        self.d.cleanup()

    def test_idempotent_credit(self):
        r1 = self.w.credit({"account": "a", "amount": 5}, "k")
        r2 = self.w.credit({"account": "a", "amount": 5}, "k")
        self.assertEqual({k: v for k, v in r2.items() if not k.startswith("_")}, r1)
        self.assertEqual(self.w.balance("a")["balance"], 5)

    def test_overdraft_rejected(self):
        with self.assertRaises(ApiError) as e:
            self.w.debit({"account": "a", "amount": 1}, "d")
        self.assertEqual(e.exception.status, 409)

    def test_failed_transfer_rolls_back(self):
        self.w.credit({"account": "a", "amount": 10}, "s")
        with self.assertRaises(ApiError):
            self.w.transfer({"from": "a", "to": "nope", "amount": 4}, "t")
        self.assertEqual(self.w.balance("a")["balance"], 10)

    def test_webhook_signature_and_dedupe(self):
        raw = b'{"account": "a", "amount": 3, "event_id": "e1"}'
        with self.assertRaises(ApiError):
            self.w.webhook(raw, "bad")
        self.w.webhook(raw, self.w.sign(raw))
        self.assertTrue(self.w.webhook(raw, self.w.sign(raw))["duplicate"])
        self.assertEqual(self.w.balance("a")["balance"], 3)

    def test_unknown_bug_rejected(self):
        with self.assertRaises(ValueError):
            Wallet(os.path.join(self.d.name, "x.sqlite"), bugs=["nope"])


class BurstTest(unittest.TestCase):
    def test_threads_released_together(self):
        stamps = burst(8, lambda i: time.perf_counter())
        self.assertLess(max(stamps) - min(stamps), 0.05)


class CheckerTest(unittest.TestCase):
    def test_fixed_wallet_passes(self):
        r = demo([])
        self.assertEqual(r["verdict"], "PASS", r["findings"])
        self.assertEqual(r["transport_errors"], 0)

    def test_each_bug_fails(self):
        for b in BUGS:
            with self.subTest(bug=b):
                self.assertEqual(demo([b])["verdict"], "FAIL")

    def test_unreachable_api_is_error_not_pass(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "l.sqlite")
            Wallet(db)
            r = check("http://127.0.0.1:9", db, b"s")
            self.assertNotEqual(r["verdict"], "PASS")

    def test_check_against_running_server(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "l.sqlite")
            srv, url = serve_in_thread(Wallet(db, bugs=["webhook_replay"], secret=b"s3"))
            try:
                r = check(url, db, b"s3")
            finally:
                srv.shutdown()
                srv.server_close()
            self.assertIn("I4_one_entry_per_webhook_event", r["violated"])


if __name__ == "__main__":
    unittest.main()
