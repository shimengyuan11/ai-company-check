// 按 id 拿到页面元素
const companyInput = document.getElementById("company");
const extraInput = document.getElementById("extra");
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

// 读历史列表，渲染成 <li>
async function loadHistory() {
  try {
    const res = await fetch("/history");
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
      li.textContent = item.company + " —— " + item.created_at;
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
    const res = await fetch("/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ company: company, extra: extraInput.value.trim() }),
    });
    const data = await res.json();

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

loadHistory(); // 页面一打开就先显示历史
