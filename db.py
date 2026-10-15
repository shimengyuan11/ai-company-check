"""数据库相关的全部操作都放这里：建表、存一条背调、读历史列表。

用 Postgres，连接串从环境变量 DATABASE_URL 读（本地放 .env，线上放平台环境变量）。
数据存在托管库里，重新部署、休眠都不会再丢——这是从 SQLite 单文件换过来要解决的事。

从 SQLite 迁过来的三处差异（都在这份文件里）：
1. 占位符：SQLite 用 `?`，Postgres 用 `%s`
2. 自增主键：SQLite 的 `AUTOINCREMENT` → Postgres 的 `SERIAL`
3. 拿新插入的 id：SQLite 用 `cur.lastrowid` → Postgres 用 `RETURNING id`
"""
import os
from datetime import datetime

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError(
        "缺少配置：DATABASE_URL（Postgres 连接串；本地放 .env，线上放平台环境变量）"
    )


def get_conn():
    """新开一个连接。`with` 退出时自动提交并关闭（失败则回滚）。
    row_factory=dict_row 让查询结果能按列名取值，例如 row["company"]。"""
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db():
    """建表。IF NOT EXISTS 表示：表已经存在就什么都不做（所以可以重复调用）。"""
    with get_conn() as conn:
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
    """存一条背调，返回它的 id。"""
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO analyses (company, extra, analysis, model, created_at)"
            " VALUES (%s, %s, %s, %s, %s)"
            " RETURNING id",
            (company, extra, analysis, model, datetime.now().isoformat(timespec="seconds")),
        ).fetchone()
    return row["id"]


def list_history(limit=10):
    """读最近 limit 条（不含正文，列表页用不上）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, company, created_at FROM analyses ORDER BY id DESC LIMIT %s",
            (limit,),
        ).fetchall()
    return rows


def get_analysis(record_id):
    """读某一条的完整内容。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM analyses WHERE id = %s", (record_id,)
        ).fetchone()
    return row
