"""多智能体（provider）与额度：管理员配渠道、生成时选渠道、按人卡 token 额度。

模型调用全部 mock——不花钱、不依赖外网。

跑法（项目根目录）：
    ./.venv/Scripts/python.exe tests/test_providers.py -v
"""
import unittest
import uuid
from unittest.mock import patch

import _db
from _db import client as _client, ensure_admin

FAKE_RESULT = {"text": "草稿正文", "tokens_in": 100, "tokens_out": 200, "model": "fake-model"}


def setUpModule():
    ensure_admin()


def _phone():
    return "139" + str(uuid.uuid4().int)[:8]


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self.admin_c = _db.ADMIN["client"]
        self.a_c = _client()
        self.a = _db.register(self.a_c, "prov_a")

    def _new_provider(self, name=None, key="sk-test-secret-value"):
        name = name or f"渠道_{uuid.uuid4().hex[:6]}"
        r = self.admin_c.post(
            "/api/providers",
            json={
                "name": name,
                "base_url": "https://api.example.com/v1",
                "model": "test-model",
                "api_key": key,
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    # ---------- 安全边界 ----------

    def test_api_key_is_never_returned(self):
        p = self._new_provider()
        self.assertNotIn("api_key", p)
        self.assertTrue(p["has_key"])
        listed = self.admin_c.get("/api/providers").json()["items"]
        self.assertTrue(all("api_key" not in x for x in listed), "列表也不能回显 key")

    def test_api_key_is_encrypted_at_rest(self):
        secret = "sk-test-secret-value"
        self._new_provider(key=secret)
        rows = _db.sql("SELECT api_key FROM ai_providers")
        self.assertTrue(rows, "应该至少有一条记录")
        self.assertTrue(
            all(secret not in (r[0] or "") for r in rows), "库里不能是明文"
        )

    def test_normal_user_cannot_write_providers(self):
        r = self.a_c.post(
            "/api/providers",
            json={"name": "x", "base_url": "https://x", "model": "m", "api_key": "k"},
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.a_c.get("/api/limits").status_code, 403)

    def test_normal_user_can_see_available_providers(self):
        self._new_provider()
        r = self.a_c.get("/api/providers")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["items"], "普通同事要能看到有哪些渠道路可选")

    # ---------- 生成时选渠道 ----------

    @patch("main.generate_pitch", return_value=FAKE_RESULT)
    def test_generate_passes_selected_provider(self, mock):
        p = self._new_provider()
        c = self.a_c.post(
            "/api/customers",
            json={"contact_name": "选渠道客户", "phone": _phone(), "country": "美国"},
        ).json()
        r = self.a_c.post(
            f"/api/customers/{c['id']}/generate",
            json={"request": "催单", "provider_id": p["id"]},
        )
        self.assertEqual(r.status_code, 200, r.text)
        args = mock.call_args
        provider = args.args[4] if len(args.args) > 4 else args.kwargs.get("provider")
        self.assertIsNotNone(provider, "选中的渠道要传进 generate_pitch")
        self.assertEqual(provider["base_url"], "https://api.example.com/v1")
        self.assertEqual(provider["model"], "test-model")
        # key 必须是解密后的明文（真的是发给上游用的），但不出现在响应里
        self.assertEqual(provider["api_key"], "sk-test-secret-value")
        self.assertNotIn("sk-test-secret-value", r.text)

    @patch("main.generate_pitch", return_value=FAKE_RESULT)
    def test_inactive_provider_is_rejected(self, _mock):
        p = self._new_provider()
        self.admin_c.patch(f"/api/providers/{p['id']}", json={"is_active": False})
        c = self.a_c.post(
            "/api/customers",
            json={"contact_name": "停用渠道", "phone": _phone(), "country": "美国"},
        ).json()
        r = self.a_c.post(
            f"/api/customers/{c['id']}/generate",
            json={"request": "催单", "provider_id": p["id"]},
        )
        self.assertEqual(r.status_code, 404)

    # ---------- 记账 ----------

    @patch("main.generate_pitch", return_value=FAKE_RESULT)
    def test_tokens_are_recorded(self, _mock):
        c = self.a_c.post(
            "/api/customers",
            json={"contact_name": "记账客户", "phone": _phone(), "country": "美国"},
        ).json()
        r = self.a_c.post(f"/api/customers/{c['id']}/generate", json={"request": "催单"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["tokens"], {"in": 100, "out": 200})

        rows = _db.sql(
            "SELECT tokens_in, tokens_out FROM generations ORDER BY id DESC LIMIT 1"
        )
        self.assertEqual(tuple(rows[0]), (100, 200))

    # ---------- 额度 ----------

    @patch("main.generate_pitch", return_value=FAKE_RESULT)
    def test_monthly_token_limit_blocks(self, _mock):
        """额度用完就停——同事用的是公司额度，不是谁的私人 key。"""
        self.admin_c.put(f"/api/limits/{self.a['id']}", json={"monthly_tokens": 250})
        c = self.a_c.post(
            "/api/customers",
            json={"contact_name": "额度客户", "phone": _phone(), "country": "美国"},
        ).json()
        first = self.a_c.post(f"/api/customers/{c['id']}/generate", json={"request": "1"})
        self.assertEqual(first.status_code, 200, first.text)  # 用了 300 tokens > 250
        second = self.a_c.post(f"/api/customers/{c['id']}/generate", json={"request": "2"})
        self.assertEqual(second.status_code, 429)

    @patch("main.generate_pitch", return_value=FAKE_RESULT)
    def test_zero_limit_means_unlimited(self, _mock):
        self.admin_c.put(f"/api/limits/{self.a['id']}", json={"monthly_tokens": 0})
        c = self.a_c.post(
            "/api/customers",
            json={"contact_name": "不限额度", "phone": _phone(), "country": "美国"},
        ).json()
        for i in range(3):
            r = self.a_c.post(f"/api/customers/{c['id']}/generate", json={"request": str(i)})
            self.assertEqual(r.status_code, 200, "0 = 不限，不该被拦")

    def test_limits_view_reports_usage(self):
        items = self.admin_c.get("/api/limits").json()["items"]
        self.assertTrue(items)
        row = [x for x in items if x["user_id"] == self.a["id"]][0]
        self.assertIn("used_tokens", row)
        self.assertIn("monthly_tokens", row)


if __name__ == "__main__":
    unittest.main(verbosity=2)
