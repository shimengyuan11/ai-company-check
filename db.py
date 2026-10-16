"""数据库相关的全部操作都放这里：建表、存、读。

用 Postgres，连接串从环境变量 DATABASE_URL 读（本地放 .env，线上放平台环境变量）。
数据存在托管库里，重新部署、休眠都不会再丢。

两条踩出来的经验（换远程库之后才暴露的）：
1. 时间戳一律存 UTC。以前用 datetime.now() 存的是「跑代码那台机器的本地时间」，
   本机（UTC+8）和 Render（UTC）写进同一张表就差 8 小时。转本地时间是前端的事。
2. 连接要复用。连一次远程库要 ~2 秒（TLS 握手 + 认证），每个请求都新建连接会让页面
   每次加载卡 2~3 秒。所以用连接池。

【权限的做法】V2 的客户是「各管各的」——所以**权限过滤写在 SQL 里**，不写在路由里。
   取客户一律走 get_customer(customer_id, viewer)，它在 WHERE 里就带上了归属条件。
   好处：想「忘了加权限」都很难，因为这里根本没有一个「不带 viewer 的取客户函数」。
"""
import atexit
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError(
        "缺少配置：DATABASE_URL（Postgres 连接串；本地放 .env，线上放平台环境变量）"
    )

# 连接池：进程启动时开好连接，之后每次操作「借一条、用完还回去」。
# check=check_connection 会在借出前确认连接还活着（Neon 闲置时可能断开空闲连接）。
pool = ConnectionPool(
    DATABASE_URL,
    min_size=1,
    max_size=4,
    kwargs={"row_factory": dict_row},  # 让查询结果能按列名取值，例如 row["company"]
    check=ConnectionPool.check_connection,
    max_idle=300.0,  # 空闲超 5 分钟的连接放掉，别一直占着 Neon 的计算额度
    open=True,
)
atexit.register(pool.close)  # 进程退出时正常收掉连接池，避免关停时报错噪音


def _now():
    """统一的时间戳：UTC + 带时区后缀，谁也歧义不了。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db():
    """建表。IF NOT EXISTS：表已存在就什么都不做（所以可以重复调用）。"""
    with pool.connection() as conn:
        # —— V1：一次背调 = 一行 ——
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS analyses (
                id          SERIAL PRIMARY KEY,
                company     TEXT NOT NULL,
                extra       TEXT,
                analysis    TEXT NOT NULL,
                model       TEXT,
                created_at  TEXT
            )
            """
        )

        # —— V2：用户 / 客户 / 跟进 ——
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            SERIAL PRIMARY KEY,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name  TEXT,
                is_admin      BOOLEAN NOT NULL DEFAULT FALSE,
                is_active     BOOLEAN NOT NULL DEFAULT TRUE,
                created_at    TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id           SERIAL PRIMARY KEY,
                owner_id     INTEGER NOT NULL REFERENCES users(id),
                contact_name TEXT,
                phone        TEXT,
                country      TEXT,
                company      TEXT,
                source       TEXT,
                note         TEXT,
                stage        TEXT,
                high_intent  BOOLEAN NOT NULL DEFAULT FALSE,
                created_at   TEXT,
                updated_at   TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS followups (
                id          SERIAL PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                user_id     INTEGER NOT NULL REFERENCES users(id),
                content     TEXT NOT NULL,
                kind        TEXT DEFAULT '跟进',
                created_at  TEXT
            )
            """
        )
        # 这两条是最常用的查询条件，给它们建索引
        conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_owner ON customers(owner_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_followups_customer ON followups(customer_id)")

        # —— 老库升级（幂等，可以反复跑）——
        # 建档字段从「公司名必填」改成「姓名 / 号码 / 国家 必填、公司选填」。
        # 这里只加列、放开 company 的非空约束；**必填在接口层强制**——
        # 因为库里已有的老记录没有姓名和号码，硬加 NOT NULL 会让升级直接失败。
        conn.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS contact_name TEXT")
        conn.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS phone TEXT")
        conn.execute("ALTER TABLE customers ALTER COLUMN company DROP NOT NULL")


# ===================== V1：背调 =====================


def save_analysis(company, extra, analysis, model):
    """存一条背调，返回它的 id。"""
    with pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO analyses (company, extra, analysis, model, created_at)"
            " VALUES (%s, %s, %s, %s, %s)"
            " RETURNING id",
            (company, extra, analysis, model, _now()),
        ).fetchone()
    return row["id"]


def list_history(limit=10):
    """读最近 limit 条（不含正文，列表页用不上）。"""
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT id, company, created_at FROM analyses ORDER BY id DESC LIMIT %s",
            (limit,),
        ).fetchall()
    return rows


def get_analysis(record_id):
    """读某一条的完整内容。"""
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT * FROM analyses WHERE id = %s", (record_id,)
        ).fetchone()
    return row


# ===================== V2：用户 =====================


def count_users():
    with pool.connection() as conn:
        return conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"]


def create_user(username, password_hash, display_name="", is_admin=False):
    with pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO users (username, password_hash, display_name, is_admin, created_at)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (username, password_hash, display_name or username, is_admin, _now()),
        ).fetchone()
    return row["id"]


def get_user_by_username(username):
    with pool.connection() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username = %s", (username,)
        ).fetchone()


def get_user(user_id):
    with pool.connection() as conn:
        return conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()


def list_users():
    """给「移交客户」的下拉用——只取 id 和名字，绝不把 password_hash 带出去。"""
    with pool.connection() as conn:
        return conn.execute(
            "SELECT id, username, display_name, is_active FROM users ORDER BY id"
        ).fetchall()


# ===================== V2：客户 =====================


def create_customer(owner_id, contact_name, phone, country, company="", source="", note=""):
    """建档。姓名 / 号码 / 国家 必填（由接口层强制），公司名选填。"""
    with pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO customers"
            " (owner_id, contact_name, phone, country, company, source, note, created_at, updated_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (owner_id, contact_name, phone, country, company, source, note, _now(), _now()),
        ).fetchone()
    return row["id"]


def list_customers(owner_id, is_admin=False):
    """客户列表。普通人只看自己的；管理员看全部。

    每条带上「有多少条跟进」，列表页一眼能看出谁很久没跟了。
    """
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT c.*,
                   (SELECT count(*) FROM followups f WHERE f.customer_id = c.id) AS followup_count
            FROM customers c
            WHERE %s OR c.owner_id = %s
            ORDER BY c.id DESC
            """,
            (is_admin, owner_id),
        ).fetchall()
    return rows


def get_customer(customer_id, viewer_id, is_admin=False):
    """取一个客户。**权限过滤就在这条 SQL 里**——看不到就返回 None，和「不存在」同等待遇。"""
    with pool.connection() as conn:
        return conn.execute(
            "SELECT * FROM customers WHERE id = %s AND (%s OR owner_id = %s)",
            (customer_id, is_admin, viewer_id),
        ).fetchone()


def update_customer(customer_id, viewer_id, is_admin=False, **fields):
    """改客户资料。只允许改白名单里的字段，避免调用方随手拼 SQL。"""
    allowed = ("contact_name", "phone", "country", "company", "source", "note", "stage", "high_intent")
    sets, values = [], []
    for key in allowed:
        if key in fields:
            sets.append(f"{key} = %s")
            values.append(fields[key])
    if not sets:
        return False
    sets.append("updated_at = %s")
    values.append(_now())
    values += [customer_id, is_admin, viewer_id]
    with pool.connection() as conn:
        cur = conn.execute(
            f"UPDATE customers SET {', '.join(sets)}"
            " WHERE id = %s AND (%s OR owner_id = %s)",
            values,
        )
        return cur.rowcount > 0


def transfer_customer(customer_id, new_owner_id, viewer_id, is_admin=False):
    """把客户转给另一个业务——「有人离职」时用的就是它。"""
    with pool.connection() as conn:
        cur = conn.execute(
            "UPDATE customers SET owner_id = %s, updated_at = %s"
            " WHERE id = %s AND (%s OR owner_id = %s)",
            (new_owner_id, _now(), customer_id, is_admin, viewer_id),
        )
        return cur.rowcount > 0


def delete_customer(customer_id, viewer_id, is_admin=False):
    """删客户（连同它的跟进记录——外键上是 ON DELETE CASCADE）。

    权限条件同样写在 SQL 里：删别人的客户会命中 0 行，返回 False。
    """
    with pool.connection() as conn:
        cur = conn.execute(
            "DELETE FROM customers WHERE id = %s AND (%s OR owner_id = %s)",
            (customer_id, is_admin, viewer_id),
        )
        return cur.rowcount > 0


# ===================== V2：跟进 =====================


def add_followup(customer_id, user_id, content, kind="跟进"):
    """写一条跟进。只有 content 必填——这就是「写跟进 ≤10 秒」的来源。"""
    with pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO followups (customer_id, user_id, content, kind, created_at)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (customer_id, user_id, content, kind, _now()),
        ).fetchone()
    return row["id"]


def list_followups(customer_id):
    """某个客户的全部跟进，新的在上。带上「谁写的」。"""
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT f.*, u.display_name, u.username
            FROM followups f JOIN users u ON u.id = f.user_id
            WHERE f.customer_id = %s
            ORDER BY f.id DESC
            """,
            (customer_id,),
        ).fetchall()
