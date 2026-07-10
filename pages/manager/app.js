/**
 * 抖音 AI Bot 管理面板前端逻辑
 * 使用 AstrBot Plugin Bridge API 或原生 fetch 回退
 */

// 插件页面 URL 前缀（后端注册的路由前缀）
const PAGE_PATH = "astrbot_plugin_douyin_ai_bot";

// Bridge API 兼容层：优先用 bridge，不可用时回退到 fetch
async function apiGet(path) {
  const bridge = window.AstrBotPluginPage;
  if (bridge && typeof bridge.apiGet === "function") {
    return await bridge.apiGet(path);
  }
  const resp = await fetch(`/${PAGE_PATH}/${path}`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return await resp.json();
}

async function apiPost(path, body) {
  const bridge = window.AstrBotPluginPage;
  if (bridge && typeof bridge.apiPost === "function") {
    return await bridge.apiPost(path, body);
  }
  const resp = await fetch(`/${PAGE_PATH}/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return await resp.json();
}

// ── 状态 ──

let qrPollTimer = null;
let qrToken = "";

// ── Toast ──

function showToast(msg, isErr = false) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.toggle("error", isErr);
  t.classList.remove("hidden");
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => t.classList.add("hidden"), 4000);
}

// ── 加载状态 ──

async function loadStatus() {
  try {
    const data = await apiGet("status");
    if (!data) return;

    // 统计卡片
    const runningEl = document.getElementById("stat-running");
    runningEl.textContent = data.running ? "运行中" : "已停止";
    runningEl.className = "stat-value " + (data.running ? "ok" : "err");

    const cookieEl = document.getElementById("stat-cookie");
    cookieEl.textContent = data.cookie_valid ? "已登录" : data.cookie_configured ? "Cookie 无效" : "未配置";
    cookieEl.className = "stat-value " + (data.cookie_valid ? "ok" : data.cookie_configured ? "warn" : "err");

    document.getElementById("stat-replied").textContent = data.replied_count || 0;

    // 详细状态
    setBadge("det-running", data.running);
    setBadge("det-cookie", data.cookie_valid);
    document.getElementById("det-user").textContent =
      data.user_info?.nickname
        ? `${data.user_info.nickname} (${data.user_info.user_id || ""})`
        : "未登录";
    setBadge("det-reply", data.reply_enabled);
    setBadge("det-proactive", data.proactive_enabled);
    setBadge("det-affection", data.affection_enabled);
    setBadge("det-memory", data.memory_enabled);
    setBadge("det-mood", data.mood_enabled);
    setBadge("det-share", data.share_parse_enabled);
    document.getElementById("det-mood-text").textContent = data.mood || "—";
    document.getElementById("det-interval").textContent = data.poll_interval ? `${data.poll_interval}s` : "—";
    document.getElementById("det-probability").textContent = data.reply_probability != null ? `${data.reply_probability}%` : "—";
    document.getElementById("det-replied-count").textContent = data.replied_count || 0;
    document.getElementById("det-owner").textContent = data.owner_name || "未设置";

    // Cookie 信息
    const cookieInfo = document.getElementById("cookie-info");
    if (data.cookie_configured) {
      cookieInfo.innerHTML =
        `<div class="cookie-box" title="掩码显示，前30位">${data.cookie_masked || "已配置"}</div>`;
    } else {
      cookieInfo.innerHTML = `<p style="color: var(--text-muted);">点击「扫码登录」按钮通过抖音 App 扫码获取 Cookie</p>`;
    }
  } catch (e) {
    console.error("加载状态失败:", e);
  }
}

async function loadStats() {
  try {
    const data = await apiGet("stats");
    if (!data) return;
    document.getElementById("stat-affection").textContent = data.affection_users ?? "—";
    document.getElementById("stat-memory").textContent = data.memory_entries ?? "—";
    document.getElementById("stat-blacklist").textContent = data.blacklist_count ?? "—";
  } catch (e) {
    console.error("加载统计失败:", e);
  }
}

async function loadLogs() {
  try {
    const data = await apiGet("logs");
    const el = document.getElementById("log-content");
    if (data.logs && data.logs.length > 0) {
      el.innerHTML = data.logs.map(l => escapeHtml(l)).join("");
    } else {
      el.innerHTML = '<div class="empty">暂无日志</div>';
    }
  } catch (e) {
    console.error("加载日志失败:", e);
  }
}

function setBadge(id, enabled) {
  const el = document.getElementById(id);
  if (!el) return;
  if (enabled === true) {
    el.innerHTML = '<span class="badge ok">✅ 开启</span>';
  } else if (enabled === false) {
    el.innerHTML = '<span class="badge err">❌ 关闭</span>';
  } else {
    el.textContent = "—";
  }
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// ── QR 扫码登录 ──

async function startQrLogin() {
  const modal = document.getElementById("qrcode-modal");
  const container = document.getElementById("qrcode-container");
  const statusEl = document.getElementById("qr-status");

  modal.classList.add("active");
  container.innerHTML = '<div class="loading-spinner"></div><div class="placeholder">正在生成二维码...</div>';
  statusEl.textContent = "正在连接抖音...";

  try {
    const data = await apiGet("qrcode");
    if (!data || !data.ok) {
      statusEl.textContent = "❌ 获取二维码失败，请稍后重试";
      container.innerHTML = '<div class="placeholder">获取失败</div>';
      return;
    }

    qrToken = data.token;

    // 显示二维码图片
    container.innerHTML = `<img src="${data.qrcode_img_url}" alt="抖音登录二维码" />`;
    statusEl.textContent = "📱 请打开抖音 App 扫描二维码";

    // 开始轮询检查状态
    startQrPolling();
  } catch (e) {
    console.error("QR 码请求失败:", e);
    statusEl.textContent = "❌ 请求失败: " + (e.message || "未知错误");
    container.innerHTML = '<div class="placeholder">请求失败</div>';
  }
}

function startQrPolling() {
  const statusEl = document.getElementById("qr-status");
  let pollCount = 0;
  const MAX_POLLS = 300; // 5 分钟（1秒/次）

  clearInterval(qrPollTimer);
  qrPollTimer = setInterval(async () => {
    pollCount++;
    if (pollCount > MAX_POLLS) {
      clearInterval(qrPollTimer);
      statusEl.textContent = "⏰ 二维码已过期，请刷新重新扫码";
      return;
    }

    try {
      const data = await apiGet(`qrcode/check?token=${qrToken}`);
      if (!data) return;

      const code = data.status;

      if (code === 3) {
        // 登录成功！
        clearInterval(qrPollTimer);
        statusEl.textContent = "✅ 登录成功！正在获取 Cookie...";

        // 提取 Cookie 并保存
        if (data.cookies) {
          const saveResult = await apiPost("cookie", {
            cookie: data.cookies,
          });

          if (saveResult && saveResult.ok) {
            const userName = data.user_info?.nickname || saveResult.user_name || "未知";
            statusEl.textContent = `✅ 登录成功！用户: ${userName}`;

            // 更新 Cookie 显示
            const cookieInfo = document.getElementById("cookie-info");
            cookieInfo.innerHTML =
              `<div class="cookie-box" title="完整 Cookie 已保存">✅ 已自动获取 Cookie: ${data.cookies.substring(0, 50)}...</div>`;

            showToast(`🎉 抖音登录成功！用户: ${userName}`);
            loadStatus();
          } else {
            statusEl.textContent = "⚠️ 登录成功但 Cookie 保存失败，请重试";
            showToast("⚠️ Cookie 保存失败", true);
          }
        } else {
          statusEl.textContent = "⚠️ 登录成功但未获取到 Cookie";
          showToast("⚠️ 未获取到 Cookie，请重试", true);
        }
      } else if (code === 1) {
        statusEl.textContent = "📱 已扫描，请在手机上确认登录...";
      } else if (code === 2) {
        clearInterval(qrPollTimer);
        statusEl.textContent = "❌ 二维码已过期，请刷新";
        showToast("二维码已过期", true);
      } else if (code === 0) {
        // 等待扫码
        const dots = ".".repeat((pollCount % 3) + 1);
        statusEl.textContent = `📱 等待扫码${dots}`;
      }
    } catch (e) {
      console.error("QR 轮询失败:", e);
    }
  }, 1000);
}

function closeQrModal() {
  clearInterval(qrPollTimer);
  document.getElementById("qrcode-modal").classList.remove("active");
}

// ── 启动/停止 ──

async function startBot() {
  try {
    const data = await apiPost("start", {});
    if (data && data.ok !== false) {
      showToast("✅ Bot 已启动");
      loadStatus();
    } else {
      showToast("❌ 启动失败: " + (data?.message || "未知错误"), true);
    }
  } catch (e) {
    showToast("❌ 启动请求失败: " + (e.message || ""), true);
  }
}

async function stopBot() {
  try {
    const data = await apiPost("stop", {});
    if (data && data.ok !== false) {
      showToast("⏹ Bot 已停止");
      loadStatus();
    } else {
      showToast("❌ 停止失败: " + (data?.message || "未知错误"), true);
    }
  } catch (e) {
    showToast("❌ 停止请求失败: " + (e.message || ""), true);
  }
}

// ── 手动保存 Cookie ──

async function saveManualCookie() {
  const textarea = document.getElementById("cookie-textarea");
  const msgEl = document.getElementById("cookie-save-msg");
  const cookie = textarea.value.trim();

  if (!cookie) {
    msgEl.textContent = "⚠️ Cookie 不能为空";
    msgEl.style.color = "var(--danger)";
    return;
  }

  try {
    const data = await apiPost("cookie", { cookie });
    if (data && data.ok !== false) {
      msgEl.textContent = "✅ Cookie 已保存";
      msgEl.style.color = "var(--success)";
      textarea.value = "";
      loadStatus();
      showToast("🍪 Cookie 已保存成功");
    } else {
      msgEl.textContent = "❌ 保存失败: " + (data?.message || "未知错误");
      msgEl.style.color = "var(--danger)";
    }
  } catch (e) {
    msgEl.textContent = "❌ 请求失败: " + (e.message || "");
    msgEl.style.color = "var(--danger)";
  }

  // 3 秒后清除消息
  setTimeout(() => { msgEl.textContent = ""; }, 3000);
}

// ── 全部刷新 ──

async function refreshAll() {
  await Promise.all([loadStatus(), loadStats(), loadLogs()]);
  showToast("🔄 已刷新");
}

// ── 初始化 ──

document.addEventListener("DOMContentLoaded", function () {
  // 按钮绑定
  document.getElementById("btn-refresh").addEventListener("click", refreshAll);
  document.getElementById("btn-qrcode").addEventListener("click", startQrLogin);
  document.getElementById("btn-start").addEventListener("click", startBot);
  document.getElementById("btn-stop").addEventListener("click", stopBot);
  document.getElementById("btn-qr-close").addEventListener("click", closeQrModal);
  document.getElementById("btn-qr-refresh").addEventListener("click", function () {
    clearInterval(qrPollTimer);
    startQrLogin();
  });
  document.getElementById("btn-cookie-save").addEventListener("click", saveManualCookie);

  // 点击遮罩关闭弹窗
  document.getElementById("qrcode-modal").addEventListener("click", function (e) {
    if (e.target === this) closeQrModal();
  });

  // 初始加载
  refreshAll();

  // 自动刷新（每 30 秒）
  setInterval(loadStatus, 30000);
});
