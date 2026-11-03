"""Access roles, password hashing (scrypt), sessions and login lockout."""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

ROLES = {
    "viewer": {"structures:read"},
    "analyst": {"structures:read", "structures:import", "study:read", "export"},
    "admin": {"structures:read", "structures:import", "study:read", "export", "audit:read"},
}
IDLE_TIMEOUT = 15 * 60
MAX_FAILS, LOCK_SECONDS = 5, 5 * 60


def can(role, perm):
    return perm in ROLES.get(role, set())


def hash_pw(pw, salt=None):
    salt = salt or os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return salt.hex() + "$" + h.hex()


def check_pw(pw, stored):
    salt, h = stored.split("$")
    cand = hash_pw(pw, bytes.fromhex(salt)).split("$")[1]
    return hmac.compare_digest(cand, h)


class Users:
    """Demo accounts (made-up people) with random passwords created on first run."""

    def __init__(self, root):
        self.path = os.path.join(root, "users.json")
        self.created = None
        if not os.path.exists(self.path):
            self.created = {}
            users = {}
            for name, role in [("demo-viewer", "viewer"), ("demo-analyst", "analyst"), ("demo-admin", "admin")]:
                pw = os.environ.get("AFPE_PW_" + role.upper()) or secrets.token_urlsafe(9)
                users[name] = {"role": role, "pw": hash_pw(pw)}
                self.created[name] = pw
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(users, f, indent=1)
        self.users = json.load(open(self.path))
        self.fails = {}
        self.lock = threading.Lock()

    def authenticate(self, name, pw):
        """Returns (role or None, reason)."""
        with self.lock:
            n, until = self.fails.get(name, (0, 0))
            if until > time.time():
                return None, "locked"
        u = self.users.get(name)
        ok = bool(u) and check_pw(pw, u["pw"])
        if not u:
            check_pw(pw, hash_pw("x"))  # even timing for unknown users
        with self.lock:
            if ok:
                self.fails.pop(name, None)
                return u["role"], "ok"
            n += 1
            self.fails[name] = (n, time.time() + LOCK_SECONDS if n >= MAX_FAILS else 0)
            return None, "locked" if n >= MAX_FAILS else "bad credentials"


class Sessions:
    def __init__(self):
        self.s = {}
        self.lock = threading.Lock()

    @staticmethod
    def _k(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def create(self, user, role):
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.s[self._k(token)] = {"user": user, "role": role, "seen": time.time()}
        return token

    def get(self, token):
        if not token:
            return None
        with self.lock:
            e = self.s.get(self._k(token))
            if not e:
                return None
            if time.time() - e["seen"] > IDLE_TIMEOUT:
                del self.s[self._k(token)]
                return None
            e["seen"] = time.time()
            return dict(e)

    def drop(self, token):
        with self.lock:
            self.s.pop(self._k(token or ""), None)
