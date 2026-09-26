/* Flowbench — infinite canvas for wiring local Python functions into workflows. No build step. */
(function () {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const cap = (el, id) => { try { el.setPointerCapture(id); } catch (e) { /* pointer already gone */ } };
  const uid = () => Math.random().toString(36).slice(2, 9);
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } }
  };
  const base = p => String(p).split(/[\\/]/).pop();
  const dir = p => String(p).replace(/[\\/][^\\/]*$/, '');
  let toastT = 0;
  function toast(m, ms = 2400) { const t = $('#toast'); t.textContent = m; t.classList.add('show'); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove('show'), ms); }

  /* ───────── State ───────── */
  const KIND = {
    func: { label: 'FUNCTION', w: 310 }, value: { label: 'VALUE', w: 250 }, code: { label: 'CODE', w: 340, h: 280 },
    viewer: { label: 'VIEWER', w: 400, h: 320 }, terminal: { label: 'TERMINAL', w: 580, h: 340 }, note: { label: 'NOTE', w: 280, h: 170 }
  };
  const S = store.get('flowbench:flow', null) || { nodes: [], edges: [], view: { x: 60, y: 40, k: 1 } };
  const R = {};            // runtime per node: status, preview, logs, error, ms
  const sel = new Set(); let selEdge = null;
  const node = id => S.nodes.find(n => n.id === id);
  const inPorts = n => n.kind === 'func' ? (n.data.params || []).map(p => p.name) : n.kind === 'code' ? (n.data.inputs || '').split(',').map(s => s.trim()).filter(Boolean) : n.kind === 'viewer' ? ['data'] : [];
  const hasOut = n => ['func', 'value', 'code', 'viewer'].includes(n.kind);

  /* languages for Code nodes (runner.py has the matching runtime table) */
  const LANGS = {
    python: { label: 'Python', tier: 'vars', image: 'python:3.12-slim', tpl: '# inputs become variables; assign your output to `result`\nresult = x' },
    javascript: { label: 'JavaScript', short: 'JS', tier: 'vars', image: 'node:22-alpine', tpl: '// inputs are variables; set `result` to pass data on\nresult = x.map(v => v * 2);' },
    typescript: { label: 'TypeScript', short: 'TS', tier: 'vars', image: 'node:24-alpine', tpl: '// inputs are variables; set `result` to pass data on\nconst xs: number[] = x;\nresult = xs.map((v: number) => v * 2);' },
    powershell: { label: 'PowerShell', short: 'PS', tier: 'prog', image: 'mcr.microsoft.com/powershell', tpl: '# $env:FLOW_IN is a JSON file with the inputs; whatever you print is the result\n$in = Get-Content $env:FLOW_IN -Raw | ConvertFrom-Json\n@{ count = @($in.x).Count } | ConvertTo-Json -Compress' },
    bash: { label: 'Bash', short: 'SH', tier: 'prog', image: 'bash:5', tpl: '# $FLOW_IN is a JSON file with the inputs; whatever you print is the result\n# images saved into $FLOW_OUT show up on the canvas\necho "{\\"inputs\\": $(cat "$FLOW_IN")}"' },
    rust: { label: 'Rust', short: 'RS', tier: 'prog', image: 'rust:1-slim', tpl: 'use std::{env, fs};\n\nfn main() {\n    // $FLOW_IN is a JSON file with the inputs; print the result (JSON is parsed)\n    let input = fs::read_to_string(env::var("FLOW_IN").unwrap()).unwrap_or_default();\n    let sum: u64 = (1..=100).sum();\n    println!("{{\\"sum\\": {}, \\"input_bytes\\": {}}}", sum, input.len());\n}' },
    c: { label: 'C', tier: 'prog', image: 'gcc:14', tpl: '#include <stdio.h>\n#include <math.h>\n\nint main(void) {\n    /* getenv("FLOW_IN") is a JSON file with the inputs; print the result */\n    printf("{\\"sqrt2\\": %.6f}\\n", sqrt(2.0));\n    return 0;\n}' },
    cpp: { label: 'C++', tier: 'prog', image: 'gcc:14', tpl: '#include <iostream>\n#include <numeric>\n#include <vector>\n\nint main() {\n    // getenv("FLOW_IN") is a JSON file with the inputs; print the result\n    std::vector<int> v{1, 2, 3, 4};\n    std::cout << "{\\"sum\\": " << std::accumulate(v.begin(), v.end(), 0) << "}" << std::endl;\n}' },
    go: { label: 'Go', tier: 'prog', image: 'golang:1.23-alpine', tpl: 'package main\n\nimport (\n\t"encoding/json"\n\t"fmt"\n\t"os"\n)\n\nfunc main() {\n\t// $FLOW_IN is a JSON file with the inputs; print the result (JSON is parsed)\n\traw, _ := os.ReadFile(os.Getenv("FLOW_IN"))\n\tvar in map[string]any\n\tjson.Unmarshal(raw, &in)\n\tout, _ := json.Marshal(map[string]any{"inputs": len(in)})\n\tfmt.Println(string(out))\n}' },
    r: { label: 'R', tier: 'prog', image: 'r-base', tpl: '# Sys.getenv("FLOW_IN") is a JSON file with the inputs; whatever you print is the result\n# plots saved into FLOW_OUT appear on the canvas\npng(file.path(Sys.getenv("FLOW_OUT"), "plot.png"), width = 640, height = 360)\nplot(sin(seq(0, 10, 0.1)), type = "l", col = "red")\ninvisible(dev.off())\ncat(sum(1:10))' },
    julia: { label: 'Julia', short: 'JL', tier: 'prog', image: 'julia:1', tpl: '# ENV["FLOW_IN"] is a JSON file with the inputs; print the result\nprintln(sum(1:10))' },
    java: { label: 'Java', tier: 'prog', image: 'eclipse-temurin:21', tpl: '// System.getenv("FLOW_IN") is a JSON file with the inputs; print the result\npublic class Main {\n    public static void main(String[] args) {\n        System.out.println("{\\"answer\\": " + (6 * 7) + "}");\n    }\n}' },
    shell: { label: 'Shell (container)', short: 'SH', tier: 'prog', image: 'alpine:3', tpl: '# runs inside the container image; $FLOW_IN is a JSON file with the inputs\necho "hello from $(uname -sm)"' }
  };
  const langOf = n => (n.data && n.data.lang) || 'python';
  const isTpl = code => !String(code || '').trim() || Object.values(LANGS).some(L => L.tpl === code);
  let saveT = 0;
  function save() { clearTimeout(saveT); saveT = setTimeout(() => store.set('flowbench:flow', S), 300); }

  /* history */
  const H = { past: [], future: [] };
  const snap = () => JSON.stringify({ nodes: S.nodes, edges: S.edges });
  let lastSnap = snap();
  function commit() { const s = snap(); if (s === lastSnap) return; H.past.push(lastSnap); if (H.past.length > 80) H.past.shift(); H.future = []; lastSnap = s; save(); drawMinimap(); }
  function restore(s) { const o = JSON.parse(s); S.nodes = o.nodes; S.edges = o.edges; lastSnap = s; sel.clear(); selEdge = null; renderAll(); save(); }
  function undo() { if (!H.past.length) return; H.future.push(snap()); restore(H.past.pop()); }
  function redo() { if (!H.future.length) return; H.past.push(snap()); restore(H.future.pop()); }

  /* ───────── Runner connection ───────── */
  // Served by runner.py itself (http://127.0.0.1:<port>/)? Then the runner is on the same host.
  const LOCAL = location.protocol === 'http:' && /^(127\.0\.0\.1|localhost)(:\d+)?$/.test(location.host);
  const RC = { url: LOCAL ? 'ws://' + location.host : store.get('flowbench:url', 'ws://127.0.0.1:8765'), token: store.get('flowbench:token', ''), ws: null, ok: false, info: null, pend: new Map(), seq: 0, retry: 0, timer: 0 };
  (function readHash() {
    const h = new URLSearchParams(location.hash.slice(1));
    if (h.get('token')) { RC.token = h.get('token'); RC.url = h.get('runner') || RC.url; store.set('flowbench:token', RC.token); store.set('flowbench:url', RC.url); history.replaceState(null, '', location.pathname); }
  })();
  function setStatus(s) {
    const led = $('#runner-led'), lab = $('#runner-label');
    led.className = 'led' + (s === 'on' ? ' on' : s === 'busy' ? ' busy' : '');
    lab.textContent = s === 'on' ? 'RUNNER · PY ' + (RC.info ? RC.info.pyversion : '') : s === 'busy' ? 'RUNNING…' : s === 'connecting' ? 'CONNECTING…' : 'RUNNER OFFLINE';
  }
  function connect() {
    clearTimeout(RC.timer);
    if (!RC.token) { setStatus('off'); return; }
    if (RC.ws && RC.ws.readyState <= 1) return;
    setStatus('connecting');
    let ws;
    try { ws = new WebSocket(RC.url); } catch (e) { setStatus('off'); return; }
    RC.ws = ws;
    ws.onopen = async () => {
      const r = await req('hello', { token: RC.token });
      if (!r.ok) { toast('Runner refused the token. Paste the token printed by runner.py'); RC.token = ''; store.set('flowbench:token', ''); openConnect(); return; }
      RC.ok = true; RC.info = r; RC.retry = 0; setStatus('on'); toast('Connected to runner · ' + r.roots.length + ' folder' + (r.roots.length > 1 ? 's' : ''));
      $('#connect-modal').hidden = true;
      LIB.roots = r.roots; LIB.index = null; renderLib();
      S.nodes.filter(n => n.kind === 'code').forEach(renderNode);
      S.nodes.filter(n => n.kind === 'terminal').forEach(n => startTerm(n));
    };
    ws.onmessage = e => { let m; try { m = JSON.parse(e.data); } catch { return; } onMsg(m); };
    ws.onclose = () => {
      const was = RC.ok;
      if (!was && !LOCAL && RC.retry === 0) toast('This website can’t reach the runner (your browser blocks it). Open the local address runner.py prints: http://127.0.0.1:8765', 8000); RC.ok = false; RC.ws = null; setStatus('off');
      RC.pend.forEach(p => p({ ok: false, error: 'Runner disconnected' })); RC.pend.clear();
      Object.values(TERMS).forEach(t => { t.alive = false; });
      if (was) { toast('Runner disconnected'); renderLib(); }
      if (RC.token) { RC.retry = Math.min(RC.retry + 1, 6); RC.timer = setTimeout(connect, 1500 * RC.retry); }
    };
  }
  function req(type, payload = {}) {
    return new Promise(res => {
      if (!RC.ws || RC.ws.readyState !== 1) return res({ ok: false, error: 'Runner not connected' });
      const id = ++RC.seq; RC.pend.set(id, res);
      RC.ws.send(JSON.stringify(Object.assign({ id, type }, payload)));
    });
  }
  const tell = (type, payload) => { if (RC.ws && RC.ws.readyState === 1) RC.ws.send(JSON.stringify(Object.assign({ type }, payload))); };
  function onMsg(m) {
    if (m.re && RC.pend.has(m.re)) { const p = RC.pend.get(m.re); RC.pend.delete(m.re); p(m); }
    if (m.type === 'node') onNodeEvent(m);
    else if (m.type === 'stream') onStream(m);
    else if (m.type === 'term-data') { const t = termByTid(m.tid); if (t) t.xt.write(m.data); }
    else if (m.type === 'term-exit') { const t = termByTid(m.tid); if (t) { t.alive = false; t.xt.write('\r\n\x1b[2m[process exited' + (m.code != null ? ' with code ' + m.code : '') + ' · click restart in the node header]\x1b[0m\r\n'); } }
  }
  function openConnect() { $('#rc-url').value = RC.url; $('#rc-token').value = ''; $('#connect-modal').hidden = false; }
  $('#runner-pill').onclick = () => { if (RC.ok) toast('Python ' + RC.info.python + ' · folders: ' + RC.info.roots.join(', '), 5000); else openConnect(); };
  $('#rc-connect').onclick = () => {
    RC.url = $('#rc-url').value.trim() || RC.url; RC.token = $('#rc-token').value.trim();
    store.set('flowbench:url', RC.url); store.set('flowbench:token', RC.token);
    if (RC.ws) try { RC.ws.close(); } catch { /* ignore */ }
    RC.ws = null; RC.retry = 0; connect();
  };
  document.addEventListener('click', e => { const c = e.target.closest('[data-close]'); if (c) c.closest('.modal').hidden = true; });
  $$('.modal').forEach(m => m.addEventListener('pointerdown', e => { if (e.target === m) m.hidden = true; }));

  /* ───────── Library ───────── */
  const LIB = { roots: [], open: new Set(store.get('flowbench:open', [])), kids: {}, funcs: {}, index: null, indexAt: 0 };
  async function listDir(p) { if (LIB.kids[p]) return LIB.kids[p]; const r = await req('ls', { path: p }); LIB.kids[p] = r.ok ? r.items : []; return LIB.kids[p]; }
  async function scanFile(p, fresh) { if (LIB.funcs[p] && !fresh) return LIB.funcs[p]; const r = await req('scan', { path: p }); LIB.funcs[p] = r.ok ? r.funcs : []; if (!r.ok) toast(r.error); return LIB.funcs[p]; }
  async function renderLib() {
    const tree = $('#lib-tree');
    if (!RC.ok) {
      tree.innerHTML = `<div class="lib-empty">Your Python files will appear here once the local runner is connected.<br><button class="pill primary" id="lib-connect">CONNECT RUNNER</button></div>`;
      $('#lib-connect').onclick = openConnect; return;
    }
    const q = $('#lib-search').value.trim().toLowerCase();
    if (q) return renderSearch(q);
    const html = await Promise.all(LIB.roots.map(r => dirHTML(r, r.split(/[\\/]/).pop() || r, 0)));
    tree.innerHTML = html.join('');
  }
  async function dirHTML(p, name, depth) {
    const open = LIB.open.has(p) || depth === 0 && !LIB.open.has('!' + p);
    let h = `<div class="ti dir" data-dir="${esc(p)}" title="${esc(p)}"><span class="tw">${ICON(open ? 'chevron-down' : 'chevron-right')}</span><span class="ic">${ICON('folder')}</span>${esc(name)}</div>`;
    if (!open) return h;
    const items = await listDir(p), kids = [];
    for (const it of items) {
      if (it.dir) kids.push(await dirHTML(it.path, it.name, depth + 1));
      else {
        const fo = LIB.open.has(it.path);
        if (it.lang) { kids.push(scriptItem(it)); continue; }
        kids.push(`<div class="ti file" data-file="${esc(it.path)}" title="${esc(it.path)}"><span class="tw">${ICON(fo ? 'chevron-down' : 'chevron-right')}</span><span class="ic">${ICON('file')}</span>${esc(it.name)}</div>`);
        if (fo) { const fs = await scanFile(it.path); kids.push(`<div class="kids">${fs.length ? fs.map(f => fnItem(it.path, f)).join('') : '<div class="ti"><small style="color:var(--mute)">no public functions</small></div>'}</div>`); }
      }
    }
    return h + `<div class="kids">${kids.join('') || '<div class="ti"><small style="color:var(--mute)">empty</small></div>'}</div>`;
  }
  const scriptItem = it => `<div class="ti script" draggable="true" data-script="${esc(it.path)}" data-lang="${it.lang}" title="${esc(it.path + '\n\nDrag onto the canvas, or click to add')}"><span class="tw"></span><span class="ic">${ICON('file-code')}</span>${esc(it.name)}<small class="tag">${esc((LANGS[it.lang] || {}).short || (LANGS[it.lang] || {}).label || it.lang)}</small></div>`;
  const fnItem = (file, f) => `<div class="ti fn" draggable="true" data-fn="${esc(f.name)}" data-fnfile="${esc(file)}" title="${esc((f.doc || f.name) + '\n\nDrag onto the canvas, or click to add')}"><span class="tw"></span><span class="ic">${ICON('func')}</span>${esc(f.name)}<small>(${esc(f.params.map(p => p.name).join(', '))})</small></div>`;
  async function ensureIndex() { if (LIB.index && Date.now() - LIB.indexAt < 30000) return LIB.index; const r = await req('index'); LIB.index = r.ok ? r.files : []; LIB.indexAt = Date.now(); return LIB.index; }
  async function renderSearch(q) {
    const tree = $('#lib-tree'); tree.innerHTML = '<div class="lib-empty">Searching…</div>';
    const idx = await ensureIndex(), rows = [];
    idx.forEach(f => f.funcs.forEach(fn => { if (fn.toLowerCase().includes(q) || base(f.path).toLowerCase().includes(q)) rows.push({ file: f.path, fn }); }));
    tree.innerHTML = rows.length ? rows.slice(0, 300).map(r => `<div class="ti fn" draggable="true" data-fn="${esc(r.fn)}" data-fnfile="${esc(r.file)}" title="${esc(r.file)}"><span class="tw"></span><span class="ic">${ICON('func')}</span>${esc(r.fn)}<small>${esc(base(r.file))}</small></div>`).join('') : '<div class="lib-empty">No functions match.</div>';
  }
  $('#lib-tree').addEventListener('click', async e => {
    const d = e.target.closest('[data-dir]'), f = e.target.closest('[data-file]'), fn = e.target.closest('[data-fn]'), sc = e.target.closest('[data-script]');
    if (sc) { const c = viewCenter(); addScriptNode(sc.dataset.script, sc.dataset.lang, c.x - 170, c.y - 120); return; }
    if (d) { const p = d.dataset.dir, isRoot = LIB.roots.includes(p); if (isRoot) { LIB.open.has('!' + p) ? LIB.open.delete('!' + p) : LIB.open.add('!' + p); } else { LIB.open.has(p) ? LIB.open.delete(p) : LIB.open.add(p); } }
    else if (f) { const p = f.dataset.file; LIB.open.has(p) ? LIB.open.delete(p) : LIB.open.add(p); }
    else if (fn) { const c = viewCenter(); await addFuncNode(fn.dataset.fnfile, fn.dataset.fn, c.x - 130, c.y - 60); return; }
    else return;
    store.set('flowbench:open', [...LIB.open]); renderLib();
  });
  $('#lib-tree').addEventListener('dragstart', e => { const sc = e.target.closest('[data-script]'); if (sc) { e.dataTransfer.setData('text/x-flowbench', JSON.stringify({ script: sc.dataset.script, lang: sc.dataset.lang })); e.dataTransfer.effectAllowed = 'copy'; return; } const fn = e.target.closest('[data-fn]'); if (!fn) return; e.dataTransfer.setData('text/x-flowbench', JSON.stringify({ file: fn.dataset.fnfile, func: fn.dataset.fn })); e.dataTransfer.effectAllowed = 'copy'; });
  let sT = 0; $('#lib-search').addEventListener('input', () => { clearTimeout(sT); sT = setTimeout(renderLib, 220); });
  $('#lib-refresh').onclick = () => { LIB.kids = {}; LIB.funcs = {}; LIB.index = null; renderLib(); };
  $('#lib-toggle').onclick = () => { $('#lib').classList.add('hide'); document.body.classList.add('lib-hidden'); setTimeout(applyView, 260); };
  $('#lib-open').onclick = () => { $('#lib').classList.remove('hide'); document.body.classList.remove('lib-hidden'); setTimeout(applyView, 260); };

  async function addFuncNode(file, func, x, y) {
    if (!RC.ok) { openConnect(); return null; }
    const fs = await scanFile(file), f = fs.find(v => v.name === func);
    if (!f) { toast('Function not found: ' + func); return null; }
    return addNode('func', x, y, { file, func, params: f.params, doc: f.doc, ret: f.ret, line: f.line, values: {} }, func);
  }
  function addScriptNode(file, lang, x, y) { return addNode('code', x, y, { lang, file, inputs: '', values: {} }, base(file)); }
  function addNode(kind, x, y, data, title) {
    const k = KIND[kind], n = { id: uid(), kind, x: gsnap(x), y: gsnap(y), w: k.w, title: title || k.label.toLowerCase(), data: data || {} };
    if (k.h) n.h = k.h;
    if (kind === 'code' && !n.data.code && !n.data.file) { const L = LANGS[n.data.lang || 'python']; Object.assign(n.data, { code: L.tpl, inputs: n.data.inputs != null ? n.data.inputs : (L.tier === 'vars' ? 'x' : ''), values: n.data.values || {} }); }
    if (kind === 'code' && n.data.file && n.data.inputs == null) Object.assign(n.data, { inputs: '', values: {} });
    if (kind === 'value' && n.data.expr == null) n.data.expr = '42';
    if (kind === 'terminal') Object.assign(n.data, { shell: n.data.shell || 'default', cwd: n.data.cwd || (RC.info ? RC.info.roots[0] : '') });
    if (kind === 'note' && n.data.text == null) n.data.text = '';
    S.nodes.push(n); sel.clear(); sel.add(n.id); renderNode(n); markSel(); commit();
    if (kind === 'terminal') startTerm(n);
    return n;
  }

  /* ───────── Canvas view ───────── */
  const stage = $('#stage'), world = $('#world');
  const GRID = 20, gsnap = (v, free) => free ? Math.round(v) : Math.round(v / GRID) * GRID;
  // the stage must never scroll natively (focusing an off-screen input would shift everything)
  stage.addEventListener('scroll', () => { if (stage.scrollLeft || stage.scrollTop) { stage.scrollLeft = 0; stage.scrollTop = 0; } });
  function applyView() {
    const v = S.view;
    world.style.transform = `translate(${v.x}px,${v.y}px) scale(${v.k})`;
    stage.style.backgroundPosition = `${v.x}px ${v.y}px`; stage.style.backgroundSize = `${GRID * v.k}px ${GRID * v.k}px`;
    $('#zoom-val').textContent = Math.round(v.k * 100) + '%';
    drawMinimap(); save();
  }
  const toWorld = (cx, cy) => { const r = stage.getBoundingClientRect(); return { x: (cx - r.left - S.view.x) / S.view.k, y: (cy - r.top - S.view.y) / S.view.k }; };
  const viewCenter = () => { const r = stage.getBoundingClientRect(); return toWorld(r.left + r.width / 2, r.top + r.height / 2); };
  function zoomAt(k, cx, cy) {
    const r = stage.getBoundingClientRect(), px = cx - r.left, py = cy - r.top, v = S.view, nk = clamp(k, 0.15, 2.5);
    v.x = px - (px - v.x) * nk / v.k; v.y = py - (py - v.y) * nk / v.k; v.k = nk; applyView();
  }
  function fit() {
    if (!S.nodes.length) { S.view = { x: 60, y: 40, k: 1 }; applyView(); return; }
    const b = bounds(), r = stage.getBoundingClientRect(), pad = 60;
    const k = clamp(Math.min((r.width - pad * 2) / (b.x2 - b.x1), (r.height - pad * 2) / (b.y2 - b.y1)), 0.15, 1.2);
    S.view = { k, x: (r.width - (b.x2 - b.x1) * k) / 2 - b.x1 * k, y: (r.height - (b.y2 - b.y1) * k) / 2 - b.y1 * k }; applyView();
  }
  function bounds() {
    let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
    S.nodes.forEach(n => { const el = nodeEl(n.id), h = el ? el.offsetHeight : 120; x1 = Math.min(x1, n.x); y1 = Math.min(y1, n.y); x2 = Math.max(x2, n.x + n.w); y2 = Math.max(y2, n.y + h); });
    return { x1, y1, x2, y2 };
  }
  $('#zoom-in').onclick = () => { const r = stage.getBoundingClientRect(); zoomAt(S.view.k * 1.2, r.left + r.width / 2, r.top + r.height / 2); };
  $('#zoom-out').onclick = () => { const r = stage.getBoundingClientRect(); zoomAt(S.view.k / 1.2, r.left + r.width / 2, r.top + r.height / 2); };
  $('#zoom-fit').onclick = fit;
  stage.addEventListener('wheel', e => {
    const scrollable = e.target.closest('.n-body, .n-code, .xterm, .n-note, .n-tbl, .n-text, .n-log, .n-err');
    if (e.ctrlKey || e.metaKey) { e.preventDefault(); zoomAt(S.view.k * Math.exp(-e.deltaY * (e.deltaMode ? 0.05 : 0.0022)), e.clientX, e.clientY); return; }
    if (scrollable) return;
    e.preventDefault();
    const f = e.deltaMode ? 30 : 1; S.view.x -= (e.shiftKey ? e.deltaY : e.deltaX) * f; S.view.y -= (e.shiftKey ? 0 : e.deltaY) * f; applyView();
  }, { passive: false });

  /* ───────── Node rendering ───────── */
  const nodesHost = $('#nodes');
  let topZ = 1;
  const toFront = id => { const el = nodeEl(id); if (el) el.style.zIndex = ++topZ; };
  const nodeEl = id => nodesHost.querySelector(`.node[data-id="${id}"]`);
  function headHTML(n) {
    const k = KIND[n.kind], st = (R[n.id] || {}).status || '';
    const sub = n.kind === 'func' ? base(n.data.file) : n.kind === 'code' ? (LANGS[langOf(n)] || LANGS.python).label.replace(' (container)', '') + (n.data.container || langOf(n) === 'shell' ? ' · container' : '') : '';
    const btns = [];
    if (['func', 'code', 'value', 'viewer'].includes(n.kind)) btns.push(`<button class="n-btn" data-act="run" title="Run this node and everything it needs">${ICON('play')}</button>`);
    if (n.kind === 'func') btns.push(`<button class="n-btn" data-act="src" title="View source">${ICON('code')}</button><button class="n-btn" data-act="termhere" title="Open a terminal in this file's folder">${ICON('terminal')}</button>`);
    if (n.kind === 'terminal') btns.push(`<button class="n-btn" data-act="restart" title="Restart terminal">${ICON('refresh')}</button>`);
    btns.push(`<button class="n-btn" data-act="del" title="Delete (Del)">${ICON('close')}</button>`);
    return `<div class="n-head"><span class="n-st ${st}"></span><span class="n-kind">${k.label}</span><span class="n-title">${esc(n.title)}</span><span class="n-sub">${esc(sub)}</span>${btns.join('')}</div>`;
  }
  function portsHTML(n) {
    const linked = new Set(S.edges.filter(e => e.to === n.id).map(e => e.port));
    let h = '';
    if (n.kind === 'func') {
      if (n.data.doc) h += `<div class="n-doc">${esc(n.data.doc.split('\n')[0])}</div>`;
      h += '<div class="ports">' + (n.data.params || []).map(p => `<div class="port in ${linked.has(p.name) ? 'linked' : ''}" data-port="${esc(p.name)}"><i class="pdot" data-in="${esc(p.name)}"></i><span class="pname">${esc(p.name)}${p.ann ? `<small>: ${esc(p.ann)}</small>` : ''}</span><input class="pval" data-val="${esc(p.name)}" value="${esc((n.data.values || {})[p.name] || '')}" placeholder="${esc(p.default != null ? p.default : 'required')}" spellcheck="false"></div>`).join('');
      h += `<div class="port out"><span class="pname"><small>return${n.data.ret ? ': ' + esc(n.data.ret) : ''}</small></span><i class="pdot" data-out="1"></i></div></div>`;
    } else if (n.kind === 'code') {
      const lang = langOf(n), L = LANGS[lang] || LANGS.python, box = !!n.data.container || lang === 'shell';
      const avail = RC.info && RC.info.langs ? RC.info.langs[lang] : null, eng = RC.info && RC.info.engine;
      h += `<div class="n-field">LANG <select data-f="lang">${Object.entries(LANGS).map(([k, v]) => `<option value="${k}" ${k === lang ? 'selected' : ''}>${v.label}</option>`).join('')}</select>`
        + (lang === 'shell' ? '' : `<label class="n-check" title="Run this node inside a Podman / Docker container"><input type="checkbox" data-f="container" ${n.data.container ? 'checked' : ''}>${ICON('box')}CONTAINER</label>`) + '</div>';
      if (box) h += `<div class="n-field">IMAGE <input data-f="image" value="${esc(n.data.image || '')}" placeholder="${esc(L.image)}" spellcheck="false"></div>`;
      let warn = '';
      if (box && eng && !eng.ok) warn = `Containers aren’t ready (${esc(eng.detail || 'unknown')}). Run <code>flowbench podman</code> once.`;
      else if (!box && lang !== 'python' && avail === false) warn = `${L.label} isn’t installed on this computer. Tick CONTAINER to run it with Podman.`;
      const how = n.data.file ? 'Runs the file · inputs: JSON file <code>FLOW_IN</code> · what it prints is the result'
        : lang === 'python' && !box ? 'Inputs are variables · set <code>result</code> · matplotlib plots are captured'
        : L.tier === 'vars' ? 'Inputs are variables · set <code>result</code> · images saved to <code>FLOW_OUT</code> show here'
        : 'Inputs: JSON file <code>FLOW_IN</code> · print the result · images in <code>FLOW_OUT</code> show here';
      h += `<div class="n-hint${warn ? ' warn' : ''}">${warn || how}</div>`;
      h += `<div class="n-field">INPUTS <input data-f="inputs" value="${esc(n.data.inputs || '')}" placeholder="x, y" spellcheck="false"></div>`;
      h += '<div class="ports">' + inPorts(n).map(p => `<div class="port in ${linked.has(p) ? 'linked' : ''}" data-port="${esc(p)}"><i class="pdot" data-in="${esc(p)}"></i><span class="pname">${esc(p)}</span><input class="pval" data-val="${esc(p)}" value="${esc((n.data.values || {})[p] || '')}" placeholder="value if not wired" spellcheck="false"></div>`).join('') + '</div>';
      if (n.data.file) h += `<div class="n-filebar">${ICON('file-code')}<span title="${esc(n.data.file)}">${esc(base(n.data.file))}</span><button class="mini-btn" data-act="src">VIEW</button><button class="mini-btn" data-act="inline">EDIT HERE</button></div>`;
      else h += `<div style="display:flex;flex-direction:column;flex:1;min-height:0;padding:0 10px 4px"><textarea class="n-code" data-f="code" spellcheck="false">${esc(n.data.code || '')}</textarea></div>`;
      h += `<div class="ports"><div class="port out"><span class="pname"><small>result</small></span><i class="pdot" data-out="1"></i></div></div>`;
    } else if (n.kind === 'value') {
      h += `<div class="n-field" style="padding-top:2px">PYTHON <input data-f="expr" value="${esc(n.data.expr || '')}" placeholder="42, 'text', [1, 2], np.linspace(0, 1, 50)" spellcheck="false"></div>`;
      h += `<div class="ports"><div class="port out"><span class="pname"><small>value</small></span><i class="pdot" data-out="1"></i></div></div>`;
    } else if (n.kind === 'viewer') {
      h += `<div class="ports"><div class="port in ${linked.has('data') ? 'linked' : ''}" data-port="data"><i class="pdot" data-in="data"></i><span class="pname">data</span></div><div class="port out"><span class="pname"><small>pass-through</small></span><i class="pdot" data-out="1"></i></div></div>`;
    } else if (n.kind === 'terminal') {
      h += `<div class="n-field">SHELL <select data-f="shell"><option value="default">default shell</option><option value="powershell">PowerShell</option><option value="cmd">cmd</option><option value="python">Python</option><option value="bash">bash</option><option value="container">container</option></select> CWD <input data-f="cwd" value="${esc(n.data.cwd || '')}" spellcheck="false"></div>${n.data.shell === 'container' ? `<div class="n-field">IMAGE <input data-f="image" value="${esc(n.data.image || '')}" placeholder="python:3.12-slim" spellcheck="false"></div>` : ''}<div class="term-host"></div>`;
    } else if (n.kind === 'note') {
      h += `<textarea class="n-note" data-f="text" placeholder="Write a note…">${esc(n.data.text || '')}</textarea>`;
    }
    return h;
  }
  function renderNode(n) {
    let el = nodeEl(n.id), keepTerm = null;
    if (el && n.kind === 'terminal') keepTerm = $('.term-host', el);
    if (!el) { el = document.createElement('div'); el.dataset.id = n.id; el.style.zIndex = ++topZ; nodesHost.appendChild(el); roNodes.observe(el); }
    el.className = `node k-${n.kind}${sel.has(n.id) ? ' sel' : ''}`;
    el.style.left = n.x + 'px'; el.style.top = n.y + 'px'; el.style.width = n.w + 'px'; el.style.height = n.h ? n.h + 'px' : '';
    el.innerHTML = headHTML(n) + portsHTML(n) + (n.kind === 'terminal' || n.kind === 'note' ? '' : '<div class="n-body"></div>') + '<div class="n-resize"></div>';
    if (n.kind === 'terminal') {
      $('select[data-f="shell"]', el).value = n.data.shell || 'default';
      if (keepTerm) $('.term-host', el).replaceWith(keepTerm); else if (TERMS[n.id]) $('.term-host', el).replaceWith(TERMS[n.id].host);
    }
    renderBody(n);
    scheduleEdges();
  }
  function renderBody(n) {
    const el = nodeEl(n.id); if (!el) return;
    const st = $('.n-st', el), r = R[n.id] || {};
    if (st) st.className = 'n-st ' + (r.status || '');
    const b = $('.n-body', el); if (!b) return;
    let h = '';
    if (r.status || r.logs) {
      const p = r.preview || {};
      const meta = [];
      if (r.status) meta.push(r.status === 'running' ? '<b>running…</b>' : r.status === 'cached' ? 'cached' : r.status === 'skipped' ? 'skipped (an input failed)' : r.status === 'error' ? '<b style="color:var(--red)">error</b>' : '<b>✓</b>');
      if (r.ms != null && r.status !== 'running') meta.push(r.ms + ' ms');
      if (p.type) meta.push(esc(p.type) + (p.shape ? ' ' + esc(JSON.stringify(p.shape)) : '') + (p.dtype ? ' ' + esc(p.dtype) : ''));
      h += `<div class="n-meta"><span>${meta.join(' · ')}</span>${r.status === 'ok' || r.status === 'cached' || r.error ? `<button class="n-expand" data-act="expand" title="Open the output in a large window">${ICON('expand')}</button>` : ''}</div>`;
      if (r.error) h += `<div class="n-err">${esc(r.error)}</div>`;
      if (p.images) h += p.images.map(src => `<img class="n-img" src="${src.startsWith('data:') ? src : 'data:image/png;base64,' + src}" alt="plot">`).join('');
      if (p.html) h += `<div class="n-tbl">${p.html}</div>`;
      if (p.plot && !p.images) h += '<canvas class="n-plot"></canvas>';
      if (p.text != null && !p.html) h += (n.kind === 'viewer' || !p.images) ? `<div class="n-text">${esc(p.text)}</div>` : '';
      if (r.logs) h += `<details class="n-fold" ${n.kind === 'viewer' || r.status === 'error' ? 'open' : ''}><summary>OUTPUT LOG</summary><div class="n-log">${esc(r.logs)}</div></details>`;
    }
    b.innerHTML = h;
    const cv = $('.n-plot', b); if (cv && r.preview && r.preview.plot) requestAnimationFrame(() => drawPlot(cv, r.preview.plot));
  }
  function drawPlot(cv, data) {
    const w = cv.clientWidth, h = cv.clientHeight, dpr = Math.min(2, devicePixelRatio || 1); if (!w) return;
    cv.width = w * dpr; cv.height = h * dpr; const g = cv.getContext('2d'); g.scale(dpr, dpr);
    const mn = Math.min(...data), mx = Math.max(...data), pad = 8, sx = (w - pad * 2) / Math.max(1, data.length - 1), sy = (h - pad * 2) / ((mx - mn) || 1);
    const css = getComputedStyle(document.documentElement);
    g.strokeStyle = css.getPropertyValue('--line').trim(); g.lineWidth = 1;
    if (mn < 0 && mx > 0) { const y0 = h - pad - (0 - mn) * sy; g.beginPath(); g.moveTo(pad, y0); g.lineTo(w - pad, y0); g.stroke(); }
    g.strokeStyle = css.getPropertyValue('--ink').trim(); g.lineWidth = 1.5; g.beginPath();
    data.forEach((v, i) => { const x = pad + i * sx, y = h - pad - (v - mn) * sy; if (i) g.lineTo(x, y); else g.moveTo(x, y); }); g.stroke();
    g.fillStyle = css.getPropertyValue('--mute').trim(); g.font = '10px JetBrains Mono, monospace';
    g.fillText(mx.toPrecision(4), pad + 2, pad + 9); g.fillText(mn.toPrecision(4), pad + 2, h - pad - 2);
  }
  function markSel() { $$('.node', nodesHost).forEach(el => el.classList.toggle('sel', sel.has(el.dataset.id))); }
  function renderAll() { nodesHost.innerHTML = ''; S.nodes.forEach(renderNode); markSel(); renderEdges(); applyView(); }
  const roNodes = new ResizeObserver(() => scheduleEdges());

  /* ───────── Edges ───────── */
  let edgeRaf = 0;
  function scheduleEdges() { if (!edgeRaf) edgeRaf = requestAnimationFrame(() => { edgeRaf = 0; renderEdges(); }); }
  function portPos(id, port, out) {
    const el = nodeEl(id); if (!el) return null;
    const dot = out ? $('.pdot[data-out]', el) : $(`.pdot[data-in="${CSS.escape(port)}"]`, el); if (!dot) return null;
    const r = dot.getBoundingClientRect(); return toWorld(r.left + r.width / 2, r.top + r.height / 2);
  }
  const curve = (a, b) => { const c = Math.max(40, Math.abs(b.x - a.x) * 0.5); return `M${a.x},${a.y} C${a.x + c},${a.y} ${b.x - c},${b.y} ${b.x},${b.y}`; };
  function renderEdges() {
    const g = $('#edge-g'); let h = '';
    S.edges.forEach(e => {
      const a = portPos(e.from, null, true), b = portPos(e.to, e.port, false); if (!a || !b) return;
      const d = curve(a, b), running = (R[e.to] || {}).status === 'running';
      h += `<path class="edge ${e.id === selEdge ? 'sel' : ''} ${running ? 'flow' : ''}" d="${d}"></path><path class="edge-hit" data-edge="${e.id}" d="${d}"></path>`;
    });
    g.innerHTML = h;
  }
  $('#edge-g').addEventListener('pointerdown', e => { const p = e.target.closest('[data-edge]'); if (!p) return; e.stopPropagation(); selEdge = p.dataset.edge; sel.clear(); markSel(); renderEdges(); });
  function connectEdge(from, to, port) {
    if (from === to) return;
    const fn = node(from), tn = node(to); if (!fn || !tn || !hasOut(fn) || !inPorts(tn).includes(port)) return;
    // prevent cycles: walk downstream from `to`
    const down = new Set([to]), q = [to];
    while (q.length) { const x = q.pop(); S.edges.forEach(e => { if (e.from === x && !down.has(e.to)) { down.add(e.to); q.push(e.to); } }); }
    if (down.has(from)) { toast('That wire would create a loop'); return; }
    S.edges = S.edges.filter(e => !(e.to === to && e.port === port));
    S.edges.push({ id: uid(), from, to, port });
    renderNode(tn); commit();
  }

  /* ───────── Pointer interactions ───────── */
  let drag = null, spaceDown = false;
  stage.addEventListener('pointerdown', e => {
    if (e.target.closest('.zoom, .minimap')) return;
    const nEl = e.target.closest('.node');
    // wiring
    const outDot = e.target.closest('.pdot[data-out]'), inDot = e.target.closest('.pdot[data-in]');
    if (outDot || inDot) {
      e.preventDefault(); e.stopPropagation();
      const id = nEl.dataset.id;
      if (inDot) {
        const port = inDot.dataset.in, ex = S.edges.find(x => x.to === id && x.port === port);
        if (ex) { S.edges = S.edges.filter(x => x !== ex); renderNode(node(id)); drag = { t: 'wire', from: ex.from, dir: 'out' }; }
        else drag = { t: 'wire', to: id, port, dir: 'in' };
      } else drag = { t: 'wire', from: id, dir: 'out' };
      cap(stage, e.pointerId); updateTempWire(e); return;
    }
    if (nEl) {
      const id = nEl.dataset.id, n = node(id);
      if (e.target.closest('.n-resize')) { e.preventDefault(); drag = { t: 'resize', id, x0: e.clientX, y0: e.clientY, w: n.w, h: nEl.offsetHeight }; cap(stage, e.pointerId); return; }
      const head = e.target.closest('.n-head');
      if (head && !e.target.closest('button') && !(spaceDown)) {
        e.preventDefault();
        if (!sel.has(id)) { if (!e.shiftKey) sel.clear(); sel.add(id); } else if (e.shiftKey) sel.delete(id);
        toFront(id);
        selEdge = null; markSel(); renderEdges();
        drag = { t: 'move', x0: e.clientX, y0: e.clientY, orig: [...sel].map(i => { const m = node(i); return { m, x: m.x, y: m.y }; }), moved: false };
        cap(stage, e.pointerId); return;
      }
      if (!spaceDown && e.button !== 1) { toFront(id); if (!sel.has(id) || sel.size > 1) { sel.clear(); sel.add(id); selEdge = null; markSel(); renderEdges(); } return; }
    }
    // background: pan, or box-select with Shift
    if (e.button === 2) return;
    e.preventDefault(); closePalette();
    if (e.shiftKey && e.button === 0) { drag = { t: 'box', x0: e.clientX, y0: e.clientY }; }
    else { drag = { t: 'pan', x0: e.clientX, y0: e.clientY, vx: S.view.x, vy: S.view.y, moved: false }; stage.classList.add('panning'); }
    cap(stage, e.pointerId);
  });
  stage.addEventListener('pointermove', e => {
    if (!drag) return;
    const k = S.view.k;
    if (drag.t === 'pan') { const dx = e.clientX - drag.x0, dy = e.clientY - drag.y0; if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true; S.view.x = drag.vx + dx; S.view.y = drag.vy + dy; applyView(); }
    else if (drag.t === 'move') {
      const dx = (e.clientX - drag.x0) / k, dy = (e.clientY - drag.y0) / k; if (Math.abs(dx) + Math.abs(dy) > 2) drag.moved = true;
      drag.orig.forEach(o => { o.m.x = gsnap(o.x + dx, e.altKey); o.m.y = gsnap(o.y + dy, e.altKey); const el = nodeEl(o.m.id); el.style.left = o.m.x + 'px'; el.style.top = o.m.y + 'px'; });
      scheduleEdges();
    } else if (drag.t === 'resize') {
      const n = node(drag.id), el = nodeEl(drag.id);
      n.w = Math.max(200, gsnap(drag.w + (e.clientX - drag.x0) / k, e.altKey)); n.h = Math.max(80, gsnap(drag.h + (e.clientY - drag.y0) / k, e.altKey));
      el.style.width = n.w + 'px'; el.style.height = n.h + 'px'; scheduleEdges();
    } else if (drag.t === 'wire') updateTempWire(e);
    else if (drag.t === 'box') {
      const r = stage.getBoundingClientRect(), b = $('#selbox'), x = Math.min(e.clientX, drag.x0) - r.left, y = Math.min(e.clientY, drag.y0) - r.top;
      Object.assign(b.style, { display: 'block', left: x + 'px', top: y + 'px', width: Math.abs(e.clientX - drag.x0) + 'px', height: Math.abs(e.clientY - drag.y0) + 'px' });
    }
  });
  stage.addEventListener('pointerup', e => {
    const d = drag; drag = null; stage.classList.remove('panning');
    if (!d) return;
    if (d.t === 'pan' && !d.moved) { sel.clear(); selEdge = null; markSel(); renderEdges(); }
    if (d.t === 'move' && d.moved) commit();
    if (d.t === 'resize') { commit(); const t = TERMS[d.id]; if (t) fitTerm(t); rerenderPlots(d.id); }
    if (d.t === 'wire') {
      $('#edge-temp').setAttribute('d', '');
      const el = document.elementFromPoint(e.clientX, e.clientY), tgtNode = el && el.closest('.node');
      if (tgtNode) {
        const id = tgtNode.dataset.id, n = node(id);
        if (d.dir === 'out') {
          const pin = el.closest('[data-port]'), port = pin ? pin.dataset.port : inPorts(n)[0];
          if (port) connectEdge(d.from, id, port); else toast('That node has no inputs');
        } else if (hasOut(n)) connectEdge(id, d.to, d.port);
      } else if (!el || !el.closest('.bar, .lib')) openPalette(e.clientX, e.clientY, d);
      renderEdges(); commit();
    }
    if (d.t === 'box') {
      const b = $('#selbox'); b.style.display = 'none';
      const p1 = toWorld(Math.min(e.clientX, d.x0), Math.min(e.clientY, d.y0)), p2 = toWorld(Math.max(e.clientX, d.x0), Math.max(e.clientY, d.y0));
      S.nodes.forEach(n => { const el = nodeEl(n.id); if (n.x < p2.x && n.x + n.w > p1.x && n.y < p2.y && n.y + el.offsetHeight > p1.y) sel.add(n.id); });
      markSel();
    }
  });
  function updateTempWire(e) {
    const p = toWorld(e.clientX, e.clientY), a = drag.dir === 'out' ? portPos(drag.from, null, true) : p, b = drag.dir === 'out' ? p : portPos(drag.to, drag.port, false);
    if (a && b) $('#edge-temp').setAttribute('d', curve(a, b));
  }
  stage.addEventListener('dblclick', e => { if (e.target.closest('.node, .zoom, .minimap')) return; openPalette(e.clientX, e.clientY, null); });
  stage.addEventListener('contextmenu', e => { if (!e.target.closest('.node')) e.preventDefault(); });
  stage.addEventListener('dragover', e => { if (e.dataTransfer.types.includes('text/x-flowbench')) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; } });
  stage.addEventListener('drop', async e => {
    const raw = e.dataTransfer.getData('text/x-flowbench'); if (!raw) return; e.preventDefault();
    const d = JSON.parse(raw), p = toWorld(e.clientX, e.clientY);
    if (d.script) addScriptNode(d.script, d.lang, p.x - 40, p.y - 20); else await addFuncNode(d.file, d.func, p.x - 40, p.y - 20);
  });

  /* node inputs & buttons (delegated) */
  nodesHost.addEventListener('input', e => {
    const el = e.target.closest('.node'); if (!el) return; const n = node(el.dataset.id);
    if (e.target.dataset.val != null) { n.data.values = n.data.values || {}; n.data.values[e.target.dataset.val] = e.target.value; }
    else if (e.target.dataset.f) { n.data[e.target.dataset.f] = e.target.type === 'checkbox' ? e.target.checked : e.target.value; }
    save();
  });
  nodesHost.addEventListener('change', e => {
    const el = e.target.closest('.node'); if (!el) return; const n = node(el.dataset.id), f = e.target.dataset.f;
    if (f === 'inputs') { const keep = new Set(inPorts(n)); S.edges = S.edges.filter(x => x.to !== n.id || keep.has(x.port)); renderNode(n); }
    if (n.kind === 'code' && f === 'lang') {
      const L = LANGS[langOf(n)];
      if (!n.data.file && isTpl(n.data.code)) { n.data.code = L.tpl; if (!String(n.data.inputs || '').trim() || n.data.inputs === 'x') n.data.inputs = L.tier === 'vars' ? 'x' : ''; }
      renderNode(n);
    }
    if (n.kind === 'code' && f === 'container') renderNode(n);
    if (n.kind === 'terminal' && f === 'shell') renderNode(n);
    if (n.kind === 'terminal' && (f === 'shell' || f === 'cwd' || f === 'image')) startTerm(n, true);
    commit();
  });
  nodesHost.addEventListener('keydown', e => { if (e.target.classList.contains('n-code') && e.key === 'Tab') { e.preventDefault(); const t = e.target, s = t.selectionStart; t.setRangeText('    ', s, t.selectionEnd, 'end'); t.dispatchEvent(new Event('input', { bubbles: true })); } });
  nodesHost.addEventListener('focusout', e => { if (e.target.matches('textarea, input')) commit(); });
  nodesHost.addEventListener('click', async e => {
    const b = e.target.closest('[data-act]'); if (!b) return; const el = b.closest('.node'), n = node(el.dataset.id), act = b.dataset.act;
    if (act === 'run') run([n.id]);
    if (act === 'del') { sel.clear(); sel.add(n.id); deleteSel(); }
    if (act === 'src') showSource(n.data.file, n.data.line);
    if (act === 'termhere') addNode('terminal', n.x + n.w + 40, n.y, { cwd: dir(n.data.file) }, 'terminal');
    if (act === 'restart') startTerm(n, true);
    if (act === 'expand') showOutput(n);
    if (act === 'inline') { const r = await req('read', { path: n.data.file }); if (!r.ok) return toast(r.error); n.data.code = r.text; delete n.data.file; renderNode(n); commit(); toast('Copied into the node · the original file is unchanged'); }
  });
  nodesHost.addEventListener('dblclick', e => {
    const t = e.target.closest('.n-title'); if (!t) return; const n = node(t.closest('.node').dataset.id);
    const v = prompt('Rename node', n.title); if (v && v.trim()) { n.title = v.trim(); renderNode(n); commit(); }
  });
  function rerenderPlots(id) { const el = nodeEl(id), r = R[id]; const cv = el && $('.n-plot', el); if (cv && r && r.preview && r.preview.plot) drawPlot(cv, r.preview.plot); }

  function deleteSel() {
    if (selEdge) { const ed = S.edges.find(e => e.id === selEdge); S.edges = S.edges.filter(e => e.id !== selEdge); selEdge = null; if (ed && node(ed.to)) renderNode(node(ed.to)); renderEdges(); commit(); return; }
    if (!sel.size) return;
    sel.forEach(id => { closeTerm(id); const el = nodeEl(id); if (el) { roNodes.unobserve(el); el.remove(); } delete R[id]; });
    const aff = new Set(S.edges.filter(e => sel.has(e.from)).map(e => e.to));
    S.nodes = S.nodes.filter(n => !sel.has(n.id)); S.edges = S.edges.filter(e => !sel.has(e.from) && !sel.has(e.to)); sel.clear();
    aff.forEach(id => { const n = node(id); if (n) renderNode(n); });
    renderEdges(); commit();
  }
  let CLIP = null;
  function copySel() { if (!sel.size) return; const ids = new Set(sel); CLIP = { nodes: S.nodes.filter(n => ids.has(n.id)).map(n => JSON.parse(JSON.stringify(n))), edges: S.edges.filter(e => ids.has(e.from) && ids.has(e.to)).map(e => ({ ...e })) }; toast('Copied ' + CLIP.nodes.length + ' node' + (CLIP.nodes.length > 1 ? 's' : '')); }
  function paste(off = 40) {
    if (!CLIP) return; const map = {}; sel.clear();
    CLIP.nodes.forEach(n => { const m = JSON.parse(JSON.stringify(n)); map[n.id] = m.id = uid(); m.x += off; m.y += off; S.nodes.push(m); sel.add(m.id); renderNode(m); if (m.kind === 'terminal') startTerm(m); });
    CLIP.edges.forEach(e => S.edges.push({ id: uid(), from: map[e.from], to: map[e.to], port: e.port }));
    CLIP.nodes.forEach(n => { n.x += off; n.y += off; });
    markSel(); renderEdges(); commit();
  }

  /* ───────── Run ───────── */
  let running = false;
  async function run(targets, force) {
    if (!RC.ok) { openConnect(); return; }
    if (running) { toast('Already running · press STOP to cancel'); return; }
    const nodes = S.nodes.filter(n => ['func', 'code', 'value', 'viewer'].includes(n.kind));
    if (!nodes.length) { toast('Add a function, code or value node first'); return; }
    running = true; setStatus('busy'); $('#run-all').disabled = true;
    const r = await req('run', { nodes: nodes.map(n => ({ id: n.id, kind: n.kind, data: n.data })), edges: S.edges.map(e => ({ from: e.from, to: e.to, port: e.port })), targets: targets || null, force: !!force });
    running = false; $('#run-all').disabled = false; setStatus(RC.ok ? 'on' : 'off'); renderEdges();
    if (r.error) toast(r.error, 4000); else if (r.failed) toast(r.failed + ' node' + (r.failed > 1 ? 's' : '') + ' failed · see the red messages'); else toast('✓ Workflow finished');
  }
  function onNodeEvent(m) {
    const r = R[m.nid] = R[m.nid] || {};
    r.status = m.status;
    if (m.status === 'running') { r.logs = ''; r.error = null; }
    if (m.preview) r.preview = m.preview;
    if (m.status === 'error') { r.error = m.error; r.preview = null; }
    if (m.ms != null) r.ms = m.ms;
    const n = node(m.nid); if (n) renderBody(n);
    renderEdges(); drawMinimap();
  }
  let logQ = {}, logRaf = 0;
  function onStream(m) {
    if (!m.nid) { if (/Error|Traceback/.test(m.text)) console.warn(m.text); return; }
    const r = R[m.nid] = R[m.nid] || {}; r.logs = ((r.logs || '') + m.text).slice(-20000); logQ[m.nid] = 1;
    if (!logRaf) logRaf = requestAnimationFrame(() => { logRaf = 0; Object.keys(logQ).forEach(id => { const n = node(id), el = n && nodeEl(id); if (!el) return; const lg = $('.n-log', el); if (lg) { lg.textContent = R[id].logs; lg.scrollTop = lg.scrollHeight; } else renderBody(n); }); logQ = {}; });
  }
  $('#run-all').onclick = () => run(null);
  $('#run-sel').onclick = () => { if (!sel.size) return toast('Select nodes first (click, or Shift+drag a box)'); run([...sel]); };
  $('#stop').onclick = async () => { if (!RC.ok) return; await req('stop'); running = false; $('#run-all').disabled = false; setStatus('on'); Object.values(R).forEach(r => { if (r.status === 'running') r.status = 'error', r.error = 'Stopped'; }); S.nodes.forEach(renderBody); renderEdges(); toast('Stopped · the Python kernel was reset'); };

  /* ───────── Terminals ───────── */
  const TERMS = {};
  const termByTid = tid => Object.values(TERMS).find(t => t.tid === tid);
  function xtermTheme() {
    return { background: '#0a0a0a', foreground: '#e8e8e2', cursor: '#e0262e', selectionBackground: 'rgba(224,38,46,.35)', black: '#1b1b19', brightBlack: '#6c6c66', red: '#ff6b70', green: '#3cc47c', yellow: '#e0a526', blue: '#5b9dff', magenta: '#a17dff', cyan: '#43c6d9', white: '#e8e8e2' };
  }
  function startTerm(n, restart) {
    const el = nodeEl(n.id); if (!el) return;
    let t = TERMS[n.id];
    if (!window.Terminal) { $('.term-host', el).innerHTML = '<div class="term-off">Terminal library failed to load.</div>'; return; }
    if (!t) {
      const host = $('.term-host', el);
      const xt = new window.Terminal({ fontFamily: 'JetBrains Mono, ui-monospace, monospace', fontSize: 12.5, cursorBlink: true, allowProposedApi: false, theme: xtermTheme(), scrollback: 5000 });
      const fitA = new window.FitAddon.FitAddon(); xt.loadAddon(fitA); xt.open(host);
      t = TERMS[n.id] = { xt, fit: fitA, host, tid: null, alive: false, cols: 0, rows: 0, id: n.id };
      xt.onData(d => { if (t.alive) tell('term-input', { tid: t.tid, data: d }); });
      new ResizeObserver(() => fitTerm(t)).observe(host);
    }
    if (!RC.ok) { t.xt.write('\x1b[2mConnect the runner to use this terminal.\x1b[0m\r\n'); return; }
    if (t.alive && !restart) return;
    if (t.tid) tell('term-close', { tid: t.tid });
    t.tid = n.id + '-' + uid(); t.alive = true;
    if (restart) t.xt.reset();
    fitTerm(t, true);
    req('term-open', { tid: t.tid, shell: n.data.shell || 'default', image: n.data.image || '', cwd: n.data.cwd || '', cols: t.xt.cols, rows: t.xt.rows }).then(r => {
      if (!r.ok) { t.alive = false; t.xt.write('\x1b[31m' + r.error + '\x1b[0m\r\n'); }
      else if (r.cwd && r.cwd !== n.data.cwd) { n.data.cwd = r.cwd; const inp = $('input[data-f="cwd"]', nodeEl(n.id)); if (inp) inp.value = r.cwd; save(); }
    });
  }
  function fitTerm(t, silent) {
    try { t.fit.fit(); } catch { return; }
    if ((t.xt.cols !== t.cols || t.xt.rows !== t.rows) && t.alive && !silent) tell('term-resize', { tid: t.tid, cols: t.xt.cols, rows: t.xt.rows });
    t.cols = t.xt.cols; t.rows = t.xt.rows;
  }
  function closeTerm(id) { const t = TERMS[id]; if (!t) return; if (t.tid) tell('term-close', { tid: t.tid }); t.xt.dispose(); delete TERMS[id]; }
  $('#term-btn').onclick = () => { const c = viewCenter(), k = S.nodes.filter(n => n.kind === 'terminal').length; addNode('terminal', c.x - 290 + k * 30, c.y - 170 + k * 30, {}, 'terminal ' + (k + 1)); };

  /* ───────── Source viewer ───────── */
  async function showSource(file, line) {
    const r = await req('read', { path: file }); if (!r.ok) return toast(r.error);
    $('#src-title').textContent = file;
    $('#src-body').innerHTML = r.text.split('\n').map((l, i) => `<span class="${i + 1 === line ? 'hl' : ''}">${esc(l) || ' '}</span>`).join('');
    $('#src-modal').hidden = false;
    const hl = $('#src-body .hl'); if (hl) hl.scrollIntoView({ block: 'center' });
  }

  /* ───────── Quick-add palette ───────── */
  let PAL = null;
  const BUILTINS = [['value', 'Value', 'a number, text, list or any Python expression'], ['code', 'Code · Python', 'your own Python snippet · plots are captured'], ['code:javascript', 'Code · JavaScript', 'runs with Node.js'], ['code:typescript', 'Code · TypeScript', 'runs with Node.js'], ['code:rust', 'Code · Rust', 'compiled with rustc'], ['code:cpp', 'Code · C++', 'compiled with g++'], ['code:c', 'Code · C', 'compiled with gcc'], ['code:powershell', 'Code · PowerShell', 'a PowerShell script'], ['code:bash', 'Code · Bash', 'a bash script'], ['code:go', 'Code · Go', 'local Go or a container'], ['code:r', 'Code · R', 'local R or a container'], ['code:shell', 'Container shell', 'any image, e.g. alpine, ubuntu'], ['viewer', 'Viewer', 'big preview of a result: plot, table, text'], ['terminal', 'Terminal', 'a real shell on the canvas'], ['note', 'Note', 'a sticky note']];
  async function openPalette(cx, cy, wire) {
    const p = toWorld(cx, cy); PAL = { x: p.x, y: p.y, wire, idx: 0, items: [] };
    const box = $('#palette'); box.hidden = false;
    box.style.left = Math.min(cx, innerWidth - 350) + 'px'; box.style.top = Math.min(cy, innerHeight - 380) + 'px';
    $('#pal-q').value = ''; $('#pal-q').focus(); await palList();
  }
  function closePalette() { $('#palette').hidden = true; PAL = null; }
  async function palList() {
    if (!PAL) return;
    const q = $('#pal-q').value.trim().toLowerCase(), items = [];
    BUILTINS.forEach(([k, name, desc]) => { if (!q || name.toLowerCase().includes(q) || desc.toLowerCase().includes(q)) { const [kind, lang] = k.split(':'); items.push({ kind, lang, name, desc }); } });
    if (RC.ok) { const idx = await ensureIndex(); idx.forEach(f => f.funcs.forEach(fn => { if (!q || fn.toLowerCase().includes(q)) items.push({ kind: 'func', name: fn, file: f.path, desc: base(f.path) }); })); }
    PAL.items = items.slice(0, 60); PAL.idx = 0; palRender();
  }
  function palRender() {
    $('#pal-list').innerHTML = PAL.items.map((it, i) => `<div class="pi ${i === PAL.idx ? 'on' : ''}" data-i="${i}"><i style="background:var(--k-${it.kind})"></i><b>${esc(it.name)}</b><small>${esc(it.desc)}</small></div>`).join('') || '<div class="pi">Nothing found</div>';
  }
  async function palPick(i) {
    const it = PAL && PAL.items[i]; if (!it) return; const { x, y, wire } = PAL; closePalette();
    const n = it.kind === 'func' ? await addFuncNode(it.file, it.name, x, y - 20) : addNode(it.kind, x, y - 20, it.lang ? { lang: it.lang } : {}, it.lang ? LANGS[it.lang].label.replace(' (container)', '').toLowerCase() : it.kind === 'terminal' ? 'terminal' : null);
    if (!n || !wire) return;
    if (wire.dir === 'out' && inPorts(n).length) connectEdge(wire.from, n.id, inPorts(n)[0]);
    if (wire.dir === 'in' && hasOut(n)) connectEdge(n.id, wire.to, wire.port);
  }
  $('#pal-q').addEventListener('input', palList);
  $('#pal-q').addEventListener('keydown', e => {
    if (!PAL) return;
    if (e.key === 'ArrowDown') { PAL.idx = Math.min(PAL.items.length - 1, PAL.idx + 1); palRender(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { PAL.idx = Math.max(0, PAL.idx - 1); palRender(); e.preventDefault(); }
    else if (e.key === 'Enter') { palPick(PAL.idx); e.preventDefault(); }
    else if (e.key === 'Escape') closePalette();
  });
  $('#pal-list').addEventListener('click', e => { const p = e.target.closest('[data-i]'); if (p) palPick(+p.dataset.i); });
  $('#add-btn').onclick = () => { const r = $('#add-btn').getBoundingClientRect(); const s = stage.getBoundingClientRect(); openPalette(Math.max(s.left + 40, r.left - 140), r.bottom + 8, null); };

  /* ───────── Minimap ───────── */
  const mm = $('#minimap'), mg = mm.getContext('2d');
  function mmGeom() {
    const r = stage.getBoundingClientRect(), v = S.view;
    const vb = { x1: -v.x / v.k, y1: -v.y / v.k, x2: (r.width - v.x) / v.k, y2: (r.height - v.y) / v.k };
    const b = S.nodes.length ? bounds() : vb;
    const x1 = Math.min(b.x1, vb.x1), y1 = Math.min(b.y1, vb.y1), x2 = Math.max(b.x2, vb.x2), y2 = Math.max(b.y2, vb.y2);
    const W = 200, Hh = 130, s = Math.min((W - 16) / (x2 - x1 || 1), (Hh - 16) / (y2 - y1 || 1));
    return { vb, s, ox: 8 - x1 * s + ((W - 16) - (x2 - x1) * s) / 2, oy: 8 - y1 * s + ((Hh - 16) - (y2 - y1) * s) / 2 };
  }
  function drawMinimap() {
    const dpr = Math.min(2, devicePixelRatio || 1); if (mm.width !== 200 * dpr) { mm.width = 200 * dpr; mm.height = 130 * dpr; }
    mg.setTransform(dpr, 0, 0, dpr, 0, 0); mg.clearRect(0, 0, 200, 130);
    const G = mmGeom(), css = getComputedStyle(document.documentElement);
    S.nodes.forEach(n => {
      const el = nodeEl(n.id), h = el ? el.offsetHeight : 100, st = (R[n.id] || {}).status;
      mg.fillStyle = st === 'error' ? css.getPropertyValue('--red') : st === 'running' ? css.getPropertyValue('--red') : css.getPropertyValue('--k-' + n.kind);
      mg.globalAlpha = 0.75; mg.fillRect(G.ox + n.x * G.s, G.oy + n.y * G.s, Math.max(2, n.w * G.s), Math.max(2, h * G.s)); mg.globalAlpha = 1;
    });
    mg.strokeStyle = css.getPropertyValue('--red').trim(); mg.lineWidth = 1.5;
    mg.strokeRect(G.ox + G.vb.x1 * G.s, G.oy + G.vb.y1 * G.s, (G.vb.x2 - G.vb.x1) * G.s, (G.vb.y2 - G.vb.y1) * G.s);
  }
  let mmDrag = false;
  const mmGo = e => { const G = mmGeom(), r = mm.getBoundingClientRect(), wx = (e.clientX - r.left - G.ox) / G.s, wy = (e.clientY - r.top - G.oy) / G.s, s = stage.getBoundingClientRect(); S.view.x = s.width / 2 - wx * S.view.k; S.view.y = s.height / 2 - wy * S.view.k; applyView(); };
  mm.addEventListener('pointerdown', e => { e.stopPropagation(); mmDrag = true; cap(mm, e.pointerId); mmGo(e); });
  mm.addEventListener('pointermove', e => { if (mmDrag) mmGo(e); });
  mm.addEventListener('pointerup', () => { mmDrag = false; });

  /* ───────── Save / open ───────── */
  $('#save-flow').onclick = () => {
    const name = (prompt('File name', 'workflow') || '').trim(); if (!name) return;
    const blob = new Blob([JSON.stringify({ flowbench: 1, nodes: S.nodes, edges: S.edges, view: S.view }, null, 2)], { type: 'application/json' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name.replace(/\.flow\.json$|\.json$/i, '') + '.flow.json'; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 3000);
  };
  $('#open-flow').onclick = () => $('#file-in').click();
  $('#file-in').onchange = async e => {
    const f = e.target.files[0]; e.target.value = ''; if (!f) return;
    try {
      const o = JSON.parse(await f.text()); if (!Array.isArray(o.nodes) || !Array.isArray(o.edges)) throw new Error('not a Flowbench file');
      Object.keys(TERMS).forEach(closeTerm); Object.keys(R).forEach(k => delete R[k]);
      commit(); S.nodes = o.nodes; S.edges = o.edges; if (o.view) S.view = o.view; renderAll(); commit();
      S.nodes.filter(n => n.kind === 'terminal').forEach(n => startTerm(n)); toast('Opened ' + f.name);
    } catch (err) { toast('Could not open: ' + err.message); }
  };


  /* ───────── Tidy: arrange the workflow left → right in data-flow order ───────── */
  function tidy() {
    if (!S.nodes.length) return;
    const flow = S.nodes.filter(n => S.edges.some(e => e.from === n.id || e.to === n.id));
    const loose = S.nodes.filter(n => !flow.includes(n));
    const level = {}, parents = id => S.edges.filter(e => e.to === id).map(e => e.from);
    const lv = (id, seen = new Set()) => { if (level[id] != null) return level[id]; if (seen.has(id)) return 0; seen.add(id); const ps = parents(id); return (level[id] = ps.length ? 1 + Math.max(...ps.map(p => lv(p, seen))) : 0); };
    flow.forEach(n => lv(n.id));
    const cols = [];
    flow.forEach(n => { (cols[level[n.id]] = cols[level[n.id]] || []).push(n); });
    const h = n => { const el = nodeEl(n.id); return el ? el.offsetHeight : 160; };
    const b = bounds(), x0 = gsnap(Math.min(b.x1, 40)), y0 = gsnap(Math.min(b.y1, 40));
    let x = x0;
    cols.forEach((col, ci) => {
      if (!col) return;
      if (ci) col.sort((a, c) => avgY(a) - avgY(c));
      let y = y0;
      col.forEach(n => { n.x = x; n.y = y; y = gsnap(y + h(n) + 40); });
      x = gsnap(x + Math.max(...col.map(n => n.w)) + 100);
    });
    function avgY(n) { const ps = parents(n.id).map(node).filter(Boolean); return ps.length ? ps.reduce((s, p) => s + p.y, 0) / ps.length : 0; }
    let yLoose = gsnap(Math.max(y0, ...flow.map(n => n.y + h(n))) + 80), xl = x0;
    loose.forEach(n => { n.x = xl; n.y = yLoose; xl = gsnap(xl + n.w + 40); });
    S.nodes.forEach(n => { const el = nodeEl(n.id); if (el) { el.style.left = n.x + 'px'; el.style.top = n.y + 'px'; } });
    renderEdges(); commit(); fit(); toast('Arranged left → right · Ctrl+Z to undo');
  }
  $('#tidy').onclick = tidy;

  /* ───────── Large output viewer ───────── */
  function showOutput(n) {
    const r = R[n.id] || {}, p = r.preview || {};
    let h = '';
    if (r.error) h += `<div class="n-err big">${esc(r.error)}</div>`;
    if (p.images) h += p.images.map(src => `<img class="out-img" src="${src.startsWith('data:') ? src : 'data:image/png;base64,' + src}" alt="output">`).join('');
    if (p.html) h += `<div class="n-tbl big">${p.html}</div>`;
    if (p.plot && !p.images) h += '<canvas class="n-plot big"></canvas>';
    if (p.text != null) h += `<pre class="out-text">${esc(p.text)}</pre>`;
    if (r.logs) h += `<h3 class="out-h">OUTPUT LOG</h3><pre class="out-text log">${esc(r.logs)}</pre>`;
    $('#out-title').textContent = n.title + (p.type ? '  ·  ' + p.type + (p.shape ? ' ' + JSON.stringify(p.shape) : '') : '');
    $('#out-body').innerHTML = h || '<p class="fine">No output yet. Run the node first.</p>';
    $('#out-modal').hidden = false;
    const cv = $('#out-body .n-plot'); if (cv) requestAnimationFrame(() => drawPlot(cv, p.plot));
  }

  /* ───────── Export as a Python script ───────── */
  function exportPy() {
    const run = S.nodes.filter(n => ['func', 'code', 'value', 'viewer'].includes(n.kind));
    if (!run.length) return toast('Nothing to export yet');
    const ids = new Set(run.map(n => n.id)), ins = {}, used = new Set(['np', 'plt', 'sys']), name = {};
    run.forEach(n => { ins[n.id] = {}; });
    S.edges.forEach(e => { if (ids.has(e.from) && ids.has(e.to)) ins[e.to][e.port] = e.from; });
    run.forEach(n => {
      let v = 'n_' + (n.title || n.kind).toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '') || 'n_node';
      if (/^n_\d/.test(v)) v = 'n_' + v.slice(2);
      let u = v, i = 2; while (used.has(u)) u = v + '_' + i++; used.add(u); name[n.id] = u;
    });
    const indeg = {}, order = [];
    run.forEach(n => { indeg[n.id] = Object.keys(ins[n.id]).length; });
    const ready = run.filter(n => !indeg[n.id]).map(n => n.id);
    while (ready.length) { const id = ready.shift(); order.push(id); run.forEach(m => { if (Object.values(ins[m.id]).includes(id)) { indeg[m.id] -= Object.values(ins[m.id]).filter(x => x === id).length; if (indeg[m.id] === 0) ready.push(m.id); } }); }
    const files = [...new Set(run.filter(n => n.kind === 'func').map(n => n.data.file))], mods = {};
    files.forEach((f, i) => { mods[f] = 'mod_' + base(f).replace(/\.py$/, '').replace(/\W+/g, '_') + (files.filter(g => base(g) === base(f)).length > 1 ? '_' + i : ''); });
    const L = [`"""Generated by Flowbench on ${new Date().toISOString().slice(0, 16).replace('T', ' ')}.`, 'Run with:  python this_file.py', '"""', 'import importlib.util', 'import os', 'import sys', '', 'try:', '    import numpy as np', 'except ImportError:', '    np = None', '', '',
      'def _load(path):', '    """Import a .py file by its path."""', '    sys.path.insert(0, os.path.dirname(path))', '    spec = importlib.util.spec_from_file_location(os.path.splitext(os.path.basename(path))[0], path)', '    mod = importlib.util.module_from_spec(spec)', '    spec.loader.exec_module(mod)', '    return mod', '', ''];
    files.forEach(f => L.push(`${mods[f]} = _load(r"${f}")`));
    if (files.length) L.push('', '');
    order.forEach(id => {
      const n = node(id), v = name[id], ref = p => ins[id][p] ? name[ins[id][p]] : null;
      L.push(`# ── ${n.kind}: ${n.title}`);
      if (n.kind === 'value') L.push(`${v} = ${(n.data.expr || 'None').trim() || 'None'}`);
      else if (n.kind === 'func') {
        const args = (n.data.params || []).map(p => { const r = ref(p.name), t = String((n.data.values || {})[p.name] || '').trim(); return r ? `${p.name}=${r}` : t ? `${p.name}=${t}` : null; }).filter(Boolean);
        L.push(`${v} = ${mods[n.data.file]}.${n.data.func}(${args.join(', ')})`);
      } else if (n.kind === 'code' && (langOf(n) !== 'python' || n.data.container || n.data.file)) {
        L.push(`# (${(LANGS[langOf(n)] || {}).label || langOf(n)} node — run it in Flowbench; not converted to Python)`, `${v} = None`);
      } else if (n.kind === 'code') {
        const params = inPorts(n), fn = '_' + v.replace(/^n_/, 'code_');
        L.push(`def ${fn}(${params.join(', ')}):`);
        (n.data.code || 'pass').replace(/\t/g, '    ').split('\n').forEach(line => L.push(line.trim() ? '    ' + line : ''));
        L.push('    return locals().get("result")');
        L.push(`${v} = ${fn}(${params.map(p => ref(p) || ((n.data.values || {})[p] || 'None')).join(', ')})`);
      } else {
        const r = ref('data'); L.push(`${v} = ${r || 'None'}`, `print("${n.title.replace(/"/g, '\\"')}:", ${v})`);
      }
      L.push('');
    });
    L.push('', 'if "matplotlib.pyplot" in sys.modules:', '    sys.modules["matplotlib.pyplot"].show()', '');
    const fname = (prompt('Script name', 'workflow') || '').trim(); if (!fname) return;
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([L.join('\n')], { type: 'text/x-python' }));
    a.download = fname.replace(/\.py$/i, '') + '.py'; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 3000);
    toast('Exported ' + a.download + ' · run it with: python ' + a.download, 4000);
  }
  $('#export-py').onclick = exportPy;

  /* ───────── Keyboard ───────── */
  document.addEventListener('keydown', e => {
    const tg = e.target instanceof Element ? e.target : document.body;
    if (e.key === ' ' && !tg.closest('input, textarea, select, .xterm')) spaceDown = true;
    if (tg.closest('input, textarea, select, .xterm, .palette')) {
      if (e.key === 'Escape') e.target.blur();
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); run(null); }
      return;
    }
    const mod = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
    if (k === 'delete' || k === 'backspace') { e.preventDefault(); deleteSel(); }
    else if (mod && k === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); }
    else if (mod && k === 'y') { e.preventDefault(); redo(); }
    else if (mod && k === 'c') copySel();
    else if (mod && k === 'v') paste();
    else if (mod && k === 'd') { e.preventDefault(); copySel(); paste(); }
    else if (mod && k === 'a') { e.preventDefault(); S.nodes.forEach(n => sel.add(n.id)); markSel(); }
    else if (mod && k === 's') { e.preventDefault(); $('#save-flow').click(); }
    else if (k === 'enter' && mod) { e.preventDefault(); run(null); }
    else if (k === 'enter' && e.shiftKey) { e.preventDefault(); $('#run-sel').click(); }
    else if (k === 'f') fit();
    else if (k === 't' && !mod) tidy();
    else if (e.key === '?') $('#help-modal').hidden = false;
    else if (k === 'escape') { sel.clear(); selEdge = null; markSel(); renderEdges(); closePalette(); $$('.modal').forEach(m => { m.hidden = true; }); }
  });
  document.addEventListener('keyup', e => { if (e.key === ' ') spaceDown = false; });
  $('#undo').onclick = undo; $('#redo').onclick = redo;

  /* ───────── Theme ───────── */
  const th = store.get('flowbench:theme', 'dark'); if (th === 'light') document.documentElement.dataset.theme = 'light';
  $('#theme-btn').onclick = () => {
    const light = document.documentElement.dataset.theme !== 'light';
    if (light) document.documentElement.dataset.theme = 'light'; else delete document.documentElement.dataset.theme;
    store.set('flowbench:theme', light ? 'light' : 'dark'); renderEdges(); drawMinimap(); S.nodes.forEach(n => rerenderPlots(n.id));
  };

  /* ───────── Boot ───────── */
  const firstRun = !S.nodes.length;
  if (firstRun) {
    const note = { id: uid(), kind: 'note', x: 40, y: 30, w: 330, h: 230, title: 'start here', data: { text: 'Welcome to Flowbench.\n\n1. Connect the runner (top left) so the library shows your Python files.\n2. Drag a function from the library onto the canvas.\n3. Drag from a ● output to a ● input to wire nodes together.\n4. Press RUN (top bar). Results, tables and plots appear inside the nodes.\n5. Open terminals (>_ TERMINAL) to check results in parallel.\n\nThe small workflow on the right is a demo: run it!' } };
    const v = { id: uid(), kind: 'value', x: 420, y: 40, w: 280, title: 'x axis', data: { expr: 'np.linspace(0, 12, 400)' } };
    const c = { id: uid(), kind: 'code', x: 760, y: 30, w: 360, h: 300, title: 'damped sine', data: { inputs: 'x', values: {}, code: '# inputs become variables; assign your output to `result`\nimport matplotlib.pyplot as plt\ny = np.sin(x) * np.exp(-x / 5)\nplt.figure(figsize=(5, 2.6))\nplt.plot(x, y)\nplt.title("damped sine")\nresult = y' } };
    const w = { id: uid(), kind: 'viewer', x: 1180, y: 30, w: 420, h: 380, title: 'result', data: {} };
    S.nodes = [note, v, c, w]; S.edges = [{ id: uid(), from: v.id, to: c.id, port: 'x' }, { id: uid(), from: c.id, to: w.id, port: 'data' }];
    lastSnap = snap(); save();
  }
  renderAll(); renderLib(); setStatus('off');
  requestAnimationFrame(() => { if (firstRun) fit(); renderEdges(); drawMinimap(); });
  window.addEventListener('resize', () => { applyView(); });
  connect();
  if (!RC.token) setTimeout(() => { if (!RC.ok) openConnect(); }, 600);
  window.FLOWBENCH = { S, R, RC, run, exportPy, addNode, addFuncNode, connectEdge, fit, renderAll };
})();
