"""演示数据：一键造 / 一键清，且**清空不碰真实数据**。

跑法（项目根目录）：
    ./.venv/Scripts/python.exe tests/test_demo.py -v
"""
import unittest
import uuid

import _db
from _db import client as _client, ensure_admin


def setUpModule():
    ensure_admin()


def _phone():
    return "139" + str(uuid.uuid4().int)[:8]


class DemoSeedTest(unittest.TestCase):
    def setUp(self):
        self.admin_c = _db.ADMIN["client"]
        self.a_c = _client()
        self.a = _db.register(self.a_c, "demo_a")

    def test_seed_creates_five_per_section(self):
        r = self.admin_c.post("/api/demo/seed")
        self.assertEqual(r.status_code, 200, r.text)
        c = r.json()["created"]
        self.assertEqual(c["customers"], 5)
        self.assertEqual(c["products"], 5)
        self.assertEqual(c["templates"], 5)
        self.assertEqual(c["reminders"], 5)
        self.assertGreaterEqual(c["followups"], 5)

        # 各栏目接口真能看到（不是只在统计里加了个数）
        items = self.admin_c.get("/api/customers").json()["items"]
        demo = [x for x in items if x["contact_name"].startswith("演示·")]
        self.assertEqual(len(demo), 5)
        stages = {x["stage"] for x in demo}
        self.assertGreaterEqual(len(stages), 4, "演示客户的阶段要各不相同")

        prods = self.admin_c.get("/api/products").json()["items"]
        self.assertTrue(any(p["name"].startswith("演示·") for p in prods))
        tpls = self.admin_c.get("/api/templates").json()["items"]
        self.assertTrue(any(t["title"].startswith("演示·") for t in tpls))

    def test_clear_removes_only_demo(self):
        real = self.a_c.post(
            "/api/customers",
            json={"contact_name": "真实客户", "phone": _phone(), "country": "美国"},
        )
        self.assertEqual(real.status_code, 200, real.text)

        self.admin_c.post("/api/demo/seed")
        r = self.admin_c.post("/api/demo/clear")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertGreaterEqual(r.json()["removed"]["customers"], 5)

        names = [x["contact_name"] for x in self.admin_c.get("/api/customers").json()["items"]]
        self.assertFalse(any(n.startswith("演示·") for n in names), "演示数据要清干净")
        self.assertIn("真实客户", names, "真实数据一条都不能少")

    def test_seed_twice_does_not_duplicate(self):
        self.admin_c.post("/api/demo/seed")
        second = self.admin_c.post("/api/demo/seed").json()["created"]
        self.assertEqual(second["customers"], 5, "重复生成应先清后建，不是往上叠")
        demo = [
            x
            for x in self.admin_c.get("/api/customers").json()["items"]
            if x["contact_name"].startswith("演示·")
        ]
        self.assertEqual(len(demo), 5)

    def test_any_logged_in_user_can_seed_for_themselves(self):
        """演示数据是给自己看的，普通同事也能造——但数据归他自己，不串到别人。"""
        r = self.a_c.post("/api/demo/seed")
        self.assertEqual(r.status_code, 200, r.text)
        mine = [
            x
            for x in self.a_c.get("/api/customers").json()["items"]
            if x["contact_name"].startswith("演示·")
        ]
        self.assertEqual(len(mine), 5, "给自己造的 5 条应该看得见")
        self.assertTrue(all(x["owner_id"] == self.a["id"] for x in mine))

    def test_anonymous_cannot_seed(self):
        anon = _client()
        self.assertEqual(anon.post("/api/demo/seed").status_code, 401)
        self.assertEqual(anon.post("/api/demo/clear").status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
