"""Build dist/: a read-only static copy of the SYNTHETIC demo for Vercel (no server, no login, no accounts,
no audit log, no import). Only the made-up seed proteins are embedded; nothing is sent anywhere."""
import csv, io, json, os, shutil, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from afpe import analysis
OUT = os.path.join(HERE, "dist"); shutil.rmtree(OUT, ignore_errors=True); shutil.copytree(os.path.join(HERE, "static"), OUT)
seed = os.path.join(HERE, "data", "seed"); recs = {}
for f in sorted(os.listdir(seed)):
    if f.endswith(".json"):
        r = json.load(open(os.path.join(seed, f)))
        assert r.get("synthetic") is True, f + " is not synthetic; refusing to publish it"
        recs[f[:-5]] = r
data = {"list": [{"id": k, "name": r["name"], "synthetic": True, "scenario": r.get("scenario"), "n_pockets": len(r["pockets"])} for k, r in recs.items()],
        "rec": {k: analysis.enrich(r) for k, r in recs.items()}, "csv": {}}
for k, r in recs.items():
    inp = {}
    for p in r["pockets"]:
        for i in p["lining"]: inp.setdefault(i, []).append(p["id"])
    b = io.StringIO(); w = csv.writer(b); w.writerow(["structure", "resi", "resn", "plddt", "band", "pockets", "synthetic"])
    for res, v in zip(r["residues"], r["plddt"]): w.writerow([k, res["resi"], res["resn"], v, analysis.band(v), ";".join(inp.get(res["resi"], [])), True])
    data["csv"][k] = b.getvalue()
shim = """/* STATIC DEMO SHIM: answers /api/* from embedded synthetic data; read-only, no accounts. */
window.AFPE_DATA = %s;
(function () {
  const D = window.AFPE_DATA, real = window.fetch.bind(window);
  const J = (o, s) => new Response(JSON.stringify(o), { status: s || 200, headers: { 'Content-Type': 'application/json' } });
  window.fetch = function (p, init) {
    p = String(p); if (!p.startsWith('/api/')) return real(p, init);
    if (p === '/api/config') return Promise.resolve(J({ passwordless: false, demo_users: [] }));
    if (p === '/api/me') return Promise.resolve(J({ user: 'static-demo', role: 'viewer', permissions: ['structures:read', 'export'], key_source: 'none' }));
    if (p === '/api/structures') return Promise.resolve(J(D.list));
    let m = p.match(/^\\/api\\/structures\\/([\\w.-]+)$/); if (m) return Promise.resolve(D.rec[m[1]] ? J(D.rec[m[1]]) : J({ error: 'Not in the static demo.' }, 404));
    m = p.match(/^\\/api\\/export\\/([\\w.-]+)\\.csv$/); if (m && D.csv[m[1]]) return Promise.resolve(new Response(D.csv[m[1]], { headers: { 'Content-Type': 'text/csv' } }));
    return Promise.resolve(J({ error: 'Not available in the read-only static demo.' }, 403));
  };
  document.addEventListener('DOMContentLoaded', () => {
    const b = document.createElement('div'); b.style.cssText = 'background:#3a2f12;color:#ffd98a;padding:6px 14px;font:13px sans-serif;text-align:center';
    b.textContent = 'Read-only static demo: made-up proteins only, no login, no imports, no audit trail. Not a clinical tool. Do not use with real patient data.';
    document.body.prepend(b); const lo = document.getElementById('logout'); if (lo) lo.hidden = true;
  });
})();
""" % json.dumps(data, separators=(",", ":"))
open(os.path.join(OUT, "static-shim.js"), "w").write(shim)
h = open(os.path.join(OUT, "index.html")).read()
h = h.replace('<script src="/vendor/3Dmol-min.js"></script>', '<script src="/static-shim.js"></script>\n<script src="/vendor/3Dmol-min.js"></script>', 1)
assert "static-shim" in h
open(os.path.join(OUT, "index.html"), "w").write(h)
json.dump({"version": 2, "cleanUrls": True, "headers": [{"source": "/(.*)", "headers": [
    {"key": "X-Content-Type-Options", "value": "nosniff"}, {"key": "Referrer-Policy", "value": "no-referrer"},
    {"key": "X-Frame-Options", "value": "SAMEORIGIN"}]}]}, open(os.path.join(OUT, "vercel.json"), "w"))
print("built", len(recs), "synthetic structures,", sum(len(x) for x in os.listdir(OUT)), "files in", OUT)
