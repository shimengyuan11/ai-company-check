"""LLM 调用层。

开源版的一条硬约定：**这里不内置任何默认渠道，也不读部署者自己的 key**。
每次调用都必须显式带一个「智能体」（`ai_providers` 表里的一行：base_url + model + api_key），
由当前登录用户自己配置——谁用谁的额度，账才清楚。

.env 里仍可放 DATABASE_URL / SECRET_KEY 这类部署配置，但 `LLM_*` 环境变量已不再被读取。
这样别人 clone 下来、或者拿安装包试用时，不会有人悄悄花掉部署者的钱。
"""

import httpx
from dotenv import load_dotenv

load_dotenv()  # 保留：本地把 DATABASE_URL 之类放 .env 更方便


class LLMNotConfigured(RuntimeError):
    """没有可用的智能体渠道。

    这是一种正常的「还没配置」，不是异常——调用方应该把它翻译成一句
    明确的人话，引导使用者去配，而不是静默回落到别人的 key。
    """


NOT_CONFIGURED_HINT = (
    "还没有可用的智能体：请到「智能体」页点「添加」，"
    "填上名称 / Base URL / 模型名 / API Key，然后回来重试。"
)


def _pick(provider, field):
    """取 provider 的一个必填字段；缺了就抛「去配置」。"""
    value = (provider or {}).get(field)
    if not value:
        raise LLMNotConfigured(NOT_CONFIGURED_HINT)
    return value


def _chat(provider, system_prompt, user_content, timeout=60.0):
    """发一次 chat/completions。

    trust_env=False：不吃系统代理。踩过的坑——本机代理指向一个不可用地址时，
    curl 能通、程序不通，判据是「经该代理访问目标是否成功」。
    """
    base_url = str(_pick(provider, "base_url")).rstrip("/")
    api_key = _pick(provider, "api_key")
    model = _pick(provider, "model")

    response = httpx.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
        },
        timeout=timeout,
        trust_env=False,
    )
    response.raise_for_status()
    return response.json()


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


def analyze_company(company: str, extra: str = "", provider=None) -> str:
    """调一次 LLM，返回背调分析的正文。

    provider 必传（`{"base_url","api_key","model"}`）——不再有服务器默认渠道。
    """
    user_content = f"公司名：{company}"
    if extra:
        user_content += f"\n补充信息：{extra}"

    data = _chat(provider, SYSTEM_PROMPT, user_content)
    return (data["choices"][0]["message"]["content"] or "").strip()


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
    customer: dict, template_body: str = "", request: str = "", followups=None, provider=None
) -> dict:
    """按客户 + 模板 + 要求 + 最近的跟进记录生成一条话术。

    返回 `{"text", "tokens_in", "tokens_out", "model"}`——带上用量，好按人算账。

    customer 传 db.get_customer 那一行即可（姓名 / 公司 / 国家 / 阶段 / 备注 / 来源）。
    followups 传最近的几条跟进（每条至少含 content / created_at）——「聊到哪了」
    才是写话术最要紧的上下文，缺了它生成的东西只能套模板。
    provider 必传：用哪个渠道写在请求里，不再回落到服务器配置。
    """
    model = _pick(provider, "model")

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

    data = _chat(provider, PITCH_PROMPT, user_content)
    usage = data.get("usage") or {}
    return {
        "text": (data["choices"][0]["message"]["content"] or "").strip(),
        "tokens_in": int(usage.get("prompt_tokens") or 0),
        "tokens_out": int(usage.get("completion_tokens") or 0),
        "model": model,
    }
