"""Encryption at rest: every stored record is AES-256-GCM encrypted.

Key source, in order:
  1. AFPE_DATA_KEY env var (base64 of 32 bytes) - use this, fed from a key
     manager (cloud KMS / secrets manager), in any non-demo setting;
  2. a local dev key file data/.keys/data.key (created 0600 on first run).
The record id is bound as associated data, so a ciphertext cannot be swapped
under another record's name without failing authentication.
"""
import base64
import json
import os
import re

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def _load_key(root, env_name, file_name):
    env = os.environ.get(env_name)
    if env:
        key = base64.b64decode(env)
        if len(key) != 32:
            raise ValueError(f"{env_name} must be base64 of exactly 32 bytes")
        return key, "env"
    kdir = os.path.join(root, ".keys")
    os.makedirs(kdir, mode=0o700, exist_ok=True)
    path = os.path.join(kdir, file_name)
    if not os.path.exists(path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(os.urandom(32))
    with open(path, "rb") as f:
        return f.read(), "local-dev-file"


def load_keys(root):
    data_key, src = _load_key(root, "AFPE_DATA_KEY", "data.key")
    audit_key, _ = _load_key(root, "AFPE_AUDIT_KEY", "audit.key")
    return data_key, audit_key, src


class EncryptedStore:
    def __init__(self, root, key):
        self.dir = os.path.join(root, "store")
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        self.aead = AESGCM(key)

    def _path(self, rid):
        if not ID_RE.match(rid):
            raise ValueError("bad record id")
        return os.path.join(self.dir, rid + ".enc")

    def put(self, rid, obj):
        nonce = os.urandom(12)
        ct = self.aead.encrypt(nonce, json.dumps(obj).encode(), rid.encode())
        tmp = self._path(rid) + ".tmp"
        with open(tmp, "wb") as f:
            f.write(b"AFPE1" + nonce + ct)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._path(rid))

    def get(self, rid):
        p = self._path(rid)
        if not os.path.exists(p):
            return None
        with open(p, "rb") as f:
            blob = f.read()
        if blob[:5] != b"AFPE1":
            raise ValueError("unknown record format")
        return json.loads(self.aead.decrypt(blob[5:17], blob[17:], rid.encode()))

    def ids(self):
        return sorted(f[:-4] for f in os.listdir(self.dir) if f.endswith(".enc"))
