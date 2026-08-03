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

// ---------- display dictionary (docs/17): Russian labels for the electrician, technical code kept in title ----------
// Display layer only — enum values in API/localStorage/exports/`value=` are never changed.
const ST_LABELS = { PASS: 'Соответствует', FAIL: 'Не проходит', NEEDS_REVIEW: 'Требует проверки', UNSIGNED_ADVISORY: 'Не подписано · рекомендательно', SIGNED: 'Подписано', VERIFIED: 'Проверено', 'READ-ONLY': 'Только просмотр' };
const GOV_LABELS = { overload_coordination: 'координация по перегрузке', short_circuit: 'термическая стойкость к КЗ', voltage_drop: 'падение напряжения' };
const PACK_LABELS = { 'iec-stub': 'IEC 60364 (демо-данные)', 'pue-rk': 'ПУЭ РК (adilet)' };
const SEV_LABELS = { error: 'ошибка', warning: 'предупреждение', info: 'инфо' };            // normcheck severity
const DIFF_LABELS = { match: 'совпадает', violation: 'нарушение', not_checked: 'не проверено' }; // import-diff status
const SECTION_LABELS = { standard_ratings: 'номинальные ряды аппаратов', standard_sections: 'стандартные сечения', device_parameters: 'параметры аппаратов', overload_rule: 'правило перегрузки', ampacity: 'пропускная способность', ambient_correction: 'поправка на температуру', grouping_correction: 'поправка на группировку', adiabatic_k: 'коэффициент адиабаты k', resistivity: 'удельные сопротивления', reactance: 'реактансы', voltage_drop_limit: 'предел ΔU' };
// Origin/data-status codes as they appear inline in server prose (provenance/disclaimer text) — not
// the same dict as packStatusBadge's own `origin` translation (that one renders a standalone badge
// and must not be touched here). PASS/FAIL stay untranslated — engineering notation, not prose.
const ORIGIN_STATUS_LABELS = { illustrative: 'синтетические (демо)', public_standard: 'публичный стандарт', licensed: 'лицензионные', NEEDS_REVIEW: 'требует проверки', VERIFIED: 'проверено' };
const stLabel = (s) => ST_LABELS[s] ?? (s ?? '');            // unknown code shown as-is
const govLabel = (g) => GOV_LABELS[g] ?? (g ?? '');           // unknown code shown as-is
const packLabel = (p) => PACK_LABELS[p] ?? (p ?? '');
const sevLabel = (s) => SEV_LABELS[s] ?? (s ?? '');
const diffLabel = (s) => DIFF_LABELS[s] ?? (s ?? '');
// Schedule/SLD device strings arrive from the engine (e.g. "MCB 16A C", "gG_fuse 20A"): MCB/MCCB is
// engineering notation and stays; only the snake_case gG_fuse token is humanized (docs/17 §4).
const deviceText = (s) => String(s ?? '').replace(/gG_fuse/g, 'Предохранитель gG');
// Provenance/disclaimer strings are server-generated Russian prose that lists untrusted sections and
// origin/status codes by their raw code — translate just those tokens for display (server text is
// not otherwise rewritten).
const humanizeSections = (t) => String(t ?? '').replace(/\b(standard_ratings|standard_sections|device_parameters|overload_rule|ampacity|ambient_correction|grouping_correction|adiabatic_k|voltage_drop_limit|resistivity|reactance|illustrative|public_standard|licensed|NEEDS_REVIEW|VERIFIED)\b/g, (m) => SECTION_LABELS[m] || ORIGIN_STATUS_LABELS[m] || m);

// ---------- storage ----------
const K_WS = 'ec_v2_workspace', K_IDX = 'ec_v2_projects', KP = id => 'ec_v2_project_' + id;
function jget(k, def) { try { return JSON.parse(localStorage.getItem(k)) ?? def; } catch { return def; } }
function jset(k, v) { localStorage.setItem(k, JSON.stringify(v)); }
function wsGet() { let w = jget(K_WS, null); if (!w) { w = { schema_version: 2, id: uid('ws'), created_at: nowISO() }; jset(K_WS, w); } return w; }
function idxGet() { return jget(K_IDX, []); }
function idxSet(a) { jset(K_IDX, a); }
function projGet(id) { return jget(KP(id), null); }
function setSave(state) { const el = $('saveState'); el.className = 'save ' + state; el.textContent = { saving: 'сохранение…', saved: 'сохранено', unsaved: 'не сохранено' }[state] || ''; }
function projSet(p, options = {}) {
  setSave('saving');
  if (options.touch !== false) p.updated_at = nowISO();
  p.rollup = rollup(p);
  jset(KP(p.id), p);
  const idx = idxGet().filter(x => x.id !== p.id);
  idx.unshift({ id: p.id, name: p.name, board_ref: p.board_ref, updated_at: p.updated_at, rollup: p.rollup, count: (p.circuits || []).length });
  idxSet(idx);
  setSave('saved');
  if (options.sync !== false) scheduleProjectSync(p.id);
}
function projDel(id) {
  localStorage.removeItem(KP(id));
  idxSet(idxGet().filter(x => x.id !== id));
  if (SYNC_AVAILABLE) deleteRemoteProject(id);
}
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
let PROJ = null, CID = null, VIZ = null, NORMCHECK = null, HEALTH = { mode: 'fallback' }, PACKS = [];
let IMPORT_FILE = null, IMPORT_RESULT = null;
let BOARD_COPILOT_PROJECT_ID = null, BOARD_COPILOT_HISTORY = [], BOARD_PROPOSAL = null, BOARD_PROPOSAL_BASE = null, COPILOT_UNDO = null;
let SYNC_AVAILABLE = false;
const PROJECT_SYNC_TIMERS = new Map();
function packQuery() { return PROJ?.norm_pack ? ('?pack=' + encodeURIComponent(PROJ.norm_pack)) : ''; }
function packStatusBadge(el, pack) {
  const status = pack?.status || '';
  const origin = { illustrative: 'синтетические', public_standard: 'публичный стандарт', licensed: 'лицензия' }[status] || status;
  const verification = pack?.verification_status || 'NEEDS_REVIEW';
  el.textContent = [origin, stLabel(verification)].filter(Boolean).join(' · ');
  el.title = verification;
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
  const ms = h.match(/^\/s\/([^/]+)$/);
  const mp = h.match(/^\/p\/([^/]+)\/print$/);
  const m = h.match(/^\/p\/([^/]+)(?:\/c\/([^/]+))?/);
  ['dashboard', 'project', 'editor', 'print', 'shared', 'norms'].forEach(s => $('screen-' + s).hidden = true);
  const mn = h.match(/^\/norms(?:\/([^/]+))?(?:\/([^/]+))?$/);
  $('advisory').hidden = mp ? true : false;
  $('homeLink').classList.toggle('active', !mn);
  $('btnNorms').classList.toggle('active', Boolean(mn));
  if (mn) {
    show('norms');
    crumbs(['Щиты', '#/']);
    topBadge(''); setSave(''); $('saveState').textContent = '';
    loadNormDocs().then(() => {
      if (mn[1]) return openNormDoc(mn[1], mn[2] ? { anchor: mn[2] } : {});
      NORMS.docId = null;
      $('normToc').hidden = true; $('normReader').hidden = true;
      $('normEmpty').hidden = $('normSearchInput').value.trim() ? true : false;
    });
    return;
  }
  if (ms) {
    PROJ = null;
    show('shared');
    crumbs(['Щиты', '#/']);
    topBadge('');
    setSave(''); $('saveState').textContent = 'только чтение';
    renderShared(ms[1]);
    return;
  }
  setSave('saved');
  if (mp) {
    PROJ = projGet(mp[1]);
    if (!PROJ) { go('/'); return; }
    show('print'); renderPrint(); crumbs([PROJ.name, '#/p/' + PROJ.id]); topBadge('');
    return;
  }
  if (!m) { renderDashboard(); show('dashboard'); crumbs(null); topBadge(''); return; }
  const pid = m[1], cid = m[2];
  PROJ = projGet(pid);
  if (!PROJ) { go('/'); return; }
  if (cid) { openEditor(cid); show('editor'); }
  else { renderProject(); show('project'); }
}
function show(s) { $('screen-' + s).hidden = false; }
// design-v2-spec §2.2: #crumbs renders ONE "← back-context" element, not a breadcrumb trail — the
// current screen's own name is already in its h1/h2, so it's never repeated here. `back` is
// [label, hash] for the parent screen, or null on the dashboard (nothing to go back to).
function crumbs(back) {
  $('crumbs').innerHTML = back ? `<a data-h="${esc(back[1])}">← ${esc(back[0])}</a>` : '';
}
$('crumbs').addEventListener('click', e => { const h = e.target.dataset.h; if (h) go(h.replace(/^#/, '')); });
$('homeLink').addEventListener('click', () => go('/'));
function topBadge(s) { const b = $('statusBadge'); b.textContent = s ? stLabel(s) : '—'; b.title = s || ''; b.className = 'badge ' + ({ PASS: 'pass', FAIL: 'fail', NEEDS_REVIEW: 'review' }[s] || ''); }

// ---------- Russian count agreement (цепь/цепи/цепей, требует/требуют) ----------
function pluralRu(n, one, few, many) {
  const n100 = Math.abs(n) % 100, n10 = n100 % 10;
  if (n100 > 10 && n100 < 20) return many;
  if (n10 === 1) return one;
  if (n10 > 1 && n10 < 5) return few;
  return many;
}
function createNewProject() { const p = blankProject(); projSet(p); go('/p/' + p.id); }

// ========================= DASHBOARD =========================
function renderDashboard(filter) {
  let idx = idxGet().sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
  if (filter) idx = idx.filter(p => (p.name + ' ' + (p.board_ref || '')).toLowerCase().includes(filter.toLowerCase()));
  const grid = $('projectGrid');
  if (!idx.length) {
    grid.innerHTML = `<div class="empty">Пока нет ни одного щита.<br>Начни — так проще увидеть, как это работает.
      <br><br><button id="btnEmptyNew" class="primary cta">+ Новый щит</button></div>`;
    return;
  }
  // design-v2-spec §2.5: one worded status line per card ("4 цепи · 4 требуют проверки"), not 4 colored chips —
  // color lives only in the dot, tinted to the worst status present.
  grid.innerHTML = idx.map(p => {
    const r = p.rollup?.counts || { PASS: 0, FAIL: 0, NEEDS_REVIEW: 0 };
    const count = p.count || 0;
    const issues = (r.FAIL || 0) + (r.NEEDS_REVIEW || 0);
    const worst = r.FAIL ? 'FAIL' : (r.NEEDS_REVIEW ? 'NEEDS_REVIEW' : (count ? 'PASS' : ''));
    const circuitsWord = pluralRu(count, 'цепь', 'цепи', 'цепей');
    const statusLine = !count ? 'нет цепей'
      : issues ? `${count} ${circuitsWord} · ${issues} ${pluralRu(issues, 'требует', 'требуют', 'требуют')} проверки`
      : `${count} ${circuitsWord} · все соответствуют`;
    return `<div class="card" data-open="${p.id}">
      <div class="card-top">
        <h3>${esc(p.name)} <span class="cref">${esc(p.board_ref || '')}</span></h3>
        <div class="kebab-wrap">
          <button class="kebab" data-kebab="${p.id}" type="button" aria-haspopup="true" aria-expanded="false" title="Ещё действия">⋯</button>
          <div class="kebab-menu" hidden>
            <button data-rename="${p.id}">Переименовать</button>
            <button data-dup="${p.id}">Дублировать</button>
            <button data-export="${p.id}" title="Сохранить файл проекта (.ecproj.json)">Файл проекта</button>
            <button data-del="${p.id}">Удалить</button>
          </div>
        </div>
      </div>
      <div class="card-status ${worst}"><span class="dot"></span>${esc(statusLine)}</div>
      <div class="cmeta"><span>изменён ${when(p.updated_at)}</span></div></div>`;
  }).join('');
}
function when(iso) { if (!iso) return '—'; const s = (Date.now() - new Date(iso)) / 1000; if (s < 60) return 'только что'; if (s < 3600) return Math.floor(s / 60) + ' мин назад'; if (s < 86400) return Math.floor(s / 3600) + ' ч назад'; return new Date(iso).toLocaleDateString('ru'); }

function closeCardMenus() {
  document.querySelectorAll('#projectGrid .kebab-menu').forEach(m => { m.hidden = true; });
  document.querySelectorAll('#projectGrid [data-kebab]').forEach(b => b.setAttribute('aria-expanded', 'false'));
}
$('projectGrid').addEventListener('click', e => {
  if (e.target.id === 'btnEmptyNew') { createNewProject(); return; }
  const kebab = e.target.closest('[data-kebab]');
  if (kebab) {
    const menu = kebab.nextElementSibling, willOpen = menu.hidden;
    closeCardMenus();
    if (willOpen) { menu.hidden = false; kebab.setAttribute('aria-expanded', 'true'); }
    return;
  }
  const t = e.target, d = t.dataset;
  if (d.del) { const id = d.del; const p = projGet(id); projDel(id); renderDashboard($('dashSearch').value); toast(`Щит «${p?.name || ''}» удалён`, 'Отменить', () => { projSet(p); renderDashboard(); }); return; }
  if (d.rename) { const p = projGet(d.rename); const n = prompt('Имя щита:', p.name); if (n) { p.name = n; projSet(p); renderDashboard($('dashSearch').value); } return; }
  if (d.dup) { const p = projGet(d.dup); const copy = deepCopyProject(p, p.name + ' (копия)'); projSet(copy); renderDashboard(); return; }
  if (d.export) { exportProject(projGet(d.export)); return; }
  if (d.open) { go('/p/' + d.open); }
});
document.addEventListener('click', e => { if (!e.target.closest('#projectGrid')) closeCardMenus(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeCardMenus(); });
$('btnNewProject').addEventListener('click', createNewProject);
$('btnSample').addEventListener('click', () => { const p = sampleProject(); projSet(p); go('/p/' + p.id); });
$('dashSearch').addEventListener('input', e => renderDashboard(e.target.value));
$('importFile').addEventListener('change', importProject);
$('scheduleImportFile').addEventListener('change', importScheduleFile);
$('btnImportCancel').addEventListener('click', closeScheduleImport);
$('btnImportRemap').addEventListener('click', () => submitScheduleImport(false));
$('btnImportCreate').addEventListener('click', createImportedProject);
$('importConfirm').addEventListener('change', e => { $('btnImportCreate').disabled = !e.target.checked; });

function deepCopyProject(p, name) {
  const c = JSON.parse(JSON.stringify(p));
  c.id = uid('prj'); c.name = name || c.name; c.created_at = c.updated_at = nowISO();
  c.circuits.forEach(ck => { ck.id = uid('ckt'); });
  return c;
}

function closeScheduleImport() {
  IMPORT_FILE = null; IMPORT_RESULT = null;
  $('scheduleImportFile').value = '';
  $('importReview').hidden = true;
}

async function importScheduleFile(e) {
  const file = e.target.files[0]; if (!file) return;
  IMPORT_FILE = file; IMPORT_RESULT = null;
  $('importReview').hidden = false;
  $('importSource').textContent = `${file.name} · ${(file.size / 1024).toFixed(1)} КиБ · разбираю…`;
  $('importMappingBody').innerHTML = '';
  $('importPreview').innerHTML = '';
  $('importDiff').innerHTML = '';
  await submitScheduleImport(false, null);
}

function currentImportMapping() {
  if (!IMPORT_RESULT) return null;
  const result = {};
  document.querySelectorAll('#importMappingBody select').forEach((select, index) => {
    result[IMPORT_RESULT.mapping[index].source_header] = select.value || null;
  });
  return result;
}

async function submitScheduleImport(confirmed, mapping = undefined) {
  if (!IMPORT_FILE) return null;
  const data = new FormData();
  data.append('file', IMPORT_FILE);
  const chosen = mapping === undefined ? currentImportMapping() : mapping;
  if (chosen) data.append('mapping', JSON.stringify(chosen));
  data.append('confirmed', confirmed ? 'true' : 'false');
  const button = confirmed ? $('btnImportCreate') : $('btnImportRemap');
  button.disabled = true;
  try {
    const response = await fetch(API + '/api/import-schedule', { method: 'POST', body: data });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      throw new Error(payload?.detail?.message || payload?.detail || `HTTP ${response.status}`);
    }
    IMPORT_RESULT = await response.json();
    renderScheduleImport();
    return IMPORT_RESULT;
  } catch (error) {
    $('importIssues').innerHTML = `<span class="import-issue">${esc(error.message)}</span>`;
    toast('импорт не выполнен: ' + error.message);
    return null;
  } finally {
    button.disabled = false;
    $('btnImportCreate').disabled = !$('importConfirm').checked;
  }
}

function renderScheduleImport() {
  const result = IMPORT_RESULT; if (!result) return;
  $('importReview').hidden = false;
  $('importSource').textContent = `${IMPORT_FILE.name} · ${(IMPORT_FILE.size / 1024).toFixed(1)} КиБ · ${result.project_draft.circuits.length} цепей`;
  const options = ['<option value="">— не использовать —</option>'].concat(
    result.canonical_fields.map(field => `<option value="${esc(field)}">${esc(field)}</option>`),
  ).join('');
  $('importMappingBody').innerHTML = result.mapping.map((entry, index) => `<tr>
    <td>${esc(entry.source_header)}</td>
    <td><select data-map-index="${index}">${options}</select></td>
    <td class="mono dim">${esc(entry.method)}</td>
  </tr>`).join('');
  result.mapping.forEach((entry, index) => {
    document.querySelector(`#importMappingBody select[data-map-index="${index}"]`).value = entry.field || '';
  });

  const shownIssues = result.issues.slice(0, 24);
  $('importIssues').innerHTML = shownIssues.map(issue => `<span class="import-issue">${issue.row ? `строка ${issue.row}: ` : ''}${esc(issue.message)}</span>`).join('')
    + (result.issues.length > shownIssues.length ? `<span class="import-issue">ещё ${result.issues.length - shownIssues.length}</span>` : '');
  const previewHead = result.headers.map(header => `<th>${esc(header)}</th>`).join('');
  const previewRows = result.preview.map(row => `<tr>${result.headers.map(header => `<td>${esc(row[header] ?? '')}</td>`).join('')}</tr>`).join('');
  $('importPreview').innerHTML = `<table><thead><tr>${previewHead}</tr></thead><tbody>${previewRows}</tbody></table>`;

  $('importDiff').innerHTML = result.diff.rows.map(row => {
    const checks = row.checks.map(check => `${esc(check.field)}: ${fmt(check.observed)} → ${fmt(check.required)} (${esc(diffLabel(check.status))}${check.reason ? `, ${esc(check.reason)}` : ''})`).join('<br>');
    return `<div class="import-diff-row ${row.status}"><b>${esc(row.circuit_ref)}</b><span class="mono">${checks}</span><span class="chip ${row.status === 'match' ? 'pass' : (row.status === 'violation' ? 'fail' : 'review')}" title="${esc(row.status)}">${esc(diffLabel(row.status))}</span></div>`;
  }).join('');
  $('importIdentity').textContent = humanizeSections(`${result.diff.data_identity} ${result.diff.signoff_notice} ${result.diff.disclaimer}`);
  $('importConfirm').checked = Boolean(result.assumptions_confirmed);
  $('btnImportCreate').disabled = !$('importConfirm').checked;
}

async function createImportedProject() {
  if (!$('importConfirm').checked) return;
  const result = await submitScheduleImport(true);
  if (!result || !result.assumptions_confirmed) return;
  try {
    const validated = await postJSON('/api/project-validate', { project: result.project_draft });
    projSet(validated.project);
    const projectId = validated.project.id;
    closeScheduleImport();
    go('/p/' + projectId);
    toast('Щит создан после проверки сопоставления и допущений');
  } catch (error) {
    toast('проект не прошёл контрольный пересчёт: ' + error.message);
  }
}

// ========================= PROJECT / BOARD =========================
async function renderProject() {
  const p = PROJ;
  if (BOARD_COPILOT_PROJECT_ID !== p.id) {
    BOARD_COPILOT_PROJECT_ID = p.id; BOARD_COPILOT_HISTORY = []; COPILOT_UNDO = null;
    renderBoardProposal(null); $('boardProposalUndo').hidden = true;
    $('boardCopilotMessages').innerHTML = '<div class="msg bot">Опиши изменение или спроси о щите. Сервер ничего не применит без подтверждения.</div>';
  }
  crumbs(['Щиты', '#/']);
  $('b_name').value = p.name; $('b_ref').value = p.board_ref || ''; $('b_location').value = p.location || '';
  $('b_supply').textContent = `${p.supply.voltage_v} В · ${p.supply.phases}ф · ${p.supply.earthing} · мест ${p.supply.ways_total}`;
  $('projDisclaimer').textContent = 'Рекомендательный расчёт; требуется подпись инженера по каждой цепи и по щиту.';
  renderPackSelect();
  renderBoardMetaLine();
  resetNormcheckPanel();
  const body = $('scheduleBody');
  if (!p.circuits.length) { body.innerHTML = `<tr><td colspan="14" class="empty" style="border:none">Пусто — добавь цепь или опиши словами ниже.</td></tr>`; $('boardTotals').innerHTML = ''; topBadge(''); $('boardRollup').textContent = '—'; $('boardRollup').className = 'badge'; return; }
  body.innerHTML = `<tr><td colspan="14" style="color:var(--dim)">пересчёт цепей движком…</td></tr>`;
  let rep;
  // ?sld=1 → the preview SVG comes back with the report (one server call / one engine pass).
  try { rep = await postJSON('/api/project-report?sld=1', { project: p }); }
  catch (e) { body.innerHTML = `<tr><td colspan="14" style="color:var(--bad)">Ошибка пересчёта: ${esc(e.message)}</td></tr>`; return; }
  // sync snapshots back for dashboard rollup
  rep.rows.forEach(r => { const c = p.circuits.find(x => x.id === r.id); if (c) c.result = { status: r.status, section: null, In: null, IB: r.IB_a, Iz: r.Iz_a, vd: r.dU_pct, governing: r.governing }; });
  projSet(p, { touch: false, sync: false });
  body.innerHTML = rep.rows.map(r => rowHTML(r)).join('');
  const b = rep.board;
  topBadge(b.status);
  $('boardRollup').textContent = `щит: ${stLabel(b.status)}`; $('boardRollup').title = b.status; $('boardRollup').className = 'badge ' + ({ PASS: 'pass', FAIL: 'fail', NEEDS_REVIEW: 'review' }[b.status] || '');
  renderTotals(b);
  renderSldInto(rep.sld);
}
function renderPackSelect() {
  const sel = $('b_pack');
  const cur = PROJ.norm_pack || PACKS[0]?.name || '';
  sel.innerHTML = PACKS.map(pk => `<option value="${esc(pk.name)}" title="${esc(pk.name)}">${esc(packLabel(pk.name))} (${esc(pk.version)})</option>`).join('');
  sel.value = cur;
  packStatusBadge($('b_packStatus'), PACKS.find(pk => pk.name === cur));
}
$('b_pack').addEventListener('change', () => { PROJ.norm_pack = $('b_pack').value; projSet(PROJ); renderProject(); });
// design-v2-spec §2.4: one quiet metadata line built from the same b_ref/b_location/b_pack fields that
// live inside the popover — reads the live input values, not just PROJ, so it updates as you type.
function renderBoardMetaLine() {
  const p = PROJ;
  const ref = $('b_ref').value.trim() || '—';
  const location = $('b_location').value.trim() || '—';
  const packName = $('b_pack').value || p.norm_pack || PACKS[0]?.name || '';
  const parts = [ref, location, `${p.supply.voltage_v} В`, `${p.supply.phases}ф`, p.supply.earthing, `${p.supply.ways_total} мест`, packLabel(packName)];
  $('boardMetaLine').textContent = parts.filter(Boolean).join(' · ');
}
function setBoardMetaOpen(open) {
  $('boardMetaPopover').hidden = !open;
  $('btnBoardMeta').setAttribute('aria-expanded', open ? 'true' : 'false');
}
$('btnBoardMeta').addEventListener('click', e => { e.stopPropagation(); setBoardMetaOpen($('boardMetaPopover').hidden); });
$('btnBoardMetaClose').addEventListener('click', () => setBoardMetaOpen(false));
document.addEventListener('click', e => {
  if ($('boardMetaPopover').hidden) return;
  if (e.target.closest('#boardMetaPopover') || e.target.closest('#btnBoardMeta')) return;
  setBoardMetaOpen(false);
});
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('boardMetaPopover').hidden) { setBoardMetaOpen(false); $('btnBoardMeta').focus(); } });
function rowHTML(r, readOnly = false) {
  const sign = r.signoff === 'SIGNED' ? '<svg class="icon icon-sm" viewBox="0 0 24 24" aria-label="подписано" title="подписано"><path d="M20 6 9 17l-5-5"/></svg>' : '';
  return `<tr data-cid="${r.id}">
    <td class="mono">${esc(r.ref)}</td><td>${esc(r.description)}</td><td class="num">${fmt(r.kw)}</td><td class="num">${fmt(r.pf)}</td>
    <td class="mono">${esc(r.phase)}</td><td class="num">${fmt(r.IB_a, 1)}</td><td title="${esc(r.device)}">${esc(deviceText(r.device))}</td><td>${esc(r.rcd)}</td>
    <td>${esc(r.cable)}</td><td class="num">${fmt(r.length_m)}</td><td class="num">${fmt(r.Iz_a, 1)}</td><td class="num">${fmt(r.dU_pct)}</td>
    <td class="st-cell ${r.status}" title="${esc(r.status)}">${esc(stLabel(r.status))}${sign}</td>${readOnly ? '' : `
    <td><div class="rowact">
      <button data-edit="${r.id}" title="Править" aria-label="Править цепь"><svg class="icon icon-sm" viewBox="0 0 24 24" style="pointer-events:none"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg></button>
      <button data-dupc="${r.id}" title="Дублировать" aria-label="Дублировать цепь"><svg class="icon icon-sm" viewBox="0 0 24 24" style="pointer-events:none"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg></button>
      <button data-delc="${r.id}" title="Удалить" aria-label="Удалить цепь"><svg class="icon icon-sm" viewBox="0 0 24 24" style="pointer-events:none"><path d="M3 6h18"/><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/><path d="M6 6v14a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V6"/></svg></button>
    </div></td>`}</tr>`;
}
function renderTotals(b, targetId = 'boardTotals') {
  const t = b.totals, d = b.demand, w = b.ways, ph = t.phase;
  const phaseKeys = b.topology?.phases === 1 ? ['L1'] : ['L1', 'L2', 'L3'];
  const mx = Math.max(...phaseKeys.map(k => ph[k].A), 1);
  const imbalance = t.imbalance_applicable
    ? `перекос ${fmt(t.imbalance_pct, 0)}%${t.imbalance_flag ? ' — выше нормы' : ''}`
    : 'однофазный щит · перекос неприменим (R05)';
  const phaseTitle = b.topology?.phases === 1 ? 'Фазный ток (реальный)' : 'Баланс фаз (реальный)';
  $(targetId).innerHTML = `
    <div class="totbox"><h4>Подключённая нагрузка</h4><div class="big">${fmt(t.connected_kw)} кВт</div><div style="color:var(--dim)">${fmt(t.connected_kva)} кВА</div></div>
    <div class="totbox"><h4>${phaseTitle}</h4>
      <div class="phbar">${phaseKeys.map(k => `<div class="b" style="height:${Math.round(ph[k].A / mx * 100)}%"><span>${k}<br>${fmt(ph[k].A, 0)}A</span></div>`).join('')}</div>
      <div style="margin-top:20px" class="${t.imbalance_flag ? 'warn' : ''}">${imbalance}</div></div>
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
// crumb text no longer echoes the board name (design-v2-spec §2.2: it's a static "← Щиты" on this screen)
const saveBoardMeta = debounce(() => { PROJ.name = $('b_name').value; PROJ.board_ref = $('b_ref').value; PROJ.location = $('b_location').value; projSet(PROJ); }, 500);
['b_name', 'b_ref', 'b_location'].forEach(id => $(id).addEventListener('input', () => { setSave('unsaved'); saveBoardMeta(); renderBoardMetaLine(); }));
$('btnBackDash').addEventListener('click', () => go('/'));
$('btnAddCircuit').addEventListener('click', () => { const c = newCircuit(); c.sort_index = PROJ.circuits.length; c.ref = 'C' + (PROJ.circuits.length + 1); PROJ.circuits.push(c); projSet(PROJ); go('/p/' + PROJ.id + '/c/' + c.id); });
$('btnNlAdd').addEventListener('click', nlAddCircuit);
$('nlQuick').addEventListener('keydown', e => { if (e.key === 'Enter') nlAddCircuit(); });
$('btnExportProj').addEventListener('click', () => exportProject(PROJ));
$('btnShare').addEventListener('click', createShareLink);
$('btnReport').addEventListener('click', downloadReport);
$('btnNormcheck').addEventListener('click', loadNormcheck);
$('btnBundle').addEventListener('click', downloadBundle);
$('btnPrint').addEventListener('click', () => { if (PROJ) go('/p/' + PROJ.id + '/print'); });
$('btnSldRefresh').addEventListener('click', loadSldPreview);

// ---------- generic dropdown menu helper (design C: board "Ещё" menu) ----------
function initDropdown(toggle, menu) {
  const setOpen = open => { menu.hidden = !open; toggle.setAttribute('aria-expanded', open ? 'true' : 'false'); };
  toggle.addEventListener('click', e => { e.stopPropagation(); setOpen(menu.hidden); });
  menu.addEventListener('click', e => { if (e.target.closest('button,label')) setOpen(false); });
  document.addEventListener('click', e => { if (!menu.hidden && !menu.contains(e.target) && e.target !== toggle) setOpen(false); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !menu.hidden) { setOpen(false); toggle.focus(); } });
}
initDropdown($('btnMore'), $('boardMoreMenu'));
initDropdown($('btnDashMore'), $('dashMoreMenu'));

// ---------- board-level Copilot: proposal only (docs/15-copilot-tools) ----------
function boardCopilotMessage(cls, text, sub = '') {
  const root = $('boardCopilotMessages');
  const message = document.createElement('div'); message.className = 'msg ' + cls;
  message.innerHTML = `${esc(text)}${sub ? `<small>${esc(sub)}</small>` : ''}`;
  root.appendChild(message); root.scrollTop = root.scrollHeight;
}
function metricChange(label, before, after) {
  const show = value => typeof value === 'number' ? fmt(value) : (value ?? '—');
  return `<span><b>${esc(label)}:</b> ${esc(show(before))} → ${esc(show(after))}</span>`;
}
function renderBoardProposal(proposal, baseVersion = null) {
  BOARD_PROPOSAL = proposal;
  BOARD_PROPOSAL_BASE = proposal ? baseVersion : null;
  const panel = $('boardProposal'); panel.hidden = !proposal;
  if (!proposal) { $('boardProposalDiff').innerHTML = ''; return; }
  const circuits = proposal.diff.circuits.map(item => {
    const before = item.before || {}, after = item.after || {};
    return `<article class="norm-card info"><span class="norm-sev">${esc(item.change.toUpperCase())}</span>
      <div class="norm-main"><h4>${esc(item.ref || item.circuit_id)}</h4>
        <div class="norm-values">
          ${metricChange('S, мм²', before.section_mm2, after.section_mm2)} ·
          ${metricChange('In, A', before.protection_in_a, after.protection_in_a)} ·
          ${metricChange('IB, A', before.design_current_a, after.design_current_a)} ·
          ${metricChange('ΔU, %', before.voltage_drop_pct, after.voltage_drop_pct)} ·
          <span><b>статус:</b> <span title="${esc(before.status || '')}">${esc(stLabel(before.status) || '—')}</span> → <span title="${esc(after.status || '')}">${esc(stLabel(after.status) || '—')}</span></span>
        </div></div></article>`;
  }).join('');
  const b0 = proposal.diff.board_before, b1 = proposal.diff.board_after;
  $('boardProposalDiff').innerHTML = circuits + `<div class="norm-values proposal-board">
    ${metricChange('Статус щита', b0.status, b1.status)} ·
    ${metricChange('Подключено, кВт', b0.connected_kw, b1.connected_kw)} ·
    ${metricChange('Перекос, %', b0.imbalance_pct, b1.imbalance_pct)} ·
    ${metricChange('Ток ввода, A', b0.incomer_md_a, b1.incomer_md_a)}
  </div>`;
  $('boardProposalIdentity').textContent = `${proposal.diff.data_identity} ${proposal.diff.signoff_notice} ${proposal.diff.disclaimer}`;
}
async function sendBoardCopilot() {
  const input = $('boardCopilotInput'), message = input.value.trim();
  if (!message || !PROJ) return;
  const baseVersion = PROJ.updated_at;
  input.value = ''; boardCopilotMessage('user', message); boardCopilotMessage('bot', 'Copilot планирует и вызывает инструменты…');
  const busy = $('boardCopilotMessages').lastChild;
  try {
    const response = await postJSON('/api/copilot', { project: PROJ, message, history: BOARD_COPILOT_HISTORY.slice(-20) });
    busy.remove();
    boardCopilotMessage('bot', response.reply, response.model ? `модель: ${response.model}` : '');
    BOARD_COPILOT_HISTORY.push({ role: 'user', content: message }, { role: 'assistant', content: response.reply });
    if (!response.provenance_ok) boardCopilotMessage('bot', `Числа в ответе не прошли проверку провенанса: ${response.unverified_numbers.join(', ')}. Предложение и расчёт не изменены.`);
    if (response.error) boardCopilotMessage('bot', `Запрос не завершён: ${response.error}.`);
    renderBoardProposal(response.proposal, baseVersion);
  } catch (error) { busy.remove(); boardCopilotMessage('bot', 'Ошибка: ' + error.message); }
}
function applyBoardProposal() {
  if (!BOARD_PROPOSAL || !PROJ) return;
  if (PROJ.updated_at !== BOARD_PROPOSAL_BASE) {
    toast('Проект изменился после расчёта предложения — запроси новое предложение.'); return;
  }
  const ids = new Set(PROJ.circuits.map(item => item.id));
  for (const operation of BOARD_PROPOSAL.ops) {
    if (operation.op === 'add' && ids.has(operation.circuit_id)) { toast('Предложение устарело: такая цепь уже есть в щите.'); return; }
    if (operation.op !== 'add' && !ids.has(operation.circuit_id)) { toast('Предложение устарело: целевая цепь не найдена.'); return; }
    if (operation.op === 'add') ids.add(operation.circuit_id);
    if (operation.op === 'delete') ids.delete(operation.circuit_id);
  }
  COPILOT_UNDO = JSON.parse(JSON.stringify(PROJ));
  BOARD_PROPOSAL.ops.forEach(operation => {
    if (operation.op === 'add') {
      PROJ.circuits.push({ id: operation.circuit_id, ref: operation.ref || '', sort_index: PROJ.circuits.length,
        request: operation.request, meta: operation.meta, result: null, signoff: { status: 'UNSIGNED_ADVISORY' } });
    } else if (operation.op === 'edit') {
      const circuit = PROJ.circuits.find(item => item.id === operation.circuit_id);
      if (operation.request) circuit.request = operation.request;
      if (operation.meta) circuit.meta = operation.meta;
      if (operation.ref !== null && operation.ref !== undefined) circuit.ref = operation.ref;
      circuit.result = null; circuit.signoff = { status: 'UNSIGNED_ADVISORY' };
    } else if (operation.op === 'delete') {
      PROJ.circuits = PROJ.circuits.filter(item => item.id !== operation.circuit_id);
    }
  });
  PROJ.circuits.forEach((circuit, index) => { circuit.sort_index = index; });
  projSet(PROJ); renderBoardProposal(null); renderProject();
  $('boardProposalUndo').hidden = false;
  toast('Предложение применено; щит пересчитан сервером заново.', 'Отменить', undoBoardProposal);
}
function undoBoardProposal() {
  if (!COPILOT_UNDO) return;
  PROJ = JSON.parse(JSON.stringify(COPILOT_UNDO)); COPILOT_UNDO = null;
  projSet(PROJ); renderProject(); $('boardProposalUndo').hidden = true;
}
$('boardCopilotSend').addEventListener('click', sendBoardCopilot);
$('boardCopilotInput').addEventListener('keydown', e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) sendBoardCopilot(); });
$('boardProposalApply').addEventListener('click', applyBoardProposal);
$('boardProposalReject').addEventListener('click', () => { renderBoardProposal(null); boardCopilotMessage('bot', 'Предложение отклонено; проект не изменён.'); });
$('boardProposalUndo').addEventListener('click', undoBoardProposal);

// ---------- deterministic normcheck (docs/14) ----------
function resetNormcheckPanel() {
  NORMCHECK = null;
  $('normSummary').textContent = 'не запускался';
  $('normFindings').innerHTML = '<span class="dim">Нажми «Нормоконтроль»: правила пересчитают проект на сервере и не используют сохранённые клиентские результаты.</span>';
  $('normIdentity').textContent = '';
  clearNormMarkers();
}
function clearNormMarkers() {
  document.querySelectorAll('#scheduleBody tr[data-cid]').forEach(tr => {
    tr.classList.remove('norm-error', 'norm-warning', 'norm-info', 'norm-review');
    tr.querySelectorAll('.norm-marker').forEach(el => el.remove());
  });
}
function citeText(c) {
  if (!c) return 'источник правила не задан';
  return [c.standard, c.clause ? `§ ${c.clause}` : '', c.table ? `табл. ${c.table}` : ''].filter(Boolean).join(' · ');
}
function maxSeverity(a, b) {
  const rank = { error: 3, warning: 2, info: 1, review: 0 };
  if (!a) return b;
  return (rank[b] || 0) > (rank[a] || 0) ? b : a;
}
function applyNormMarkers(findings) {
  clearNormMarkers();
  const byCircuit = {};
  findings.filter(f => f.circuit_id).forEach(f => {
    const level = f.status === 'not_checked' ? 'review' : f.severity;
    byCircuit[f.circuit_id] = maxSeverity(byCircuit[f.circuit_id], level);
  });
  document.querySelectorAll('#scheduleBody tr[data-cid]').forEach(tr => {
    const level = byCircuit[tr.dataset.cid]; if (!level) return;
    tr.classList.add('norm-' + level);
    const marker = document.createElement('span'); marker.className = 'norm-marker ' + level;
    marker.textContent = level === 'review' ? '?' : '!';
    marker.title = level === 'review' ? 'Есть непроверенные правила' : `Замечание: ${sevLabel(level)}`;
    tr.querySelector('td')?.appendChild(marker);
  });
}
function renderNormcheck(rep) {
  NORMCHECK = rep;
  const s = rep.summary;
  $('normSummary').textContent = `${s.errors} ошибок · ${s.warnings} предупреждений · ${s.infos} инфо · ${s.not_checked} не проверено`;
  $('normFindings').innerHTML = rep.findings.length ? rep.findings.map(f => {
    const unchecked = f.status === 'not_checked';
    const label = unchecked ? 'не проверено' : sevLabel(f.severity);
    const open = f.circuit_id ? `<button class="ghost norm-open" data-norm-cid="${esc(f.circuit_id)}">${esc(f.circuit_ref || 'цепь')} →</button>` : '';
    return `<article class="norm-card ${esc(f.severity)} ${unchecked ? 'not-checked' : ''}">
      <span class="norm-sev" title="${esc(unchecked ? 'not_checked' : f.severity)}">${esc(label)}</span>
      <div class="norm-main"><h4>${esc(f.rule_id)} · ${esc(f.title)}</h4><p>${esc(f.detail)}</p>
        <div class="norm-values">факт: ${esc(JSON.stringify(f.observed))}<br>требуется: ${esc(JSON.stringify(f.required))}</div>
        <div class="norm-cite">${esc(citeText(f.citation))}${citeOpenButton(f.citation)} · ${esc(f.source_section)} · ${f.source_trusted ? 'источник проверен' : 'источник требует проверки'}</div>
      </div>${open}</article>`;
  }).join('') : '<span class="dim">Нарушений и непроверенных правил не найдено.</span>';
  $('normIdentity').textContent = humanizeSections(`${rep.data_identity} ${rep.provenance_note} ${rep.signoff_notice} ${rep.disclaimer}`);
  applyNormMarkers(rep.findings);
}
async function loadNormcheck() {
  if (!PROJ) return;
  $('normSummary').textContent = 'проверяю R01–R10…';
  $('normFindings').innerHTML = '<span class="dim">Сервер заново пересчитывает все цепи…</span>';
  try { renderNormcheck(await postJSON('/api/normcheck', { project: PROJ })); }
  catch (e) { $('normSummary').textContent = 'ошибка'; $('normFindings').innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`; }
}
$('normFindings').addEventListener('click', e => {
  const cid = e.target.closest('[data-norm-cid]')?.dataset.normCid;
  if (cid) go('/p/' + PROJ.id + '/c/' + cid);
});

// ---------- single-line preview + document bundle (docs/13) ----------
function renderSldInto(sld, targetId = 'sldPreview') {  // sld = {svg, sheets} from a fresh report
  const el = $(targetId);
  if (!sld || !sld.svg) { el.innerHTML = '<span class="dim">Нет цепей — добавь цепь.</span>'; return; }
  const note = sld.sheets > 1 ? `<div class="dim" style="margin-bottom:6px">Листов: ${sld.sheets} (показан 1-й; полный набор — в пакете документов).</div>` : '';
  el.innerHTML = note + sld.svg;
}
async function loadSldPreview() {  // manual "Обновить предпросмотр" — fetches fresh
  const el = $('sldPreview');
  if (!PROJ || !PROJ.circuits.length) { el.innerHTML = '<span class="dim">Нет цепей — добавь цепь.</span>'; return; }
  el.innerHTML = '<span class="dim">Строю однолинейку…</span>';
  try { renderSldInto(await postJSON('/api/project-sld', { project: PROJ })); }
  catch (e) { el.innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`; }
}
async function downloadBundle() {
  if (!PROJ || !PROJ.circuits.length) { toast('Добавь хотя бы одну цепь'); return; }
  toast('Готовлю пакет документов…');
  try {
    const r = await fetch(API + '/api/project-export', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project: PROJ }) });
    if (!r.ok) {
      const payload = await r.json().catch(() => null);
      toast('ошибка экспорта: ' + (payload?.detail?.message || payload?.detail || `HTTP ${r.status}`));
      return;
    }
    const name = (PROJ.board_ref || PROJ.name || 'board').replace(/\s+/g, '_') + '_пакет.zip';
    download(name, await r.blob(), 'application/zip');
    toast('Пакет документов скачан (SVG+DXF+XLSX+MD)');
  } catch (e) { toast('' + e.message); }
}
async function renderPrint() {
  const root = $('printRoot');
  root.innerHTML = '<p class="dim">Готовлю печатную страницу…</p>';
  let rep;
  // Always fetch a FRESH report (with the SLD) in one call — the print route reloads PROJ from
  // localStorage, so a cached _lastReport would risk printing a stale schedule beside a fresh diagram.
  try { rep = await postJSON('/api/project-report?sld=1', { project: PROJ }); }
  catch (e) { root.innerHTML = `<p style="color:var(--bad)">${esc(e.message)}</p>`; return; }
  const sld = rep.sld || { svg: '' };
  const b = rep.board, t = b.totals, sp = PROJ.supply || {};
  const rows = rep.rows.map(r => `<tr><td>${esc(r.ref)}</td><td>${esc(r.description)}</td><td>${fmt(r.kw)}</td><td>${esc(r.phase)}</td><td>${fmt(r.IB_a, 1)}</td><td>${esc(deviceText(r.device))}</td><td>${esc(r.rcd)}</td><td>${esc(r.cable)}</td><td>${fmt(r.length_m)}</td><td>${fmt(r.Iz_a, 1)}</td><td>${fmt(r.dU_pct)}</td><td>${esc(stLabel(r.status))}</td></tr>`).join('');
  root.innerHTML = `
    <div class="print-actions no-print"><button id="doPrint" class="primary"><svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><path d="M6 9V3a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v6"/><rect x="6" y="14" width="12" height="8" rx="1"/></svg>Печать / Сохранить PDF</button> <button id="printBack" class="ghost">← Назад к щиту</button></div>
    <h1 class="ptitle">${esc(PROJ.name)} <small>${esc(PROJ.board_ref || '')}</small></h1>
    <p class="pmeta">Питание: ${esc(sp.voltage_v || 400)} В · ${esc(sp.phases || 3)}ф · ${esc(sp.earthing || 'TN-C-S')} · мест ${b.ways.used}/${b.ways.total}</p>
    <p class="pnote">${esc(humanizeSections(rep.data_identity || ''))}</p>
    <h2>Таблица щита (panel schedule)</h2>
    <table class="ptable"><thead><tr><th>Ref</th><th>Описание</th><th>кВт</th><th>Фаза</th><th>IB,A</th><th>Аппарат</th><th>УЗО</th><th>Кабель</th><th>L,м</th><th>IZ,A</th><th>ΔU%</th><th>Статус</th></tr></thead><tbody>${rows}</tbody></table>
    <h2>Итоги щита</h2>
    <p class="pmeta">Подключённая нагрузка: <b>${fmt(t.connected_kw)} кВт / ${fmt(t.connected_kva)} кВА</b> · ${t.imbalance_applicable ? `перекос фаз ${fmt(t.imbalance_pct, 0)}%${t.imbalance_flag ? ' — выше нормы' : ''}` : 'однофазный щит, перекос фаз неприменим'} · резерв мест ${b.ways.spare}/${b.ways.total}</p>
    <h2>Однолинейная схема</h2>
    <div class="print-sld">${sld.svg || ''}</div>
    <p class="pnote">${esc(humanizeSections(rep.provenance_note || ''))}</p>
    <p class="pnote"><b>${esc(rep.signoff_notice || stLabel('UNSIGNED_ADVISORY'))}</b></p>
    <p class="pnote"><b>${esc(rep.disclaimer || '')}</b></p>`;
  $('doPrint').addEventListener('click', () => window.print());
  $('printBack').addEventListener('click', () => go('/p/' + PROJ.id));
}

async function nlAddCircuit() {
  const text = $('nlQuick').value.trim(); if (!text) return;
  $('nlQuick').value = ''; toast('Разбираю описание…');
  try {
    const j = await postJSON('/api/intake', { text });
    if (!j.ok) { toast('' + (j.message || 'не удалось разобрать')); return; }
    const c = newCircuit(j.request, { phase: j.request.load.phases === 3 ? 'L1L2L3' : 'L1', rcd: { present: false }, diversity_category: j.request.load.purpose }, 'C' + (PROJ.circuits.length + 1));
    c.sort_index = PROJ.circuits.length; PROJ.circuits.push(c); projSet(PROJ);
    go('/p/' + PROJ.id + '/c/' + c.id);
  } catch (e) { toast('ошибка: ' + e.message); }
}

// ========================= EDITOR =========================
function openEditor(cid) {
  CID = cid;
  const c = PROJ.circuits.find(x => x.id === cid); if (!c) { go('/p/' + PROJ.id); return; }
  crumbs([PROJ.name, '#/p/' + PROJ.id]);
  fillForm(c.request, c.meta);
  $('edRef').textContent = c.ref || '(без ref)';
  setSignoffBadge(c.signoff);
  recompute();
}
function setSignoffBadge(so) {
  const el = $('edSignoff'); const signed = so?.status === 'SIGNED';
  el.textContent = signed ? `${stLabel('SIGNED')}: ${so.engineer_name || ''}` : stLabel('UNSIGNED_ADVISORY');
  el.title = signed ? 'SIGNED' : 'UNSIGNED_ADVISORY';
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
  const packNote = VIZ.data_provenance_note ? humanizeSections(VIZ.data_provenance_note) : `Норм-пакет «${packLabel(r.data_pack.name)}» (${humanizeSections(r.data_pack.status)}).`;
  $('provenance').textContent = packNote + ' Не данные производителя оборудования.';
  relayoutActive();
}
function renderSummary(r) {
  const c = r.selected_cable, p = r.selected_protection;
  // design-v2-spec §2.6: a quiet key-value strip, with "Определяет:" (the governing constraint)
  // called out — last entry carries the `strong` flag, rendered with the .kv-strong class.
  $('summary').innerHTML = [['Кабель', `${fmt(c.cross_section_mm2)} мм² ${c.material}/${c.insulation}`], ['IZ', `${fmt(c.Iz_a)} A`], ['Аппарат', `${esc(deviceText(p.device_class))} ${fmt(p.In_a)} A`, p.device_class], ['IB', `${fmt(r.design_current_a)} A`], ['ΔU', `${fmt(r.voltage_drop_pct)} %`], ['Определяет', esc(govLabel(c.governing_constraint)), c.governing_constraint, true]].map(([k, v, ttl, strong]) => `<div class="kv${strong ? ' kv-strong' : ''}"><span>${k}:</span> <b${ttl ? ` title="${esc(ttl)}"` : ''}>${v}</b></div>`).join('');
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
// design-v2-spec §3: Plotly can't consume CSS custom properties directly, so these mirror the
// :root tokens in styles.css by value — keep them in sync if the palette ever changes there.
const T = { navy: '#24407A', navySoft: 'rgba(36,64,122,.12)', burgundy: '#7E2440', ink: '#1C2536', dim: '#5A6478', faint: '#8B93A5', ok: '#2E7D4F', okMuted: 'rgba(46,125,79,.55)', warn: '#A9701E', bad: '#C8321F', grid: '#ECE9E1', notPassing: '#D8D3C7' };
const BASE = { paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font: { color: T.dim, size: 12, family: "'IBM Plex Sans', -apple-system, sans-serif" }, margin: { l: 60, r: 18, t: 46, b: 70 }, legend: { orientation: 'h', y: -0.22, yanchor: 'top', x: 0 }, showlegend: true };
const CFG = { responsive: true, displayModeBar: false };
function vlines(shapes, x, color, dash, label, anns) { if (x == null) return; shapes.push({ type: 'line', x0: x, x1: x, yref: 'paper', y0: 0, y1: 1, line: { color, width: 1.5, dash } }); anns.push({ x: Math.log10(x), y: 1, yref: 'paper', text: label, showarrow: false, font: { color, size: 11 }, xanchor: 'left', yanchor: 'bottom' }); }
function renderTCC(t) {
  if (!t) return; const dmax = t.device.max, dmin = t.device.min;
  const deviceOff = t.device.available === false;
  const traces = [
    { x: t.cable.withstand.map(p => p[0]), y: t.cable.withstand.map(p => p[1]), name: `Кабель ${fmt(t.cable.section_mm2)} мм² (I²t)`, mode: 'lines', line: { color: T.bad, width: 2.5 } },
  ];
  if (!deviceOff) traces.push(
    { x: dmax.map(p => p[0]), y: dmax.map(p => p[1]), mode: 'lines', line: { color: T.navy, width: 1 }, showlegend: false },
    { x: dmin.map(p => p[0]), y: dmin.map(p => p[1]), name: `${t.device.class}${t.device.class === 'gG_fuse' ? '' : ' ' + t.device.curve_type} (полоса)`, mode: 'lines', line: { color: T.navy, width: 1 }, fill: 'tonexty', fillcolor: T.navySoft },
  );
  const shapes = [], anns = [];
  vlines(shapes, t.markers.IB, T.faint, 'dot', 'IB', anns); vlines(shapes, t.markers.In, T.warn, 'dash', 'In', anns); vlines(shapes, t.markers.Iscc, T.bad, 'dot', 'Iscc', anns);
  if (deviceOff) anns.push({ xref: 'paper', yref: 'paper', x: 0.5, y: 0.5, text: 'кривая аппарата отсутствует в норм-пакете', showarrow: false, font: { color: T.faint, size: 12 } });
  const coord = deviceOff ? '' : (t.coordinated === null ? '' : (t.coordinated ? '  ·  иллюстративная проверка: OK' : '  ·  не координируется (иллюстр.)'));
  Plotly.react('plot_tcc', traces, Object.assign({}, BASE, { title: { text: 'Время-токовая координация' + coord, font: { size: 14, color: t.coordinated === false && !deviceOff ? T.bad : T.dim } }, xaxis: { type: 'log', title: 'Ток, A', gridcolor: T.grid }, yaxis: { type: 'log', title: 'Время, с', gridcolor: T.grid }, shapes, annotations: anns }), CFG);
}
function renderSweep(s) {
  if (!s) return; const x = s.rows.map(r => fmt(r.section_mm2)), y = s.rows.map(r => r.Iz_a);
  const colors = s.rows.map(r => r.section_mm2 === s.chosen_mm2 ? T.burgundy : (r.amp_ok && r.vd_ok && r.sc_ok ? T.okMuted : T.notPassing));
  Plotly.react('plot_sweep', [{ x, y, type: 'bar', marker: { color: colors } }], Object.assign({}, BASE, { title: { text: `Подбор сечения — выбрано ${fmt(s.chosen_mm2)} мм² (${govLabel(s.governing)})`, font: { size: 14 } }, xaxis: { title: 'Сечение, мм²', type: 'category' }, yaxis: { title: 'IZ, A', gridcolor: T.grid }, showlegend: false, shapes: [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: s.required_iz_a, y1: s.required_iz_a, line: { color: T.warn, width: 1.5, dash: 'dash' } }], annotations: [{ xref: 'paper', x: 0.01, y: s.required_iz_a, text: `требуемый IZ ≥ ${fmt(s.required_iz_a)} A`, showarrow: false, font: { color: T.warn, size: 11 }, yanchor: 'bottom' }] }), CFG);
}
function renderVD(v) {
  if (!v) return;
  Plotly.react('plot_vd', [{ x: v.series.map(p => p[0]), y: v.series.map(p => p[1]), mode: 'lines', name: `ΔU при ${fmt(v.section_mm2)} мм²`, line: { color: T.navy, width: 2.5 } }, { x: [v.current_length_m], y: [v.current_pct], mode: 'markers', name: 'текущая длина', marker: { color: T.ink, size: 9 } }], Object.assign({}, BASE, { title: { text: 'Профиль падения напряжения', font: { size: 14 } }, xaxis: { title: 'Длина, м', gridcolor: T.grid }, yaxis: { title: 'ΔU, %', gridcolor: T.grid }, shapes: [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: v.limit_pct, y1: v.limit_pct, line: { color: T.bad, width: 1.5, dash: 'dash' } }], annotations: [{ xref: 'paper', x: 0.01, y: v.limit_pct, text: `предел ${fmt(v.limit_pct)} %`, showarrow: false, font: { color: T.bad, size: 11 }, yanchor: 'bottom' }] }), CFG);
}
function renderDerating(d) {
  if (!d) return;
  Plotly.react('plot_derating', [{ x: d.stages.map(s => s.label), y: d.stages.map(s => s.value), type: 'bar', marker: { color: [T.navy, '#4F6CA8', T.ok] }, text: d.stages.map(s => fmt(s.value)), textposition: 'outside' }], Object.assign({}, BASE, { title: { text: `Поправочные коэффициенты: It → IZ (нужно ≥ ${fmt(d.required_iz_a)} A)`, font: { size: 14 } }, yaxis: { title: 'A', gridcolor: T.grid }, showlegend: false, shapes: [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: d.required_iz_a, y1: d.required_iz_a, line: { color: T.warn, width: 1.5, dash: 'dash' } }] }), CFG);
}
function renderSLD(s) {
  if (!s) return; const col = { PASS: T.ok, FAIL: T.bad, NEEDS_REVIEW: T.warn }[s.status] || T.faint;
  const bw = 150, gap = 46, y = 70, h = 78; let x = 20, svg = `<svg viewBox="0 0 ${20 + (bw + gap) * 4} 200" class="sld" style="max-width:100%">`;
  s.nodes.forEach((n, idx) => {
    if (idx > 0) svg += `<line x1="${x - gap}" y1="${y + h / 2}" x2="${x}" y2="${y + h / 2}" stroke="${T.ink}" stroke-width="2"/>`;
    svg += `<rect x="${x}" y="${y}" width="${bw}" height="${h}" rx="3" fill="#FFFFFF" stroke="${idx === s.nodes.length - 1 ? col : T.ink}" stroke-width="2"/>`;
    svg += `<text x="${x + bw / 2}" y="${y + 30}" fill="${T.ink}" font-size="14" font-weight="600" text-anchor="middle">${esc(deviceText(n.label))}</text>`;
    svg += `<text x="${x + bw / 2}" y="${y + 52}" fill="${T.dim}" font-size="12" text-anchor="middle" font-family="monospace">${esc(deviceText(n.sub))}</text>`;
    x += bw + gap;
  });
  svg += `<text x="20" y="30" fill="${col}" font-size="15" font-weight="700">${esc(stLabel(s.status))} · определяет: ${esc(govLabel(s.governing))}</text></svg>`;
  $('plot_sld').innerHTML = svg;
}
function renderTrace(r) {
  const dot = st => `<span class="st-dot st-${st}"></span>`;
  $('trace').innerHTML = r.audit_trace.map(s => {
    const cites = (s.citations || []).map(c =>
      esc(`${c.standard}${c.clause ? ' §' + c.clause : ''}${c.table ? ' Табл.' + c.table : ''}`) + citeOpenButton(c)
    ).join('; ');
    return `<details class="step"><summary>${dot(s.status)}${esc(s.title)}</summary><div class="body">${s.formula ? `<div class="f">${esc(s.formula)}</div>` : ''}${s.computation ? `<div class="comp">${esc(s.computation)}</div>` : ''}${cites ? `<div class="cite">↳ ${cites}</div>` : ''}</div></details>`;
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

// design-v2-spec §2.6: right-column "Копилот"/"Обоснование расчёта" tabs — deliberately scoped to
// #rtabs/.rpane (not the shared .tabs/.tabpane the block above uses), which is queried document-wide.
$('rtabs').addEventListener('click', e => {
  if (e.target.tagName !== 'BUTTON') return;
  document.querySelectorAll('#rtabs button').forEach(b => b.classList.remove('active')); e.target.classList.add('active');
  const id = e.target.dataset.rtab;
  $('paneCopilot').hidden = id !== 'copilot'; $('paneTrace').hidden = id !== 'trace';
});

// ---------- copilot (editor) ----------
function addMsg(cls, html, sub) { const d = document.createElement('div'); d.className = 'msg ' + cls; d.innerHTML = html + (sub ? `<small>${esc(sub)}</small>` : ''); $('chat').appendChild(d); $('chat').scrollTop = $('chat').scrollHeight; }
async function chatSend() {
  const text = $('chatInput').value.trim(); if (!text) return; addMsg('user', esc(text)); $('chatInput').value = ''; addMsg('bot', 'разбираю…'); const busy = $('chat').lastChild;
  try { const j = await postJSON('/api/intake', { text }); busy.remove(); if (!j.ok) { addMsg('bot', '' + esc(j.message || 'не удалось')); return; } fillForm(j.request, buildMeta()); await recompute(); const c = VIZ.result.selected_cable, p = VIZ.result.selected_protection; addMsg('bot', `Разобрал: <b>${fmt(c.cross_section_mm2)} мм²</b>, ${esc(deviceText(p.device_class))} <b>${fmt(p.In_a)} A</b>, статус <b>${esc(stLabel(VIZ.result.overall_status))}</b>. Нажми «Сохранить в щит».`, 'модель: ' + (j.model || '')); }
  catch (e) { busy.remove(); addMsg('bot', '' + esc(e.message)); }
}
async function doExplain() {
  addMsg('bot', 'объясняю…'); const busy = $('chat').lastChild;
  try { const j = await postJSON('/api/explain' + packQuery(), buildRequest()); busy.remove(); const n = j.narrative; const tag = n.model ? `${n.model}; провенанс ${n.provenance_ok ? 'OK' : 'FAIL ' + JSON.stringify(n.unverified_numbers)}` : 'шаблон (без ключа)'; addMsg('bot', esc(n.text), tag); }
  catch (e) { busy.remove(); addMsg('bot', '' + esc(e.message)); }
}
async function doReview() {
  addMsg('bot', 'ревьюер проверяет…'); const busy = $('chat').lastChild;
  try { const j = await postJSON('/api/verify' + packQuery(), buildRequest()); busy.remove(); const v = j.verdict; const bad = !v.agrees || !v.deterministic_ok; const issues = (v.issues || []).length ? '<br>' + v.issues.map(esc).join('<br>') : ''; addMsg('rev' + (bad ? ' bad' : ''), `Ревьюер: детерм. <b style="color:var(--${v.deterministic_ok ? 'ok' : 'bad'})">${v.deterministic_ok ? 'OK' : 'FAIL'}</b>, LLM ${v.agrees ? 'согласен' : 'НЕ согласен'}${issues}`, v.model ? 'модель: ' + v.model : 'детерминированно'); }
  catch (e) { busy.remove(); addMsg('bot', '' + esc(e.message)); }
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
  rd.onload = async () => {
    try {
      const p = JSON.parse(rd.result);
      const validated = await postJSON('/api/project-validate', { project: p });
      const copy = deepCopyProject(validated.project, validated.project.name);
      projSet(copy); e.target.value = ''; go('/p/' + copy.id); toast('Проект импортирован');
    } catch (err) { toast('не удалось импортировать файл: ' + err.message); }
  };
  rd.readAsText(f);
}
async function downloadReport() {
  toast('Готовлю отчёт по щиту…');
  try { const rep = await postJSON('/api/project-report', { project: PROJ }); download(`${(PROJ.board_ref || PROJ.name).replace(/\s+/g, '_')}_отчёт.md`, rep.markdown, 'text/markdown'); }
  catch (e) { toast('' + e.message); }
}

// ---------- net ----------
async function postJSON(url, body) {
  const r = await fetch(API + url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!r.ok) {
    const payload = await r.json().catch(() => null);
    throw new Error(payload?.detail?.message || payload?.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

// ---------- project sync + read-only shares (docs/15) ----------
function workspaceHeaders(extra = {}) { return Object.assign({}, extra, { 'X-Workspace': wsGet().id }); }
function scheduleProjectSync(projectId) {
  if (!SYNC_AVAILABLE) return;
  clearTimeout(PROJECT_SYNC_TIMERS.get(projectId));
  PROJECT_SYNC_TIMERS.set(projectId, setTimeout(() => {
    PROJECT_SYNC_TIMERS.delete(projectId);
    syncProject(projectId);
  }, 2000));
}
async function syncProject(projectOrId) {
  if (!SYNC_AVAILABLE) return false;
  const project = typeof projectOrId === 'string' ? projGet(projectOrId) : projectOrId;
  if (!project) return false;
  const snapshot = JSON.parse(JSON.stringify(project));
  try {
    const response = await fetch(API + '/api/projects/' + encodeURIComponent(snapshot.id), {
      method: 'PUT', headers: workspaceHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ project: snapshot }),
    });
    const payload = await response.json().catch(() => null);
    if (response.status === 409 && payload?.detail?.error === 'project_conflict') {
      const remote = payload.detail.project;
      projSet(remote, { touch: false, sync: false });
      if (PROJ?.id === remote.id) { PROJ = remote; route(); }
      toast('Проект обновлён с другого устройства; показана более свежая версия.');
      return false;
    }
    if (response.status === 503 && payload?.detail?.error === 'no_db') {
      SYNC_AVAILABLE = false;
      return false;
    }
    if (!response.ok) throw new Error(payload?.detail?.message || `HTTP ${response.status}`);
    const current = projGet(snapshot.id);
    if (current?.updated_at === snapshot.updated_at) {
      projSet(payload.project, { touch: false, sync: false });
      if (PROJ?.id === snapshot.id) PROJ = payload.project;
      return true;
    }
    return false;
  } catch (error) {
    setSave('unsaved');
    toast('Локально сохранено; синхронизация недоступна: ' + error.message);
    return false;
  }
}
async function deleteRemoteProject(projectId) {
  try {
    await fetch(API + '/api/projects/' + encodeURIComponent(projectId), {
      method: 'DELETE', headers: workspaceHeaders(),
    });
  } catch { /* local delete remains valid offline */ }
}
async function syncFromServer() {
  if (!SYNC_AVAILABLE) return;
  try {
    const response = await fetch(API + '/api/projects', { headers: workspaceHeaders() });
    if (!response.ok) return;
    const payload = await response.json();
    let pulled = false;
    payload.projects.forEach(remote => {
      const local = projGet(remote.id);
      if (!local || (remote.updated_at || '') > (local.updated_at || '')) {
        projSet(remote, { touch: false, sync: false });
        pulled = pulled || Boolean(local);
      } else if ((local.updated_at || '') > (remote.updated_at || '')) {
        scheduleProjectSync(local.id);
      } else if (JSON.stringify(local) !== JSON.stringify(remote)) {
        scheduleProjectSync(local.id); // server resolves equal-version ambiguity with explicit 409
      }
    });
    if (pulled) toast('Проекты обновлены с другого устройства.');
  } catch { /* localStorage-first: startup remains usable offline */ }
}
async function createShareLink() {
  if (!PROJ) return;
  if (!SYNC_AVAILABLE) { toast('Share-ссылка требует DATABASE_URL; локальный проект сохранён.'); return; }
  clearTimeout(PROJECT_SYNC_TIMERS.get(PROJ.id)); PROJECT_SYNC_TIMERS.delete(PROJ.id);
  if (!await syncProject(PROJ.id)) return;
  try {
    const response = await fetch(API + '/api/projects/' + encodeURIComponent(PROJ.id) + '/share', {
      method: 'POST', headers: workspaceHeaders(),
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) throw new Error(payload?.detail?.message || `HTTP ${response.status}`);
    const link = `${location.origin}${location.pathname}#/s/${payload.token}`;
    let copied = false;
    if (navigator.clipboard?.writeText) {
      try { await navigator.clipboard.writeText(link); copied = true; } catch { copied = false; }
    }
    if (!copied) prompt('Скопируйте ссылку (только просмотр):', link);
    toast(
      `${copied ? 'Share-ссылка (только просмотр) скопирована' : 'Share-ссылка (только просмотр) создана'}. Любой с ссылкой может читать проект.`,
      'Открыть',
      () => go('/s/' + payload.token),
    );
  } catch (error) { toast('share-ссылка не создана: ' + error.message); }
}
async function renderShared(token) {
  const body = $('sharedScheduleBody');
  body.innerHTML = '<tr><td colspan="13" class="dim">Сервер заново пересчитывает проект…</td></tr>';
  $('sharedTotals').innerHTML = '';
  $('sharedSld').innerHTML = '<span class="dim">Загрузка…</span>';
  try {
    const response = await fetch(API + '/api/shared/' + encodeURIComponent(token));
    const payload = await response.json().catch(() => null);
    if (!response.ok) throw new Error(payload?.detail?.message || `HTTP ${response.status}`);
    const project = payload.project, report = payload.report;
    $('sharedName').textContent = `${project.name} · ${project.board_ref || ''}`;
    $('sharedMeta').textContent = `${project.location || ''} · ${project.supply.voltage_v} В · ${project.supply.phases}ф · обновлён ${project.updated_at || '—'}`;
    body.innerHTML = report.rows.length ? report.rows.map(row => rowHTML(row, true)).join('')
      : '<tr><td colspan="13" class="empty">В проекте нет цепей.</td></tr>';
    renderTotals(report.board, 'sharedTotals');
    renderSldInto(report.sld, 'sharedSld');
    $('sharedIdentity').textContent = humanizeSections(`${report.data_identity} ${report.provenance_note} ${report.signoff_notice} ${report.disclaimer}`);
    topBadge(report.board.status);
  } catch (error) {
    body.innerHTML = `<tr><td colspan="13" style="color:var(--bad)">${esc(error.message)}</td></tr>`;
    $('sharedName').textContent = 'Share-ссылка недоступна';
  }
}

// ---------- boot ----------
window.addEventListener('hashchange', route);
// llm_admission is server-controlled ("local"/"disabled"); an older server may omit it entirely,
// in which case behavior stays exactly as before (mode-only tags/greeting below).
const llmAdmissionDisabled = () => HEALTH.mode !== 'fallback' && HEALTH.llm_admission === 'disabled';
async function boot() {
  toggleSrc(); toggleCurve(); wsGet(); migrateLegacy();
  try {
    HEALTH = await (await fetch(API + '/api/health')).json();
    const el = $('modeTag');
    if (llmAdmissionDisabled()) { el.textContent = 'ИИ: отключён'; el.title = 'ИИ-помощник на этом стенде отключён владельцем (llm_admission: disabled)'; }
    else { el.textContent = HEALTH.mode === 'fallback' ? 'ИИ: резервный режим' : 'ИИ: активен'; el.title = `${HEALTH.mode} · ${HEALTH.model_fast || ''}`; }
  } catch { const el = $('modeTag'); el.textContent = 'ИИ: недоступен'; el.title = 'offline'; }
  try { PACKS = await (await fetch(API + '/api/packs')).json(); } catch { PACKS = []; }
  SYNC_AVAILABLE = HEALTH.project_store === 'neon';
  await syncFromServer();
  if (HEALTH.mode === 'fallback') addMsg('bot', 'Ключ Gemini на сервере не задан — копилот в режиме фолбэка (NL-разбор недоступен, объяснение — шаблон, ревьюер — детерминированный). Расчёт и графики работают полностью.');
  else if (llmAdmissionDisabled()) addMsg('bot', 'ИИ-помощник на этом стенде отключён владельцем — расчёт, графики и нормоконтроль работают полностью.');
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

// ========================= NORM LIBRARY (docs/20 §10) =========================
// Читалка официальных текстов норм. Числа для расчёта сюда не приходят и отсюда не уходят:
// движок берёт их только из датапаков (docs/20 §0, §8.3).
const NORM_ST = { in_force: 'действует', repealed: 'утратил силу', unknown: 'статус неясен' };
let NORMS = { docs: [], docId: null };

const normBadge = (st) => `<span class="nlib-status ${esc(st)}" title="${esc(st)}">${esc(NORM_ST[st] || st)}</span>`;
// Сниппет приходит с маркерами [[…]] от обоих бэкендов поиска: экранируем текст, потом подсвечиваем.
const normSnippet = (s) => esc(s).replace(/\[\[(.+?)\]\]/g, '<mark>$1</mark>');
// §8.4: плашка с дословной сноской об утрате силы — везде, где показан текст документа.
const normNote = (d) => (d.status === 'repealed' && d.status_note)
  ? `<div class="nlib-repealed-note">${esc(d.status_note)}</div>` : '';

function normBody(section) {
  if (section.has_table) return `<pre class="nlib-table">${esc(section.body)}</pre>`;
  return section.body.split('\n\n').filter(Boolean).map(p => `<p>${esc(p)}</p>`).join('');
}

async function normFetch(path, options) {
  const r = await fetch(API + path, options);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    // §8.2: честный отказ со списком того, что есть — без подстановки похожего документа.
    const detail = body.detail || {};
    throw new Error(detail.message || 'Библиотека норм недоступна');
  }
  return body;
}

async function loadNormDocs() {
  try {
    NORMS.docs = (await normFetch('/api/norms')).documents;
  } catch (e) { NORMS.docs = []; }
  const list = $('normDocList');
  list.innerHTML = NORMS.docs.length ? NORMS.docs.map(d => `
    <button class="nlib-doc ${d.id === NORMS.docId ? 'active' : ''}" data-doc="${esc(d.id)}">
      <div class="t">${esc(d.title)}</div>
      <div class="m">${normBadge(d.status)} · ${d.sections} п. · ${esc(d.id)}</div>
    </button>`).join('')
    : '<span class="dim">Библиотека пуста. Загрузите корпус: <code>electricopilot norms ingest</code>.</span>';
}

async function openNormDoc(docId, opts = {}) {
  NORMS.docId = docId;
  await loadNormDocs();
  $('normResults').hidden = true;
  $('normEmpty').hidden = true;
  const toc = $('normToc'); const reader = $('normReader');
  toc.hidden = false; reader.hidden = false;
  reader.innerHTML = '<span class="dim">загружаю текст…</span>';
  const qs = new URLSearchParams();
  if (opts.anchor) qs.set('anchor', opts.anchor);
  if (opts.offset != null) qs.set('offset', opts.offset);
  try {
    const data = await normFetch('/api/norms/' + encodeURIComponent(docId) + (qs.toString() ? '?' + qs : ''));
    NORMS.doc = data.document;
    renderNormOutline(docId, data);
    renderNormReader(docId, data);
    if (opts.anchor) {
      const target = reader.querySelector(`[data-anchor-mark="${CSS.escape(opts.anchor)}"]`);
      if (target) { target.scrollIntoView({ block: 'center' }); target.classList.add('nlib-focus'); }
    } else reader.scrollTop = 0;
  } catch (e) { reader.innerHTML = `<span class="dim">${esc(e.message)}</span>`; }
}

// Оглавление строится по заголовкам разделов/глав/параграфов, а не по каждому пункту:
// у ПУЭ 7791 пункт, список их якорей — не содержание, а стена кодов.
function renderNormOutline(docId, data) {
  const d = data.document;
  $('normToc').innerHTML = `<div class="nlib-doc-head">
      <h3>${esc(d.title)}</h3>
      <div class="nlib-doc-meta">${normBadge(d.status)} · ${d.sections} пунктов ·
        <a href="${esc(d.source_url)}" target="_blank" rel="noopener">первоисточник ↗</a></div>
      ${normNote(d)}
    </div>
    <div class="nlib-outline">${data.outline.map(o => `
      <button class="nlib-outline-item d${o.depth}" data-doc="${esc(docId)}" data-anchor="${esc(o.anchor)}"
        title="${esc(o.text)}">${esc(o.text)}</button>`).join('') || '<span class="dim">Разделы не размечены.</span>'}</div>`;
}

// Сплошной текст, как в первоисточнике: заголовки — заголовками, пункты — абзацами,
// якорь показывается мелко и только при наведении (это адрес для ссылки, а не часть нормы).
function renderNormReader(docId, data) {
  const p = data.page;
  const html = p.sections.map(s => {
    if (s.kind === 'heading') {
      const level = s.depth === 0 ? 'h2' : s.depth === 2 ? 'h4' : 'h3';
      return `<${level} class="nlib-h" data-anchor-mark="${esc(s.anchor)}">${esc(s.heading)}</${level}>`;
    }
    const body = s.has_table
      ? `<pre class="nlib-table">${esc(s.body)}</pre>`
      : s.body.split('\n\n').filter(Boolean).map(x => `<p>${esc(x)}</p>`).join('');
    return `<section class="nlib-clause" data-anchor-mark="${esc(s.anchor)}">
        <a class="nlib-anchor-link" href="${esc(s.source_url)}" target="_blank" rel="noopener"
           title="пункт ${esc(s.anchor)} на adilet.zan.kz">${esc(s.anchor)}</a>
        ${s.heading ? `<h4 class="nlib-h">${esc(s.heading)}</h4>` : ''}${body}
      </section>`;
  }).join('');
  const from = p.offset + 1, to = Math.min(p.offset + p.limit, p.total);
  $('normReader').innerHTML = `<div class="nlib-text">${html}</div>
    <div class="nlib-pager">
      ${p.prev_offset !== null ? `<button class="ghost" data-doc="${esc(docId)}" data-offset="${p.prev_offset}">‹ назад</button>` : '<span></span>'}
      <span class="dim">пункты ${from}–${to} из ${p.total}</span>
      ${p.next_offset !== null ? `<button class="ghost" data-doc="${esc(docId)}" data-offset="${p.next_offset}">дальше ›</button>` : '<span></span>'}
    </div>`;
}

async function runNormSearch(query) {
  const box = $('normResults');
  if (!query.trim()) { box.hidden = true; $('normEmpty').hidden = false; return; }
  $('normToc').hidden = true; $('normReader').hidden = true; $('normEmpty').hidden = true;
  box.hidden = false; box.innerHTML = '<span class="dim">ищу…</span>';
  try {
    const data = await normFetch('/api/norms/search', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, limit: 30, include_repealed: $('normInclRepealed').checked }),
    });
    box.innerHTML = data.results.length ? data.results.map(h => `
      <div class="nlib-hit" data-doc="${esc(h.doc_id)}" data-anchor="${esc(h.anchor)}">
        <div class="crumb">${esc(h.breadcrumb || '—')} · ${normBadge(h.doc_status)}</div>
        <div>${normSnippet(h.snippet)}</div>
      </div>`).join('')
      : '<span class="dim">Ничего не найдено. Пункт может быть в документе, которого нет в библиотеке.</span>';
  } catch (e) { box.innerHTML = `<span class="dim">${esc(e.message)}</span>`; }
}

// --- боковая панель читалки: открывается по клику на цитату в отчёте/нормоконтроле (§10.3) ---
async function openNormPanel(docId, anchor) {
  const panel = $('normPanel');
  panel.hidden = false;
  $('normPanelTitle').textContent = 'загружаю пункт…';
  $('normPanelBody').innerHTML = '';
  $('normPanelSource').href = `https://adilet.zan.kz/rus/docs/${encodeURIComponent(docId)}#${encodeURIComponent(anchor)}`;
  try {
    const data = await normFetch(`/api/norms/${encodeURIComponent(docId)}/sections/${encodeURIComponent(anchor)}`);
    const s = data.section, d = data.document;
    $('normPanelTitle').innerHTML = `${esc(s.heading || d.title)} ${normBadge(d.status)}`;
    $('normPanelSource').href = s.source_url;
    $('normPanelBody').innerHTML = `<div class="crumb">${esc(s.breadcrumb)}</div>${normNote(d)}
      <div class="nlib-body">${normBody(s)}</div>`;
  } catch (e) {
    $('normPanelTitle').textContent = 'Пункт недоступен';
    $('normPanelBody').innerHTML = `<span class="dim">${esc(e.message)}</span>`;
  }
}

// Цитата с doc_id+anchor становится ссылкой «открыть пункт»; без якоря рендерится как раньше (§9).
function citeOpenButton(c) {
  return (c && c.doc_id && c.anchor)
    ? ` <button class="nlib-cite-open" data-cite-doc="${esc(c.doc_id)}" data-cite-anchor="${esc(c.anchor)}">открыть пункт</button>`
    : '';
}

document.addEventListener('click', (e) => {
  const cite = e.target.closest('[data-cite-doc]');
  if (cite) { openNormPanel(cite.dataset.citeDoc, cite.dataset.citeAnchor); return; }
  const doc = e.target.closest('.nlib-doc');
  if (doc) { go('/norms/' + doc.dataset.doc); return; }
  const hit = e.target.closest('[data-anchor]');
  if (hit && hit.dataset.doc) { openNormDoc(hit.dataset.doc, { anchor: hit.dataset.anchor }); return; }
  const pager = e.target.closest('[data-offset]');
  if (pager) { openNormDoc(pager.dataset.doc, { offset: +pager.dataset.offset }); }
});
$('normPanelClose').addEventListener('click', () => { $('normPanel').hidden = true; });
$('btnNorms').addEventListener('click', () => go('/norms'));
$('normSearchInput').addEventListener('input', debounce((e) => runNormSearch(e.target.value), 300));
$('normInclRepealed').addEventListener('change', () => runNormSearch($('normSearchInput').value));
