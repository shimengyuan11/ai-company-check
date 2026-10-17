import datetime as dt
import os
import re
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

import auth
import db
from db import get_analysis, init_db, list_history, save_analysis
from llm import MODEL, PITCH_MODEL, analyze_company, generate_pitch

BASE_DIR = Path(__file__).parent  # 项目根目录；拼路径都用它

app = FastAPI(title="外贸客户工作台", version="0.2")

# 会话密钥：给登录 cookie 签名用（防伪造）。
# 没配也不至于崩——随机生成一个，但那样每次重启/重新部署，所有人都要重新登录。
SESSION_SECRET = os.environ.get("SESSION_SECRET") or os.urandom(32).hex()
if not os.environ.get("SESSION_SECRET"):
    print("[警告] 未配置 SESSION_SECRET，已临时随机生成：重启后所有人都要重新登录。")

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",  # 跨站请求不带 cookie，够挡掉大部分 CSRF
    max_age=60 * 60 * 24 * 14,
)

init_db()  # 启动时建表


# ==================================================================
# V1：AI 公司背调（接口保持原样，页面见 static/index.html）
# ==================================================================


class AnalyzeRequest(BaseModel):
    """前端提交上来的数据。缺 company 会直接返回 422。"""

    company: str
    extra: str = ""


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    try:
        text = analyze_company(req.company, req.extra)
    except httpx.HTTPStatusError as e:
        return {
            "error": f"调用 LLM 失败：HTTP {e.response.status_code}",
            "detail": e.response.text[:300],
        }
    record_id = save_analysis(req.company, req.extra, text, MODEL)
    return {"id": record_id, "company": req.company, "analysis": text}


@app.get("/history")
def history(limit: int = 10):
    return {"items": list_history(limit)}


@app.get("/history/{record_id}")
def history_detail(record_id: int):
    row = get_analysis(record_id)
    if row is None:
        return {"error": f"没有 id={record_id} 的记录"}
    return row


# ==================================================================
# V2：登录
# ==================================================================


class RegisterRequest(BaseModel):
    username: str
    password: str
    code: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/register")
def api_register(req: RegisterRequest, request: Request):
    allowed, as_admin = auth.registration_allowed(req.code)
    if not allowed:
        raise HTTPException(status_code=403, detail="注册已关闭（需要邀请码）")
    username = req.username.strip()
    if len(username) < 2:
        raise HTTPException(status_code=400, detail="用户名至少 2 个字符")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    if db.get_user_by_username(username) is not None:
        raise HTTPException(status_code=400, detail="用户名已被占用")
    uid = db.create_user(username, auth.hash_password(req.password), username, as_admin)
    request.session["user_id"] = uid
    return {"user": auth.public_user(db.get_user(uid))}


@app.post("/api/login")
def api_login(req: LoginRequest, request: Request):
    user = db.get_user_by_username(req.username.strip())
    # 三句判断合成一句回应：不告诉对方「是用户名错还是密码错」，免得被拿来枚举账号
    bad = (
        user is None
        or not user["is_active"]
        or not auth.verify_password(req.password, user["password_hash"])
    )
    if bad:
        raise HTTPException(status_code=401, detail="用户名或密码不对")
    request.session["user_id"] = user["id"]
    return {"user": auth.public_user(user)}


@app.post("/api/logout")
def api_logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def api_me(request: Request):
    user = auth.current_user(request)
    return {"user": auth.public_user(user) if user else None}


@app.get("/api/signup-status")
def api_signup_status():
    """注册现在是开是关？

    公开信息，不泄露邀请码本身。存在的理由：**光看 403 分不清"没配邀请码"还是"填错了"**，
    前端拿到这个就能把话说清楚；运维/排查时也能一眼看出线上到底配没配。
    """
    return {"open": bool(auth.SIGNUP_CODE)}


# ==================================================================
# V2：客户 / 跟进 / 移交
# ==================================================================


class CustomerRequest(BaseModel):
    """建档。姓名 / 号码 / 国家 必填，公司名选填（号码是以后查重/防撞单的抓手）。"""

    contact_name: str
    phone: str
    country: str
    company: str = ""
    source: str = ""
    note: str = ""


class CustomerPatch(BaseModel):
    contact_name: str | None = None
    phone: str | None = None
    country: str | None = None
    company: str | None = None
    source: str | None = None
    note: str | None = None
    stage: str | None = None
    high_intent: bool | None = None


class TransferRequest(BaseModel):
    to_user_id: int


class FollowupRequest(BaseModel):
    content: str


# 客户阶段：固定五档，不做成可增长的词典（那会长草，见 PRD 对「标签 vs 分类树」的判断）。
# 空值表示还没标阶段，界面上归到「未分组」。
STAGES = ("新线索", "已联系", "跟进中", "已报价", "成交", "流失")


def _visible_customer(customer_id: int, user):
    """**凡是碰客户的接口，都必须先过这一关**——看不到就当它不存在（404，不泄露「存在」）。

    权限判断本身在 db.get_customer 的 SQL 里，这里只是把「没权限」翻译成 404。
    """
    c = db.get_customer(customer_id, user["id"], user["is_admin"])
    if c is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    return c


@app.get("/api/users")
def api_users(request: Request):
    """给「移交客户」的下拉用。只返回 id / 名字，不含任何密码字段。"""
    auth.require_user(request)
    return {"items": db.list_users()}


@app.get("/api/customers")
def api_list_customers(
    request: Request, q: str = "", stage: str = "", sort: str = "recent", idle_days: int = 0
):
    """客户列表。

    - `q`：搜索词（姓名 / 公司 / 号码）
    - `stage`：阶段筛选；空 = 全部，`none` = 还没标阶段的
    - `sort`：`recent` 最近建档在前（默认）/ `stale` 最久没联系在前
    - `idle_days`：只看「N 天及以上没联系」的（3 / 5 / 7 / 10 这几档由界面给）
    """
    user = auth.require_user(request)
    if stage and stage != "none" and stage not in STAGES:
        raise HTTPException(status_code=400, detail=f"不认识的客户阶段：{stage}")
    if sort not in ("recent", "stale"):
        raise HTTPException(status_code=400, detail=f"不认识的排序方式：{sort}")
    if idle_days < 0:
        raise HTTPException(status_code=400, detail="未联系天数不能是负数")
    return {
        "items": db.list_customers(user["id"], user["is_admin"], q, stage, sort, idle_days),
        "stages": list(STAGES),
    }


@app.post("/api/customers")
def api_create_customer(req: CustomerRequest, request: Request):
    user = auth.require_user(request)
    values = {
        "contact_name": req.contact_name.strip(),
        "phone": req.phone.strip(),
        "country": req.country.strip(),
    }
    # 必填在接口层强制——库里不加 NOT NULL，是为了让老数据能平滑升级（见 db.init_db 的注释）
    if not all(values.values()):
        raise HTTPException(status_code=400, detail="姓名、号码、国家 三个都要填")
    # 查重：号码被**任何人**建过就拒绝（防撞单）。
    # 只说「已经被建过」，不说「是谁建的」——这样既防重复，又不破坏「各管各的」。
    if db.phone_taken(db.norm_phone(values["phone"])):
        raise HTTPException(
            status_code=409,
            detail="这个号码已经被建档过了（可能是同事建的）。让对方把客户转给你，或者换个号码。",
        )
    cid = db.create_customer(
        user["id"],
        values["contact_name"],
        values["phone"],
        values["country"],
        req.company.strip(),
        req.source.strip(),
        req.note.strip(),
    )
    return db.get_customer(cid, user["id"], user["is_admin"])


@app.delete("/api/customers/{customer_id}")
def api_delete_customer(customer_id: int, request: Request):
    """删客户（连同它的跟进记录）。"""
    user = auth.require_user(request)
    _visible_customer(customer_id, user)  # 看不到就不给删；404 也不泄露"存在"
    if not db.delete_customer(customer_id, user["id"], user["is_admin"]):
        raise HTTPException(status_code=404, detail="客户不存在")
    return {"ok": True}


@app.get("/api/customers/{customer_id}")
def api_get_customer(customer_id: int, request: Request):
    user = auth.require_user(request)
    return _visible_customer(customer_id, user)


@app.patch("/api/customers/{customer_id}")
def api_patch_customer(customer_id: int, req: CustomerPatch, request: Request):
    user = auth.require_user(request)
    current = _visible_customer(customer_id, user)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    # 改号码时也要查重（否则这个规则从 PATCH 就能绕过去）；
    # 「还是自己原来那个号」不算冲突。
    if "phone" in fields:
        new_norm = db.norm_phone(fields["phone"])
        if new_norm and new_norm != db.norm_phone(current["phone"]) and db.phone_taken(new_norm):
            raise HTTPException(status_code=409, detail="这个号码已经被建档过了。")
    db.update_customer(customer_id, user["id"], user["is_admin"], **fields)
    return db.get_customer(customer_id, user["id"], user["is_admin"])


@app.post("/api/customers/{customer_id}/transfer")
def api_transfer(customer_id: int, req: TransferRequest, request: Request):
    """把客户转给另一个业务——「有人离职」时用的就是它。"""
    user = auth.require_user(request)
    _visible_customer(customer_id, user)
    target = db.get_user(req.to_user_id)
    if target is None or not target["is_active"]:
        raise HTTPException(status_code=400, detail="对方账号不存在或已停用")
    if not db.transfer_customer(customer_id, req.to_user_id, user["id"], user["is_admin"]):
        raise HTTPException(status_code=403, detail="你没有权限转这个客户")
    return {"ok": True}


@app.get("/api/customers/{customer_id}/followups")
def api_list_followups(customer_id: int, request: Request):
    user = auth.require_user(request)
    _visible_customer(customer_id, user)
    return {"items": db.list_followups(customer_id)}


@app.post("/api/customers/{customer_id}/followups")
def api_add_followup(customer_id: int, req: FollowupRequest, request: Request):
    user = auth.require_user(request)
    _visible_customer(customer_id, user)  # 少了这一行，任何人都能往别人的客户里写跟进
    content = req.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="跟进内容不能为空")
    return {"id": db.add_followup(customer_id, user["id"], content)}


@app.patch("/api/customers/{customer_id}/followups/{followup_id}")
def api_patch_followup(
    customer_id: int, followup_id: int, req: FollowupRequest, request: Request
):
    """改一条跟进。写完发现漏了一句，就地补——不用删了重写。"""
    user = auth.require_user(request)
    _visible_customer(customer_id, user)
    content = req.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="跟进内容不能为空")
    if not db.update_followup(followup_id, customer_id, content):
        raise HTTPException(status_code=404, detail="这条跟进不存在")
    return {"ok": True}


@app.delete("/api/customers/{customer_id}/followups/{followup_id}")
def api_delete_followup(customer_id: int, followup_id: int, request: Request):
    user = auth.require_user(request)
    _visible_customer(customer_id, user)
    if not db.delete_followup(followup_id, customer_id):
        raise HTTPException(status_code=404, detail="这条跟进不存在")
    return {"ok": True}


@app.get("/api/options")
def api_options(request: Request):
    """选项库（国家 / 来源…）——**团队共享**，用来做下拉建议。

    共享的理由：它不是客户隐私，是团队共同积累的"词典"。各存各的，
    每个人都要重新攒一遍，"少打字"就没戏了。
    """
    auth.require_user(request)
    return {
        "country": db.list_options("country"),
        "source": db.list_options("source"),
    }


# ---------- 附件（PI / 报价单） ----------

MAX_UPLOAD = 10 * 1024 * 1024  # 10 MB：够放 PI / 报价单，又不至于把免费层塞满


@app.post("/api/customers/{customer_id}/attachments")
async def api_upload_attachment(
    customer_id: int, request: Request, file: UploadFile = File(...)
):
    user = auth.require_user(request)
    _visible_customer(customer_id, user)  # 往别人的客户里传文件？404
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件是空的")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="文件超过 10 MB，先压一下再传")
    att_id = db.add_attachment(
        customer_id, user["id"], file.filename or "未命名", file.content_type or "", data
    )
    return {"id": att_id, "size": len(data)}


@app.get("/api/customers/{customer_id}/attachments")
def api_list_attachments(customer_id: int, request: Request):
    user = auth.require_user(request)
    _visible_customer(customer_id, user)
    return {"items": db.list_attachments(customer_id)}


@app.get("/api/attachments/{att_id}")
def api_download_attachment(att_id: int, request: Request):
    user = auth.require_user(request)
    att = db.get_attachment(att_id)
    if att is None:
        raise HTTPException(status_code=404, detail="附件不存在")
    # 附件跟着客户走：看不到那个客户，就别想下它的文件
    _visible_customer(att["customer_id"], user)
    # 文件名可能带中文，直接塞进 header 会坏；用 RFC 5987 的写法
    disposition = "attachment; filename*=UTF-8''" + quote(att["filename"])
    return Response(
        content=bytes(att["data"]),
        media_type=att["content_type"] or "application/octet-stream",
        headers={"Content-Disposition": disposition},
    )


@app.delete("/api/customers/{customer_id}/attachments/{att_id}")
def api_delete_attachment(customer_id: int, att_id: int, request: Request):
    user = auth.require_user(request)
    _visible_customer(customer_id, user)
    if not db.delete_attachment(att_id, customer_id):
        raise HTTPException(status_code=404, detail="附件不存在")
    return {"ok": True}


# ---------- 产品目录（团队共享） ----------


class ProductRequest(BaseModel):
    name: str
    spec: str = ""
    price: str = ""
    note: str = ""
    # 自定义属性：材质 / MOQ / 交期… 固定列追不上业务的口头描述，所以留一个键值袋
    attrs: dict[str, str] = Field(default_factory=dict)


class ProductPatch(BaseModel):
    name: str | None = None
    spec: str | None = None
    price: str | None = None
    note: str | None = None
    attrs: dict[str, str] | None = None


@app.get("/api/products")
def api_list_products(request: Request):
    auth.require_user(request)
    return {"items": db.list_products()}


@app.post("/api/products")
def api_create_product(req: ProductRequest, request: Request):
    auth.require_user(request)
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="产品名不能为空")
    pid = db.create_product(
        name, req.spec.strip(), req.price.strip(), req.note.strip(), req.attrs
    )
    return db.get_product(pid)


@app.patch("/api/products/{product_id}")
def api_patch_product(product_id: int, req: ProductPatch, request: Request):
    auth.require_user(request)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if "name" in fields and not fields["name"].strip():
        raise HTTPException(status_code=400, detail="产品名不能为空")
    row = db.update_product(product_id, **fields)
    if row is None:
        raise HTTPException(status_code=404, detail="产品不存在")
    return row


@app.delete("/api/products/{product_id}")
def api_delete_product(product_id: int, request: Request):
    auth.require_user(request)
    if not db.delete_product(product_id):
        raise HTTPException(status_code=404, detail="产品不存在")
    return {"ok": True}


# ==================================================================
# 第 3 期：话术库（模板 + 标签，团队共享）
# ==================================================================

# 标签分三组——不是一棵分类树。三个维度会交叉（FB 来的也可能是代理商），
# 做成树必然卡在「这条该挂哪个枝」，标签可以一次挂多个。
TAG_GROUPS = ("来源", "类型", "场景")


class TemplateRequest(BaseModel):
    title: str
    body: str
    tag_ids: list[int] = Field(default_factory=list)


class TemplatePatch(BaseModel):
    title: str | None = None
    body: str | None = None
    tag_ids: list[int] | None = None


class TagRequest(BaseModel):
    grp: str
    name: str


class TagPatch(BaseModel):
    is_active: bool


@app.get("/api/tags")
def api_list_tags(request: Request, include_inactive: bool = False):
    """标签清单。默认只给在用的；`include_inactive=1` 连停用的一起给（管理时用）。"""
    auth.require_user(request)
    return {"groups": list(TAG_GROUPS), "items": db.list_tags(include_inactive)}


@app.post("/api/tags")
def api_create_tag(req: TagRequest, request: Request):
    auth.require_user(request)
    grp = req.grp.strip()
    name = req.name.strip()
    if grp not in TAG_GROUPS:
        raise HTTPException(status_code=400, detail=f"标签组只能是：{' / '.join(TAG_GROUPS)}")
    if not name:
        raise HTTPException(status_code=400, detail="标签名不能为空")
    return {"id": db.create_tag(grp, name)}


@app.patch("/api/tags/{tag_id}")
def api_set_tag_active(tag_id: int, req: TagPatch, request: Request):
    """停用 / 重新启用标签。停用后它从下拉里消失，但老模板上仍然看得到。"""
    auth.require_user(request)
    if not db.set_tag_active(tag_id, req.is_active):
        raise HTTPException(status_code=404, detail="标签不存在")
    return {"ok": True}


class TagMerge(BaseModel):
    into_id: int


@app.delete("/api/tags/{tag_id}")
def api_delete_tag(tag_id: int, request: Request):
    """真删标签。挂在模板上的关联会一起清掉——前端删之前要拿 use_count 提醒用户。"""
    auth.require_user(request)
    if not db.delete_tag(tag_id):
        raise HTTPException(status_code=404, detail="标签不存在")
    return {"ok": True}


@app.post("/api/tags/{tag_id}/merge")
def api_merge_tag(tag_id: int, req: TagMerge, request: Request):
    """把「这条」合并进「那条」：模板上的旧标签换成新的，旧标签消失。

    这是治「FB 和 facebook 并存」的正解——比手工删再逐个补标签省事且不漏。
    """
    auth.require_user(request)
    if tag_id == req.into_id:
        raise HTTPException(status_code=400, detail="不能合并到自己")
    if db.get_tag(req.into_id) is None:
        raise HTTPException(status_code=404, detail="要合并到的标签不存在")
    if not db.merge_tag(tag_id, req.into_id):
        raise HTTPException(status_code=404, detail="标签不存在")
    return {"ok": True}


@app.get("/api/templates")
def api_list_templates(request: Request, q: str = "", tag_id: int | None = None):
    """模板列表。用得多的排前面。"""
    auth.require_user(request)
    return {"items": db.list_templates(q, tag_id)}


@app.post("/api/templates")
def api_create_template(req: TemplateRequest, request: Request):
    user = auth.require_user(request)
    title = req.title.strip()
    body = req.body.strip()
    if not title or not body:
        raise HTTPException(status_code=400, detail="标题和正文都要填")
    tid = db.create_template(user["id"], title, body, req.tag_ids)
    return db.get_template(tid)


@app.get("/api/templates/{template_id}")
def api_get_template(template_id: int, request: Request):
    auth.require_user(request)
    t = db.get_template(template_id)
    if t is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return t


@app.patch("/api/templates/{template_id}")
def api_patch_template(template_id: int, req: TemplatePatch, request: Request):
    auth.require_user(request)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if "title" in fields and not fields["title"].strip():
        raise HTTPException(status_code=400, detail="标题不能为空")
    if "body" in fields and not fields["body"].strip():
        raise HTTPException(status_code=400, detail="正文不能为空")
    if not db.update_template(template_id, **fields):
        raise HTTPException(status_code=404, detail="模板不存在")
    return db.get_template(template_id)


@app.delete("/api/templates/{template_id}")
def api_delete_template(template_id: int, request: Request):
    auth.require_user(request)
    if not db.delete_template(template_id):
        raise HTTPException(status_code=404, detail="模板不存在")
    return {"ok": True}


# ---- 按客户生成话术（AI 出草稿，人过一遍再发） ----

# 每人每天的生成上限。这是真金白银的调用，得有个闸——
# 没有闸就只能靠自觉，站一旦放给同事，账单是不会有上限的。
LLM_DAILY_LIMIT = int(os.environ.get("LLM_DAILY_LIMIT", "100"))


class GenerateRequest(BaseModel):
    template_id: int | None = None
    request: str = ""  # 「想达成什么」，例如：让他回邮件确认数量
    provider_id: int | None = None  # 用哪个智能体；不选就用服务器默认那套


@app.post("/api/customers/{customer_id}/generate")
def api_generate(customer_id: int, req: GenerateRequest, request: Request):
    """按这个客户生成一条话术，并把它存下来。

    存下来的理由：不存等于「用过就没了」，下次又得重写一遍，回到扒聊天记录。
    """
    user = auth.require_user(request)
    customer = _visible_customer(customer_id, user)  # 看不到这个客户就别想生成
    # 每日限额：先算今天已经用掉几次，到顶就不调模型了（省钱也省得白等）
    day_start = dt.datetime.now(dt.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    used = db.count_generations_since(user["id"], day_start.isoformat(timespec="seconds"))
    if used >= LLM_DAILY_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"今天已经生成 {used} 次，到上限了（每人每天 {LLM_DAILY_LIMIT} 次）。",
        )
    template = None
    if req.template_id:
        template = db.get_template(req.template_id)
        if template is None:
            raise HTTPException(status_code=404, detail="模板不存在")
    # 月度 token 额度（0 = 不限）：内部成本控制的闸，不是对外收款
    limit = db.get_user_limit(user["id"])
    if limit:
        month_start = dt.datetime.now(dt.timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        used_tokens = db.tokens_used_since(
            user["id"], month_start.isoformat(timespec="seconds")
        )
        if used_tokens >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"本月额度用完了（已用 {used_tokens} / {limit} tokens），找管理员加额度。",
            )

    # 选哪个智能体——不选就走服务器默认那套
    provider = None
    if req.provider_id:
        provider = db.get_provider(req.provider_id, with_key=True)
        if provider is None or not provider["is_active"]:
            raise HTTPException(status_code=404, detail="这个智能体不存在或已停用")
        if not provider["api_key"]:
            raise HTTPException(status_code=400, detail="这个智能体还没配 key")

    # 最近的跟进记录也喂进去——「聊到哪一步了」是写话术最关键的信息，
    # 只给客户资料的话，生成出来的东西跟谁都能套，业务还得自己改一遍。
    recent = db.list_followups(customer_id)[:3]
    try:
        result = generate_pitch(
            customer, template["body"] if template else "", req.request, recent, provider
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"调用模型失败：HTTP {e.response.status_code}（{e.response.text[:120]}）",
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"连不上模型服务：{type(e).__name__}")
    if not result.get("text"):
        raise HTTPException(status_code=502, detail="模型没有返回内容，再试一次")

    gid = db.add_generation(
        customer_id,
        req.template_id,
        user["id"],
        req.request.strip(),
        result["text"],
        result["model"],
        result.get("tokens_in", 0),
        result.get("tokens_out", 0),
        req.provider_id,
    )
    if template:
        db.bump_template_use(template["id"])
    return {
        "id": gid,
        "output": result["text"],
        "model": result["model"],
        "template_title": template["title"] if template else None,
        "tokens": {
            "in": result.get("tokens_in", 0),
            "out": result.get("tokens_out", 0),
        },
        # 把「依据什么生成的」回给前端——业务能核对，AI 也就不再是黑箱
        "basis": {
            "customer": customer.get("contact_name") or "",
            "followups": len(recent),
            # 少于 2 条跟进时，草稿的依据其实很薄——前端要显式提醒业务
            "reliable": len(recent) >= 2,
            "template": template["title"] if template else None,
        },
    }


@app.get("/api/customers/{customer_id}/generations")
def api_list_generations(customer_id: int, request: Request):
    """这个客户生成过什么。权限和客户本身一致——看不到客户就看不到记录。"""
    user = auth.require_user(request)
    _visible_customer(customer_id, user)
    return {"items": db.list_generations(customer_id)}


# ==================================================================
# 提醒（日历看板，个人日程）
# ==================================================================


class ReminderRequest(BaseModel):
    customer_id: int
    due_date: str  # YYYY-MM-DD
    note: str = ""


class ReminderPatch(BaseModel):
    done: bool | None = None
    note: str | None = None
    due_date: str | None = None


def _parse_date(text: str) -> dt.date:
    try:
        return dt.date.fromisoformat((text or "").strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式要 YYYY-MM-DD")


@app.get("/api/reminders/pending-count")
def api_pending_reminders(request: Request):
    """左导航红点上的数字：今天及以前、还没做完的提醒条数。"""
    user = auth.require_user(request)
    return {"count": db.count_pending_reminders(user["id"], dt.date.today())}


@app.get("/api/reminders")
def api_list_reminders(request: Request, date_from: str = "", date_to: str = ""):
    """我的提醒（同事的安排看不到）。不传区间就默认给 date_from 所在的整月。"""
    user = auth.require_user(request)
    start = _parse_date(date_from) if date_from else dt.date.today().replace(day=1)
    if date_to:
        end = _parse_date(date_to)
    else:
        next_month = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        end = next_month - dt.timedelta(days=1)
    return {"items": db.list_reminders(user["id"], start, end)}


@app.post("/api/reminders")
def api_create_reminder(req: ReminderRequest, request: Request):
    user = auth.require_user(request)
    _visible_customer(req.customer_id, user)  # 别人的客户，连提醒都不该建得下来
    due = _parse_date(req.due_date)
    return {"id": db.create_reminder(req.customer_id, user["id"], due, req.note.strip())}


@app.patch("/api/reminders/{reminder_id}")
def api_patch_reminder(reminder_id: int, req: ReminderPatch, request: Request):
    """改提醒：标做完 / 改备注 / 挪日期——传什么改什么。"""
    user = auth.require_user(request)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if "due_date" in fields:
        fields["due_date"] = _parse_date(fields["due_date"])
    if "note" in fields:
        fields["note"] = fields["note"].strip()
    if not fields:
        raise HTTPException(status_code=400, detail="没有要改的内容")
    if not db.update_reminder(reminder_id, user["id"], **fields):
        raise HTTPException(status_code=404, detail="提醒不存在")
    return {"ok": True}


@app.delete("/api/reminders/{reminder_id}")
def api_delete_reminder(reminder_id: int, request: Request):
    user = auth.require_user(request)
    if not db.delete_reminder(reminder_id, user["id"]):
        raise HTTPException(status_code=404, detail="提醒不存在")
    return {"ok": True}


# ==================================================================
# 智能体（多 provider）与额度
# ==================================================================
# 管理员登记可用渠道（key 加密存、接口永不回显），给每人分配月度 token 额度。
# 这是内部成本控制，不涉及对外收款。


class ProviderRequest(BaseModel):
    name: str
    base_url: str
    model: str
    api_key: str = ""
    is_active: bool = True


class ProviderPatch(BaseModel):
    name: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    is_active: bool | None = None


class LimitRequest(BaseModel):
    monthly_tokens: int


def _require_admin(request: Request):
    user = auth.require_user(request)
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="只有管理员能改智能体配置")
    return user


def _month_start_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    ).isoformat(timespec="seconds")


@app.get("/api/providers")
def api_list_providers(request: Request):
    """可用智能体。**永远不带 api_key**——普通同事也能看到有哪些可选。"""
    user = auth.require_user(request)
    return {"items": db.list_providers(include_inactive=bool(user["is_admin"]))}


@app.post("/api/providers")
def api_create_provider(req: ProviderRequest, request: Request):
    _require_admin(request)
    name = req.name.strip()
    base_url = req.base_url.strip().rstrip("/")
    model = req.model.strip()
    if not name or not base_url or not model:
        raise HTTPException(status_code=400, detail="名称、接口地址、模型名都要填")
    pid = db.create_provider(name, base_url, model, req.api_key.strip(), req.is_active)
    return db.get_provider(pid)


@app.patch("/api/providers/{provider_id}")
def api_patch_provider(provider_id: int, req: ProviderPatch, request: Request):
    _require_admin(request)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    for key in ("name", "base_url", "model"):
        if key in fields:
            fields[key] = fields[key].strip()
            if not fields[key]:
                raise HTTPException(status_code=400, detail=f"{key} 不能为空")
    if "base_url" in fields:
        fields["base_url"] = fields["base_url"].rstrip("/")
    if "api_key" in fields:
        fields["api_key"] = fields["api_key"].strip()
    row = db.update_provider(provider_id, **fields)
    if row is None:
        raise HTTPException(status_code=404, detail="智能体不存在")
    return row


@app.delete("/api/providers/{provider_id}")
def api_delete_provider(provider_id: int, request: Request):
    _require_admin(request)
    if not db.delete_provider(provider_id):
        raise HTTPException(status_code=404, detail="智能体不存在")
    return {"ok": True}


@app.get("/api/limits")
def api_list_limits(request: Request):
    """管理员视角：每个人这个月用了多少、额度多少。"""
    _require_admin(request)
    usage = {r["id"]: r for r in db.usage_by_user(_month_start_iso())}
    items = []
    for u in db.list_user_limits():
        used = usage.get(u["id"])
        items.append(
            {
                "user_id": u["id"],
                "username": u["username"],
                "display_name": u["display_name"],
                "is_admin": u["is_admin"],
                "monthly_tokens": u["monthly_tokens"],
                "used_tokens": int(used["tokens"]) if used else 0,
                "calls": int(used["calls"]) if used else 0,
            }
        )
    return {"items": items}


@app.put("/api/limits/{user_id}")
def api_set_limit(user_id: int, req: LimitRequest, request: Request):
    """给某人设月度 token 额度；0 = 不限。"""
    _require_admin(request)
    if req.monthly_tokens < 0:
        raise HTTPException(status_code=400, detail="额度不能是负数")
    if db.get_user(user_id) is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    db.set_user_limit(user_id, req.monthly_tokens)
    return {"ok": True}


# ---- 演示数据：一键造 / 一键清（只有管理员能用） ----
# 所有数据带「演示·」前缀，清空只删带前缀的，真实数据一条不碰。


@app.post("/api/demo/seed")
def api_seed_demo(request: Request):
    """给自己造一套演示数据。客户/提醒归自己，产品/模板是团队共享的（都带「演示·」前缀）。"""
    user = auth.require_user(request)
    return {"ok": True, "created": db.seed_demo_data(user["id"])}


@app.post("/api/demo/clear")
def api_clear_demo(request: Request):
    """清掉所有「演示·」前缀的数据。真实数据一条不碰。"""
    auth.require_user(request)
    return {"ok": True, "removed": db.clear_demo_data()}


# ==================================================================
# 页面
# ==================================================================

# 静态文件和页面一律「每次都回来验一次」（ETag 没变就 304，代价很小）。
# 不这么做的后果是真实踩过的：前端改完、部署也成功，用户刷新还是旧界面——
# 浏览器对没有 Cache-Control 的响应会做启发式缓存。宁多一次 304，也要「刷新即见」。
NO_CACHE = {"Cache-Control": "no-cache"}


class NoCacheStaticFiles(StaticFiles):
    """静态资源的缓存策略——**分两种**，这是性能关键。

    - URL 里带 `?v=<文件修改时间>`（页面自动加的指纹）→ 长期缓存 immutable：
      文件一变 URL 就变，所以可以放心让浏览器一年不再问。
    - 没带指纹（直接访问 /static/xxx）→ no-cache，每次回来验一次。

    为什么非分不可：一开始为了修"改了看不到"全站 no-cache，代价是每次刷新都重新
    下载 workspace.js(61KB) + style.css(40KB)——跨境实测 0.76s / 0.96s，
    这才是"刷新慢"的主因，不是数据库。
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        query = (scope.get("query_string") or b"").decode("latin-1")
        if "v=" in query:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


# 页面里的静态资源 URL：/static/xxx.js 或 .css
_ASSET_RE = re.compile(r'(/static/[A-Za-z0-9_\-.]+\.(?:js|css))(")')


def _page(path: Path) -> Response:
    """发页面时，给静态资源的 URL 带上「文件修改时间」当版本号。

    为什么非做不可：浏览器手里可能留着**旧缓存条目**（在 no-cache 头生效之前存下的），
    那条条目自己带着旧的缓存策略，普通刷新不一定换新——于是就出现
    「HTML 是新的、JS 还是旧的」这种半新不旧的界面：左侧导航有新栏目，点了却没反应、内容区全白。
    给 URL 加版本号后，文件一变 URL 就变，浏览器没得选，只能重新拉。
    """
    html = path.read_text(encoding="utf-8")

    def with_version(match: "re.Match[str]") -> str:
        url = match.group(1)
        try:
            version = int((BASE_DIR / url.lstrip("/")).stat().st_mtime)
        except OSError:
            version = 0
        return f"{url}?v={version}{match.group(2)}"

    return HTMLResponse(_ASSET_RE.sub(with_version, html), headers=NO_CACHE)


@app.get("/")
def index():
    """把前端页面发回浏览器。"""
    return _page(BASE_DIR / "static" / "index.html")


@app.get("/login")
def login_page():
    """独立登录页。未登录的 /app 会被送到这里，登录成功再跳回去。"""
    return _page(BASE_DIR / "static" / "login.html")


@app.get("/app")
def workspace(request: Request):
    """V2：外贸客户工作台（登录后使用）。

    **未登录在服务端就挡住**（302 去登录页，带 next 回来）——比「前端藏内容」稳：
    脚本出错时也不会停在一片空白上。真正的权限仍然在每个接口里，这里只是少让人撞墙。
    """
    if auth.current_user(request) is None:
        return RedirectResponse("/login?next=/app", status_code=302)
    return _page(BASE_DIR / "static" / "workspace.html")


app.mount("/static", NoCacheStaticFiles(directory=BASE_DIR / "static"), name="static")
