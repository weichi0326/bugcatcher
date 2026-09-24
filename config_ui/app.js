"use strict";

const SCOPES = ["config", "archives", "reports"];
const PAGE_NAMES = {overview: "概览", settings: "配置 / 常用设置", operations: "运行管理", qq: "QQ 管理", config: "配置 / 规则文件", archives: "记录与报告 / 聊天归档", reports: "记录与报告 / 分析报告"};
const OPERATION_NAMES = {diagnose: "环境与连接诊断", deploy: "部署 Python 环境", bridge: "重写 QQ 消息连接配置", "napcat-setup": "下载并安装 QQ 与 NapCat", "napcat-download": "下载 QQ 与 NapCat", "napcat-install": "安装 QQ 与 NapCat"};
const MASTER_CONFIG_PATH = "config/配置主表.json";
const SETTINGS_FIELDS = [
  ["settingAppId", ["feishu_bot", "app_id"]],
  ["settingAppSecret", ["feishu_bot", "app_secret"]],
  ["settingModelProvider", ["models", "text", "provider"]],
  ["settingModelBaseUrl", ["models", "text", "base_url"]],
  ["settingModelApiKey", ["models", "text", "api_key"]],
  ["settingModelName", ["models", "text", "model"]],
  ["settingGroupWhitelist", ["analysis", "qq_game_sentiment", "group_whitelist"]],
  ["settingPushChatIds", ["analysis", "qq_game_sentiment", "feishu_push_chat_ids"]],
];
const state = {
  page: "overview", files: {config: [], archives: [], reports: []},
  fileLoaded: {config: false, archives: false, reports: false},
  fileLoading: {config: false, archives: false, reports: false},
  settingsLoading: false,
  selected: {config: null, archives: null, reports: null},
  fileVersion: {archives: "", reports: ""},
  config: {content: "", etag: null, editable: false, mode: "raw", dirty: false, saving: false, note: ""},
  settings: {master: null, etag: null, dirty: false, saving: false},
  model: {providers: [], models: [], profiles: {}, activeProvider: "", providerRequest: 0, request: 0, contextVersion: 0},
  operations: {service: null, job: null, napcat: null, qq: null, loading: false, requesting: false, error: ""},
  qqSetup: {running: false, message: ""},
  archive: {records: [], total: 0, next: 0, loading: false},
  report: "", reportMode: "rendered", requestId: 0
};

const $ = (id) => document.getElementById(id);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
};

async function api(url, options = {}) {
  const response = await fetch(url, {cache: "no-store", ...options});
  let data;
  try { data = await response.json(); } catch { throw new Error(`服务返回了无法识别的内容（HTTP ${response.status}）`); }
  if (!response.ok || data.ok === false) {
    const detail = Array.isArray(data.errors) ? data.errors.join("；") : data.error || data.message || `请求失败（HTTP ${response.status}）`;
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return data;
}

function toast(message, type = "normal") {
  const item = el("div", `toast ${type}`, message);
  $("toastHost").append(item);
  setTimeout(() => item.remove(), 4500);
}

function confirmLeave() {
  if (state.page === "config" && state.config.saving) {
    toast("文件正在保存，请等待完成", "error");
    return false;
  }
  if (state.page === "settings" && state.settings.saving) {
    settingsStatus("正在保存并应用配置，请等待完成。");
    return false;
  }
  if (state.page === "config" && state.config.dirty && !window.confirm("配置文件有未保存的修改，确定离开吗？")) return false;
  if (state.page === "settings" && state.settings.dirty && !window.confirm("常用设置有未保存的修改，确定离开吗？")) return false;
  return true;
}

function setPage(page) {
  if (!PAGE_NAMES[page] || page === state.page || !confirmLeave()) return;
  if (["config", "archives", "reports"].includes(state.page)) state.requestId += 1;
  if (state.page === "config" && state.config.dirty) {
    state.config.dirty = false;
    state.selected.config = null;
    $("configDocument").classList.add("hidden");
    $("configEmpty").classList.remove("hidden");
    $("saveConfigBtn").disabled = true;
    $("configMeta").textContent = "";
    $("dirtyIndicator").textContent = "";
  }
  if (state.page === "settings" && state.settings.dirty) {
    state.settings.dirty = false;
    state.settings.master = null;
  }
  state.page = page;
  document.querySelector("main").classList.toggle("settings-main", page === "settings" || page === "config");
  document.querySelectorAll(".page").forEach(node => node.classList.toggle("active", node.id === `page-${page}`));
  const navPage = page === "config" ? "settings" : page === "reports" ? "archives" : page;
  document.querySelectorAll(".nav-item").forEach(node => node.classList.toggle("active", node.dataset.page === navPage));
  document.querySelectorAll(".section-tab").forEach(node => node.classList.toggle("active", node.dataset.go === page));
  $("pageCrumb").textContent = PAGE_NAMES[page];
  if (page === "overview") refreshOverview();
  else if (page === "settings") {
    if (!state.settings.master && !state.settingsLoading) loadSettings();
    if (!state.fileLoaded.config && !state.fileLoading.config) loadFiles("config", true);
  }
  else if (page === "operations" || page === "qq") loadOperationsStatus();
  else if (page === "config") {
    if (!state.fileLoaded.config && !state.fileLoading.config) loadFiles("config");
    else if (state.fileLoaded.config) {
      renderFileList("config");
      if (!state.selected.config && state.files.config.length) openFile("config", state.files.config[0]);
    }
  } else loadFiles(page);
}

function localDate(value) {
  if (!value) return "";
  if (typeof value === "number") value = value < 1e11 ? value * 1000 : value;
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString("zh-CN", {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"});
}

function prettySize(value) {
  const size = Number(value);
  if (!Number.isFinite(size)) return "";
  return size < 1024 ? `${size} B` : size < 1048576 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1048576).toFixed(1)} MB`;
}

function pathOf(file) { return String(file.path || ""); }
function nameOf(file) { return String(file.name || pathOf(file).split("/").pop() || "未命名文件"); }

async function loadHealth() {
  try {
    const data = await api("/api/health");
    $("connectionDot").className = "connection-dot online";
    $("connectionText").textContent = "服务已连接";
    return data;
  } catch (error) {
    $("connectionDot").className = "connection-dot offline";
    $("connectionText").textContent = "连接失败";
    return {ok: false, error: error.message};
  }
}

async function loadOverview() {
  const [overviewResult] = await Promise.allSettled([api("/api/overview"), loadHealth()]);
  const setup = $("overviewSetup");
  setup.replaceChildren();
  if (overviewResult.status === "rejected") {
    setup.textContent = `读取配置状态失败：${overviewResult.reason.message}`;
    setup.classList.remove("hidden");
    return;
  }
  const data = overviewResult.value;
  if (data.needs_bootstrap || data.master_exists === false || data.bootstrap_available) {
    setup.append(el("span", "", "尚未创建配置主表，先从示例创建后再启动服务。"));
    const create = el("button", "button button-primary", "从示例创建配置");
    create.addEventListener("click", async () => {
      create.disabled = true;
      try { await api("/api/bootstrap", {method: "POST"}); toast("配置主表已创建", "success"); await loadOverview(); }
      catch (error) { toast(error.message, "error"); create.disabled = false; }
    });
    setup.append(create);
  }
  setup.classList.toggle("hidden", !setup.childNodes.length);
  $("lastUpdated").textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN", {hour: "2-digit", minute: "2-digit"})}`;
}

function refreshOverview() {
  loadOverview();
  loadOperationsStatus();
}

function operationJobRunning(job) {
  return Boolean(job && ["queued", "pending", "running", "starting", "stopping", "in_progress"].includes(String(job.status).toLowerCase()));
}

function renderOperationLines(element, lines, emptyMessage) {
  const entries = Array.isArray(lines) ? lines.map(line => typeof line === "string" ? line : String(line?.message ?? line?.text ?? "")) : [];
  const content = entries.length ? entries.join("\n") : emptyMessage;
  if (element.textContent !== content) {
    const nearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 40;
    element.textContent = content;
    if (nearBottom) element.scrollTop = element.scrollHeight;
  }
  return entries.length;
}

function setServiceIndicator(id, kind, label) {
  const node = $(id);
  node.className = `service-indicator is-${kind}`;
  node.replaceChildren(el("span", "service-indicator-dot"), document.createTextNode(label));
}

function updateQRCode(image, version) {
  const current = String(version);
  const changed = image.dataset.version !== current;
  if (changed) {
    image.dataset.version = current;
    image.dataset.failures = "0";
    image.dataset.retryAt = "0";
  }
  const failures = Number(image.dataset.failures || 0);
  if (failures >= 3) return;
  if (changed || (failures && Date.now() >= Number(image.dataset.retryAt || 0))) {
    image.style.visibility = "hidden";
    image.src = `/api/qq/qrcode?v=${encodeURIComponent(current)}&retry=${failures}`;
    image.dataset.retryAt = String(Date.now() + 10000);
  }
}

function renderOperations() {
  const {service, job, napcat, qq, requesting, error} = state.operations;
  const running = service?.running === true;
  const busy = requesting || operationJobRunning(job) || state.qqSetup.running;
  const mode = service?.mode === "integrated" ? "飞书与 QQ" : service?.mode === "bot" ? "仅机器人" : "未知模式";
  $("operationServiceState").textContent = error ? "状态不可用" : !service ? "正在读取…" : running ? "运行中" : "未运行";
  $("operationServiceDetail").textContent = error || (!service ? "" : running ? `${mode}${service.pid ? ` · PID ${service.pid}` : ""}` : service.stopped_by_user ? "已手动停止" : service.exit_code === undefined || service.exit_code === null ? "" : `上次退出码：${service.exit_code}`);
  const jobName = job ? OPERATION_NAMES[job.name] || String(job.name || "维护操作") : "暂无记录";
  let jobStatus = job ? ({queued: "排队中", pending: "准备中", running: "进行中", starting: "启动中", stopping: "停止中", succeeded: "已完成", success: "已完成", completed: "已完成", failed: "执行失败", error: "执行失败", cancelled: "已取消"}[String(job.status).toLowerCase()] || String(job.status || "状态未知")) : "";
  const issues = Array.isArray(job?.diagnostic?.issues) ? job.diagnostic.issues : [];
  if (job?.name === "diagnose" && job.status === "success") jobStatus = issues.length ? `检测完成，发现 ${issues.length} 项环境问题` : "检测完成，环境正常";
  if (job?.name === "diagnose" && job.status === "failed") jobStatus = "诊断程序执行失败";
  $("operationJobState").textContent = job ? `${jobName} · ${jobStatus}` : "暂无记录";
  const time = job ? localDate(job.finished_at || job.started_at) : "";
  const diagnosisDetail = job?.name === "diagnose" && job.status === "success" && issues.length ? `${issues[0].title}；${issues[0].detail}` : "";
  $("operationJobDetail").textContent = job ? diagnosisDetail || [job.error || "", time, job.status === "failed" && !job.error && job.exit_code !== null ? `退出码 ${job.exit_code}` : ""].filter(Boolean).join(" · ") : "";
  const botReady = running && service?.ready === true;
  const qqRunning = qq?.running === true;
  const qqLoggedIn = qq?.logged_in === true;
  const qrVersion = qq?.qrcode_version;
  $("homeLaunchHint").textContent = qqLoggedIn ? "" : "QQ 尚未登录时，飞书可先运行。";
  setServiceIndicator("homeLaunchState", error ? "off" : botReady ? "on" : running ? "pending" : "off",
    error ? "状态不可用" : botReady ? "运行中" : running ? "启动中" : "未运行");
  setServiceIndicator("homeFeishuBadge", botReady ? "on" : running ? "pending" : "off", botReady ? "进程运行中" : running ? "启动中" : "未运行");
  $("homeFeishuHint").textContent = "";
  setServiceIndicator("homeQQBadge", !napcat?.installed ? "off" : qqLoggedIn ? "on" : qqRunning ? "pending" : "off",
    !napcat?.installed ? "未安装" : qqLoggedIn ? "已登录" : qqRunning ? "等待扫码" : "未运行");
  $("homeQQHint").textContent = qqLoggedIn && qq.account?.user_id ? `QQ ${qq.account.user_id}` : qqRunning && !qrVersion ? "正在获取二维码" : "";
  setServiceIndicator("homeArchiveBadge", botReady ? "on" : running ? "pending" : "off", botReady ? "本地端口已监听" : running ? "启动中" : "未运行");
  $("homeArchiveHint").textContent = botReady && !qqLoggedIn ? "等待 QQ 登录后上报消息" : "";
  $("homeLaunchDetail").textContent = error || (!service ? "" : running ? `${mode}${service.pid ? ` · PID ${service.pid}` : ""}` : service.exit_code != null ? `上次服务退出码：${service.exit_code}` : "");
  $("homePrimary").disabled = !service || busy;
  $("homePrimary").textContent = running ? "停止全部" : "启动全部";
  const overviewAlert = $("overviewAlert");
  overviewAlert.replaceChildren();
  let alertText = "", alertPage = "", alertAction = "";
  if (error) { alertText = error; alertPage = "operations"; alertAction = "查看运行管理"; }
  else if (!running && service?.exit_code != null) { alertText = `机器人上次异常退出（代码 ${service.exit_code}）。`; alertPage = "operations"; alertAction = "查看日志"; }
  else if (job?.status === "failed") { alertText = `${jobName}执行失败。`; alertPage = "operations"; alertAction = "查看维护操作输出"; }
  else if (running && !napcat?.installed) { alertText = "QQ 尚未安装；飞书可以继续运行。"; alertPage = "qq"; alertAction = "安装 QQ"; }
  else if (running && qqRunning && !qqLoggedIn && !qrVersion && Date.now() - new Date(service.started_at).valueOf() > 15000) {
    alertText = "QQ 已启动，但还没有取得扫码二维码。"; alertPage = "qq"; alertAction = "查看 QQ 状态";
  }
  if (alertText) {
    overviewAlert.append(el("span", "", alertText));
    const action = el("button", "button button-secondary", alertAction);
    action.type = "button";
    action.addEventListener("click", () => setPage(alertPage));
    overviewAlert.append(action);
  }
  overviewAlert.classList.toggle("hidden", !alertText);
  setServiceIndicator("botServiceBadge", service?.mode === "bot" && botReady ? "on" : service?.mode === "bot" && running ? "pending" : "off", service?.mode === "bot" && botReady ? "运行中" : service?.mode === "bot" && running ? "启动中" : "未运行");
  const setupJob = operationJobRunning(job) && ["napcat-setup", "napcat-download", "napcat-install"].includes(job?.name);
  const installStatus = napcat?.installed ? "已安装" : setupJob ? job.name === "napcat-download" ? "正在下载" : job.name === "napcat-install" ? "正在安装" : "正在下载并安装" : napcat?.staged ? "已下载，等待安装" : "未安装";
  $("qqInstallHint").classList.toggle("hidden", !!napcat?.installed);
  setServiceIndicator("qqInstallBadge", napcat?.installed ? "on" : setupJob || napcat?.staged ? "pending" : "off", installStatus);
  $("qqVersion").textContent = napcat?.qq_version || (napcat?.installed ? "已安装，版本待识别" : "—");
  $("qqReleaseVersion").textContent = qq?.napcat_version || (napcat?.installed ? "启动后获取" : napcat?.release_version || "—");
  $("qqSetupActions").classList.toggle("hidden", !!napcat?.installed);
  $("qqSetupBtn").disabled = !napcat || napcat.installed || busy;
  $("qqSetupBtn").textContent = state.qqSetup.running || setupJob ? "正在处理…" : napcat?.staged ? "继续安装 QQ" : "下载并安装 QQ";
  $("qqSetupStatus").textContent = napcat?.installed ? "" : state.qqSetup.message || (setupJob ? job.name === "napcat-download" ? "正在下载并校验安装组件…" : job.name === "napcat-install" ? "安装中…" : "正在下载并安装…" : "");
  setServiceIndicator("qqLoginBadge", qqLoggedIn ? "on" : qqRunning ? "pending" : "off", qqLoggedIn ? "已登录" : qqRunning ? "等待扫码" : "未启动");
  setServiceIndicator("qqConnectionBadge", qqLoggedIn && botReady ? "on" : qqRunning ? "pending" : "off", qqLoggedIn && botReady ? "两端已启动" : qqRunning ? botReady ? "等待 QQ 登录" : "接收端未启动" : "QQ 未启动");
  $("qqProcessState").textContent = qqRunning ? "运行中" : "未运行";
  $("qqBotState").textContent = botReady ? "端口已监听" : running ? "启动中" : "未运行";
  const account = qq?.account;
  $("qqNickname").textContent = account?.nickname || "尚未登录";
  $("qqAccountId").textContent = account?.user_id ? `QQ ${account.user_id}` : "";
  const avatar = $("qqAvatar");
  const avatarFallback = $("qqAvatarFallback");
  if (account?.user_id) {
    const avatarUrl = account.avatar_url?.startsWith("https://") ? account.avatar_url : `https://q1.qlogo.cn/g?b=qq&nk=${encodeURIComponent(account.user_id)}&s=100`;
    if (avatar.src !== avatarUrl) {
      avatar.dataset.failedUrl = "";
      avatar.src = avatarUrl;
    }
    const ready = avatar.dataset.failedUrl !== avatarUrl && avatar.complete && avatar.naturalWidth > 0;
    avatar.classList.toggle("hidden", !ready);
    avatarFallback.classList.toggle("hidden", ready);
  } else {
    avatar.removeAttribute("src"); avatar.dataset.failedUrl = "";
    avatar.classList.add("hidden"); avatarFallback.classList.remove("hidden");
  }
  $("qqScanPanel").classList.toggle("hidden", !qrVersion || qqLoggedIn);
  $("homeQrPanel").classList.toggle("hidden", !qrVersion || qqLoggedIn);
  if (qrVersion && !qqLoggedIn) {
    updateQRCode($("qqQrImage"), qrVersion);
    updateQRCode($("homeQrImage"), qrVersion);
  }
  const qrFailed = qrVersion && Number($("qqQrImage").dataset.failures || 0) >= 3;
  $("qqLoginHint").textContent = qqLoggedIn ? "" : qrFailed ? "二维码加载失败，请刷新页面重试。" : qrVersion ? "" : qqRunning ? "二维码生成中…" : "请先在概览启动服务。";
  $("homeQrError").textContent = qrVersion && Number($("homeQrImage").dataset.failures || 0) >= 3 ? "二维码加载失败，请刷新页面重试。" : "";
  $("qqRescanActions").classList.toggle("hidden", !qqLoggedIn || service?.mode !== "integrated" || qq?.externally_started);
  $("qqRescanBtn").disabled = !running || busy;
  const serviceLineCount = renderOperationLines($("operationServiceLog"), service?.lines, running ? "服务正在启动，等待输出。" : "服务尚无输出。");
  $("operationServiceLogHint").textContent = service ? `${running ? "运行中" : "已停止"} · ${serviceLineCount} 行` : "";
  const jobLineCount = renderOperationLines($("operationLog"), job?.lines, job ? "操作尚无输出。" : "暂无输出。");
  $("operationLogHint").textContent = job ? `${jobName} · ${jobLineCount} 行` : "";
  document.querySelectorAll("[data-operation-job]").forEach(button => {
    button.disabled = !service || busy || (button.dataset.operationJob === "deploy" && running);
    if (button.dataset.operationJob === "bridge") button.disabled ||= !qqLoggedIn;
  });
  document.querySelectorAll("[data-operation-start]").forEach(button => {
    button.disabled = !service || busy;
    button.textContent = button.dataset.operationStart === "bot" ? (running ? "切换为仅机器人" : "仅启动机器人") : (running && service.mode !== "integrated" ? "切换为整合运行" : "重新启动全部");
  });
}

async function loadOperationsStatus() {
  if (state.operations.loading || state.operations.requesting) return;
  state.operations.loading = true;
  try {
    const data = await api("/api/operations/status");
    state.operations.service = data.service || {running: false};
    state.operations.job = data.job || null;
    state.operations.napcat = data.napcat || null;
    state.operations.qq = data.qq || null;
    state.operations.error = "";
    if (state.page === "operations" || state.page === "qq") $("lastUpdated").textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN", {hour: "2-digit", minute: "2-digit"})}`;
  } catch (error) {
    state.operations.service = null;
    state.operations.qq = null;
    state.operations.error = `读取失败：${error.message}`;
  } finally {
    state.operations.loading = false;
    renderOperations();
  }
}

async function runOperation(url, body) {
  if (state.operations.requesting || operationJobRunning(state.operations.job) || state.qqSetup.running) return;
  state.operations.requesting = true;
  renderOperations();
  try {
    await api(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    toast("操作已提交，正在更新运行状态", "success");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    state.operations.requesting = false;
    await loadOperationsStatus();
  }
}

async function waitForQQSetupJob(name, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const data = await api("/api/operations/status");
    state.operations.service = data.service || {running: false};
    state.operations.job = data.job || null;
    state.operations.napcat = data.napcat || null;
    state.operations.qq = data.qq || null;
    renderOperations();
    const job = data.job;
    if (job?.name !== name) throw new Error("安装操作已被其他操作替换，请检查运行管理中的维护操作输出。");
    if (!operationJobRunning(job)) {
      if (["success", "succeeded", "completed"].includes(String(job.status).toLowerCase())) return data;
      throw new Error(job.error || `${OPERATION_NAMES[name]}失败，请查看运行管理中的维护操作输出。`);
    }
    await new Promise(resolve => setTimeout(resolve, 2000));
  }
  throw new Error("等待安装任务超时；任务可能仍在运行，请到运行管理查看进度。");
}

async function setupQQ() {
  if (state.qqSetup.running || state.operations.requesting || operationJobRunning(state.operations.job) || state.operations.napcat?.installed) return;
  state.qqSetup.running = true;
  try {
    state.qqSetup.message = "正在下载并安装 QQ 与 NapCat；如出现安装窗口，请按提示完成。";
    renderOperations();
    try {
      await api("/api/operations/job", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: "napcat-setup"})});
      const installed = await waitForQQSetupJob("napcat-setup", 46 * 60 * 1000);
      if (!installed.napcat?.installed) throw new Error("安装已结束，但未检测到 QQ 与 NapCat。请查看运行管理中的维护操作输出。");
    } catch (error) {
      if (error.message !== "未知操作") throw error;
      // 兼容仍在运行、尚未重启的旧版管理服务。
      if (!state.operations.napcat?.staged) {
        state.qqSetup.message = "正在下载并校验 QQ 与 NapCat 安装组件…";
        renderOperations();
        await api("/api/operations/job", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: "napcat-download"})});
        const downloaded = await waitForQQSetupJob("napcat-download", 15 * 60 * 1000);
        if (!downloaded.napcat?.staged && !downloaded.napcat?.installed) throw new Error("下载已结束，但未检测到安装组件。请查看运行管理中的维护操作输出。");
      }
      if (!state.operations.napcat?.installed) {
        state.qqSetup.message = "下载完成，正在启动安装程序；如出现安装窗口，请按提示完成。";
        renderOperations();
        await api("/api/operations/job", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: "napcat-install"})});
        const installed = await waitForQQSetupJob("napcat-install", 31 * 60 * 1000);
        if (!installed.napcat?.installed) throw new Error("安装程序已结束，但未检测到 QQ 与 NapCat。请查看运行管理中的维护操作输出。");
      }
    }
    state.qqSetup.message = "";
    toast("QQ 与 NapCat 安装完成", "success");
  } catch (error) {
    state.qqSetup.message = error.message;
    toast(error.message, "error");
  } finally {
    state.qqSetup.running = false;
    await loadOperationsStatus();
  }
}

function getSetting(object, path) {
  return path.reduce((value, key) => value && typeof value === "object" ? value[key] : undefined, object);
}

function setSetting(object, path, value) {
  let target = object;
  for (const key of path.slice(0, -1)) {
    if (!target[key] || typeof target[key] !== "object" || Array.isArray(target[key])) target[key] = {};
    target = target[key];
  }
  target[path[path.length - 1]] = value;
}

function settingsStatus(message, error = false) {
  const status = $("settingsStatus");
  status.replaceChildren(...(message ? [el("span", "", message)] : []));
  status.classList.toggle("hidden", !message);
  status.classList.toggle("is-error", error);
}

function markSettingsDirty() {
  if (!state.settings.master || state.settings.saving) return;
  state.settings.dirty = true;
  $("settingsSaveHint").textContent = "● 有未保存的修改";
  $("settingsSaveHint").classList.add("dirty");
}

function settingsList(value) {
  return Array.isArray(value) ? value.map(item => String(item ?? "")).join("\n") : "";
}

function listFromInput(id, label, pattern) {
  const values = $(id).value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
  const invalid = values.find(value => !pattern.test(value));
  if (invalid) throw new Error(`${label}中有无效 ID：${invalid}`);
  return [...new Set(values)];
}

function modelStatus(message, error = false) {
  const status = $("modelActionStatus");
  status.textContent = message;
  status.classList.toggle("is-error", error);
}

function clearModelResults() {
  state.model.models = [];
  state.model.request += 1;
  state.model.contextVersion += 1;
  $("modelResults").classList.add("hidden");
  $("modelList").replaceChildren();
  modelStatus("");
}

function modelProfileFromForm() {
  return {
    base_url: $("settingModelBaseUrl").value.trim(),
    api_key: $("settingModelApiKey").value.trim(),
    model: $("settingModelName").value.trim()
  };
}

function storeModelDraft() {
  if (state.model.activeProvider) state.model.profiles[state.model.activeProvider] = {
    ...(state.model.profiles[state.model.activeProvider] || {}), ...modelProfileFromForm()
  };
}

function showModelProvider(provider) {
  const profile = state.model.profiles[provider] || {};
  const preset = state.model.providers.find(item => item.id === provider);
  state.model.activeProvider = provider;
  $("settingModelBaseUrl").value = profile.base_url || (preset && preset.base_url) || "";
  $("settingModelApiKey").value = profile.api_key || "";
  $("settingModelName").value = profile.model || "";
  clearModelResults();
  updateModelTestButton();
}

function updateModelTestButton() {
  $("modelTestBtn").disabled = state.settings.saving || !$("settingModelName").value.trim();
}

function invalidateModelChoice() {
  $("settingModelName").value = "";
  clearModelResults();
  storeModelDraft();
  updateModelTestButton();
  modelStatus("连接信息已更改，请重新获取模型列表并选择分析模型。");
}

function renderProviderOptions() {
  const select = $("settingModelProvider");
  const selected = select.value;
  const options = state.model.providers;
  select.replaceChildren();
  select.append(new Option("选择服务商", ""));
  const known = options.some(provider => provider.id === selected);
  if (selected && !known) select.append(new Option(`${selected}（自定义）`, selected));
  options.forEach(provider => select.append(new Option(provider.label || provider.id, provider.id)));
  select.value = selected;
}

async function loadModelProviders() {
  const request = ++state.model.providerRequest;
  try {
    const data = await api("/api/model/providers");
    if (request !== state.model.providerRequest) return;
    state.model.providers = Array.isArray(data.providers) ? data.providers.filter(item => item && typeof item.id === "string") : [];
    renderProviderOptions();
    if (state.model.activeProvider && !$("settingModelBaseUrl").value.trim()) {
      const preset = state.model.providers.find(item => item.id === state.model.activeProvider);
      if (preset) $("settingModelBaseUrl").value = preset.base_url || "";
    }
  } catch (error) {
    if (request === state.model.providerRequest) modelStatus(`服务商预设加载失败：${error.message}`, true);
  }
}

function currentModelPayload(model = $("settingModelName").value.trim()) {
  return {
    provider: $("settingModelProvider").value.trim(),
    base_url: $("settingModelBaseUrl").value.trim(),
    api_key: $("settingModelApiKey").value.trim(),
    model
  };
}

function modelLatency(data) {
  const value = Number(data.latency_ms);
  return Number.isFinite(value) && value >= 0 ? `${Math.round(value)} ms` : "已响应";
}

function renderModelList() {
  const query = $("modelSearch").value.trim().toLowerCase();
  const matches = state.model.models.filter(item => `${item.id} ${item.name || ""}`.toLowerCase().includes(query));
  const models = matches.slice(0, 200);
  const list = $("modelList");
  list.replaceChildren();
  $("modelResultsCount").textContent = `显示 ${models.length} / ${matches.length} 个匹配模型（总计 ${state.model.models.length}）`;
  if (!models.length) { list.append(el("div", "model-empty", query ? "没有匹配的模型" : "未返回可用模型")); return; }
  models.forEach(item => {
    const row = el("div", "model-row");
    row.classList.toggle("selected", item.id === $("settingModelName").value.trim());
    const name = el("div", "model-name");
    name.append(el("strong", "", item.name || item.id));
    if (item.name && item.name !== item.id) name.append(el("small", "", item.id));
    const result = el("span", "model-row-result");
    const test = el("button", "button button-secondary", "测速");
    test.type = "button";
    test.addEventListener("click", () => testModel(item.id, result, test));
    const select = el("button", "button button-primary", "选为分析模型");
    select.type = "button";
    select.addEventListener("click", () => {
      if (state.settings.saving) return;
      $("settingModelName").value = item.id;
      storeModelDraft();
      $("modelList").querySelectorAll(".model-row").forEach(node => node.classList.toggle("selected", node === row));
      updateModelTestButton();
      markSettingsDirty();
      modelStatus(`已选择 ${item.id}，点击“保存并应用配置”后生效。`);
    });
    row.append(name, result, test, select);
    list.append(row);
  });
}

async function fetchModelList() {
  if (state.settings.saving) return;
  if (!$("settingModelProvider").value || !$("settingModelBaseUrl").value.trim()) {
    modelStatus("请先选择服务商并填写 API 地址。", true);
    return;
  }
  const button = $("modelListBtn");
  clearModelResults();
  const request = ++state.model.request;
  button.disabled = true;
  modelStatus("正在获取模型列表…");
  try {
    const data = await api("/api/model/list", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(currentModelPayload())});
    if (request !== state.model.request) return;
    state.model.models = Array.isArray(data.models) ? data.models.filter(item => item && typeof item.id === "string" && item.id) : [];
    $("modelSearch").value = "";
    $("modelResults").classList.remove("hidden");
    renderModelList();
    modelStatus(`获取到 ${state.model.models.length} 个模型，请按需测速。列表请求耗时 ${modelLatency(data)}。`);
  } catch (error) {
    if (request === state.model.request) modelStatus(`获取模型失败：${error.message}`, true);
  } finally { button.disabled = state.settings.saving; }
}

async function testModel(model, result = $("modelActionStatus"), button = $("modelTestBtn")) {
  if (state.settings.saving) return;
  if (!model) { modelStatus("请先获取模型列表并选择分析模型", true); return; }
  const contextVersion = state.model.contextVersion;
  button.disabled = true;
  result.textContent = "测速中…";
  try {
    const data = await api("/api/model/test", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(currentModelPayload(model))});
    if (contextVersion !== state.model.contextVersion || state.settings.saving) return;
    result.textContent = `${modelLatency(data)} · ${data.preview || "调用成功"}`;
    result.classList.remove("is-error");
  } catch (error) {
    if (contextVersion !== state.model.contextVersion || state.settings.saving) return;
    result.textContent = `测速失败：${error.message}`;
    result.classList.add("is-error");
  } finally { button.disabled = state.settings.saving || (button === $("modelTestBtn") && !$("settingModelName").value.trim()); }
}

async function loadSettings() {
  if (state.settings.saving || state.settingsLoading) return;
  state.settingsLoading = true;
  const hadContent = Boolean(state.settings.master);
  settingsStatus("正在读取配置主表…");
  if (!hadContent) $("settingsForm").classList.add("hidden");
  $("settingsSaveBtn").disabled = true;
  try {
    const data = await api(`/api/file?${new URLSearchParams({scope: "config", path: MASTER_CONFIG_PATH})}`);
    const master = JSON.parse(data.content);
    if (!master || typeof master !== "object" || Array.isArray(master)) throw new Error("配置主表必须是 JSON 对象");
    if (data.editable !== true) throw new Error("配置主表当前只读，无法通过此页面保存");
    state.settings = {master, etag: data.etag, dirty: false, saving: false, pushTargetEdited: false};
    const configuredProvider = String(getSetting(master, ["models", "text", "provider"]) ?? "");
    const savedProfiles = getSetting(master, ["models", "provider_profiles"]);
    state.model.profiles = Object.create(null);
    if (savedProfiles && typeof savedProfiles === "object" && !Array.isArray(savedProfiles)) {
      Object.entries(savedProfiles).forEach(([provider, profile]) => {
        if (profile && typeof profile === "object" && !Array.isArray(profile)) {
          state.model.profiles[provider] = {
            ...profile,
            base_url: String(profile.base_url ?? ""), api_key: String(profile.api_key ?? ""), model: String(profile.model ?? "")
          };
        }
      });
    }
    if (configuredProvider) {
      state.model.profiles[configuredProvider] = {
        ...(state.model.profiles[configuredProvider] || {}),
        base_url: String(getSetting(master, ["models", "text", "base_url"]) ?? ""),
        api_key: String(getSetting(master, ["models", "text", "api_key"]) ?? ""),
        model: String(getSetting(master, ["models", "text", "model"]) ?? "")
      };
    }
    $("settingModelProvider").replaceChildren(new Option(configuredProvider || "选择服务商", configuredProvider));
    SETTINGS_FIELDS.forEach(([id, path]) => {
      if (id.startsWith("settingModel")) return;
      const value = getSetting(master, path);
      $(id).value = Array.isArray(value) ? settingsList(value) : String(value ?? "");
    });
    showModelProvider(configuredProvider);
    const ids = getSetting(master, ["analysis", "qq_game_sentiment", "feishu_push_chat_ids"]);
    $("pushTestChatId").value = Array.isArray(ids) && ids.length ? String(ids[0] ?? "") : "";
    $("settingsSaveHint").textContent = "";
    $("settingsSaveHint").classList.remove("dirty");
    $("settingsSaveBtn").disabled = false;
    $("settingsForm").classList.remove("hidden");
    settingsStatus("");
    loadModelProviders();
    $("lastUpdated").textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN", {hour: "2-digit", minute: "2-digit"})}`;
  } catch (error) {
    settingsStatus(hadContent ? `重新读取失败，仍显示上次内容：${error.message}` : error.status === 404 ? "尚未创建配置主表。" : `读取失败：${error.message}`, true);
    if (hadContent) $("settingsSaveBtn").disabled = false;
    if (error.status === 404) {
      const create = el("button", "button button-primary", "从示例创建配置主表");
      create.type = "button";
      create.addEventListener("click", async () => {
        create.disabled = true;
        try { await api("/api/bootstrap", {method: "POST"}); toast("配置主表已创建", "success"); await loadSettings(); }
        catch (failure) { settingsStatus(`创建失败：${failure.message}`, true); create.disabled = false; $("settingsStatus").append(create); }
      });
      $("settingsStatus").append(create);
    }
  } finally {
    state.settingsLoading = false;
  }
}

async function saveSettings(event) {
  event.preventDefault();
  if (!state.settings.master || state.settings.saving) return;
  let master;
  try {
    master = JSON.parse(JSON.stringify(state.settings.master));
    SETTINGS_FIELDS.slice(0, 2).forEach(([id, path]) => setSetting(master, path, $(id).value.trim()));
    storeModelDraft();
    const provider = $("settingModelProvider").value.trim();
    const profile = provider ? state.model.profiles[provider] : {base_url: "", api_key: "", model: ""};
    if (provider && (!profile.base_url || !profile.model)) {
      throw new Error("请先填写当前服务商 API 地址，获取模型列表并选择分析模型");
    }
    setSetting(master, ["models", "provider_profiles"], state.model.profiles);
    setSetting(master, ["models", "text", "provider"], provider);
    for (const key of ["base_url", "api_key", "model"]) setSetting(master, ["models", "text", key], profile[key]);
    setSetting(master, ["analysis", "qq_game_sentiment", "group_whitelist"], listFromInput("settingGroupWhitelist", "QQ 白名单群聊", /^\d+$/));
    setSetting(master, ["analysis", "qq_game_sentiment", "feishu_push_chat_ids"], listFromInput("settingPushChatIds", "飞书推送会话", /^oc_\S+$/));
  } catch (error) { settingsStatus(`无法保存：${error.message}`, true); return; }
  const button = $("settingsSaveBtn");
  state.settings.saving = true;
  state.model.request += 1;
  state.model.contextVersion += 1;
  let savedToDisk = false;
  button.disabled = true;
  SETTINGS_FIELDS.forEach(([id]) => { $(id).disabled = true; });
  $("settingsReloadBtn").disabled = true;
  $("modelListBtn").disabled = true;
  $("modelTestBtn").disabled = true;
  $("modelList").querySelectorAll("button").forEach(item => { item.disabled = true; });
  button.textContent = "保存中…";
  settingsStatus("正在保存配置主表…");
  try {
    const content = JSON.stringify(master, null, 2) + "\n";
    const saved = await api("/api/file", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({scope: "config", path: MASTER_CONFIG_PATH, content, etag: state.settings.etag})});
    state.settings.master = master;
    state.settings.etag = saved.etag;
    state.settings.dirty = false;
    savedToDisk = true;
    $("settingsSaveHint").textContent = "已保存到配置主表";
    $("settingsSaveHint").classList.remove("dirty");
    if (!state.settings.pushTargetEdited) {
      const ids = getSetting(master, ["analysis", "qq_game_sentiment", "feishu_push_chat_ids"]);
      $("pushTestChatId").value = ids.length ? ids[0] : "";
    }
    button.textContent = "正在应用…";
    settingsStatus("已保存，正在应用配置…");
    const applied = await api("/api/apply", {method: "POST"});
    settingsStatus(`已保存并应用配置。${applied.restart_hint || ""}`);
    $("settingsSaveHint").textContent = "";
    modelStatus("");
  } catch (error) {
    const message = error.status === 409 ? "配置主表已在其他地方修改，请重新读取后再保存" : error.message;
    settingsStatus(`${savedToDisk ? "已保存，但应用失败" : "保存失败"}：${message}`, true);
    $("settingsSaveHint").textContent = "";
  } finally {
    state.settings.saving = false;
    button.disabled = false;
    button.textContent = "保存并应用配置";
    SETTINGS_FIELDS.forEach(([id]) => { $(id).disabled = false; });
    $("settingsReloadBtn").disabled = false;
    $("modelListBtn").disabled = false;
    $("modelList").querySelectorAll("button").forEach(item => { item.disabled = false; });
    updateModelTestButton();
  }
}

async function sendPushTest() {
  const chatId = $("pushTestChatId").value.trim();
  const message = $("pushTestMessage").value.trim();
  const status = $("pushTestStatus");
  if (state.settings.saving) { status.textContent = "正在保存设置，请稍后发送测试消息"; return; }
  const savedPushIds = settingsList(getSetting(state.settings.master, ["analysis", "qq_game_sentiment", "feishu_push_chat_ids"])).trim();
  const credentialsChanged = $("settingAppId").value.trim() !== String(getSetting(state.settings.master, ["feishu_bot", "app_id"]) ?? "").trim()
    || $("settingAppSecret").value.trim() !== String(getSetting(state.settings.master, ["feishu_bot", "app_secret"]) ?? "").trim();
  if (credentialsChanged || $("settingPushChatIds").value.trim() !== savedPushIds) {
    status.textContent = "请先保存并应用飞书凭据或推送会话的修改";
    return;
  }
  if (!chatId || !message) { status.textContent = "请填写会话 ID 和消息文本"; return; }
  const button = $("pushTestBtn");
  button.disabled = true;
  status.textContent = "正在发送…";
  try {
    await api("/api/push-test", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({chat_id: chatId, message})});
    status.textContent = "发送成功";
  } catch (error) {
    status.textContent = `发送失败：${error.message}`;
  } finally { button.disabled = false; }
}

async function loadFiles(scope, quiet = false) {
  if (state.fileLoading[scope]) return;
  state.fileLoading[scope] = true;
  const list = $(`${scope}List`);
  if (!state.fileLoaded[scope]) list.replaceChildren(el("div", "loading-state", "正在加载文件…"));
  try {
    const data = await api(`/api/files?scope=${encodeURIComponent(scope)}`);
    state.files[scope] = Array.isArray(data.files) ? data.files : [];
    state.fileLoaded[scope] = true;
    const selected = state.selected[scope];
    if (selected && !state.files[scope].some(file => pathOf(file) === selected)) {
      if (scope === "config" && state.config.dirty) {
        toast("当前规则文件已移出列表，未保存内容仍保留在编辑区", "error");
      } else {
        state.requestId += 1;
        state.selected[scope] = null;
        $(`${scope}Document`).classList.add("hidden");
        $(`${scope}Empty`).classList.remove("hidden");
        if (scope === "config") {
          $("saveConfigBtn").disabled = true;
          $("configMeta").textContent = "";
          $("dirtyIndicator").textContent = "";
        } else {
          state.fileVersion[scope] = "";
          $(scope === "archives" ? "deleteArchiveBtn" : "deleteReportBtn").disabled = true;
        }
      }
    }
    renderFileList(scope);
    if (scope === "config" && state.page === "config" && !state.selected.config && state.files.config.length) openFile("config", state.files.config[0]);
    $("lastUpdated").textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN", {hour: "2-digit", minute: "2-digit"})}`;
  } catch (error) {
    if (!state.fileLoaded[scope]) {
      list.replaceChildren(el("div", "error-state", error.message));
      $(`${scope}Count`).textContent = "加载失败";
    }
    if (!quiet) toast(error.message, "error");
  } finally {
    state.fileLoading[scope] = false;
  }
}

function groupOf(scope, file) {
  const parts = pathOf(file).replace(/\\/g, "/").split("/").filter(Boolean);
  if (scope === "config") return parts.length > 2 ? parts.slice(0, -1).join(" / ") : "主目录";
  if (scope === "archives") return parts.length > 2 ? parts.slice(0, -2).join(" / ") : "其他归档";
  return parts.length > 2 ? parts.slice(0, -2).join(" / ") : "其他报告";
}

function renderFileList(scope) {
  const list = $(`${scope}List`);
  const query = ($(`${scope}Search`).value || "").trim().toLowerCase();
  const files = state.files[scope].filter(file => `${nameOf(file)} ${pathOf(file)}`.toLowerCase().includes(query));
  $(`${scope}Count`).textContent = `${files.length} 个文件${query ? "（已筛选）" : ""}`;
  list.replaceChildren();
  if (!files.length) { list.append(el("div", "message-empty", query ? "没有匹配的文件" : scope === "config" ? "当前主表没有引用 Markdown 源文件" : "暂无文件")); return; }
  const groups = new Map();
  files.forEach(file => {
    const group = groupOf(scope, file);
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(file);
  });
  groups.forEach((groupFiles, group) => {
    list.append(el("div", "file-group-label", group));
    groupFiles.forEach(file => {
    const button = el("button", `file-item ${state.selected[scope] === pathOf(file) ? "active" : ""}`);
    button.type = "button";
    const icon = el("span", "file-item-icon", scope === "reports" ? "▣" : scope === "archives" ? "▤" : "◇");
    const main = el("span", "file-item-main");
    main.append(el("span", "file-item-name", nameOf(file)), el("span", "file-item-sub", scope === "archives" ? pathOf(file).replace(/\\/g, "/").split("/").slice(-2, -1)[0] || "" : [localDate(file.modified), prettySize(file.size)].filter(Boolean).join(" · ")));
    button.append(icon, main);
    button.title = pathOf(file);
    button.addEventListener("click", () => openFile(scope, file));
      list.append(button);
    });
  });
}

async function openFile(scope, file) {
  if (scope === "config" && !confirmLeave()) return;
  const path = pathOf(file);
  const requestId = ++state.requestId;
  if (scope === "config") {
    const editor = $("rawEditor");
    const save = $("saveConfigBtn");
    const original = state.config.pending?.original || {readOnly: editor.readOnly, disabled: save.disabled};
    const pending = {original};
    state.config.pending = pending;
    editor.readOnly = true;
    save.disabled = true;
    try {
      const data = await api(`/api/file?${new URLSearchParams({scope, path})}`);
      if (requestId !== state.requestId) return;
      state.selected.config = path;
      renderFileList(scope);
      $("configEmpty").classList.add("hidden");
      $("configDocument").classList.remove("hidden");
      $("configFileName").textContent = nameOf(file);
      $("configFilePath").textContent = path;
      showConfig(file, data);
    } catch (error) {
      if (requestId !== state.requestId) return;
      toast(error.message, "error");
    } finally {
      if (state.config.pending === pending) {
        editor.readOnly = original.readOnly;
        save.disabled = original.disabled;
        state.config.pending = null;
      }
    }
    return;
  }
  state.selected[scope] = path;
  state.fileVersion[scope] = "";
  $(scope === "archives" ? "deleteArchiveBtn" : "deleteReportBtn").disabled = true;
  renderFileList(scope);
  $(`${scope}Empty`).classList.add("hidden");
  $(`${scope}Document`).classList.remove("hidden");
  $(`${scope}FileName`).textContent = nameOf(file);
  $(`${scope}FilePath`).textContent = path;
  if (scope === "archives") {
    state.archive = {records: [], total: 0, next: 0, loading: false};
    $("messageList").replaceChildren(el("div", "loading-state", "正在读取消息…"));
  } else $("reportContent").replaceChildren(el("div", "loading-state", "正在读取报告…"));
  try {
    if (scope === "archives") await loadArchivePage(path, requestId, true);
    else {
      const data = await api(`/api/file?${new URLSearchParams({scope, path})}`);
      if (requestId !== state.requestId) return;
      state.fileVersion.reports = data.version || "";
      $("deleteReportBtn").disabled = !state.fileVersion.reports;
      showReport(data);
    }
  } catch (error) {
    if (requestId !== state.requestId) return;
    const target = scope === "archives" ? $("messageList") : $("reportContent");
    target.replaceChildren(el("div", "error-state", error.message));
    toast(error.message, "error");
  }
}

async function deleteSelectedFile(scope) {
  const path = state.selected[scope];
  const version = state.fileVersion[scope];
  if (!path || !version) return;
  const name = path.split("/").pop();
  const extra = scope === "reports" ? "同名报告元数据也会删除。" : "正在记录的新消息可能再次生成同日归档。";
  if (!window.confirm(`确定永久删除「${name}」？${extra}`)) return;
  const button = $(scope === "archives" ? "deleteArchiveBtn" : "deleteReportBtn");
  button.disabled = true;
  try {
    await api("/api/file", {method: "DELETE", headers: {"Content-Type": "application/json"}, body: JSON.stringify({scope, path, version})});
    state.requestId += 1;
    state.selected[scope] = null;
    state.fileVersion[scope] = "";
    $(`${scope}Document`).classList.add("hidden");
    $(`${scope}Empty`).classList.remove("hidden");
    await loadFiles(scope);
    toast("文件已删除", "success");
  } catch (error) {
    button.disabled = false;
    toast(error.message, "error");
  }
}

function markDirty() {
  if (!state.config.editable) return;
  state.config.dirty = true;
  $("dirtyIndicator").textContent = "● 未保存";
  $("dirtyIndicator").classList.add("dirty");
}

function showConfig(file, data) {
  state.config = {content: String(data.content ?? ""), etag: data.etag ?? null, editable: data.editable === true, mode: "raw", dirty: false, saving: false};
  const isJson = pathOf(file).toLowerCase().endsWith(".json");
  $("configType").textContent = isJson ? "JSON" : "Markdown";
  $("rawEditor").value = state.config.content;
  $("rawEditor").readOnly = !state.config.editable;
  $("saveConfigBtn").classList.toggle("hidden", !state.config.editable);
  $("saveConfigBtn").disabled = !state.config.editable;
  $("configMeta").textContent = `${prettySize(file.size)}${file.modified ? ` · 修改于 ${localDate(file.modified)}` : ""}`;
  $("dirtyIndicator").textContent = state.config.editable ? "已保存" : "只读";
  $("dirtyIndicator").classList.remove("dirty");
  $("editorModes").classList.toggle("hidden", !isJson);
  if (isJson) {
    try { JSON.parse(state.config.content); switchMode("structured", true); }
    catch { switchMode("raw", true); toast("JSON 解析失败，请在原文模式中检查内容", "error"); }
  } else switchMode("raw", true);
}

function makePrimitiveControl(value, kind) {
  if (kind === "boolean") {
    const input = el("input"); input.type = "checkbox"; input.checked = Boolean(value); input.addEventListener("change", markDirty); return input;
  }
  const long = kind === "string" && (String(value).length > 110 || String(value).includes("\n"));
  const input = long ? el("textarea") : el("input");
  if (!long) input.type = kind === "number" ? "number" : "text";
  if (kind === "number") input.step = "any";
  input.value = kind === "null" ? "null" : String(value ?? "");
  if (kind === "null") input.disabled = true;
  input.addEventListener("input", markDirty);
  return input;
}

function kindOf(value) { return value === null ? "null" : Array.isArray(value) ? "array" : typeof value === "object" ? "object" : typeof value; }
function defaultFor(kind) { return {string: "", number: 0, boolean: false, null: null, array: [], object: {}}[kind]; }

function buildNode(value, key, removable = false) {
  const kind = kindOf(value);
  const row = el("div", `field-row ${kind === "object" || kind === "array" ? "complex-node" : ""}`); row._key = key; row._kind = kind;
  const label = el("div", "field-label", key);
  const valueWrap = el("div", ["object", "array"].includes(kind) ? "field-nested" : "field-value");
  const actions = el("div", "field-actions");
  const type = el("select", "type-select");
  ["string", "number", "boolean", "null", "array", "object"].forEach(optionKind => {
    const option = el("option", "", optionKind); option.value = optionKind; option.selected = optionKind === kind; type.append(option);
  });
  type.title = "值类型";
  type.addEventListener("change", () => { row.replaceWith(buildNode(defaultFor(type.value), key, removable)); markDirty(); });
  actions.append(type);
  if (removable) {
    const remove = el("button", "tiny-button danger", "删除"); remove.type = "button";
    remove.addEventListener("click", () => { row.remove(); markDirty(); });
    actions.append(remove);
  }
  if (kind === "object" || kind === "array") {
    const group = el("div", "field-tree");
    const title = el("div", "field-tree-title");
    title.append(el("span", "key", kind === "object" ? "对象" : `数组 · ${value.length} 项`));
    group.append(title);
    const children = el("div", "field-tree-content"); row._children = children;
    if (kind === "object") Object.entries(value).forEach(([childKey, childValue]) => children.append(buildNode(childValue, childKey, true)));
    else value.forEach((childValue, index) => children.append(buildNode(childValue, `[${index}]`, true)));
    const add = el("div", "add-row");
    const keyInput = kind === "object" ? el("input") : null;
    if (keyInput) { keyInput.placeholder = "新字段名称"; keyInput.setAttribute("aria-label", "新字段名称"); add.append(keyInput); }
    const addButton = el("button", "tiny-button", kind === "object" ? "+ 添加字段" : "+ 添加数组项"); addButton.type = "button";
    addButton.addEventListener("click", () => {
      const childCount = Array.from(children.children).filter(item => item.classList.contains("field-row")).length;
      const childKey = kind === "object" ? keyInput.value.trim() : `[${childCount}]`;
      if (kind === "object" && (!childKey || Array.from(children.children).some(item => item._key === childKey))) { toast("请输入未使用的字段名称", "error"); return; }
      children.insertBefore(buildNode("", childKey, true), add);
      if (keyInput) keyInput.value = "";
      markDirty();
    });
    if (keyInput) keyInput.addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); addButton.click(); } });
    add.append(addButton); children.append(add); group.append(children); valueWrap.append(group);
  } else {
    const control = makePrimitiveControl(value, kind); row._control = control; valueWrap.append(control);
  }
  row.append(label, valueWrap, actions);
  if (!state.config.editable) row.querySelectorAll("input,textarea,select,button").forEach(control => control.disabled = true);
  return row;
}

function serializeNode(row) {
  switch (row._kind) {
    case "object": {
      const result = {};
      Array.from(row._children.children).filter(child => child.classList.contains("field-row")).forEach(child => { result[child._key] = serializeNode(child); });
      return result;
    }
    case "array": return Array.from(row._children.children).filter(child => child.classList.contains("field-row")).map(serializeNode);
    case "number": {
      const raw = row._control.value.trim();
      if (!raw || !Number.isFinite(Number(raw))) throw new Error(`「${row._key}」需要有效数字`);
      return Number(raw);
    }
    case "boolean": return row._control.checked;
    case "null": return null;
    default: return row._control.value;
  }
}

function renderStructured(json) {
  const container = $("structuredEditor");
  container.replaceChildren();
  const root = buildNode(json, "根对象");
  root.classList.add("root-node");
  root.querySelector(":scope > .field-actions")?.remove();
  const rootTitle = root.querySelector(":scope > .field-nested > .field-tree > .field-tree-title .key");
  if (rootTitle) rootTitle.textContent = "根对象";
  container.append(root);
  container._root = root;
}

function switchMode(mode, force = false) {
  if (mode === state.config.mode && !force) return;
  if (!force) {
    try {
      if (mode === "raw") $("rawEditor").value = JSON.stringify(serializeNode($("structuredEditor")._root), null, 2) + "\n";
      else renderStructured(JSON.parse($("rawEditor").value));
    } catch (error) { toast(error.message, "error"); return; }
  } else if (mode === "structured") {
    try { renderStructured(JSON.parse($("rawEditor").value)); } catch (error) { toast(error.message, "error"); return; }
  }
  state.config.mode = mode;
  $("structuredEditor").classList.toggle("hidden", mode !== "structured");
  $("rawEditor").classList.toggle("hidden", mode !== "raw");
  document.querySelectorAll("#editorModes button").forEach(button => button.classList.toggle("active", button.dataset.mode === mode));
}

async function saveConfig(forApply = false) {
  if (!state.config.editable || !state.selected.config || state.config.saving) return false;
  let content;
  try {
    content = state.config.mode === "structured" ? JSON.stringify(serializeNode($("structuredEditor")._root), null, 2) + "\n" : $("rawEditor").value;
    if (state.selected.config.toLowerCase().endsWith(".json")) JSON.parse(content);
  } catch (error) { toast(`无法保存：${error.message}`, "error"); return false; }
  const button = $("saveConfigBtn"); button.disabled = true; button.textContent = "保存中…";
  state.config.saving = true;
  $("rawEditor").readOnly = true;
  $("applyBtn").disabled = true;
  try {
    const data = await api("/api/file", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({scope: "config", path: state.selected.config, content, etag: state.config.etag})});
    state.config.content = content; state.config.etag = data.etag ?? state.config.etag; state.config.dirty = false;
    $("dirtyIndicator").textContent = "已保存"; $("dirtyIndicator").classList.remove("dirty");
    if (!forApply) toast("文件已保存", "success");
    return true;
  } catch (error) {
    toast(error.status === 409 ? "文件已在外部更改，请重新加载后再保存" : error.message, "error");
    return false;
  } finally {
    state.config.saving = false;
    button.disabled = false;
    button.textContent = "保存文件";
    $("rawEditor").readOnly = !state.config.editable;
    if (!forApply) $("applyBtn").disabled = false;
  }
}

async function applyConfig() {
  if (state.config.saving) return;
  const button = $("applyBtn"); button.disabled = true; button.textContent = "应用中…";
  try {
    if (state.config.dirty && state.page === "config" && !await saveConfig(true)) return;
    const data = await api("/api/apply", {method: "POST"});
    toast(data.message || "源文件已保存，配置已生成并应用", "success");
    if (state.page === "config") await loadFiles("config");
  } catch (error) { toast(error.message, "error"); }
  finally { button.disabled = false; button.textContent = "应用配置"; }
}

async function loadArchivePage(path, requestId, reset = false) {
  if (state.archive.loading) return;
  state.archive.loading = true;
  try {
    const offset = reset ? 0 : state.archive.next;
    const data = await api(`/api/file?${new URLSearchParams({scope: "archives", path, offset: String(offset), limit: "100"})}`);
    if (requestId !== state.requestId) return;
    if (reset) {
      state.fileVersion.archives = data.version || "";
      $("deleteArchiveBtn").disabled = !state.fileVersion.archives;
    }
    let records = Array.isArray(data.records) ? data.records : null;
    if (!records) {
      try { const parsed = JSON.parse(data.content || "{}"); records = Array.isArray(parsed.messages) ? parsed.messages : []; }
      catch { records = data.content ? [{user_id: "归档原文", text: data.content}] : []; }
    }
    state.archive.records = reset ? records : state.archive.records.concat(records);
    state.archive.total = Number(data.total ?? data.count ?? state.archive.records.length);
    state.archive.next = offset + records.length;
    renderMessages();
  } finally { state.archive.loading = false; }
}

function messageSender(record) {
  const sender = record.sender;
  return String(record.user_id ?? (sender && typeof sender === "object" ? sender.user_id ?? sender.nickname : sender) ?? "未知用户");
}

function renderMessages() {
  const list = $("messageList"); list.replaceChildren();
  const query = $("messageSearch").value.trim().toLowerCase();
  const matching = state.archive.records.filter(record => `${messageText(record)} ${messageSender(record)} ${record.sender?.nickname ?? ""}`.toLowerCase().includes(query));
  $("messageCount").textContent = `${state.archive.records.length} / ${state.archive.total} 条消息`;
  if (!matching.length) list.append(el("div", "message-empty", query ? "已加载消息中没有匹配内容" : "此归档没有消息"));
  matching.forEach(record => {
    const card = el("div", "message-card");
    const user = messageSender(record);
    const avatar = el("div", "message-avatar", user.slice(-2));
    const main = el("div", "message-main");
    const byline = el("div", "message-byline");
    byline.append(el("strong", "", user), el("time", "", record.timestamp ?? record.time ?? ""));
    const body = el("div", "message-text", messageText(record) || "（此消息没有文本内容）");
    main.append(byline, body); card.append(avatar, main); list.append(card);
  });
  if (state.archive.next < state.archive.total && state.archive.records.length) {
    const more = el("button", "button button-secondary", "加载更多消息");
    more.style.marginTop = "18px";
    more.addEventListener("click", async () => {
      more.disabled = true;
      try { await loadArchivePage(state.selected.archives, state.requestId); }
      catch (error) { toast(error.message, "error"); more.disabled = false; }
    });
    list.append(more);
  }
}

function messageText(record) {
  const value = record.text ?? record.message ?? record.content;
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(part => typeof part === "string" ? part : part?.text ?? "").join("");
  if (value && typeof value === "object") return String(value.text ?? value.content ?? "");
  return "";
}

const reportRenderer = typeof window.markdownit === "function"
  ? window.markdownit({html: false, linkify: true, breaks: true}) : null;

function setReportMode(mode) {
  state.reportMode = mode === "source" ? "source" : "rendered";
  $("reportContent").classList.toggle("hidden", state.reportMode !== "rendered");
  $("reportSource").classList.toggle("hidden", state.reportMode !== "source");
  $("reportModes").querySelectorAll("button").forEach(button => {
    const active = button.dataset.reportMode === state.reportMode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function showReport(data) {
  state.report = String(data.content ?? "");
  $("reportSource").textContent = state.report;
  const content = $("reportContent");
  content.classList.toggle("render-fallback", !reportRenderer);
  if (reportRenderer) content.innerHTML = reportRenderer.render(state.report);
  else content.textContent = state.report;
  setReportMode(state.reportMode);
}

function wireEvents() {
  document.querySelectorAll(".nav-item").forEach(button => button.addEventListener("click", () => setPage(button.dataset.page)));
  document.querySelectorAll("[data-go]").forEach(button => button.addEventListener("click", () => setPage(button.dataset.go)));
  document.querySelectorAll("[data-refresh-scope]").forEach(button => button.addEventListener("click", () => loadFiles(button.dataset.refreshScope)));
  SCOPES.forEach(scope => $(`${scope}Search`).addEventListener("input", () => renderFileList(scope)));
  $("messageSearch").addEventListener("input", renderMessages);
  $("rawEditor").addEventListener("input", markDirty);
  $("saveConfigBtn").addEventListener("click", () => saveConfig());
  $("applyBtn").addEventListener("click", applyConfig);
  $("settingsForm").addEventListener("submit", saveSettings);
  SETTINGS_FIELDS.forEach(([id]) => $(id).addEventListener("input", markSettingsDirty));
  $("settingModelProvider").addEventListener("change", () => {
    storeModelDraft();
    const newProvider = $("settingModelProvider").value;
    showModelProvider(newProvider);
    markSettingsDirty();
    modelStatus(newProvider ? `已切换到 ${newProvider}，请确认该服务商的 Key 和分析模型。` : "请选择服务商。");
  });
  ["settingModelBaseUrl", "settingModelApiKey"].forEach(id => $(id).addEventListener("input", invalidateModelChoice));
  $("modelListBtn").addEventListener("click", fetchModelList);
  $("modelTestBtn").addEventListener("click", () => testModel($("settingModelName").value.trim()));
  $("modelSearch").addEventListener("input", renderModelList);
  $("settingsReloadBtn").addEventListener("click", () => { if (confirmLeave()) loadSettings(); });
  $("pushTestChatId").addEventListener("input", () => { state.settings.pushTargetEdited = true; });
  $("settingPushChatIds").addEventListener("input", () => {
    if (!state.settings.pushTargetEdited) $("pushTestChatId").value = $("settingPushChatIds").value.split(/\r?\n/).map(value => value.trim()).find(Boolean) || "";
  });
  $("pushTestBtn").addEventListener("click", sendPushTest);
  document.querySelectorAll("[data-operation-job]").forEach(button => button.addEventListener("click", () => runOperation("/api/operations/job", {name: button.dataset.operationJob})));
  document.querySelectorAll("[data-operation-start]").forEach(button => button.addEventListener("click", () => runOperation("/api/operations/start", {mode: button.dataset.operationStart})));
  $("homePrimary").addEventListener("click", () => runOperation(state.operations.service?.running ? "/api/operations/stop" : "/api/operations/start", state.operations.service?.running ? {} : {mode: "integrated"}));
  $("homeGuideStart").addEventListener("click", () => $("homeLaunchTitle").scrollIntoView({behavior: "smooth", block: "start"}));
  $("qqSetupBtn").addEventListener("click", setupQQ);
  $("qqRescanBtn").addEventListener("click", () => runOperation("/api/operations/start", {mode: "integrated"}));
  $("qqAvatar").addEventListener("error", () => {
    $("qqAvatar").dataset.failedUrl = $("qqAvatar").src;
    $("qqAvatar").classList.add("hidden");
    $("qqAvatarFallback").classList.remove("hidden");
  });
  $("qqAvatar").addEventListener("load", () => {
    if (!state.operations.qq?.account?.user_id) return;
    $("qqAvatar").dataset.failedUrl = "";
    $("qqAvatar").classList.remove("hidden");
    $("qqAvatarFallback").classList.add("hidden");
  });
  for (const id of ["qqQrImage", "homeQrImage"]) {
    const image = $(id);
    image.addEventListener("load", () => { image.dataset.failures = "0"; image.style.visibility = "visible"; });
    image.addEventListener("error", () => {
      const failures = Math.min(3, Number(image.dataset.failures || 0) + 1);
      image.dataset.failures = String(failures);
      image.dataset.retryAt = String(Date.now() + 1000 * 2 ** failures);
      image.style.visibility = "hidden";
    });
  }
  $("refreshBtn").addEventListener("click", () => state.page === "overview" ? refreshOverview() : state.page === "settings" ? (confirmLeave() && loadSettings()) : (state.page === "operations" || state.page === "qq") ? loadOperationsStatus() : loadFiles(state.page));
  $("editorModes").querySelectorAll("button").forEach(button => button.addEventListener("click", () => switchMode(button.dataset.mode)));
  $("reportModes").querySelectorAll("button").forEach(button => button.addEventListener("click", () => setReportMode(button.dataset.reportMode)));
  $("copyReportBtn").addEventListener("click", async () => { try { await navigator.clipboard.writeText(state.report); toast("报告原文已复制", "success"); } catch { toast("复制失败，请检查浏览器剪贴板权限", "error"); } });
  $("deleteArchiveBtn").addEventListener("click", () => deleteSelectedFile("archives"));
  $("deleteReportBtn").addEventListener("click", () => deleteSelectedFile("reports"));
  window.addEventListener("beforeunload", event => { if (state.config.dirty || state.settings.dirty) { event.preventDefault(); event.returnValue = ""; } });
}

wireEvents();
refreshOverview();
setInterval(() => { if (state.page === "overview" || state.page === "operations" || state.page === "qq") loadOperationsStatus(); }, 2500);
