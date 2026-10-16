"""登录相关：密码怎么存、当前用户是谁、谁能注册。

三条安全底线（都在这份文件里）：
1. 密码**只存哈希**，绝不存明文；bcrypt 自带随机盐，同一密码每次结果都不同。
2. 任何接口想知道「你是谁」，都从会话里读，绝不信任前端传来的 user_id。
3. password_hash 绝不返回给前端——只给能见人的字段。
"""
import os

import bcrypt
from fastapi import HTTPException, Request

import db

# 邀请码：库里已经有用户之后，注册需要填对它。
# 没配邀请码时一律拒绝注册（fail-closed：宁可拒绝，也不放陌生人进来）。
SIGNUP_CODE = os.environ.get("SIGNUP_CODE", "")


def hash_password(password: str) -> str:
    """bcrypt 自带盐——同一个密码每次哈希结果都不同，这是防彩虹表的关键。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """校验密码。哈希串损坏时返回 False，而不是抛异常。"""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def registration_allowed(code: str):
    """能不能注册。返回 (是否允许, 是否成为管理员)。

    **一律要求邀请码**——哪怕库里一个用户都没有。
    为什么不能「第一个注册的人免邀请码」：这个站是公开的，
    在真实用户注册之前，万一有陌生人先注册，他就成了管理员——
    能看到所有人的客户、还把真实主人锁在外面。宁可多配一个环境变量。
    """
    if not SIGNUP_CODE:
        return False, False  # 没配邀请码 → 一律拒绝（fail-closed，宁可拒绝也不放人进来）
    if code != SIGNUP_CODE:
        return False, False
    return True, db.count_users() == 0  # 第一个用邀请码注册的人成为管理员


def current_user(request: Request):
    """从**会话**里取当前登录用户（不从前端参数取——那是可以被伪造的）。"""
    uid = request.session.get("user_id")
    if not uid:
        return None
    user = db.get_user(uid)
    if not user or not user["is_active"]:
        request.session.clear()  # 用户被停用了，会话当场作废
        return None
    return user


def require_user(request: Request):
    """必须登录，否则 401。"""
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def public_user(user):
    """只把能见人的字段给前端——password_hash 绝不出口。"""
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"],
        "is_admin": user["is_admin"],
    }
