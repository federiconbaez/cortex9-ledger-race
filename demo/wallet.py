"""Demo wallet API used as the test target. NOT a real wallet.

The correct implementation does every balance change inside one SQLite
`BEGIN IMMEDIATE` transaction (writers are serialized), deduplicates
Idempotency-Key and webhook event ids inside that transaction, verifies the
webhook HMAC with a constant-time compare and rejects non-positive amounts.

Each name in BUGS turns on one seeded defect. Race windows of the seeded
defects are widened with a short sleep so they reproduce on demand; real
races are usually narrower."""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BUGS = (
    "debit_no_lock",      # check-then-act outside the transaction: overdraft / lost update
    "idem_ignored",       # Idempotency-Key not looked at: every retry books again
    "idem_race",          # key looked up outside the transaction: concurrent retries double-book
    "webhook_replay",     # signed webhook event id not deduplicated
    "webhook_unsigned",   # webhook signature not verified
    "transfer_partial",   # transfer commits the debit leg before the credit leg can fail
    "balance_drift",      # balance cache updated read-modify-write outside the transaction
    "negative_amount",    # debit accepts amount <= 0 (a negative debit mints money)
)
WINDOW = 0.005

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS entries(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account TEXT NOT NULL,
  amount INTEGER NOT NULL,
  kind TEXT NOT NULL,           -- credit | debit | transfer_out | transfer_in | webhook
  idem_key TEXT,
  event_id TEXT,
  transfer_id TEXT
);
CREATE TABLE IF NOT EXISTS idem(key TEXT PRIMARY KEY, response TEXT NOT NULL);
"""


class ApiError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status


class Wallet:
    def __init__(self, db_path: str, bugs=(), secret: bytes = b"lab-secret"):
        unknown = set(bugs) - set(BUGS)
        if unknown:
            raise ValueError(f"unknown bugs: {unknown}")
        self.db_path, self.bugs, self.secret = db_path, set(bugs), secret
        c = self._conn()
        c.executescript(SCHEMA)
        c.close()

    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=30, isolation_level=None, check_same_thread=False)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    # -- helpers -------------------------------------------------------
    @staticmethod
    def _amount(body):
        a = body.get("amount")
        if not isinstance(a, int):
            raise ApiError(400, "amount must be an integer")
        return a

    def _account_exists(self, c, acc):
        return c.execute("SELECT 1 FROM accounts WHERE id=?", (acc,)).fetchone() is not None

    def _idem_get(self, c, key):
        row = c.execute("SELECT response FROM idem WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def _idem_put(self, c, key, resp):
        if key and "idem_ignored" not in self.bugs:
            c.execute("INSERT OR REPLACE INTO idem(key, response) VALUES(?,?)", (key, json.dumps(resp)))

    def _book(self, c, acc, amount, kind, key=None, event=None, transfer=None):
        c.execute("INSERT INTO entries(account, amount, kind, idem_key, event_id, transfer_id) VALUES(?,?,?,?,?,?)",
                  (acc, amount, kind, key, event, transfer))
        if "balance_drift" in self.bugs and kind == "credit":
            return  # balance updated separately, see credit()
        c.execute("UPDATE accounts SET balance = balance + ? WHERE id=?", (amount, acc))

    def _with_idem(self, key, fn):
        """Run fn(conn) in one IMMEDIATE transaction, deduplicated by key."""
        c = self._conn()
        try:
            if key and "idem_race" in self.bugs:
                prior = self._idem_get(c, key)      # BUG: looked up outside the transaction
                if prior is not None:
                    return dict(prior, _replayed=True)
                time.sleep(WINDOW)
                c.execute("BEGIN IMMEDIATE")
            else:
                c.execute("BEGIN IMMEDIATE")
                if key and "idem_ignored" not in self.bugs:
                    prior = self._idem_get(c, key)
                    if prior is not None:
                        c.execute("COMMIT")
                        return dict(prior, _replayed=True)
            try:
                resp = fn(c)
                self._idem_put(c, key, resp)
                c.execute("COMMIT")
                return resp
            except Exception:
                c.execute("ROLLBACK")
                raise
        finally:
            c.close()

    # -- operations ----------------------------------------------------
    def create_account(self, acc):
        c = self._conn()
        try:
            c.execute("INSERT OR IGNORE INTO accounts(id, balance) VALUES(?, 0)", (acc,))
        finally:
            c.close()
        return {"account": acc}

    def balance(self, acc):
        c = self._conn()
        try:
            row = c.execute("SELECT balance FROM accounts WHERE id=?", (acc,)).fetchone()
        finally:
            c.close()
        if not row:
            raise ApiError(404, "no such account")
        return {"account": acc, "balance": row[0]}

    def credit(self, body, key):
        acc, amount = body.get("account"), self._amount(body)
        if amount <= 0:
            raise ApiError(400, "amount must be positive")

        def op(c):
            if not self._account_exists(c, acc):
                raise ApiError(404, "no such account")
            self._book(c, acc, amount, "credit", key=key)
            return {"ok": True, "account": acc, "amount": amount}

        resp = self._with_idem(key, op)
        if "balance_drift" in self.bugs and resp.get("ok") and not resp.get("_replayed"):
            c = self._conn()   # BUG: read-modify-write of the cached balance, no transaction
            try:
                bal = c.execute("SELECT balance FROM accounts WHERE id=?", (acc,)).fetchone()[0]
                time.sleep(WINDOW)
                c.execute("UPDATE accounts SET balance=? WHERE id=?", (bal + amount, acc))
            finally:
                c.close()
        return resp

    def debit(self, body, key):
        acc, amount = body.get("account"), self._amount(body)
        if amount <= 0 and "negative_amount" not in self.bugs:
            raise ApiError(400, "amount must be positive")
        if "debit_no_lock" in self.bugs:
            c = self._conn()   # BUG: check, wait, then act, all in autocommit
            try:
                row = c.execute("SELECT balance FROM accounts WHERE id=?", (acc,)).fetchone()
                if not row:
                    raise ApiError(404, "no such account")
                if row[0] < amount:
                    raise ApiError(409, "insufficient funds")
                time.sleep(WINDOW)
                c.execute("INSERT INTO entries(account, amount, kind, idem_key) VALUES(?,?,?,?)",
                          (acc, -amount, "debit", key))
                c.execute("UPDATE accounts SET balance=? WHERE id=?", (row[0] - amount, acc))
                return {"ok": True, "account": acc, "amount": amount}
            finally:
                c.close()

        def op(c):
            row = c.execute("SELECT balance FROM accounts WHERE id=?", (acc,)).fetchone()
            if not row:
                raise ApiError(404, "no such account")
            if row[0] < amount:
                raise ApiError(409, "insufficient funds")
            self._book(c, acc, -amount, "debit", key=key)
            return {"ok": True, "account": acc, "amount": amount}

        return self._with_idem(key, op)

    def transfer(self, body, key):
        src, dst, amount = body.get("from"), body.get("to"), self._amount(body)
        if amount <= 0:
            raise ApiError(400, "amount must be positive")
        tid = key or f"t-{time.monotonic_ns()}"
        if "transfer_partial" in self.bugs:
            c = self._conn()   # BUG: two separate transactions
            try:
                c.execute("BEGIN IMMEDIATE")
                row = c.execute("SELECT balance FROM accounts WHERE id=?", (src,)).fetchone()
                if not row or row[0] < amount:
                    c.execute("ROLLBACK")
                    raise ApiError(409, "insufficient funds")
                self._book(c, src, -amount, "transfer_out", key=key, transfer=tid)
                c.execute("COMMIT")
                c.execute("BEGIN IMMEDIATE")
                if not self._account_exists(c, dst):
                    c.execute("ROLLBACK")
                    raise ApiError(404, "no such destination")
                self._book(c, dst, amount, "transfer_in", key=key, transfer=tid)
                c.execute("COMMIT")
                return {"ok": True, "transfer": tid}
            finally:
                c.close()

        def op(c):
            row = c.execute("SELECT balance FROM accounts WHERE id=?", (src,)).fetchone()
            if not row or row[0] < amount:
                raise ApiError(409, "insufficient funds")
            self._book(c, src, -amount, "transfer_out", key=key, transfer=tid)
            if not self._account_exists(c, dst):
                raise ApiError(404, "no such destination")   # whole transaction rolls back
            self._book(c, dst, amount, "transfer_in", key=key, transfer=tid)
            return {"ok": True, "transfer": tid}

        return self._with_idem(key, op)

    def sign(self, raw: bytes) -> str:
        return hmac.new(self.secret, raw, hashlib.sha256).hexdigest()

    def webhook(self, raw: bytes, signature: str):
        if "webhook_unsigned" not in self.bugs:
            if not hmac.compare_digest(self.sign(raw), signature or ""):
                raise ApiError(401, "bad signature")
        body = json.loads(raw)
        acc, amount, ev = body.get("account"), self._amount(body), body.get("event_id")
        if amount <= 0 or not ev:
            raise ApiError(400, "bad event")

        def op(c):
            if "webhook_replay" not in self.bugs:
                if c.execute("SELECT 1 FROM entries WHERE event_id=?", (ev,)).fetchone():
                    return {"ok": True, "duplicate": True}
            if not self._account_exists(c, acc):
                raise ApiError(404, "no such account")
            self._book(c, acc, amount, "webhook", event=ev)
            return {"ok": True, "duplicate": False}

        return self._with_idem(None, op)


def make_server(wallet: Wallet, host="127.0.0.1", port=0) -> ThreadingHTTPServer:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status, obj):
            data = json.dumps({k: v for k, v in obj.items() if not k.startswith("_")}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            try:
                if self.path.startswith("/accounts/"):
                    return self._send(200, wallet.balance(self.path.split("/", 2)[2]))
                self._send(404, {"error": "not found"})
            except ApiError as e:
                self._send(e.status, {"error": str(e)})

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            key = self.headers.get("Idempotency-Key")
            try:
                if self.path == "/webhook":
                    return self._send(200, wallet.webhook(raw, self.headers.get("X-Signature")))
                body = json.loads(raw or b"{}")
                route = {"/accounts": lambda: wallet.create_account(body.get("id")),
                         "/credit": lambda: wallet.credit(body, key),
                         "/debit": lambda: wallet.debit(body, key),
                         "/transfer": lambda: wallet.transfer(body, key)}.get(self.path)
                if not route:
                    return self._send(404, {"error": "not found"})
                self._send(200, route())
            except ApiError as e:
                self._send(e.status, {"error": str(e)})
            except (ValueError, json.JSONDecodeError) as e:
                self._send(400, {"error": str(e)})

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        request_queue_size = 256   # listen() backlog; the default (5) resets bursts

    return Server((host, port), H)


def serve_in_thread(wallet):
    srv = make_server(wallet)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
