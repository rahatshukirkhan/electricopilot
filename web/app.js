'use strict';
/* ElectriCopilot Studio v2 — multi-project distribution-board workbench (docs/11). */
const $ = (id) => document.getElementById(id);
const API = '';
const fmt = (x, d = 2) => (x === null || x === undefined || x === '') ? '—'
  : (Math.abs(x) >= 100 ? Math.round(x) : +(+x).toFixed(d)).toString().replace('.', ',');
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const nowISO = () => new Date().toISOString();
const uid = (p) => p + '_' + (crypto.randomUUID ? crypto.randomUUID().slice(0, 8) : Math.random().toString(16).slice(2, 10));
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

// ---------- storage ----------
const K_WS = 'ec_v2_workspace', K_IDX = 'ec_v2_projects', KP = id => 'ec_v2_project_' + id;
function jget(k, def) { try { return JSON.parse(localStorage.getItem(k)) ?? def; } catch { return def; } }
function jset(k, v) { localStorage.setItem(k, JSON.stringify(v)); }
function wsGet() { let w = jget(K_WS, null); if (!w) { w = { schema_version: 2, id: uid('ws'), created_at: nowISO() }; jset(K_WS, w); } return w; }
function idxGet() { return jget(K_IDX, []); }
function idxSet(a) { jset(K_IDX, a); }
function projGet(id) { return jget(KP(id), null); }
function setSave(state) { const el = $('saveState'); el.className = 'save ' + state; el.textContent = { saving: 'сохранение…', saved: 'сохранено', unsaved: 'не сохранено' }[state] || ''; }
function projSet(p) {
  setSave('saving');
  p.updated_at = nowISO();
  p.rollup = rollup(p);
  jset(KP(p.id), p);
  const idx = idxGet().filter(x => x.id !== p.id);
  idx.unshift({ id: p.id, name: p.name, board_ref: p.board_ref, updated_at: p.updated_at, rollup: p.rollup, count: (p.circuits || []).length });
  idxSet(idx);
  setSave('saved');
}
function projDel(id) { localStorage.removeItem(KP(id)); idxSet(idxGet().filter(x => x.id !== id)); }
function rollup(p) {
  const c = { PASS: 0, FAIL: 0, NEEDS_REVIEW: 0 };
  (p.circuits || []).forEach(ck => { const s = ck.result?.status; if (s in c) c[s]++; });
  const status = c.FAIL ? 'FAIL' : (c.NEEDS_REVIEW ? 'NEEDS_REVIEW' : 'PASS');
  return { counts: c, status };
}

// ---------- models ----------
function blankRequest() {
  return {
    load: { description: 'Новая цепь', power_w: 2000, voltage_v: 230, phases: 1, power_factor: 0.9, purpose: 'general' },
    installation: { method: 'C', material: 'Cu', insulation: 'PVC', ambient_temp_c: 30, grouping_circuits: 1, length_m: 20 },
    protection: { device_class: 'MCB', prospective_fault_current_a: 3000, disconnection_time_s: 0.01, max_voltage_drop_pct: null, trip_curve_type: 'C' },
  };
}
function blankProject(name) {
  return {
    schema_version: 2, id: uid('prj'), name: name || 'Новый щит', board_ref: 'DB-1', location: '',
    created_at: nowISO(), updated_at: nowISO(), norm_pack: null,
    supply: { voltage_v: 400, phases: 3, ways_total: 12, earthing: 'TN-C-S', method: 'C', material: 'Cu', insulation: 'PVC', ambient_temp_c: 30 },
    diversity: { factors: { lighting: 0.9, socket: 0.5, motor: 1.0, power: 0.8, general: 0.7 } },
    circuits: [],
  };
}
function newCircuit(request, meta, ref) {
  return { id: uid('ckt'), ref: ref || '', sort_index: 0, request: request || blankRequest(),
    meta: meta || { phase: 'L1', rcd: { present: false }, diversity_category: (request?.load?.purpose || 'general') },
    result: null, signoff: { status: 'UNSIGNED_ADVISORY' } };
}
function sampleProject() {
  const p = blankProject('Щит ВРУ-1 (пример)'); p.board_ref = 'DB-1'; p.location = 'Эл.щитовая';
  const mk = (ref, desc, P, U, ph, pf, purpose, ins, amb, g, L, dev, curve, iscc, phase, rcd) => {
    const c = newCircuit({
      load: { description: desc, power_w: P, voltage_v: U, phases: ph, power_factor: pf, purpose },
      installation: { method: 'C', material: 'Cu', insulation: ins, ambient_temp_c: amb, grouping_circuits: g, length_m: L },
      protection: { device_class: dev, prospective_fault_current_a: iscc, disconnection_time_s: 0.01, max_voltage_drop_pct: null, trip_curve_type: curve },
    }, { phase, rcd: rcd ? { present: true, type: 'RCBO', ma: 30 } : { present: false }, diversity_category: purpose }, ref);
    return c;
  };
  p.circuits = [
    mk('L1', 'Розетки кухни', 3680, 230, 1, 0.95, 'socket', 'PVC', 30, 1, 18, 'MCB', 'C', 1500, 'L1', true),
    mk('L2', 'Освещение', 1200, 230, 1, 1.0, 'lighting', 'PVC', 30, 1, 25, 'MCB', 'B', 800, 'L2', false),
    mk('L3', 'Бойлер', 3000, 230, 1, 1.0, 'power', 'PVC', 30, 1, 20, 'MCB', 'C', 1500, 'L3', true),
    mk('M1', 'Двигатель 15 кВт', 15000, 400, 3, 0.85, 'motor', 'XLPE', 40, 3, 50, 'gG_fuse', 'C', 4000, 'L1L2L3', false),
  ];
  p.circuits.forEach((c, i) => c.sort_index = i);
  return p;
}

// ---------- state ----------
let PROJ = null, CID = null, VIZ = null, HEALTH = { mode: 'fallback' }, PACKS = [];
function packQuery() { return PROJ?.norm_pack ? ('?pack=' + encodeURIComponent(PROJ.norm_pack)) : ''; }
function packStatusBadge(el, pack) {
  const status = pack?.status || '';
  const origin = { illustrative: 'синтетические', public_standard: 'публичный стандарт', licensed: 'лицензия' }[status] || status;
  const verification = pack?.verification_status || 'NEEDS_REVIEW';
  el.textContent = [origin, verification].filter(Boolean).join(' · ');
  el.className = 'badge small ' + (verification === 'VERIFIED' ? 'pass' : 'review');
}

// ---------- toast ----------
let toastTimer;
function toast(msg, actionLabel, action) {
  const t = $('toast'); t.hidden = false;
  t.innerHTML = `<span>${esc(msg)}</span>`;
  if (actionLabel) { const b = document.createElement('button'); b.textContent = actionLabel; b.onclick = () => { action(); t.hidden = true; }; t.appendChild(b); }
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 6000);
}

// ---------- router ----------
function go(hash) { location.hash = hash; }
function route() {
  const h = location.hash.replace(/^#/, '') || '/';
  const mp = h.match(/^\/p\/([^/]+)\/print$/);
  const m = h.match(/^\/p\/([^/]+)(?:\/c\/([^/]+))?/);
  ['dashboard', 'project', 'editor', 'print'].forEach(s => $('screen-' + s).hidden = true);
  $('advisory').hidden = mp ? true : false;
  if (mp) {
    PROJ = projGet(mp[1]);
    if (!PROJ) { go('/'); return; }
    show('print'); renderPrint(); crumbs([['Проекты', '#/'], [PROJ.name, '#/p/' + PROJ.id], ['Печать', '']]); topBadge('');
    return;
  }
  if (!m) { renderDashboard(); show('dashboard'); crumbs([['Проекты', '#/']]); topBadge(''); return; }
  const pid = m[1], cid = m[2];
  PROJ = projGet(pid);
  if (!PROJ) { go('/'); return; }
  if (cid) { openEditor(cid); show('editor'); }
  else { renderProject(); show('project'); }
}
function show(s) { $('screen-' + s).hidden = false; }
function crumbs(items) { $('crumbs').innerHTML = items.map((it, i) => i < items.length - 1 ? `<a data-h="${it[1]}">${esc(it[0])}</a> ›` : `<span>${esc(it[0])}</span>`).join(' '); }
$('crumbs').addEventListener('click', e => { const h = e.target.dataset.h; if (h) go(h.replace(/^#/, '')); });
$('homeLink').addEventListener('click', () => go('/'));
function topBadge(s) { const b = $('statusBadge'); b.textContent = s || '—'; b.className = 'badge ' + ({ PASS: 'pass', FAIL: 'fail', NEEDS_REVIEW: 'review' }[s] || ''); }

// ========================= DASHBOARD =========================
function renderDashboard(filter) {
  let idx = idxGet().sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
  if (filter) idx = idx.filter(p => (p.name + ' ' + (p.board_ref || '')).toLowerCase().includes(filter.toLowerCase()));
  const grid = $('projectGrid');
  if (!idx.length) {
    grid.innerHTML = `<div class="empty">Пока нет щитов.<br><br>Нажми <b>+ Новый щит</b> или <b>Загрузить пример</b>, чтобы начать.</div>`; return;
  }
  grid.innerHTML = idx.map(p => {
    const r = p.rollup?.counts || { PASS: 0, FAIL: 0, NEEDS_REVIEW: 0 };
    const chip = (n, cls, lab) => `<span class="chip ${n ? cls : 'n'}">${n} ${lab}</span>`;
    return `<div class="card" data-open="${p.id}">
      <h3>${esc(p.name)} <span class="cref">${esc(p.board_ref || '')}</span></h3>
      <div class="chips">${chip(p.count || 0, 'n', 'цепей')}${chip(r.PASS, 'pass', 'PASS')}${chip(r.NEEDS_REVIEW, 'review', 'REVIEW')}${chip(r.FAIL, 'fail', 'FAIL')}</div>
      <div class="cmeta"><span>изменён ${when(p.updated_at)}</span></div>
      <div class="cact">
        <button data-open="${p.id}">Открыть</button>
        <button data-rename="${p.id}">Переименовать</button>
        <button data-dup="${p.id}">Дублировать</button>
        <button data-export="${p.id}">Экспорт</button>
        <button data-del="${p.id}">Удалить</button>
      </div></div>`;
  }).join('');
}
function when(iso) { if (!iso) return '—'; const s = (Date.now() - new Date(iso)) / 1000; if (s < 60) return 'только что'; if (s < 3600) return Math.floor(s / 60) + ' мин назад'; if (s < 86400) return Math.floor(s / 3600) + ' ч назад'; return new Date(iso).toLocaleDateString('ru'); }

$('projectGrid').addEventListener('click', e => {
  const t = e.target, d = t.dataset;
  if (d.del) { const id = d.del; const p = projGet(id); projDel(id); renderDashboard($('dashSearch').value); toast(`Щит «${p?.name || ''}» удалён`, 'Отменить', () => { projSet(p); renderDashboard(); }); return; }
  if (d.rename) { const p = projGet(d.rename); const n = prompt('Имя щита:', p.name); if (n) { p.name = n; projSet(p); renderDashboard($('dashSearch').value); } return; }
  if (d.dup) { const p = projGet(d.dup); const copy = deepCopyProject(p, p.name + ' (копия)'); projSet(copy); renderDashboard(); return; }
  if (d.export) { exportProject(projGet(d.export)); return; }
  if (d.open) { go('/p/' + d.open); }
});
$('btnNewProject').addEventListener('click', () => { const p = blankProject(); projSet(p); go('/p/' + p.id); });
$('btnSample').addEventListener('click', () => { const p = sampleProject(); projSet(p); go('/p/' + p.id); });
$('dashSearch').addEventListener('input', e => renderDashboard(e.target.value));
$('importFile').addEventListener('change', importProject);

function deepCopyProject(p, name) {
  const c = JSON.parse(JSON.stringify(p));
  c.id = uid('prj'); c.name = name || c.name; c.created_at = c.updated_at = nowISO();
  c.circuits.forEach(ck => { ck.id = uid('ckt'); });
  return c;
}

// ========================= PROJECT / BOARD =========================
async function renderProject() {
  const p = PROJ;
  crumbs([['Проекты', '#/'], [p.name, '#/p/' + p.id]]);
  $('b_name').value = p.name; $('b_ref').value = p.board_ref || ''; $('b_location').value = p.location || '';
  $('b_supply').textContent = `${p.supply.voltage_v} В · ${p.supply.phases}ф · ${p.supply.earthing} · мест ${p.supply.ways_total}`;
  $('projDisclaimer').textContent = 'Рекомендательный расчёт; требуется подпись инженера по каждой цепи и по щиту.';
  renderPackSelect();
  const body = $('scheduleBody');
  if (!p.circuits.length) { body.innerHTML = `<tr><td colspan="14" class="empty" style="border:none">Пусто — добавь цепь или опиши словами ниже.</td></tr>`; $('boardTotals').innerHTML = ''; topBadge(''); $('boardRollup').textContent = '—'; $('boardRollup').className = 'badge'; return; }
  body.innerHTML = `<tr><td colspan="14" style="color:var(--dim)">пересчёт цепей движком…</td></tr>`;
  let rep;
  // ?sld=1 → the preview SVG comes back with the report (one server call / one engine pass).
  try { rep = await postJSON('/api/project-report?sld=1', { project: p }); }
  catch (e) { body.innerHTML = `<tr><td colspan="14" style="color:var(--bad)">Ошибка пересчёта: ${esc(e.message)}</td></tr>`; return; }
  // sync snapshots back for dashboard rollup
  rep.rows.forEach(r => { const c = p.circuits.find(x => x.id === r.id); if (c) c.result = { status: r.status, section: null, In: null, IB: r.IB_a, Iz: r.Iz_a, vd: r.dU_pct, governing: r.governing }; });
  projSet(p);
  body.innerHTML = rep.rows.map(r => rowHTML(r)).join('');
  const b = rep.board;
  topBadge(b.status);
  $('boardRollup').textContent = `щит: ${b.status}`; $('boardRollup').className = 'badge ' + ({ PASS: 'pass', FAIL: 'fail', NEEDS_REVIEW: 'review' }[b.status] || '');
  renderTotals(b);
  renderSldInto(rep.sld);
}
function renderPackSelect() {
  const sel = $('b_pack');
  const cur = PROJ.norm_pack || PACKS[0]?.name || '';
  sel.innerHTML = PACKS.map(pk => `<option value="${esc(pk.name)}">${esc(pk.name)} (${esc(pk.version)})</option>`).join('');
  sel.value = cur;
  packStatusBadge($('b_packStatus'), PACKS.find(pk => pk.name === cur));
}
$('b_pack').addEventListener('change', () => { PROJ.norm_pack = $('b_pack').value; projSet(PROJ); renderProject(); });
function rowHTML(r) {
  const sign = r.signoff === 'SIGNED' ? ' ✔' : '';
  return `<tr data-cid="${r.id}">
    <td class="mono">${esc(r.ref)}</td><td>${esc(r.description)}</td><td>${fmt(r.kw)}</td><td>${fmt(r.pf)}</td>
    <td class="mono">${esc(r.phase)}</td><td>${fmt(r.IB_a, 1)}</td><td>${esc(r.device)}</td><td>${esc(r.rcd)}</td>
    <td>${esc(r.cable)}</td><td>${fmt(r.length_m)}</td><td>${fmt(r.Iz_a, 1)}</td><td>${fmt(r.dU_pct)}</td>
    <td class="st-cell ${r.status}">${r.status}${sign}</td>
    <td><div class="rowact">
      <button data-edit="${r.id}" title="Править" aria-label="Править цепь"><svg class="icon icon-sm" viewBox="0 0 24 24" style="pointer-events:none"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg></button>
      <button data-dupc="${r.id}" title="Дублировать" aria-label="Дублировать цепь"><svg class="icon icon-sm" viewBox="0 0 24 24" style="pointer-events:none"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg></button>
      <button data-delc="${r.id}" title="Удалить" aria-label="Удалить цепь"><svg class="icon icon-sm" viewBox="0 0 24 24" style="pointer-events:none"><path d="M3 6h18"/><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/><path d="M6 6v14a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V6"/></svg></button>
    </div></td></tr>`;
}
function renderTotals(b) {
  const t = b.totals, d = b.demand, w = b.ways, ph = t.phase;
  const mx = Math.max(ph.L1.A, ph.L2.A, ph.L3.A, 1);
  $('boardTotals').innerHTML = `
    <div class="totbox"><h4>Подключённая нагрузка</h4><div class="big">${fmt(t.connected_kw)} кВт</div><div style="color:var(--dim)">${fmt(t.connected_kva)} кВА</div></div>
    <div class="totbox"><h4>Баланс фаз (реальный)</h4>
      <div class="phbar">${['L1', 'L2', 'L3'].map(k => `<div class="b" style="height:${Math.round(ph[k].A / mx * 100)}%"><span>${k}<br>${fmt(ph[k].A, 0)}A</span></div>`).join('')}</div>
      <div style="margin-top:20px" class="${t.imbalance_flag ? 'warn' : ''}">перекос ${fmt(t.imbalance_pct, 0)}%${t.imbalance_flag ? ' ⚠' : ''}</div></div>
    <div class="totbox"><h4>Расчётная нагрузка <span style="color:var(--warn)">(иллюстр.)</span></h4><div class="big">${fmt(d.emd_kw)} кВт</div><div style="color:var(--dim)">ток ввода ≈ ${fmt(d.incomer_md_a, 0)} A · df синтетические</div></div>
    <div class="totbox"><h4>Резерв мест</h4><div class="big">${w.spare} / ${w.total}</div><div style="color:var(--dim)">занято ${w.used}</div></div>`;
}
$('scheduleBody').addEventListener('click', e => {
  const d = e.target.dataset;
  if (d.edit) go('/p/' + PROJ.id + '/c/' + d.edit);
  else if (d.delc) { const c = PROJ.circuits.find(x => x.id === d.delc); PROJ.circuits = PROJ.circuits.filter(x => x.id !== d.delc); projSet(PROJ); renderProject(); toast(`Цепь «${c?.ref || ''}» удалена`, 'Отменить', () => { PROJ.circuits.push(c); projSet(PROJ); renderProject(); }); }
  else if (d.dupc) { const c = PROJ.circuits.find(x => x.id === d.dupc); const copy = JSON.parse(JSON.stringify(c)); copy.id = uid('ckt'); copy.ref = (c.ref || '') + '\''; copy.sort_index = PROJ.circuits.length; PROJ.circuits.push(copy); projSet(PROJ); renderProject(); }
  else { const tr = e.target.closest('tr'); if (tr?.dataset.cid) go('/p/' + PROJ.id + '/c/' + tr.dataset.cid); }
});
const saveBoardMeta = debounce(() => { PROJ.name = $('b_name').value; PROJ.board_ref = $('b_ref').value; PROJ.location = $('b_location').value; projSet(PROJ); crumbs([['Проекты', '#/'], [PROJ.name, '#/p/' + PROJ.id]]); }, 500);
['b_name', 'b_ref', 'b_location'].forEach(id => $(id).addEventListener('input', () => { setSave('unsaved'); saveBoardMeta(); }));
$('btnBackDash').addEventListener('click', () => go('/'));
$('btnAddCircuit').addEventListener('click', () => { const c = newCircuit(); c.sort_index = PROJ.circuits.length; c.ref = 'C' + (PROJ.circuits.length + 1); PROJ.circuits.push(c); projSet(PROJ); go('/p/' + PROJ.id + '/c/' + c.id); });
$('btnNlAdd').addEventListener('click', nlAddCircuit);
$('nlQuick').addEventListener('keydown', e => { if (e.key === 'Enter') nlAddCircuit(); });
$('btnExportProj').addEventListener('click', () => exportProject(PROJ));
$('btnReport').addEventListener('click', downloadReport);
$('btnBundle').addEventListener('click', downloadBundle);
$('btnPrint').addEventListener('click', () => { if (PROJ) go('/p/' + PROJ.id + '/print'); });
$('btnSldRefresh').addEventListener('click', loadSldPreview);

// ---------- single-line preview + document bundle (docs/13) ----------
function renderSldInto(sld) {  // sld = {svg, sheets} from the report (?sld=1) or /api/project-sld
  const el = $('sldPreview');
  if (!sld || !sld.svg) { el.innerHTML = '<span class="dim">Нет цепей — добавь цепь.</span>'; return; }
  const note = sld.sheets > 1 ? `<div class="dim" style="margin-bottom:6px">Листов: ${sld.sheets} (показан 1-й; полный набор — в пакете документов).</div>` : '';
  el.innerHTML = note + sld.svg;
}
async function loadSldPreview() {  // manual "Обновить предпросмотр" — fetches fresh
  const el = $('sldPreview');
  if (!PROJ || !PROJ.circuits.length) { el.innerHTML = '<span class="dim">Нет цепей — добавь цепь.</span>'; return; }
  el.innerHTML = '<span class="dim">Строю однолинейку…</span>';
  try { renderSldInto(await postJSON('/api/project-sld', { project: PROJ })); }
  catch (e) { el.innerHTML = `<span style="color:var(--bad)">⚠ ${esc(e.message)}</span>`; }
}
async function downloadBundle() {
  if (!PROJ || !PROJ.circuits.length) { toast('Добавь хотя бы одну цепь'); return; }
  toast('Готовлю пакет документов…');
  try {
    const r = await fetch(API + '/api/project-export', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project: PROJ }) });
    if (!r.ok) { toast('⚠ ошибка экспорта: HTTP ' + r.status); return; }
    const name = (PROJ.board_ref || PROJ.name || 'board').replace(/\s+/g, '_') + '_пакет.zip';
    download(name, await r.blob(), 'application/zip');
    toast('Пакет документов скачан (SVG+DXF+XLSX+MD)');
  } catch (e) { toast('⚠ ' + e.message); }
}
async function renderPrint() {
  const root = $('printRoot');
  root.innerHTML = '<p class="dim">Готовлю печатную страницу…</p>';
  let rep;
  // Always fetch a FRESH report (with the SLD) in one call — the print route reloads PROJ from
  // localStorage, so a cached _lastReport would risk printing a stale schedule beside a fresh diagram.
  try { rep = await postJSON('/api/project-report?sld=1', { project: PROJ }); }
  catch (e) { root.innerHTML = `<p style="color:var(--bad)">⚠ ${esc(e.message)}</p>`; return; }
  const sld = rep.sld || { svg: '' };
  const b = rep.board, t = b.totals, sp = PROJ.supply || {};
  const rows = rep.rows.map(r => `<tr><td>${esc(r.ref)}</td><td>${esc(r.description)}</td><td>${fmt(r.kw)}</td><td>${esc(r.phase)}</td><td>${fmt(r.IB_a, 1)}</td><td>${esc(r.device)}</td><td>${esc(r.rcd)}</td><td>${esc(r.cable)}</td><td>${fmt(r.length_m)}</td><td>${fmt(r.Iz_a, 1)}</td><td>${fmt(r.dU_pct)}</td><td>${esc(r.status)}</td></tr>`).join('');
  root.innerHTML = `
    <div class="print-actions no-print"><button id="doPrint" class="primary">🖨 Печать / Сохранить PDF</button> <button id="printBack" class="ghost">← Назад к щиту</button></div>
    <h1 class="ptitle">${esc(PROJ.name)} <small>${esc(PROJ.board_ref || '')}</small></h1>
    <p class="pmeta">Питание: ${esc(sp.voltage_v || 400)} В · ${esc(sp.phases || 3)}ф · ${esc(sp.earthing || 'TN-C-S')} · мест ${b.ways.used}/${b.ways.total}</p>
    <p class="pnote">${esc(rep.data_identity || '')}</p>
    <h2>Таблица щита (panel schedule)</h2>
    <table class="ptable"><thead><tr><th>Ref</th><th>Описание</th><th>кВт</th><th>Фаза</th><th>IB,A</th><th>Аппарат</th><th>УЗО</th><th>Кабель</th><th>L,м</th><th>IZ,A</th><th>ΔU%</th><th>Статус</th></tr></thead><tbody>${rows}</tbody></table>
    <h2>Итоги щита</h2>
    <p class="pmeta">Подключённая нагрузка: <b>${fmt(t.connected_kw)} кВт / ${fmt(t.connected_kva)} кВА</b> · перекос фаз ${fmt(t.imbalance_pct, 0)}%${t.imbalance_flag ? ' ⚠' : ''} · резерв мест ${b.ways.spare}/${b.ways.total}</p>
    <h2>Однолинейная схема</h2>
    <div class="print-sld">${sld.svg || ''}</div>
    <p class="pnote">${esc(rep.provenance_note || '')}</p>
    <p class="pnote"><b>${esc(rep.signoff_notice || 'UNSIGNED_ADVISORY')}</b></p>
    <p class="pnote"><b>${esc(rep.disclaimer || '')}</b></p>`;
  $('doPrint').addEventListener('click', () => window.print());
  $('printBack').addEventListener('click', () => go('/p/' + PROJ.id));
}

async function nlAddCircuit() {
  const text = $('nlQuick').value.trim(); if (!text) return;
  $('nlQuick').value = ''; toast('Разбираю описание…');
  try {
    const j = await postJSON('/api/intake', { text });
    if (!j.ok) { toast('⚠ ' + (j.message || 'не удалось разобрать')); return; }
    const c = newCircuit(j.request, { phase: j.request.load.phases === 3 ? 'L1L2L3' : 'L1', rcd: { present: false }, diversity_category: j.request.load.purpose }, 'C' + (PROJ.circuits.length + 1));
    c.sort_index = PROJ.circuits.length; PROJ.circuits.push(c); projSet(PROJ);
    go('/p/' + PROJ.id + '/c/' + c.id);
  } catch (e) { toast('⚠ ошибка: ' + e.message); }
}

// ========================= EDITOR =========================
function openEditor(cid) {
  CID = cid;
  const c = PROJ.circuits.find(x => x.id === cid); if (!c) { go('/p/' + PROJ.id); return; }
  crumbs([['Проекты', '#/'], [PROJ.name, '#/p/' + PROJ.id], [c.ref || 'цепь', '']]);
  fillForm(c.request, c.meta);
  $('edRef').textContent = c.ref || '(без ref)';
  setSignoffBadge(c.signoff);
  recompute();
}
function setSignoffBadge(so) {
  const el = $('edSignoff'); const signed = so?.status === 'SIGNED';
  el.textContent = signed ? `ПОДПИСАНО: ${so.engineer_name || ''}` : 'UNSIGNED_ADVISORY';
  el.className = 'badge small ' + (signed ? 'pass' : 'review');
}
function curCircuit() { return PROJ.circuits.find(x => x.id === CID); }

function buildRequest() {
  const srcType = $('f_srcType').value;
  const load = { description: $('f_desc').value || null, voltage_v: +$('f_voltage').value, phases: +$('f_phases').value, power_factor: +$('f_pf').value, purpose: $('f_purpose').value };
  if (srcType === 'power') load.power_w = +$('f_power').value; else load.current_a = +$('f_current').value;
  return {
    load,
    installation: { method: $('f_method').value, material: $('f_material').value, insulation: $('f_insulation').value, ambient_temp_c: +$('f_ambient').value, grouping_circuits: +$('f_grouping').value, length_m: +$('f_length').value },
    protection: { device_class: $('f_device').value, prospective_fault_current_a: $('f_iscc').value ? +$('f_iscc').value : null, disconnection_time_s: +$('f_tdisc').value, max_voltage_drop_pct: $('f_vdlimit').value ? +$('f_vdlimit').value : null, trip_curve_type: $('f_curve').value },
  };
}
function buildMeta() {
  const rcd = $('f_rcd').value;
  return { phase: $('f_phase').value, rcd: rcd ? { present: true, type: rcd, ma: +$('f_rcd_ma').value } : { present: false }, diversity_category: $('f_purpose').value };
}
function fillForm(req, meta) {
  const l = req.load, i = req.installation, p = req.protection || {};
  $('f_desc').value = l.description || '';
  if (l.power_w != null) { $('f_srcType').value = 'power'; $('f_power').value = l.power_w; }
  if (l.current_a != null) { $('f_srcType').value = 'current'; $('f_current').value = l.current_a; }
  toggleSrc();
  $('f_voltage').value = l.voltage_v; $('f_phases').value = l.phases; if (l.power_factor != null) $('f_pf').value = l.power_factor; if (l.purpose) $('f_purpose').value = l.purpose;
  $('f_method').value = i.method; if (i.material) $('f_material').value = i.material; if (i.insulation) $('f_insulation').value = i.insulation;
  if (i.ambient_temp_c != null) $('f_ambient').value = i.ambient_temp_c; if (i.grouping_circuits != null) $('f_grouping').value = i.grouping_circuits; $('f_length').value = i.length_m;
  if (p.device_class) $('f_device').value = p.device_class; if (p.trip_curve_type) $('f_curve').value = p.trip_curve_type;
  $('f_iscc').value = p.prospective_fault_current_a ?? ''; if (p.disconnection_time_s != null) $('f_tdisc').value = p.disconnection_time_s; $('f_vdlimit').value = p.max_voltage_drop_pct ?? '';
  const m = meta || {}; $('f_ref').value = curCircuit()?.ref || '';
  $('f_phase').value = m.phase || (l.phases === 3 ? 'L1L2L3' : 'L1');
  $('f_rcd').value = m.rcd?.present ? (m.rcd.type || 'RCBO') : ''; $('f_rcd_ma').value = m.rcd?.ma || 30;
  toggleCurve();
}

async function recompute() {
  let v;
  try { v = await postJSON('/api/viz' + packQuery(), buildRequest()); }
  catch (e) { toast('Ошибка расчёта: ' + e.message); return; }
  VIZ = v; renderAll();
}
function renderAll() {
  const r = VIZ.result; topBadge(r.overall_status);
  renderSummary(r); renderTCC(VIZ.tcc); renderSweep(VIZ.sweep); renderVD(VIZ.vd_profile); renderDerating(VIZ.derating); renderSLD(VIZ.sld); renderTrace(r);
  const packNote = VIZ.data_provenance_note ? ('⚠ ' + VIZ.data_provenance_note) : `Норм-пакет «${r.data_pack.name}» (${r.data_pack.status}).`;
  $('provenance').textContent = packNote + ' Не данные производителя оборудования.';
  relayoutActive();
}
function renderSummary(r) {
  const c = r.selected_cable, p = r.selected_protection;
  $('summary').innerHTML = [['Кабель', `${fmt(c.cross_section_mm2)} мм² ${c.material}/${c.insulation}`], ['IZ', `${fmt(c.Iz_a)} A`], ['Аппарат', `${p.device_class} ${fmt(p.In_a)} A`], ['IB', `${fmt(r.design_current_a)} A`], ['ΔU', `${fmt(r.voltage_drop_pct)} %`], ['Связывает', c.governing_constraint]].map(([k, v]) => `<div class="kv"><span>${k}:</span> <b>${v}</b></div>`).join('');
}
$('btnSaveCircuit').addEventListener('click', () => {
  const c = curCircuit(); if (!c) return;
  c.request = buildRequest(); c.meta = buildMeta(); c.ref = $('f_ref').value;
  if (VIZ) { const r = VIZ.result; c.result = { status: r.overall_status, section: r.selected_cable.cross_section_mm2, In: r.selected_protection.In_a, IB: r.design_current_a, Iz: r.selected_cable.Iz_a, vd: r.voltage_drop_pct, governing: r.selected_cable.governing_constraint }; }
  projSet(PROJ); toast('Цепь сохранена в щит'); go('/p/' + PROJ.id);
});
$('btnBackPanel').addEventListener('click', () => go('/p/' + PROJ.id));
$('btnPrev').addEventListener('click', () => navCircuit(-1));
$('btnNext').addEventListener('click', () => navCircuit(1));
function navCircuit(dir) {
  const arr = PROJ.circuits; const i = arr.findIndex(x => x.id === CID); const j = i + dir;
  if (j >= 0 && j < arr.length) go('/p/' + PROJ.id + '/c/' + arr[j].id);
}
$('btnSign').addEventListener('click', () => {
  const c = curCircuit(); if (!c) return;
  const name = prompt('ФИО инженера, подписывающего цепь:'); if (!name) return;
  const lic = prompt('Номер лицензии/квалификации (опционально):') || null;
  if (!confirm('Подтверждаю: значения норм-таблиц ИЛЛЮСТРАТИВНЫЕ (синтетические); я обязуюсь независимо проверить расчёт по реальному стандарту и каталогам производителей перед применением.')) return;
  c.signoff = { status: 'SIGNED', engineer_name: name, license_id: lic, signed_at: nowISO(), ack_illustrative: true };
  projSet(PROJ); setSignoffBadge(c.signoff); toast('Цепь подписана (рекомендательно, синтетические данные)');
});
$('recalc').addEventListener('click', recompute);
document.querySelectorAll('#f_desc,#f_ref,#f_power,#f_current,#f_voltage,#f_phases,#f_pf,#f_purpose,#f_method,#f_material,#f_insulation,#f_ambient,#f_grouping,#f_length,#f_device,#f_curve,#f_iscc,#f_tdisc,#f_vdlimit,#f_phase,#f_rcd,#f_rcd_ma').forEach(el => el.addEventListener('change', recompute));

// ---------- charts (Plotly/SVG) ----------
const DARK = { paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font: { color: '#b8c6de', size: 12, family: "'Fira Sans', -apple-system, sans-serif" }, margin: { l: 60, r: 18, t: 46, b: 70 }, legend: { orientation: 'h', y: -0.22, yanchor: 'top', x: 0 }, showlegend: true };
const CFG = { responsive: true, displayModeBar: false };
function vlines(shapes, x, color, dash, label, anns) { if (x == null) return; shapes.push({ type: 'line', x0: x, x1: x, yref: 'paper', y0: 0, y1: 1, line: { color, width: 1.5, dash } }); anns.push({ x: Math.log10(x), y: 1, yref: 'paper', text: label, showarrow: false, font: { color, size: 11 }, xanchor: 'left', yanchor: 'bottom' }); }
function renderTCC(t) {
  if (!t) return; const dmax = t.device.max, dmin = t.device.min;
  const deviceOff = t.device.available === false;
  const traces = [
    { x: t.cable.withstand.map(p => p[0]), y: t.cable.withstand.map(p => p[1]), name: `Кабель ${fmt(t.cable.section_mm2)} мм² (I²t)`, mode: 'lines', line: { color: '#f4574a', width: 2.5 } },
  ];
  if (!deviceOff) traces.push(
    { x: dmax.map(p => p[0]), y: dmax.map(p => p[1]), mode: 'lines', line: { color: '#4f9dff', width: 1 }, showlegend: false },
    { x: dmin.map(p => p[0]), y: dmin.map(p => p[1]), name: `${t.device.class}${t.device.class === 'gG_fuse' ? '' : ' ' + t.device.curve_type} (полоса)`, mode: 'lines', line: { color: '#4f9dff', width: 1 }, fill: 'tonexty', fillcolor: 'rgba(79,157,255,.16)' },
  );
  const shapes = [], anns = [];
  vlines(shapes, t.markers.IB, '#94a4c4', 'dot', 'IB', anns); vlines(shapes, t.markers.In, '#e6ad3c', 'dash', 'In', anns); vlines(shapes, t.markers.Iscc, '#f4574a', 'dot', 'Iscc', anns);
  if (deviceOff) anns.push({ xref: 'paper', yref: 'paper', x: 0.5, y: 0.5, text: 'кривая аппарата отсутствует в норм-пакете', showarrow: false, font: { color: '#94a4c4', size: 12 } });
  const coord = deviceOff ? '' : (t.coordinated === null ? '' : (t.coordinated ? '  ·  иллюстративная проверка: OK' : '  ·  не координируется (иллюстр.)'));
  Plotly.react('plot_tcc', traces, Object.assign({}, DARK, { title: { text: 'Время-токовая координация' + coord, font: { size: 14, color: t.coordinated === false && !deviceOff ? '#f4574a' : '#b8c6de' } }, xaxis: { type: 'log', title: 'Ток, A', gridcolor: '#1b2740' }, yaxis: { type: 'log', title: 'Время, с', gridcolor: '#1b2740' }, shapes, annotations: anns }), CFG);
}
function renderSweep(s) {
  if (!s) return; const x = s.rows.map(r => fmt(r.section_mm2)), y = s.rows.map(r => r.Iz_a);
  const colors = s.rows.map(r => r.section_mm2 === s.chosen_mm2 ? '#4f9dff' : (r.amp_ok && r.vd_ok && r.sc_ok ? '#2b6b45' : '#2a3550'));
  Plotly.react('plot_sweep', [{ x, y, type: 'bar', marker: { color: colors } }], Object.assign({}, DARK, { title: { text: `Свип сечения — выбрано ${fmt(s.chosen_mm2)} мм² (${s.governing})`, font: { size: 14 } }, xaxis: { title: 'Сечение, мм²', type: 'category' }, yaxis: { title: 'IZ, A', gridcolor: '#1b2740' }, showlegend: false, shapes: [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: s.required_iz_a, y1: s.required_iz_a, line: { color: '#e6ad3c', width: 1.5, dash: 'dash' } }], annotations: [{ xref: 'paper', x: 0.01, y: s.required_iz_a, text: `требуемый IZ ≥ ${fmt(s.required_iz_a)} A`, showarrow: false, font: { color: '#e6ad3c', size: 11 }, yanchor: 'bottom' }] }), CFG);
}
function renderVD(v) {
  if (!v) return;
  Plotly.react('plot_vd', [{ x: v.series.map(p => p[0]), y: v.series.map(p => p[1]), mode: 'lines', name: `ΔU при ${fmt(v.section_mm2)} мм²`, line: { color: '#4f9dff', width: 2.5 } }, { x: [v.current_length_m], y: [v.current_pct], mode: 'markers', name: 'текущая длина', marker: { color: '#eaf0fb', size: 9 } }], Object.assign({}, DARK, { title: { text: 'Профиль падения напряжения', font: { size: 14 } }, xaxis: { title: 'Длина, м', gridcolor: '#1b2740' }, yaxis: { title: 'ΔU, %', gridcolor: '#1b2740' }, shapes: [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: v.limit_pct, y1: v.limit_pct, line: { color: '#f4574a', width: 1.5, dash: 'dash' } }], annotations: [{ xref: 'paper', x: 0.01, y: v.limit_pct, text: `предел ${fmt(v.limit_pct)} %`, showarrow: false, font: { color: '#f4574a', size: 11 }, yanchor: 'bottom' }] }), CFG);
}
function renderDerating(d) {
  if (!d) return;
  Plotly.react('plot_derating', [{ x: d.stages.map(s => s.label), y: d.stages.map(s => s.value), type: 'bar', marker: { color: ['#4f9dff', '#3f74c9', '#2b6b45'] }, text: d.stages.map(s => fmt(s.value)), textposition: 'outside' }], Object.assign({}, DARK, { title: { text: `Дерейтинг: It → IZ (нужно ≥ ${fmt(d.required_iz_a)} A)`, font: { size: 14 } }, yaxis: { title: 'A', gridcolor: '#1b2740' }, showlegend: false, shapes: [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: d.required_iz_a, y1: d.required_iz_a, line: { color: '#e6ad3c', width: 1.5, dash: 'dash' } }] }), CFG);
}
function renderSLD(s) {
  if (!s) return; const col = { PASS: '#45c565', FAIL: '#f4574a', NEEDS_REVIEW: '#e6ad3c' }[s.status] || '#94a4c4';
  const bw = 150, gap = 46, y = 70, h = 78; let x = 20, svg = `<svg viewBox="0 0 ${20 + (bw + gap) * 4} 200" class="sld" style="max-width:100%">`;
  s.nodes.forEach((n, idx) => {
    if (idx > 0) svg += `<line x1="${x - gap}" y1="${y + h / 2}" x2="${x}" y2="${y + h / 2}" stroke="#4f9dff" stroke-width="2"/>`;
    svg += `<rect x="${x}" y="${y}" width="${bw}" height="${h}" rx="9" fill="#172234" stroke="${idx === s.nodes.length - 1 ? col : '#263650'}" stroke-width="2"/>`;
    svg += `<text x="${x + bw / 2}" y="${y + 30}" fill="#eaf0fb" font-size="14" font-weight="600" text-anchor="middle">${esc(n.label)}</text>`;
    svg += `<text x="${x + bw / 2}" y="${y + 52}" fill="#94a4c4" font-size="12" text-anchor="middle" font-family="monospace">${esc(n.sub)}</text>`;
    x += bw + gap;
  });
  svg += `<text x="20" y="30" fill="${col}" font-size="15" font-weight="700">${s.status} · связывает: ${s.governing}</text></svg>`;
  $('plot_sld').innerHTML = svg;
}
function renderTrace(r) {
  const dot = st => `<span class="st-dot st-${st}"></span>`;
  $('trace').innerHTML = r.audit_trace.map(s => {
    const cites = (s.citations || []).map(c => `${c.standard}${c.clause ? ' §' + c.clause : ''}${c.table ? ' Табл.' + c.table : ''}`).join('; ');
    return `<details class="step"><summary>${dot(s.status)}${esc(s.title)}</summary><div class="body">${s.formula ? `<div class="f">${esc(s.formula)}</div>` : ''}${s.computation ? `<div class="comp">${esc(s.computation)}</div>` : ''}${cites ? `<div class="cite">↳ ${esc(cites)}</div>` : ''}</div></details>`;
  }).join('');
}
function relayoutActive() { const el = document.querySelector('.tabs button.active'); if (!el) return; const id = el.dataset.tab; if (['tcc', 'sweep', 'vd', 'derating'].includes(id)) Plotly.Plots.resize($('plot_' + id)); }
$('tabs').addEventListener('click', e => {
  if (e.target.tagName !== 'BUTTON') return;
  document.querySelectorAll('.tabs button').forEach(b => b.classList.remove('active')); e.target.classList.add('active');
  const id = e.target.dataset.tab;
  document.querySelectorAll('.tabpane').forEach(p => p.style.display = 'none'); $('plot_' + id).style.display = 'block';
  if (['tcc', 'sweep', 'vd', 'derating'].includes(id)) Plotly.Plots.resize($('plot_' + id));
});

// ---------- copilot (editor) ----------
function addMsg(cls, html, sub) { const d = document.createElement('div'); d.className = 'msg ' + cls; d.innerHTML = html + (sub ? `<small>${esc(sub)}</small>` : ''); $('chat').appendChild(d); $('chat').scrollTop = $('chat').scrollHeight; }
async function chatSend() {
  const text = $('chatInput').value.trim(); if (!text) return; addMsg('user', esc(text)); $('chatInput').value = ''; addMsg('bot', '⏳ разбираю…'); const busy = $('chat').lastChild;
  try { const j = await postJSON('/api/intake', { text }); busy.remove(); if (!j.ok) { addMsg('bot', '⚠ ' + esc(j.message || 'не удалось')); return; } fillForm(j.request, buildMeta()); await recompute(); const c = VIZ.result.selected_cable, p = VIZ.result.selected_protection; addMsg('bot', `Разобрал: <b>${fmt(c.cross_section_mm2)} мм²</b>, ${p.device_class} <b>${fmt(p.In_a)} A</b>, статус <b>${VIZ.result.overall_status}</b>. Нажми «Сохранить в щит».`, 'модель: ' + (j.model || '')); }
  catch (e) { busy.remove(); addMsg('bot', '⚠ ' + esc(e.message)); }
}
async function doExplain() {
  addMsg('bot', '⏳ объясняю…'); const busy = $('chat').lastChild;
  try { const j = await postJSON('/api/explain' + packQuery(), buildRequest()); busy.remove(); const n = j.narrative; const tag = n.model ? `${n.model}; провенанс ${n.provenance_ok ? 'OK' : 'FAIL ' + JSON.stringify(n.unverified_numbers)}` : 'шаблон (без ключа)'; addMsg('bot', esc(n.text), tag); }
  catch (e) { busy.remove(); addMsg('bot', '⚠ ' + esc(e.message)); }
}
async function doReview() {
  addMsg('bot', '⏳ ревьюер проверяет…'); const busy = $('chat').lastChild;
  try { const j = await postJSON('/api/verify' + packQuery(), buildRequest()); busy.remove(); const v = j.verdict; const bad = !v.agrees || !v.deterministic_ok; const issues = (v.issues || []).length ? '<br>' + v.issues.map(esc).join('<br>') : ''; addMsg('rev' + (bad ? ' bad' : ''), `Ревьюер: детерм. <b style="color:var(--${v.deterministic_ok ? 'ok' : 'bad'})">${v.deterministic_ok ? 'OK' : 'FAIL'}</b>, LLM ${v.agrees ? 'согласен' : 'НЕ согласен'}${issues}`, v.model ? 'модель: ' + v.model : 'детерминированно'); }
  catch (e) { busy.remove(); addMsg('bot', '⚠ ' + esc(e.message)); }
}
$('chatSend').addEventListener('click', chatSend);
$('chatInput').addEventListener('keydown', e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) chatSend(); });
$('btnExplain').addEventListener('click', doExplain); $('btnReview').addEventListener('click', doReview);

// ---------- toggles ----------
function toggleSrc() { const pw = $('f_srcType').value === 'power'; $('wrap_power').style.display = pw ? '' : 'none'; $('wrap_current').style.display = pw ? 'none' : ''; }
function toggleCurve() { $('wrap_curve').style.display = $('f_device').value === 'gG_fuse' ? 'none' : ''; }
$('f_srcType').addEventListener('change', toggleSrc); $('f_device').addEventListener('change', toggleCurve);

// ---------- export / import / report ----------
function download(name, data, type) { const b = data instanceof Blob ? data : new Blob([data], { type }); const a = document.createElement('a'); a.href = URL.createObjectURL(b); a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000); }
function exportProject(p) { if (!p) return; download(`${(p.board_ref || p.name).replace(/\s+/g, '_')}.ecproj.json`, JSON.stringify(p, null, 2), 'application/json'); }
function importProject(e) {
  const f = e.target.files[0]; if (!f) return; const rd = new FileReader();
  rd.onload = () => { try { const p = JSON.parse(rd.result); const copy = deepCopyProject(p, p.name); projSet(copy); e.target.value = ''; go('/p/' + copy.id); toast('Проект импортирован'); } catch (err) { toast('⚠ не удалось прочитать файл: ' + err.message); } };
  rd.readAsText(f);
}
async function downloadReport() {
  toast('Готовлю отчёт по щиту…');
  try { const rep = await postJSON('/api/project-report', { project: PROJ }); download(`${(PROJ.board_ref || PROJ.name).replace(/\s+/g, '_')}_отчёт.md`, rep.markdown, 'text/markdown'); }
  catch (e) { toast('⚠ ' + e.message); }
}

// ---------- net ----------
async function postJSON(url, body) { const r = await fetch(API + url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }

// ---------- boot ----------
window.addEventListener('hashchange', route);
async function boot() {
  toggleSrc(); toggleCurve(); wsGet(); migrateLegacy();
  try { HEALTH = await (await fetch(API + '/api/health')).json(); $('modeTag').textContent = `${HEALTH.mode} · ${HEALTH.model_fast || ''}`; } catch { $('modeTag').textContent = 'offline'; }
  try { PACKS = await (await fetch(API + '/api/packs')).json(); } catch { PACKS = []; }
  if (HEALTH.mode === 'fallback') addMsg('bot', 'Ключ Gemini на сервере не задан — копилот в режиме фолбэка (NL-разбор недоступен, объяснение — шаблон, ревьюер — детерминированный). Расчёт и графики работают полностью.');
  else addMsg('bot', 'Опиши цепь словами или задай параметры — соберу расчёт с трассой до норм, интерактивные графики и проверку ревьюером.');
  route();
}
function migrateLegacy() {
  const legacy = jget('ec_circuits', null);
  if (legacy && legacy.length && !idxGet().length) {
    const p = blankProject('Импортированный щит'); p.circuits = legacy.map((c, i) => { const ck = newCircuit(c.req || blankRequest(), null, 'C' + (i + 1)); ck.sort_index = i; return ck; }); projSet(p);
    localStorage.removeItem('ec_circuits');
  }
}
boot();
