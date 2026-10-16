// 外贸客户工作台 —— 前端逻辑
// 一句话：所有数据都通过 /api/* 拿；「我是谁」由后端会话决定，前端只管显示。

const $ = (id) => document.getElementById(id);
let CURRENT = null; // 当前打开的客户 id
let SIGNUP_OPEN = true; // 注册是否开放（启动时问后端）

// ---------- 小工具 ----------

function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// 后端时间统一存 UTC，显示时转成本地时区；老数据没带时区后缀就补个 Z
function formatTime(iso) {
  if (!iso) return "";
  const hasTz = /[zZ]$|[+-]\d\d:?\d\d$/.test(iso);
  const d = new Date(hasTz ? iso : iso + "Z");
  return isNaN(d) ? iso : d.toLocaleString("zh-CN", { hour12: false });
}

// 所有请求都走这里：出错时把后端给的 detail 原样抛出来，别自己编提示
async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let data = null;
  try {
    data = await res.json();
  } catch (_) {
    /* 可能是空响应 */
  }
  if (!res.ok) throw new Error((data && data.detail) || "请求失败：" + res.status);
  return data;
}

const setText = (el, t) => { el.textContent = t || ""; };

// ---------- 登录 / 注册 ----------

let registerMode = false;

function renderAuthMode() {
  $("authTitle").textContent = registerMode ? "注册" : "登录";
  $("submitAuth").textContent = registerMode ? "注册并进入" : "登录";
  $("toggleMode").textContent = registerMode ? "已有账号？去登录" : "没有账号？去注册";
  $("code").hidden = !registerMode;
  // 注册关着的时候直接把话说清楚，别让人填完一整个表单才吃一个 403
  setText(
    $("authMsg"),
    registerMode && !SIGNUP_OPEN
      ? "注册已关闭：需要管理员先在服务端配置 SIGNUP_CODE（邀请码）才能注册。"
      : ""
  );
}

function showLogin() {
  $("loginCard").hidden = false;
  $("app").hidden = true;
  $("logout").hidden = true;
  setText($("whoami"), "");
  $("username").focus();
}

function showApp(user) {
  $("loginCard").hidden = true;
  $("app").hidden = false;
  $("logout").hidden = false;
  setText($("whoami"), user.display_name + (user.is_admin ? "（管理员）" : ""));
  $("newCompany").focus();
  loadCustomers();
}

async function submitAuth() {
  const username = $("username").value.trim();
  const password = $("password").value;
  const code = $("code").value.trim();
  if (!username || !password) return setText($("authMsg"), "用户名和密码都要填");
  try {
    const body = registerMode
      ? { username, password, code }
      : { username, password };
    const data = await api(registerMode ? "/api/register" : "/api/login", {
      method: "POST",
      body: JSON.stringify(body),
    });
    $("password").value = "";
    $("code").value = "";
    showApp(data.user);
  } catch (err) {
    setText($("authMsg"), err.message);
  }
}

async function logout() {
  try { await api("/api/logout", { method: "POST" }); } catch (_) {}
  CURRENT = null;
  showLogin();
}

// ---------- 客户列表 ----------

// 统计条：把「今天该干什么」变成一眼可见的数字
function renderStats(items) {
  const todayStr = new Date().toLocaleDateString("zh-CN");
  const isToday = (c) =>
    c.created_at && new Date(c.created_at).toLocaleDateString("zh-CN") === todayStr;
  // 克制：只给「主指标」上品牌色，只给「需要动手的那张」上警示色，其余保持中性
  const cards = [
    { n: items.length, label: "客户总数", cls: "brand" },
    { n: items.filter((c) => c.high_intent).length, label: "高意向", cls: "" },
    { n: items.filter(isToday).length, label: "今日新增", cls: "" },
    { n: items.filter((c) => !c.followup_count).length, label: "还没写跟进", cls: "warn" },
  ];
  const box = $("stats");
  box.innerHTML = "";
  for (const c of cards) {
    const d = document.createElement("div");
    d.className = "stat " + c.cls;
    d.innerHTML =
      '<div class="stat-num">' + c.n + "</div>" +
      '<div class="stat-label">' + escapeHtml(c.label) + "</div>";
    box.appendChild(d);
  }
}

async function loadCustomers() {
  try {
    const data = await api("/api/customers");
    renderStats(data.items);
    const list = $("custList");
    list.innerHTML = "";
    setText($("custCount"), "共 " + data.items.length + " 个");
    if (!data.items.length) {
      const li = document.createElement("li");
      li.className = "hint";
      li.textContent = "还没有客户，上面填个公司名就能建。";
      list.appendChild(li);
      return;
    }
    for (const c of data.items) {
      const li = document.createElement("li");
      li.className = "cust-item" + (c.id === CURRENT ? " active" : "");
      li.dataset.id = c.id;
      const tags = [];
      if (c.source) tags.push('<span class="tag src">' + escapeHtml(c.source) + "</span>");
      if (c.high_intent) tags.push('<span class="tag hot">高意向</span>');
      li.innerHTML =
        '<div class="cust-name">' + escapeHtml(c.company) + "</div>" +
        '<div class="cust-meta">' + tags.join("") +
        "<span>跟进 " + c.followup_count + " 条</span></div>";
      li.addEventListener("click", () => openCustomer(c.id));
      list.appendChild(li);
    }
  } catch (err) {
    setText($("appMsg"), err.message);
  }
}

async function createCustomer() {
  const company = $("newCompany").value.trim();
  if (!company) return setText($("appMsg"), "公司名必填");
  try {
    const c = await api("/api/customers", {
      method: "POST",
      body: JSON.stringify({
        company,
        source: $("newSource").value.trim(),
        note: $("newNote").value.trim(),
      }),
    });
    $("newCompany").value = "";
    $("newSource").value = "";
    $("newNote").value = "";
    setText($("appMsg"), "已建档：" + c.company);
    $("newCompany").focus();
    await loadCustomers();
    openCustomer(c.id);
  } catch (err) {
    setText($("appMsg"), err.message);
  }
}

// ---------- 客户详情 + 跟进 + 移交 ----------

async function openCustomer(id) {
  try {
    const c = await api("/api/customers/" + id);
    CURRENT = id;
    $("detailEmpty").hidden = true;
    $("detail").hidden = false;
    setText($("dCompany"), c.company);
    const meta = [];
    if (c.source) meta.push("来源：" + c.source);
    if (c.country) meta.push("国家：" + c.country);
    if (c.stage) meta.push("阶段：" + c.stage);
    if (c.note) meta.push(c.note);
    meta.push("建档于 " + formatTime(c.created_at));
    setText($("dMeta"), meta.join(" ｜ "));
    $("fuContent").focus();
    await loadFollowups(id);
    await loadTransferOptions();
    // 刷新列表里的高亮
    document.querySelectorAll(".cust-item").forEach((el) => {
      el.classList.toggle("active", Number(el.dataset.id) === id);
    });
  } catch (err) {
    setText($("appMsg"), err.message);
  }
}

async function loadFollowups(id) {
  const data = await api("/api/customers/" + id + "/followups");
  const ul = $("fuList");
  ul.innerHTML = "";
  if (!data.items.length) {
    const li = document.createElement("li");
    li.className = "hint";
    li.textContent = "还没有跟进记录。";
    ul.appendChild(li);
    return;
  }
  for (const f of data.items) {
    const li = document.createElement("li");
    li.className = "fu-item";
    li.innerHTML =
      '<span class="fu-time">' + escapeHtml(formatTime(f.created_at)) +
      " · " + escapeHtml(f.display_name || f.username) + "</span>" +
      '<span class="fu-content">' + escapeHtml(f.content) + "</span>";
    ul.appendChild(li);
  }
}

async function addFollowup() {
  if (!CURRENT) return;
  const content = $("fuContent").value.trim();
  if (!content) return;
  try {
    await api("/api/customers/" + CURRENT + "/followups", {
      method: "POST",
      body: JSON.stringify({ content }),
    });
    $("fuContent").value = "";
    await loadFollowups(CURRENT);
    await loadCustomers();
  } catch (err) {
    setText($("appMsg"), err.message);
  }
}

async function loadTransferOptions() {
  try {
    const data = await api("/api/users");
    const sel = $("transferTo");
    sel.innerHTML = "";
    for (const u of data.items) {
      if (!u.is_active) continue;
      const opt = document.createElement("option");
      opt.value = u.id;
      opt.textContent = u.display_name || u.username;
      sel.appendChild(opt);
    }
  } catch (_) {
    /* 取不到就留空，不影响主流程 */
  }
}

async function doTransfer() {
  if (!CURRENT) return;
  const to = Number($("transferTo").value);
  if (!to) return;
  if (!confirm("确定把这个客户转给对方？转出去之后你这边就不再显示了。")) return;
  try {
    await api("/api/customers/" + CURRENT + "/transfer", {
      method: "POST",
      body: JSON.stringify({ to_user_id: to }),
    });
    setText($("appMsg"), "已转出。");
    CURRENT = null;
    $("detail").hidden = true;
    $("detailEmpty").hidden = false;
    await loadCustomers();
  } catch (err) {
    setText($("appMsg"), err.message);
  }
}

// ---------- 入口 ----------

function bind() {
  $("submitAuth").addEventListener("click", submitAuth);
  $("password").addEventListener("keydown", (e) => { if (e.key === "Enter") submitAuth(); });
  $("code").addEventListener("keydown", (e) => { if (e.key === "Enter") submitAuth(); });
  $("toggleMode").addEventListener("click", (e) => {
    e.preventDefault();
    registerMode = !registerMode;
    renderAuthMode();
  });
  $("logout").addEventListener("click", logout);

  $("createCustomer").addEventListener("click", createCustomer);
  // 「建档 ≤30 秒」：三个框都能回车提交，手不用离开键盘
  for (const id of ["newCompany", "newSource", "newNote"]) {
    $(id).addEventListener("keydown", (e) => { if (e.key === "Enter") createCustomer(); });
  }

  $("addFollowup").addEventListener("click", addFollowup);
  // 「写跟进 ≤10 秒」：一个框，回车就存
  $("fuContent").addEventListener("keydown", (e) => { if (e.key === "Enter") addFollowup(); });
  $("doTransfer").addEventListener("click", doTransfer);
}

async function boot() {
  bind();
  try {
    const st = await api("/api/signup-status");
    SIGNUP_OPEN = !!st.open;
  } catch (_) {
    /* 接口不可用就默认开着——真注册时后端还会再拦一道 */
  }
  renderAuthMode();
  try {
    const data = await api("/api/me");
    if (data.user) showApp(data.user);
    else showLogin();
  } catch (_) {
    showLogin();
  }
}

boot();
