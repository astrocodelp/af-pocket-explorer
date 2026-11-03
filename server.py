#!/usr/bin/env python3
"""AF Pocket Explorer - local server (Python standard library + cryptography + numpy).

Binds to 127.0.0.1 only. Every API call is checked against the caller's role
and written to the tamper-evident audit trail. Stored structures are encrypted
at rest (AES-256-GCM). Demo data is synthetic; do not load patient data.
"""
import csv
import io
import json
import os
import re
import sys
import urllib.request
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from afpe import analysis, pockets
from afpe.audit import AuditLog
from afpe.auth import ROLES, Sessions, Users, can
from afpe.crypto_store import EncryptedStore, load_keys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("AFPE_DATA_DIR", os.path.join(HERE, "data"))
STATIC = os.path.join(HERE, "static")
MAX_UPLOAD = 5 * 1024 * 1024
COOKIE = "afpe_session"
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; worker-src 'self' blob:; connect-src 'self'; "
       "frame-ancestors 'self'; base-uri 'none'; form-action 'self'")
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}


class App:
    def __init__(self, data_dir=DATA):
        os.makedirs(data_dir, mode=0o700, exist_ok=True)
        dkey, akey, self.key_source = load_keys(data_dir)
        self.store = EncryptedStore(data_dir, dkey)
        self.audit = AuditLog(data_dir, akey)
        self.users = Users(data_dir)
        self.sessions = Sessions()
        self.seed(data_dir)

    def seed(self, data_dir):
        """Encrypt the synthetic seed set (and any study files) into the store on first run."""
        seed_dir = os.path.join(HERE, "data", "seed")
        if os.path.isdir(seed_dir):
            for f in sorted(os.listdir(seed_dir)):
                rid = f[:-5]
                if f.endswith(".json") and rid not in self.store.ids():
                    self.store.put(rid, json.load(open(os.path.join(seed_dir, f))))
        study_dir = os.path.join(HERE, "data", "study")
        if os.path.isdir(study_dir) and "study" not in self.store.ids():
            rows, notes = [], ""
            for f in sorted(os.listdir(study_dir)):
                p = os.path.join(study_dir, f)
                if f.endswith(".csv"):
                    rows += list(csv.DictReader(open(p, encoding="utf-8")))
                elif f.endswith(".md"):
                    notes += open(p, encoding="utf-8").read() + "\n"
            if rows or notes:
                self.store.put("study", {"rows": rows, "notes": notes})

    def structure_ids(self):
        return [i for i in self.store.ids() if i != "study"]


def make_handler(app):
    class H(BaseHTTPRequestHandler):
        server_version = "AFPE"
        sys_version = ""

        def log_message(self, fmt, *args):  # keep stdout quiet; the audit trail is the record
            pass

        # ---------------------------------------------------------- helpers
        def _send(self, code, body, ctype="application/json", extra=None):
            if not isinstance(body, (bytes, bytearray)):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _token(self):
            c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
            return c[COOKIE].value if COOKIE in c else None

        def _session(self):
            return app.sessions.get(self._token())

        def _ip(self):
            return self.client_address[0]

        def _guard(self, perm, action, resource=""):
            s = self._session()
            if not s:
                app.audit.record(None, None, action, resource, "denied:unauthenticated", self._ip())
                self._send(401, {"error": "Please sign in."})
                return None
            if not can(s["role"], perm):
                app.audit.record(s["user"], s["role"], action, resource, "denied:role", self._ip())
                self._send(403, {"error": f"Your role ({s['role']}) cannot do this."})
                return None
            return s

        def _json_body(self, limit=MAX_UPLOAD):
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                raise ValueError("request too large")
            return json.loads(self.rfile.read(n) or b"{}")

        # ---------------------------------------------------------- routes
        def do_GET(self):
            path = self.path.split("?")[0]
            if not path.startswith("/api/"):
                return self._static(path)
            if path == "/api/me":
                s = self._session()
                if not s:
                    return self._send(401, {"error": "not signed in"})
                return self._send(200, {"user": s["user"], "role": s["role"], "permissions": sorted(ROLES[s["role"]]),
                                        "key_source": app.key_source})
            if path == "/api/structures":
                s = self._guard("structures:read", "list_structures")
                if not s:
                    return
                items = []
                for rid in app.structure_ids():
                    r = app.store.get(rid)
                    items.append({"id": rid, "name": r["name"], "synthetic": r.get("synthetic", False),
                                  "scenario": r.get("scenario"), "n_pockets": len(r["pockets"])})
                app.audit.record(s["user"], s["role"], "list_structures", "", "ok", self._ip())
                return self._send(200, items)
            m = re.match(r"^/api/structures/([A-Za-z0-9_.-]+)$", path)
            if m:
                rid = m.group(1)
                s = self._guard("structures:read", "read_structure", rid)
                if not s:
                    return
                r = app.store.get(rid) if rid in app.structure_ids() else None
                if not r:
                    app.audit.record(s["user"], s["role"], "read_structure", rid, "not_found", self._ip())
                    return self._send(404, {"error": f"No structure '{rid}' in the local store."})
                app.audit.record(s["user"], s["role"], "read_structure", rid, "ok", self._ip())
                return self._send(200, analysis.enrich(r))
            m = re.match(r"^/api/export/([A-Za-z0-9_.-]+)\.csv$", path)
            if m:
                rid = m.group(1)
                s = self._guard("export", "export_csv", rid)
                if not s:
                    return
                r = app.store.get(rid) if rid in app.structure_ids() else None
                if not r:
                    return self._send(404, {"error": "not found"})
                in_p = {}
                for p in r["pockets"]:
                    for i in p["lining"]:
                        in_p.setdefault(i, []).append(p["id"])
                buf = io.StringIO()
                w = csv.writer(buf)
                w.writerow(["structure", "resi", "resn", "plddt", "band", "pockets", "synthetic"])
                for res, v in zip(r["residues"], r["plddt"]):
                    w.writerow([rid, res["resi"], res["resn"], v, analysis.band(v), ";".join(in_p.get(res["resi"], [])), r.get("synthetic", False)])
                app.audit.record(s["user"], s["role"], "export_csv", rid, "ok", self._ip())
                return self._send(200, buf.getvalue().encode(), "text/csv; charset=utf-8",
                                  {"Content-Disposition": f'attachment; filename="{rid}-plddt-pockets.csv"'})
            if path == "/api/study":
                s = self._guard("study:read", "read_study")
                if not s:
                    return
                st = app.store.get("study") if "study" in app.store.ids() else None
                app.audit.record(s["user"], s["role"], "read_study", "study", "ok" if st else "empty", self._ip())
                return self._send(200, st or {"rows": [], "notes": ""})
            if path == "/api/audit":
                s = self._guard("audit:read", "read_audit")
                if not s:
                    return
                app.audit.record(s["user"], s["role"], "read_audit", "", "ok", self._ip())
                return self._send(200, {"entries": app.audit.entries(300), "verify": app.audit.verify()})
            return self._send(404, {"error": "unknown endpoint"})

        def do_POST(self):
            path = self.path.split("?")[0]
            # CSRF defence in depth (cookie is also SameSite=Strict)
            if self.headers.get("X-AFPE") != "1":
                return self._send(403, {"error": "missing request header"})
            try:
                body = self._json_body()
            except Exception as e:
                return self._send(400, {"error": str(e)})
            if path == "/api/login":
                user = str(body.get("user", ""))[:64]; pw = str(body.get("password", ""))[:256]
                role, why = app.users.authenticate(user, pw)
                if not role:
                    app.audit.record(user, None, "login", "", "failed:" + why, self._ip())
                    return self._send(401, {"error": "Account locked for 5 minutes." if why == "locked" else "Wrong user or password."})
                tok = app.sessions.create(user, role)
                app.audit.record(user, role, "login", "", "ok", self._ip())
                return self._send(200, {"user": user, "role": role}, extra={
                    "Set-Cookie": f"{COOKIE}={tok}; HttpOnly; SameSite=Strict; Path=/"})
            if path == "/api/logout":
                s = self._session()
                app.sessions.drop(self._token())
                app.audit.record(s and s["user"], s and s["role"], "logout", "", "ok", self._ip())
                return self._send(200, {"ok": True}, extra={
                    "Set-Cookie": f"{COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
            if path == "/api/import/upload":
                s = self._guard("structures:import", "import_upload")
                if not s:
                    return
                name = re.sub(r"[^A-Za-z0-9_.-]", "_", str(body.get("filename", "upload")))[:40]
                rid = "UP-" + re.sub(r"\.(pdb|ent|txt)$", "", name, flags=re.I)
                try:
                    rec = pockets.record_from_pdb(rid, f"Uploaded: {name}", str(body.get("pdb", "")), "upload")
                except Exception as e:
                    app.audit.record(s["user"], s["role"], "import_upload", rid, "failed", self._ip(), str(e))
                    return self._send(400, {"error": f"Could not read that PDB file: {e}"})
                app.store.put(rid, rec)
                app.audit.record(s["user"], s["role"], "import_upload", rid, "ok", self._ip())
                return self._send(200, {"id": rid})
            if path == "/api/import/afdb":
                s = self._guard("structures:import", "import_afdb")
                if not s:
                    return
                q = str(body.get("id", "")).strip().upper()
                if pockets.PDB_ID.match(q):
                    app.audit.record(s["user"], s["role"], "import_afdb", q, "rejected:pdb-id", self._ip())
                    return self._send(400, {"error": f"{q} looks like an experimental PDB entry: it has no pLDDT. Enter a UniProt accession or AlphaFold ID (e.g. AF-P00533-F1) instead."})
                m = pockets.AF_ID.match(q)
                if not m:
                    return self._send(400, {"error": "Enter a UniProt accession (P00533) or AlphaFold DB ID (AF-P00533-F1)."})
                acc = m.group(2); rid = f"AF-{acc}-F1"
                if rid in app.structure_ids():
                    return self._send(200, {"id": rid, "cached": True})
                try:
                    meta = json.load(urllib.request.urlopen(f"https://alphafold.ebi.ac.uk/api/prediction/{acc}", timeout=15))
                    url = meta[0]["pdbUrl"]
                    if not url.startswith("https://alphafold.ebi.ac.uk/"):
                        raise ValueError("unexpected download address")
                    text = urllib.request.urlopen(url, timeout=30).read(MAX_UPLOAD * 4).decode()
                    rec = pockets.record_from_pdb(rid, f"AlphaFold DB {rid} ({meta[0].get('uniprotDescription', '')[:60]})", text, "alphafold-db")
                except Exception as e:
                    app.audit.record(s["user"], s["role"], "import_afdb", rid, "failed", self._ip(), str(e))
                    return self._send(502, {"error": f"Could not download {rid} from AlphaFold DB ({e.__class__.__name__}). If this computer has no internet, download the PDB file from alphafold.ebi.ac.uk yourself and use Upload."})
                app.store.put(rid, rec)
                app.audit.record(s["user"], s["role"], "import_afdb", rid, "ok", self._ip())
                return self._send(200, {"id": rid})
            return self._send(404, {"error": "unknown endpoint"})

        def _static(self, path):
            if path == "/":
                path = "/index.html"
            full = os.path.realpath(os.path.join(STATIC, path.lstrip("/")))
            if not full.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(full):
                return self._send(404, b"not found", "text/plain")
            with open(full, "rb") as f:
                return self._send(200, f.read(), MIME.get(os.path.splitext(full)[1], "application/octet-stream"))

    return H


def main():
    host = os.environ.get("AFPE_HOST", "127.0.0.1")
    port = int(os.environ.get("AFPE_PORT", "8765"))
    app = App()
    if app.users.created:
        print("Created demo accounts (made-up users). Passwords are shown ONCE; they are stored only as scrypt hashes:")
        for u, pw in app.users.created.items():
            print(f"  {u:14s} {pw}")
        with open(os.path.join(DATA, "DEMO_CREDENTIALS.txt"), "w") as f:
            os.chmod(f.name, 0o600)
            f.write("\n".join(f"{u} {pw}" for u, pw in app.users.created.items()) + "\n")
    if app.key_source != "env":
        print("Note: using a local dev encryption key in data/.keys/. For anything beyond a local demo, set AFPE_DATA_KEY / AFPE_AUDIT_KEY from a key manager.")
    app.audit.record("system", "-", "server_start", f"{host}:{port}", "ok")
    print(f"AF Pocket Explorer on http://{host}:{port}  (synthetic demo data; do not load patient data)")
    sys.stdout.flush()
    ThreadingHTTPServer((host, port), make_handler(app)).serve_forever()


if __name__ == "__main__":
    main()
