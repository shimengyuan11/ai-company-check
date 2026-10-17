// 登录 / 注册页 —— 只管「进来」和「回到原来的地方」，业务逻辑一概不碰。
// 「我是谁」由后端会话决定：这里只提交凭据、然后跳走。

const $ = (id) => document.getElementById(id);

let registerMode = false;
let signupOpen = true;

// 只接受站内路径。否则 ?next=https://evil.example 就成了开放重定向。
function nextTarget() {
  const raw = new URLSearchParams(location.search).get("next") || "/app";
  const ok = raw.startsWith("/") && !raw.startsWith("//");
  return ok ? raw : "/app";
}

function setMsg(text) {
  $("authMsg").textContent = text || "";
}

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
  // 后端给的 detail 原样用，别自己编提示
  if (!res.ok) throw new Error((data && data.detail) || "请求失败：" + res.status);
  return data;
}

function renderMode() {
  $("authTitle").textContent = registerMode ? "注册" : "登录";
  $("submitAuth").textContent = registerMode ? "注册并进入" : "登录";
  $("toggleMode").textContent = registerMode ? "已有账号？去登录" : "没有账号？用邀请码注册";
  $("codeField").hidden = !registerMode;
  $("password").autocomplete = registerMode ? "new-password" : "current-password";
  // 注册关着的时候把话说清楚，别让人填完一整个表单才吃一个 403
  if (registerMode && !signupOpen) {
    setMsg("注册已关闭：要管理员在服务端配好邀请码（SIGNUP_CODE）才能注册。");
  } else {
    setMsg("");
  }
}

async function submitAuth(event) {
  event.preventDefault();
  const username = $("username").value.trim();
  const password = $("password").value;
  const code = $("code").value.trim();
  if (!username || !password) return setMsg("用户名和密码都要填");

  const btn = $("submitAuth");
  btn.disabled = true;
  try {
    const body = registerMode ? { username, password, code } : { username, password };
    await api(registerMode ? "/api/register" : "/api/login", {
      method: "POST",
      body: JSON.stringify(body),
    });
    $("password").value = "";
    $("code").value = "";
    location.replace(nextTarget()); // replace：登录完按返回不该回到登录页
  } catch (err) {
    setMsg(err.message);
    btn.disabled = false;
  }
}

function toggleMode(event) {
  event.preventDefault();
  registerMode = !registerMode;
  renderMode();
  $("username").focus();
}

async function boot() {
  try {
    const st = await api("/api/signup-status");
    signupOpen = !!st.open;
  } catch (_) {
    /* 接口不可用就按「开放」渲染，真注册时后端还会再拦一道 */
  }
  try {
    const me = await api("/api/me");
    if (me.user) {
      location.replace(nextTarget()); // 已经登录，不用再登一次
      return;
    }
  } catch (_) {
    /* 未登录就是 401，属正常分支 */
  }
  renderMode();
  $("authForm").addEventListener("submit", submitAuth);
  $("toggleMode").addEventListener("click", toggleMode);
  $("username").focus();
}

boot();
