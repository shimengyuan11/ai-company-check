"""第 5 批需求的后端测试：跟进可改可删、阶段加「跟进中」、未联系天数筛选、提醒可改。

先写期望，再让实现去满足。

跑法（项目根目录）：
    ./.venv/Scripts/python.exe tests/test_edits.py -v
"""
import datetime as dt
import unittest
import uuid

import _db
from _db import client as _client, ensure_admin


def setUpModule():
    ensure_admin()


def _phone():
    return "139" + str(uuid.uuid4().int)[:8]


class FollowupEditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a_c = _client()
        cls.a = _db.register(cls.a_c, "fu_a")
        cls.b_c = _client()
        cls.b = _db.register(cls.b_c, "fu_b")

    def _customer(self, c=None):
        r = (c or self.a_c).post(
            "/api/customers",
            json={"contact_name": "跟进客户", "phone": _phone(), "country": "美国"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_edit_followup(self):
        """跟进写完发现漏了话，应该能就地改，而不是删了重写。"""
        c = self._customer()
        fid = self.a_c.post(
            f"/api/customers/{c['id']}/followups", json={"content": "打了电话，少写了一句"}
        ).json()["id"]

        r = self.a_c.patch(
            f"/api/customers/{c['id']}/followups/{fid}",
            json={"content": "打了电话，对方要 500 件"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        items = self.a_c.get(f"/api/customers/{c['id']}/followups").json()["items"]
        mine = [x for x in items if x["id"] == fid][0]
        self.assertEqual(mine["content"], "打了电话，对方要 500 件")

    def test_delete_followup(self):
        c = self._customer()
        fid = self.a_c.post(
            f"/api/customers/{c['id']}/followups", json={"content": "写错了"}
        ).json()["id"]
        self.assertEqual(
            self.a_c.delete(f"/api/customers/{c['id']}/followups/{fid}").status_code, 200
        )
        items = self.a_c.get(f"/api/customers/{c['id']}/followups").json()["items"]
        self.assertNotIn(fid, [x["id"] for x in items])

    def test_cannot_touch_others_followup(self):
        c = self._customer()
        fid = self.a_c.post(
            f"/api/customers/{c['id']}/followups", json={"content": "别人的"}
        ).json()["id"]
        self.assertEqual(
            self.b_c.patch(
                f"/api/customers/{c['id']}/followups/{fid}", json={"content": "改"}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.b_c.delete(f"/api/customers/{c['id']}/followups/{fid}").status_code, 404
        )

    def test_empty_content_rejected(self):
        c = self._customer()
        fid = self.a_c.post(
            f"/api/customers/{c['id']}/followups", json={"content": "有内容"}
        ).json()["id"]
        self.assertEqual(
            self.a_c.patch(
                f"/api/customers/{c['id']}/followups/{fid}", json={"content": "   "}
            ).status_code,
            400,
        )


class StageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a_c = _client()
        cls.a = _db.register(cls.a_c, "stage_a")

    def test_following_stage_available(self):
        self.assertIn("跟进中", self.a_c.get("/api/customers").json()["stages"])
        c = self.a_c.post(
            "/api/customers",
            json={"contact_name": "阶段客户", "phone": _phone(), "country": "美国"},
        ).json()
        r = self.a_c.patch(f"/api/customers/{c['id']}", json={"stage": "跟进中"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["stage"], "跟进中")


class IdleFilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a_c = _client()
        cls.a = _db.register(cls.a_c, "idle_a")

    def _customer(self, name):
        return self.a_c.post(
            "/api/customers",
            json={"contact_name": name, "phone": _phone(), "country": "美国"},
        ).json()

    def _backdate(self, customer_id, days):
        """把建档时间往前挪——造出「很久没联系」的历史数据。"""
        stamp = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat(
            timespec="seconds"
        )
        _db.sql("UPDATE customers SET created_at = %s WHERE id = %s", (stamp, customer_id))

    def test_idle_days_filter(self):
        fresh = self._customer("新客户")
        old = self._customer("老客户")
        self._backdate(old["id"], 12)

        ids = [
            x["id"]
            for x in self.a_c.get("/api/customers", params={"idle_days": 5}).json()["items"]
        ]
        self.assertIn(old["id"], ids, "12 天没联系的应该出现在「5 天未联系」里")
        self.assertNotIn(fresh["id"], ids, "刚建档的不该出现")

    def test_idle_days_ignores_recent_followup(self):
        c = self._customer("刚联系过的")
        self._backdate(c["id"], 20)
        self.a_c.post(f"/api/customers/{c['id']}/followups", json={"content": "今天刚聊"})
        ids = [
            x["id"]
            for x in self.a_c.get("/api/customers", params={"idle_days": 5}).json()["items"]
        ]
        self.assertNotIn(c["id"], ids, "今天跟进过的不算未联系")

    def test_bad_idle_days_rejected(self):
        self.assertEqual(
            self.a_c.get("/api/customers", params={"idle_days": -1}).status_code, 400
        )


class ReminderEditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a_c = _client()
        cls.a = _db.register(cls.a_c, "rem2_a")

    def test_edit_note_and_date(self):
        c = self.a_c.post(
            "/api/customers",
            json={"contact_name": "提醒客户2", "phone": _phone(), "country": "美国"},
        ).json()
        today = dt.date.today().isoformat()
        rid = self.a_c.post(
            "/api/reminders",
            json={"customer_id": c["id"], "due_date": today, "note": "旧备注"},
        ).json()["id"]

        tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        r = self.a_c.patch(
            f"/api/reminders/{rid}", json={"note": "新备注", "due_date": tomorrow}
        )
        self.assertEqual(r.status_code, 200, r.text)

        items = self.a_c.get(
            "/api/reminders", params={"date_from": tomorrow, "date_to": tomorrow}
        ).json()["items"]
        mine = [x for x in items if x["id"] == rid][0]
        self.assertEqual(mine["note"], "新备注")
        self.assertEqual(mine["due_date"], tomorrow)


if __name__ == "__main__":
    unittest.main(verbosity=2)
