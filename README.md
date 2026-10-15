# AI 公司背调系统

输入一个公司名，自动生成一份结构化的背调分析，并存下来供以后查看。

**在线试用：** https://ai-company-check.onrender.com

## 它解决什么问题

做外贸 / BD / 销售的人，收到一个陌生公司的线索时，要先判断这家公司靠不靠谱。
传统做法是手动 Google + 翻 LinkedIn + 查海关数据，一次 20~30 分钟——慢、重复、全靠经验。

这个工具把它压缩成一次点击。

## 功能

- 输入公司名（+ 可选的补充信息：国家 / 行业 / 官网）
- 后端调用 LLM 生成五段式背调：**公司概况 / 主营 / 规模 / 风险点 / 建议**
- 结果存进 Postgres，页面可查看历史记录（点历史某条展开该条详情）

## 一条重要的产品决策：宁可说"不知道"，也不编

LLM 对公司不熟悉时会**自信地编造**具体数字（员工数、营收、注册资本、客户名）。
对背调这个场景，编造的代价是业务员拿着假信息去谈判。

所以提示词里加了硬约束：

- 不确定的信息必须标注「未验证：需人工核实」
- 禁止编造具体数字与客户名
- 区分「公开可查的事实」与「推断」
- 页面固定显示「AI 生成，仅供线索参考，重要决策请人工核实」

**明确不做**：按人名 / 手机号查询个人信息。原因有两层——LLM 没有查询能力（会编），
以及处理第三方个人信息涉及合规风险（PIPL / GDPR）。详见 `PRD.md`。

## 技术栈

| 层 | 用什么 |
|---|---|
| 后端 | Python + FastAPI |
| 数据库 | Postgres（托管，如 Neon 免费层；驱动 `psycopg`） |
| LLM | 任何 OpenAI 兼容接口（默认 DeepSeek） |
| 前端 | 原生 HTML / CSS / JavaScript |
| 部署 | Render |

## 本地运行

```bash
git clone https://github.com/shimengyuan11/ai-company-check.git
cd ai-company-check
python -m venv .venv
.venv\Scripts\activate        # Windows；macOS/Linux 用 source .venv/bin/activate
pip install -r requirements.txt
```

在 `ai-company-check` 文件夹里（和 `main.py` 同一层）新建 `.env`（**不要提交到 git**）：

```
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=你的key
LLM_MODEL=deepseek-flash
DATABASE_URL=postgresql://用户名:密码@主机/库名?sslmode=require
```

`DATABASE_URL` 是 Postgres 连接串——去 https://neon.com 建一个免费库就能拿到（形如上行的串，末尾的 `sslmode=require` 别丢）。本地和线上用的是同一个库。

启动：

```bash
uvicorn main:app --reload
```

打开 http://127.0.0.1:8000

## 项目结构

```
main.py            FastAPI 应用与路由（/、/analyze、/history）
llm.py             调 LLM：提示词 + 请求
db.py              数据库操作：建表 / 存 / 读（Postgres）
static/            前端三件套（页面、样式、逻辑）
PRD.md             需求与边界（含「不做什么」及原因）
```

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 前端页面 |
| POST | `/analyze` | 生成一份背调并存库 |
| GET | `/history` | 历史记录列表 |
| GET | `/history/{id}` | 单条详情 |
| GET | `/docs` | 自动生成的交互式接口文档 |

## 已知限制

- **免费实例闲置 15 分钟休眠**，下次访问约需 1 分钟唤醒
- 历史记录存在**托管的 Postgres** 里，重新部署/休眠都不会丢；代价是多了一个外部依赖（Neon 免费层，超过免费额度会暂停）
- 分析质量取决于所选模型，且**所有结论都需要人工核实**
