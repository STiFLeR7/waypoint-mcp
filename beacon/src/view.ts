// The chart card for show_chart (MCP Apps, SEP-1865): one inline HTML document, no SDK, text-only rendering.
// Bump VIEW_URI (-v2) on any change the old data shape or old HTML can't handle: hosts cache templates by URI.
export const VIEW_URI = "ui://waypoint/chart-v1";
export const VIEW_MIME = "text/html;profile=mcp-app";
export const VIEW_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="color-scheme" content="light dark">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:">
<title>Waypoint chart</title>
<style>
:root{color-scheme:light dark;
 --bg:var(--color-background-primary,light-dark(#fff,#1b1b1b));
 --ink:var(--color-text-primary,light-dark(#1c2028,#f1f1f1));
 --muted:var(--color-text-secondary,light-dark(#5b626e,#a6abb3));
 --line:var(--color-border-tertiary,light-dark(#e2e0da,#3a3a3a));
 --attn:var(--color-text-danger,light-dark(#a13d18,#ff8f6b));
 --unlog:var(--color-text-info,light-dark(#6a45a8,#c4a8ff));
 font:var(--font-text-sm-size,13px)/1.45 var(--font-sans,system-ui,sans-serif);color:var(--ink);background:var(--bg)}
body{margin:0;padding:14px 16px}h1{font-size:15px;margin:0 0 2px}p{margin:0}
.meta{color:var(--muted);font-size:12px;margin-bottom:10px}
.kpis{display:flex;flex-wrap:wrap;gap:6px 14px;margin-bottom:12px;font-size:12px;color:var(--muted)}
.kpis b{color:var(--ink);font-variant-numeric:tabular-nums;margin-right:4px}
ul{list-style:none;margin:0;padding:0}li{padding:8px 0;border-top:1px solid var(--line);min-width:0}
.row{display:flex;justify-content:space-between;gap:10px}.name{font-weight:600;overflow-wrap:anywhere}
.status,.when{color:var(--muted);font-size:12px;white-space:nowrap}
.next{overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.sig{font-size:12px;margin-right:10px}.sig.attn{color:var(--attn)}.sig.unlog{color:var(--unlog)}
</style>
</head>
<body>
<h1>Waypoint chart</h1>
<p class="meta" id="meta">Waiting for the chart…</p>
<div class="kpis" id="kpis"></div>
<ul id="list"></ul>
<script>
'use strict';
// MCP Apps view (SEP-1865, 2026-01-26), no SDK. Every value is untrusted: text only, never HTML.
const host = window.parent, pending = new Map();
let nextId = 1;
const send = m => host.postMessage(Object.assign({ jsonrpc: '2.0' }, m), '*');
const request = (method, params) => new Promise((resolve, reject) => {
  const id = nextId++; pending.set(id, { resolve, reject }); send({ id, method, params });
});
const notify = (method, params) => send({ method, params });
const $ = id => document.getElementById(id);
function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = String(text); return n; }
const str = v => typeof v === 'string' ? v : '';
const num = v => Number.isFinite(v) ? v : null;

function applyContext(ctx) {
  if (!ctx || typeof ctx !== 'object') return;
  const root = document.documentElement;
  if (ctx.theme === 'light' || ctx.theme === 'dark') { root.dataset.theme = ctx.theme; root.style.colorScheme = ctx.theme; }
  const vars = ctx.styles && ctx.styles.variables;
  if (vars && typeof vars === 'object') for (const [k, v] of Object.entries(vars)) if (k.startsWith('--') && typeof v === 'string') root.style.setProperty(k, v);
}

function render(result) {
  const sc = result && result.structuredContent;
  if (!sc || typeof sc !== 'object' || !Array.isArray(sc.projects)) {
    const t = result && Array.isArray(result.content) && result.content[0] && result.content[0].text;
    $('meta').textContent = result && result.isError ? str(t) || 'The beacon returned an error.' : 'No chart data in this result.';
    return;
  }
  const gen = str(sc.chart_generated);
  $('meta').textContent = (gen ? 'Taken ' + new Date(gen).toLocaleString() : 'Generation time unknown') +
    (sc.data_complete === false ? '. Some sources were incomplete.' : sc.data_complete == null ? '. Source health unknown.' : '.');
  const k = sc.kpi && typeof sc.kpi === 'object' ? sc.kpi : {};
  $('kpis').replaceChildren(...[['tracked', 'tracked'], ['unlogged', 'unlogged'], ['behind', 'Now behind log'], ['stale', 'stale'], ['attention', 'attention']]
    .filter(([key]) => num(k[key]) !== null).map(([key, label]) => { const s = el('span'); s.append(el('b', null, k[key]), label); return s; }));
  $('list').replaceChildren(...sc.projects.filter(p => p && str(p.name)).map(p => {
    const li = el('li'), top = el('div', 'row'), idle = num(p.idle);
    top.append(el('span', 'name', p.name), el('span', 'status', str(p.status) || 'Status unknown'));
    li.append(top);
    const sig = el('p');
    if (num(p.unlogged) > 0) sig.append(el('span', 'sig unlog', p.unlogged + (p.unlogged === 1 ? ' session' : ' sessions') + ' not logged'));
    if (p.now_behind === true) sig.append(el('span', 'sig attn', 'Now behind log'));
    if (p.unlogged == null) sig.append(el('span', 'sig', 'Logging unknown'));
    if (sig.childNodes.length) li.append(sig);
    li.append(el('p', 'next' + (str(p.next) ? '' : ' status'), str(p.next) || 'No next action recorded.'));
    li.append(el('p', 'when', (idle === null ? 'Activity unknown' : idle === 0 ? 'Today' : idle === 1 ? 'Yesterday' : idle + ' days ago') + (str(p.last_tool) ? ' · ' + p.last_tool : '')));
    return li;
  }));
}

window.addEventListener('message', e => {
  if (e.source !== host) return;
  const m = e.data;
  if (!m || m.jsonrpc !== '2.0') return;
  if (m.method === undefined) { const p = pending.get(m.id); if (p) { pending.delete(m.id); m.error ? p.reject(m.error) : p.resolve(m.result); } return; }
  switch (m.method) {
    case 'ui/notifications/tool-result': render(m.params); break;
    case 'ui/notifications/host-context-changed': applyContext(m.params); break;
    case 'ui/notifications/tool-cancelled': $('meta').textContent = 'Cancelled.'; break;
    case 'ui/resource-teardown': case 'ping': if (m.id !== undefined) send({ id: m.id, result: {} }); break;
    default: if (m.id !== undefined) send({ id: m.id, error: { code: -32601, message: 'Method not found' } });
  }
});

function autoResize() { // same measurement as the SDK's setupSizeChangedNotifications
  let w = 0, h = 0, queued = false;
  const measure = () => { if (queued) return; queued = true; requestAnimationFrame(() => {
    queued = false; const html = document.documentElement, old = html.style.height;
    html.style.height = 'max-content'; const height = Math.ceil(html.getBoundingClientRect().height); html.style.height = old;
    const width = Math.ceil(window.innerWidth);
    if (width !== w || height !== h) { w = width; h = height; notify('ui/notifications/size-changed', { width, height }); }
  }); };
  measure(); const ro = new ResizeObserver(measure); ro.observe(document.documentElement); ro.observe(document.body);
}

request('ui/initialize', { appInfo: { name: 'waypoint-chart', version: '1' }, appCapabilities: { availableDisplayModes: ['inline'] }, protocolVersion: '2026-01-26' })
  .then(r => { applyContext(r && r.hostContext); notify('ui/notifications/initialized', {}); autoResize(); })
  .catch(() => { $('meta').textContent = 'Could not connect to the host.'; });
</script>
</body>
</html>`;
