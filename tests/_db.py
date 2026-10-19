"""测试的公共地基：隔离 schema、常驻管理员、临时客户端。

为什么抽出来：`tests/` 下现在有两个测试模块（权限 / 话术库），
每个都自己建 schema 的话，跑一轮就要建两次、注册两批用户，白花时间。
放在这里以后，同一进程里只初始化一次（discover 是单进程）。

隔离怎么做：在同一个 Neon 库里开一个临时 schema，用 `options=-c search_path=...`
把连接钉进去；进程退出时 atexit 里 drop 掉。**不碰生产数据**，
也不碰共享的 options / products（它们同样落在临时 schema 里）。
注意：Neon 的池化端点不接受 search_path 启动参数，所以这里把 "-pooler" 去掉走直连。

跑法（项目根目录）：
    ./.venv/Scripts/python.exe -m unittest discover -s tests -v
"""
import atexit
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

_base = os.environ.get("DATABASE_URL", "")
if not _base:
    raise RuntimeError("缺少 DATABASE_URL：测试要读 .env 里的连接串")

# Neon 池化端点不支持 startup 参数（实测报错 unsupported startup parameter in options）
DIRECT_URL = _base.replace("-pooler", "")
SCHEMA = "rivet_test_" + uuid.uuid4().hex[:8]

_sep = "&" if "?" in DIRECT_URL else "?"
os.environ["DATABASE_URL"] = f"{DIRECT_URL}{_sep}options=-c%20search_path%3D{SCHEMA}"
os.environ["SIGNUP_CODE"] = "testcode"  # 注册需要邀请码；测试里给一个固定值
os.environ["SESSION_SECRET"] = "test-secret"
os.environ["LLM_DAILY_LIMIT"] = "5"  # 每日生成限额：测试里给小值，好验证 429

import psycopg  # noqa: E402

# 必须先建好 schema，再 import main —— main 在 import 时就会 init_db() 建表
with psycopg.connect(DIRECT_URL, autocommit=True) as _conn:
    _conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')

from fastapi.testclient import TestClient  # noqa: E402

import main as app_module  # noqa: E402

app = app_module.app

# 管理员：第一个注册的人才会是管理员，所以整轮测试只建一个，各测试模块共用
ADMIN = {}


def client():
    """每个 client 自带一套 cookie —— 这就是「两个不同的人」。"""
    return TestClient(app)


def register(c, prefix):
    username = f"{prefix}_{uuid.uuid4().hex[:6]}"
    r = c.post(
        "/api/register",
        json={"username": username, "password": "pw123456", "code": "testcode"},
    )
    assert r.status_code == 200, r.text
    return r.json()["user"]


def ensure_admin():
    """懒建管理员（幂等）。必须在任何普通用户注册之前调用——第一个注册者才是管理员。"""
    if "user" not in ADMIN:
        c = client()
        ADMIN["client"] = c
        ADMIN["user"] = register(c, "admin")
    return ADMIN["user"]


PROVIDER = {}


def ensure_provider():
    """造一个可用的智能体渠道（幂等），返回它的 id。

    开源版不再有服务器默认 key：每一次生成都必须显式带一个渠道，
    所以测试也得先有一个。渠道是团队共享的（像产品目录），建一个就够所有用例用。
    """
    if "id" not in PROVIDER:
        ensure_admin()
        r = ADMIN["client"].post(
            "/api/providers",
            json={
                "name": "test_provider_" + uuid.uuid4().hex[:6],
                "base_url": "https://api.example.com/v1",
                "model": "test-model",
                "api_key": "sk-test-not-real",
            },
        )
        assert r.status_code == 200, r.text
        PROVIDER["id"] = r.json()["id"]
    return PROVIDER["id"]


TEST_URL = f"{DIRECT_URL}{_sep}options=-c%20search_path%3D{SCHEMA}"


def sql(statement, params=()):
    """直接在测试库里跑一条 SQL。

    用来造「历史数据」——比如把某条客户的 created_at 往前挪几天，
    好让「未联系天数」这类按时间算的功能有东西可测。
    UPDATE 这类没有结果集的语句返回空列表（psycopg 对无结果集调 fetchall 会抛错）。
    """
    with psycopg.connect(TEST_URL, autocommit=True) as conn:
        cur = conn.execute(statement, params)
        return cur.fetchall() if cur.description else []


def cleanup():
    """进程退出时收摊：先关池，再删 schema。"""
    try:
        app_module.db.pool.close()
    except Exception:  # noqa: BLE001 - 清理阶段不该再抛
        pass
    try:
        with psycopg.connect(DIRECT_URL, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
    except Exception:  # noqa: BLE001
        pass


atexit.register(cleanup)
