# AF Pocket Explorer

A local, interactive demo for one question: **do AlphaFold confidence scores (pLDDT) tell you which parts of a drug target hold druggable pockets?**

Short answer the demo lets you see for yourself: *not by themselves.* The global mean pLDDT of a model is a poor proxy for how trustworthy a particular pocket is. Pocket-lining residues need to be judged on their own, and a druggable-looking pocket on low-confidence residues can be an artefact.

> **All bundled structures are SYNTHETIC.** The proteins are idealised helical bundles built by `gen_synthetic.py`; the pLDDT traces and the "fpocket-style" pocket scores are made up to illustrate four situations. They say nothing about any real protein. **No patient data** is used or should be loaded. This is a research visualisation, not a clinical tool, and it makes **no compliance claim** (it is not "HIPAA compliant" or certified in any way; see *Security notes*).

## Run it

Needs Python 3.10+ with `numpy` and `cryptography` (`pip install numpy cryptography`). No other dependencies; 3Dmol.js 2.5.5 is vendored in `static/vendor/` so it works offline.

```bash
python3 server.py            # http://127.0.0.1:8765
```

On first start it creates three **made-up demo accounts** with random passwords, prints them once and saves them to `data/DEMO_CREDENTIALS.txt` (mode 0600):

| user | role | can do |
|---|---|---|
| `demo-viewer` | viewer | open structures, layers, cross-check |
| `demo-analyst` | analyst | + import structures, CSV export, Study tab |
| `demo-admin` | admin | + read the audit trail |

**Passwordless demo sign-in.** The login page shows one button per demo account, no password needed (the password form is still there under it). Roles and the audit trail work exactly as before; these logins are recorded as `ok:passwordless-demo`. It is allowed only when `AFPE_HOST` is loopback and is turned off with `AFPE_PASSWORDLESS=0`. It is for the made-up demo only; never use it with real patient data.

Options (environment variables): `AFPE_PASSWORDLESS` (default 1), `AFPE_PORT`, `AFPE_HOST` (default `127.0.0.1`; keep it that way), `AFPE_DATA_DIR`, `AFPE_PW_VIEWER|ANALYST|ADMIN` (set known demo passwords on first run), `AFPE_DATA_KEY` / `AFPE_AUDIT_KEY` (base64 of 32 bytes; see below).

Tests: `python3 -m unittest discover -s tests -v` (23 tests, synthetic data only). Audit check from the shell: `python3 verify_audit.py`.

## What you see

**Explorer tab.** Pick a structure on the left; 3D view in the middle (drag = rotate, scroll = zoom, right-drag = move). The **Layers** switch toggles:

- **Confidence** - cartoon coloured by AlphaFold's standard bands: very high >90 dark blue, high 70-90 cyan, low 50-70 yellow, very low <50 orange.
- **Pockets** - neutral grey backbone, each predicted pocket as translucent spheres (a pocket volume stand-in) plus its lining residues as sticks, one colour per pocket.
- **Both** - confidence cartoon with the pocket volumes on top, lining sticks coloured by pLDDT. This is the view that shows a bright pocket sitting on orange/yellow residues.

Hover any residue: residue name and number, pLDDT, band, and whether it lines a pocket. Click a pocket residue to select that pocket. "Fade residues >= 70" makes the doubtful regions pop. The chart under the 3D view plots pLDDT along the sequence; the selected pocket's residues are shaded and hovering the chart marks that residue in 3D.

**Cross-check panel (right).** For the selected pocket: druggability score, number of lining residues, mean / median / min pLDDT, residues below 70 and below 50, the **global mean pLDDT** and the gap between them, a one-line verdict, and a stacked bar comparing the confidence mix of the pocket with the whole chain.

Verdict rule (simple on purpose, see `afpe/analysis.py`): *confident* = pocket mean pLDDT >= 70, at most 25% of lining residues below 70, none below 50. *Druggable* = score >= 0.5 (fpocket's usual cut-off). Confident + druggable = **agree**; druggable but not confident = **disagree**; confident but not druggable = high pLDDT does not make a pocket druggable; neither = noise.

**Compare all tab.** Every pocket of every loaded structure on one scatter plot (pocket-lining mean pLDDT vs druggability) with the structure's global mean as a hollow ring on the same row, so you can see how far the global number is from the pocket's own number. Click a dot to jump to it.

**Study results tab.** Shows the Scientist's actual study numbers once those files exist (see below). Until then it says so; nothing is invented there.

### The four synthetic cases

| id | what it shows |
|---|---|
| `SYN-KIN1` | Good case: a deep druggable pocket on very high pLDDT. Plus a confident but shallow, undruggable groove. |
| `SYN-GPCR2` | **Global mean misleads (low side):** ~40% disordered tails drag the global mean into the 60s, but the real pocket is at pLDDT ~90. A global cut-off would wrongly discard it. A druggable-ish crevice in the tail is pure noise. |
| `SYN-PPI3` | **Global mean misleads (high side):** global mean looks fine (~77), but the top-scoring druggable pocket sits under a low-confidence lid loop (pLDDT ~57). Verdict: disagree. |
| `SYN-LINK4` | Two confident domains joined by a floppy linker; the big "pocket" between them is a product of an uncertain domain arrangement (check PAE in real models). |

## Loading your own structure (analyst/admin)

- **UniProt / AlphaFold ID** (`P00533` or `AF-P00533-F1`): downloads the model from AlphaFold DB (needs internet; the server only contacts `alphafold.ebi.ac.uk`).
- **Upload** an AlphaFold PDB file.
- Experimental PDB IDs (like `1ABC`) are refused: they have no pLDDT.

Imported structures get pockets from a **small built-in toy grid detector** (LIGSITE-style buriedness) with a made-up logistic "druggability". It is **not fpocket** and its scores are not comparable to fpocket's. To use real fpocket output, run fpocket separately and extend `afpe/pockets.py` to read its `*_out/` files; the rest of the app does not care where pockets come from. Imported structures, like all stored records, are encrypted at rest.

Only the first chain is read, up to 60k atoms.

## Adding the Scientist's study results

Place de-identified files in `data/study/` (they are git-ignored by default):

- one or more `.csv`, one row per pocket, ideally with columns `target, uniprot, af_id, pocket_id, druggability, pocket_mean_plddt, pocket_min_plddt, frac_below_70, global_mean_plddt, label`; extra columns are shown in the table;
- optional `.md` summary shown above the table.

Then remove `data/store/study.enc` if it exists and restart. With `druggability` and `pocket_mean_plddt` present you also get a scatter plot (colour = `label`, hollow ring = global mean). Study data must contain no patient data.

## Security notes (what is built in, and its limits)

Built in from the start, even for the demo:

- **Access roles:** viewer / analyst / admin, enforced on the server for every API call (not just hidden in the UI). Passwords are scrypt-hashed; 5 failed logins lock an account for 5 minutes; sessions are HttpOnly + SameSite=Strict cookies with a 15-minute idle timeout; POSTs need a custom header; a strict Content-Security-Policy is sent.
- **Audit trail:** every sign-in (and failure), structure read, list, export, import, study read, audit read and every denied request is appended to `data/audit/audit.log`. Entries are hash-chained and HMAC-signed with a separate key; editing, deleting or re-ordering lines is detected and shown in the Audit tab and by `verify_audit.py`. Entries contain no record contents.
- **Encryption at rest:** every stored structure/study record is AES-256-GCM encrypted (`data/store/*.enc`), with the record id bound as authenticated data.
- **Local only:** binds to 127.0.0.1; nothing is deployed anywhere.

Limits you should know: the dev keys live in `data/.keys/` next to the data, which protects against casual disk reads but not against someone with the whole folder. For anything beyond the demo, supply `AFPE_DATA_KEY` / `AFPE_AUDIT_KEY` from a key manager you own. The audit file is tamper-*evident*, not tamper-proof: an attacker with both the file and the audit key could rewrite it, so ship entries to separate write-once storage in real use. The server speaks plain HTTP on localhost; use TLS if you ever expose it. There is no MFA. These controls are good engineering practice, not a compliance certification.

**If this ever holds patient data:** do not run it on StellarFirm hosting. Use only a host where you have a signed BAA (US) or a GDPR data-processing agreement (EU/UK), add MFA, TLS, key management and a proper review first. The bundled demo does not need any of that because it contains none.

## Layout

```
server.py            HTTP server + API (stdlib)
afpe/analysis.py     pLDDT-vs-pocket statistics and verdicts
afpe/pockets.py      PDB parsing + toy grid pocket finder (imports only)
afpe/auth.py         roles, scrypt, sessions, lockout
afpe/audit.py        hash-chained HMAC audit log
afpe/crypto_store.py AES-256-GCM record store
gen_synthetic.py     builds data/seed/*.json (synthetic proteins, pLDDT, pockets)
static/              index.html, app.js, style.css, vendor/3Dmol-min.js
tests/test_all.py
```

Seed data is regenerated with `python3 gen_synthetic.py` (needs numpy only).
