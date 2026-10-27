import os
import sqlite3
import tempfile
import unittest

from demo.wallet import SCHEMA
from ledgerrace import invariants


class InvariantTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.d.name, "l.sqlite")
        c = sqlite3.connect(self.db)
        c.executescript(SCHEMA)
        c.execute("INSERT INTO accounts VALUES('p-a', 70)")
        c.execute("INSERT INTO accounts VALUES('p-b', 30)")
        c.executemany("INSERT INTO entries(account, amount, kind, idem_key, event_id, transfer_id) VALUES(?,?,?,?,?,?)", [
            ("p-a", 100, "credit", "p-k1", None, None),
            ("p-a", -30, "transfer_out", "p-t", None, "p-t"),
            ("p-b", 30, "transfer_in", "p-t", None, "p-t"),
        ])
        c.commit()
        self.c = c

    def tearDown(self):
        self.c.close()
        self.d.cleanup()

    def names(self, **kw):
        return {v["invariant"] for v in invariants.check(self.db, **kw)}

    def test_clean_ledger(self):
        self.assertEqual(self.names(), set())

    def test_balance_mismatch(self):
        self.c.execute("UPDATE accounts SET balance=71 WHERE id='p-a'")
        self.c.commit()
        self.assertEqual(self.names(), {"I1_balance_equals_entries"})

    def test_negative_balance(self):
        self.c.execute("INSERT INTO accounts VALUES('p-c', -5)")
        self.c.execute("INSERT INTO entries(account, amount, kind) VALUES('p-c', -5, 'debit')")
        self.c.commit()
        self.assertEqual(self.names(), {"I2_no_negative_balance"})

    def test_duplicate_key(self):
        self.c.execute("INSERT INTO entries(account, amount, kind, idem_key) VALUES('p-a', 100, 'credit', 'p-k1')")
        self.c.execute("UPDATE accounts SET balance=170 WHERE id='p-a'")
        self.c.commit()
        self.assertEqual(self.names(), {"I3_one_entry_per_idempotency_key"})

    def test_duplicate_event(self):
        self.c.executemany("INSERT INTO entries(account, amount, kind, event_id) VALUES('p-b', 5, 'webhook', 'p-e')", [(), ()])
        self.c.execute("UPDATE accounts SET balance=40 WHERE id='p-b'")
        self.c.commit()
        self.assertEqual(self.names(), {"I4_one_entry_per_webhook_event"})

    def test_half_transfer(self):
        self.c.execute("DELETE FROM entries WHERE kind='transfer_in'")
        self.c.execute("UPDATE accounts SET balance=0 WHERE id='p-b'")
        self.c.commit()
        self.assertEqual(self.names(), {"I5_transfers_balance_to_zero"})

    def test_wrong_sign(self):
        self.c.execute("INSERT INTO entries(account, amount, kind) VALUES('p-b', 50, 'debit')")
        self.c.execute("UPDATE accounts SET balance=80 WHERE id='p-b'")
        self.c.commit()
        self.assertEqual(self.names(), {"I6_entry_sign_matches_kind"})

    def test_filter_by_prefix(self):
        self.c.execute("UPDATE accounts SET balance=71 WHERE id='p-a'")
        self.c.commit()
        self.assertEqual(self.names(accounts=["other"]), set())
        self.assertEqual(self.names(accounts=["p"]), {"I1_balance_equals_entries"})

    def test_opens_read_only(self):
        c = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        with self.assertRaises(sqlite3.OperationalError):
            c.execute("INSERT INTO accounts VALUES('x', 0)")
        c.close()


if __name__ == "__main__":
    unittest.main()
