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
2. 语言：目标国家是英语区（US/UK/AU/CA 等）或不确定时用英文；中国大陆客户用中文。
   其他国家默认英文。
3. 简短：不超过 120 词。开门见山，不要「希望您一切都好」这类空话堆砌。
4. 不要出现「作为 AI」「根据您提供的信息」这类措辞。
5. 结尾必须给一个明确的下一步（回邮件确认 / 约时间 / 确认数量）。
6. 只输出消息正文。不要标题、不要解释、不要用 Markdown。

这是「AI 出草稿、人负责」的环节：起草要克制，宁可少说，也不要编。"""


def generate_pitch(customer: dict, template_body: str = "", request: str = "") -> str:
    """按客户 + 模板 + 要求生成一条话术。返回正文。

    customer 传 db.get_customer 那一行即可（姓名 / 公司 / 国家 / 阶段 / 备注 / 来源）。
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

    if template_body.strip():
        user_content += "\n\n参考话术模板（可以改写，但保持意图）：\n" + template_body.strip()
    if request.strip():
        user_content += "\n\n这次想达成：\n" + request.strip()

    response = httpx.post(
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={
            "model": MODEL,
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
