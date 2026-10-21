const state = {
  session: null,
  metrics: [],
  overview: null,
  sync: null,
  selectedMetric: null,
  records: [],
  csrf: "",
  next: (() => {
    const value = new URLSearchParams(window.location.search).get("next") || "";
    return value.startsWith("/") && !value.startsWith("//") ? value : "";
  })(),
};

const $ = (selector) => document.querySelector(selector);
const loginView = $("#login-view");
const appView = $("#app-view");

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
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

function formatValue(record) {
  if (record.value === null || record.value === undefined) return "结构化事件";
  const number = Number(record.value);
  const text = Number.isFinite(number) ? (Math.abs(number) >= 100 ? number.toFixed(0) : number.toFixed(2).replace(/\.00$/, "")) : String(record.value);
  return `${text}${record.unit ? ` ${record.unit}` : ""}`;
}

function attributeSummary(attributes) {
  if (!attributes || typeof attributes !== "object") return "—";
  const entries = Object.entries(attributes).filter(([key]) => !["date", "dataCreatedTimestamp", "startTimestamp", "modifiedTimestamp", "modifiedTime"].includes(key)).slice(0, 3);
  if (!entries.length) return "—";
  return entries.map(([key, value]) => `${key}: ${typeof value === "object" ? JSON.stringify(value) : value}`).join(" · ");
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
  select.innerHTML = `<option value="">全部分类</option>${Object.entries(categories).map(([key, value]) => `<option value="${escapeHtml(key)}">${escapeHtml(value)}</option>`).join("")}`;
}

function renderMetricList() {
  const query = $("#metric-search").value.trim().toLowerCase();
  const category = $("#category-filter").value;
  const filtered = state.metrics.filter((metric) => {
    const matchesText = !query || `${metric.name} ${metric.key}`.toLowerCase().includes(query);
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

async function selectMetric(key) {
  const metric = state.metrics.find((item) => item.key === key);
  if (!metric) return;
  state.selectedMetric = key;
  renderMetricList();
  const status = (state.sync?.metrics || []).find((item) => item.metric_key === key);
  $("#selected-name").textContent = metric.name;
  $("#selected-meta").textContent = `${metric.category_name || metric.category} · ${metric.kind} · ${metric.unit || "单位未指定"} · ${metric.caveat || "按源端字段展示"}`;
  const badge = $("#selected-status");
  badge.textContent = metric.available ? statusText(status?.status || "queued") : "未接入";
  badge.className = `status-badge ${status?.status || ""}`;
  if (!metric.available) {
    renderRecords([]);
    drawChart([]);
    return;
  }
  try {
    const period = state.overview?.period || {};
    const query = new URLSearchParams({ metric_key: key, page_size: "200" });
    if (period.start_date) query.set("start_date", period.start_date);
    if (period.end_date) query.set("end_date", period.end_date);
    const data = await api(`/api/records?${query}`);
    state.records = data.records || [];
    renderRecords(state.records);
    drawChart(state.records);
  } catch (error) {
    if (error.message !== "LOGIN_REQUIRED") showNotice(error.message, "error");
  }
}

function renderRecords(records) {
  $("#record-count").textContent = `${records.length} 条${records.length >= 200 ? " · 已达当前页上限" : ""}`;
  const body = $("#record-body");
  if (!records.length) {
    body.innerHTML = `<tr><td colspan="5" class="empty-cell">当前周期暂无记录</td></tr>`;
    return;
  }
  const metricNames = Object.fromEntries(state.metrics.map((metric) => [metric.key, metric.name]));
  body.innerHTML = records.map((record) => `<tr>
    <td>${escapeHtml(record.local_date || formatDate(record.recorded_at))}</td>
    <td>${escapeHtml(metricNames[record.metric_key] || record.metric_key)}</td>
    <td>${escapeHtml(formatValue(record))}</td>
    <td>${escapeHtml(record.record_kind || "event")}</td>
    <td title="${escapeHtml(JSON.stringify(record.attributes || {}))}">${escapeHtml(attributeSummary(record.attributes))}</td>
  </tr>`).join("");
}

function drawChart(records) {
  const canvas = $("#trend-chart");
  const empty = $("#chart-empty");
  const values = records.filter((record) => typeof record.value === "number" && Number.isFinite(record.value)).slice().sort((a, b) => Date.parse(b.recorded_at || b.local_date) - Date.parse(a.recorded_at || a.local_date)).reverse();
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  const width = Math.max(320, Math.floor(rect.width || 900));
  const height = 300;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  ctx.clearRect(0, 0, width, height);
  if (!values.length) {
    canvas.classList.add("is-hidden");
    empty.classList.remove("is-hidden");
    return;
  }
  canvas.classList.remove("is-hidden");
  empty.classList.add("is-hidden");
  const pad = { left: 44, right: 20, top: 20, bottom: 36 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const min = Math.min(...values.map((record) => record.value));
  const max = Math.max(...values.map((record) => record.value));
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
    const label = (high - (high - low) * i / 4).toFixed(1).replace(/\.0$/, "");
    ctx.fillText(label, 4, yy + 4);
  }
  ctx.beginPath();
  values.forEach((record, index) => { const xx = x(index); const yy = y(record.value); index ? ctx.lineTo(xx, yy) : ctx.moveTo(xx, yy); });
  ctx.lineTo(x(values.length - 1), pad.top + plotH); ctx.lineTo(x(0), pad.top + plotH); ctx.closePath();
  ctx.fillStyle = "rgba(28, 140, 107, .10)"; ctx.fill();
  ctx.beginPath();
  values.forEach((record, index) => { const xx = x(index); const yy = y(record.value); index ? ctx.lineTo(xx, yy) : ctx.moveTo(xx, yy); });
  ctx.strokeStyle = "#1c8c6b"; ctx.lineWidth = 2.5; ctx.stroke();
  values.forEach((record, index) => { ctx.beginPath(); ctx.arc(x(index), y(record.value), 3.5, 0, Math.PI * 2); ctx.fillStyle = "#fff"; ctx.fill(); ctx.strokeStyle = "#1c8c6b"; ctx.lineWidth = 2; ctx.stroke(); });
  ctx.fillStyle = "#6a827a";
  const labels = values.length > 6 ? [values[0], values[Math.floor(values.length / 2)], values[values.length - 1]] : values;
  labels.forEach((record) => { const index = values.indexOf(record); ctx.fillText(formatDate(record.local_date || record.recorded_at), x(index) - 18, height - 10); });
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
window.addEventListener("resize", () => { if (state.records.length) drawChart(state.records); });
checkSession();