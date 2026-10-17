"""生成话术的规则测试：中文提示词、每日限额、跟进不足时的标注。

**这里全部 mock 掉模型调用**——真调一次就花钱，而且依赖外网；
本文件只测「规则对不对」，不测「模型写得好不好」。

跑法（项目根目录）：
    ./.venv/Scripts/python.exe tests/test_generate.py -v
"""
import unittest
import uuid
from unittest.mock import patch

import _db
import llm
from _db import client as _client, ensure_admin

LIMIT = 5  # 与 tests/_db.py 里设的 LLM_DAILY_LIMIT 保持一致


def setUpModule():
    ensure_admin()


def _phone():
    return "139" + str(uuid.uuid4().int)[:8]


class PitchPromptTest(unittest.TestCase):
    def test_prompt_defaults_to_chinese(self):
        """模板和输出都走中文——语言种类太多，统一中文最省事（用户 2026-10-17 定的）。"""
        self.assertIn("中文", llm.PITCH_PROMPT)


class GenerateRuleTest(unittest.TestCase):
    def setUp(self):
        # 每个测试用新用户：每日限额是按用户算的，共用会互相扣次数
        self.a_c = _client()
        self.a = _db.register(self.a_c, "gen_a")
        self.c = self.a_c.post(
            "/api/customers",
            json={"contact_name": "话术客户", "phone": _phone(), "country": "美国"},
        ).json()

    def _gen(self):
        return self.a_c.post(
            f"/api/customers/{self.c['id']}/generate", json={"request": "催他回邮件"}
        )

    @patch("main.generate_pitch", return_value={"text": "草稿正文", "tokens_in": 10, "tokens_out": 20, "model": "test-model"})
    def test_basis_marks_thin_followups(self, _mock):
        """跟进不足 2 条时要标出来——草稿的依据薄，业务得知道。"""
        d = self._gen().json()
        self.assertEqual(d["basis"]["followups"], 0)
        self.assertFalse(d["basis"]["reliable"])

        self.a_c.post(f"/api/customers/{self.c['id']}/followups", json={"content": "聊过一次"})
        self.a_c.post(f"/api/customers/{self.c['id']}/followups", json={"content": "又聊过一次"})
        d2 = self._gen().json()
        self.assertEqual(d2["basis"]["followups"], 2)
        self.assertTrue(d2["basis"]["reliable"], "够 2 条就算有依据")

    @patch("main.generate_pitch", return_value={"text": "草稿正文", "tokens_in": 10, "tokens_out": 20, "model": "test-model"})
    def test_daily_limit(self, _mock):
        """每日限额：到点之后回 429，别让成本悄悄失控。"""
        codes = [self._gen().status_code for _ in range(LIMIT + 1)]
        self.assertEqual(codes[:LIMIT], [200] * LIMIT)
        self.assertEqual(codes[LIMIT], 429)

    @patch("main.generate_pitch", return_value={"text": "草稿正文", "tokens_in": 10, "tokens_out": 20, "model": "test-model"})
    def test_limit_does_not_block_other_users(self, _mock):
        for _ in range(LIMIT):
            self._gen()
        self.assertEqual(self._gen().status_code, 429)

        other = _client()
        _db.register(other, "gen_b")
        c2 = other.post(
            "/api/customers",
            json={"contact_name": "另一个人的客户", "phone": _phone(), "country": "美国"},
        ).json()
        r = other.post(f"/api/customers/{c2['id']}/generate", json={"request": "催单"})
        self.assertEqual(r.status_code, 200, "限额按人算，不该误伤别人")

    @patch("main.generate_pitch", return_value={"text": "草稿正文", "tokens_in": 10, "tokens_out": 20, "model": "test-model"})
    def test_followups_are_passed_to_model(self, mock):
        """最近几条跟进必须真的传给模型，否则「参照跟进」只是嘴上说说。"""
        for i in range(4):
            self.a_c.post(f"/api/customers/{self.c['id']}/followups", json={"content": f"第 {i} 次沟通"})
        self._gen()
        args = mock.call_args
        passed = args.args[3] if len(args.args) > 3 else args.kwargs.get("followups")
        self.assertIsNotNone(passed, "跟进记录要作为参数传进 generate_pitch")
        self.assertLessEqual(len(passed), 3, "最多带 3 条，别把整个历史都塞进去")


if __name__ == "__main__":
    unittest.main(verbosity=2)
