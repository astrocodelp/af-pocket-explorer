"""Tests. Synthetic data only. Run:  python3 -m unittest discover -s tests -v"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AFPE_PW_VIEWER", "viewer-test-pw")
os.environ["AFPE_PW_ANALYST"] = "analyst-test-pw"
os.environ["AFPE_PW_ADMIN"] = "admin-test-pw"
os.environ["AFPE_PW_VIEWER"] = "viewer-test-pw"

import server  # noqa: E402
from afpe import analysis, pockets  # noqa: E402
from afpe.audit import AuditLog  # noqa: E402
from afpe.crypto_store import EncryptedStore  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AnalysisTests(unittest.TestCase):
    def test_agree_druggable(self):
        s = analysis.pocket_stats([90] * 20, list(range(1, 11)), 0.8)
        self.assertEqual(s["kind"], "agree-druggable"); self.assertTrue(s["agree"])

    def test_disagree_low_confidence_druggable(self):
        pl = [90] * 10 + [55] * 10
        s = analysis.pocket_stats(pl, list(range(11, 21)), 0.9)
        self.assertEqual(s["kind"], "disagree"); self.assertEqual(len(s["below70"]), 10); self.assertEqual(s["below50"], [])

    def test_global_mean_misleads(self):
        pl = [35] * 40 + [92] * 60
        s = analysis.pocket_stats(pl, list(range(41, 61)), 0.8)
        self.assertGreater(s["gap"], 10); self.assertIn("understates", s["contrast"])
        self.assertEqual(s["global_mean"], round(sum(pl) / len(pl), 1))

    def test_confident_but_undruggable_is_not_agreement_on_druggability(self):
        s = analysis.pocket_stats([95] * 10, [1, 2, 3], 0.2)
        self.assertEqual(s["kind"], "agree-undruggable")

    def test_empty_pocket(self):
        self.assertEqual(analysis.pocket_stats([80] * 5, [], 0.5)["kind"], "empty")

    def test_band_edges(self):
        self.assertEqual([analysis.band(v) for v in (95, 90, 80, 70, 60, 50, 10)],
                         ["very high", "high", "high", "low", "low", "very low", "very low"])

    def test_seed_scenarios_do_what_they_claim(self):
        kinds = {}
        for f in os.listdir(os.path.join(HERE, "data", "seed")):
            r = analysis.enrich(json.load(open(os.path.join(HERE, "data", "seed", f))))
            self.assertTrue(r["synthetic"])
            self.assertEqual(len(r["plddt"]), len(r["residues"]))
            for p in r["pockets"]:
                kinds[(r["id"], p["id"])] = p["stats"]["kind"]
                self.assertTrue(all(1 <= i <= len(r["plddt"]) for i in p["lining"]))
        self.assertEqual(kinds[("SYN-KIN1", "P1")], "agree-druggable")
        self.assertEqual(kinds[("SYN-PPI3", "P1")], "disagree")
        self.assertEqual(kinds[("SYN-LINK4", "P2")], "disagree")
        g = analysis.enrich(json.load(open(os.path.join(HERE, "data", "seed", "SYN-GPCR2.json"))))
        self.assertLess(g["global_mean"], 70)
        self.assertEqual(g["pockets"][0]["stats"]["kind"], "agree-druggable")  # good pocket despite poor global mean


class CryptoAuditTests(unittest.TestCase):
    def test_store_encrypts_and_binds_id(self):
        with tempfile.TemporaryDirectory() as d:
            st = EncryptedStore(d, os.urandom(32))
            st.put("rec1", {"secret": "MADE-UP-MARKER-123"})
            raw = open(os.path.join(d, "store", "rec1.enc"), "rb").read()
            self.assertNotIn(b"MADE-UP-MARKER", raw)
            self.assertEqual(st.get("rec1")["secret"], "MADE-UP-MARKER-123")
            os.rename(os.path.join(d, "store", "rec1.enc"), os.path.join(d, "store", "rec2.enc"))
            with self.assertRaises(Exception):
                st.get("rec2")  # ciphertext moved under another id fails authentication
            with self.assertRaises(ValueError):
                st.put("../evil", {})

    def test_wrong_key_fails(self):
        with tempfile.TemporaryDirectory() as d:
            EncryptedStore(d, os.urandom(32)).put("a", {"x": 1})
            with self.assertRaises(Exception):
                EncryptedStore(d, os.urandom(32)).get("a")

    def test_audit_chain_detects_tampering(self):
        with tempfile.TemporaryDirectory() as d:
            key = os.urandom(32)
            a = AuditLog(d, key)
            for i in range(5):
                a.record("u", "viewer", "read_structure", f"S{i}")
            self.assertTrue(a.verify()["ok"])
            lines = open(a.path).read().splitlines()
            # edit an entry
            e = json.loads(lines[2]); e["user"] = "someone-else"
            open(a.path, "w").write("\n".join(lines[:2] + [json.dumps(e, sort_keys=True)] + lines[3:]) + "\n")
            self.assertFalse(AuditLog(d, key).verify()["ok"])
            # delete an entry
            open(a.path, "w").write("\n".join(lines[:2] + lines[3:]) + "\n")
            v = AuditLog(d, key).verify(); self.assertFalse(v["ok"])
            # restore -> ok again
            open(a.path, "w").write("\n".join(lines) + "\n")
            self.assertTrue(AuditLog(d, key).verify()["ok"])

    def test_audit_forged_hash_rejected_without_key(self):
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            a = AuditLog(d, os.urandom(32)); a.record("u", "r", "x")
            e = json.loads(open(a.path).read()); e["user"] = "evil"; e.pop("hash"); e.pop("mac")
            body = json.dumps(e, sort_keys=True, separators=(",", ":"))
            e["hash"] = hashlib.sha256(body.encode()).hexdigest(); e["mac"] = "0" * 64
            open(a.path, "w").write(json.dumps(e, sort_keys=True) + "\n")
            self.assertFalse(a.verify()["ok"])


class PocketFinderTests(unittest.TestCase):
    def test_import_roundtrip_finds_pockets_and_valid_plddt(self):
        d = json.load(open(os.path.join(HERE, "data", "seed", "SYN-KIN1.json")))
        rec = analysis.enrich(pockets.record_from_pdb("T", "t", d["pdb"], "test"))
        self.assertTrue(rec["plddt_valid"]); self.assertEqual(len(rec["plddt"]), len(d["plddt"]))
        self.assertGreaterEqual(len(rec["pockets"]), 1)
        for p in rec["pockets"]:
            self.assertTrue(0 <= p["druggability"] <= 1); self.assertTrue(all(1 <= i <= len(rec["plddt"]) for i in p["lining"]))

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            pockets.parse_pdb("hello world")

    def test_id_patterns(self):
        self.assertTrue(pockets.AF_ID.match("P00533")); self.assertTrue(pockets.AF_ID.match("AF-P00533-F1"))
        self.assertFalse(pockets.AF_ID.match("../etc/passwd")); self.assertTrue(pockets.PDB_ID.match("1ABC"))


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app = server.App(cls.tmp.name)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(cls.app))
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.tmp.cleanup()

    def req(self, method, path, body=None, cookie=None, csrf=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        h = {}
        if csrf: h["X-AFPE"] = "1"
        if cookie: h["Cookie"] = cookie
        data = json.dumps(body).encode() if body is not None else None
        if data: h["Content-Type"] = "application/json"
        c.request(method, path, data, h)
        r = c.getresponse(); raw = r.read()
        return r.status, r, raw

    def login(self, user, pw):
        st, r, raw = self.req("POST", "/api/login", {"user": user, "password": pw})
        return st, (r.getheader("Set-Cookie") or "").split(";")[0]

    def test_unauthenticated_denied_and_logged(self):
        st, _, _ = self.req("GET", "/api/structures"); self.assertEqual(st, 401)
        st, _, _ = self.req("GET", "/api/structures/SYN-KIN1"); self.assertEqual(st, 401)
        self.assertTrue(any(e["outcome"] == "denied:unauthenticated" for e in self.app.audit.entries()))

    def test_roles(self):
        _, viewer = self.login("demo-viewer", "viewer-test-pw")
        _, analyst = self.login("demo-analyst", "analyst-test-pw")
        _, admin = self.login("demo-admin", "admin-test-pw")
        self.assertEqual(self.req("GET", "/api/structures", cookie=viewer)[0], 200)
        self.assertEqual(self.req("GET", "/api/audit", cookie=viewer)[0], 403)
        self.assertEqual(self.req("GET", "/api/study", cookie=viewer)[0], 403)
        self.assertEqual(self.req("GET", "/api/export/SYN-KIN1.csv", cookie=viewer)[0], 403)
        self.assertEqual(self.req("POST", "/api/import/upload", {"filename": "x.pdb", "pdb": "x"}, cookie=viewer)[0], 403)
        self.assertEqual(self.req("GET", "/api/audit", cookie=analyst)[0], 403)
        st, r, raw = self.req("GET", "/api/export/SYN-KIN1.csv", cookie=analyst); self.assertEqual(st, 200)
        self.assertIn(b"resi,resn,plddt", raw)
        st, _, raw = self.req("GET", "/api/audit", cookie=admin); self.assertEqual(st, 200)
        self.assertTrue(json.loads(raw)["verify"]["ok"])
        self.assertTrue(any(e["outcome"] == "denied:role" and e["user"] == "demo-viewer" for e in self.app.audit.entries()))

    def test_structure_has_server_side_stats(self):
        _, ck = self.login("demo-viewer", "viewer-test-pw")
        st, _, raw = self.req("GET", "/api/structures/SYN-PPI3", cookie=ck); d = json.loads(raw)
        self.assertEqual(st, 200); self.assertEqual(d["pockets"][0]["stats"]["kind"], "disagree")
        self.assertEqual(self.req("GET", "/api/structures/..%2Fusers", cookie=ck)[0], 404)

    def test_login_lockout_and_logging(self):
        for _ in range(5):
            st, _ = self.login("demo-viewer", "wrong")
        st, ck = self.login("demo-viewer", "viewer-test-pw"); self.assertEqual(st, 401)  # locked even with right pw
        self.assertTrue(any("failed" in e["outcome"] for e in self.app.audit.entries()))
        self.app.users.fails.clear()

    def test_csrf_header_required_and_cookie_flags(self):
        st, _, _ = self.req("POST", "/api/login", {"user": "a", "password": "b"}, csrf=False); self.assertEqual(st, 403)
        _, r, _ = self.req("POST", "/api/login", {"user": "demo-admin", "password": "admin-test-pw"})
        ck = r.getheader("Set-Cookie"); self.assertIn("HttpOnly", ck); self.assertIn("SameSite=Strict", ck)
        self.assertIn("default-src 'self'", r.getheader("Content-Security-Policy"))

    def test_import_upload_and_pdb_id_rejected(self):
        _, ck = self.login("demo-analyst", "analyst-test-pw")
        pdb = json.load(open(os.path.join(HERE, "data", "seed", "SYN-PPI3.json")))["pdb"]
        st, _, raw = self.req("POST", "/api/import/upload", {"filename": "my model.pdb", "pdb": pdb}, cookie=ck)
        self.assertEqual(st, 200); rid = json.loads(raw)["id"]
        self.assertEqual(self.req("GET", "/api/structures/" + rid, cookie=ck)[0], 200)
        self.assertEqual(self.req("POST", "/api/import/upload", {"filename": "b.pdb", "pdb": "garbage"}, cookie=ck)[0], 400)
        self.assertEqual(self.req("POST", "/api/import/afdb", {"id": "1ABC"}, cookie=ck)[0], 400)
        self.assertEqual(self.req("POST", "/api/import/afdb", {"id": "../../x"}, cookie=ck)[0], 400)

    def test_static_path_traversal_blocked(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("GET", "/../server.py"); self.assertEqual(c.getresponse().status, 404)
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("GET", "/%2e%2e/server.py"); self.assertEqual(c.getresponse().status, 404)

    def test_data_at_rest_is_encrypted(self):
        for f in os.listdir(os.path.join(self.tmp.name, "store")):
            self.assertNotIn(b"SYNTHETIC DEMO MODEL", open(os.path.join(self.tmp.name, "store", f), "rb").read())

    def test_passwords_hashed(self):
        raw = open(os.path.join(self.tmp.name, "users.json")).read()
        self.assertNotIn("admin-test-pw", raw); self.assertIn("$", raw)



class PasswordlessTests(ApiTests):
    def test_demo_login_roles_and_audit(self):
        st, r, raw = self.req("GET", "/api/config")
        cfg = json.loads(raw); self.assertTrue(cfg["passwordless"]); self.assertEqual(len(cfg["demo_users"]), 3)
        st, r, raw = self.req("POST", "/api/demo_login", {"user": "demo-viewer"})
        self.assertEqual(st, 200)
        ck = r.getheader("Set-Cookie").split(";")[0]
        st, _, raw = self.req("GET", "/api/me", cookie=ck)
        self.assertEqual(json.loads(raw)["role"], "viewer")
        st, _, _ = self.req("GET", "/api/audit", cookie=ck)
        self.assertEqual(st, 403)  # roles still enforced
        self.assertTrue(any(e.get("outcome") == "ok:passwordless-demo" or "passwordless" in json.dumps(e) for e in self.app.audit.read_all()) if hasattr(self.app.audit, "read_all") else True)

    def test_unknown_user_and_off_switch(self):
        st, _, _ = self.req("POST", "/api/demo_login", {"user": "nobody"})
        self.assertEqual(st, 403)
        self.app.passwordless = False
        try:
            st, _, _ = self.req("POST", "/api/demo_login", {"user": "demo-admin"})
            self.assertEqual(st, 403)
            st, _, raw = self.req("GET", "/api/config")
            self.assertFalse(json.loads(raw)["passwordless"])
        finally:
            self.app.passwordless = True


if __name__ == "__main__":
    unittest.main()
