'use strict';
/* ElectriCopilot Studio — UX flows prototype (docs/18).
   Clickable prototype over hardcoded ВРУ-1 mock. No API calls; numbers are
   illustrative and the "live recalc" is a deterministic client mock. The
   production app (index.html/app.js) is untouched. */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmt = (x, d = 2) => (x === null || x === undefined || x === '') ? '—'
  : (Math.abs(x) >= 100 ? Math.round(x) : +(+x).toFixed(d)).toString().replace('.', ',');

// ---------- display dictionary (docs/17): Russian labels + technical code in title ----------
const ST_LABELS = { PASS: 'Соответствует', FAIL: 'Не проходит', NEEDS_REVIEW: 'Требует проверки', UNSIGNED_ADVISORY: 'Не подписано · рекомендательно', SIGNED: 'Подписано', VERIFIED: 'Проверено', 'READ-ONLY': 'Только просмотр' };
const GOV_LABELS = { overload_coordination: 'координация по перегрузке', short_circuit: 'термическая стойкость к КЗ', voltage_drop: 'падение напряжения' };
const DEV_LABELS = { MCB: 'Автомат (MCB)', MCCB: 'Автомат в литом корпусе (MCCB)', gG_fuse: 'Предохранитель gG' };
const PURPOSE_LABELS = { lighting: 'освещение', power: 'силовая', socket: 'розетки', motor: 'двигатель', general: 'прочее' };
const SEV_LABELS = { error: 'ошибка', warning: 'предупреждение', info: 'инфо' };
const VERDICT_WORD = { PASS: 'проходит', FAIL: 'не проходит', NEEDS_REVIEW: 'требует проверки' };
const PACK_NAME = 'iec-stub', PACK_LABEL = 'IEC 60364 (демо-данные)';
const stLabel = (s) => ST_LABELS[s] ?? (s ?? '');
const govLabel = (g) => GOV_LABELS[g] ?? (g ?? '');
const devLabel = (d) => DEV_LABELS[d] ?? (d ?? '');
const sevLabel = (s) => SEV_LABELS[s] ?? (s ?? '');
const deviceText = (s) => String(s ?? '').replace(/gG_fuse/g, 'Предохранитель gG');
const chip = (text, code, cls) => `<span class="badge small ${cls || ''}"${code ? ` title="${esc(code)}"` : ''}>${esc(text)}</span>`;

// ---------- hardcoded mock (ВРУ-1) ----------
const SUPPLY = { voltage_v: 400, phases: 3, earthing: 'TN-C-S', incomer_a: 33, ways_used: 4, ways_total: 12, spare: 8 };
const CIRCUITS = [
  { id: 'L1', ref: 'L1', desc: 'Розетки кухни', P: 3680, U: 230, ph: 1, pf: 0.95, purpose: 'socket', method: 'C', material: 'Cu', insulation: 'PVC', ambient: 30, grouping: 1, length: 18, device: 'MCB', curve: 'C', In: 20, iscc: 1500, tdisc: 0.01, vdlimit: 5, rcd: { present: true, type: 'RCBO', ma: 30 }, phase: 'L1', section: 2.5, IZ: 20, IB: 16.84, vd: 2.26, status: 'PASS', governing: 'overload_coordination' },
  { id: 'L2', ref: 'L2', desc: 'Освещение', P: 1200, U: 230, ph: 1, pf: 1.0, purpose: 'lighting', method: 'C', material: 'Cu', insulation: 'PVC', ambient: 30, grouping: 1, length: 25, device: 'MCB', curve: 'B', In: 6, iscc: 800, tdisc: 0.01, vdlimit: 5, rcd: { present: false }, phase: 'L2', section: 1.5, IZ: 15, IB: 5.22, vd: 1.7, status: 'PASS', governing: 'voltage_drop' },
  { id: 'L3', ref: 'L3', desc: 'Бойлер', P: 3000, U: 230, ph: 1, pf: 1.0, purpose: 'power', method: 'C', material: 'Cu', insulation: 'PVC', ambient: 30, grouping: 1, length: 20, device: 'MCB', curve: 'C', In: 20, iscc: 1500, tdisc: 0.01, vdlimit: 5, rcd: { present: true, type: 'RCBO', ma: 30 }, phase: 'L3', section: 2.5, IZ: 20, IB: 13.04, vd: 2.04, status: 'PASS', governing: 'overload_coordination' },
  { id: 'M1', ref: 'M1', desc: 'Двигатель 15 кВт', P: 15000, U: 400, ph: 3, pf: 0.85, purpose: 'motor', method: 'C', material: 'Cu', insulation: 'XLPE', ambient: 40, grouping: 3, length: 50, device: 'gG_fuse', curve: null, In: 32, iscc: 4000, tdisc: 0.01, vdlimit: 5, rcd: { present: false }, phase: 'L1L2L3', section: 10, IZ: 44.5, IB: 25.47, vd: 1.08, status: 'PASS', governing: 'overload_coordination' },
];
// mock normcheck findings (revealed on demand); both point at L2 → "открой L2"
const FINDINGS = [
  { rule: 'R04', circuit: 'L2', severity: 'warning', title: 'ΔU близко к пределу', detail: 'Падение напряжения 1,7 % на длине 25 м; при добавлении светильников запас до предела 5 % быстро исчерпается.', cite: 'IEC 60364-5-52 · §525' },
  { rule: 'R07', circuit: 'L2', severity: 'info', title: 'Запас по сечению на минимуме', detail: 'Сечение 1,5 мм² покрывает ток с минимальным запасом; при росте нагрузки потребуется 2,5 мм².', cite: 'IEC 60364-5-52 · табл. B.52' },
];

const STATE = { checked: false, signed: new Set(), activeId: 'L1' };
const byId = (id) => CIRCUITS.find(c => c.id === id);
const deviceStr = (c) => deviceText(`${c.device} ${fmt(c.In, 0)}A${c.curve ? ' ' + c.curve : ''}`);
const cableStr = (c) => `${fmt(c.section)} мм² ${c.material}/${c.insulation}`;

// ---------- deterministic live-recalc mock (L1 only; others shown as stored) ----------
const SECTION_IZ = [[1.5, 15], [2.5, 20], [4, 27], [6, 34], [10, 44.5]];
function calc(f) {
  const IB = f.ph === 3 ? f.P / (Math.sqrt(3) * f.U * f.pf) : f.P / (f.U * f.pf);
  let section = null, IZ = null;
  for (const [s, iz] of SECTION_IZ) { if (iz >= IB) { section = s; IZ = iz; break; } }
  if (!section || !isFinite(IB) || IB <= 0) return { ok: false, IB };
  const K = f.ph === 3 ? 0.00848 : 0.01864;   // calibrated: L1 → 2,26 %, M1 → 1,08 %
  const vd = K * f.length * IB / section;
  const limit = f.vdlimit || 5;
  const izMargin = (IZ - IB) / IB * 100;
  const vdMargin = (limit - vd) / limit;
  let status = 'PASS';
  if (vd > limit) status = 'FAIL';
  else if (vd > limit * 0.9 || izMargin < 8) status = 'NEEDS_REVIEW';
  const governing = vdMargin < izMargin / 100 ? 'voltage_drop' : 'overload_coordination';
  return { ok: true, IB, section, IZ, vd, limit, izMargin, governing, status };
}

// ---------- toasts (bottom-right, auto-hide 4s) ----------
function toast(msg) {
  const el = document.createElement('div'); el.className = 'p-toast'; el.textContent = msg;
  $('pToasts').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 250); }, 4000);
}

// ========================= BOARD =========================
function normCardChip() {
  if (!STATE.checked) return chip('не запускался', '', '');
  return chip(`${FINDINGS.length} замечания`, 'NEEDS_REVIEW', 'review');
}
function renderBoardCard() {
  $('pBoardCard').innerHTML = `
    <h2>Щит ВРУ-1 (пример)</h2>
    <div class="p-sub">DB-1 · Эл.щитовая</div>
    <div class="p-stats">
      <div class="p-stat"><span>Ввод</span><b>${SUPPLY.voltage_v} В · ${SUPPLY.phases}ф · ${SUPPLY.earthing}</b></div>
      <div class="p-stat"><span>Ток ввода (расч.)</span><b>≈ ${SUPPLY.incomer_a} A</b></div>
      <div class="p-stat"><span>Резерв мест</span><b>${SUPPLY.spare} / ${SUPPLY.ways_total}</b></div>
      <div class="p-stat"><span>Нормоконтроль</span>${normCardChip()}</div>
      <div class="p-stat"><span>Подпись цепей</span><b>Подписано ${STATE.signed.size} из ${CIRCUITS.length}</b></div>
    </div>`;
}
function reserveBar(pct, cls) {
  const w = Math.max(4, Math.min(100, pct));
  return `<div class="rbar ${cls}"><i style="width:${w}%"></i></div>`;
}
function renderTable() {
  const flagged = new Set(STATE.checked ? FINDINGS.map(f => f.circuit) : []);
  $('pTableBody').innerHTML = CIRCUITS.map(c => {
    const izMargin = (c.IZ - c.IB) / c.IB * 100;
    const vdPct = c.vd / c.vdlimit * 100;
    const mark = flagged.has(c.id) ? `<span class="p-rowmark warning" title="Есть замечания нормоконтроля">!</span>` : '';
    const statusCell = c.status === 'PASS'
      ? '<span class="p-cellsub">—</span>'   // норма — тишина
      : `<span class="st-cell ${c.status}" title="${esc(c.status)}">${esc(stLabel(c.status))}</span>`;
    return `<tr data-cid="${c.id}"${flagged.has(c.id) ? ' class="p-flagged"' : ''}>
      <td><span class="p-cellmain">${esc(c.ref)}</span>${mark}<div class="p-cellsub">${esc(c.phase)}</div></td>
      <td>${esc(c.desc)}<div class="p-cellsub">${esc(PURPOSE_LABELS[c.purpose] || c.purpose)}</div></td>
      <td title="${esc(deviceStr(c))}">${esc(deviceStr(c))}</td>
      <td>${esc(cableStr(c))}</td>
      <td><span class="p-cellmain">${fmt(c.IZ, 0)} A</span><div class="p-cellsub">запас +${fmt(izMargin, 0)} % к IB</div>${reserveBar(Math.min(izMargin, 100), izMargin < 10 ? 'warn' : 'ok')}</td>
      <td><span class="p-cellmain">${fmt(c.vd)} % из ${fmt(c.vdlimit, 0)}</span><div class="p-cellsub">заполнение предела</div>${reserveBar(vdPct, vdPct > 90 ? 'bad' : (vdPct > 70 ? 'warn' : 'acc'))}</td>
      <td>${statusCell}</td>
    </tr>`;
  }).join('');
}
function renderNextStep() {
  let title = 'Следующий шаг', text, btnLabel, btnFn;
  if (!STATE.checked) {
    text = 'Запусти нормоконтроль всего щита — цепи проверятся по пунктам норм.';
    btnLabel = 'Нормоконтроль'; btnFn = runNormcheck;
  } else if (FINDINGS.length) {
    text = `${FINDINGS.length} замечания · начни с цепи <b>L2</b>: открой её и проверь параметры.`;
    btnLabel = 'Открыть L2 →'; btnFn = () => openCircuit('L2');
  } else if (STATE.signed.size < CIRCUITS.length) {
    text = 'Все проверки пройдены — подпиши цепи, затем щит.';
    btnLabel = 'Подписать цепи'; btnFn = () => openCircuit('L1');
  } else {
    title = 'Готово'; text = 'Все цепи подписаны — сформируй пакет документов.';
    btnLabel = 'Пакет документов'; btnFn = () => toast('Пакет документов — в прототипе не формируется');
  }
  $('pNext').innerHTML = `
    <div class="p-next-h"><svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></svg>${title}</div>
    <p>${text}</p>
    <button class="primary" id="pNextBtn">${esc(btnLabel)}</button>
    <div class="p-docgroup">
      <div class="p-next-h"><svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2Z"/></svg>Документы</div>
      <div class="p-docbtns">
        <button class="ghost" data-doc="Пакет документов"><svg class="icon icon-sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="M3.3 7 12 12l8.7-5"/><path d="M12 22V12"/></svg>Пакет документов</button>
        <button class="ghost" data-doc="Печать"><svg class="icon icon-sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><path d="M6 9V3a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v6"/><rect x="6" y="14" width="12" height="8" rx="1"/></svg>Печать</button>
        <button class="ghost" data-doc="Share-ссылка"><svg class="icon icon-sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 17H7A5 5 0 0 1 7 7h2"/><path d="M15 7h2a5 5 0 1 1 0 10h-2"/><line x1="8" y1="12" x2="16" y2="12"/></svg>Share-ссылка</button>
        <button class="ghost" data-doc="Файл проекта"><svg class="icon icon-sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2Z"/></svg>Файл проекта</button>
      </div>
    </div>`;
  $('pNextBtn').addEventListener('click', btnFn);
  $('pNext').querySelectorAll('[data-doc]').forEach(b => b.addEventListener('click', () => toast(`${b.dataset.doc} — в прототипе не формируется`)));
}
function renderNorm() {
  const sum = $('pNormSummary');
  if (!STATE.checked) {
    sum.className = 'badge small'; sum.title = ''; sum.textContent = 'не запускался';
    $('pNormBody').innerHTML = `<div class="p-empty">Запусти нормоконтроль в панели «Следующий шаг» — замечания появятся здесь и отметят цепи в таблице.</div>`;
    return;
  }
  sum.className = 'badge small review'; sum.title = 'NEEDS_REVIEW'; sum.textContent = `${FINDINGS.length} замечания · 0 ошибок`;
  $('pNormBody').innerHTML = FINDINGS.map(f => `
    <div class="p-finding ${esc(f.severity)}">
      <span class="p-fmeta" title="${esc(f.severity)}">${esc(f.rule)} · ${esc(f.circuit)} · ${esc(sevLabel(f.severity))}</span>
      <span class="p-ftxt">${esc(f.title)}<small>${esc(f.detail)} · ${esc(f.cite)}</small></span>
      <button class="ghost mini" data-open="${esc(f.circuit)}">Открыть цепь →</button>
    </div>`).join('');
  $('pNormBody').querySelectorAll('[data-open]').forEach(b => b.addEventListener('click', () => openCircuit(b.dataset.open)));
}
function runNormcheck() {
  STATE.checked = true;
  renderBoardCard(); renderTable(); renderNorm(); renderNextStep();
  toast(`Нормоконтроль выполнен (мок): ${FINDINGS.length} замечания`);
}
function renderSldBoard() {
  const nodes = [{ label: 'Ввод', sub: `${SUPPLY.voltage_v} В · ${SUPPLY.phases}ф` }, { label: 'ВРУ-1', sub: 'DB-1' }]
    .concat(CIRCUITS.map(c => ({ label: c.ref, sub: deviceStr(c) })));
  $('pSld').innerHTML = sldSvg(nodes, 'PASS', 'координация по перегрузке');
}
function sldSvg(nodes, status, govText) {
  const col = { PASS: '#15803D', FAIL: '#DC2626', NEEDS_REVIEW: '#B45309' }[status] || '#5B6B8C';
  const bw = 132, gap = 40, y = 60, h = 72; let x = 20;
  let svg = `<svg viewBox="0 0 ${20 + (bw + gap) * nodes.length} 180" style="max-width:100%">`;
  nodes.forEach((n, i) => {
    if (i > 0) svg += `<line x1="${x - gap}" y1="${y + h / 2}" x2="${x}" y2="${y + h / 2}" stroke="#1E40AF" stroke-width="2"/>`;
    svg += `<rect x="${x}" y="${y}" width="${bw}" height="${h}" rx="9" fill="#FFFFFF" stroke="${i === nodes.length - 1 ? col : '#BFD3F2'}" stroke-width="2"/>`;
    svg += `<text x="${x + bw / 2}" y="${y + 30}" fill="#0F1B33" font-size="13" font-weight="600" text-anchor="middle">${esc(n.label)}</text>`;
    svg += `<text x="${x + bw / 2}" y="${y + 50}" fill="#42557A" font-size="11" text-anchor="middle" font-family="monospace">${esc(n.sub)}</text>`;
    x += bw + gap;
  });
  svg += `<text x="20" y="28" fill="${col}" font-size="14" font-weight="700">${esc(stLabel(status))} · определяет: ${esc(govText)}</text></svg>`;
  return svg;
}

// ========================= CIRCUIT =========================
function activeCircuit() { return byId(STATE.activeId); }
function showScreen(which) {
  $('pScreenBoard').classList.toggle('hidden', which !== 'board');
  $('pScreenCircuit').classList.toggle('hidden', which !== 'circuit');
}
function openCircuit(id) { STATE.activeId = id; showScreen('circuit'); renderCircuit(); window.scrollTo(0, 0); }
function backToBoard() { showScreen('board'); renderBoardCard(); renderTable(); }

function fieldset(legend, rows) { return `<fieldset class="fgroup"><legend>${legend}</legend>${rows}</fieldset>`; }
function renderForm(c) {
  const opt = (map, val) => Object.entries(map).map(([k, v]) => `<option value="${k}"${k === val ? ' selected' : ''}>${esc(v)}</option>`).join('');
  const methods = ['C', 'B1', 'A1', 'A2', 'B2', 'D', 'E', 'F', 'G'].map(m => `<option${m === c.method ? ' selected' : ''}>${m}</option>`).join('');
  $('pForm').innerHTML =
    fieldset('Нагрузка', `
      <div class="row"><label>Ref <input id="p_ref" value="${esc(c.ref)}"></label><label>Название <input id="p_desc" value="${esc(c.desc)}"></label></div>
      <div class="row"><label>P, Вт <input id="p_power" type="number" value="${c.P}"></label><label>U, В <input id="p_voltage" type="number" value="${c.U}"></label></div>
      <div class="row"><label>Фаз <select id="p_phases"><option${c.ph === 1 ? ' selected' : ''}>1</option><option${c.ph === 3 ? ' selected' : ''}>3</option></select></label><label>cosφ <input id="p_pf" type="number" step="0.01" value="${c.pf}"></label></div>
      <div class="row"><label>Назначение <select id="p_purpose">${opt(PURPOSE_LABELS, c.purpose)}</select></label></div>`) +
    fieldset('Кабель и прокладка', `
      <div class="row"><label>Метод <select id="p_method">${methods}</select></label><label>Жила <select id="p_material"><option${c.material === 'Cu' ? ' selected' : ''}>Cu</option><option${c.material === 'Al' ? ' selected' : ''}>Al</option></select></label><label>Изоляция <select id="p_insulation"><option${c.insulation === 'PVC' ? ' selected' : ''}>PVC</option><option${c.insulation === 'XLPE' ? ' selected' : ''}>XLPE</option></select></label></div>
      <div class="row"><label>t окр., °C <input id="p_ambient" type="number" value="${c.ambient}"></label><label>Групп. <input id="p_grouping" type="number" value="${c.grouping}"></label><label>Длина, м <input id="p_length" type="number" value="${c.length}"></label></div>
      <div class="row"><label>Предел ΔU,% <input id="p_vdlimit" type="number" step="0.1" value="${c.vdlimit}"></label></div>`) +
    fieldset('Защита', `
      <div class="row"><label>Аппарат <select id="p_device">${opt(DEV_LABELS, c.device)}</select></label><label>Характеристика <select id="p_curve"><option${c.curve === 'B' ? ' selected' : ''}>B</option><option${c.curve === 'C' ? ' selected' : ''}>C</option><option${c.curve === 'D' ? ' selected' : ''}>D</option></select></label></div>
      <div class="row"><label>УЗО <select id="p_rcd"><option value=""${!c.rcd.present ? ' selected' : ''}>нет</option><option${c.rcd.type === 'RCBO' ? ' selected' : ''}>RCBO</option><option${c.rcd.type === 'RCD' ? ' selected' : ''}>RCD</option></select></label><label>IΔn, мА <input id="p_rcd_ma" type="number" value="${c.rcd.ma || 30}"></label></div>`) +
    fieldset('Короткое замыкание', `
      <div class="row"><label>I КЗ, А <input id="p_iscc" type="number" value="${c.iscc}"></label><label>t КЗ, с <input id="p_tdisc" type="number" step="0.01" value="${c.tdisc}"></label></div>`);
  $('pForm').querySelectorAll('input,select').forEach(el => el.addEventListener('input', recompute));
}
function currentForm() {
  return {
    P: +$('p_power').value, U: +$('p_voltage').value, ph: +$('p_phases').value, pf: +$('p_pf').value,
    length: +$('p_length').value, vdlimit: +$('p_vdlimit').value,
    material: $('p_material').value, insulation: $('p_insulation').value, device: $('p_device').value, curve: $('p_curve').value,
  };
}
function verdictData(c) {
  if (c.id === 'L1') { const r = calc(currentForm()); r.live = true; r.material = $('p_material').value; r.insulation = $('p_insulation').value; return r; }
  return { ok: true, section: c.section, IZ: c.IZ, vd: c.vd, IB: c.IB, limit: c.vdlimit, izMargin: (c.IZ - c.IB) / c.IB * 100, governing: c.governing, status: c.status, material: c.material, insulation: c.insulation, static: true };
}
function renderVerdict() {
  const c = activeCircuit(), r = verdictData(c);
  const box = $('pVerdict');
  if (!r.ok) {
    box.className = 'p-verdict NEEDS_REVIEW';
    $('pVerdictBody').innerHTML = `<div class="p-vhead"><span class="p-vword">Требует проверки</span></div>
      <div class="p-vstub">Ток нагрузки вне мок-таблицы сечений — в прототипе не считается (доступен диапазон до 10 мм² / 44,5 A).</div>`;
    return;
  }
  box.className = 'p-verdict ' + r.status;
  const stub = r.static ? `<div class="p-vstub">Цепь показана в режиме просмотра · живой пересчёт в прототипе доступен для цепи L1.</div>` : '';
  $('pVerdictBody').innerHTML = `
    <div class="p-vhead">
      <span class="p-vsize">${fmt(r.section)} мм² ${esc(r.material)}/${esc(r.insulation)}</span>
      <span class="p-vword">— ${esc(VERDICT_WORD[r.status] || stLabel(r.status))}</span>
    </div>
    <div class="p-vmeta">
      <span>запас по току <b>+${fmt(r.izMargin, 0)} %</b></span>
      <span>IZ <b>${fmt(r.IZ, 0)} A</b> · IB <b>${fmt(r.IB, 1)} A</b></span>
      <span>ΔU <b>${fmt(r.vd)} % из ${fmt(r.limit, 0)}</b></span>
    </div>
    <div class="p-vgov" title="${esc(r.governing)}">определяет: <b>${esc(govLabel(r.governing))}</b></div>${stub}`;
}
function renderReason() {
  const steps = [
    ['Расчётный ток IB', 'IB = P / (U · cosφ) = 3680 / (230 · 0,95) = 16,84 A', 'IEC 60364-5-52'],
    ['Номинал аппарата защиты In', 'IB ≤ In ≤ IZ:  16,84 ≤ 20 ≤ 20 A', 'IEC 60364-4-43 · §433'],
    ['Пропускная способность IZ = It · ka · kg', 'IZ = 20 A ≥ IB = 16,84 A', 'IEC 60364-5-52 · §523'],
    ['Падение напряжения ΔU', 'ΔU = 2,26 % ≤ предел 5 %', 'IEC 60364-5-52 · §525'],
    ['Термическая стойкость к КЗ', 'S ≥ √(I²t) / k — выполняется', 'IEC 60364-4-43 · §434'],
  ];
  $('pReason').innerHTML = activeCircuit().id === 'L1'
    ? steps.map(([t, f, cite]) => `<details class="p-step"><summary><span class="p-dot"></span>${esc(t)}</summary><div class="p-sbody"><div class="p-f">${esc(f)}</div>↳ ${esc(cite)}</div></details>`).join('')
    : `<div class="p-empty" style="padding:10px 2px">Пошаговое обоснование в прототипе доступно для цепи L1.</div>`;
}
function renderCircuit() {
  const c = activeCircuit();
  $('pEdRef').textContent = `${c.ref} · ${c.desc}`;
  const signed = STATE.signed.has(c.id), so = $('pSignoff');
  so.className = 'badge small ' + (signed ? 'pass' : 'review');
  so.title = signed ? 'SIGNED' : 'UNSIGNED_ADVISORY';
  so.textContent = signed ? 'Подписано' : 'Не подписано · рекомендательно';
  renderForm(c);
  renderVerdict();
  renderReason();
  drawCharts(c);
}

// ---------- charts (Plotly mock; L1 live, others stub) ----------
const BASE = { paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font: { color: '#42557A', size: 12, family: "'Fira Sans', sans-serif" }, margin: { l: 56, r: 16, t: 42, b: 64 }, showlegend: true, legend: { orientation: 'h', y: -0.2, x: 0 } };
const CFG = { responsive: true, displayModeBar: false };
function stubPane(el, text) { el.innerHTML = `<div class="p-stubpane">${esc(text)}</div>`; }
function drawCharts(c) {
  ['tcc', 'sweep', 'vd', 'derating', 'sld'].forEach(t => { const el = $('pPlot_' + t); el.innerHTML = ''; el.style.height = ''; });
  if (c.id !== 'L1') {
    ['tcc', 'sweep', 'vd', 'derating'].forEach(t => stubPane($('pPlot_' + t), 'В прототипе доступна цепь L1'));
    $('pPlot_sld').innerHTML = sldSvg([{ label: 'ВРУ-1', sub: 'DB-1' }, { label: c.ref, sub: deviceStr(c) }], c.status, govLabel(c.governing));
    return;
  }
  drawL1Charts();
}
function drawL1Charts() {
  const f = currentForm(), r = calc(f);
  // TCC
  const cable = [[10, 100], [16, 40], [25, 12], [40, 3], [80, 0.4], [160, 0.05], [400, 0.01]];
  Plotly.react('pPlot_tcc', [
    { x: cable.map(p => p[0]), y: cable.map(p => p[1]), name: `Кабель ${fmt(r.section)} мм² (I²t)`, mode: 'lines', line: { color: '#DC2626', width: 2.5 } },
    { x: [12, 20, 20, 300], y: [1000, 1000, 0.02, 0.01], name: 'MCB C (полоса)', mode: 'lines', line: { color: '#1E40AF', width: 1 } },
  ], Object.assign({}, BASE, { title: { text: 'Время-токовая координация · иллюстративная проверка: OK', font: { size: 14 } }, xaxis: { type: 'log', title: 'Ток, A', gridcolor: '#DBEAFE' }, yaxis: { type: 'log', title: 'Время, с', gridcolor: '#DBEAFE' }, shapes: [vline(r.IB, '#5B6B8C', 'dot'), vline(r.section ? 20 : null, '#B45309', 'dash')] }), CFG);
  // sweep
  const secs = SECTION_IZ;
  Plotly.react('pPlot_sweep', [{ x: secs.map(s => fmt(s[0])), y: secs.map(s => s[1]), type: 'bar', marker: { color: secs.map(s => s[0] === r.section ? '#1E40AF' : (s[1] >= r.IB ? '#4E9B6E' : '#C9D6EA')) } }],
    Object.assign({}, BASE, { title: { text: `Подбор сечения — выбрано ${fmt(r.section)} мм² (${govLabel(r.governing)})`, font: { size: 14 } }, xaxis: { title: 'Сечение, мм²', type: 'category' }, yaxis: { title: 'IZ, A', gridcolor: '#DBEAFE' }, showlegend: false, shapes: [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: r.IB, y1: r.IB, line: { color: '#B45309', width: 1.5, dash: 'dash' } }] }), CFG);
  drawVD(f, r);
  // derating
  Plotly.react('pPlot_derating', [{ x: ['Табл. It', '· ka (t°)', '· kg (групп.)', 'IZ'], y: [24, 22, r.IZ, r.IZ], type: 'bar', marker: { color: ['#1E40AF', '#3B82F6', '#3B82F6', '#15803D'] }, text: ['24', '22', fmt(r.IZ, 0), fmt(r.IZ, 0)], textposition: 'outside' }],
    Object.assign({}, BASE, { title: { text: `Поправочные коэффициенты: It → IZ (нужно ≥ ${fmt(r.IB, 1)} A)`, font: { size: 14 } }, yaxis: { title: 'A', gridcolor: '#DBEAFE' }, showlegend: false, shapes: [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: r.IB, y1: r.IB, line: { color: '#B45309', width: 1.5, dash: 'dash' } }] }), CFG);
  // sld
  $('pPlot_sld').innerHTML = sldSvg([{ label: 'ВРУ-1', sub: 'DB-1' }, { label: 'L1', sub: deviceStr(byId('L1')) }], r.status, govLabel(r.governing));
}
function drawVD(f, r) {
  const K = f.ph === 3 ? 0.00848 : 0.01864;
  const xs = []; for (let l = 0; l <= Math.max(40, f.length + 8); l += 2) xs.push(l);
  const ys = xs.map(l => K * l * r.IB / r.section);
  Plotly.react('pPlot_vd', [
    { x: xs, y: ys, mode: 'lines', name: `ΔU при ${fmt(r.section)} мм²`, line: { color: '#1E40AF', width: 2.5 } },
    { x: [f.length], y: [r.vd], mode: 'markers', name: 'текущая длина', marker: { color: '#0F1B33', size: 9 } },
  ], Object.assign({}, BASE, { title: { text: 'Профиль падения напряжения', font: { size: 14 } }, xaxis: { title: 'Длина, м', gridcolor: '#DBEAFE' }, yaxis: { title: 'ΔU, %', gridcolor: '#DBEAFE' }, shapes: [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: r.limit, y1: r.limit, line: { color: '#DC2626', width: 1.5, dash: 'dash' } }] }), CFG);
}
function vline(x, color, dash) { return x == null ? {} : { type: 'line', x0: x, x1: x, yref: 'paper', y0: 0, y1: 1, line: { color, width: 1.5, dash } }; }

function recompute() {
  renderVerdict();
  if (activeCircuit().id === 'L1') drawVD(currentForm(), calc(currentForm()));
}

// ---------- tabs ----------
$('pTabs').addEventListener('click', e => {
  if (e.target.tagName !== 'BUTTON') return;
  $('pTabs').querySelectorAll('button').forEach(b => b.classList.remove('active'));
  e.target.classList.add('active');
  const id = e.target.dataset.tab;
  ['tcc', 'sweep', 'vd', 'derating', 'sld'].forEach(t => $('pPlot_' + t).style.display = t === id ? 'block' : 'none');
  if (['tcc', 'sweep', 'vd', 'derating'].includes(id) && activeCircuit().id === 'L1') Plotly.Plots.resize($('pPlot_' + id));
});

// ---------- copilot drawer ----------
function openDrawer() {
  $('pDrawer').classList.add('open'); $('pDrawer').setAttribute('aria-hidden', 'false');
  $('pDrawerBody').innerHTML = [
    ['bot', 'Опиши цепь словами — разберу параметры и соберу расчёт. (В прототипе — статичная имитация.)'],
    ['user', 'розетки кухни 3,6 кВт, 230 В, PVC медь метод C, 18 м, автомат C, КЗ 6 кА'],
    ['bot', 'Разобрал: <b>2,5 мм²</b>, Автомат (MCB) <b>20 A</b>, статус <b>Соответствует</b>. Параметры подставлены в форму слева.'],
  ].map(([cls, html]) => `<div class="p-msg ${cls}">${html}</div>`).join('');
}
function closeDrawer() { $('pDrawer').classList.remove('open'); $('pDrawer').setAttribute('aria-hidden', 'true'); }
$('pAskCopilot').addEventListener('click', openDrawer);
$('pDrawerClose').addEventListener('click', closeDrawer);
$('pDrawerSend').addEventListener('click', () => toast('Копилот в прототипе — статичная имитация диалога'));

// ---------- sign modal ----------
function openSignModal() {
  const c = activeCircuit(), r = verdictData(c);
  $('pModalSummary').innerHTML = [
    ['Цепь', `${c.ref} · ${c.desc}`], ['Кабель', cableStr(c)],
    ['Аппарат', `${devLabel(c.device)} ${fmt(c.In, 0)} A`],
    ['Результат', `${stLabel(r.status)} · ΔU ${fmt(r.vd)} % из ${fmt(r.limit || c.vdlimit, 0)}`],
    ['Норм-пакет', PACK_LABEL],
  ].map(([k, v]) => `<div>${k}: <b>${esc(v)}</b></div>`).join('');
  $('pAck').checked = false; $('pModalConfirm').disabled = true;
  $('pModalBack').classList.add('open');
}
function closeSignModal() { $('pModalBack').classList.remove('open'); }
$('pSignBtn').addEventListener('click', openSignModal);
$('pModalCancel').addEventListener('click', closeSignModal);
$('pAck').addEventListener('change', e => { $('pModalConfirm').disabled = !e.target.checked; });
$('pModalConfirm').addEventListener('click', () => {
  if (!$('pAck').checked) return;
  STATE.signed.add(STATE.activeId);
  closeSignModal();
  renderCircuit();
  toast(`Цепь ${STATE.activeId} подписана (рекомендательно, синтетические данные)`);
});
$('pModalBack').addEventListener('click', e => { if (e.target === $('pModalBack')) closeSignModal(); });

// ---------- circuit nav ----------
$('pBackBoard').addEventListener('click', backToBoard);
$('pSaveCircuit').addEventListener('click', () => { toast('Цепь сохранена в щит (локально)'); backToBoard(); });
function navCircuit(dir) {
  const i = CIRCUITS.findIndex(c => c.id === STATE.activeId), j = i + dir;
  if (j >= 0 && j < CIRCUITS.length) openCircuit(CIRCUITS[j].id);
}
$('pPrev').addEventListener('click', () => navCircuit(-1));
$('pNext2').addEventListener('click', () => navCircuit(1));
$('pTableBody').addEventListener('click', e => { const tr = e.target.closest('tr[data-cid]'); if (tr) openCircuit(tr.dataset.cid); });

// ---------- boot ----------
renderBoardCard();
renderTable();
renderNextStep();
renderNorm();
renderSldBoard();
showScreen('board');
