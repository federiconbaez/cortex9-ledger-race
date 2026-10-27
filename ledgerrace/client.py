"""Tiny HTTP client (stdlib) for the documented wallet contract."""
from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.request


class Client:
    def __init__(self, base_url: str, secret: bytes, timeout=30):
        self.base, self.secret, self.timeout = base_url.rstrip("/"), secret, timeout
        self.transport_errors = 0

    def _req(self, method, path, body=None, headers=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
        except OSError as e:  # connection reset/refused/timeout: not an API answer
            self.transport_errors += 1
            return 0, {"transport_error": repr(e)}

    def create(self, acc):
        return self._req("POST", "/accounts", {"id": acc})

    def credit(self, acc, amount, key=None):
        return self._req("POST", "/credit", {"account": acc, "amount": amount},
                         {"Idempotency-Key": key} if key else None)

    def debit(self, acc, amount, key=None):
        return self._req("POST", "/debit", {"account": acc, "amount": amount},
                         {"Idempotency-Key": key} if key else None)

    def transfer(self, src, dst, amount, key=None):
        return self._req("POST", "/transfer", {"from": src, "to": dst, "amount": amount},
                         {"Idempotency-Key": key} if key else None)

    def balance(self, acc):
        return self._req("GET", f"/accounts/{acc}")

    def webhook(self, event: dict, signature: str | None = None):
        raw = json.dumps(event, sort_keys=True).encode()
        sig = signature if signature is not None else hmac.new(self.secret, raw, hashlib.sha256).hexdigest()
        return self._req("POST", "/webhook", raw=raw, headers={"X-Signature": sig})
