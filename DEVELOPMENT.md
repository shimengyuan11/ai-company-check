# 开发说明（Development Notes）

> `README.md` 讲"这个项目是什么、怎么用"；这份讲"怎么继续开发它"。

## 环境与本地运行

- Python 3.12+（开发时用的是 3.15）
- 依赖见 `requirements.txt`：`fastapi` / `uvicorn` / `httpx` / `python-dotenv`
- 环境变量放在项目根目录的 `.env`（已在 `.gitignore` 中，不进仓库）

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
| `db.py` | SQLite 操作：建表 / 存一条 / 读列表 / 读单条；表结构见 `PRD.md` 的字段表 |
| `static/` | 前端三件套；`app.js` 用 `fetch` 调 `POST /analyze` 与 `GET /history` |

## 数据与部署的取舍

- **用 SQLite 单文件**：本地零依赖、够用；但云平台免费实例的磁盘是临时的，
  重新部署或休眠后 `data.db` 会清空（作为在线 demo 可接受）
- 生产化要补的两件事：换成 Postgres/MySQL；加索引与分页

## 当前状态

- [x] 后端四个接口 + SQLite 存取（本地实测通过）
- [x] 前端页面：输入公司名 → 出结果 → 看历史
- [x] 部署到 Render，线上可访问（地址见 `README.md`）
- [x] GitHub 仓库与 README / PRD
- [x] **线上真实调用链的端到端验证**：2026-10-15 在线上跑通真实背调（`比亚迪` / `河南拜斯特钢构有限公司`），`GET /history/{id}` 可读回完整正文——key、模型名、Render 出网均正常
- [ ] 前端接上 `/history/{id}`：点历史某条展开该条详情（**接口已实现，前端未用**）
- [x] 结果区的 Markdown 渲染：`**加粗**` 已渲染为 `<strong>`（前端先 `escapeHtml` 转义、再替换，防 XSS）
- [ ] 清掉本地验证时造出的测试数据（`测试公司` / `test` 等）

## 走过的岔路（否决过的方案与原因）

- **不做「按人名 / 手机号查询个人信息」**：LLM 没有查询能力，对具体自然人必然编造；
  且处理第三方个人信息涉及 PIPL / GDPR 合规风险。替代方向（v2）是把"查隐私"
  换成"给方法"：输入侧加对方角色/原话摘要，输出侧加沟通建议与核实清单。
  详见 `PRD.md` 的「需求变更记录」
- **暂不换 Postgres**：免费实例的临时磁盘对 demo 够用；换库要动数据层，留到有真实需求时再做
- **不用 ORM**：这个规模下原生 `sqlite3` + 手写 SQL 更透明，也便于学习和排查

## 换盘 / 换机器时会踩的坑

- 把项目放在 **exFAT / FAT32** 分区上时，git 会因"该文件系统不记录所有权"报
  `detected dubious ownership`，需要执行
  `git config --global --add safe.directory <仓库绝对路径>`（**用正斜杠**）
- exFAT **大小写不敏感**：本地 `Main.py` 与 `main.py` 会被当成同一个文件，
  但 Linux 服务器上不是——部署到云平台前留意文件名大小写
- 依赖只装在 `.venv/` 里：换机器后必须重新 `pip install`，直接拷代码不会带上依赖
- httpx / requests 默认会读取**系统代理**：若本机代理指向一个不可用的地址，
  会出现"curl 能通、程序不通"，判据是"经该代理访问目标是否成功"而非"端口是否在监听"
