// 外贸客户工作台 —— 前端逻辑
// 一句话：所有数据都通过 /api/* 拿；「我是谁」由后端会话决定，前端只管显示。

// 从自己这个 script 的 URL 里读版本号（main.py 发页面时会带 ?v=文件修改时间）。
// 用处：出问题时用户能直接报出他跑的是哪一版，省掉「你看到的是不是旧的」这种来回。
const BUILD = (() => {
  const src = (document.currentScript && document.currentScript.src) || "";
  const m = src.match(/[?&]v=(\d+)/);
  return m ? m[1] : "dev";
})();

const $ = (id) => document.getElementById(id);
let CURRENT = null; // 当前打开的客户 id
let CURRENT_DATA = null; // 当前客户的完整数据（编辑资料时回填用）
let SEARCH = ""; // 当前搜索词
let STAGE_FILTER = ""; // 阶段筛选："" = 全部，"none" = 未分组，其余为具体阶段
let IDLE_DAYS = ""; // 未联系天数筛选："" = 全部，其余是 3 / 5 / 7 / 10
let STAGES = []; // 后端给的固定档位，避免前后端各写一份

// 阶段 → 标签配色的修饰类
const STAGE_CLASS = {
  新线索: "new",
  已联系: "contacted",
  已报价: "quoted",
  成交: "won",
  流失: "lost",
};

// ---------- 小工具 ----------

function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// 后端时间统一存 UTC，显示时转成本地时区；老数据没带时区后缀就补个 Z
function toDate(iso) {
  if (!iso) return null;
  const hasTz = /[zZ]$|[+-]\d\d:?\d\d$/.test(iso);
  const d = new Date(hasTz ? iso : iso + "Z");
  return isNaN(d) ? null : d;
}

function formatTime(iso) {
  const d = toDate(iso);
  return d ? d.toLocaleString("zh-CN", { hour12: false }) : iso || "";
}

// 两个日期差几天（按日历天算，不受具体时刻影响）——「未联系天数」的算法底座
function daysBetween(iso) {
  const d = toDate(iso);
  if (!d) return null;
  const a = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const now = new Date();
  const b = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  return Math.round((b - a) / 86400000);
}

// 只显示月-日，列表里省地方
function shortDate(iso) {
  const d = toDate(iso);
  return d ? d.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" }) : "";
}

// 联系状态：跟进过的看「最后跟进到今」，没跟进过的看「建档到今」。
// 两个时间概念分开说清楚——建档是建档，联系是联系，别混成一个。
function contactStatus(c) {
  const lastDays = daysBetween(c.last_followup_at);
  const createdDays = daysBetween(c.created_at);
  if (lastDays !== null) {
    return {
      text: lastDays === 0 ? "今天已跟进" : lastDays + " 天未联系",
      sub: "上次跟进 " + shortDate(c.last_followup_at) + "（" + (lastDays === 0 ? "今天" : lastDays + " 天前") + "）",
      days: lastDays,
    };
  }
  return {
    text: createdDays === 0 ? "今天建档" : createdDays + " 天未联系",
    sub: "建档 " + shortDate(c.created_at) + "（还没写过跟进）",
    days: createdDays,
  };
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
// 客户在界面上怎么称呼：优先姓名，没姓名就退到公司名
const titleOf = (c) => c.contact_name || c.company || "（未填姓名）";

// ---------- 登录 / 退出 ----------
// 登录界面在独立的 /login 页（服务端把未登录的 /app 302 过去）。
// 这里只负责两件事：拿不到用户就回登录页；退出也回登录页。

function goLogin() {
  location.replace("/login?next=/app");
}

function showApp(user) {
  $("app").hidden = false;
  $("logout").hidden = false;
  IS_ADMIN = !!user.is_admin;
  setText($("whoami"), user.display_name + (user.is_admin ? "（管理员）" : ""));
  setText($("build"), "build " + BUILD);
  $("newName").focus();
  switchView("customers");
  loadOptions();
  loadCustomers();
  loadPendingCount(); // 左导航「日历」旁的红点
  loadProviders(); // 生成话术那个智能体下拉
}

async function logout() {
  try { await api("/api/logout", { method: "POST" }); } catch (_) {}
  CURRENT = null;
  goLogin();
}

// ---------- 客户列表 ----------

// 统计条：把「今天该干什么」变成一眼可见的数字
function renderStats(items) {
  const todayStr = new Date().toLocaleDateString("zh-CN");
  const isToday = (c) => {
    const d = toDate(c.created_at);
    return d ? d.toLocaleDateString("zh-CN") === todayStr : false;
  };
  // 「多久没联系」= 最后一条跟进；一条都没写过就退回建档时间
  const lastTouch = (c) => {
    const d = toDate(c.last_followup_at) || toDate(c.created_at);
    return d ? d.getTime() : 0;
  };
  const weekAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
  // 克制：只给「主指标」上品牌色，只给「需要动手的那张」上警示色，其余保持中性
  const cards = [
    { n: items.length, label: "客户总数", cls: "brand" },
    { n: items.filter((c) => c.high_intent).length, label: "高意向", cls: "" },
    { n: items.filter(isToday).length, label: "今日新增", cls: "" },
    { n: items.filter((c) => lastTouch(c) < weekAgo).length, label: "7 天没联系", cls: "warn" },
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

// 阶段下拉有两处：列表工具条（含「全部 / 未分组」）和详情栏（含「未设置」）。
// 选项来自后端返回的 STAGES，前后端不各写一份。
function fillStageOptions() {
  const filter = $("stageFilter");
  filter.innerHTML = "";
  for (const [value, label] of [["", "全部阶段"], ["none", "未分组"], ...STAGES.map((s) => [s, s])]) {
    const o = document.createElement("option");
    o.value = value;
    o.textContent = label;
    filter.appendChild(o);
  }
  filter.value = STAGE_FILTER;

  const sel = $("dStage");
  sel.innerHTML = "";
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "未设置阶段";
  sel.appendChild(blank);
  for (const s of STAGES) {
    const o = document.createElement("option");
    o.value = s;
    o.textContent = s;
    sel.appendChild(o);
  }
}

async function loadCustomers() {
  try {
    const p = new URLSearchParams();
    if (SEARCH) p.set("q", SEARCH);
    if (STAGE_FILTER) p.set("stage", STAGE_FILTER);
    if (IDLE_DAYS) p.set("idle_days", IDLE_DAYS);
    const qs = p.toString();
    const data = await api("/api/customers" + (qs ? "?" + qs : ""));
    // 只在阶段列表变化时重建下拉——每次都重建会打断用户正在做的选择
    if (Array.isArray(data.stages) && data.stages.join() !== STAGES.join()) {
      STAGES = data.stages;
      fillStageOptions();
    }
    renderStats(data.items);
    const list = $("custList");
    list.innerHTML = "";
    setText($("custCount"), "共 " + data.items.length + " 个");
    if (!data.items.length) {
      const li = document.createElement("li");
      li.className = "hint";
      li.textContent =
        SEARCH || STAGE_FILTER
          ? "当前筛选下没有客户。"
          : "还没有客户，上面填姓名 / 号码 / 国家 就能建。";
      list.appendChild(li);
      return;
    }
    for (const c of data.items) {
      const li = document.createElement("li");
      li.className = "cust-item" + (c.id === CURRENT ? " active" : "");
      li.dataset.id = c.id;
      const tags = [];
      if (c.stage) {
        tags.push(
          '<span class="tag stage ' + (STAGE_CLASS[c.stage] || "") + '">' +
            escapeHtml(c.stage) + "</span>"
        );
      }
      if (c.source) tags.push('<span class="tag src">' + escapeHtml(c.source) + "</span>");
      if (c.high_intent) tags.push('<span class="tag hot">高意向</span>');
      const sub = [c.company, c.country].filter(Boolean).join(" · ");
      const st = contactStatus(c);
      const stCls = st.days !== null && st.days >= 7 ? "contact warn" : "contact";
      // 两栏：左边是「这是谁」，右边是「多久没联系 + 跟进了几条」。
      // 全挤在左边会一长条看不清重点，右侧对齐才好扫。
      li.innerHTML =
        '<div class="cust-row">' +
        '<div class="cust-main">' +
        '<div class="cust-name">' + escapeHtml(titleOf(c)) + "</div>" +
        '<div class="cust-meta">' + tags.join("") +
        (sub ? "<span>" + escapeHtml(sub) + "</span>" : "") +
        "</div></div>" +
        '<div class="cust-side">' +
        '<span class="' + stCls + '">' + escapeHtml(st.text) + "</span>" +
        '<span class="cust-fu">跟进 ' + c.followup_count + " 条</span>" +
        "</div></div>";
      li.addEventListener("click", () => openCustomer(c.id));
      list.appendChild(li);
    }
  } catch (err) {
    setText($("appMsg"), err.message);
  }
}

const NEW_FIELDS = ["newName", "newPhone", "newCountry", "newCompany", "newSource", "newNote"];

async function createCustomer() {
  const body = {
    contact_name: $("newName").value.trim(),
    phone: $("newPhone").value.trim(),
    country: $("newCountry").value.trim(),
    company: $("newCompany").value.trim(),
    source: $("newSource").value.trim(),
    note: $("newNote").value.trim(),
  };
  if (!body.contact_name || !body.phone || !body.country) {
    return setText($("appMsg"), "姓名、号码、国家 三个都要填");
  }
  try {
    const c = await api("/api/customers", { method: "POST", body: JSON.stringify(body) });
    for (const id of NEW_FIELDS) $(id).value = "";
    setText($("appMsg"), "已建档：" + titleOf(c));
    $("newName").focus();
    await loadCustomers();
    loadOptions(); // 刚填的国家/来源，下一个人就能下拉选到了
    openCustomer(c.id);
  } catch (err) {
    setText($("appMsg"), err.message);
  }
}

// ---------- 客户详情 / 跟进 / 移交 / 删除 ----------

async function openCustomer(id) {
  try {
    const c = await api("/api/customers/" + id);
    CURRENT = id;
    CURRENT_DATA = c;
    $("detailEmpty").hidden = true;
    $("detail").hidden = false;
    // 每次打开都退出「编辑资料」态，免得串到上一个客户
    $("dEdit").hidden = true;
    $("dMeta").hidden = false;
    setText($("dTitle"), titleOf(c));
    const bits = [];
    if (c.company) bits.push("公司：" + c.company);
    if (c.phone) bits.push("号码：" + c.phone);
    if (c.country) bits.push("国家：" + c.country);
    if (c.source) bits.push("来源：" + c.source);
    if (c.note) bits.push(c.note);
    bits.push("建档于 " + formatTime(c.created_at));
    setText($("dMeta"), bits.join(" ｜ "));
    const st = contactStatus(c);
    $("dContact").textContent = st.text + " · " + st.sub;
    setText($("toggleHot"), c.high_intent ? "取消高意向" : "标记高意向");
    $("dStage").value = c.stage || "";
    // 生成区：模板下拉 + 这个客户生成过的记录
    $("genRequest").value = "";
    $("genOut").hidden = true;
    setText($("genMsg"), "");
    loadGenTemplates();
    await loadGenerations(id);
    $("fuContent").focus();
    await loadFollowups(id);
    await loadAttachments(id);
    await loadTransferOptions();
    setText($("fileMsg"), "");
    $("fileInput").value = "";
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

    const head = document.createElement("div");
    head.className = "fu-head";
    const time = document.createElement("span");
    time.className = "fu-time";
    time.textContent = formatTime(f.created_at) + " · " + (f.display_name || f.username);

    const actions = document.createElement("span");
    actions.className = "fu-actions";
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "ghost fu-btn";
    edit.textContent = "编辑";
    edit.addEventListener("click", () => startEditFollowup(li, f));
    const del = document.createElement("button");
    del.type = "button";
    del.className = "ghost danger fu-btn";
    del.textContent = "删";
    del.addEventListener("click", () => deleteFollowup(f));
    actions.append(edit, del);
    head.append(time, actions);

    const content = document.createElement("span");
    content.className = "fu-content";
    content.textContent = f.content;

    li.append(head, content);
    ul.appendChild(li);
  }
}

// 就地编辑：写完跟进发现漏了一句，直接在这条上补，不用删了重写
function startEditFollowup(li, f) {
  if (li.querySelector(".fu-edit")) return;
  const box = document.createElement("div");
  box.className = "fu-edit";
  const area = document.createElement("textarea");
  area.rows = 2;
  area.value = f.content;
  const row = document.createElement("div");
  row.className = "fu-edit-actions";
  const save = document.createElement("button");
  save.type = "button";
  save.textContent = "保存";
  save.addEventListener("click", () => saveFollowup(f, area.value));
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "ghost";
  cancel.textContent = "取消";
  cancel.addEventListener("click", () => box.remove());
  row.append(save, cancel);
  box.append(area, row);
  li.appendChild(box);
  area.focus();
}

async function saveFollowup(f, content) {
  const text = (content || "").trim();
  if (!text) return setText($("appMsg"), "跟进内容不能为空");
  try {
    await api("/api/customers/" + CURRENT + "/followups/" + f.id, {
      method: "PATCH",
      body: JSON.stringify({ content: text }),
    });
    setText($("appMsg"), "已更新这条跟进。");
    await loadFollowups(CURRENT);
    await loadCustomers();
  } catch (err) {
    setText($("appMsg"), err.message);
  }
}

async function deleteFollowup(f) {
  if (!confirm("删掉这条跟进？不能恢复。")) return;
  try {
    await api("/api/customers/" + CURRENT + "/followups/" + f.id, { method: "DELETE" });
    setText($("appMsg"), "已删除这条跟进。");
    await loadFollowups(CURRENT);
    await loadCustomers();
  } catch (err) {
    setText($("appMsg"), err.message);
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
    closeDetail();
    await loadCustomers();
  } catch (err) {
    setText($("appMsg"), err.message);
  }
}

// 阶段一改就存（没有「保存」按钮——和「写跟进回车就存」同一个原则）
async function changeStage() {
  if (!CURRENT) return;
  const stage = $("dStage").value;
  try {
    await api("/api/customers/" + CURRENT, {
      method: "PATCH",
      body: JSON.stringify({ stage }),
    });
    setText($("appMsg"), stage ? "已标为「" + stage + "」" : "已清空阶段");
    await loadCustomers();
  } catch (err) {
    setText($("appMsg"), err.message);
  }
}

// ---- 编辑客户资料：填错了/有新信息，就地改，不用删了重建 ----

function startEditCustomer() {
  if (!CURRENT_DATA) return;
  const c = CURRENT_DATA;
  $("eName").value = c.contact_name || "";
  $("ePhone").value = c.phone || "";
  $("eCountry").value = c.country || "";
  $("eCompany").value = c.company || "";
  $("eSource").value = c.source || "";
  $("eNote").value = c.note || "";
  $("dEdit").hidden = false;
  $("dMeta").hidden = true;
  setText($("editMsg"), "");
  $("eName").focus();
}

function cancelEditCustomer() {
  $("dEdit").hidden = true;
  $("dMeta").hidden = false;
  setText($("editMsg"), "");
}

async function saveCustomerEdit() {
  if (!CURRENT) return;
  const payload = {
    contact_name: $("eName").value.trim(),
    phone: $("ePhone").value.trim(),
    country: $("eCountry").value.trim(),
    company: $("eCompany").value.trim(),
    source: $("eSource").value.trim(),
    note: $("eNote").value.trim(),
  };
  if (!payload.contact_name || !payload.phone || !payload.country) {
    return setText($("editMsg"), "姓名、号码、国家 三个都要填");
  }
  try {
    // 后端会顺手查重：号码改成别人已经建过的，会回 409
    await api("/api/customers/" + CURRENT, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    setText($("appMsg"), "客户资料已更新。");
    cancelEditCustomer();
    await openCustomer(CURRENT);
    await loadCustomers();
  } catch (err) {
    setText($("editMsg"), err.message);
  }
}

async function toggleHot() {
  if (!CURRENT) return;
  try {
    const c = await api("/api/customers/" + CURRENT);
    await api("/api/customers/" + CURRENT, {
      method: "PATCH",
      body: JSON.stringify({ high_intent: !c.high_intent }),
    });
    await openCustomer(CURRENT);
    await loadCustomers();
  } catch (err) {
    setText($("appMsg"), err.message);
  }
}

async function deleteCustomer() {
  if (!CURRENT) return;
  if (!confirm("确定删掉这个客户？它下面的跟进记录会一起没，而且不能恢复。")) return;
  try {
    await api("/api/customers/" + CURRENT, { method: "DELETE" });
    setText($("appMsg"), "已删除。");
    closeDetail();
    await loadCustomers();
  } catch (err) {
    setText($("appMsg"), err.message);
  }
}

function closeDetail() {
  CURRENT = null;
  $("detail").hidden = true;
  $("detailEmpty").hidden = false;
}

// ---------- 选项库：填过一次，以后大家都能下拉选 ----------

function fillDatalist(id, values) {
  const dl = $(id);
  dl.innerHTML = "";
  for (const v of values || []) {
    const o = document.createElement("option");
    o.value = v;
    dl.appendChild(o);
  }
}

async function loadOptions() {
  try {
    const d = await api("/api/options");
    fillDatalist("optCountry", d.country);
    fillDatalist("optSource", d.source);
  } catch (_) {
    /* 取不到就不给建议，不影响建档 */
  }
}

// ---------- 视图切换（左导航） ----------

function switchView(name) {
  $("viewCustomers").hidden = name !== "customers";
  $("viewCalendar").hidden = name !== "calendar";
  $("viewTemplates").hidden = name !== "templates";
  $("viewProducts").hidden = name !== "products";
  $("viewAi").hidden = name !== "ai";
  document.querySelectorAll(".nav-item[data-view]").forEach((el) => {
    el.classList.toggle("active", el.dataset.view === name);
  });
  if (name === "customers") {
    // 从话术库/别处切回来时，生成下拉可能已经有新模板了
    if (CURRENT) loadGenTemplates();
  }
  if (name === "products") loadProducts();
  if (name === "ai") loadAiView();
  if (name === "templates") {
    loadTags();
    loadTemplates();
  }
  if (name === "calendar") {
    loadReminderCustomers();
    loadReminders();
  }
}

// ---------- 附件（PI / 报价单） ----------

function fmtSize(n) {
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  return (n / 1024 / 1024).toFixed(1) + " MB";
}

async function loadAttachments(id) {
  const data = await api("/api/customers/" + id + "/attachments");
  const ul = $("fileList");
  ul.innerHTML = "";
  if (!data.items.length) {
    const li = document.createElement("li");
    li.className = "hint";
    li.textContent = "还没有附件。PI / 报价单传上来，以后就不用翻聊天记录找文件了。";
    ul.appendChild(li);
    return;
  }
  for (const a of data.items) {
    const li = document.createElement("li");
    li.className = "file-item";
    const link = document.createElement("a");
    link.href = "/api/attachments/" + a.id;
    link.textContent = a.filename;
    const size = document.createElement("span");
    size.className = "file-size";
    size.textContent = fmtSize(a.size) + " · " + (a.display_name || a.username);
    const del = document.createElement("button");
    del.className = "ghost danger";
    del.textContent = "删";
    del.style.marginLeft = "auto";
    del.addEventListener("click", () => deleteAttachment(a.id, a.filename));
    li.append(link, size, del);
    ul.appendChild(li);
  }
}

async function uploadAttachment() {
  if (!CURRENT) return;
  const f = $("fileInput").files[0];
  if (!f) return setText($("fileMsg"), "先选一个文件");
  const fd = new FormData();
  fd.append("file", f);
  try {
    // 注意：这里不能走 api()——它会给请求带上 Content-Type: application/json，
    // 而上传必须让浏览器自己带 multipart 的 boundary，手设会坏。
    const res = await fetch("/api/customers/" + CURRENT + "/attachments", {
      method: "POST",
      body: fd,
    });
    if (!res.ok) {
      const d = await res.json().catch(() => null);
      throw new Error((d && d.detail) || "上传失败：" + res.status);
    }
    $("fileInput").value = "";
    setText($("fileMsg"), "已上传：" + f.name);
    await loadAttachments(CURRENT);
  } catch (err) {
    setText($("fileMsg"), err.message);
  }
}

async function deleteAttachment(attId, name) {
  if (!confirm("删掉附件「" + name + "」？不能恢复。")) return;
  try {
    await api("/api/customers/" + CURRENT + "/attachments/" + attId, { method: "DELETE" });
    setText($("fileMsg"), "已删除。");
    await loadAttachments(CURRENT);
  } catch (err) {
    setText($("fileMsg"), err.message);
  }
}

// ---------- 产品目录（团队共享） ----------

// 自定义属性：材质 / MOQ / 交期…… 固定列追不上业务的描述，所以让用户自己加键值对
function attrRow(key = "", value = "") {
  const row = document.createElement("div");
  row.className = "newrow attr-row";
  const k = document.createElement("input");
  k.className = "attr-key";
  k.placeholder = "属性名（材质 / MOQ / 交期）";
  k.value = key;
  const v = document.createElement("input");
  v.className = "attr-val";
  v.placeholder = "值";
  v.value = value;
  const del = document.createElement("button");
  del.type = "button";
  del.className = "ghost danger attr-del";
  del.textContent = "×";
  del.addEventListener("click", () => row.remove());
  row.append(k, v, del);
  return row;
}

function addAttrRow(key = "", value = "") {
  $("pAttrs").appendChild(attrRow(key, value));
}

function collectAttrs() {
  const out = {};
  for (const row of document.querySelectorAll("#pAttrs .attr-row")) {
    const k = row.querySelector(".attr-key").value.trim();
    const v = row.querySelector(".attr-val").value.trim();
    if (k) out[k] = v;
  }
  return out;
}

function attrsText(attrs) {
  return Object.entries(attrs || {})
    .map(([k, v]) => k + "：" + v)
    .join(" · ");
}

async function loadProducts() {
  try {
    const data = await api("/api/products");
    const ul = $("prodList");
    ul.innerHTML = "";
    setText($("prodCount"), "共 " + data.items.length + " 个");
    if (!data.items.length) {
      const li = document.createElement("li");
      li.className = "hint";
      li.textContent = "还没有产品，上面填个产品名就能加。";
      ul.appendChild(li);
      return;
    }
    for (const p of data.items) {
      const li = document.createElement("li");
      li.className = "cust-item";
      const bits = [p.spec, p.price].filter(Boolean).join(" · ");
      const extra = attrsText(p.attrs);
      li.innerHTML =
        '<div class="cust-name">' + escapeHtml(p.name) + "</div>" +
        '<div class="cust-meta">' + (bits ? "<span>" + escapeHtml(bits) + "</span>" : "") +
        (extra ? "<span>" + escapeHtml(extra) + "</span>" : "") +
        "<span>" + escapeHtml(formatTime(p.created_at)) + "</span></div>";
      const actions = document.createElement("span");
      actions.className = "prod-actions";
      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "ghost fu-btn";
      edit.textContent = "编辑";
      edit.addEventListener("click", (e) => {
        e.stopPropagation();
        startEditProduct(p);
      });
      const del = document.createElement("button");
      del.type = "button";
      del.className = "ghost danger fu-btn";
      del.textContent = "删";
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteProduct(p.id, p.name);
      });
      actions.append(edit, del);
      li.querySelector(".cust-meta").appendChild(actions);
      ul.appendChild(li);
    }
  } catch (err) {
    setText($("prodMsg"), err.message);
  }
}

async function createProduct() {
  const name = $("pName").value.trim();
  if (!name) return setText($("prodMsg"), "产品名必填");
  const payload = {
    name,
    spec: $("pSpec").value.trim(),
    price: $("pPrice").value.trim(),
    attrs: collectAttrs(),
  };
  try {
    if (PROD_EDIT_ID) {
      await api("/api/products/" + PROD_EDIT_ID, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      setText($("prodMsg"), "已保存修改：" + name);
    } else {
      await api("/api/products", { method: "POST", body: JSON.stringify(payload) });
      setText($("prodMsg"), "已加：" + name);
    }
    resetProductForm();
    await loadProducts();
  } catch (err) {
    setText($("prodMsg"), err.message);
  }
}

function resetProductForm() {
  PROD_EDIT_ID = null;
  $("pName").value = "";
  $("pSpec").value = "";
  $("pPrice").value = "";
  $("pAttrs").innerHTML = "";
  $("createProduct").textContent = "加产品";
}

function startEditProduct(p) {
  PROD_EDIT_ID = p.id;
  $("pName").value = p.name;
  $("pSpec").value = p.spec || "";
  $("pPrice").value = p.price || "";
  $("pAttrs").innerHTML = "";
  for (const [k, v] of Object.entries(p.attrs || {})) addAttrRow(k, v);
  $("createProduct").textContent = "保存修改";
  $("pName").focus();
}

async function deleteProduct(pid, name) {
  if (!confirm("删掉产品「" + name + "」？")) return;
  try {
    await api("/api/products/" + pid, { method: "DELETE" });
    await loadProducts();
  } catch (err) {
    setText($("prodMsg"), err.message);
  }
}

// ---------- 话术库：模板 + 标签（团队共享） ----------
// 标签是三组（来源 / 类型 / 场景），不是一棵树——三个维度会交叉，
// 做成树必然卡在「这条该挂哪个枝」，chip 可以一次挂多个。

let TAGS = []; // 在用的标签
let ALL_TAGS = []; // 含停用的（管理区用）
let TAG_GROUPS = []; // 来源 / 类型 / 场景（后端给，避免前后端各写一份）
let TPL_SELECTED = new Set(); // 表单里选中的标签 id
let TPL_EDIT_ID = null; // 正在编辑的模板 id（null = 新建）
let TPL_FILTER_TAG = null; // 列表按哪个标签筛
let TPL_SEARCH = "";
let PROD_EDIT_ID = null; // 正在编辑的产品 id（null = 新增）

function tagRow(grp, list, isOn, onClick) {
  const row = document.createElement("div");
  row.className = "tag-row";
  const label = document.createElement("span");
  label.className = "tag-row-label";
  label.textContent = grp;
  row.appendChild(label);
  for (const t of list) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "tag-chip" + (isOn(t) ? " on" : "");
    chip.textContent = t.name;
    chip.addEventListener("click", () => onClick(t));
    row.appendChild(chip);
  }
  return row;
}

function renderTagPicker() {
  // 表单：多选
  const picker = $("tplTagPicker");
  picker.innerHTML = "";
  for (const grp of TAG_GROUPS) {
    const list = TAGS.filter((t) => t.grp === grp);
    if (!list.length) continue;
    picker.appendChild(
      tagRow(
        grp,
        list,
        (t) => TPL_SELECTED.has(t.id),
        (t) => {
          if (TPL_SELECTED.has(t.id)) TPL_SELECTED.delete(t.id);
          else TPL_SELECTED.add(t.id);
          renderTagPicker();
        }
      )
    );
  }

  // 列表上方：单选筛选
  const filter = $("tplFilterTags");
  filter.innerHTML = "";
  for (const grp of TAG_GROUPS) {
    const list = TAGS.filter((t) => t.grp === grp);
    if (!list.length) continue;
    filter.appendChild(
      tagRow(
        grp,
        list,
        (t) => TPL_FILTER_TAG === t.id,
        (t) => {
          TPL_FILTER_TAG = TPL_FILTER_TAG === t.id ? null : t.id;
          renderTagPicker();
          loadTemplates();
        }
      )
    );
  }
}

async function loadTags() {
  try {
    const d = await api("/api/tags");
    TAGS = d.items;
    TAG_GROUPS = d.groups;
    const sel = $("newTagGrp");
    sel.innerHTML = "";
    for (const g of TAG_GROUPS) {
      const o = document.createElement("option");
      o.value = g;
      o.textContent = g;
      sel.appendChild(o);
    }
    renderTagPicker();
    // 管理区要连停用的一起看（否则删不掉的标签会永远藏在暗处）
    const all = await api("/api/tags?include_inactive=1");
    ALL_TAGS = all.items;
    renderTagAdmin();
  } catch (err) {
    setText($("tplMsg"), err.message);
  }
}

// 标签管理：删掉不用的、把重复的合并成一个。
// 不整理的话标签只会越攒越乱（FB / facebook / 脸书 三条并存）。
function renderTagAdmin() {
  const box = $("tagAdmin");
  if (!box) return;
  box.innerHTML = "";
  for (const grp of TAG_GROUPS) {
    const list = ALL_TAGS.filter((t) => t.grp === grp);
    if (!list.length) continue;
    const row = document.createElement("div");
    row.className = "tag-admin-row";
    const label = document.createElement("span");
    label.className = "tag-row-label";
    label.textContent = grp;
    row.appendChild(label);

    for (const t of list) {
      const item = document.createElement("span");
      item.className = "tag-admin-item" + (t.is_active ? "" : " off");
      item.append(document.createTextNode(t.name + " "));
      const count = document.createElement("b");
      count.textContent = "(" + t.use_count + ")";
      item.appendChild(count);

      const others = list.filter((x) => x.id !== t.id);
      if (others.length) {
        const merge = document.createElement("select");
        merge.className = "tag-merge-select";
        const blank = document.createElement("option");
        blank.value = "";
        blank.textContent = "并到…";
        merge.appendChild(blank);
        for (const other of others) {
          const o = document.createElement("option");
          o.value = other.id;
          o.textContent = other.name;
          merge.appendChild(o);
        }
        merge.addEventListener("change", () => {
          if (merge.value) mergeTag(t.id, Number(merge.value), t.name);
        });
        item.appendChild(merge);
      }

      const del = document.createElement("button");
      del.type = "button";
      del.className = "ghost danger tag-del";
      del.textContent = "删";
      del.addEventListener("click", () => deleteTag(t));
      item.appendChild(del);
      row.appendChild(item);
    }
    box.appendChild(row);
  }
}

async function deleteTag(t) {
  const used = t.use_count ? "有 " + t.use_count + " 个模板在用，" : "";
  if (!confirm("删掉标签「" + t.name + "」？" + used + "删了不能恢复。\n（只想暂时不用它，可以改成停用）"))
    return;
  try {
    await api("/api/tags/" + t.id, { method: "DELETE" });
    setText($("tagMsg"), "已删除：" + t.name);
    await loadTags();
    await loadTemplates();
  } catch (err) {
    setText($("tagMsg"), err.message);
  }
}

async function mergeTag(fromId, intoId, name) {
  if (!confirm("把「" + name + "」合并过去？合并后这个标签消失，原来的模板会换成新的那个。")) return;
  try {
    await api("/api/tags/" + fromId + "/merge", {
      method: "POST",
      body: JSON.stringify({ into_id: intoId }),
    });
    setText($("tagMsg"), "已合并：" + name);
    await loadTags();
    await loadTemplates();
  } catch (err) {
    setText($("tagMsg"), err.message);
  }
}

async function loadTemplates() {
  try {
    const p = new URLSearchParams();
    if (TPL_SEARCH) p.set("q", TPL_SEARCH);
    if (TPL_FILTER_TAG) p.set("tag_id", TPL_FILTER_TAG);
    const qs = p.toString();
    const data = await api("/api/templates" + (qs ? "?" + qs : ""));
    const ul = $("tplList");
    ul.innerHTML = "";
    setText($("tplCount"), "共 " + data.items.length + " 条");
    if (!data.items.length) {
      const li = document.createElement("li");
      li.className = "hint";
      li.textContent =
        TPL_SEARCH || TPL_FILTER_TAG
          ? "当前筛选下没有模板。"
          : "还没有模板。上面写一条，以后一句话就能调出来。";
      ul.appendChild(li);
      return;
    }
    for (const t of data.items) {
      const li = document.createElement("li");
      li.className = "cust-item tpl-item";

      const head = document.createElement("div");
      head.className = "tpl-head";
      const name = document.createElement("div");
      name.className = "cust-name";
      name.textContent = t.title;
      const count = document.createElement("span");
      count.className = "hint";
      count.textContent = "用过 " + t.use_count + " 次";
      head.append(name, count);

      const body = document.createElement("p");
      body.className = "tpl-body";
      body.textContent = t.body;

      const meta = document.createElement("div");
      meta.className = "cust-meta";
      for (const tg of t.tags) {
        const s = document.createElement("span");
        s.className = "tag";
        s.textContent = tg.grp + "·" + tg.name;
        meta.appendChild(s);
      }

      const actions = document.createElement("div");
      actions.className = "tpl-actions";
      const edit = document.createElement("button");
      edit.className = "ghost";
      edit.textContent = "编辑";
      edit.addEventListener("click", () => startEditTemplate(t.id));
      const del = document.createElement("button");
      del.className = "ghost danger";
      del.textContent = "删";
      del.addEventListener("click", () => deleteTemplate(t.id, t.title));
      actions.append(edit, del);

      li.append(head, body, meta, actions);
      ul.appendChild(li);
    }
  } catch (err) {
    setText($("tplMsg"), err.message);
  }
}

async function startEditTemplate(id) {
  try {
    const t = await api("/api/templates/" + id);
    TPL_EDIT_ID = id;
    $("tplFormTitle").textContent = "编辑话术模板";
    $("tplTitle").value = t.title;
    $("tplBody").value = t.body;
    TPL_SELECTED = new Set(t.tags.map((x) => x.id));
    $("cancelEdit").hidden = false;
    $("saveTemplate").textContent = "保存修改";
    renderTagPicker();
    $("tplTitle").focus();
  } catch (err) {
    setText($("tplMsg"), err.message);
  }
}

function cancelEditTemplate() {
  TPL_EDIT_ID = null;
  $("tplFormTitle").textContent = "新建话术模板";
  $("tplTitle").value = "";
  $("tplBody").value = "";
  TPL_SELECTED = new Set();
  $("cancelEdit").hidden = true;
  $("saveTemplate").textContent = "保存模板";
  renderTagPicker();
}

async function saveTemplate() {
  const title = $("tplTitle").value.trim();
  const body = $("tplBody").value.trim();
  if (!title || !body) return setText($("tplMsg"), "标题和正文都要填");
  const payload = { title, body, tag_ids: [...TPL_SELECTED] };
  try {
    if (TPL_EDIT_ID) {
      await api("/api/templates/" + TPL_EDIT_ID, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      setText($("tplMsg"), "已保存修改：" + title);
    } else {
      await api("/api/templates", { method: "POST", body: JSON.stringify(payload) });
      setText($("tplMsg"), "已保存：" + title);
    }
    cancelEditTemplate();
    await loadTemplates();
    syncGenTemplates(); // 客户详情里那个下拉要立刻能选到新模板
  } catch (err) {
    setText($("tplMsg"), err.message);
  }
}

async function deleteTemplate(id, title) {
  if (!confirm("删掉模板「" + title + "」？不能恢复。")) return;
  try {
    await api("/api/templates/" + id, { method: "DELETE" });
    setText($("tplMsg"), "已删除。");
    if (TPL_EDIT_ID === id) cancelEditTemplate();
    await loadTemplates();
    syncGenTemplates();
  } catch (err) {
    setText($("tplMsg"), err.message);
  }
}

async function addTag() {
  const grp = $("newTagGrp").value;
  const name = $("newTagName").value.trim();
  if (!name) return setText($("tplMsg"), "标签名要填");
  try {
    const d = await api("/api/tags", {
      method: "POST",
      body: JSON.stringify({ grp, name }),
    });
    $("newTagName").value = "";
    await loadTags();
    TPL_SELECTED.add(d.id);
    renderTagPicker();
    setText($("tplMsg"), "已加标签：" + grp + "·" + name);
  } catch (err) {
    setText($("tplMsg"), err.message);
  }
}

let tplSearchTimer = null;
function onTplSearchInput() {
  clearTimeout(tplSearchTimer);
  tplSearchTimer = setTimeout(() => {
    TPL_SEARCH = $("tplSearch").value.trim();
    loadTemplates();
  }, 250);
}

// ---------- 按客户生成话术（AI 出草稿，人负责） ----------

async function loadGenTemplates() {
  try {
    const data = await api("/api/templates");
    const sel = $("genTemplate");
    sel.innerHTML = "";
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "不用模板 · 直接按客户生成新的";
    sel.appendChild(none);
    for (const t of data.items) {
      const o = document.createElement("option");
      o.value = t.id;
      o.textContent = t.title + (t.use_count ? "（用过 " + t.use_count + " 次）" : "");
      sel.appendChild(o);
    }
  } catch (_) {
    /* 模板取不到也能直接按客户生成 */
  }
}

// 话术库那边一存/一删，客户详情的下拉立刻跟着变——不用重新打开客户
function syncGenTemplates() {
  if ($("genTemplate")) loadGenTemplates();
}

async function loadGenerations(id) {
  try {
    const d = await api("/api/customers/" + id + "/generations");
    const ul = $("genList");
    ul.innerHTML = "";
    for (const g of d.items) {
      const li = document.createElement("li");
      li.className = "fu-item";
      const time = document.createElement("span");
      time.className = "fu-time";
      time.textContent =
        formatTime(g.created_at) +
        " · " +
        (g.display_name || g.username) +
        (g.template_title ? " · " + g.template_title : "");
      const body = document.createElement("span");
      body.className = "fu-content";
      body.textContent = g.output;
      li.append(time, body);
      ul.appendChild(li);
    }
  } catch (_) {
    /* 生成记录取不到不影响主流程 */
  }
}

async function doGenerate() {
  if (!CURRENT) return;
  const btn = $("doGenerate");
  btn.disabled = true;
  setText($("genMsg"), "正在生成…");
  try {
    const payload = { request: $("genRequest").value.trim() };
    const tplId = $("genTemplate").value;
    if (tplId) payload.template_id = Number(tplId);
    const provId = $("genProvider").value;
    if (provId) payload.provider_id = Number(provId);
    const d = await api("/api/customers/" + CURRENT + "/generate", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    $("genText").value = d.output;
    $("genOut").hidden = false;
    // 把「依据什么生成的」说清楚——业务能核对，AI 就不是黑箱
    const b = d.basis || {};
    const parts = ["客户资料"];
    parts.push(b.followups ? b.followups + " 条跟进" : "还没有跟进记录");
    if (b.template) parts.push("模板「" + b.template + "」");
    const thin =
      b.reliable === false ? "。⚠ 跟进不足 2 条，草稿会比较泛，建议先补两条跟进再生成" : "";
    setText(
      $("genMsg"),
      "已生成（" + d.model + "）。依据：" + parts.join(" + ") + thin + "。改完再复制——AI 出草稿，人负责。"
    );
    await loadGenerations(CURRENT);
    await loadCustomers(); // 模板的使用次数变了，列表上的「用过 N 次」要跟上
  } catch (err) {
    setText($("genMsg"), err.message);
  } finally {
    btn.disabled = false;
  }
}

async function copyGen() {
  const text = $("genText").value;
  try {
    await navigator.clipboard.writeText(text);
    setText($("genMsg"), "已复制。");
  } catch (_) {
    // 浏览器不给剪贴板权限时，至少帮人选中，别让人自己拖
    $("genText").select();
    setText($("genMsg"), "浏览器不让直接复制，已帮你选中，按 Ctrl+C。");
  }
}

// ---------- 日历看板：给自己排「哪天联系谁」 ----------
// 红点 = 今天及以前还没做完的条数（昨天漏掉的今天更该提醒）。

let CAL_MONTH = new Date();
CAL_MONTH.setDate(1);
let CAL_SELECTED = new Date();
let REMINDERS = {}; // "YYYY-MM-DD" -> [提醒]

function ymd(d) {
  const p = (n) => String(n).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
}

async function loadReminders() {
  const first = new Date(CAL_MONTH);
  const last = new Date(CAL_MONTH.getFullYear(), CAL_MONTH.getMonth() + 1, 0);
  try {
    const d = await api(
      "/api/reminders?date_from=" + ymd(first) + "&date_to=" + ymd(last)
    );
    REMINDERS = {};
    for (const r of d.items) {
      const key = String(r.due_date).slice(0, 10);
      (REMINDERS[key] = REMINDERS[key] || []).push(r);
    }
  } catch (err) {
    setText($("remMsg"), err.message);
  }
  renderCalendar();
  renderDay();
}

function renderCalendar() {
  $("calTitle").textContent =
    CAL_MONTH.getFullYear() + " 年 " + (CAL_MONTH.getMonth() + 1) + " 月";
  const grid = $("calGrid");
  grid.innerHTML = "";
  const startWeekday = (CAL_MONTH.getDay() + 6) % 7; // 周一当一周第一天
  const daysInMonth = new Date(
    CAL_MONTH.getFullYear(),
    CAL_MONTH.getMonth() + 1,
    0
  ).getDate();
  const todayKey = ymd(new Date());
  const selKey = ymd(CAL_SELECTED);

  for (let i = 0; i < startWeekday; i++) {
    const blank = document.createElement("div");
    blank.className = "cal-cell empty";
    grid.appendChild(blank);
  }
  for (let day = 1; day <= daysInMonth; day++) {
    const date = new Date(CAL_MONTH.getFullYear(), CAL_MONTH.getMonth(), day);
    const key = ymd(date);
    const pending = (REMINDERS[key] || []).filter((x) => !x.done).length;
    const cell = document.createElement("button");
    cell.type = "button";
    cell.className =
      "cal-cell" + (key === todayKey ? " today" : "") + (key === selKey ? " selected" : "");
    const num = document.createElement("span");
    num.className = "cal-num";
    num.textContent = String(day);
    cell.appendChild(num);
    if (pending) {
      const dot = document.createElement("span");
      dot.className = "cal-dot";
      dot.textContent = String(pending);
      cell.appendChild(dot);
    }
    cell.addEventListener("click", () => {
      CAL_SELECTED = date;
      renderCalendar();
      renderDay();
    });
    grid.appendChild(cell);
  }
}

function renderDay() {
  const key = ymd(CAL_SELECTED);
  $("calDayTitle").textContent =
    CAL_SELECTED.getMonth() + 1 + " 月 " + CAL_SELECTED.getDate() + " 日安排";
  const ul = $("calList");
  ul.innerHTML = "";
  const items = REMINDERS[key] || [];
  if (!items.length) {
    const li = document.createElement("li");
    li.className = "hint";
    li.textContent = "这一天还没有安排。下面选个客户，加到这一天。";
    ul.appendChild(li);
    return;
  }
  for (const r of items) {
    const li = document.createElement("li");
    li.className = "cust-item rem-item" + (r.done ? " done" : "");
    const name = document.createElement("div");
    name.className = "cust-name";
    name.textContent = r.contact_name + (r.company ? " · " + r.company : "");
    const note = document.createElement("div");
    note.className = "cust-meta";
    note.textContent = r.done ? "已联系" : r.note || "（没写备注）";
    const actions = document.createElement("div");
    actions.className = "tpl-actions";
    const noteBtn = document.createElement("button");
    noteBtn.type = "button";
    noteBtn.className = "ghost";
    noteBtn.textContent = "改备注";
    noteBtn.addEventListener("click", () => editReminderNote(r));
    const done = document.createElement("button");
    done.type = "button";
    done.className = "ghost";
    done.textContent = r.done ? "撤销完成" : "标为已联系";
    done.addEventListener("click", () => toggleReminder(r));
    const del = document.createElement("button");
    del.type = "button";
    del.className = "ghost danger";
    del.textContent = "删";
    del.addEventListener("click", () => deleteReminder(r));
    actions.append(noteBtn, done, del);
    li.append(name, note, actions);
    ul.appendChild(li);
  }
}

async function toggleReminder(r) {
  try {
    await api("/api/reminders/" + r.id, {
      method: "PATCH",
      body: JSON.stringify({ done: !r.done }),
    });
    await refreshReminders();
  } catch (err) {
    setText($("remMsg"), err.message);
  }
}

async function editReminderNote(r) {
  const note = prompt("改备注：", r.note || "");
  if (note === null) return;
  try {
    await api("/api/reminders/" + r.id, {
      method: "PATCH",
      body: JSON.stringify({ note }),
    });
    await refreshReminders();
    setText($("remMsg"), "备注已更新。");
  } catch (err) {
    setText($("remMsg"), err.message);
  }
}

async function deleteReminder(r) {
  if (!confirm("删掉这条提醒？")) return;
  try {
    await api("/api/reminders/" + r.id, { method: "DELETE" });
    await refreshReminders();
  } catch (err) {
    setText($("remMsg"), err.message);
  }
}

async function addReminder() {
  const cid = $("remCustomer").value;
  if (!cid) return setText($("remMsg"), "先选一个客户");
  try {
    await api("/api/reminders", {
      method: "POST",
      body: JSON.stringify({
        customer_id: Number(cid),
        due_date: ymd(CAL_SELECTED),
        note: $("remNote").value.trim(),
      }),
    });
    $("remNote").value = "";
    setText($("remMsg"), "已加到 " + ymd(CAL_SELECTED));
    await refreshReminders();
  } catch (err) {
    setText($("remMsg"), err.message);
  }
}

async function loadReminderCustomers() {
  try {
    const d = await api("/api/customers");
    const sel = $("remCustomer");
    sel.innerHTML = "";
    for (const c of d.items) {
      const o = document.createElement("option");
      o.value = c.id;
      o.textContent = titleOf(c) + (c.company ? " · " + c.company : "");
      sel.appendChild(o);
    }
  } catch (_) {
    /* 客户列表取不到也不影响看日历 */
  }
}

async function loadPendingCount() {
  try {
    const d = await api("/api/reminders/pending-count");
    const badge = $("reminderBadge");
    if (d.count > 0) {
      badge.textContent = d.count > 99 ? "99+" : String(d.count);
      badge.hidden = false;
    } else {
      badge.textContent = ""; // 没有待办时连「0」都不留，免得导航里挂个空数
      badge.hidden = true;
    }
  } catch (_) {
    /* 红点取不到就不显示，不打扰主流程 */
  }
}

async function refreshReminders() {
  await loadReminders();
  await loadPendingCount();
}

function calShift(months) {
  CAL_MONTH = new Date(CAL_MONTH.getFullYear(), CAL_MONTH.getMonth() + months, 1);
  loadReminders();
}

function calGoToday() {
  const now = new Date();
  CAL_MONTH = new Date(now.getFullYear(), now.getMonth(), 1);
  CAL_SELECTED = now;
  loadReminders();
}

// ---------- 智能体（管理员登记渠道 + 分配月度额度） ----------
// 注意：这里**不涉及对外收款**。管理员配的是公司自己申请的 key，
// 额度是内部成本控制——同事用公司额度，不是谁的私人钱包。

let PROVIDERS = [];
let PROV_EDIT_ID = null;
let IS_ADMIN = false;

async function loadProviders() {
  try {
    const d = await api("/api/providers");
    PROVIDERS = d.items;
    renderProviderList();
    fillGenProviders();
  } catch (err) {
    setText($("pvMsg"), err.message);
  }
}

// 生成话术那个下拉：默认走服务器配置，其余是管理员登记的渠道
function fillGenProviders() {
  const sel = $("genProvider");
  if (!sel) return;
  const keep = sel.value;
  sel.innerHTML = "";
  const def = document.createElement("option");
  def.value = "";
  def.textContent = "服务器默认";
  sel.appendChild(def);
  for (const p of PROVIDERS.filter((x) => x.is_active)) {
    const o = document.createElement("option");
    o.value = p.id;
    o.textContent = p.name;
    sel.appendChild(o);
  }
  if (keep) sel.value = keep;
}

function renderProviderList() {
  const ul = $("pvList");
  if (!ul) return;
  ul.innerHTML = "";
  setText($("pvCount"), "共 " + PROVIDERS.length + " 个");
  if (!PROVIDERS.length) {
    const li = document.createElement("li");
    li.className = "hint";
    li.textContent = IS_ADMIN
      ? "还没有渠道，上面加一个。"
      : "管理员还没配渠道，生成时走服务器默认那套。";
    ul.appendChild(li);
    return;
  }
  for (const p of PROVIDERS) {
    const li = document.createElement("li");
    li.className = "cust-item";
    const st = p.has_key ? "已配 key" : "⚠ 未配 key";
    li.innerHTML =
      '<div class="cust-row"><div class="cust-main">' +
      '<div class="cust-name">' + escapeHtml(p.name) + "</div>" +
      '<div class="cust-meta"><span>' + escapeHtml(p.model) + "</span>" +
      "<span>" + escapeHtml(p.base_url) + "</span><span>" + st + "</span></div></div>" +
      '<div class="cust-side"><span class="contact' + (p.is_active ? "" : " warn") + '">' +
      (p.is_active ? "启用中" : "已停用") + "</span></div></div>";

    if (IS_ADMIN) {
      const actions = document.createElement("span");
      actions.className = "prod-actions";
      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "ghost fu-btn";
      edit.textContent = "编辑";
      edit.addEventListener("click", () => startEditProvider(p));
      const del = document.createElement("button");
      del.type = "button";
      del.className = "ghost danger fu-btn";
      del.textContent = "删";
      del.addEventListener("click", () => deleteProvider(p));
      actions.append(edit, del);
      li.querySelector(".cust-meta").appendChild(actions);
    }
    ul.appendChild(li);
  }
}

function startEditProvider(p) {
  PROV_EDIT_ID = p.id;
  $("pvFormTitle").textContent = "编辑智能体：" + p.name;
  $("pvName").value = p.name;
  $("pvBase").value = p.base_url;
  $("pvModel").value = p.model;
  $("pvKey").value = "";
  $("pvKey").placeholder = p.has_key ? "留空 = 不改 key" : "这个渠道还没配 key";
  $("cancelProvider").hidden = false;
  $("saveProvider").textContent = "保存修改";
  $("pvName").focus();
}

function cancelProviderEdit() {
  PROV_EDIT_ID = null;
  $("pvFormTitle").textContent = "添加智能体";
  $("pvName").value = "";
  $("pvBase").value = "";
  $("pvModel").value = "";
  $("pvKey").value = "";
  $("pvKey").placeholder = "API Key（加密保存，保存后不回显）";
  $("cancelProvider").hidden = true;
  $("saveProvider").textContent = "保存";
}

async function saveProvider() {
  const payload = {
    name: $("pvName").value.trim(),
    base_url: $("pvBase").value.trim(),
    model: $("pvModel").value.trim(),
  };
  const key = $("pvKey").value.trim();
  if (!payload.name || !payload.base_url || !payload.model) {
    return setText($("pvMsg"), "名称、接口地址、模型名都要填");
  }
  try {
    if (PROV_EDIT_ID) {
      if (key) payload.api_key = key; // 留空就不动原来的 key
      await api("/api/providers/" + PROV_EDIT_ID, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      setText($("pvMsg"), "已保存修改。");
    } else {
      payload.api_key = key;
      await api("/api/providers", { method: "POST", body: JSON.stringify(payload) });
      setText($("pvMsg"), "已添加：" + payload.name);
    }
    cancelProviderEdit();
    await loadProviders();
  } catch (err) {
    setText($("pvMsg"), err.message);
  }
}

async function deleteProvider(p) {
  if (!confirm("删掉渠道「" + p.name + "」？之后生成就不能选它了。")) return;
  try {
    await api("/api/providers/" + p.id, { method: "DELETE" });
    setText($("pvMsg"), "已删除：" + p.name);
    await loadProviders();
  } catch (err) {
    setText($("pvMsg"), err.message);
  }
}

async function loadLimits() {
  if (!IS_ADMIN) return;
  try {
    const d = await api("/api/limits");
    const ul = $("limitList");
    ul.innerHTML = "";
    for (const r of d.items) {
      const li = document.createElement("li");
      li.className = "cust-item";
      li.innerHTML =
        '<div class="cust-row"><div class="cust-main">' +
        '<div class="cust-name">' + escapeHtml(r.display_name || r.username) + "</div>" +
        '<div class="cust-meta"><span>本月调用 ' + r.calls + " 次</span>" +
        "<span>已用 " + r.used_tokens + " tokens</span></div></div></div>";
      const row = document.createElement("div");
      row.className = "newrow";
      row.style.marginTop = "8px";
      const input = document.createElement("input");
      input.type = "number";
      input.min = "0";
      input.value = r.monthly_tokens;
      input.placeholder = "月度 token 额度（0 = 不限）";
      const save = document.createElement("button");
      save.type = "button";
      save.className = "ghost";
      save.textContent = "保存额度";
      save.addEventListener("click", () => saveLimit(r.user_id, input.value));
      row.append(input, save);
      li.appendChild(row);
      ul.appendChild(li);
    }
  } catch (err) {
    setText($("limitMsg"), err.message);
  }
}

async function saveLimit(userId, value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n < 0) return setText($("limitMsg"), "额度要填 0 或正数");
  try {
    await api("/api/limits/" + userId, {
      method: "PUT",
      body: JSON.stringify({ monthly_tokens: n }),
    });
    setText($("limitMsg"), "已保存。");
    await loadLimits();
  } catch (err) {
    setText($("limitMsg"), err.message);
  }
}

async function loadAiView() {
  $("aiAdminCard").hidden = !IS_ADMIN;
  $("limitCard").hidden = !IS_ADMIN;
  await loadProviders();
  if (IS_ADMIN) await loadLimits();
}

// ---------- 入口 ----------

// 搜索：敲字后停 250ms 再查，免得每按一个键都打一次接口
let searchTimer = null;
function onSearchInput() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    SEARCH = $("searchBox").value.trim();
    loadCustomers();
  }, 250);
}

function bind() {
  $("logout").addEventListener("click", logout);
  $("searchBox").addEventListener("input", onSearchInput);
  $("stageFilter").addEventListener("change", () => {
    STAGE_FILTER = $("stageFilter").value;
    loadCustomers();
  });
  $("idleFilter").addEventListener("change", () => {
    IDLE_DAYS = $("idleFilter").value;
    loadCustomers();
  });
  $("dStage").addEventListener("change", changeStage);

  // 编辑客户资料
  $("editCustomer").addEventListener("click", startEditCustomer);
  $("saveCustomer").addEventListener("click", saveCustomerEdit);
  $("cancelCustomer").addEventListener("click", cancelEditCustomer);

  // 话术库
  $("saveTemplate").addEventListener("click", saveTemplate);
  $("cancelEdit").addEventListener("click", cancelEditTemplate);
  $("addTag").addEventListener("click", addTag);
  $("tplSearch").addEventListener("input", onTplSearchInput);

  // 生成话术
  $("doGenerate").addEventListener("click", doGenerate);
  $("copyGen").addEventListener("click", copyGen);

  // 智能体
  $("saveProvider").addEventListener("click", saveProvider);
  $("cancelProvider").addEventListener("click", cancelProviderEdit);

  // 产品自定义属性
  $("addAttr").addEventListener("click", () => addAttrRow());

  // 日历看板
  $("addReminder").addEventListener("click", addReminder);
  $("calPrev").addEventListener("click", () => calShift(-1));
  $("calNext").addEventListener("click", () => calShift(1));
  $("calToday").addEventListener("click", calGoToday);

  $("createCustomer").addEventListener("click", createCustomer);
  // 「建档 ≤30 秒」：每个框都能回车提交，手不用离开键盘
  for (const id of NEW_FIELDS) {
    $(id).addEventListener("keydown", (e) => { if (e.key === "Enter") createCustomer(); });
  }

  $("addFollowup").addEventListener("click", addFollowup);
  // 「写跟进 ≤10 秒」：一个框，回车就存
  $("fuContent").addEventListener("keydown", (e) => { if (e.key === "Enter") addFollowup(); });
  $("doTransfer").addEventListener("click", doTransfer);
  $("toggleHot").addEventListener("click", toggleHot);
  $("deleteCustomer").addEventListener("click", deleteCustomer);

  // 左导航：切换视图
  document.querySelectorAll(".nav-item[data-view]").forEach((el) => {
    el.addEventListener("click", () => switchView(el.dataset.view));
  });

  // 附件
  $("uploadFile").addEventListener("click", uploadAttachment);

  // 产品目录
  $("createProduct").addEventListener("click", createProduct);
  for (const id of ["pName", "pSpec", "pPrice"]) {
    $(id).addEventListener("keydown", (e) => { if (e.key === "Enter") createProduct(); });
  }
}

async function boot() {
  bind();
  try {
    const data = await api("/api/me");
    if (data.user) {
      showApp(data.user);
      return;
    }
  } catch (_) {
    /* 会话过期或接口不可用——都回登录页，别停在一片空白上 */
  }
  goLogin();
}

boot();
