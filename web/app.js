'use strict';
const $ = (id) => document.getElementById(id);
const API = '';                       // same-origin
let VIZ = null;                       // last /api/viz payload
let CIRCUITS = load('ec_circuits', []);
let ACTIVE = null;

const fmt = (x, d = 2) => (x === null || x === undefined) ? '—' :
  (Math.abs(x) >= 100 ? Math.round(x) : +(+x).toFixed(d)).toString().replace('.', ',');
function load(k, def) { try { return JSON.parse(localStorage.getItem(k)) ?? def; } catch { return def; } }
function save(k, v) { localStorage.setItem(k, JSON.stringify(v)); }

const DARK = {
  paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
  font: { color: '#c9d4e0', size: 12 }, margin: { l: 60, r: 18, t: 46, b: 70 },
  legend: { orientation: 'h', y: -0.22, yanchor: 'top', x: 0 }, showlegend: true,
};
const CFG = { responsive: true, displayModeBar: false };

// ---------- request building ----------
function buildRequest() {
  const srcType = $('f_srcType').value;
  const load = {
    description: $('f_desc').value || null,
    voltage_v: +$('f_voltage').value, phases: +$('f_phases').value,
    power_factor: +$('f_pf').value, purpose: $('f_purpose').value,
  };
  if (srcType === 'power') load.power_w = +$('f_power').value; else load.current_a = +$('f_current').value;
  const iscc = $('f_iscc').value ? +$('f_iscc').value : null;
  const vdl = $('f_vdlimit').value ? +$('f_vdlimit').value : null;
  return {
    load,
    installation: {
      method: $('f_method').value, material: $('f_material').value, insulation: $('f_insulation').value,
      ambient_temp_c: +$('f_ambient').value, grouping_circuits: +$('f_grouping').value, length_m: +$('f_length').value,
    },
    protection: {
      device_class: $('f_device').value, prospective_fault_current_a: iscc,
      disconnection_time_s: +$('f_tdisc').value, max_voltage_drop_pct: vdl,
      trip_curve_type: $('f_curve').value,
    },
    project_ref: $('projectName').value, designer: 'studio (черновик)',
  };
}
function fillForm(req) {
  const l = req.load, i = req.installation, p = req.protection || {};
  if (l.description != null) $('f_desc').value = l.description;
  if (l.power_w != null) { $('f_srcType').value = 'power'; $('f_power').value = l.power_w; }
  if (l.current_a != null) { $('f_srcType').value = 'current'; $('f_current').value = l.current_a; }
  toggleSrc();
  $('f_voltage').value = l.voltage_v; $('f_phases').value = l.phases;
  if (l.power_factor != null) $('f_pf').value = l.power_factor;
  if (l.purpose) $('f_purpose').value = l.purpose;
  $('f_method').value = i.method; if (i.material) $('f_material').value = i.material;
  if (i.insulation) $('f_insulation').value = i.insulation;
  if (i.ambient_temp_c != null) $('f_ambient').value = i.ambient_temp_c;
  if (i.grouping_circuits != null) $('f_grouping').value = i.grouping_circuits;
  $('f_length').value = i.length_m;
  if (p.device_class) $('f_device').value = p.device_class;
  if (p.trip_curve_type) $('f_curve').value = p.trip_curve_type;
  $('f_iscc').value = p.prospective_fault_current_a ?? '';
  if (p.disconnection_time_s != null) $('f_tdisc').value = p.disconnection_time_s;
  $('f_vdlimit').value = p.max_voltage_drop_pct ?? '';
  toggleCurve();
}

// ---------- recompute + render ----------
async function recompute() {
  const req = buildRequest();
  let v;
  try {
    const r = await fetch(API + '/api/viz', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(req) });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    v = await r.json();
  } catch (e) { setStatus('FAIL'); alert('Ошибка расчёта: ' + e.message); return; }
  VIZ = v; renderAll();
}
function renderAll() {
  const r = VIZ.result;
  setStatus(r.overall_status);
  renderSummary(r);
  renderTCC(VIZ.tcc); renderSweep(VIZ.sweep); renderVD(VIZ.vd_profile);
  renderDerating(VIZ.derating); renderSLD(VIZ.sld);
  renderTrace(r); relayoutActive();
  $('provenance').textContent = '⚠ ' + (VIZ.data_provenance_note || '');
  $('disclaimer').textContent = r.disclaimer;
}
function setStatus(s) {
  const b = $('statusBadge'); b.textContent = s;
  b.className = 'badge ' + ({ PASS: 'pass', FAIL: 'fail', NEEDS_REVIEW: 'review' }[s] || '');
}
function renderSummary(r) {
  const c = r.selected_cable, p = r.selected_protection;
  $('summary').innerHTML = [
    ['Кабель', `${fmt(c.cross_section_mm2)} мм² ${c.material}/${c.insulation}`],
    ['IZ', `${fmt(c.Iz_a)} A`], ['Аппарат', `${p.device_class} ${fmt(p.In_a)} A`],
    ['IB', `${fmt(r.design_current_a)} A`], ['ΔU', `${fmt(r.voltage_drop_pct)} %`],
    ['Связывает', c.governing_constraint],
  ].map(([k, val]) => `<div class="kv"><span>${k}:</span> <b>${val}</b></div>`).join('');
}

function vlines(shapes, x, color, dash, label, anns) {
  if (x == null) return;
  shapes.push({ type: 'line', x0: x, x1: x, yref: 'paper', y0: 0, y1: 1, line: { color, width: 1.5, dash } });
  anns.push({ x: Math.log10(x), y: 1, yref: 'paper', text: label, showarrow: false, font: { color, size: 11 }, xanchor: 'left', yanchor: 'bottom' });
}
function renderTCC(t) {
  if (!t) return;
  const dmax = t.device.max, dmin = t.device.min;
  const traces = [
    { x: t.cable.withstand.map(p => p[0]), y: t.cable.withstand.map(p => p[1]), name: `Кабель ${fmt(t.cable.section_mm2)} мм² (I²t)`, mode: 'lines', line: { color: '#f85149', width: 2.5 } },
    { x: dmax.map(p => p[0]), y: dmax.map(p => p[1]), name: 'Аппарат (медленно)', mode: 'lines', line: { color: '#4ea1ff', width: 1 }, showlegend: false },
    { x: dmin.map(p => p[0]), y: dmin.map(p => p[1]), name: `${t.device.class} ${t.device.class === 'gG_fuse' ? '' : t.device.curve_type} (полоса)`, mode: 'lines', line: { color: '#4ea1ff', width: 1 }, fill: 'tonexty', fillcolor: 'rgba(78,161,255,.18)' },
  ];
  const shapes = [], anns = [];
  vlines(shapes, t.markers.IB, '#8b98a9', 'dot', 'IB', anns);
  vlines(shapes, t.markers.In, '#d29922', 'dash', 'In', anns);
  vlines(shapes, t.markers.Iscc, '#f85149', 'dot', 'Iscc', anns);
  const coord = t.coordinated === null ? '' : (t.coordinated ? '  ✅ координировано' : '  ❌ нет координации');
  Plotly.react('plot_tcc', traces, Object.assign({}, DARK, {
    title: { text: 'Время-токовая координация' + coord, font: { size: 14 } },
    xaxis: { type: 'log', title: 'Ток, A', gridcolor: '#2b3444' },
    yaxis: { type: 'log', title: 'Время, с', gridcolor: '#2b3444' },
    shapes, annotations: anns,
  }), CFG);
}
function renderSweep(s) {
  if (!s) return;
  const x = s.rows.map(r => fmt(r.section_mm2)), y = s.rows.map(r => r.Iz_a);
  const colors = s.rows.map(r => r.section_mm2 === s.chosen_mm2 ? '#4ea1ff' : (r.amp_ok && r.vd_ok && r.sc_ok ? '#2b5f36' : '#3a3f4b'));
  Plotly.react('plot_sweep', [{ x, y, type: 'bar', marker: { color: colors }, name: 'IZ по сечению' }],
    Object.assign({}, DARK, {
      title: { text: `Свип сечения — выбрано ${fmt(s.chosen_mm2)} мм² (${s.governing})`, font: { size: 14 } },
      xaxis: { title: 'Сечение, мм²', type: 'category' }, yaxis: { title: 'IZ, A', gridcolor: '#2b3444' },
      shapes: [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: s.required_iz_a, y1: s.required_iz_a, line: { color: '#d29922', width: 1.5, dash: 'dash' } }],
      annotations: [{ xref: 'paper', x: 0.01, y: s.required_iz_a, text: `требуемый IZ ≥ ${fmt(s.required_iz_a)} A`, showarrow: false, font: { color: '#d29922', size: 11 }, yanchor: 'bottom' }],
    }), CFG);
}
function renderVD(v) {
  if (!v) return;
  Plotly.react('plot_vd', [
    { x: v.series.map(p => p[0]), y: v.series.map(p => p[1]), mode: 'lines', name: `ΔU при ${fmt(v.section_mm2)} мм²`, line: { color: '#4ea1ff', width: 2.5 } },
    { x: [v.current_length_m], y: [v.current_pct], mode: 'markers', name: 'текущая длина', marker: { color: '#e6edf3', size: 9 } },
  ], Object.assign({}, DARK, {
    title: { text: 'Профиль падения напряжения', font: { size: 14 } },
    xaxis: { title: 'Длина, м', gridcolor: '#2b3444' }, yaxis: { title: 'ΔU, %', gridcolor: '#2b3444' },
    shapes: [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: v.limit_pct, y1: v.limit_pct, line: { color: '#f85149', width: 1.5, dash: 'dash' } }],
    annotations: [{ xref: 'paper', x: 0.01, y: v.limit_pct, text: `предел ${fmt(v.limit_pct)} %`, showarrow: false, font: { color: '#f85149', size: 11 }, yanchor: 'bottom' }],
  }), CFG);
}
function renderDerating(d) {
  if (!d) return;
  Plotly.react('plot_derating', [{
    x: d.stages.map(s => s.label), y: d.stages.map(s => s.value), type: 'bar',
    marker: { color: ['#4ea1ff', '#3d6ea5', '#2b5f36'] }, text: d.stages.map(s => fmt(s.value)), textposition: 'outside',
  }], Object.assign({}, DARK, {
    title: { text: `Дерейтинг: It → IZ (нужно ≥ ${fmt(d.required_iz_a)} A)`, font: { size: 14 } },
    xaxis: { title: '' }, yaxis: { title: 'A', gridcolor: '#2b3444' }, showlegend: false,
    shapes: [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: d.required_iz_a, y1: d.required_iz_a, line: { color: '#d29922', width: 1.5, dash: 'dash' } }],
  }), CFG);
}
function renderSLD(s) {
  if (!s) return;
  const col = { PASS: '#3fb950', FAIL: '#f85149', NEEDS_REVIEW: '#d29922' }[s.status] || '#8b98a9';
  const bw = 150, gap = 46, y = 70, h = 78;
  let x = 20, svg = `<svg viewBox="0 0 ${20 + (bw + gap) * 4} 200" class="sld" style="max-width:100%">`;
  s.nodes.forEach((n, idx) => {
    if (idx > 0) svg += `<line x1="${x - gap}" y1="${y + h / 2}" x2="${x}" y2="${y + h / 2}" stroke="#4ea1ff" stroke-width="2"/>`;
    svg += `<rect x="${x}" y="${y}" width="${bw}" height="${h}" rx="9" fill="#1c2330" stroke="${idx === s.nodes.length - 1 ? col : '#2b3444'}" stroke-width="2"/>`;
    svg += `<text x="${x + bw / 2}" y="${y + 30}" fill="#e6edf3" font-size="14" font-weight="600" text-anchor="middle">${esc(n.label)}</text>`;
    svg += `<text x="${x + bw / 2}" y="${y + 52}" fill="#8b98a9" font-size="12" text-anchor="middle" font-family="monospace">${esc(n.sub)}</text>`;
    x += bw + gap;
  });
  svg += `<text x="20" y="30" fill="${col}" font-size="15" font-weight="700">${s.status} · связывает: ${s.governing}</text></svg>`;
  $('plot_sld').innerHTML = svg;
}
function renderTrace(r) {
  const dot = (st) => `<span class="st-dot st-${st}"></span>`;
  $('trace').innerHTML = r.audit_trace.map(s => {
    const cites = (s.citations || []).map(c => `${c.standard}${c.clause ? ' §' + c.clause : ''}${c.table ? ' Табл.' + c.table : ''}`).join('; ');
    return `<details class="step"><summary>${dot(s.status)}${esc(s.title)}</summary>
      <div class="body">${s.formula ? `<div class="f">${esc(s.formula)}</div>` : ''}
      ${s.computation ? `<div class="comp">${esc(s.computation)}</div>` : ''}
      ${cites ? `<div class="cite">↳ ${esc(cites)}</div>` : ''}</div></details>`;
  }).join('');
}
const esc = (s) => String(s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

// ---------- tabs ----------
function relayoutActive() {
  const id = document.querySelector('.tabs button.active').dataset.tab;
  if (['tcc', 'sweep', 'vd', 'derating'].includes(id)) Plotly.Plots.resize($('plot_' + id));
}
$('tabs').addEventListener('click', e => {
  if (e.target.tagName !== 'BUTTON') return;
  document.querySelectorAll('.tabs button').forEach(b => b.classList.remove('active'));
  e.target.classList.add('active');
  const id = e.target.dataset.tab;
  document.querySelectorAll('.tabpane').forEach(p => p.style.display = 'none');
  $('plot_' + id).style.display = 'block';
  if (['tcc', 'sweep', 'vd', 'derating'].includes(id)) Plotly.Plots.resize($('plot_' + id));
});

// ---------- copilot ----------
function addMsg(cls, html, sub) {
  const d = document.createElement('div'); d.className = 'msg ' + cls;
  d.innerHTML = html + (sub ? `<small>${esc(sub)}</small>` : '');
  $('chat').appendChild(d); $('chat').scrollTop = $('chat').scrollHeight;
}
async function chatSend() {
  const text = $('chatInput').value.trim(); if (!text) return;
  addMsg('user', esc(text)); $('chatInput').value = '';
  addMsg('bot', '⏳ разбираю…');
  const busy = $('chat').lastChild;
  try {
    const r = await fetch(API + '/api/intake', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
    const j = await r.json();
    busy.remove();
    if (!j.ok) { addMsg('bot', '⚠ ' + esc(j.message || 'не удалось разобрать')); return; }
    fillForm(j.request); await recompute();
    const c = VIZ.result.selected_cable, p = VIZ.result.selected_protection;
    addMsg('bot', `Разобрал и посчитал: <b>${fmt(c.cross_section_mm2)} мм²</b>, аппарат <b>${p.device_class} ${fmt(p.In_a)} A</b>, статус <b>${VIZ.result.overall_status}</b>.`, 'модель: ' + (j.model || ''));
  } catch (e) { busy.remove(); addMsg('bot', '⚠ ошибка: ' + esc(e.message)); }
}
async function doExplain() {
  addMsg('bot', '⏳ формирую объяснение…'); const busy = $('chat').lastChild;
  try {
    const r = await fetch(API + '/api/explain', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(buildRequest()) });
    const j = await r.json(); busy.remove();
    const n = j.narrative;
    const tag = n.model ? `${n.model}; провенанс ${n.provenance_ok ? 'OK' : 'FAIL ' + JSON.stringify(n.unverified_numbers)}` : 'шаблон (без ключа)';
    addMsg('bot', esc(n.text), tag);
  } catch (e) { busy.remove(); addMsg('bot', '⚠ ' + esc(e.message)); }
}
async function doReview() {
  addMsg('bot', '⏳ ревьюер проверяет…'); const busy = $('chat').lastChild;
  try {
    const r = await fetch(API + '/api/verify', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(buildRequest()) });
    const j = await r.json(); busy.remove();
    const v = j.verdict;
    const bad = !v.agrees || !v.deterministic_ok;
    const issues = (v.issues || []).length ? '<br>' + v.issues.map(esc).join('<br>') : '';
    addMsg('rev' + (bad ? ' bad' : ''), `Ревьюер: детерм. проверка ${v.deterministic_ok ? '✅' : '❌'}, LLM ${v.agrees ? 'согласен ✅' : 'НЕ согласен ❌'}${issues}`, v.model ? 'модель: ' + v.model : 'детерминированно');
  } catch (e) { busy.remove(); addMsg('bot', '⚠ ' + esc(e.message)); }
}

// ---------- project circuits ----------
function renderCircuits() {
  $('circuitList').innerHTML = CIRCUITS.map((c, i) => {
    const st = c.status || '—', cls = { PASS: 'pass', FAIL: 'fail', NEEDS_REVIEW: 'review' }[st] || '';
    return `<li class="${i === ACTIVE ? 'active' : ''}" data-i="${i}">
      <div><div class="c-name">${esc(c.req.load.description || 'Цепь ' + (i + 1))}</div>
      <div class="c-sub">${c.summary || ''}</div></div>
      <div style="text-align:right"><div class="c-badge ${cls}">${st}</div>
      <button class="del" data-del="${i}">✕</button></div></li>`;
  }).join('') || '<li style="color:var(--dim);border:none;background:none">пусто — «Сохранить в проект»</li>';
}
function saveCircuit() {
  if (!VIZ) return;
  const c = VIZ.result.selected_cable, p = VIZ.result.selected_protection;
  CIRCUITS.push({
    req: buildRequest(), status: VIZ.result.overall_status,
    summary: `${fmt(c.cross_section_mm2)}мм² · ${p.device_class} ${fmt(p.In_a)}A · IB ${fmt(VIZ.result.design_current_a)}A`,
  });
  ACTIVE = CIRCUITS.length - 1; save('ec_circuits', CIRCUITS); renderCircuits();
}
$('circuitList').addEventListener('click', e => {
  const del = e.target.dataset.del;
  if (del != null) { CIRCUITS.splice(+del, 1); ACTIVE = null; save('ec_circuits', CIRCUITS); renderCircuits(); return; }
  const li = e.target.closest('li'); if (!li || li.dataset.i == null) return;
  ACTIVE = +li.dataset.i; fillForm(CIRCUITS[ACTIVE].req); renderCircuits(); recompute();
});

// ---------- export ----------
function exportReport() {
  if (!VIZ) return;
  const r = VIZ.result, c = r.selected_cable, p = r.selected_protection;
  const cite = s => s.citations.map(x => `${x.standard}${x.clause ? ' §' + x.clause : ''}`).join('; ');
  let md = `# ElectriCopilot — отчёт: ${$('projectName').value}\n\n> ${r.disclaimer}\n\n`;
  md += `**Статус:** ${r.overall_status} · связывает: ${c.governing_constraint}\n\n`;
  md += `## Результат\n- Кабель: ${fmt(c.cross_section_mm2)} мм² ${c.material}/${c.insulation}, IZ=${fmt(c.Iz_a)} A\n`;
  md += `- Аппарат: ${p.device_class} ${fmt(p.In_a)} A (I2/In=${fmt(p.I2_over_In)})\n`;
  md += `- IB=${fmt(r.design_current_a)} A · ΔU=${fmt(r.voltage_drop_pct)}% (предел ${fmt(r.voltage_drop_limit_pct)}%)\n\n`;
  md += `## Проверки\n${r.checks.map(ch => `- ${ch.passed ? '✅' : '❌'} ${ch.name}: ${ch.detail} [${cite(ch)}]`).join('\n')}\n\n`;
  md += `## Трасса\n${r.audit_trace.map(s => `### ${s.title}\n${s.formula ? '`' + s.formula + '`\n' : ''}${s.computation || ''}\n↳ ${cite(s)}`).join('\n\n')}\n\n`;
  md += `## Провенанс\n> ${VIZ.data_provenance_note}\n\n## Подпись\n- НЕ ПОДПИСАНО (UNSIGNED_ADVISORY) — требуется подпись инженера.\n`;
  const blob = new Blob([md], { type: 'text/markdown' });
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
  a.download = `${$('projectName').value.replace(/\s+/g, '_')}_${c.cross_section_mm2}mm2.md`; a.click();
}

// ---------- toggles + wiring ----------
function toggleSrc() {
  const pw = $('f_srcType').value === 'power';
  $('wrap_power').style.display = pw ? '' : 'none'; $('wrap_current').style.display = pw ? 'none' : '';
}
function toggleCurve() { $('wrap_curve').style.display = $('f_device').value === 'gG_fuse' ? 'none' : ''; }
$('f_srcType').addEventListener('change', toggleSrc);
$('f_device').addEventListener('change', toggleCurve);
$('recalc').addEventListener('click', recompute);
$('saveCircuit').addEventListener('click', saveCircuit);
$('exportReport').addEventListener('click', exportReport);
$('addCircuit').addEventListener('click', () => { ACTIVE = null; renderCircuits(); });
$('chatSend').addEventListener('click', chatSend);
$('chatInput').addEventListener('keydown', e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) chatSend(); });
$('btnExplain').addEventListener('click', doExplain);
$('btnReview').addEventListener('click', doReview);
document.querySelectorAll('#f_desc,#f_power,#f_current,#f_voltage,#f_phases,#f_pf,#f_purpose,#f_method,#f_material,#f_insulation,#f_ambient,#f_grouping,#f_length,#f_device,#f_curve,#f_iscc,#f_tdisc,#f_vdlimit')
  .forEach(el => el.addEventListener('change', recompute));

async function boot() {
  toggleSrc(); toggleCurve(); renderCircuits();
  try { const h = await (await fetch(API + '/api/health')).json(); $('modeTag').textContent = `${h.mode} · ${h.model_fast || ''}`; }
  catch { $('modeTag').textContent = 'offline'; }
  addMsg('bot', 'Привет! Опиши цепь словами или задай параметры слева — соберу расчёт с трассой до норм, интерактивные графики и проверку ревьюером.');
  recompute();
}
boot();
