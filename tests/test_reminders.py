"""第 4 批需求的后端测试：提醒（日历）/ 产品自定义属性 / 标签删除与合并。

先写期望行为，再让实现去满足它。

跑法（项目根目录）：
    ./.venv/Scripts/python.exe tests/test_reminders.py -v
"""
import datetime as dt
import unittest
import uuid

import _db
from _db import client as _client, ensure_admin


def setUpModule():
    ensure_admin()


def _today():
    return dt.date.today().isoformat()


def _days_ago(n):
    return (dt.date.today() - dt.timedelta(days=n)).isoformat()


class ReminderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a_c = _client()
        cls.a = _db.register(cls.a_c, "rem_a")
        cls.b_c = _client()
        cls.b = _db.register(cls.b_c, "rem_b")

    def _customer(self, c=None, name="提醒客户"):
        r = (c or self.a_c).post(
            "/api/customers",
            json={"contact_name": name, "phone": "139" + str(uuid.uuid4().int)[:8], "country": "美国"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    # ---------- 增删查 ----------

    def test_create_list_and_delete(self):
        c = self._customer()
        r = self.a_c.post(
            "/api/reminders",
            json={"customer_id": c["id"], "due_date": _today(), "note": "打电话确认数量"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        rid = r.json()["id"]

        items = self.a_c.get("/api/reminders", params={"date_from": _today(), "date_to": _today()}).json()["items"]
        self.assertIn(rid, [x["id"] for x in items])
        self.assertEqual(items[0]["contact_name"], "提醒客户")

        self.assertEqual(self.a_c.delete(f"/api/reminders/{rid}").status_code, 200)
        left = self.a_c.get("/api/reminders", params={"date_from": _today(), "date_to": _today()}).json()["items"]
        self.assertNotIn(rid, [x["id"] for x in left])

    def test_bad_date_is_rejected(self):
        c = self._customer()
        r = self.a_c.post(
            "/api/reminders", json={"customer_id": c["id"], "due_date": "2026-13-45"}
        )
        self.assertEqual(r.status_code, 400)

    # ---------- 红点数字 ----------

    def test_pending_count_includes_today_and_overdue(self):
        """红点 = 今天及以前还没完成的条数（迟到没做的也要提醒，不能漏）。"""
        before = self.a_c.get("/api/reminders/pending-count").json()["count"]
        c = self._customer()
        r1 = self.a_c.post("/api/reminders", json={"customer_id": c["id"], "due_date": _today()})
        r2 = self.a_c.post("/api/reminders", json={"customer_id": c["id"], "due_date": _days_ago(2)})
        after = self.a_c.get("/api/reminders/pending-count").json()["count"]
        self.assertEqual(after, before + 2, "今天一条 + 逾期一条都要算进红点")

        # 做完一条，红点减一
        self.a_c.patch(f"/api/reminders/{r1.json()['id']}", json={"done": True})
        self.assertEqual(
            self.a_c.get("/api/reminders/pending-count").json()["count"], before + 1
        )
        # 收尾：把超期那条删掉，别影响其他用例
        self.a_c.delete(f"/api/reminders/{r2.json()['id']}")

    def test_future_reminder_not_counted(self):
        before = self.a_c.get("/api/reminders/pending-count").json()["count"]
        c = self._customer()
        future = (dt.date.today() + dt.timedelta(days=5)).isoformat()
        r = self.a_c.post("/api/reminders", json={"customer_id": c["id"], "due_date": future})
        self.assertEqual(self.a_c.get("/api/reminders/pending-count").json()["count"], before)
        self.a_c.delete(f"/api/reminders/{r.json()['id']}")

    # ---------- 权限 ----------

    def test_cannot_set_reminder_on_others_customer(self):
        c = self._customer()
        r = self.b_c.post(
            "/api/reminders", json={"customer_id": c["id"], "due_date": _today()}
        )
        self.assertEqual(r.status_code, 404, "别人的客户，连存在都不该看到")

    def test_reminders_are_personal(self):
        c = self._customer()
        r = self.a_c.post("/api/reminders", json={"customer_id": c["id"], "due_date": _today()})
        rid = r.json()["id"]
        mine = self.b_c.get("/api/reminders", params={"date_from": _today(), "date_to": _today()}).json()["items"]
        self.assertNotIn(rid, [x["id"] for x in mine], "提醒是个人日程，别人看不到")
        self.assertEqual(self.b_c.patch(f"/api/reminders/{rid}", json={"done": True}).status_code, 404)
        self.a_c.delete(f"/api/reminders/{rid}")

    def test_requires_login(self):
        anon = _client()
        self.assertEqual(anon.get("/api/reminders").status_code, 401)
        self.assertEqual(anon.get("/api/reminders/pending-count").status_code, 401)


class ProductAttrTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a_c = _client()
        cls.a = _db.register(cls.a_c, "prod_a")

    def test_create_product_with_custom_attrs(self):
        """产品除了名称/规格/价格，还能挂任意自定义属性（材质、MOQ、交期…）。"""
        r = self.a_c.post(
            "/api/products",
            json={
                "name": f"不锈钢法兰_{uuid.uuid4().hex[:6]}",
                "spec": "DN50 PN16",
                "price": "12.5 USD",
                "attrs": {"材质": "304 不锈钢", "MOQ": "500 件", "交期": "25 天"},
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        pid = r.json()["id"]

        items = self.a_c.get("/api/products").json()["items"]
        mine = [p for p in items if p["id"] == pid][0]
        self.assertEqual(mine["attrs"]["材质"], "304 不锈钢")
        self.assertEqual(mine["attrs"]["MOQ"], "500 件")

    def test_patch_product_attrs(self):
        r = self.a_c.post("/api/products", json={"name": f"垫片_{uuid.uuid4().hex[:6]}"})
        pid = r.json()["id"]
        r2 = self.a_c.patch(
            f"/api/products/{pid}", json={"attrs": {"标准": "DIN", "包装": "纸箱"}}
        )
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertEqual(r2.json()["attrs"]["标准"], "DIN")
        self.assertEqual(r2.json()["attrs"]["包装"], "纸箱")


class TagManageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a_c = _client()
        cls.a = _db.register(cls.a_c, "tagm_a")

    def _new_tag(self, name=None):
        name = name or f"待删_{uuid.uuid4().hex[:6]}"
        r = self.a_c.post("/api/tags", json={"grp": "场景", "name": name})
        self.assertEqual(r.status_code, 200, r.text)
        return name, r.json()["id"]

    def _new_template(self, tag_ids):
        r = self.a_c.post(
            "/api/templates",
            json={"title": f"模板_{uuid.uuid4().hex[:6]}", "body": "Hi {客户名}", "tag_ids": tag_ids},
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_delete_tag_removes_it_from_templates(self):
        """标签能真删（不只是停用）：删掉后它从列表消失，模板上也不再挂它。"""
        name, tid = self._new_tag()
        tpl = self._new_template([tid])
        self.assertIn(tid, [t["id"] for t in tpl["tags"]])

        r = self.a_c.delete(f"/api/tags/{tid}")
        self.assertEqual(r.status_code, 200, r.text)

        tags = {(t["grp"], t["name"]) for t in self.a_c.get("/api/tags", params={"include_inactive": True}).json()["items"]}
        self.assertNotIn(("场景", name), tags, "删掉就该彻底没有了")
        left = self.a_c.get(f"/api/templates/{tpl['id']}").json()["tags"]
        self.assertEqual([t["id"] for t in left], [], "模板上的关联也要一起清掉")

    def test_merge_tag_moves_templates(self):
        """把「facebook」合并进「FB」：模板上的旧标签换成新的，旧标签消失。"""
        old_name, old_id = self._new_tag()
        new_name, new_id = self._new_tag()
        tpl = self._new_template([old_id])

        r = self.a_c.post(f"/api/tags/{old_id}/merge", json={"into_id": new_id})
        self.assertEqual(r.status_code, 200, r.text)

        got = self.a_c.get(f"/api/templates/{tpl['id']}").json()["tags"]
        self.assertEqual([t["id"] for t in got], [new_id])
        all_tags = {
            (t["grp"], t["name"]) for t in self.a_c.get("/api/tags", params={"include_inactive": True}).json()["items"]
        }
        self.assertNotIn(("场景", old_name), all_tags)

    def test_tag_usage_count_reported(self):
        """删除前要能知道「有几个模板在用它」，否则删起来没底。"""
        name, tid = self._new_tag()
        self._new_template([tid])
        self._new_template([tid])
        items = self.a_c.get("/api/tags", params={"include_inactive": True}).json()["items"]
        mine = [t for t in items if t["id"] == tid][0]
        self.assertEqual(mine["use_count"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
