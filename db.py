"""数据库相关的全部操作都放这里：建表、存一条背调、读历史列表。

用 Postgres，连接串从环境变量 DATABASE_URL 读（本地放 .env，线上放平台环境变量）。
数据存在托管库里，重新部署、休眠都不会再丢。

换成远程库之后暴露出来的两条经验（都写在这里，免得以后忘）：
1. 时间戳一律存 UTC。以前用 datetime.now() 存的是「跑代码那台机器的本地时间」，
   本机（UTC+8）和 Render（UTC）写进同一张表就差 8 小时。转成本地时间是前端的事。
2. 连接要复用。连一次远程库要 ~2 秒（TLS 握手 + 认证），每个请求都新建连接会让页面
   每次加载卡 2~3 秒。改用连接池：进程里常备几条连接，用完还回去。

从 SQLite 迁过来的三处差异：
占位符 `?`→`%s`、自增 `AUTOINCREMENT`→`SERIAL`、取新 id `lastrowid`→`RETURNING id`
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


def init_db():
    """建表。IF NOT EXISTS 表示：表已经存在就什么都不做（所以可以重复调用）。"""
    with pool.connection() as conn:
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


def save_analysis(company, extra, analysis, model):
    """存一条背调，返回它的 id。created_at 存 UTC（前端负责转本地时间显示）。"""
    with pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO analyses (company, extra, analysis, model, created_at)"
            " VALUES (%s, %s, %s, %s, %s)"
            " RETURNING id",
            (
                company,
                extra,
                analysis,
                model,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
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
