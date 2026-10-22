const state = {
  session: null,
  metrics: [],
  overview: null,
  sync: null,
  selectedMetric: null,
  records: [],
  hasMore: false,
  nextCursor: null,
  chartFields: [],
  chartField: "",
  chartPoints: [],
  selectedPointIndex: -1,
  metricRequestId: 0,
  csrf: "",
  next: (() => {
    const value = new URLSearchParams(window.location.search).get("next") || "";
    return value.startsWith("/") && !value.startsWith("//") ? value : "";
  })(),
};

const $ = (selector) => document.querySelector(selector);
const loginView = $("#login-view");
const appView = $("#app-view");
const FIELD_LABELS = {
  date: "日期", dataCreatedTimestamp: "记录创建时间", startTimestamp: "开始时间", endTimestamp: "结束时间",
  modifiedTimestamp: "云端修改时间", modifiedTime: "云端修改时间", measurementTime: "测量时间", timestamp: "时间",
  steps: "步数", totalSteps: "总步数", distance: "距离", totalDistance: "总距离", calories: "消耗热量",
  totalCalories: "总消耗", duration: "时长", totalDuration: "总时长", heartRate: "心率", avgHeartRate: "平均心率",
  heartRateValue: "心率读数", bloodOxygen: "血氧", bloodOxygenSaturationValue: "血氧饱和度", respiratoryRate: "呼吸率",
  breathRate: "呼吸率", stressValue: "压力值", stress: "压力值", heartRateVarValue: "心率变异性（HRV）",
  hrv: "心率变异性（HRV）", rmssd: "RMSSD", sdnn: "SDNN", sleepIndex: "睡眠指数", sourceType: "来源类型",
  sportMode: "运动模式", deviceId: "设备 ID", deviceSn: "设备序列号", recordId: "记录 ID", status: "状态",
  type: "类型", score: "评分", value: "数值", ear: "耳别", frequency: "频率", level: "等级 / 读数",
  average: "平均值", min: "最小值", max: "最大值", count: "次数", goal: "目标", achievement: "成就",
};
const TECHNICAL_FIELD = /(timestamp|modifiedtime|recordid|deviceid|devicesn|userid|timeoffset|^id$|^version$|^date$|^time$|^sourcekey$)/i;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;", "\"":"&quot;", "'":"&#39;"}[char]));
}

function showNotice(message, kind = "") {
  const node = $("#notice");
  node.textContent = message;
  node.className = `notice ${kind}`;
  if (!message) node.classList.add("is-hidden");
}

function statusText(status) {
  return ({queued:"排队中", running:"同步中", backfilling:"历史回填", success:"已完成", failed:"失败", partial:"部分成功", blocked_auth:"等待 OPPO 认证", blocked_capability:"未接入"}[status] || status || "未知");
}

function formatDate(value) {
  if (!value) return "—";
  return String(value).slice(0, 10);
}

function formatNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return Math.abs(number) >= 100 ? number.toLocaleString("zh-CN", { maximumFractionDigits: 2 }) : number.toFixed(2).replace(/\.00$/, "");
}

function formatValue(record) {
  if (record.value === null || record.value === undefined) {
    const count = record.attribute_count ?? Object.keys(record.attributes || {}).length;
    return count ? `多字段记录 · ${count} 项` : "结构化记录";
  }
  return `${formatNumber(record.value)}${record.unit ? ` ${record.unit}` : ""}`;
}

function fieldLabel(key) {
  if (FIELD_LABELS[key]) return FIELD_LABELS[key];
  return String(key).replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/[_-]+/g, " ").replace(/^./, (letter) => letter.toUpperCase());
}

function attributeEntries(attributes) {
  return attributes && typeof attributes === "object" && !Array.isArray(attributes) ? Object.entries(attributes) : [];
}

function displayAttributeValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function renderAttributeList(attributes) {
  const entries = attributeEntries(attributes);
  if (!entries.length) return `<p class="no-attributes">源端没有返回可展开的字段。</p>`;
  return `<dl class="attribute-list">${entries.map(([key, value]) => `
    <div class="attribute-entry"><dt>${escapeHtml(fieldLabel(key))}<code>${escapeHtml(key)}</code></dt>
      <dd>${typeof value === "object" && value !== null ? `<pre>${escapeHtml(displayAttributeValue(value))}</pre>` : escapeHtml(displayAttributeValue(value))}</dd></div>`).join("")}</dl>`;
}

function attributeSummary(attributes) {
  const all = attributeEntries(attributes);
  const useful = all.filter(([key]) => !TECHNICAL_FIELD.test(key));
  const entries = (useful.length ? useful : all).slice(0, 3);
  if (!entries.length) return "源端未提供字段";
  return entries.map(([key, value]) => {
    const raw = typeof value === "object" && value !== null ? "复合内容" : displayAttributeValue(value);
    const text = raw.length > 72 ? `${raw.slice(0, 69)}…` : raw;
    return `${fieldLabel(key)}：${text}`;
  }).join(" · ");
}

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options, headers: { "Accept": "application/json", ...(options.headers || {}) } });
  let body = null;
  try { body = await response.json(); } catch { body = null; }
  if (response.status === 401) {
    showLogin();
    throw new Error("LOGIN_REQUIRED");
  }
  if (!response.ok) {
    const message = body?.error?.message || body?.detail || "请求失败";
    throw new Error(message);
  }
  return body;
}

function showLogin() {
  loginView.classList.remove("is-hidden");
  appView.classList.add("is-hidden");
  $("#password").value = "";
}

function showApp() {
  loginView.classList.add("is-hidden");
  appView.classList.remove("is-hidden");
  $("#user-label").textContent = state.session?.username || "";
}

async function checkSession() {
  try {
    const session = await api("/api/session");
    if (session.authenticated) {
      state.session = session;
      state.csrf = session.csrf_token;
      showApp();
      await loadDashboard();
    } else {
      showLogin();
    }
  } catch (error) {
    showLogin();
  }
}

async function login(event) {
  event.preventDefault();
  const errorNode = $("#login-error");
  errorNode.textContent = "";
  const button = event.submitter || $("#login-form button");
  button.disabled = true;
  try {
    const body = await api("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: $("#username").value, password: $("#password").value }) });
    state.session = body;
    state.csrf = body.csrf_token;
    if (state.next) {
      window.location.href = state.next;
      return;
    }
    showApp();
    await loadDashboard();
  } catch (error) {
    errorNode.textContent = error.message === "LOGIN_NOT_CONFIGURED" ? "服务端尚未配置网页登录密码" : "登录失败，请检查用户名和密码";
  } finally {
    button.disabled = false;
  }
}

async function logout() {
  try { await api("/api/logout", { method: "POST", headers: { ["X-CSRF-Token"]: state.csrf } }); } catch { /* session can already be expired */ }
  state.session = null;
  state.csrf = "";
  showLogin();
}

async function loadDashboard() {
  showNotice("正在读取服务器缓存…");
  try {
    const days = $("#days-select").value;
    const [overview, metrics, sync] = await Promise.all([
      api(`/api/health/overview?days=${encodeURIComponent(days)}`),
      api("/api/metrics"),
      api("/api/sync/status"),
    ]);
    state.overview = overview;
    state.metrics = metrics.metrics || [];
    state.sync = sync;
    renderOverview();
    renderCategories(metrics.categories || {});
    renderMetricList();
    renderSync(sync);
    if (!state.selectedMetric || !state.metrics.some((metric) => metric.key === state.selectedMetric)) {
      const first = state.metrics.find((metric) => metric.available) || state.metrics[0];
      if (first) await selectMetric(first.key);
    } else {
      await selectMetric(state.selectedMetric);
    }
    showNotice(overview.records_in_window ? "缓存读取完成" : "当前周期暂无标准化记录", overview.records_in_window ? "" : "warning");
  } catch (error) {
    if (error.message !== "LOGIN_REQUIRED") showNotice(error.message, "error");
  }
}

function renderOverview() {
  const overview = state.overview;
  const sync = overview?.sync || state.sync || {};
  const covered = Object.keys(overview?.coverage || {}).length;
  const total = state.metrics.filter((metric) => metric.available).length;
  $("#summary-records").textContent = String(overview?.records_in_window ?? 0);
  $("#summary-period").textContent = `${formatDate(overview?.period?.start_date)} — ${formatDate(overview?.period?.end_date)}`;
  $("#summary-metrics").textContent = `${covered} / ${total}`;
  $("#summary-sync").textContent = statusText(sync.overall_status);
  $("#summary-sync-detail").textContent = `${sync.interval_minutes || 10} 分钟计划刷新`;
  $("#summary-cache").textContent = `${state.metrics.filter((metric) => metric.count > 0).length} 项有缓存`;
}

function renderCategories(categories) {
  const select = $("#category-filter");
  const selected = select.value;
  select.innerHTML = `<option value="">全部分类</option>${Object.entries(categories).map(([key, value]) => `<option value="${escapeHtml(key)}">${escapeHtml(value)}</option>`).join("")}`;
  if (categories[selected]) select.value = selected;
}

function renderMetricList() {
  const query = $("#metric-search").value.trim().toLowerCase();
  const category = $("#category-filter").value;
  const filtered = state.metrics.filter((metric) => {
    const matchesText = !query || `${metric.name} ${metric.key} ${metric.description || ""}`.toLowerCase().includes(query);
    return matchesText && (!category || metric.category === category);
  });
  $("#metric-count").textContent = `${filtered.length} 项`;
  $("#metric-list").innerHTML = filtered.length ? filtered.map((metric) => `
    <button class="metric-item ${metric.key === state.selectedMetric ? "selected" : ""}" data-metric="${escapeHtml(metric.key)}" ${metric.available ? "" : "disabled"}>
      <span><span class="metric-name">${escapeHtml(metric.name)}</span><span class="metric-key">${escapeHtml(metric.key)}${metric.available ? "" : " · 未接入"}</span></span>
      <span class="metric-number">${metric.count || 0}</span>
    </button>`).join("") : `<div class="loading-line">没有匹配的指标</div>`;
  document.querySelectorAll("[data-metric]").forEach((node) => node.addEventListener("click", () => selectMetric(node.dataset.metric)));
}

function clearPointDetail(message = "点击图中的点，可查看该条记录的精确数值和完整字段。") {
  $("#chart-point-detail").innerHTML = `<span>${escapeHtml(message)}</span>`;
}

async function selectMetric(key) {
  const metric = state.metrics.find((item) => item.key === key);
  if (!metric) return;
  const requestId = ++state.metricRequestId;
  state.selectedMetric = key;
  state.records = [];
  state.hasMore = false;
  state.nextCursor = null;
  state.selectedPointIndex = -1;
  renderMetricList();
  const status = (state.sync?.metrics || []).find((item) => item.metric_key === key);
  $("#selected-name").textContent = metric.name;
  $("#selected-meta").textContent = `${metric.category_name || metric.category} · ${metric.kind_label || metric.kind} · ${metric.unit || "字段单位以源端为准"}`;
  const fieldHints = metric.field_hints?.length ? ` 常见字段线索（以实际返回为准）：${metric.field_hints.join("、")}。` : "";
  $("#selected-description").textContent = `${metric.description || metric.kind_explanation || "源端字段原样保留；展开每条记录查看完整结构。"}${fieldHints}`;
  const badge = $("#selected-status");
  badge.textContent = metric.available ? statusText(status?.status || "queued") : "未接入";
  badge.className = `status-badge ${status?.status || ""}`;
  $("#chart-field-wrap").classList.add("is-hidden");
  $("#chart-tooltip").classList.add("is-hidden");
  clearPointDetail(metric.available ? "正在读取记录…" : "该数据集当前未接入。");
  $("#load-more-button").classList.add("is-hidden");
  if (!metric.available) {
    renderRecords([]);
    configureChartFields([]);
    drawChart();
    return;
  }
  try {
    const data = await fetchMetricPage(key);
    if (requestId !== state.metricRequestId) return;
    state.records = data.records || [];
    state.hasMore = Boolean(data.has_more);
    state.nextCursor = data.next_cursor || null;
    renderRecords();
    configureChartFields(state.records);
    drawChart();
  } catch (error) {
    if (error.message !== "LOGIN_REQUIRED") showNotice(error.message, "error");
  }
}

async function fetchMetricPage(key, cursor = null) {
  const period = state.overview?.period || {};
  const query = new URLSearchParams({ metric_key: key, page_size: "200" });
  if (period.start_date) query.set("start_date", period.start_date);
  if (period.end_date) query.set("end_date", period.end_date);
  if (cursor) query.set("cursor", cursor);
  return api(`/api/records?${query}`);
}

async function loadMoreRecords() {
  if (!state.hasMore || !state.nextCursor) return;
  const button = $("#load-more-button");
  button.disabled = true;
  button.textContent = "正在读取…";
  const key = state.selectedMetric;
  const requestId = state.metricRequestId;
  try {
    const data = await fetchMetricPage(key, state.nextCursor);
    if (requestId !== state.metricRequestId) return;
    state.records.push(...(data.records || []));
    state.hasMore = Boolean(data.has_more);
    state.nextCursor = data.next_cursor || null;
    renderRecords();
    configureChartFields(state.records);
    drawChart();
  } catch (error) {
    if (error.message !== "LOGIN_REQUIRED") showNotice(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = "加载更早记录";
  }
}

function renderRecords() {
  const records = state.records;
  $("#record-count").textContent = `${records.length} 条${state.hasMore ? " · 还有更早记录" : ""}`;
  const body = $("#record-body");
  $("#load-more-button").classList.toggle("is-hidden", !state.hasMore);
  if (!records.length) {
    body.innerHTML = `<tr><td colspan="6" class="empty-cell">当前周期暂无记录</td></tr>`;
    return;
  }
  const metricNames = Object.fromEntries(state.metrics.map((metric) => [metric.key, metric.name]));
  body.innerHTML = records.map((record) => {
    const attrs = record.attributes || {};
    const entries = attributeEntries(attrs);
    const kind = record.record_kind_label || state.metrics.find((metric) => metric.key === record.metric_key)?.kind_label || "结构化记录";
    const id = escapeHtml(record.record_id);
    return `<tr class="record-row" data-record="${id}">
      <td>${escapeHtml(record.local_date || formatDate(record.recorded_at))}</td>
      <td>${escapeHtml(metricNames[record.metric_key] || record.display_name || record.metric_key)}</td>
      <td>${escapeHtml(formatValue(record))}</td>
      <td><span class="record-kind">${escapeHtml(kind)}</span></td>
      <td>${escapeHtml(attributeSummary(attrs))}</td>
      <td><details class="record-details"><summary>查看 ${entries.length || "源端"} 项字段</summary>${renderAttributeList(attrs)}</details></td>
    </tr>`;
  }).join("");
}

function flattenNumericFields(value, prefix = "", result = new Map()) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return result;
  Object.entries(value).forEach(([key, child]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    if (TECHNICAL_FIELD.test(key)) return;
    if (typeof child === "number" && Number.isFinite(child) && !/(^|[._])(type|status|flag|code)$/i.test(key)) {
      result.set(path, (result.get(path) || 0) + 1);
    } else if (child && typeof child === "object" && !Array.isArray(child)) {
      flattenNumericFields(child, path, result);
    }
  });
  return result;
}

function getPathValue(record, path) {
  const separator = path.indexOf("::");
  if (separator >= 0) {
    if (record.metric_key !== path.slice(0, separator)) return undefined;
    path = path.slice(separator + 2);
  }
  if (path === "__value") return record.value;
  return path.split(".").reduce((value, key) => value && typeof value === "object" ? value[key] : undefined, record.attributes);
}

function pathLabel(path) {
  return path.split(".").map((key) => fieldLabel(key)).join(" / ");
}

function configureChartFields(records) {
  const options = new Map();
  const scalarCount = records.filter((record) => typeof record.value === "number" && Number.isFinite(record.value)).length;
  const composite = state.metrics.find((metric) => metric.key === state.selectedMetric)?.kind === "composite";
  if (scalarCount && !composite) options.set("__value", { label: "标准化数值", count: scalarCount });
  records.forEach((record) => {
    const sourceName = state.metrics.find((metric) => metric.key === record.metric_key)?.name || record.metric_key;
    const prefix = composite ? `${record.metric_key}::` : "";
    if (composite && typeof record.value === "number" && Number.isFinite(record.value)) {
      const key = `${record.metric_key}::__value`;
      options.set(key, { label: `${sourceName} · 标准化数值`, count: (options.get(key)?.count || 0) + 1 });
    }
    flattenNumericFields(record.attributes).forEach((count, path) => {
      const key = `${prefix}${path}`;
      const label = composite ? `${sourceName} · ${pathLabel(path)}` : pathLabel(path);
      options.set(key, { label, count: (options.get(key)?.count || 0) + 1 });
    });
  });
  state.chartFields = [...options.entries()].map(([key, item]) => ({ key, ...item }));
  const wrapper = $("#chart-field-wrap");
  const select = $("#chart-field");
  const previous = select.value;
  wrapper.classList.toggle("is-hidden", !state.chartFields.length);
  if (!state.chartFields.length) {
    select.innerHTML = "";
    state.chartField = "";
    return;
  }
  const hasStructuredOptions = state.chartFields.some((field) => field.key !== "__value" && !field.key.endsWith("::__value"));
  select.innerHTML = `${hasStructuredOptions && state.chartFields.length > 1 ? `<option value="">选择一个字段…</option>` : ""}${state.chartFields.map((field) => `<option value="${escapeHtml(field.key)}">${escapeHtml(field.label)} · ${field.count} 条</option>`).join("")}`;
  if (state.chartFields.some((field) => field.key === previous)) select.value = previous;
  else if (scalarCount && !composite) select.value = "__value";
  else if (state.chartFields.length === 1) select.value = state.chartFields[0].key;
  else select.value = "";
  state.chartField = select.value;
}

function chartUsesStandardValue() {
  return state.chartField === "__value" || state.chartField.endsWith("::__value");
}

function getChartValue(record) {
  const value = getPathValue(record, state.chartField);
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function drawChart() {
  const canvas = $("#trend-chart");
  const empty = $("#chart-empty");
  const tooltip = $("#chart-tooltip");
  const field = state.chartFields.find((item) => item.key === state.chartField);
  const values = field ? state.records.map((record) => ({ record, value: getChartValue(record) })).filter((item) => item.value !== null).sort((a, b) => Date.parse(a.record.recorded_at || a.record.local_date) - Date.parse(b.record.recorded_at || b.record.local_date)) : [];
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  const width = Math.max(320, Math.floor(rect.width || 900));
  const height = 300;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  state.chartPoints = [];
  tooltip.classList.add("is-hidden");
  $("#chart-caption").textContent = field ? `${field.label} · ${values.length} 个数值点${chartUsesStandardValue() && state.metrics.find((metric) => metric.key === state.selectedMetric)?.unit ? ` · ${state.metrics.find((metric) => metric.key === state.selectedMetric).unit}` : ""} · 点击点看精确值` : "结构化统计可选择数值字段；不会擅自把不同指标混成一条线";
  if (!field || !values.length) {
    canvas.classList.add("is-hidden");
    empty.classList.remove("is-hidden");
    clearPointDetail(field ? "所选字段暂无数值。" : "该记录没有可绘制的数值字段；全部源字段仍可在下方展开查看。");
    return;
  }
  canvas.classList.remove("is-hidden");
  empty.classList.add("is-hidden");
  const pad = { left: 58, right: 20, top: 24, bottom: 42 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const min = Math.min(...values.map((item) => item.value));
  const max = Math.max(...values.map((item) => item.value));
  const range = max - min || Math.max(Math.abs(max) * .15, 1);
  const low = min - range * .1;
  const high = max + range * .1;
  const x = (index) => pad.left + (values.length === 1 ? plotW / 2 : index * plotW / (values.length - 1));
  const y = (value) => pad.top + (high - value) * plotH / (high - low);
  ctx.font = "11px system-ui";
  ctx.strokeStyle = "#e2ece8";
  ctx.fillStyle = "#6a827a";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const yy = pad.top + plotH * i / 4;
    ctx.beginPath(); ctx.moveTo(pad.left, yy); ctx.lineTo(width - pad.right, yy); ctx.stroke();
    const label = formatNumber(high - (high - low) * i / 4);
    ctx.fillText(label, 4, yy + 4);
  }
  ctx.beginPath();
  values.forEach((item, index) => { const xx = x(index); const yy = y(item.value); index ? ctx.lineTo(xx, yy) : ctx.moveTo(xx, yy); });
  ctx.lineTo(x(values.length - 1), pad.top + plotH); ctx.lineTo(x(0), pad.top + plotH); ctx.closePath();
  ctx.fillStyle = "rgba(28, 140, 107, .10)"; ctx.fill();
  ctx.beginPath();
  values.forEach((item, index) => { const xx = x(index); const yy = y(item.value); index ? ctx.lineTo(xx, yy) : ctx.moveTo(xx, yy); });
  ctx.strokeStyle = "#1c8c6b"; ctx.lineWidth = 2.5; ctx.stroke();
  state.chartPoints = values.map((item, index) => ({ ...item, index, x: x(index), y: y(item.value), label: field.label }));
  state.chartPoints.forEach((point, index) => {
    ctx.beginPath(); ctx.arc(point.x, point.y, index === state.selectedPointIndex ? 6 : 4, 0, Math.PI * 2);
    ctx.fillStyle = index === state.selectedPointIndex ? "#1c8c6b" : "#fff"; ctx.fill();
    ctx.strokeStyle = "#1c8c6b"; ctx.lineWidth = 2; ctx.stroke();
  });
  ctx.fillStyle = "#6a827a";
  const labelIndexes = values.length > 6 ? [...new Set([0, Math.floor(values.length / 2), values.length - 1])] : values.map((_, index) => index);
  labelIndexes.forEach((index) => {
    const label = formatDate(values[index].record.local_date || values[index].record.recorded_at);
    ctx.fillText(label, Math.max(0, Math.min(width - 70, x(index) - 22)), height - 10);
  });
  if (state.selectedPointIndex >= state.chartPoints.length) state.selectedPointIndex = -1;
  if (state.selectedPointIndex < 0) clearPointDetail();
}

function nearestChartPoint(event) {
  const canvas = $("#trend-chart");
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  let nearest = null;
  let distance = 15;
  state.chartPoints.forEach((point, index) => {
    const current = Math.hypot(point.x - x, point.y - y);
    if (current < distance) { nearest = { ...point, index }; distance = current; }
  });
  return nearest;
}

function showChartTooltip(point, event) {
  const tooltip = $("#chart-tooltip");
  if (!point) {
    tooltip.classList.add("is-hidden");
    $("#trend-chart").style.cursor = "default";
    return;
  }
  const recordDate = point.record.local_date || formatDate(point.record.recorded_at);
  const unit = chartUsesStandardValue() ? point.record.unit : "";
  tooltip.textContent = `${recordDate} · ${point.label}：${formatNumber(point.value)}${unit ? ` ${unit}` : ""} · 点击查看详情`;
  tooltip.classList.remove("is-hidden");
  const stage = $(".chart-stage").getBoundingClientRect();
  const canvas = $("#trend-chart").getBoundingClientRect();
  tooltip.style.left = `${Math.max(8, Math.min(stage.width - 290, event.clientX - stage.left + 12))}px`;
  tooltip.style.top = `${Math.max(4, Math.min(stage.height - 48, event.clientY - stage.top - 28))}px`;
  canvas && ($("#trend-chart").style.cursor = "pointer");
}

function selectChartPoint(point) {
  if (!point) return;
  state.selectedPointIndex = point.index;
  drawChart();
  const record = point.record;
  const dateText = record.local_date || formatDate(record.recorded_at);
  const unit = chartUsesStandardValue() ? record.unit : "";
  const value = `${formatNumber(point.value)}${unit ? ` ${unit}` : ""}`;
  $("#chart-point-detail").innerHTML = `<div class="point-reading"><span>${escapeHtml(dateText)} · ${escapeHtml(point.label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(record.record_kind_label || "源端记录")}</small></div>${renderAttributeList(record.attributes || {})}`;
}

function renderSync(sync) {
  const rows = sync?.metrics || [];
  $("#sync-updated").textContent = `${statusText(sync?.overall_status)} · 每 ${sync?.interval_minutes || 10} 分钟`;
  $("#sync-table").innerHTML = `<div class="sync-row sync-header"><span>指标</span><span>状态</span><span>缓存行</span><span>最近成功</span><span>错误 / 下一次计划</span></div>` + rows.map((row) => `<div class="sync-row">
    <span class="sync-name">${escapeHtml(row.name || row.metric_key)}<span class="sync-sub">${escapeHtml(row.metric_key)}</span></span>
    <span><span class="sync-state ${escapeHtml(row.status)}">${escapeHtml(statusText(row.status))}</span></span>
    <span>${escapeHtml(row.rows_cached ?? 0)}</span>
    <span>${escapeHtml(row.last_success_at || "—")}</span>
    <span>${escapeHtml(row.last_error_message || row.next_due_at || "—")}</span>
  </div>`).join("");
}

async function startSync() {
  const button = $("#sync-button");
  button.disabled = true;
  showNotice("补同步任务已排队，后台会按限速逐项请求…");
  try {
    const body = state.selectedMetric && state.metrics.find((metric) => metric.key === state.selectedMetric)?.available ? { metrics: [state.selectedMetric], mode: "incremental" } : { mode: "incremental" };
    const result = await api("/api/sync", { method: "POST", headers: { "Content-Type": "application/json", ["X-CSRF-Token"]: state.csrf }, body: JSON.stringify(body) });
    await pollJob(result.job_id);
  } catch (error) {
    if (error.message !== "LOGIN_REQUIRED") showNotice(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function pollJob(jobId) {
  for (let attempt = 0; attempt < 180; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    const job = await api(`/api/sync/jobs/${encodeURIComponent(jobId)}`);
    if (["success", "partial", "failed"].includes(job.status)) {
      showNotice(job.status === "success" ? "补同步完成，正在刷新页面数据。" : `补同步结束：${statusText(job.status)}`, job.status === "success" ? "" : "warning");
      await loadDashboard();
      return;
    }
  }
  showNotice("任务仍在后台运行，可稍后刷新查看状态。", "warning");
}

$("#login-form").addEventListener("submit", login);
$("#logout-button").addEventListener("click", logout);
$("#refresh-button").addEventListener("click", loadDashboard);
$("#sync-button").addEventListener("click", startSync);
$("#days-select").addEventListener("change", loadDashboard);
$("#metric-search").addEventListener("input", renderMetricList);
$("#category-filter").addEventListener("change", renderMetricList);
$("#load-more-button").addEventListener("click", loadMoreRecords);
$("#chart-field").addEventListener("change", () => {
  state.chartField = $("#chart-field").value;
  state.selectedPointIndex = -1;
  clearPointDetail();
  drawChart();
});
$("#trend-chart").addEventListener("pointermove", (event) => showChartTooltip(nearestChartPoint(event), event));
$("#trend-chart").addEventListener("pointerleave", () => showChartTooltip(null));
$("#trend-chart").addEventListener("click", (event) => selectChartPoint(nearestChartPoint(event)));
$("#trend-chart").addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight", "Enter"].includes(event.key) || !state.chartPoints.length) return;
  event.preventDefault();
  if (event.key === "Enter") {
    selectChartPoint(state.chartPoints[Math.max(0, state.selectedPointIndex)]);
  } else {
    const delta = event.key === "ArrowRight" ? 1 : -1;
    const index = Math.max(0, Math.min(state.chartPoints.length - 1, state.selectedPointIndex < 0 ? 0 : state.selectedPointIndex + delta));
    const point = { ...state.chartPoints[index], index };
    selectChartPoint(point);
  }
});
window.addEventListener("resize", drawChart);
checkSession();
