"""V2 权限与客户移交的回归测试。

为什么需要它：客户「各管各的」这条规则只写在 SQL 里，没有任何自动化护栏，
而「A 看得到 B 的客户」在本项目里是事故级问题。这里把三条规则钉死：

1. 普通业务看不到别人的客户（列表为空、按 id 拿 404、改/删/写跟进/传附件一律 404）
2. 管理员看得到全部（为了「离职转接」）
3. 移交之后归属真的换人（原主人立刻看不到，新主人能写跟进）
4. 防撞单：尾号 9 位相同也算同一个号，回 409

隔离方式：在同一个 Neon 库里开一个临时 schema，用 `options=-c search_path=...`
把连接钉进去，跑完 drop 掉。**不碰生产数据**，也不碰共享的 options / products
（它们同样落在临时 schema 里）。
注意：Neon 的池化端点（host 带 -pooler）不接受启动参数，所以这里把 "-pooler" 去掉走直连。

跑法（项目根目录）：
    ./.venv/Scripts/python.exe -m unittest discover -s tests -v
"""
import os
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

_base = os.environ.get("DATABASE_URL", "")
if not _base:
    raise RuntimeError("缺少 DATABASE_URL：测试要读 .env 里的连接串")

# Neon 池化端点不支持 startup 参数（实测报错 unsupported startup parameter in options）
_DIRECT = _base.replace("-pooler", "")
SCHEMA = "rivet_test_" + uuid.uuid4().hex[:8]

_sep = "&" if "?" in _DIRECT else "?"
os.environ["DATABASE_URL"] = f"{_DIRECT}{_sep}options=-c%20search_path%3D{SCHEMA}"
os.environ["SIGNUP_CODE"] = "testcode"  # 注册需要邀请码；测试里给一个固定值
os.environ["SESSION_SECRET"] = "test-secret"

import psycopg  # noqa: E402

# 必须先建好 schema，再 import main —— main 在 import 时就会 init_db() 建表
with psycopg.connect(_DIRECT, autocommit=True) as _conn:
    _conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')

from fastapi.testclient import TestClient  # noqa: E402

import main as app_module  # noqa: E402

app = app_module.app

ADMIN = {}  # 模块级管理员（第一个注册的人才会成为管理员，所以要建一次共用）


def _client():
    """每个 client 自带一套 cookie —— 这就是「两个不同的人」。"""
    return TestClient(app)


def _register(client, prefix):
    username = f"{prefix}_{uuid.uuid4().hex[:6]}"
    r = client.post(
        "/api/register",
        json={"username": username, "password": "pw123456", "code": "testcode"},
    )
    assert r.status_code == 200, r.text
    return r.json()["user"]


def _phone():
    """每个客户一个不重复的号码，免得测试之间互相触发 409。"""
    return "139" + str(uuid.uuid4().int)[:8]


def _create(client, name="测试客户", phone=None, country="美国"):
    r = client.post(
        "/api/customers",
        json={"contact_name": name, "phone": phone or _phone(), "country": country},
    )
    assert r.status_code == 200, r.text
    return r.json()


def setUpModule():
    c = _client()
    ADMIN["user"] = _register(c, "admin")
    ADMIN["client"] = c


def tearDownModule():
    app_module.db.pool.close()
    with psycopg.connect(_DIRECT, autocommit=True) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')


class PermissionTest(unittest.TestCase):
    def setUp(self):
        self.a_c, self.b_c = _client(), _client()
        self.a = _register(self.a_c, "user_a")
        self.b = _register(self.b_c, "user_b")

    # ---------- 规则 1：各管各的 ----------

    def test_admin_is_only_the_first_user(self):
        """管理员只能是第一个注册的人——这条错了，权限模型就塌了。"""
        admin_me = ADMIN["client"].get("/api/me").json()["user"]
        self.assertTrue(admin_me["is_admin"])
        self.assertFalse(self.a_c.get("/api/me").json()["user"]["is_admin"])

    def test_other_user_cannot_see_customer(self):
        c = _create(self.a_c, "张三")
        self.assertEqual(c["owner_id"], self.a["id"])

        listed = self.b_c.get("/api/customers").json()["items"]
        self.assertEqual([x["id"] for x in listed], [], "B 的列表里不该有 A 的客户")

        self.assertEqual(self.b_c.get(f"/api/customers/{c['id']}").status_code, 404)

    def test_other_user_cannot_modify_or_delete(self):
        c = _create(self.a_c, "李四")
        cid = c["id"]
        self.assertEqual(
            self.b_c.patch(f"/api/customers/{cid}", json={"note": "越权改"}).status_code, 404
        )
        self.assertEqual(self.b_c.delete(f"/api/customers/{cid}").status_code, 404)
        self.assertEqual(
            self.b_c.post(f"/api/customers/{cid}/followups", json={"content": "越权写"}).status_code,
            404,
        )
        self.assertEqual(
            self.b_c.post(
                f"/api/customers/{cid}/attachments", files={"file": ("x.txt", b"hi")}
            ).status_code,
            404,
        )
        # A 那边一切如常，数据没被动过
        mine = self.a_c.get(f"/api/customers/{cid}").json()
        self.assertEqual(mine["note"], "")

    def test_anonymous_is_rejected(self):
        anon = _client()
        self.assertEqual(anon.get("/api/customers").status_code, 401)
        # 注意体必须合法：FastAPI 先做参数校验再进函数体，空 body 会先返回 422
        # （422 也不建任何东西，只是校验先撞上了；要测的是「认证在拦」）
        r = anon.post(
            "/api/customers",
            json={"contact_name": "陌生人", "phone": _phone(), "country": "美国"},
        )
        self.assertEqual(r.status_code, 401, "未登录不该建得下客户")

    # ---------- 登录页与 /app 的门禁 ----------

    def test_login_page_renders(self):
        r = _client().get("/login")
        self.assertEqual(r.status_code, 200)
        self.assertIn("登录", r.text)

    def test_app_redirects_anonymous_to_login(self):
        r = _client().get("/app", follow_redirects=False)
        self.assertEqual(r.status_code, 302, "未登录访问工作台应该被送去登录页")
        self.assertEqual(r.headers["location"], "/login?next=/app")
        # 登录之后能进
        self.assertEqual(self.a_c.get("/app").status_code, 200)

    # ---------- 规则 2：管理员看得到全部 ----------

    def test_admin_sees_everyones_customers(self):
        c = _create(self.a_c, "王五")
        items = ADMIN["client"].get("/api/customers").json()["items"]
        self.assertIn(c["id"], [x["id"] for x in items])

    # ---------- 规则 3：移交 ----------

    def test_transfer_moves_ownership(self):
        c = _create(self.a_c, "赵六")
        cid = c["id"]
        r = self.a_c.post(f"/api/customers/{cid}/transfer", json={"to_user_id": self.b["id"]})
        self.assertEqual(r.status_code, 200, r.text)

        # 新主人：列表里有、能写跟进
        self.assertIn(cid, [x["id"] for x in self.b_c.get("/api/customers").json()["items"]])
        self.assertEqual(
            self.b_c.post(f"/api/customers/{cid}/followups", json={"content": "接手第一条"}).status_code,
            200,
        )
        # 原主人：列表里没了、按 id 拿 404
        self.assertNotIn(cid, [x["id"] for x in self.a_c.get("/api/customers").json()["items"]])
        self.assertEqual(self.a_c.get(f"/api/customers/{cid}").status_code, 404)

    def test_cannot_transfer_someone_elses_customer(self):
        c = _create(self.a_c, "钱七")
        r = self.b_c.post(f"/api/customers/{c['id']}/transfer", json={"to_user_id": self.b["id"]})
        self.assertEqual(r.status_code, 404, "偷别人的客户来转，应该连存在都看不到")

    # ---------- 规则 4：防撞单 ----------

    def test_duplicate_phone_is_rejected_across_users(self):
        _create(self.a_c, "孙八", phone="+86 138 0000 4321")
        r = self.b_c.post(
            "/api/customers",
            json={"contact_name": "孙八", "phone": "13800004321", "country": "美国"},
        )
        self.assertEqual(r.status_code, 409, "同一号码（尾号 9 位相同）再建档应该被拒")

    def test_duplicate_detail_does_not_leak_owner(self):
        _create(self.a_c, "周九", phone="+86 138 0000 8765")
        r = self.b_c.post(
            "/api/customers",
            json={"contact_name": "周九", "phone": "13800008765", "country": "美国"},
        )
        self.assertEqual(r.status_code, 409)
        detail = r.json()["detail"]
        self.assertNotIn(self.a["username"], detail, "409 的提示不能泄露是谁建的")
        self.assertNotIn(str(self.a["id"]), detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
