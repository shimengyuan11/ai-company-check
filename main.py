import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

import auth
import db
from db import get_analysis, init_db, list_history, save_analysis
from llm import MODEL, analyze_company

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


# ==================================================================
# V2：客户 / 跟进 / 移交
# ==================================================================


class CustomerRequest(BaseModel):
    company: str
    source: str = ""
    note: str = ""


class CustomerPatch(BaseModel):
    company: str | None = None
    source: str | None = None
    note: str | None = None
    country: str | None = None
    stage: str | None = None
    high_intent: bool | None = None


class TransferRequest(BaseModel):
    to_user_id: int


class FollowupRequest(BaseModel):
    content: str


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
def api_list_customers(request: Request):
    user = auth.require_user(request)
    return {"items": db.list_customers(user["id"], user["is_admin"])}


@app.post("/api/customers")
def api_create_customer(req: CustomerRequest, request: Request):
    user = auth.require_user(request)
    company = req.company.strip()
    if not company:
        raise HTTPException(status_code=400, detail="公司名不能为空")
    cid = db.create_customer(user["id"], company, req.source.strip(), req.note.strip())
    return db.get_customer(cid, user["id"], user["is_admin"])


@app.get("/api/customers/{customer_id}")
def api_get_customer(customer_id: int, request: Request):
    user = auth.require_user(request)
    return _visible_customer(customer_id, user)


@app.patch("/api/customers/{customer_id}")
def api_patch_customer(customer_id: int, req: CustomerPatch, request: Request):
    user = auth.require_user(request)
    _visible_customer(customer_id, user)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
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


# ==================================================================
# 页面
# ==================================================================


@app.get("/")
def index():
    """把前端页面发回浏览器。"""
    return FileResponse(BASE_DIR / "static" / "index.html")


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
