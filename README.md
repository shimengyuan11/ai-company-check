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
- 结果存进 SQLite，页面可查看历史记录

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
| 数据库 | SQLite（Python 自带 `sqlite3`，零额外依赖） |
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
