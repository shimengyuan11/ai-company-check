// 外贸客户工作台 —— 前端逻辑
// 一句话：所有数据都通过 /api/* 拿；「我是谁」由后端会话决定，前端只管显示。

const $ = (id) => document.getElementById(id);
let CURRENT = null; // 当前打开的客户 id
let SEARCH = ""; // 当前搜索词
let STAGE_FILTER = ""; // 阶段筛选："" = 全部，"none" = 未分组，其余为具体阶段
let SORT = "recent"; // recent（最近建档）/ stale（最久没联系）
let STAGES = []; // 后端给的固定五档，避免前后端各写一份

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
  setText($("whoami"), user.display_name + (user.is_admin ? "（管理员）" : ""));
  $("newName").focus();
  switchView("customers");
  loadOptions();
  loadCustomers();
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
    if (SORT !== "recent") p.set("sort", SORT);
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
      li.innerHTML =
        '<div class="cust-name">' + escapeHtml(titleOf(c)) + "</div>" +
        '<div class="cust-meta">' + tags.join("") +
        (sub ? "<span>" + escapeHtml(sub) + "</span>" : "") +
        "<span>跟进 " + c.followup_count + " 条</span></div>";
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
    $("detailEmpty").hidden = true;
    $("detail").hidden = false;
    setText($("dTitle"), titleOf(c));
    const bits = [];
    if (c.company) bits.push("公司：" + c.company);
    if (c.phone) bits.push("号码：" + c.phone);
    if (c.country) bits.push("国家：" + c.country);
    if (c.source) bits.push("来源：" + c.source);
    if (c.note) bits.push(c.note);
    bits.push("建档于 " + formatTime(c.created_at));
    setText($("dMeta"), bits.join(" ｜ "));
    setText($("toggleHot"), c.high_intent ? "取消高意向" : "标记高意向");
    $("dStage").value = c.stage || "";
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
  $("viewProducts").hidden = name !== "products";
  document.querySelectorAll(".nav-item[data-view]").forEach((el) => {
    el.classList.toggle("active", el.dataset.view === name);
  });
  if (name === "products") loadProducts();
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
      li.innerHTML =
        '<div class="cust-name">' + escapeHtml(p.name) + "</div>" +
        '<div class="cust-meta">' + (bits ? "<span>" + escapeHtml(bits) + "</span>" : "") +
        "<span>" + escapeHtml(formatTime(p.created_at)) + "</span></div>";
      const del = document.createElement("button");
      del.className = "ghost danger";
      del.textContent = "删";
      del.style.marginLeft = "auto";
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteProduct(p.id, p.name);
      });
      li.querySelector(".cust-meta").appendChild(del);
      ul.appendChild(li);
    }
  } catch (err) {
    setText($("prodMsg"), err.message);
  }
}

async function createProduct() {
  const name = $("pName").value.trim();
  if (!name) return setText($("prodMsg"), "产品名必填");
  try {
    await api("/api/products", {
      method: "POST",
      body: JSON.stringify({
        name,
        spec: $("pSpec").value.trim(),
        price: $("pPrice").value.trim(),
      }),
    });
    $("pName").value = "";
    $("pSpec").value = "";
    $("pPrice").value = "";
    setText($("prodMsg"), "已加：" + name);
    $("pName").focus();
    await loadProducts();
  } catch (err) {
    setText($("prodMsg"), err.message);
  }
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
  $("sortSelect").addEventListener("change", () => {
    SORT = $("sortSelect").value;
    loadCustomers();
  });
  $("dStage").addEventListener("change", changeStage);

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
