import os
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

import auth
import db
from db import get_analysis, init_db, list_history, save_analysis
from llm import MODEL, analyze_company, generate_pitch

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
STAGES = ("新线索", "已联系", "已报价", "成交", "流失")


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
def api_list_customers(request: Request, q: str = "", stage: str = "", sort: str = "recent"):
    """客户列表。

    - `q`：搜索词（姓名 / 公司 / 号码）
    - `stage`：阶段筛选；空 = 全部，`none` = 还没标阶段的
    - `sort`：`recent` 最近建档在前（默认）/ `stale` 最久没联系在前（挑今天该跟谁）
    """
    user = auth.require_user(request)
    if stage and stage != "none" and stage not in STAGES:
        raise HTTPException(status_code=400, detail=f"不认识的客户阶段：{stage}")
    if sort not in ("recent", "stale"):
        raise HTTPException(status_code=400, detail=f"不认识的排序方式：{sort}")
    return {
        "items": db.list_customers(user["id"], user["is_admin"], q, stage, sort),
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
    return {"id": db.create_product(name, req.spec.strip(), req.price.strip(), req.note.strip())}


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


class GenerateRequest(BaseModel):
    template_id: int | None = None
    request: str = ""  # 「想达成什么」，例如：让他回邮件确认数量


@app.post("/api/customers/{customer_id}/generate")
def api_generate(customer_id: int, req: GenerateRequest, request: Request):
    """按这个客户生成一条话术，并把它存下来。

    存下来的理由：不存等于「用过就没了」，下次又得重写一遍，回到扒聊天记录。
    """
    user = auth.require_user(request)
    customer = _visible_customer(customer_id, user)  # 看不到这个客户就别想生成
    template = None
    if req.template_id:
        template = db.get_template(req.template_id)
        if template is None:
            raise HTTPException(status_code=404, detail="模板不存在")
    try:
        text = generate_pitch(customer, template["body"] if template else "", req.request)
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"调用模型失败：HTTP {e.response.status_code}（{e.response.text[:120]}）",
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"连不上模型服务：{type(e).__name__}")
    if not text:
        raise HTTPException(status_code=502, detail="模型没有返回内容，再试一次")

    gid = db.add_generation(
        customer_id, req.template_id, user["id"], req.request.strip(), text, MODEL
    )
    if template:
        db.bump_template_use(template["id"])
    return {
        "id": gid,
        "output": text,
        "model": MODEL,
        "template_title": template["title"] if template else None,
    }


@app.get("/api/customers/{customer_id}/generations")
def api_list_generations(customer_id: int, request: Request):
    """这个客户生成过什么。权限和客户本身一致——看不到客户就看不到记录。"""
    user = auth.require_user(request)
    _visible_customer(customer_id, user)
    return {"items": db.list_generations(customer_id)}


# ==================================================================
# 页面
# ==================================================================

# 静态文件和页面一律「每次都回来验一次」（ETag 没变就 304，代价很小）。
# 不这么做的后果是真实踩过的：前端改完、部署也成功，用户刷新还是旧界面——
# 浏览器对没有 Cache-Control 的响应会做启发式缓存。宁多一次 304，也要「刷新即见」。
NO_CACHE = {"Cache-Control": "no-cache"}


class NoCacheStaticFiles(StaticFiles):
    """给 /static 下的文件补上 Cache-Control: no-cache。"""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


@app.get("/")
def index():
    """把前端页面发回浏览器。"""
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/login")
def login_page():
    """独立登录页。未登录的 /app 会被送到这里，登录成功再跳回去。"""
    return FileResponse(BASE_DIR / "static" / "login.html", headers=NO_CACHE)


@app.get("/app")
def workspace(request: Request):
    """V2：外贸客户工作台（登录后使用）。

    **未登录在服务端就挡住**（302 去登录页，带 next 回来）——比「前端藏内容」稳：
    脚本出错时也不会停在一片空白上。真正的权限仍然在每个接口里，这里只是少让人撞墙。
    """
    if auth.current_user(request) is None:
        return RedirectResponse("/login?next=/app", status_code=302)
    return FileResponse(BASE_DIR / "static" / "workspace.html", headers=NO_CACHE)


app.mount("/static", NoCacheStaticFiles(directory=BASE_DIR / "static"), name="static")
