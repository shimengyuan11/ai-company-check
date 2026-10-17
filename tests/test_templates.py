"""第 3 期：话术库（模板 + 标签）的回归测试。

钉住四条：
1. 模板和标签是**团队共享**的——B 能看、也能改 A 建的模板（和客户「各管各的」不同）
2. 标签停用后从下拉消失，但老模板上仍然看得到（不能把历史抽走）
3. 标签固定三组，乱写组名要被拒
4. 未登录一律 401

跑法（项目根目录）：
    ./.venv/Scripts/python.exe -m unittest discover -s tests -v
"""
import unittest
import uuid

import _db
from _db import client as _client, ensure_admin


def setUpModule():
    ensure_admin()


class TemplateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 只注册一次：模板是共享的，各测试用唯一标题/标签区分，不靠「列表长度」断言
        cls.a_c = _client()
        cls.a = _db.register(cls.a_c, "tpl_a")
        cls.b_c = _client()
        cls.b = _db.register(cls.b_c, "tpl_b")

    # ---------- 小工具 ----------

    def _tag_map(self, c=None):
        data = (c or self.a_c).get("/api/tags").json()
        return {(t["grp"], t["name"]): t for t in data["items"]}

    def _new_tag(self, grp="场景", name=None):
        name = name or f"临时_{uuid.uuid4().hex[:6]}"
        r = self.a_c.post("/api/tags", json={"grp": grp, "name": name})
        self.assertEqual(r.status_code, 200, r.text)
        return name, r.json()["id"]

    def _create(self, body="Hi {客户名}，关于 {产品}…", tag_ids=None, c=None):
        title = f"标题_{uuid.uuid4().hex[:6]}"
        r = (c or self.a_c).post(
            "/api/templates", json={"title": title, "body": body, "tag_ids": tag_ids or []}
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    # ---------- 默认标签 ----------

    def test_default_tags_are_seeded(self):
        """三组默认标签要铺好——否则第一次打开话术库面对空标签表没法起手。"""
        tags = self._tag_map()
        for grp in ("来源", "类型", "场景"):
            self.assertTrue(
                [k for k in tags if k[0] == grp], f"{grp} 组应该有默认标签"
            )
        self.assertIn(("场景", "逼单"), tags)
        self.assertIn(("来源", "FB"), tags)

    def test_bad_group_is_rejected(self):
        r = self.a_c.post("/api/tags", json={"grp": "乱写的组", "name": "x"})
        self.assertEqual(r.status_code, 400)

    # ---------- 模板 ----------

    def test_create_template_with_tags_and_filter_by_tag(self):
        name, tid = self._new_tag()
        tpl = self._create(tag_ids=[tid])
        self.assertEqual([t["id"] for t in tpl["tags"]], [tid])

        listed = self.a_c.get("/api/templates", params={"tag_id": tid}).json()["items"]
        self.assertIn(tpl["id"], [t["id"] for t in listed])
        # 这个标签下不该混进别的模板
        self.assertTrue(all(tid in [x["id"] for x in t["tags"]] for t in listed))

    def test_template_is_shared_across_users(self):
        tpl = self._create(body="共享测试 {客户名}")
        seen = [t["id"] for t in self.b_c.get("/api/templates").json()["items"]]
        self.assertIn(tpl["id"], seen, "话术库是团队共享的，B 应该看得到 A 建的模板")

        r = self.b_c.patch(f"/api/templates/{tpl['id']}", json={"body": "B 改过的正文"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["body"], "B 改过的正文")

    def test_template_validation_and_delete(self):
        r = self.a_c.post("/api/templates", json={"title": "  ", "body": "x"})
        self.assertEqual(r.status_code, 400, "空标题要拒")

        tpl = self._create()
        self.assertEqual(self.a_c.delete(f"/api/templates/{tpl['id']}").status_code, 200)
        self.assertEqual(self.a_c.get(f"/api/templates/{tpl['id']}").status_code, 404)

    # ---------- 标签停用 ----------

    def test_deactivated_tag_hides_from_list_but_stays_on_template(self):
        name, tid = self._new_tag()
        tpl = self._create(tag_ids=[tid])

        r = self.a_c.patch(f"/api/tags/{tid}", json={"is_active": False})
        self.assertEqual(r.status_code, 200, r.text)

        # 下拉里没了
        active = {(t["grp"], t["name"]) for t in self.a_c.get("/api/tags").json()["items"]}
        self.assertNotIn(("场景", name), active)
        # 但老模板上还挂着（历史不能被抽走）
        kept = self.a_c.get(f"/api/templates/{tpl['id']}").json()["tags"]
        self.assertEqual([t["id"] for t in kept], [tid])
        # 管理视图要能看到停用的
        all_tags = self.a_c.get("/api/tags", params={"include_inactive": True}).json()["items"]
        self.assertIn(("场景", name), {(t["grp"], t["name"]) for t in all_tags})

    # ---------- 登录门禁 ----------

    def test_requires_login(self):
        anon = _client()
        self.assertEqual(anon.get("/api/tags").status_code, 401)
        self.assertEqual(anon.get("/api/templates").status_code, 401)
        r = anon.post("/api/templates", json={"title": "x", "body": "y"})
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
