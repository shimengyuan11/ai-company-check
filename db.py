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
import json
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


# 预置标签：第一次打开话术库就有东西可选。
# 不给默认值的话，面对一张空标签表没人知道怎么起手；给太多又会「长草」，所以只铺常用项。
DEFAULT_TAGS = {
    "来源": ("FB", "邮件", "展会", "阿里国际站"),
    "类型": ("个人买家", "代理商", "批发商", "终端客户"),
    "场景": ("首次触达", "已读不回激活", "逼单", "嫌贵", "比价", "老客户复购"),
}


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
                phone_norm   TEXT,
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
        # 选项库：国家 / 来源 这类字段，谁填过一次，以后大家就能直接下拉选。
        # 故意**不分归属**——它不是客户隐私，是团队共同积累的"词典"，
        # 各存各的就没意义了（每个人都要重新攒一遍）。
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS options (
                id         SERIAL PRIMARY KEY,
                kind       TEXT NOT NULL,
                value      TEXT NOT NULL,
                created_at TEXT,
                UNIQUE (kind, value)
            )
            """
        )

        # 附件：PI / 报价单这类文件，业务自己传上来挂在客户名下。
        # 直接存进 Postgres（bytea）——少一个外部依赖，免费层 0.5GB 够放上千份 PDF。
        # 以后不够用了再换对象存储，那时只需改这两个函数。
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS attachments (
                id           SERIAL PRIMARY KEY,
                customer_id  INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                user_id      INTEGER NOT NULL REFERENCES users(id),
                filename     TEXT NOT NULL,
                content_type TEXT,
                size         INTEGER NOT NULL,
                data         BYTEA NOT NULL,
                created_at   TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_attachments_customer ON attachments(customer_id)"
        )

        # 产品目录：也是**团队共享**的（公司卖的是同一批产品）。
        # attrs 是「自定义属性」：材质 / MOQ / 交期 这类每个产品不一样的字段，
        # 存 JSON 文本，前端按键值对渲染。固定列永远追不上业务的口头描述。
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id         SERIAL PRIMARY KEY,
                name       TEXT NOT NULL,
                spec       TEXT,
                price      TEXT,
                note       TEXT,
                attrs      TEXT,
                created_at TEXT
            )
            """
        )

        # 提醒（日历看板）：给某个客户定一个「该联系」的日子。
        # **个人日程**——查询按 user_id 过滤，同事看不到你的安排。
        # 客户删了，提醒跟着走（ON DELETE CASCADE）。
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id          SERIAL PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                user_id     INTEGER NOT NULL REFERENCES users(id),
                due_date    DATE NOT NULL,
                note        TEXT,
                done        BOOLEAN NOT NULL DEFAULT FALSE,
                created_at  TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_date)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reminders_customer ON reminders(customer_id)"
        )

        # —— 第 3 期：话术库 ——
        # 模板：**团队共享**（谁都能用、都能改），owner_id 只记录谁建的。
        # body 里支持 {客户名} {产品} {价格} 这类占位符——没有占位符模板就不能复用。
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS templates (
                id         SERIAL PRIMARY KEY,
                owner_id   INTEGER REFERENCES users(id),
                title      TEXT NOT NULL,
                body       TEXT NOT NULL,
                use_count  INTEGER NOT NULL DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )

        # 标签：**三组**（来源 / 类型 / 场景），不是一棵分类树。
        # 理由见 docs/v2-needs-triage.md 第六节：三个维度会交叉（FB 来的也可能是代理商），
        # 做成树必然卡在「这条该挂哪个枝」。
        # is_active：停用而不是物理删——删了会把老模板上的标签一起带走。
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tags (
                id         SERIAL PRIMARY KEY,
                grp        TEXT NOT NULL,
                name       TEXT NOT NULL,
                is_active  BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TEXT,
                UNIQUE (grp, name)
            )
            """
        )

        # 模板 ↔ 标签：多对多。这就是 triage 里说的「练数据库的好例子」。
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS template_tags (
                template_id INTEGER NOT NULL REFERENCES templates(id) ON DELETE CASCADE,
                tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (template_id, tag_id)
            )
            """
        )

        # 生成记录：挂在客户下，记「基于哪个模板 + 当时的要求 + 用了哪个模型」。
        # 不存下来就等于「用过就没了」，又回到扒聊天记录。
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS generations (
                id          SERIAL PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                template_id INTEGER REFERENCES templates(id) ON DELETE SET NULL,
                user_id     INTEGER NOT NULL REFERENCES users(id),
                request     TEXT,
                output      TEXT NOT NULL,
                model       TEXT,
                created_at  TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_generations_customer ON generations(customer_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_template_tags_tag ON template_tags(tag_id)"
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
        # 查重用的归一化号码（只留数字）。+86 138 0000 1234 与 13800001234 是同一个号，
        # 直接比原字符串会漏掉——所以另存一份"只留数字"的副本专门用来比对。
        conn.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS phone_norm TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_customers_phone_norm ON customers(phone_norm)"
        )

        # —— 老库升级：产品自定义属性（材质 / MOQ / 交期…），存 JSON 文本 ——
        conn.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS attrs TEXT")

        # —— 预置默认标签（幂等：靠 UNIQUE(grp, name) 兜住，重复启动不会重复插）——
        for grp, names in DEFAULT_TAGS.items():
            for name in names:
                conn.execute(
                    "INSERT INTO tags (grp, name, is_active, created_at)"
                    " VALUES (%s, %s, TRUE, %s)"
                    " ON CONFLICT (grp, name) DO NOTHING",
                    (grp, name, _now()),
                )


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


def norm_phone(phone):
    """号码归一化：只留 0-9。

    这样 `+86 138 0000 1234`、`138-0000-1234`、`13800001234` 在比对时算同一个号。
    （国家码的有无仍算不同——那是录入习惯问题，不是这个函数能解决的。）
    """
    return "".join(ch for ch in (phone or "") if ch in "0123456789")


def phone_taken(phone_norm):
    """这个号码是不是已经被**任何人**建档了（跨所有业务员）。

    只回答"是/否"，**绝不告诉调用方是谁的**——这样既防撞单，
    又不破坏"各管各的"（你知道冲突存在，但看不到对方的客户）。

    判定用两条：① 归一化后完全相同；② **尾号 9 位相同**。
    为什么要第②条：同一个号，有人写 `+86 138 0000 1234`、有人写 `13800001234`，
    归一化后是 `8613800001234` 和 `13800001234`——国家码在前面，**尾号才是唯一的那段**。
    """
    if not phone_norm:
        return False
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT 1 AS x FROM customers
            WHERE phone_norm = %(p)s
               OR (length(phone_norm) >= 9 AND length(%(p)s) >= 9
                   AND right(phone_norm, 9) = right(%(p)s, 9))
            LIMIT 1
            """,
            {"p": phone_norm},
        ).fetchone()
    return row is not None


def create_customer(owner_id, contact_name, phone, country, company="", source="", note=""):
    """建档。姓名 / 号码 / 国家 必填（由接口层强制），公司名选填。

    顺手把这次填的 国家 / 来源 记进选项库——下次（谁）建档就能直接下拉选。
    """
    with pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO customers"
            " (owner_id, contact_name, phone, phone_norm, country, company, source, note,"
            "  created_at, updated_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                owner_id,
                contact_name,
                phone,
                norm_phone(phone),
                country,
                company,
                source,
                note,
                _now(),
                _now(),
            ),
        ).fetchone()
        for kind, value in (("country", country), ("source", source)):
            value = (value or "").strip()
            if value:
                conn.execute(
                    "INSERT INTO options (kind, value, created_at) VALUES (%s, %s, %s)"
                    " ON CONFLICT (kind, value) DO NOTHING",  # 已经有了就什么都不做
                    (kind, value, _now()),
                )
    return row["id"]


def list_options(kind):
    """某个分类下攒下来的所有选项（团队共享）。"""
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT value FROM options WHERE kind = %s ORDER BY id DESC", (kind,)
        ).fetchall()
    return [r["value"] for r in rows]


def list_customers(owner_id, is_admin=False, q="", stage="", sort="recent"):
    """客户列表。普通人只看自己的；管理员看全部。

    每条带上「有多少条跟进」和「最后一次跟进是什么时候」——列表页一眼能看出谁很久没跟了。

    q 非空时按 姓名 / 公司 / 号码 模糊匹配。号码要**同时比原样和归一化两种**——
    否则你搜 `13800001234`，就找不到人家录成 `+86 138 0000 1234` 的那条。

    stage：空 = 全部；`none` = 还没标阶段的；其余按阶段精确匹配。
    sort：`recent`（默认，最近建档在前）/ `stale`（最久没联系在前，用来挑今天该跟谁）。
    """
    q = (q or "").strip()
    stage = (stage or "").strip()
    params = [is_admin, owner_id]
    # 括号不能省：OR 的优先级低于 AND，不加括号时管理员会绕过下面的 stage / 搜索条件
    sql = """
        SELECT c.*,
               (SELECT count(*) FROM followups f WHERE f.customer_id = c.id) AS followup_count,
               (SELECT max(f.created_at) FROM followups f WHERE f.customer_id = c.id)
                   AS last_followup_at
        FROM customers c
        WHERE (%s OR c.owner_id = %s)
    """
    if q:
        like = f"%{q}%"
        digits = norm_phone(q)
        sql += """
          AND (c.contact_name ILIKE %s
               OR c.company ILIKE %s
               OR c.phone ILIKE %s
               OR (%s <> '' AND c.phone_norm LIKE %s))
        """
        # 最后那个 %s <> '' 是防线：如果搜的词里一个数字都没有，别让它匹配到所有号码
        params += [like, like, like, digits, f"%{digits}%"]
    if stage == "none":
        sql += " AND (c.stage IS NULL OR c.stage = '')"
    elif stage:
        sql += " AND c.stage = %s"
        params.append(stage)
    if sort == "stale":
        # created_at / last_followup_at 都是同格式的 ISO(UTC) 字符串，直接比字符串就等价于比时间
        sql += """
          ORDER BY coalesce(
              (SELECT max(f.created_at) FROM followups f WHERE f.customer_id = c.id),
              c.created_at
          ) ASC
        """
    else:
        sql += " ORDER BY c.id DESC"
    with pool.connection() as conn:
        rows = conn.execute(sql, params).fetchall()
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
    allowed = (
        "contact_name", "phone", "phone_norm", "country",
        "company", "source", "note", "stage", "high_intent",
    )
    if "phone" in fields:  # 号码改了，归一化副本要跟着改，否则查重会失准
        fields["phone_norm"] = norm_phone(fields["phone"])
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


# ===================== V2：附件 =====================


def add_attachment(customer_id, user_id, filename, content_type, data):
    with pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO attachments"
            " (customer_id, user_id, filename, content_type, size, data, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (customer_id, user_id, filename, content_type, len(data), data, _now()),
        ).fetchone()
    return row["id"]


def list_attachments(customer_id):
    """附件清单——**故意不取 data 字段**。

    列表页只要文件名和大小；把二进制一起捞出来，客户一多就会又慢又占内存。
    """
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT a.id, a.filename, a.content_type, a.size, a.created_at,
                   u.display_name, u.username
            FROM attachments a JOIN users u ON u.id = a.user_id
            WHERE a.customer_id = %s
            ORDER BY a.id DESC
            """,
            (customer_id,),
        ).fetchall()


def get_attachment(att_id):
    """取单个附件（含内容）。只有下载那一下才用。"""
    with pool.connection() as conn:
        return conn.execute(
            "SELECT * FROM attachments WHERE id = %s", (att_id,)
        ).fetchone()


def delete_attachment(att_id, customer_id):
    """删附件。customer_id 也进条件——免得用一个 id 删掉别处的文件。"""
    with pool.connection() as conn:
        cur = conn.execute(
            "DELETE FROM attachments WHERE id = %s AND customer_id = %s",
            (att_id, customer_id),
        )
        return cur.rowcount > 0


# ===================== V2：产品目录（团队共享） =====================


def _attrs_dict(raw):
    """库里存的是 JSON 文本；读坏了的当空 dict，别让一条脏数据把整页打挂。"""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _attrs_text(attrs):
    return json.dumps(attrs or {}, ensure_ascii=False)


def _product_row(row):
    """产品行 → 给前端的 dict（attrs 从 JSON 文本还原成对象）。"""
    if row is None:
        return None
    out = dict(row)
    out["attrs"] = _attrs_dict(row.get("attrs"))
    return out


def create_product(name, spec="", price="", note="", attrs=None):
    with pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO products (name, spec, price, note, attrs, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (name, spec, price, note, _attrs_text(attrs), _now()),
        ).fetchone()
    return row["id"]


def update_product(product_id, **fields):
    """改产品。attrs 传进来就整体替换（前端每次提交完整的一组键值对）。"""
    sets, values = [], []
    for key in ("name", "spec", "price", "note"):
        if key in fields:
            sets.append(f"{key} = %s")
            values.append(fields[key])
    if "attrs" in fields:
        sets.append("attrs = %s")
        values.append(_attrs_text(fields["attrs"]))
    if not sets:
        return get_product(product_id)
    values.append(product_id)
    with pool.connection() as conn:
        row = conn.execute(
            f"UPDATE products SET {', '.join(sets)} WHERE id = %s RETURNING *", values
        ).fetchone()
    return _product_row(row)


def get_product(product_id):
    with pool.connection() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = %s", (product_id,)).fetchone()
    return _product_row(row)


def list_products():
    with pool.connection() as conn:
        rows = conn.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    return [_product_row(r) for r in rows]


def delete_product(product_id):
    with pool.connection() as conn:
        cur = conn.execute("DELETE FROM products WHERE id = %s", (product_id,))
        return cur.rowcount > 0


# ===================== 提醒（日历看板，个人日程） =====================


def create_reminder(customer_id, user_id, due_date, note=""):
    with pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO reminders (customer_id, user_id, due_date, note, done, created_at)"
            " VALUES (%s, %s, %s, %s, FALSE, %s) RETURNING id",
            (customer_id, user_id, due_date, note, _now()),
        ).fetchone()
    return row["id"]


def list_reminders(user_id, date_from, date_to):
    """某个日期区间里「我的」提醒（连带客户名，日历上直接能看出是谁）。"""
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT r.id, r.customer_id, r.due_date, r.note, r.done, r.created_at,
                   c.contact_name, c.company, c.country, c.stage
            FROM reminders r JOIN customers c ON c.id = r.customer_id
            WHERE r.user_id = %s AND r.due_date BETWEEN %s AND %s
            ORDER BY r.due_date, r.id
            """,
            (user_id, date_from, date_to),
        ).fetchall()


def set_reminder_done(reminder_id, user_id, done):
    with pool.connection() as conn:
        cur = conn.execute(
            "UPDATE reminders SET done = %s WHERE id = %s AND user_id = %s",
            (bool(done), reminder_id, user_id),
        )
        return cur.rowcount > 0


def delete_reminder(reminder_id, user_id):
    with pool.connection() as conn:
        cur = conn.execute(
            "DELETE FROM reminders WHERE id = %s AND user_id = %s", (reminder_id, user_id)
        )
        return cur.rowcount > 0


def count_pending_reminders(user_id, today):
    """红点数字：今天及以前、还没做完的条数。

    为什么要带「以前」：昨天该联系却忘了，今天更该提醒——漏掉比迟到更糟。
    """
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT count(*) AS n FROM reminders"
            " WHERE user_id = %s AND done = FALSE AND due_date <= %s",
            (user_id, today),
        ).fetchone()
    return row["n"]


# ===================== 第 3 期：话术库（团队共享） =====================
# 模板和标签都**不分归属**——话术是团队资产（和产品目录一个性质），
# 不像客户那样各管各的。归属只用来记「谁建的」。
# 只有一个地方要权限：生成记录挂在客户下，谁能看这个客户，谁才能看它的生成记录。


def list_tags(include_inactive=False):
    """标签清单，按组给前端分组用。默认只给在用的（停用的从下拉里消失）。

    每条带上 use_count（有几个模板挂着它）——删/合并之前得让人知道影响面。
    """
    sql = """
        SELECT t.*,
               (SELECT count(*) FROM template_tags tt WHERE tt.tag_id = t.id) AS use_count
        FROM tags t
    """
    if not include_inactive:
        sql += " WHERE t.is_active = TRUE"
    sql += " ORDER BY t.grp, t.name"
    with pool.connection() as conn:
        return conn.execute(sql).fetchall()


def create_tag(grp, name):
    """建标签；同组同名已存在就复用它（顺手重新启用）。

    用 ON CONFLICT 而不是「先查再插」：两个请求同时建同一个标签时不会撞车。
    """
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO tags (grp, name, is_active, created_at)
            VALUES (%s, %s, TRUE, %s)
            ON CONFLICT (grp, name)
            DO UPDATE SET is_active = TRUE
            RETURNING id
            """,
            (grp, name, _now()),
        ).fetchone()
    return row["id"]


def set_tag_active(tag_id, active):
    """停用 / 重新启用。不物理删——删了会把老模板上的标签一起抽走。"""
    with pool.connection() as conn:
        cur = conn.execute("UPDATE tags SET is_active = %s WHERE id = %s", (bool(active), tag_id))
        return cur.rowcount > 0


def get_tag(tag_id):
    with pool.connection() as conn:
        return conn.execute("SELECT * FROM tags WHERE id = %s", (tag_id,)).fetchone()


def delete_tag(tag_id):
    """真删一个标签（模板上的关联由外键 CASCADE 一起清掉）。

    什么时候该用它、什么时候该停用：**删是「这条标签本身就是错的 / 重复的」**（比如多打了一个），
    停用是「暂时不想看到但仍要留着历史」。合并则是重复标签的正解。
    """
    with pool.connection() as conn:
        cur = conn.execute("DELETE FROM tags WHERE id = %s", (tag_id,))
        return cur.rowcount > 0


def merge_tag(src_id, dst_id):
    """把 src 的模板关联整体迁到 dst，再把 src 删掉。

    解决的是「FB」和「facebook」并存这种乱：迁完旧标签消失，模板上的标记变成新的那个。
    已存在的关联靠 ON CONFLICT DO NOTHING 跳过，不会撞主键。
    """
    if src_id == dst_id:
        return False
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO template_tags (template_id, tag_id)
            SELECT template_id, %s FROM template_tags WHERE tag_id = %s
            ON CONFLICT DO NOTHING
            """,
            (dst_id, src_id),
        )
        cur = conn.execute("DELETE FROM tags WHERE id = %s", (src_id,))
        return cur.rowcount > 0


def _tags_by_template(conn, template_ids):
    """一次把一批模板的标签捞回来，避免每个模板一条查询（N+1）。"""
    ids = list(template_ids)
    if not ids:
        return {}
    rows = conn.execute(
        """
        SELECT tt.template_id, t.id, t.grp, t.name, t.is_active
        FROM template_tags tt JOIN tags t ON t.id = tt.tag_id
        WHERE tt.template_id = ANY(%s)
        ORDER BY t.grp, t.name
        """,
        (ids,),
    ).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["template_id"], []).append(
            {"id": r["id"], "grp": r["grp"], "name": r["name"], "is_active": r["is_active"]}
        )
    return out


def create_template(owner_id, title, body, tag_ids=None):
    with pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO templates (owner_id, title, body, use_count, created_at, updated_at)"
            " VALUES (%s, %s, %s, 0, %s, %s) RETURNING id",
            (owner_id, title, body, _now(), _now()),
        ).fetchone()
        tid = row["id"]
        for tag_id in tag_ids or []:
            conn.execute(
                "INSERT INTO template_tags (template_id, tag_id) VALUES (%s, %s)"
                " ON CONFLICT DO NOTHING",
                (tid, tag_id),
            )
    return tid


def list_templates(q="", tag_id=None):
    """模板列表。用得多的排前面——常用的一眼能看到。"""
    q = (q or "").strip()
    conds, params = [], []
    if q:
        conds.append("(t.title ILIKE %s OR t.body ILIKE %s)")
        like = f"%{q}%"
        params += [like, like]
    if tag_id:
        conds.append(
            "EXISTS (SELECT 1 FROM template_tags tt"
            " WHERE tt.template_id = t.id AND tt.tag_id = %s)"
        )
        params.append(tag_id)
    sql = "SELECT t.* FROM templates t"
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY t.use_count DESC, t.id DESC"
    with pool.connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        tags = _tags_by_template(conn, [r["id"] for r in rows])
    for r in rows:
        r["tags"] = tags.get(r["id"], [])
    return rows


def get_template(template_id):
    with pool.connection() as conn:
        row = conn.execute("SELECT * FROM templates WHERE id = %s", (template_id,)).fetchone()
        if row is not None:
            row["tags"] = _tags_by_template(conn, [template_id]).get(template_id, [])
    return row


def update_template(template_id, title=None, body=None, tag_ids=None):
    """改模板。只改传进来的字段；tag_ids 传了才重写标签关联（传空列表 = 清空标签）。"""
    sets, values = [], []
    if title is not None:
        sets.append("title = %s")
        values.append(title)
    if body is not None:
        sets.append("body = %s")
        values.append(body)
    with pool.connection() as conn:
        if sets:
            sets.append("updated_at = %s")
            values.append(_now())
            values.append(template_id)
            cur = conn.execute(
                f"UPDATE templates SET {', '.join(sets)} WHERE id = %s", values
            )
            if cur.rowcount == 0:
                return False
        if tag_ids is not None:
            conn.execute("DELETE FROM template_tags WHERE template_id = %s", (template_id,))
            for tag_id in tag_ids:
                conn.execute(
                    "INSERT INTO template_tags (template_id, tag_id) VALUES (%s, %s)"
                    " ON CONFLICT DO NOTHING",
                    (template_id, tag_id),
                )
    return True


def delete_template(template_id):
    with pool.connection() as conn:
        cur = conn.execute("DELETE FROM templates WHERE id = %s", (template_id,))
        return cur.rowcount > 0


def bump_template_use(template_id):
    """用一次就 +1。用得多的一眼能看到——这也是判断该留该删的依据。"""
    with pool.connection() as conn:
        conn.execute("UPDATE templates SET use_count = use_count + 1 WHERE id = %s", (template_id,))


def add_generation(customer_id, template_id, user_id, request, output, model):
    with pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO generations"
            " (customer_id, template_id, user_id, request, output, model, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (customer_id, template_id, user_id, request, output, model, _now()),
        ).fetchone()
    return row["id"]


def list_generations(customer_id):
    """这个客户生成过什么。权限由调用方先过 get_customer 把关。"""
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT g.id, g.customer_id, g.template_id, g.request, g.output, g.model,
                   g.created_at, t.title AS template_title,
                   u.display_name, u.username
            FROM generations g
            LEFT JOIN templates t ON t.id = g.template_id
            JOIN users u ON u.id = g.user_id
            WHERE g.customer_id = %s
            ORDER BY g.id DESC
            """,
            (customer_id,),
        ).fetchall()
