/* AF Pocket Explorer front end. No inline scripts (CSP); all text inserted via textContent. */
(function () {
  'use strict';
  const BANDS = [
    { key: 'very high', label: 'Very high (>90)', color: '#0053D6', min: 90 },
    { key: 'high', label: 'High (70-90)', color: '#65CBF3', min: 70 },
    { key: 'low', label: 'Low (50-70)', color: '#FFDB13', min: 50 },
    { key: 'very low', label: 'Very low (<50)', color: '#FF7D45', min: 0 },
  ];
  const POCKET_COLORS = ['#e040fb', '#26d07c', '#ff8a3d', '#40c4ff', '#f6e05e'];
  const bandOf = v => (v > 90 ? BANDS[0] : v > 70 ? BANDS[1] : v > 50 ? BANDS[2] : BANDS[3]);
  const $ = id => document.getElementById(id);

  function h(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const k in (attrs || {})) {
      if (k === 'class') e.className = attrs[k]; else if (k === 'style') e.style.cssText = attrs[k];
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), attrs[k]); else e.setAttribute(k, attrs[k]);
    }
    for (const c of kids.flat()) if (c != null) e.append(c.nodeType ? c : document.createTextNode(String(c)));
    return e;
  }
  const clear = e => { while (e.firstChild) e.removeChild(e.firstChild); return e; };

  async function api(path, opts) {
    opts = opts || {};
    const init = { method: opts.body ? 'POST' : 'GET', headers: { 'X-AFPE': '1' }, credentials: 'same-origin' };
    if (opts.body) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
    const r = await fetch(path, init);
    if (r.status === 401 && path !== '/api/login' && path !== '/api/me') { showLogin(); throw new Error('Please sign in.'); }
    const ct = r.headers.get('Content-Type') || '';
    const data = ct.includes('json') ? await r.json() : await r.text();
    if (!r.ok) throw new Error((data && data.error) || r.statusText);
    return data;
  }

  const S = { me: null, list: [], rec: null, pocketIdx: 0, layer: 'conf', viewer: null, marker: null, all: null };

  // ---------------------------------------------------------------- login / shell
  async function loadDemoLogin() {
    try {
      const r = await fetch('/api/config'); const c = await r.json();
      const box = $('demoBtns'); box.innerHTML = '';
      $('demoLogin').hidden = !c.passwordless;
      $('pwBox').open = !c.passwordless;
      (c.demo_users || []).forEach(u => {
        const b = document.createElement('button'); b.className = 'primary'; b.type = 'button';
        b.textContent = 'Enter as ' + u.user + ' (' + u.role + ')';
        b.addEventListener('click', async () => {
          $('demoErr').textContent = '';
          try { await api('/api/demo_login', { body: { user: u.user } }); await enter(await api('/api/me')); }
          catch (e) { $('demoErr').textContent = e.message; }
        });
        box.appendChild(b);
      });
    } catch (e) { $('pwBox').open = true; }
  }
  function showLogin() {
    S.me = null; loadDemoLogin();
    $('login').hidden = false; $('tabs').hidden = true; $('who').hidden = true;
    document.querySelectorAll('.tab').forEach(t => t.hidden = true);
  }
  async function enter(me) {
    S.me = me;
    $('login').hidden = true; $('tabs').hidden = false; $('who').hidden = false;
    $('whoText').textContent = me.user + ' (' + me.role + ')';
    $('tab-audit').hidden = !me.permissions.includes('audit:read');
    $('importCard').hidden = !me.permissions.includes('structures:import');
    $('csv').hidden = !me.permissions.includes('export');
    document.querySelector('[data-tab=study]').hidden = !me.permissions.includes('study:read');
    switchTab('explore');
    await loadList();
  }
  $('loginForm').addEventListener('submit', async ev => {
    ev.preventDefault(); $('loginErr').textContent = '';
    try { const r = await api('/api/login', { body: { user: $('lu').value, password: $('lp').value } });
      $('lp').value = ''; await enter(await api('/api/me')); } catch (e) { $('loginErr').textContent = e.message; }
  });
  $('logout').addEventListener('click', async () => { await api('/api/logout', { body: {} }); S.rec = null; showLogin(); });

  function switchTab(name) {
    document.querySelectorAll('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === name));
    document.querySelectorAll('.tab').forEach(t => t.hidden = t.id !== 'tab-' + name);
    if (name === 'explore' && S.viewer) { S.viewer.resize(); S.viewer.render(); drawCharts(); }
    if (name === 'compare') renderCompare();
    if (name === 'study') renderStudy();
    if (name === 'audit') renderAudit();
  }
  $('tabs').addEventListener('click', e => { if (e.target.dataset.tab) switchTab(e.target.dataset.tab); });

  // ---------------------------------------------------------------- structure list
  async function loadList(select) {
    S.list = await api('/api/structures');
    const box = clear($('structList'));
    for (const s of S.list) {
      box.append(h('div', { class: 'item', 'data-id': s.id, onclick: () => openStructure(s.id) },
        h('b', {}, s.id, s.synthetic ? h('span', { class: 'tag' }, 'synthetic') : h('span', { class: 'tag' }, 'imported')),
        h('span', {}, s.name + ' - ' + s.n_pockets + ' pocket(s)')));
    }
    if (select || !S.rec) await openStructure(select || (S.list[0] && S.list[0].id));
  }

  async function openStructure(id) {
    if (!id) return;
    S.rec = await api('/api/structures/' + encodeURIComponent(id));
    S.pocketIdx = bestPocketIdx(S.rec);
    document.querySelectorAll('#structList .item').forEach(i => i.classList.toggle('on', i.dataset.id === id));
    const sc = clear($('scenario'));
    sc.append(h('b', {}, S.rec.id + ': '), S.rec.name + '. ', S.rec.description || '',
      h('div', { class: 'muted small', style: 'margin-top:4px' }, 'Pocket method: ' + (S.rec.pocket_method || 'n/a') + (S.rec.synthetic ? ' - all numbers are made up for illustration.' : '')));
    buildViewer(); renderPocketTabs(); renderXcheck(); drawCharts();
  }
  function bestPocketIdx(rec) {  // start on the most 'interesting' pocket: highest druggability
    let b = 0; rec.pockets.forEach((p, i) => { if (p.druggability > rec.pockets[b].druggability) b = i; }); return b;
  }

  // ---------------------------------------------------------------- 3D viewer
  function pocketMap(rec) {  // resi -> [pocket indices]
    const m = {}; rec.pockets.forEach((p, i) => p.lining.forEach(r => (m[r] = m[r] || []).push(i))); return m;
  }
  function buildViewer() {
    const rec = S.rec, el = $('viewer');
    if (!S.viewer) S.viewer = $3Dmol.createViewer(el, { backgroundColor: '#0b0f14', antialias: true });
    const v = S.viewer; v.clear(); v.removeAllShapes(); S.marker = null;
    v.addModel(rec.pdb, 'pdb');
    v.zoomTo(); applyLayers(true);
    const pm = pocketMap(rec);
    v.setHoverable({}, true, (atom, vw, ev) => {
      if (!atom || atom.resi == null) return;
      const pl = rec.plddt[atom.resi - 1], res = rec.residues[atom.resi - 1] || {};
      const ps = (pm[atom.resi] || []).map(i => rec.pockets[i].id);
      const tip = $('tip'); clear(tip);
      tip.append(h('b', {}, (res.resn || atom.resn) + ' ' + atom.resi), '  pLDDT ' + pl.toFixed(1) + ' (' + bandOf(pl).key + ')  ',
        h('span', { style: 'color:' + (ps.length ? '#26d07c' : '#8da0b6') }, ps.length ? 'in pocket ' + ps.join(', ') : 'not in a pocket'));
      const r = $('viewerWrap').getBoundingClientRect();
      tip.style.left = Math.min(ev.clientX - r.left + 14, r.width - 260) + 'px'; tip.style.top = (ev.clientY - r.top + 14) + 'px'; tip.hidden = false;
    }, () => { $('tip').hidden = true; });
    v.setClickable({}, true, atom => { const ps = pm[atom.resi]; if (ps) { S.pocketIdx = ps[0]; renderPocketTabs(); renderXcheck(); applyLayers(); drawCharts(); } });
    v.render();
  }
  function applyLayers(noRender) {
    const v = S.viewer, rec = S.rec; if (!v || !rec) return;
    const layer = S.layer, pm = pocketMap(rec), fade = $('optLow').checked;
    v.setStyle({}, {}); v.removeAllShapes(); S.marker = null;
    const confColor = a => {
      const pl = rec.plddt[a.resi - 1];
      if (fade && pl >= 70) return '#3a4452';
      return bandOf(pl).color;
    };
    const lineSel = [];
    rec.pockets.forEach((p, i) => p.lining.forEach(r => lineSel.push(r)));
    if (layer === 'pocket') {
      v.setStyle({}, { cartoon: { color: '#56616f', opacity: 0.8 } });
    } else {
      v.setStyle({}, { cartoon: { colorfunc: confColor } });
    }
    if ($('optLining').checked && layer !== 'conf') {
      rec.pockets.forEach((p, i) => {
        const sel = i === S.pocketIdx;
        const color = POCKET_COLORS[i % POCKET_COLORS.length];
        const st = layer === 'pocket' ? { stick: { color, radius: sel ? 0.22 : 0.12 }, cartoon: { color: '#56616f', opacity: 0.8 } }
          : { stick: { colorfunc: a => bandOf(rec.plddt[a.resi - 1]).color, radius: sel ? 0.22 : 0.12 }, cartoon: { colorfunc: confColor } };
        v.setStyle({ resi: p.lining, not: { atom: ['N', 'C', 'O'] } }, st);
      });
    }
    if (layer === 'conf' && $('optLining').checked === false) { /* plain confidence */ }
    if ($('optSpheres').checked && layer !== 'conf') {
      rec.pockets.forEach((p, i) => {
        const color = POCKET_COLORS[i % POCKET_COLORS.length], sel = i === S.pocketIdx;
        p.spheres.forEach(s => v.addSphere({ center: { x: s[0], y: s[1], z: s[2] }, radius: s[3], color, alpha: sel ? 0.42 : 0.14 }));
      });
    }
    v.spin($('optSpin').checked ? 'y' : false);
    if (!noRender) v.render();
    renderLegend();
  }
  function renderLegend() {
    const lg = clear($('legend'));
    if (S.layer !== 'pocket') BANDS.forEach(b => lg.append(h('div', {}, h('span', { class: 'sw', style: 'background:' + b.color }), 'pLDDT ' + b.label)));
    if (S.layer !== 'conf') S.rec.pockets.forEach((p, i) => lg.append(h('div', {}, h('span', { class: 'sw', style: 'background:' + POCKET_COLORS[i % POCKET_COLORS.length] + ';opacity:.8' }),
      p.id + ' ' + p.name + ' (drug. ' + p.druggability.toFixed(2) + ')')));
  }
  function highlightResidue(resi) {
    const v = S.viewer; if (!v || !S.rec) return;
    if (S.marker) { v.removeShape(S.marker); S.marker = null; }
    if (resi != null) {
      const ca = v.selectedAtoms({ resi: resi, atom: 'CA' })[0];
      if (ca) S.marker = v.addSphere({ center: { x: ca.x, y: ca.y, z: ca.z }, radius: 1.6, color: '#ffffff', alpha: 0.9 });
    }
    v.render();
  }
  document.querySelectorAll('#layerSeg button').forEach(b => b.addEventListener('click', () => {
    S.layer = b.dataset.layer; document.querySelectorAll('#layerSeg button').forEach(x => x.classList.toggle('on', x === b)); applyLayers();
  }));
  ['optSpheres', 'optLining', 'optLow', 'optSpin'].forEach(id => $(id).addEventListener('change', () => applyLayers()));
  $('vReset').addEventListener('click', () => { S.viewer.zoomTo(); S.viewer.render(); });
  $('vZoom').addEventListener('click', () => { const p = S.rec.pockets[S.pocketIdx]; if (p) { S.viewer.zoomTo({ resi: p.lining }, 600); } });
  $('vShot').addEventListener('click', () => { const a = h('a', { href: S.viewer.pngURI(), download: S.rec.id + '.png' }); a.click(); });

  // ---------------------------------------------------------------- cross-check panel
  function renderPocketTabs() {
    const box = clear($('pocketTabs'));
    S.rec.pockets.forEach((p, i) => box.append(h('button', { class: i === S.pocketIdx ? 'on' : '', onclick: () => { S.pocketIdx = i; renderPocketTabs(); renderXcheck(); applyLayers(); drawCharts(); } },
      p.id)));
    if (!S.rec.pockets.length) box.append(h('span', { class: 'muted' }, 'No pockets detected'));
  }
  function renderXcheck() {
    const box = clear($('xcheck')), rec = S.rec, p = rec.pockets[S.pocketIdx];
    if (!p) { box.append(h('p', { class: 'muted' }, 'No pocket was detected in this structure.'), kvGlobal(rec)); return; }
    const s = p.stats, cls = s.agree === true ? 'agree' : s.agree === false ? 'disagree' : 'mixed';
    box.append(h('div', { style: 'font-weight:600' }, p.id + ' - ' + p.name),
      h('div', { class: 'muted small' }, p.note || ''),
      h('div', { class: 'verdict ' + cls }, s.verdict),
      kv([['Druggability score' + (rec.synthetic ? ' (made up)' : ' (toy)'), p.druggability.toFixed(2) + (s.druggable ? '  \u2265 0.5' : '  < 0.5')],
        ['Lining residues', s.n],
        ['Pocket mean pLDDT', s.mean], ['Pocket median pLDDT', s.median], ['Pocket min pLDDT', s.min],
        ['Residues < 70', s.below70.length + ' (' + Math.round(s.frac_below70 * 100) + '%)'],
        ['Residues < 50', s.below50.length],
        ['GLOBAL mean pLDDT', s.global_mean], ['Pocket minus global', (s.gap > 0 ? '+' : '') + s.gap]]),
      h('div', { class: 'contrast' }, s.contrast),
      h('div', { class: 'muted small', style: 'margin-top:8px' }, 'Rule used: confident = mean \u2265 70, \u2264 25% of lining < 70, none < 50. Druggable = score \u2265 0.5.'));
    if (s.below70.length) box.append(h('div', { class: 'small', style: 'margin-top:6px' }, h('span', { class: 'muted' }, 'Low-confidence lining residues: '), s.below70.slice(0, 40).join(', ') + (s.below70.length > 40 ? ' ...' : '')));
  }
  function kv(rows) { const d = h('div', { class: 'kv' }); rows.forEach(r => d.append(h('span', {}, r[0]), h('b', {}, r[1]))); return d; }
  function kvGlobal(rec) { return kv([['GLOBAL mean pLDDT', rec.global_mean]]); }

  // ---------------------------------------------------------------- charts
  function ctx(canvas, hgt) {
    const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth || canvas.width;
    canvas.style.height = hgt + 'px'; canvas.width = w * dpr; canvas.height = hgt * dpr;
    const c = canvas.getContext('2d'); c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, w, hgt); return { c, w, hgt };
  }
  function drawCharts() { if (S.rec) { drawTrace(); drawMix(); } }
  function drawTrace() {
    const rec = S.rec, cv = $('trace'), { c, w, hgt } = ctx(cv, 150), pl = rec.plddt, n = pl.length;
    const L = 30, R = 6, T = 6, B = 16, pw = w - L - R, ph = hgt - T - B;
    const X = i => L + (i - 0.5) / n * pw, Y = v => T + (1 - v / 100) * ph;
    BANDS.forEach((b, i) => { const top = i === 0 ? 100 : BANDS[i - 1].min; c.fillStyle = b.color + '22'; c.fillRect(L, Y(top), pw, Y(b.min) - Y(top)); });
    const p = rec.pockets[S.pocketIdx];
    if (p) { c.fillStyle = POCKET_COLORS[S.pocketIdx % 5] + '66'; p.lining.forEach(r => c.fillRect(X(r) - pw / n / 2, T, Math.max(1.5, pw / n), ph)); }
    c.strokeStyle = '#2a3646'; c.fillStyle = '#8da0b6'; c.font = '10px sans-serif'; c.textAlign = 'right';
    [0, 50, 70, 90, 100].forEach(v => { c.beginPath(); c.moveTo(L, Y(v)); c.lineTo(L + pw, Y(v)); c.stroke(); c.fillText(v, L - 3, Y(v) + 3); });
    c.beginPath(); pl.forEach((v, i) => i ? c.lineTo(X(i + 1), Y(v)) : c.moveTo(X(1), Y(v))); c.strokeStyle = '#e6edf5'; c.lineWidth = 1.5; c.stroke(); c.lineWidth = 1;
    c.strokeStyle = '#ffffff88'; c.setLineDash([4, 3]); c.beginPath(); c.moveTo(L, Y(rec.global_mean)); c.lineTo(L + pw, Y(rec.global_mean)); c.stroke(); c.setLineDash([]);
    c.fillStyle = '#fff'; c.textAlign = 'left'; c.fillText('global mean ' + rec.global_mean, L + 4, Y(rec.global_mean) - 3);
    c.textAlign = 'center'; c.fillStyle = '#8da0b6'; c.fillText('residue number (1 - ' + n + ')', L + pw / 2, hgt - 3);
    cv.onmousemove = ev => { const r = cv.getBoundingClientRect(), i = Math.max(1, Math.min(n, Math.round((ev.clientX - r.left - L) / pw * n + 0.5))); highlightResidue(i); cv.title = 'residue ' + i + '  pLDDT ' + pl[i - 1]; };
    cv.onmouseleave = () => highlightResidue(null);
  }
  function drawMix() {
    const rec = S.rec, cv = $('mix'), { c, w, hgt } = ctx(cv, 120), p = rec.pockets[S.pocketIdx];
    const rows = [['Whole chain', rec.global_bands, rec.plddt.length]];
    if (p) rows.unshift(['Pocket ' + p.id, p.stats.bands, p.stats.n]);
    rows.forEach((r, k) => {
      const y = 12 + k * 52; let x = 0; c.fillStyle = '#e6edf5'; c.font = '11px sans-serif'; c.textAlign = 'left'; c.fillText(r[0] + ' (' + r[2] + ' residues)', 0, y - 2);
      BANDS.forEach(b => { const f = (r[1][b.key] || 0) / r[2], ww = f * w; c.fillStyle = b.color; c.fillRect(x, y + 3, ww, 26);
        if (ww > 26) { c.fillStyle = b.key === 'very high' ? '#fff' : '#111'; c.fillText(Math.round(f * 100) + '%', x + 4, y + 21); } x += ww; });
    });
  }
  window.addEventListener('resize', () => { if (S.viewer) S.viewer.resize(); drawCharts(); if (!$('tab-compare').hidden) renderCompare(); });

  // ---------------------------------------------------------------- import
  async function afterImport(id) { await loadList(id); }
  $('afForm').addEventListener('submit', async ev => {
    ev.preventDefault(); const m = $('importMsg'); m.textContent = 'Downloading from AlphaFold DB ...';
    try { const r = await api('/api/import/afdb', { body: { id: $('afId').value } }); m.textContent = 'Loaded ' + r.id; await afterImport(r.id); }
    catch (e) { m.textContent = e.message; }
  });
  $('pdbFile').addEventListener('change', async ev => {
    const f = ev.target.files[0]; if (!f) return; const m = $('importMsg');
    if (f.size > 4e6) { m.textContent = 'File too large (4 MB max).'; return; }
    m.textContent = 'Analysing ...';
    try { const r = await api('/api/import/upload', { body: { filename: f.name, pdb: await f.text() } }); m.textContent = 'Loaded ' + r.id; await afterImport(r.id); }
    catch (e) { m.textContent = e.message; } ev.target.value = '';
  });
  $('csv').addEventListener('click', async () => {
    const t = await api('/api/export/' + encodeURIComponent(S.rec.id) + '.csv');
    const a = h('a', { href: URL.createObjectURL(new Blob([t], { type: 'text/csv' })), download: S.rec.id + '-plddt-pockets.csv' }); a.click();
  });

  // ---------------------------------------------------------------- compare tab
  async function renderCompare() {
    if (!S.all) { S.all = []; for (const s of S.list) S.all.push(await api('/api/structures/' + encodeURIComponent(s.id))); }
    const pts = [];
    S.all.forEach(r => r.pockets.forEach((p, i) => pts.push({ rec: r, p, i, x: p.stats.mean, y: p.druggability, g: r.global_mean })));
    const cv = $('scatter'), W = cv.clientWidth || 760, { c } = ctx(cv, 420), L = 44, T = 14, pw = W - L - 14, ph = 420 - T - 38;
    const X = v => L + (v - 30) / 70 * pw, Y = v => T + (1 - v) * ph;
    c.fillStyle = '#12281f'; c.fillRect(X(70), T, X(100) - X(70), Y(0.5) - T);
    c.fillStyle = '#331818'; c.fillRect(X(30), T, X(70) - X(30), Y(0.5) - T);
    c.fillStyle = '#8da0b6'; c.font = '11px sans-serif'; c.textAlign = 'left';
    c.fillText('druggable + confident: agree', X(71), T + 14); c.fillText('druggable but low confidence: disagree', X(31), T + 14);
    c.fillText('confident but not druggable', X(71), Y(0) - 6); c.fillText('low-confidence + undruggable: noise', X(31), Y(0) - 6);
    c.strokeStyle = '#4a5a70'; c.setLineDash([4, 4]); c.beginPath(); c.moveTo(X(70), T); c.lineTo(X(70), T + ph); c.moveTo(L, Y(0.5)); c.lineTo(L + pw, Y(0.5)); c.stroke(); c.setLineDash([]);
    c.strokeStyle = '#2a3646'; c.textAlign = 'center';
    [30, 40, 50, 60, 70, 80, 90, 100].forEach(v => c.fillText(v, X(v), T + ph + 14)); c.fillText('mean pLDDT of pocket-lining residues (hollow ring = global mean of the same model)', L + pw / 2, T + ph + 30);
    c.textAlign = 'right'; [0, 0.25, 0.5, 0.75, 1].forEach(v => c.fillText(v, L - 5, Y(v) + 3));
    c.save(); c.translate(11, T + ph / 2); c.rotate(-Math.PI / 2); c.textAlign = 'center'; c.fillText('druggability score', 0, 0); c.restore();
    pts.forEach(pt => { c.strokeStyle = '#8da0b6'; c.lineWidth = 1; c.beginPath(); c.moveTo(X(pt.x), Y(pt.y)); c.lineTo(X(pt.g), Y(pt.y)); c.stroke();
      c.beginPath(); c.arc(X(pt.g), Y(pt.y), 6, 0, 7); c.strokeStyle = '#fff'; c.stroke();
      c.beginPath(); c.arc(X(pt.x), Y(pt.y), 7, 0, 7); c.fillStyle = POCKET_COLORS[pt.i % 5]; c.fill(); c.strokeStyle = '#000'; c.stroke();
      c.fillStyle = '#e6edf5'; c.textAlign = 'left'; c.fillText(pt.rec.id.replace('SYN-', '') + ' ' + pt.p.id, X(pt.x) + 10, Y(pt.y) - 8); pt.cx = X(pt.x); pt.cy = Y(pt.y); });
    cv.onclick = ev => { const r = cv.getBoundingClientRect(), mx = ev.clientX - r.left, my = ev.clientY - r.top; const hit = pts.find(p => Math.hypot(p.cx - mx, p.cy - my) < 12);
      if (hit) { switchTab('explore'); openStructure(hit.rec.id).then(() => { S.pocketIdx = hit.i; renderPocketTabs(); renderXcheck(); applyLayers(); drawCharts(); }); } };
    const t = h('table', {}, h('tr', {}, ['Structure', 'Pocket', 'Druggability', 'Pocket mean', 'Pocket min', '<70', '<50', 'Global mean', 'Verdict'].map((x, i) => h('th', { class: i > 1 && i < 8 ? 'n' : '' }, x))));
    pts.forEach(pt => { const s = pt.p.stats; t.append(h('tr', { class: 'click', onclick: () => { switchTab('explore'); openStructure(pt.rec.id).then(() => { S.pocketIdx = pt.i; renderPocketTabs(); renderXcheck(); applyLayers(); drawCharts(); }); } },
      h('td', {}, pt.rec.id), h('td', {}, pt.p.id + ' ' + pt.p.name), h('td', { class: 'n' }, pt.y.toFixed(2)), h('td', { class: 'n' }, s.mean), h('td', { class: 'n' }, s.min),
      h('td', { class: 'n' }, s.below70.length), h('td', { class: 'n' }, s.below50.length), h('td', { class: 'n' }, s.global_mean), h('td', {}, s.kind))); });
    clear($('cmpTable')).append(t);
  }

  // ---------------------------------------------------------------- study tab
  async function renderStudy() {
    const box = clear($('studyBody'));
    const st = await api('/api/study');
    if (!st.rows.length && !st.notes) {
      box.append(h('p', {}, 'No study result files have been loaded yet.'),
        h('p', { class: 'muted' }, 'When the Scientist\'s de-identified results are available, drop the CSV (one row per pocket; columns such as target, pocket_id, druggability, pocket_mean_plddt, pocket_min_plddt, frac_below_70, global_mean_plddt, label) and an optional summary .md into data/study/, delete data/store/study.enc and restart. They then appear here with the study\'s actual numbers.'));
      return;
    }
    if (st.notes) box.append(h('pre', { style: 'white-space:pre-wrap;background:#0e131a;padding:10px;border-radius:8px' }, st.notes));
    if (st.rows.length) {
      const cols = Object.keys(st.rows[0]);
      box.append(h('p', { class: 'muted small' }, st.rows.length + ' rows (study data, not the synthetic demo).'));
      const cv = h('canvas', { width: 700, height: 360 });
      if (cols.includes('druggability') && cols.includes('pocket_mean_plddt')) { box.append(cv); setTimeout(() => studyScatter(cv, st.rows), 0); }
      const t = h('table', {}, h('tr', {}, cols.map(k => h('th', {}, k))));
      st.rows.slice(0, 500).forEach(r => t.append(h('tr', {}, cols.map(k => h('td', {}, r[k])))));
      box.append(t);
    }
  }
  function studyScatter(cv, rows) {
    const { c, w } = ctx(cv, 360), L = 44, T = 10, pw = w - L - 12, ph = 360 - T - 36, X = v => L + (v - 30) / 70 * pw, Y = v => T + (1 - v) * ph;
    c.strokeStyle = '#4a5a70'; c.setLineDash([4, 4]); c.beginPath(); c.moveTo(X(70), T); c.lineTo(X(70), T + ph); c.moveTo(L, Y(.5)); c.lineTo(L + pw, Y(.5)); c.stroke(); c.setLineDash([]);
    c.fillStyle = '#8da0b6'; c.font = '11px sans-serif'; c.textAlign = 'center'; [30, 50, 70, 90, 100].forEach(v => c.fillText(v, X(v), T + ph + 14));
    c.fillText('pocket mean pLDDT', L + pw / 2, T + ph + 30); c.textAlign = 'right'; [0, .5, 1].forEach(v => c.fillText(v, L - 5, Y(v) + 3));
    const labels = [...new Set(rows.map(r => r.label || ''))], cols = ['#26d07c', '#ff6b6b', '#4da3ff', '#ffc233'];
    rows.forEach(r => { const x = parseFloat(r.pocket_mean_plddt), y = parseFloat(r.druggability); if (isNaN(x) || isNaN(y)) return;
      c.beginPath(); c.arc(X(Math.max(30, x)), Y(y), 5, 0, 7); c.fillStyle = cols[labels.indexOf(r.label || '') % 4] + 'cc'; c.fill();
      const g = parseFloat(r.global_mean_plddt); if (!isNaN(g)) { c.beginPath(); c.arc(X(Math.max(30, g)), Y(y), 4, 0, 7); c.strokeStyle = '#ffffff55'; c.stroke(); } });
    c.textAlign = 'left'; labels.forEach((l, i) => { c.fillStyle = cols[i % 4]; c.fillText('\u25CF ' + (l || 'unlabelled'), L + 8 + i * 160, T + 12); });
  }

  // ---------------------------------------------------------------- audit tab
  async function renderAudit() {
    const d = await api('/api/audit'), badge = $('auditBadge');
    badge.className = 'badge ' + (d.verify.ok ? 'ok' : 'bad');
    badge.textContent = d.verify.ok ? 'chain verified (' + d.verify.entries + ' entries)' : 'TAMPERING DETECTED at line ' + d.verify.broken_at_line + ': ' + d.verify.reason;
    const t = h('table', {}, h('tr', {}, ['#', 'time (UTC)', 'user', 'role', 'action', 'resource', 'outcome', 'ip'].map(x => h('th', {}, x))));
    d.entries.forEach(e => t.append(h('tr', {}, [e.seq, e.ts, e.user, e.role, e.action, e.resource, e.outcome, e.ip].map(x => h('td', {}, x)))));
    clear($('auditBody')).append(t);
  }

  // ---------------------------------------------------------------- start
  (async function init() {
    try { await enter(await api('/api/me')); } catch (e) { showLogin(); }
  })();
})();
