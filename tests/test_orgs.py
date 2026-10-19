"""组织隔离：两家公司共用一个实例时，互相看不到对方的客户与背调历史。

为什么要有这组用例：管理员原本能看库里所有人的客户——那是给"一家公司内部
离职转接"用的。但同一个实例若给两家公司用，这条规则就变成隐私事故：
A 公司的管理员能看到 B 公司的客户名单。这组用例把这个边界钉死。

组织怎么来：邀请码对应一个组织。默认组织用 SIGNUP_CODE（单团队部署时行为不变）；
测试里每个用例自己建一个新组织，保证「该组织第一个人」这条前提不与其他用例抢。

跑法（项目根目录）：
    ./.venv/Scripts/python.exe tests/test_orgs.py -v
"""
import unittest
import uuid
from unittest.mock import patch

import _db
from _db import client as _client, ensure_admin


def setUpModule():
    ensure_admin()
    _db.ensure_provider()


def _new_org():
    """建一个全新组织，返回它的邀请码。每个用例一个，互不干扰。"""
    code = "org-" + uuid.uuid4().hex[:8]
    _db.sql(
        "INSERT INTO orgs (name, code, created_at) VALUES (%s, %s, %s)",
        ("测试公司 " + code[-4:], code, "2026-10-18T00:00:00+00:00"),
    )
    return code


def _phone():
    return "137" + str(uuid.uuid4().int)[:8]


def _new_customer(c, name):
    r = c.post(
        "/api/customers",
        json={"contact_name": name, "phone": _phone(), "country": "美国"},
    )
    assert r.status_code == 200, r.text
    return r.json()


class OrgIsolationTest(unittest.TestCase):
    def test_other_org_admin_cannot_see_my_customers(self):
        """A 公司管理员建的客户，B 公司管理员（哪怕也是 admin）看不到、也打不开。"""
        a_id = _new_customer(_db.ADMIN["client"], "甲公司客户")["id"]

        b = _client()
        b_user = _db.register(b, "org_b", code=_new_org())
        self.assertTrue(b_user["is_admin"], "新组织里第一个注册的人就是该组织管理员")

        listed = [x["id"] for x in b.get("/api/customers").json()["items"]]
        self.assertNotIn(a_id, listed, "跨组织的客户不该出现在列表里")
        self.assertEqual(
            b.get(f"/api/customers/{a_id}").status_code, 404, "直取跨组织客户要当它不存在"
        )

    def test_admin_sees_all_inside_own_org(self):
        """组织内部规则不变：管理员仍能看到本组织所有人的客户（离职转接要靠它）。"""
        code = _new_org()
        b_admin = _client()
        admin_user = _db.register(b_admin, "org_b_admin", code=code)
        self.assertTrue(admin_user["is_admin"])

        staff = _client()
        _db.register(staff, "org_b_staff", code=code)
        cid = _new_customer(staff, "B 公司业务员的客户")["id"]

        listed = [x["id"] for x in b_admin.get("/api/customers").json()["items"]]
        self.assertIn(cid, listed, "同组织的管理员应当能看到同事的客户")

    def test_transfer_list_stays_inside_org(self):
        """移交下拉只列本组织的人，别把别公司的同事列出来。"""
        b = _client()
        _db.register(b, "org_b_x", code=_new_org())
        names = [u["username"] for u in b.get("/api/users").json()["items"]]
        self.assertTrue(names, "至少能看到自己组织的人")
        self.assertNotIn(_db.ADMIN["user"]["username"], names, "不该出现别组织的管理员")

    def test_history_is_isolated_between_orgs(self):
        """背调历史同样按组织隔离：B 组织管理员看不到 A 组织的记录。"""
        with patch("main.analyze_company", return_value="背调正文"):
            r = _db.ADMIN["client"].post("/analyze", json={"company": "A 公司背调"})
        self.assertEqual(r.status_code, 200, r.text)
        rid = r.json()["id"]

        b = _client()
        _db.register(b, "org_b_h", code=_new_org())
        listed = [x["id"] for x in b.get("/history").json()["items"]]
        self.assertNotIn(rid, listed, "跨组织的背调记录不该出现")
        self.assertIn("error", b.get(f"/history/{rid}").json(), "直取也拿不到")


if __name__ == "__main__":
    unittest.main(verbosity=2)
