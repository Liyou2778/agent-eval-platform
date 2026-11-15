'use strict';

/* ------------------------------------------------------------------ *
 * Agent 评测中台 · 单页看板（原生 JS，无构建）
 * ------------------------------------------------------------------ */

const state = {
  datasets: [],
  targets: [],
  runs: [],
  currentDatasetId: null,
  currentRunId: null,
  lastRun: null,
  pollTimer: null,
  compareData: null,
};

const TARGET_TEMPLATES = {
  mock: {
    accuracy: 0.85,
    salt: 'demo-v1',
    latency_ms: [20, 200],
    max_steps: 3,
    price_in_per_1m: 0.15,
    price_out_per_1m: 0.6,
  },
  http: {
    url: 'http://127.0.0.1:8205/api/health',
    method: 'GET',
    headers: {},
    body_template: {},
    timeout_s: 10,
    response_map: { answer: 'status', steps: 'service', tokens_in: 'version', tokens_out: 'database' },
  },
  'openai-compatible': {
    base_url: 'https://api.openai.com/v1',
    model: 'gpt-4o-mini',
    api_key_env: 'OPENAI_API_KEY',
    temperature: 0,
    max_tokens: 256,
    timeout_s: 30,
  },
};

const STATUS_TEXT = {
  queued: '排队中',
  running: '运行中',
  completed: '已完成',
  cancelled: '已取消',
  failed: '失败',
  pass: '通过',
  fail: '未通过',
  error: '错误',
};

/* ------------------------------------------------------------------ *
 * helpers
 * ------------------------------------------------------------------ */

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function esc(value) {
  return String(value === null || value === undefined ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function fmtPct(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return (number * 100).toFixed(1) + '%';
}

function fmtMs(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return number >= 1000 ? (number / 1000).toFixed(2) + ' s' : number.toFixed(0) + ' ms';
}

function fmtNum(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return number.toLocaleString('en-US');
}

function fmtTime(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString('zh-CN', { hour12: false });
}

function statusBadge(status) {
  const text = STATUS_TEXT[status] || status || '—';
  return `<span class="status ${esc(status)}">${esc(text)}</span>`;
}

function durationText(run) {
  if (!run || !run.started_at) return '—';
  const start = new Date(run.started_at).getTime();
  const end = run.ended_at ? new Date(run.ended_at).getTime() : Date.now();
  const seconds = Math.max(0, (end - start) / 1000);
  return seconds.toFixed(2) + ' s';
}

function judgeLabel(judge) {
  if (!judge) return '—';
  if (judge.kind === 'llm') return `LLM-as-judge (${judge.mode || 'mock'})`;
  return '规则断言 (rule)';
}

let toastTimer = null;
function toast(message, isError = false) {
  const el = $('#toast');
  el.textContent = message;
  el.classList.toggle('error', Boolean(isError));
  el.hidden = false;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4200);
}

async function api(path, options = {}) {
  const config = { headers: { 'Content-Type': 'application/json' }, ...options };
  const response = await fetch(path, config);
  let data = null;
  try { data = await response.json(); } catch (error) { data = null; }
  if (!response.ok) {
    let detail = data && data.detail;
    if (Array.isArray(detail)) {
      detail = detail.map((item) => `${(item.loc || []).join('.')}: ${item.msg}`).join('; ');
    }
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  return data;
}

/* ------------------------------------------------------------------ *
 * tabs
 * ------------------------------------------------------------------ */

function switchTab(name) {
  $$('.tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.tab === name));
  $$('.tab-panel').forEach((panel) => panel.classList.toggle('active', panel.id === 'panel-' + name));
  if (name === 'datasets') loadDatasets().catch((error) => toast(error.message, true));
  if (name === 'targets') loadTargets().catch((error) => toast(error.message, true));
  if (name === 'runs') {
    refreshRuns()
      .then(() => {
        if (!state.currentRunId && state.runs.length) selectRun(state.runs[0].id).catch((error) => toast(error.message, true));
      })
      .catch((error) => toast(error.message, true));
  }
  if (name === 'compare') {
    refreshRuns()
      .then(refreshCompareOptions)
      .catch((error) => toast(error.message, true));
  }
}

/* ------------------------------------------------------------------ *
 * stats / health
 * ------------------------------------------------------------------ */

async function loadStats() {
  try {
    const [health, stats] = await Promise.all([api('/api/health'), api('/api/stats')]);
    const chips = [
      `<span class="stat-chip ok">● 服务正常 · v${esc(health.version)}</span>`,
      `<span class="stat-chip">数据集 <b>${stats.datasets}</b></span>`,
      `<span class="stat-chip">用例 <b>${stats.cases}</b></span>`,
      `<span class="stat-chip">目标 <b>${stats.targets}</b></span>`,
      `<span class="stat-chip">Run <b>${stats.runs}</b></span>`,
    ];
    $('#top-stats').innerHTML = chips.join('');
  } catch (error) {
    $('#top-stats').innerHTML = `<span class="stat-chip bad">● 服务异常：${esc(error.message)}</span>`;
  }
}

/* ------------------------------------------------------------------ *
 * datasets + cases
 * ------------------------------------------------------------------ */

function fillSelect(select, items, valueKey, labelFn, emptyLabel = '—') {
  if (!select) return;
  const previous = select.value;
  if (!items.length) {
    select.innerHTML = `<option value="">${esc(emptyLabel)}</option>`;
    return;
  }
  select.innerHTML = items
    .map((item) => `<option value="${esc(item[valueKey])}">${esc(labelFn(item))}</option>`)
    .join('');
  const keep = items.some((item) => String(item[valueKey]) === String(previous));
  select.value = keep ? previous : String(items[0][valueKey]);
}

async function loadDatasets() {
  const data = await api('/api/datasets');
  state.datasets = data.items;
  $('#dataset-count').textContent = state.datasets.length;
  renderDatasetTable();
  fillSelect($('#import-dataset'), state.datasets, 'id', (item) => `${item.name} (${item.version}) · ${item.case_count} 条`, '暂无数据集');
  fillSelect($('#run-dataset'), state.datasets, 'id', (item) => `${item.name} (${item.version}) · ${item.case_count} 条`, '暂无数据集');
  if (state.currentDatasetId && state.datasets.some((item) => item.id === state.currentDatasetId)) {
    await loadCases(state.currentDatasetId);
  } else if (state.datasets.length) {
    state.currentDatasetId = state.datasets[0].id;
    await loadCases(state.currentDatasetId);
  } else {
    state.currentDatasetId = null;
    $('#case-preview-title').textContent = '点击数据集「查看用例」';
    $('#case-table tbody').innerHTML = '<tr><td colspan="4" class="empty-row">暂无用例</td></tr>';
  }
}

function renderDatasetTable() {
  const tbody = $('#dataset-table tbody');
  if (!state.datasets.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-row">还没有数据集，先创建一个并导入用例</td></tr>';
    return;
  }
  tbody.innerHTML = state.datasets.map((dataset) => `
    <tr class="${dataset.id === state.currentDatasetId ? 'selected' : ''}">
      <td>${dataset.id}</td>
      <td>${esc(dataset.name)}</td>
      <td>${esc(dataset.version)}</td>
      <td>${dataset.case_count}</td>
      <td class="muted">${esc(fmtTime(dataset.created_at))}</td>
      <td><button class="btn small" data-cases="${dataset.id}">查看用例</button></td>
    </tr>
  `).join('');
  $$('#dataset-table [data-cases]').forEach((button) => {
    button.addEventListener('click', () => {
      state.currentDatasetId = Number(button.dataset.cases);
      renderDatasetTable();
      loadCases(state.currentDatasetId).catch((error) => toast(error.message, true));
    });
  });
}

async function loadCases(datasetId) {
  const data = await api(`/api/datasets/${datasetId}/cases?limit=200`);
  const dataset = state.datasets.find((item) => item.id === datasetId);
  $('#case-preview-title').textContent =
    `${dataset ? dataset.name : '#' + datasetId} · 共 ${data.total} 条（显示 ${data.items.length}）`;
  const tbody = $('#case-table tbody');
  if (!data.items.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty-row">暂无 active 用例</td></tr>';
    return;
  }
  tbody.innerHTML = data.items.map((item) => `
    <tr>
      <td>${item.id}</td>
      <td class="cell-input">${esc(item.input)}</td>
      <td class="mono">${esc(item.expected)}</td>
      <td>${(item.tags || []).map((tag) => `<span class="tag small">${esc(tag)}</span>`).join('')}</td>
    </tr>
  `).join('');
}

async function createDataset(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = Object.fromEntries(new FormData(form).entries());
  try {
    const dataset = await api('/api/datasets', { method: 'POST', body: JSON.stringify(data) });
    toast(`已创建数据集 #${dataset.id} ${dataset.name}`);
    form.name.value = '';
    await loadDatasets();
    loadStats();
  } catch (error) {
    toast('创建数据集失败：' + error.message, true);
  }
}

async function importCases(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const formData = new FormData(form);
  const datasetId = formData.get('dataset_id');
  if (!datasetId) {
    toast('请先创建数据集', true);
    return;
  }
  const text = (formData.get('text') || '').trim();
  if (!text) {
    toast('请粘贴用例文本或选择文件', true);
    return;
  }
  const resultEl = $('#import-result');
  try {
    const result = await api(`/api/datasets/${datasetId}/cases`, {
      method: 'POST',
      body: JSON.stringify({ text, format: formData.get('format') || 'auto' }),
    });
    resultEl.hidden = false;
    resultEl.textContent = JSON.stringify(result, null, 2);
    toast(`导入完成：${result.imported} 条，跳过 ${result.skipped} 条`);
    await loadDatasets();
    loadStats();
  } catch (error) {
    resultEl.hidden = false;
    resultEl.textContent = '导入失败：' + error.message;
    toast('导入失败：' + error.message, true);
  }
}

/* ------------------------------------------------------------------ *
 * targets
 * ------------------------------------------------------------------ */

async function loadTargets() {
  const data = await api('/api/targets');
  state.targets = data.items;
  $('#target-count').textContent = state.targets.length;
  renderTargetTable();
  fillSelect($('#probe-target'), state.targets, 'id', (item) => `#${item.id} ${item.name} (${item.kind})`, '暂无目标');
  fillSelect($('#run-target'), state.targets, 'id', (item) => `#${item.id} ${item.name} (${item.kind})`, '暂无目标');
}

function configSummary(config) {
  if (!config) return '—';
  const text = JSON.stringify(config);
  return text.length > 90 ? text.slice(0, 90) + '…' : text;
}

function renderTargetTable() {
  const tbody = $('#target-table tbody');
  if (!state.targets.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-row">还没有目标，可使用默认 mock 模板创建一个</td></tr>';
    return;
  }
  tbody.innerHTML = state.targets.map((target) => `
    <tr>
      <td>${target.id}</td>
      <td>${esc(target.name)}</td>
      <td><span class="tag small">${esc(target.kind)}</span></td>
      <td class="mono muted">${esc(configSummary(target.config))}</td>
      <td><button class="btn small" data-probe="${target.id}">探活</button></td>
    </tr>
  `).join('');
  $$('#target-table [data-probe]').forEach((button) => {
    button.addEventListener('click', async () => {
      const targetId = Number(button.dataset.probe);
      await probeTargetById(targetId);
    });
  });
}

function updateTargetTemplate() {
  const kind = $('#target-kind').value;
  $('#target-config').value = JSON.stringify(TARGET_TEMPLATES[kind] || {}, null, 2);
}

async function createTarget(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const formData = new FormData(form);
  let config;
  try {
    config = JSON.parse(formData.get('config') || '{}');
  } catch (error) {
    toast('config 不是合法 JSON：' + error.message, true);
    return;
  }
  try {
    const target = await api('/api/targets', {
      method: 'POST',
      body: JSON.stringify({ name: formData.get('name'), kind: formData.get('kind'), config }),
    });
    toast(`已创建目标 #${target.id} ${target.name}`);
    form.name.value = '';
    await loadTargets();
    loadStats();
  } catch (error) {
    toast('创建目标失败：' + error.message, true);
  }
}

async function probeTargetById(targetId) {
  const inputEl = $('#probe-form input[name="input"]');
  const input = inputEl ? inputEl.value : '你好，请用一句话介绍你自己。';
  const resultEl = $('#probe-result');
  try {
    const result = await api(`/api/targets/${targetId}/probe`, {
      method: 'POST',
      body: JSON.stringify({ input }),
    });
    resultEl.hidden = false;
    if (result.ok) {
      resultEl.textContent =
        `探活成功 · kind=${result.kind} · ${fmtMs(result.latency_ms)} · steps=${result.steps} · ` +
        `tokens=${result.tokens_in}/${result.tokens_out} · cost=$${Number(result.cost_usd).toFixed(6)}\n\n` +
        result.output;
    } else {
      resultEl.textContent = `探活失败 · kind=${result.kind} · error_type=${result.error_type} · ${fmtMs(result.latency_ms)}\n\n${result.error}`;
    }
    toast(result.ok ? '探活成功' : `探活失败：${result.error_type}`, !result.ok);
  } catch (error) {
    resultEl.hidden = false;
    resultEl.textContent = '探活请求失败：' + error.message;
    toast('探活请求失败：' + error.message, true);
  }
}

async function probeTarget(event) {
  event.preventDefault();
  const targetId = event.currentTarget.target_id.value;
  if (!targetId) {
    toast('请先创建目标', true);
    return;
  }
  await probeTargetById(Number(targetId));
}

/* ------------------------------------------------------------------ *
 * runs
 * ------------------------------------------------------------------ */

function isFinalStatus(status) {
  return status === 'completed' || status === 'cancelled' || status === 'failed';
}

async function refreshRuns() {
  const data = await api('/api/runs?limit=50');
  state.runs = data.items;
  $('#run-count').textContent = state.runs.length;
  renderRunTable();
  refreshCompareOptions();
}

function renderRunTable() {
  const tbody = $('#run-table tbody');
  if (!state.runs.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-row">还没有运行记录，先发起一次评测</td></tr>';
    return;
  }
  tbody.innerHTML = state.runs.map((run) => {
    const metrics = run.metrics || {};
    const progress = run.progress || { percent: 0, done: 0, total: 0 };
    const rate = run.status === 'queued' ? '—' : fmtPct(metrics.pass_rate);
    return `
      <tr class="expandable ${run.id === state.currentRunId ? 'selected' : ''}" data-run="${run.id}">
        <td>#${run.id}</td>
        <td>${statusBadge(run.status)}</td>
        <td>${esc(run.target_name || '—')}</td>
        <td>
          <div class="progress" style="min-width:80px"><i style="width:${progress.percent}%"></i></div>
          <span class="muted">${progress.done}/${progress.total}</span>
        </td>
        <td>${rate}</td>
        <td class="muted">${esc(fmtTime(run.started_at || run.created_at))}</td>
      </tr>
    `;
  }).join('');
  $$('#run-table [data-run]').forEach((row) => {
    row.addEventListener('click', () => {
      selectRun(Number(row.dataset.run)).catch((error) => toast(error.message, true));
    });
  });
}

function markSelectedRunRow() {
  $$('#run-table [data-run]').forEach((row) => {
    row.classList.toggle('selected', Number(row.dataset.run) === state.currentRunId);
  });
}

async function selectRun(runId) {
  state.currentRunId = Number(runId);
  stopPolling();
  markSelectedRunRow();
  const run = await loadRunDetail();
  if (run && !isFinalStatus(run.status)) ensurePolling();
}

function stopPolling() {
  if (state.pollTimer) {
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }
}

function ensurePolling(delay = 500) {
  stopPolling();
  state.pollTimer = setTimeout(pollTick, delay);
}

async function pollTick() {
  state.pollTimer = null;
  if (!state.currentRunId) return;
  try {
    const run = await loadRunDetail();
    if (run && !isFinalStatus(run.status)) {
      state.pollTimer = setTimeout(pollTick, 700);
    } else {
      await refreshRuns();
    }
  } catch (error) {
    toast('进度轮询失败：' + error.message, true);
  }
}

async function loadRunDetail() {
  if (!state.currentRunId) return null;
  const run = await api('/api/runs/' + state.currentRunId);
  state.lastRun = run;
  renderRunDetail(run);
  if (isFinalStatus(run.status)) {
    await loadFailedResults(run.id);
  }
  return run;
}

function renderMetricGrid(metrics) {
  const cards = [
    { label: '通过率', value: fmtPct(metrics.pass_rate), cls: 'green' },
    { label: 'pass@1', value: fmtPct(metrics.pass_at_1) },
    { label: '通过 / 失败 / 错误', value: `${metrics.passed} / ${metrics.failed} / ${metrics.errors}` },
    { label: '平均延迟', value: fmtMs(metrics.latency_ms.avg) },
    { label: 'P50 延迟', value: fmtMs(metrics.latency_ms.p50) },
    { label: 'P95 延迟', value: fmtMs(metrics.latency_ms.p95) },
    { label: '平均步数', value: Number(metrics.avg_steps).toFixed(2) },
    { label: 'Tokens (in/out)', value: `${fmtNum(metrics.tokens_in)} / ${fmtNum(metrics.tokens_out)}`, cls: 'small' },
    { label: '总成本', value: '$' + Number(metrics.cost_usd).toFixed(6) },
    { label: '重试用例', value: metrics.retried_cases },
    {
      label: '错误分类',
      value: Object.keys(metrics.error_counts || {}).length
        ? Object.entries(metrics.error_counts).map(([key, value]) => `${key}:${value}`).join(' ')
        : '无',
      cls: Object.keys(metrics.error_counts || {}).length ? 'red small' : 'small',
    },
  ];
  return `<div class="metric-grid">${cards.map((card) => `
    <div class="metric">
      <div class="label">${esc(card.label)}</div>
      <div class="value ${card.cls || ''}">${esc(card.value)}</div>
    </div>
  `).join('')}</div>`;
}

function renderBars(element, items) {
  if (!element) return;
  const max = Math.max(...items.map((item) => Number(item.value) || 0), 1);
  element.innerHTML = items.map((item) => {
    const width = Math.max(1.5, ((Number(item.value) || 0) / max) * 100);
    return `
      <div class="bar-row">
        <span class="bar-label">${esc(item.label)}</span>
        <div class="bar-track"><div class="bar-fill" style="width:${width.toFixed(1)}%;background:${item.color || 'var(--accent)'}"></div></div>
        <span class="bar-value">${esc(item.display !== undefined ? item.display : item.value)}</span>
      </div>
    `;
  }).join('');
}

function renderRunDetail(run) {
  const metrics = run.metrics || {};
  const progress = run.progress || { done: 0, total: run.total || 0, percent: 0 };
  const isActive = !isFinalStatus(run.status);
  $('#run-detail-title').textContent = `Run #${run.id} · ${run.dataset_name || ''} → ${run.target_name || ''}`;
  const cancelButton = $('#cancel-run');
  cancelButton.hidden = !isActive;
  cancelButton.dataset.run = run.id;

  const html = `
    <div class="compare-head">
      <div class="box"><div class="k">状态</div><div class="v">${statusBadge(run.status)}</div></div>
      <div class="box"><div class="k">数据集 / 目标</div><div class="v">${esc(run.dataset_name || '—')} / ${esc(run.target_name || '—')}</div></div>
      <div class="box"><div class="k">并发 / 重试 / 超时</div><div class="v">${run.config.concurrency} / ${run.config.retries} / ${run.config.timeout_s}s</div></div>
      <div class="box"><div class="k">判定</div><div class="v">${esc(judgeLabel(run.config.judge))}</div></div>
      <div class="box"><div class="k">耗时</div><div class="v">${esc(durationText(run))}</div></div>
    </div>
    <div class="progress-line">
      <div class="progress"><i style="width:${progress.percent}%"></i></div>
      <span>${progress.done} / ${progress.total} · ${progress.percent}%</span>
    </div>
    ${renderMetricGrid(metrics)}
    <div class="charts-2">
      <div><h3>延迟分布（ms）</h3><div class="chart" id="latency-chart"></div></div>
      <div><h3>判定分布</h3><div class="chart" id="pass-chart"></div></div>
    </div>
    <h3>失败用例 <span class="hint" id="failed-count"></span></h3>
    <div class="table-wrap tall">
      <table id="failed-table">
        <thead><tr><th>Case</th><th>状态</th><th>输入</th><th>延迟</th><th>判定 / 错误</th><th></th></tr></thead>
        <tbody><tr><td colspan="6" class="empty-row">加载中…</td></tr></tbody>
      </table>
    </div>
  `;
  $('#run-detail').className = '';
  $('#run-detail').innerHTML = html;

  renderBars($('#latency-chart'), [
    { label: '平均', value: metrics.latency_ms.avg, display: fmtMs(metrics.latency_ms.avg) },
    { label: 'P50', value: metrics.latency_ms.p50, display: fmtMs(metrics.latency_ms.p50) },
    { label: 'P95', value: metrics.latency_ms.p95, display: fmtMs(metrics.latency_ms.p95), color: 'var(--amber)' },
  ]);
  renderBars($('#pass-chart'), [
    { label: '通过', value: metrics.passed, display: String(metrics.passed), color: 'var(--green)' },
    { label: '未通过', value: metrics.failed, display: String(metrics.failed), color: 'var(--red)' },
    { label: '错误', value: metrics.errors, display: String(metrics.errors), color: 'var(--purple)' },
  ]);
}

function detailBlocks(item) {
  const judgeText = item.judge ? JSON.stringify(item.judge, null, 2) : (item.error || '无');
  const caseInfo = item.case || {};
  return `
    <div class="detail-grid">
      <div><h4>输入 input</h4><pre class="block">${esc(caseInfo.input || '(空)')}</pre></div>
      <div><h4>期望 expected</h4><pre class="block">${esc(caseInfo.expected || '(空)')}</pre></div>
      <div class="full"><h4>输出 output</h4><pre class="block">${esc(item.output || '(空输出)')}</pre></div>
      <div class="full"><h4>判定 judge / 错误 error</h4><pre class="block">${esc(judgeText)}</pre></div>
    </div>
  `;
}

async function loadFailedResults(runId) {
  const data = await api(`/api/runs/${runId}/results?status=failed&limit=100`);
  const countEl = $('#failed-count');
  if (countEl) countEl.textContent = `共 ${data.total} 条（显示 ${data.items.length} 条，点击行展开 input/expected/output/judge.reason）`;
  const tbody = $('#failed-table tbody');
  if (!tbody) return;
  if (!data.items.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-row">没有失败用例</td></tr>';
    return;
  }
  tbody.innerHTML = data.items.map((item) => {
    const reason = item.judge && item.judge.reason ? item.judge.reason : (item.error || '—');
    return `
      <tr class="expandable" data-result="${item.id}">
        <td>${item.case_id}</td>
        <td>${statusBadge(item.status)}</td>
        <td class="cell-input">${esc(item.case ? item.case.input : '')}</td>
        <td>${esc(fmtMs(item.latency_ms))}</td>
        <td class="muted">${esc(String(reason).slice(0, 120))}</td>
        <td class="muted">展开</td>
      </tr>
      <tr class="detail-row" hidden>
        <td colspan="6">${detailBlocks(item)}</td>
      </tr>
    `;
  }).join('');
  bindExpandable($('#failed-table'));
}

function bindExpandable(root = document) {
  $$('tr.expandable', root).forEach((row) => {
    row.addEventListener('click', () => {
      const detail = row.nextElementSibling;
      if (detail && detail.classList.contains('detail-row')) {
        detail.hidden = !detail.hidden;
        const cell = row.lastElementChild;
        if (cell) cell.textContent = detail.hidden ? '展开' : '收起';
      }
    });
  });
}

async function submitRun(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const formData = new FormData(form);
  if (!formData.get('dataset_id')) {
    toast('请先创建数据集', true);
    return;
  }
  if (!formData.get('target_id')) {
    toast('请先创建目标', true);
    return;
  }
  const payload = {
    dataset_id: Number(formData.get('dataset_id')),
    target_id: Number(formData.get('target_id')),
    concurrency: Number(formData.get('concurrency')),
    retries: Number(formData.get('retries')),
    timeout_s: Number(formData.get('timeout_s')),
    judge: {
      kind: formData.get('judge_kind'),
      mode: formData.get('judge_mode'),
    },
  };
  try {
    const run = await api('/api/runs', { method: 'POST', body: JSON.stringify(payload) });
    toast(`已发起 Run #${run.id}`);
    state.currentRunId = run.id;
    await refreshRuns();
    await loadRunDetail();
    ensurePolling();
  } catch (error) {
    toast('发起评测失败：' + error.message, true);
  }
}

async function cancelRun(event) {
  event.preventDefault();
  const runId = Number(event.currentTarget.dataset.run || state.currentRunId);
  if (!runId) return;
  try {
    await api(`/api/runs/${runId}/cancel`, { method: 'POST' });
    toast(`Run #${runId} 已取消`);
    stopPolling();
    await loadRunDetail();
    await refreshRuns();
  } catch (error) {
    toast('取消失败：' + error.message, true);
  }
}

/* ------------------------------------------------------------------ *
 * compare
 * ------------------------------------------------------------------ */

function refreshCompareOptions() {
  const candidates = state.runs.filter((run) => isFinalStatus(run.status));
  const options = candidates
    .map((run) => `<option value="${run.id}">#${run.id} · ${esc(run.target_name || '')} · ${esc(STATUS_TEXT[run.status] || run.status)} · 通过率 ${fmtPct((run.metrics || {}).pass_rate)}</option>`)
    .join('');
  const selectA = $('#compare-a');
  const selectB = $('#compare-b');
  if (!selectA || !selectB) return;
  const prevA = selectA.value;
  const prevB = selectB.value;
  selectA.innerHTML = options || '<option value="">暂无已完成的 run</option>';
  selectB.innerHTML = options || '<option value="">暂无已完成的 run</option>';
  if (prevA && candidates.some((run) => String(run.id) === prevA)) selectA.value = prevA;
  if (prevB && candidates.some((run) => String(run.id) === prevB)) selectB.value = prevB;
  if (candidates.length >= 2) {
    if (!selectA.value || !candidates.some((run) => String(run.id) === selectA.value)) {
      selectA.value = String(candidates[1].id);
    }
    if (!selectB.value || !candidates.some((run) => String(run.id) === selectB.value)) {
      selectB.value = String(candidates[0].id);
    }
  }
}

function deltaCell(delta, higherIsBetter) {
  const number = Number(delta);
  if (!Number.isFinite(number) || number === 0) {
    return '<span class="muted">0</span>';
  }
  const good = higherIsBetter ? number > 0 : number < 0;
  const cls = good ? 'delta-up' : 'delta-down';
  return `<span class="${cls}">${number > 0 ? '▲ +' : '▼ '}${number.toFixed(4)}</span>`;
}

function renderCompareCategory(key, title, pillClass, items) {
  if (!items.length) return '';
  const rows = items.map((item) => `
    <tr class="expandable">
      <td>${item.case_id}</td>
      <td class="cell-input">${esc(item.input || '')}</td>
      <td>${statusBadge(item.a_status || 'missing')}</td>
      <td>${statusBadge(item.b_status || 'missing')}</td>
      <td class="muted">${esc(String(item.judge_reason || item.error || '').slice(0, 90))}</td>
      <td class="muted">展开</td>
    </tr>
    <tr class="detail-row" hidden>
      <td colspan="6">
        <div class="detail-grid">
          <div><h4>期望 expected</h4><pre class="block">${esc(item.expected || '(空)')}</pre></div>
          <div><h4>case</h4><pre class="block">case_id=${item.case_id}</pre></div>
          <div class="full"><h4>A 输出 / 判定原因</h4><pre class="block">${esc((item.output || '(空)') + '\n\n---\n' + (item.judge_reason || item.error || ''))}</pre></div>
          <div class="full"><h4>B 输出 / 判定原因</h4><pre class="block">${esc((item.b_output || '(空)') + '\n\n---\n' + (item.b_judge_reason || item.b_error || ''))}</pre></div>
        </div>
      </td>
    </tr>
  `).join('');
  return `
    <details class="section" ${key === 'regressed' || key === 'fixed' ? 'open' : ''}>
      <summary>${esc(title)} <span class="count-pill ${pillClass}">${items.length}</span></summary>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Case</th><th>输入</th><th>A</th><th>B</th><th>判定原因</th><th></th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </details>
  `;
}

function renderCompare(data) {
  const container = $('#compare-result');
  const metricsA = data.metrics.a;
  const metricsB = data.metrics.b;
  const counts = data.counts;
  const deltas = data.metric_deltas;
  const metricRows = [
    ['通过率', 'pass_rate', fmtPct, true],
    ['pass@1', 'pass_at_1', fmtPct, true],
    ['平均延迟', 'latency_avg_ms', fmtMs, false],
    ['P50 延迟', 'latency_p50_ms', fmtMs, false],
    ['P95 延迟', 'latency_p95_ms', fmtMs, false],
    ['平均步数', 'avg_steps', (value) => Number(value).toFixed(2), false],
    ['Tokens 合计', 'tokens_total', fmtNum, false],
    ['总成本', 'cost_usd', (value) => '$' + Number(value).toFixed(6), false],
  ];
  const tableRows = metricRows.map(([label, key, formatter, higherIsBetter]) => {
    const delta = deltas[key];
    return `<tr>
      <td>${esc(label)}</td>
      <td>${esc(formatter(delta.a))}</td>
      <td>${esc(formatter(delta.b))}</td>
      <td>${deltaCell(delta.delta, higherIsBetter)}</td>
    </tr>`;
  }).join('');

  const countsHtml = [
    ['修复', counts.fixed, 'green'],
    ['回归', counts.regressed, 'red'],
    ['仍失败', counts.still_failed, 'amber'],
    ['仍通过', counts.still_passed, 'muted'],
    ['新增用例', counts.new_case, 'muted'],
    ['移除用例', counts.removed_case, 'muted'],
  ].map(([label, value, cls]) => `
    <div class="box"><div class="k">${esc(label)}</div><div class="v"><span class="count-pill ${cls}">${value}</span></div></div>
  `).join('');

  const categoriesHtml = [
    renderCompareCategory('fixed', '修复（A 失败 → B 通过）', 'green', data.diff.fixed),
    renderCompareCategory('regressed', '回归（A 通过 → B 失败）', 'red', data.diff.regressed),
    renderCompareCategory('still_failed', '仍失败（两次都失败）', 'amber', data.diff.still_failed),
    renderCompareCategory('still_passed', '仍通过（两次都通过）', 'muted', data.diff.still_passed),
    renderCompareCategory('new_case', '新增用例（仅 B 有结果）', 'muted', data.diff.new_case),
    renderCompareCategory('removed_case', '移除用例（仅 A 有结果）', 'muted', data.diff.removed_case),
  ].join('');

  container.className = '';
  container.innerHTML = `
    <div class="card">
      <h2>对比结果 <span class="hint">A = 基线 run #${data.a.id}，B = 候选 run #${data.b.id}</span></h2>
      <div class="compare-head">
        <div class="box"><div class="k">A 通过率</div><div class="v">${fmtPct(metricsA.pass_rate)}</div></div>
        <div class="box"><div class="k">B 通过率</div><div class="v">${fmtPct(metricsB.pass_rate)}</div></div>
        <div class="box"><div class="k">通过率变化</div><div class="v">${deltaCell(deltas.pass_rate.delta, true)}</div></div>
        ${countsHtml}
      </div>
      <div class="charts-2">
        <div><h3>通过率对比</h3><div class="chart">
          ${[['A', metricsA.pass_rate], ['B', metricsB.pass_rate]].map(([label, value]) => `
            <div class="bar-row">
              <span class="bar-label">${label}</span>
              <div class="bar-track"><div class="bar-fill" style="width:${Math.max(1, value * 100).toFixed(1)}%;background:${label === 'A' ? 'var(--muted)' : 'var(--green)'}"></div></div>
              <span class="bar-value">${fmtPct(value)}</span>
            </div>`).join('')}
        </div></div>
        <div><h3>P95 延迟对比</h3><div class="chart">
          ${[['A', metricsA.latency_ms.p95], ['B', metricsB.latency_ms.p95]].map(([label, value]) => `
            <div class="bar-row">
              <span class="bar-label">${label}</span>
              <div class="bar-track"><div class="bar-fill" style="width:${Math.max(1, Math.min(100, (Number(value) / Math.max(Number(metricsA.latency_ms.p95), Number(metricsB.latency_ms.p95), 1)) * 100)).toFixed(1)}%;background:${label === 'A' ? 'var(--muted)' : 'var(--amber)'}"></div></div>
              <span class="bar-value">${fmtMs(value)}</span>
            </div>`).join('')}
        </div></div>
      </div>
      <h3>指标口径对比</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>指标</th><th>A（基线）</th><th>B（候选）</th><th>Δ (B − A)</th></tr></thead>
          <tbody>${tableRows}</tbody>
        </table>
      </div>
      <h3>用例级 diff</h3>
      ${categoriesHtml || '<div class="empty">两个 run 没有可比的结果</div>'}
    </div>
  `;
  bindExpandable(container);
}

async function runCompare(event) {
  event.preventDefault();
  const a = $('#compare-a').value;
  const b = $('#compare-b').value;
  if (!a || !b) {
    toast('请先选择两个已完成的 run', true);
    return;
  }
  if (a === b) {
    toast('A 和 B 不能是同一个 run', true);
    return;
  }
  try {
    const data = await api(`/api/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
    state.compareData = data;
    renderCompare(data);
    toast(`对比完成：修复 ${data.counts.fixed}，回归 ${data.counts.regressed}，仍失败 ${data.counts.still_failed}`);
  } catch (error) {
    toast('对比失败：' + error.message, true);
  }
}

/* ------------------------------------------------------------------ *
 * init
 * ------------------------------------------------------------------ */

async function init() {
  $$('.tab').forEach((tab) => tab.addEventListener('click', () => switchTab(tab.dataset.tab)));
  $('#dataset-form').addEventListener('submit', createDataset);
  $('#import-form').addEventListener('submit', importCases);
  $('#import-file').addEventListener('change', async (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    const text = await file.text();
    $('#import-form textarea[name="text"]').value = text;
    toast(`已读取文件 ${file.name}（${text.length} 字符）`);
  });
  $('#target-form').addEventListener('submit', createTarget);
  $('#target-kind').addEventListener('change', updateTargetTemplate);
  $('#probe-form').addEventListener('submit', probeTarget);
  $('#run-form').addEventListener('submit', submitRun);
  $('#cancel-run').addEventListener('click', cancelRun);
  $('#compare-form').addEventListener('submit', runCompare);

  updateTargetTemplate();
  await loadStats();
  await Promise.all([loadDatasets(), loadTargets()]);
  await refreshRuns();
  refreshCompareOptions();
  setInterval(loadStats, 15000);
}

document.addEventListener('DOMContentLoaded', () => {
  init().catch((error) => toast('初始化失败：' + error.message, true));
});
