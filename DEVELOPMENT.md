# 开发说明（Development Notes）

> `README.md` 讲"这个项目是什么、怎么用"；这份讲"怎么继续开发它"。

## 停在哪儿（2026-10-15 收工 · 下次从这里续）

**一句话**：数据层从 SQLite 换 Postgres 的代码已写完、本地 diff 已核验，但**还没连真实库验证、还没推送**。

- **⚠️ 推之前必须先做这一件事**：去 https://neon.com 建个免费库，把连接串配到**两处**——
  本地 `.env` 的 `DATABASE_URL`，和 **Render 后台的环境变量**。
  **否则一推送，线上会在启动时直接崩**（`db.py` 一导入就读 `DATABASE_URL`，读不到就 `RuntimeError`）。
- **本地现状**：`.env` 里还没有 `DATABASE_URL`，所以现在本地 `uvicorn main:app` 会报
  「缺少配置：DATABASE_URL」——**这是预期行为，不是 bug**。线上跑的还是旧代码，正常。
- **续上的三步**：① 配好上面两处连接串 → ② 本地验证（`init_db` / 写一条 / 读列表 / 读单条，再起服务用浏览器点一遍）
  → ③ 提交推送；等 Render 部署完跑一条背调，再触发一次 **Manual Deploy**，确认那条记录还在
  （这才算"数据不再丢"的证据）。
- **为什么这么改**：实测发现每次重新部署都会清空历史，而 PRD 的验收标准写的就是"刷新后历史还在"。
- **否决过的岔路**：① 不用 ORM（沿用既有决策，原生驱动 `psycopg` + 手写 SQL 更透明）；
  ② 不做"SQLite/Postgres 双方言"——那会让本地和线上跑在不同实现上、行为分叉；
  ③ 不迁移旧的本地测试数据（新库从空开始，旧数据备份在 `backup-data.db`）。
- **用户提的下一步方向**：优化结果/背景页面，并补一些其他功能。

## 环境与本地运行

- Python 3.12+（开发时用的是 3.15）
- 依赖见 `requirements.txt`：`fastapi` / `uvicorn` / `httpx` / `python-dotenv` / `psycopg[binary]`
- 环境变量放在项目根目录的 `.env`（已在 `.gitignore` 中，不进仓库）：
  `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`，外加 `DATABASE_URL`（Postgres 连接串）

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows；macOS/Linux 用 source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

## 代码地图

| 文件 | 职责 |
|---|---|
| `main.py` | FastAPI 应用与路由：`/`（页面）、`POST /analyze`、`GET /history`、`GET /history/{id}`；启动时调用 `init_db()` |
| `llm.py` | 提示词 + 调 LLM（httpx；显式 `trust_env=False`，避免请求被系统代理劫持） |
| `db.py` | Postgres 操作（驱动 `psycopg`）：建表 / 存一条 / 读列表 / 读单条；连接串取 `DATABASE_URL`；表结构见 `PRD.md` 的字段表 |
| `static/` | 前端三件套；`app.js` 用 `fetch` 调 `POST /analyze`、`GET /history` 与 `GET /history/{id}`（点历史展开详情） |

## 数据与部署的取舍

- **用托管 Postgres（Neon 免费层）**：数据不再随免费实例的临时磁盘清空，重新部署/休眠都还在。
  代价是多一个外部依赖 + 连接串配置（`DATABASE_URL`）；本地和线上共用同一个库
- **不用 ORM**：这个规模下原生驱动 `psycopg` + 手写 SQL 更透明，也便于学习和排查
- 生产化还可补：加索引与分页（现在只靠主键索引，列表默认取最近 10 条）
- **SQLite → Postgres 的三处差异**：占位符 `?`→`%s`、自增 `AUTOINCREMENT`→`SERIAL`、
  取新 id `lastrowid`→`RETURNING id`（都写在 `db.py` 文件头注释里）

## 当前状态

- [x] 后端四个接口 + 数据库存取（本地实测通过）
- [x] 前端页面：输入公司名 → 出结果 → 看历史
- [x] 部署到 Render，线上可访问（地址见 `README.md`）
- [x] GitHub 仓库与 README / PRD
- [x] **线上真实调用链的端到端验证**：2026-10-15 在线上跑通真实背调（`比亚迪` / `河南拜斯特钢构有限公司`），`GET /history/{id}` 可读回完整正文——key、模型名、Render 出网均正常
- [x] 前端接上 `/history/{id}`：点历史某条在结果区展开该条完整详情（标题随之切到「历史记录 #N」）
- [x] 结果区的 Markdown 渲染：`**加粗**` 已渲染为 `<strong>`（前端先 `escapeHtml` 转义、再替换，防 XSS）
- [x] 清掉本地验证时造出的测试数据（`测试公司` / `test`；删前备份在 `backup-data.db`）
- [ ] 数据层从 SQLite 换成托管 Postgres（历史不再因重新部署清空）——**代码已改，待真实库验证**

## 走过的岔路（否决过的方案与原因）

- **不做「按人名 / 手机号查询个人信息」**：LLM 没有查询能力，对具体自然人必然编造；
  且处理第三方个人信息涉及 PIPL / GDPR 合规风险。替代方向（v2）是把"查隐私"
  换成"给方法"：输入侧加对方角色/原话摘要，输出侧加沟通建议与核实清单。
  详见 `PRD.md` 的「需求变更记录」
- **（已推翻）暂不换 Postgres**：当初判断"免费实例的临时磁盘对 demo 够用"，
  后来实测发现每次重新部署都会清空历史——对一个"存下来供以后查看"的工具，这是硬伤；
  于是 2026-10-15 换成托管 Postgres（Neon 免费层）。详见 `PRD.md` 的「需求变更记录」
- **不用 ORM**：这个规模下原生驱动 + 手写 SQL 更透明，也便于学习和排查

## 换盘 / 换机器时会踩的坑

- 把项目放在 **exFAT / FAT32** 分区上时，git 会因"该文件系统不记录所有权"报
  `detected dubious ownership`，需要执行
  `git config --global --add safe.directory <仓库绝对路径>`（**用正斜杠**）
- exFAT **大小写不敏感**：本地 `Main.py` 与 `main.py` 会被当成同一个文件，
  但 Linux 服务器上不是——部署到云平台前留意文件名大小写
- 依赖只装在 `.venv/` 里：换机器后必须重新 `pip install`，直接拷代码不会带上依赖
- httpx / requests 默认会读取**系统代理**：若本机代理指向一个不可用的地址，
  会出现"curl 能通、程序不通"，判据是"经该代理访问目标是否成功"而非"端口是否在监听"
