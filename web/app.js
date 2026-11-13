'use strict';

const $ = (s) => document.querySelector(s);
const SVG_NS = 'http://www.w3.org/2000/svg';

function el(name, attrs = {}, parent) {
  const n = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
}

const money = (v) => {
  if (v == null || isNaN(v)) return '—';
  if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(2) + ' 亿';
  if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(1) + ' 万';
  return Math.round(v).toLocaleString();
};
const num = (v) => (v == null || isNaN(v) ? '—' : Math.round(v).toLocaleString());
const pct = (v) => (v == null || isNaN(v) ? '—' : (v * 100).toFixed(1) + '%');

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(path + ' -> ' + r.status);
  return r.json();
}

function axes(svg, w, h, pad, xLabels, yMax, yFmt) {
  const g = el('g', {}, svg);
  for (let i = 0; i <= 4; i++) {
    const y = pad.t + (h - pad.t - pad.b) * (i / 4);
    el('line', { x1: pad.l, x2: w - pad.r, y1: y, y2: y, stroke: '#eef1f6' }, g);
    const t = el('text', { x: pad.l - 8, y: y + 4, 'text-anchor': 'end', fill: '#8a94a6', 'font-size': 11 }, g);
    t.textContent = yFmt(yMax * (1 - i / 4));
  }
  const step = Math.max(1, Math.ceil(xLabels.length / 8));
  xLabels.forEach((lab, i) => {
    if (i % step) return;
    const x = pad.l + (w - pad.l - pad.r) * (xLabels.length === 1 ? 0.5 : i / (xLabels.length - 1));
    const t = el('text', { x, y: h - pad.b + 16, 'text-anchor': 'middle', fill: '#8a94a6', 'font-size': 11 }, g);
    t.textContent = lab;
  });
}

function lineChart(host, rows) {
  host.innerHTML = '';
  if (!rows.length) return;
  const w = host.clientWidth || 900, h = 240, pad = { l: 56, r: 16, t: 12, b: 26 };
  const svg = el('svg', { viewBox: `0 0 ${w} ${h}`, width: '100%', height: h }, host);
  const gmvMax = Math.max(...rows.map((r) => r.gmv || 0)) * 1.1;
  const ordMax = Math.max(...rows.map((r) => r.orders || 0)) * 1.2;
  axes(svg, w, h, pad, rows.map((r) => String(r.day).slice(5)), gmvMax, money);
  const X = (i) => pad.l + (w - pad.l - pad.r) * (rows.length === 1 ? 0.5 : i / (rows.length - 1));
  const Y = (v, max) => h - pad.b - (h - pad.t - pad.b) * (v / max || 0);
  let d = '', a = '';
  rows.forEach((r, i) => {
    d += (i ? ' L' : 'M') + X(i) + ' ' + Y(r.gmv || 0, gmvMax);
    a += (i ? ' L' : 'M') + X(i) + ' ' + Y(r.gmv || 0, gmvMax);
  });
  a += ' L' + X(rows.length - 1) + ' ' + (h - pad.b) + ' L' + X(0) + ' ' + (h - pad.b) + ' Z';
  el('path', { d: a, fill: 'rgba(43,108,176,.10)' }, svg);
  el('path', { d, fill: 'none', stroke: '#2b6cb0', 'stroke-width': 2 }, svg);
  let od = '';
  rows.forEach((r, i) => { od += (i ? ' L' : 'M') + X(i) + ' ' + Y(r.orders || 0, ordMax); });
  el('path', { d: od, fill: 'none', stroke: '#38a169', 'stroke-width': 1.6, 'stroke-dasharray': '4 3' }, svg);
  const lg = el('g', { transform: `translate(${pad.l + 4},${pad.t + 2})` }, svg);
  el('rect', { width: 9, height: 3, y: 4, fill: '#2b6cb0' }, lg);
  const t1 = el('text', { x: 14, y: 9, 'font-size': 11, fill: '#6b7688' }, lg);
  t1.textContent = '净销售额 ' + money(rows[rows.length - 1].gmv);
  el('rect', { width: 9, height: 3, y: 4, x: 130, fill: '#38a169' }, lg);
  const t2 = el('text', { x: 144, y: 9, 'font-size': 11, fill: '#6b7688' }, lg);
  t2.textContent = '订单数 ' + num(rows[rows.length - 1].orders);
}

function barChart(host, rows, labelKey, valueKey, color = '#2b6cb0', fmt = money) {
  host.innerHTML = '';
  if (!rows.length) return;
  const w = host.clientWidth || 480, rowH = 30, h = rows.length * rowH + 16;
  const svg = el('svg', { viewBox: `0 0 ${w} ${h}`, width: '100%', height: h }, host);
  const max = Math.max(...rows.map((r) => r[valueKey] || 0));
  const labelW = 78, barMax = w - labelW - 86;
  rows.forEach((r, i) => {
    const y = 8 + i * rowH;
    const t = el('text', { x: 0, y: y + 13, 'font-size': 12, fill: '#3b4658' }, svg);
    t.textContent = r[labelKey];
    el('rect', { x: labelW, y: y + 3, width: barMax, height: 14, rx: 4, fill: '#f0f3f8' }, svg);
    el('rect', { x: labelW, y: y + 3, width: Math.max(2, barMax * (r[valueKey] / max)), height: 14, rx: 4, fill: color }, svg);
    const v = el('text', { x: labelW + barMax + 8, y: y + 14, 'font-size': 12, fill: '#6b7688' }, svg);
    v.textContent = fmt(r[valueKey]);
  });
}

function table(host, cols, rows, cls) {
  host.innerHTML = '';
  const t = document.createElement('table');
  const thead = document.createElement('thead');
  const hr = document.createElement('tr');
  cols.forEach((c) => { const th = document.createElement('th'); th.textContent = c.label; hr.appendChild(th); });
  thead.appendChild(hr); t.appendChild(thead);
  const tb = document.createElement('tbody');
  rows.forEach((r) => {
    const tr = document.createElement('tr');
    cols.forEach((c) => {
      const td = document.createElement('td');
      const raw = r[c.key];
      td.textContent = c.fmt ? c.fmt(raw) : raw == null ? '—' : raw;
      if (cls && c.key === 'status') td.className = cls[raw] || '';
      tr.appendChild(td);
    });
    tb.appendChild(tr);
  });
  t.appendChild(tb); host.appendChild(t);
}

function chips(kpi) {
  const items = [
    ['有效订单', num(kpi.orders), '状态 paid/shipped/completed/closed'],
    ['净销售额', money(kpi.gmv), '扣折扣后金额'],
    ['客单价', money(kpi.avg_order_value), '净销售额 / 订单数'],
    ['退货率', pct(kpi.return_rate), num(kpi.returned_units) + ' 件退货'],
    ['动销率', pct(kpi.sku_coverage), (kpi.active_skus || 0) + ' / ' + kpi.sku_count + ' 个 SKU'],
    ['折扣率', pct(kpi.discount_rate), '折扣额 / 原价额'],
  ];
  $('#chips').innerHTML = items.map(([l, v, f]) =>
    `<div class="chip"><div class="label">${l}</div><div class="value">${v}</div><div class="foot">${f}</div></div>`).join('');
}

async function load() {
  const role = $('#role').value;
  const days = Number($('#days').value);
  const q = role ? '?role=' + encodeURIComponent(role) : '';
  const qa = role ? '&role=' + encodeURIComponent(role) : '';
  const t0 = performance.now();
  try {
    const [kpi, trend, cat, region, ret, repeat, dq, runs] = await Promise.all([
      api('/api/kpi' + q), api('/api/trend?days=' + days + qa), api('/api/category'),
      api('/api/region'), api('/api/returns'), api('/api/repeat'), api('/api/dq'), api('/api/runs?limit=8'),
    ]);
    chips(kpi);
    lineChart($('#trend'), trend);
    $('#trend-hint').textContent = `最近 ${trend.length} 天，合计 ${money(trend.reduce((s, r) => s + (r.gmv || 0), 0))}`;
    barChart($('#category'), cat, 'category', 'gmv', '#2b6cb0');
    barChart($('#region'), region, 'region', 'gmv', '#38a169');
    barChart($('#returns'), ret, 'reason', 'units', '#c05621', num);
    barChart($('#repeat'), repeat.slice(-6), 'sales_month', 'repeat_rate', '#4c51bf', pct);
    table($('#dq'), [
      { key: 'rule_id', label: '#' }, { key: 'rule_name', label: '规则' },
      { key: 'rows_checked', label: '检查行', fmt: num }, { key: 'rows_failed', label: '失败', fmt: num },
      { key: 'status', label: '结果' },
    ], dq, { PASS: 'pass', FAIL: 'fail', ERROR: 'fail' });
    table($('#runs'), [
      { key: 'run_id', label: 'run' }, { key: 'mode', label: '模式' },
      { key: 'rows_in', label: '读入', fmt: num }, { key: 'rows_out', label: '写入', fmt: num },
      { key: 'status', label: '状态' },
    ], runs);
    $('#elapsed').textContent = '前端取数 ' + Math.round(performance.now() - t0) + ' ms';
  } catch (e) {
    $('#elapsed').textContent = '加载失败：' + e.message;
  }
}

$('#refresh').addEventListener('click', load);
$('#role').addEventListener('change', load);
$('#days').addEventListener('change', load);
let timer;
window.addEventListener('resize', () => { clearTimeout(timer); timer = setTimeout(load, 200); });
load();