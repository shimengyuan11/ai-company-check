"""数据库相关的全部操作都放这里：建表、存一条背调、读历史列表。"""
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "data.db"  # 数据库文件固定放在 db.py 旁边


def get_conn():
    """每次操作都新开一个连接，用完就关。简单、安全，适合单人用的小项目。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # 让查询结果能按列名取值，例如 row["company"]
    return conn


def init_db():
    """建表。IF NOT EXISTS 表示：表已经存在就什么都不做（所以可以重复调用）。"""
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS analyses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            company     TEXT NOT NULL,
            extra       TEXT,
            analysis    TEXT NOT NULL,
            model       TEXT,
            created_at  TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def save_analysis(company, extra, analysis, model):
    """存一条背调，返回它的 id。"""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO analyses (company, extra, analysis, model, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (company, extra, analysis, model, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def list_history(limit=10):
    """读最近 limit 条（不含正文，列表页用不上）。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, company, created_at FROM analyses ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_analysis(record_id):
    """读某一条的完整内容。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM analyses WHERE id = ?", (record_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None
