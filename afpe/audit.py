"""Append-only, tamper-evident audit trail.

Each line is a JSON entry carrying the hash of the previous entry and an
HMAC-SHA256 over its own canonical form (key: AFPE_AUDIT_KEY or a local dev
key). Editing, deleting or reordering any line breaks verify(). The app has
no API to change or delete entries. Entries hold who/what/when/outcome only,
never record contents.
"""
import hashlib
import hmac
import json
import os
import threading
import time

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, root, key):
        d = os.path.join(root, "audit")
        os.makedirs(d, mode=0o700, exist_ok=True)
        self.path = os.path.join(d, "audit.log")
        self.key = key
        self.lock = threading.Lock()
        self.seq, self.last = 0, GENESIS
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        e = json.loads(line)
                        self.seq, self.last = e["seq"], e["hash"]

    def _mac(self, body):
        return hmac.new(self.key, body.encode(), hashlib.sha256).hexdigest()

    def record(self, user, role, action, resource="", outcome="ok", ip="", detail=""):
        with self.lock:
            e = {"seq": self.seq + 1, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "user": user or "-", "role": role or "-", "action": action,
                 "resource": str(resource)[:120], "outcome": outcome, "ip": ip,
                 "detail": str(detail)[:200], "prev": self.last}
            body = json.dumps(e, sort_keys=True, separators=(",", ":"))
            e["hash"] = hashlib.sha256(body.encode()).hexdigest()
            e["mac"] = self._mac(body)
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(json.dumps(e, sort_keys=True) + "\n")
                f.flush(); os.fsync(f.fileno())
            self.seq, self.last = e["seq"], e["hash"]
            return e

    def entries(self, limit=200):
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        return [json.loads(l) for l in lines[-limit:]][::-1]

    def verify(self):
        prev, n = GENESIS, 0
        if not os.path.exists(self.path):
            return {"ok": True, "entries": 0}
        with open(self.path, encoding="utf-8") as f:
            all_lines = f.readlines()
        for i, line in enumerate(all_lines, 1):
            if not line.strip():
                continue
            try:
                e = json.loads(line)
                h, m = e.pop("hash"), e.pop("mac")
            except Exception:
                return {"ok": False, "entries": n, "broken_at_line": i, "reason": "unreadable entry"}
            body = json.dumps(e, sort_keys=True, separators=(",", ":"))
            if e.get("prev") != prev:
                return {"ok": False, "entries": n, "broken_at_line": i, "reason": "chain link mismatch (entry removed or reordered)"}
            if hashlib.sha256(body.encode()).hexdigest() != h or not hmac.compare_digest(self._mac(body), m):
                return {"ok": False, "entries": n, "broken_at_line": i, "reason": "entry content altered"}
            if e.get("seq") != n + 1:
                return {"ok": False, "entries": n, "broken_at_line": i, "reason": "sequence gap"}
            prev, n = h, n + 1
        return {"ok": True, "entries": n}
