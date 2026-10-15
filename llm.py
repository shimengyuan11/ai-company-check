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
