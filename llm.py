import os

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.environ.get("LLM_BASE_URL", "")
API_KEY = os.environ.get("LLM_API_KEY", "")
MODEL = os.environ.get("LLM_MODEL", "")

missing = [name for name, value in [
    ("LLM_BASE_URL", BASE_URL),
    ("LLM_API_KEY", API_KEY),
    ("LLM_MODEL", MODEL),
] if not value]
if missing:
    raise RuntimeError("缺少配置：" + "、".join(missing) + "（请看项目目录下的 .env 文件）")

# 话术生成可以**单独指一个模型**（比如更便宜的 / 免费渠道），不填就沿用上面那套。
# 拆开的原因：背调是一次性重活，话术是天天用的轻活，两者的性价比考虑不一样。
PITCH_BASE_URL = os.environ.get("PITCH_LLM_BASE_URL") or BASE_URL
PITCH_API_KEY = os.environ.get("PITCH_LLM_API_KEY") or API_KEY
PITCH_MODEL = os.environ.get("PITCH_LLM_MODEL") or MODEL

SYSTEM_PROMPT = """你是给外贸 / BD / 销售用的公司背景调查助手。
用户会给你一个公司名，可能附带补充信息。请输出一份简明背调报告。

用下面五个小标题，顺序固定，每个标题下 1-3 句话：
公司概况 / 主营 / 规模 / 风险点 / 建议

硬性要求：
1. 如果对这家公司不了解，就写「未验证：需人工核实」，不要猜。
2. 禁止编造具体数字：员工人数、营收、注册资本、成立年份、客户名单都不能编。
   不确定就写「未验证」，并说明建议去哪里核实。
3. 把「公开可查的事实」和「你的推断」分开写，推断要标明「推断」。
4. 全文 300 字以内，中文。
"""


def analyze_company(company: str, extra: str = "") -> str:
    """调一次 LLM，返回背调分析的正文。"""
    user_content = f"公司名：{company}"
    if extra:
        user_content += f"\n补充信息：{extra}"

    response = httpx.post(
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
        },
        timeout=60.0,
        trust_env=False,
    )
    response.raise_for_status()

    data = response.json()
    return data["choices"][0]["message"]["content"]

# ===================== 第 3 期：按客户生成话术 =====================

PITCH_PROMPT = """你是外贸业务的写作助手。用户会给你一段客户信息、一条话术模板（可能已填入客户信息），
以及他想达成的要求。请写一条可以直接发给这个客户的消息。

硬性要求：
1. **不能编造**客户信息里没有的东西：价格、数量、交期、型号一律不要自己造；
   需要这些信息时，写成问题去问对方。
2. 语言：**一律用中文写**——语言种类太多，统一中文最省事。
   如果用户在「这次想达成」里明确指定了别的语言（例如「用英文回复」），就按他说的来。
3. 简短：不超过 120 词。开门见山，不要「希望您一切都好」这类空话堆砌。
4. 不要出现「作为 AI」「根据您提供的信息」这类措辞。
5. 结尾必须给一个明确的下一步（回邮件确认 / 约时间 / 确认数量）。
6. 只输出消息正文。不要标题、不要解释、不要用 Markdown。

这是「AI 出草稿、人负责」的环节：起草要克制，宁可少说，也不要编。"""


def generate_pitch(
    customer: dict, template_body: str = "", request: str = "", followups=None
) -> str:
    """按客户 + 模板 + 要求 + 最近的跟进记录生成一条话术。返回正文。

    customer 传 db.get_customer 那一行即可（姓名 / 公司 / 国家 / 阶段 / 备注 / 来源）。
    followups 传最近的几条跟进（每条至少含 content / created_at）——「聊到哪了」
    才是写话术最要紧的上下文，缺了它生成的东西只能套模板。
    """
    bits = []
    for label, key in [
        ("姓名", "contact_name"),
        ("公司", "company"),
        ("国家", "country"),
        ("来源", "source"),
        ("阶段", "stage"),
        ("备注", "note"),
    ]:
        value = (customer or {}).get(key)
        if value:
            bits.append(f"{label}：{value}")
    user_content = "客户信息：\n" + ("\n".join(bits) if bits else "（只有姓名，其余没填）")

    if followups:
        lines = []
        for f in list(followups)[:5]:
            when = (f.get("created_at") or "")[:10]
            content = (f.get("content") or "").strip().replace("\n", " ")
            if content:
                lines.append(f"- {when}：{content}")
        if lines:
            user_content += "\n\n最近的跟进记录（从新到旧，写话术要接得上这些）：\n" + "\n".join(lines)

    if template_body.strip():
        user_content += "\n\n参考话术模板（可以改写，但保持意图）：\n" + template_body.strip()
    if request.strip():
        user_content += "\n\n这次想达成：\n" + request.strip()

    response = httpx.post(
        f"{PITCH_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {PITCH_API_KEY}"},
        json={
            "model": PITCH_MODEL,
            "messages": [
                {"role": "system", "content": PITCH_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
        },
        timeout=60.0,
        trust_env=False,
    )
    response.raise_for_status()

    data = response.json()
    return data["choices"][0]["message"]["content"].strip()
