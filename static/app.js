// 按 id 拿到页面元素
const companyInput = document.getElementById("company");
const extraInput = document.getElementById("extra");
const providerSelect = document.getElementById("provider");
const goButton = document.getElementById("go");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const resultTitleEl = document.getElementById("resultTitle");
const historyEl = document.getElementById("history");

function setStatus(text) {
  statusEl.textContent = text;
}

// 把 HTML 特殊字符转义成实体，防止外部文本被当成标签执行
function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// 把 LLM 返回的纯文本渲染成 HTML：先转义，再把 **xxx** 换成 <strong>。
// 顺序不能反——先替换粗体的话，外部文本里的 <script> 就会被当成真标签（XSS）。
function renderMarkdown(text) {
  return escapeHtml(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
}

// 后端统一存 UTC 时间。转成浏览者所在时区再显示。
// 老数据可能没带时区后缀（没有 +00:00），补个 Z 当 UTC 处理，否则浏览器会误当成当地时间。
function formatTime(iso) {
  const hasTz = /[zZ]$|[+-]\d\d:?\d\d$/.test(iso);
  const d = new Date(hasTz ? iso : iso + "Z");
  return isNaN(d) ? iso : d.toLocaleString("zh-CN", { hour12: false });
}

// 拉一次「可用的智能体」填下拉。
// 开源版不再内置任何服务器 key：生成一次烧一次额度，用的是你自己的渠道。
async function loadProviders() {
  try {
    const res = await fetch("/api/providers");
    if (res.status === 401) {
      providerSelect.innerHTML = '<option value="">需要登录</option>';
      providerSelect.disabled = true;
      setStatus("AI 背调要登录后才能用：生成一次消耗的是你自己的智能体额度。左下角可以登录 / 注册。");
      return;
    }
    const data = await res.json();
    const items = data.items || [];
    if (!items.length) {
      providerSelect.innerHTML = '<option value="">还没配置智能体</option>';
      providerSelect.disabled = true;
      setStatus("还没有可用的智能体：登录后到「智能体」页添加一个（填自己的 Base URL / 模型 / API Key）。");
      return;
    }
    providerSelect.innerHTML = "";
    for (const p of items) {
      const opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = p.name + " · " + p.model;
      providerSelect.appendChild(opt);
    }
    providerSelect.disabled = false;
  } catch (err) {
    setStatus("读取智能体失败：" + err.message);
  }
}

// 读历史列表，渲染成 <li>。后端只给当前登录用户自己的记录。
async function loadHistory() {
  try {
    const res = await fetch("/history");
    if (res.status === 401) {
      historyEl.innerHTML = "";
      const li = document.createElement("li");
      li.textContent = "登录后能看到你自己的背调记录";
      historyEl.appendChild(li);
      return;
    }
    const data = await res.json();
    historyEl.innerHTML = "";
    if (!data.items.length) {
      const li = document.createElement("li");
      li.textContent = "（还没有记录）";
      historyEl.appendChild(li);
      return;
    }
    for (const item of data.items) {
      const li = document.createElement("li");
      li.textContent = item.company + " —— " + formatTime(item.created_at);
      li.className = "history-item";
      li.title = "点击查看这条详情";
      li.addEventListener("click", () => showDetail(item.id));
      historyEl.appendChild(li);
    }
  } catch (err) {
    historyEl.innerHTML = "";
    const li = document.createElement("li");
    li.textContent = "历史加载失败：" + err.message;
    historyEl.appendChild(li);
  }
}

// 点历史某条：取回该条完整正文，显示在结果区
async function showDetail(id) {
  setStatus("正在读取历史记录 #" + id + "…");
  try {
    const res = await fetch("/history/" + id);
    const data = await res.json();
    if (data.error) {
      setStatus("出错了：" + data.error);
      return;
    }
    resultTitleEl.textContent = "历史记录 #" + data.id;
    resultEl.innerHTML = renderMarkdown(data.analysis);
    setStatus("已加载历史记录 #" + data.id + "：" + data.company);
  } catch (err) {
    setStatus("读取失败：" + err.message);
  }
}

// 点按钮要做的事
async function analyze() {
  const company = companyInput.value.trim();
  if (!company) {
    setStatus("先填一个公司名");
    companyInput.focus();
    return;
  }

  goButton.disabled = true; // 防连点
  setStatus("正在生成，请稍等（大约 10 秒）…");
  resultEl.textContent = "";

  try {
    const payload = { company: company, extra: extraInput.value.trim() };
    if (providerSelect.value) payload.provider_id = Number(providerSelect.value);

    const res = await fetch("/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      // 401 = 没登录；400 = 还没配智能体。两种都说清楚下一步做什么
      const detail = data.detail || "HTTP " + res.status;
      setStatus(res.status === 401 ? "要登录才能生成：" + detail : detail);
      return;
    }
    if (data.error) {
      setStatus("出错了：" + data.error);
      resultEl.textContent = JSON.stringify(data, null, 2);
    } else {
      setStatus("完成，已存为第 " + data.id + " 条");
      resultTitleEl.textContent = "本次结果";
      resultEl.innerHTML = renderMarkdown(data.analysis);
      loadHistory();
    }
  } catch (err) {
    setStatus("请求失败：" + err.message);
  } finally {
    goButton.disabled = false;
  }
}

goButton.addEventListener("click", analyze);
companyInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    analyze();
  }
});

loadProviders(); // 先问清楚有没有可用渠道，再让用户敲公司名
loadHistory();    // 页面一打开就先显示自己的历史
