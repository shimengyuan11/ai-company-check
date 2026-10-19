# Meridian · 外贸客户工作台

一个给外贸 / BD / 销售用的小系统：**管客户、记跟进、写话术、查公司底细**，一个人一个号，客户各管各的。

> 在线演示：https://ai-company-check.onrender.com
> 它从一个「AI 公司背调工具」长起来，后来补上了真正的日常工作面（客户 / 跟进 / 话术 / 日历），现在是两者并列。

---

## 为什么有这个项目

我是跨行做的这个项目，出发点是自己在业务里真实遇到的两个麻烦：

1. **收到陌生公司线索时，判断靠不靠谱要花 20~30 分钟**（Google + LinkedIn + 海关数据翻一遍）。
2. **客户和跟进散在微信、WhatsApp、Excel 里**——人一换，前面聊到哪一步就没了。

所以这里有两块：一块是**背调**（把上面的 20 分钟压缩成一次点击），一块是**工作台**（客户建档 / 跟进 / 阶段 / 话术 / 日历提醒）。

## 功能

**AI 公司背调**
- 输入公司名 + 可选补充信息，输出五段式报告：公司概况 / 主营 / 规模 / 风险点 / 建议
- 提示词硬性禁止编造数字与客户名，不确定的一律写「未验证：需人工核实」
- 历史记录按人隔离，只有自己（或管理员）看得到

**客户工作台**
- 客户建档（姓名 / 号码 / 国家必填，回车即存）、防撞单（归一化号码或尾号 9 位相同即 409）
- 三栏外壳：左导航 / 中列表 / 右详情；阶段六档、未联系天数（3/5/7/10 天筛选）
- 跟进可写可改可删；客户可移交给同事；管理员可见全部（为离职转接）
- 附件（PI / 报价单）存 Postgres；产品目录带自定义属性
- 日历跟进：给客户排哪天联系，当天导航挂红点

**话术库 + AI 草稿**
- 模板与标签团队共享，标签可停用 / 删除 / 合并
- 按客户生成消息草稿：喂进客户资料 + 最近 3 条跟进 + 模板，界面回显「依据：客户资料 + N 条跟进 + 模板 X」
- AI 只出草稿，不代发；每日限额 + 按人月度 token 额度双重闸

**多智能体**
- 渠道（Base URL / 模型 / API Key）配在数据库里，管理界面维护，key 加密存储、接口永不回传
- **本项目不内置任何默认 key**：谁用谁配，见下「先配一个智能体」

## 先配一个智能体（重要）

这个仓库**不带任何 API key**，也不会去读部署者的环境变量。所以：

1. 注册并登录
2. 进「智能体」页 → 添加一个渠道：填你自己的
   - Base URL（如 `https://api.openai.com/v1`、DashScope / 硅基流动 / 任意 OpenAI 兼容网关）
   - 模型名（如 `gpt-4o-mini`）
   - API Key
3. 回到背调页或客户详情，选这个渠道就能用

没配之前，背调和话术都会返回一句明确的提示，而不是报个看不懂的错。

## 本地跑起来

需要 Python 3.11+ 和一个 Postgres（Neon 免费层够用）。

```bash
# 1. 装依赖
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt  # macOS / Linux

# 2. 配环境变量
cp .env.example .env    # 然后填 DATABASE_URL / SESSION_SECRET

# 3. 起服务
.venv/Scripts/python.exe -m uvicorn main:app --port 8000
# 打开 http://127.0.0.1:8000
```

Windows 下也可以直接双击 `启动.bat`（自动建虚拟环境、装依赖、缺 `.env` 时引导你填）。

## 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `DATABASE_URL` | ✅ | Postgres 连接串；Neon 记得带 `?sslmode=require` |
| `SESSION_SECRET` | ✅ | 会话 cookie 签名密钥，随便一串长随机字符 |
| `SIGNUP_CODE` | 建议 | 注册邀请码；不设则任何人都能注册 |
| `LLM_DAILY_LIMIT` | 可选 | 每人每天生成次数上限，默认 100 |
| `SECRETS_KEY` | 可选 | 加密存 provider key 用的 Fernet key；不设则自动生成到本地文件 |

**模型 key 不在环境变量里**——它在「智能体」页由使用者自己填、加密后存数据库。这是刻意的：部署者不该替所有访客付钱。

## 测试

```bash
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

- 73 个用例，覆盖：客户隔离 / 管理员可见全部 / 移交换主 / 尾号查重 / 未登录拦截 / `/app` 302 门禁 /
  页面与静态资源缓存头 / 话术模板共享 / 标签停用不抽走历史 / 标签删除合并 / 生成不越权 /
  每日限额 429 / 跟进上下文喂给模型 / 未配渠道被拦住 / 背调历史按人隔离 / 提醒与产品属性
- 隔离方式：在同一个库里开临时 schema（`rivet_test_xxxxxxxx`）并把 `search_path` 钉进去，跑完 drop——**不碰生产数据**
- 注意：全部用例要跑 10 分钟左右（每条 SQL 都是远程库一次往返）；单跑一个文件快得多
- Neon 池化端点（host 带 `-pooler`）不接受 `search_path` 启动参数，测试里去掉它走直连

## 技术栈与结构

| 层 | 用什么 |
|---|---|
| 后端 | Python + FastAPI（单进程，`main.py` 路由 + `db.py` 手写 SQL） |
| 数据库 | Postgres（Neon），驱动 `psycopg` + 连接池 |
| 前端 | 原生 HTML / CSS / JS，无构建步骤；登录页地球用 three.js（本地 vendor，无 CDN 依赖） |
| 部署 | Render（免费层，从 GitHub 拉代码） |
| AI | 任意 OpenAI 兼容 `/chat/completions` 渠道 |

```
main.py          路由：背调 / 登录 / 客户 / 跟进 / 话术 / 提醒 / 智能体 / 页面
db.py            建表 + 全部 SQL（权限过滤写在 SQL 里，不存在"不带 viewer 的取客户函数"）
auth.py          会话、密码哈希、require_user
llm.py           模型调用层（只认 provider，不读全局 key）
secrets_store.py provider key 的加密存取
static/          前端页面与脚本；vendor/ 放 three.js，textures/ 放地球贴图
tests/           回归护栏（临时 schema 隔离）
DEVELOPMENT.md   开发笔记：踩过的坑、约定、下一步
```

## 几条刻意的取舍

- **不用 ORM**：这个规模下手写 SQL 更透明，排查问题直接看得见语句
- **客户权限写在 SQL**：每个取客户的函数都必须带 viewer，避免"新开一个函数忘了过滤"
- **时间戳一律存 UTC**：显示时才转本地
- **AI 只出草稿**：提示词禁止编造价格 / 数量 / 交期，缺的写成问题去问对方
- **不做按人名 / 手机号查个人信息**：LLM 没有查询能力必然编造，且涉及 PIPL / GDPR

## 已知边界

- 单进程部署，没有后台任务队列；实时类功能（如消息 webhook）需要常驻实例 + 队列
- 只做了桌面端布局，窄屏（< 1180px）会隐藏登录页的地球，未做移动端适配
- WhatsApp 接入（在工作台里直接收发消息）**尚未实现**，只有设计与风险评估，见 `docs/`
- 想加新栏目要改前端 `renderPanel` 与后端白名单两处

## License

MIT，见 `LICENSE`。
