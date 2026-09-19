"""建表 DDL（架构文档 §30~§36）。所有表 IF NOT EXISTS，可重复执行。

MIGRATIONS 是已有库的增量变更，通过 schema_migrations 版本表每个只执行一次；
Database.init() 不会在正常重启时重放任何迁移，更不会执行破坏性清理。
"""

DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name       TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    partner_id  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stars (
    id            TEXT PRIMARY KEY,
    author_id     TEXT NOT NULL REFERENCES users(id),
    recipient_id  TEXT NOT NULL REFERENCES users(id),
    content       TEXT NOT NULL,
    mood_type     TEXT,
    mood_text     TEXT,
    note          TEXT,
    visibility    TEXT NOT NULL CHECK (visibility IN ('visible','hidden')),
    state         TEXT NOT NULL CHECK (state IN ('CREATED','SHARED','SEALED',
                     'ALLOCATED_RANDOMLY','OFFERED','DELIVERED','SESSION_LOCKED','OPENED')),
    written_at    TEXT NOT NULL,
    shared_at     TEXT,
    opened_at     TEXT,
    opened_by     TEXT,
    first_view_at TEXT,
    first_view_by TEXT,
    open_mode     TEXT CHECK (open_mode IN ('visible_from_start','request_random',
                     'author_offer','anniversary','special_day')),
    shared_origin TEXT CHECK (shared_origin IN ('visible_from_start','revealed_by_request',
                     'revealed_by_offer','revealed_by_anniversary','revealed_by_special_day')),
    cycle_id      TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS star_requests (
    id                TEXT PRIMARY KEY,
    requester_id      TEXT NOT NULL REFERENCES users(id),
    owner_id          TEXT NOT NULL REFERENCES users(id),
    status            TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','cancelled')),
    allocated_star_id TEXT,
    requested_at      TEXT NOT NULL,
    responded_at      TEXT,
    allocated_at      TEXT,
    opened_at         TEXT
);

CREATE TABLE IF NOT EXISTS star_offers (
    id           TEXT PRIMARY KEY,
    star_id      TEXT NOT NULL REFERENCES stars(id),
    sender_id    TEXT NOT NULL REFERENCES users(id),
    recipient_id TEXT NOT NULL REFERENCES users(id),
    status       TEXT NOT NULL CHECK (status IN ('offered','delivered','opened','completed')),
    offered_at   TEXT NOT NULL,
    opened_at    TEXT,
    message      TEXT
);

CREATE TABLE IF NOT EXISTS star_responses (
    id             TEXT PRIMARY KEY,
    star_id        TEXT NOT NULL REFERENCES stars(id),
    responder_id   TEXT NOT NULL REFERENCES users(id),
    type           TEXT NOT NULL CHECK (type IN ('text','audio')),
    text           TEXT,
    audio_url      TEXT,
    audio_duration REAL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS special_dates (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    month      INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    day        INTEGER NOT NULL CHECK (day BETWEEN 1 AND 31),
    year       INTEGER,
    type       TEXT NOT NULL CHECK (type IN ('anniversary','custom')),
    active     INTEGER NOT NULL DEFAULT 1,
    created_by TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS star_opening_sessions (
    id              TEXT PRIMARY KEY,
    type            TEXT NOT NULL CHECK (type IN ('anniversary','special_day')),
    special_date_id TEXT,
    initiated_by    TEXT NOT NULL REFERENCES users(id),
    confirmed_by    TEXT,
    status          TEXT NOT NULL CHECK (status IN ('waiting_confirmation','active','completed','cancelled')),
    started_at      TEXT,
    ended_at        TEXT,
    actor_a_count   INTEGER,
    actor_b_count   INTEGER,
    quota_cutoff_at TEXT
);

CREATE TABLE IF NOT EXISTS star_session_items (
    id         TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES star_opening_sessions(id),
    star_id    TEXT NOT NULL REFERENCES stars(id),
    author_id  TEXT NOT NULL,
    opened     INTEGER NOT NULL DEFAULT 0,
    opened_at  TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id           TEXT PRIMARY KEY,
    recipient_id TEXT NOT NULL REFERENCES users(id),
    type         TEXT NOT NULL,
    title        TEXT NOT NULL,
    body         TEXT,
    star_id      TEXT,
    is_read      INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    actor_id   TEXT,
    star_id    TEXT,
    detail     TEXT,
    created_at TEXT NOT NULL
);

-- F08：浏览器会话服务端登记表；logout 时按 sid 撤销，旧 cookie 立即失效。
CREATE TABLE IF NOT EXISTS auth_sessions (
    sid        TEXT PRIMARY KEY,
    actor_id   TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_ts INTEGER NOT NULL,
    revoked_at TEXT
);

-- F10：幂等操作结果缓存；(actor_id, operation_id) 唯一，重试返回同一结果。
CREATE TABLE IF NOT EXISTS idempotency_keys (
    actor_id     TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    endpoint     TEXT NOT NULL,
    result_json  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (actor_id, operation_id)
);

-- 查看足迹（第二轮修订复审后口径）：只保留"每人累计次数"，不保存逐次查看
-- 时间——第一次真正查看的时间与人在 stars.first_view_at/first_view_by（只写一次）。
-- star_views 是旧版逐行表：作为一次性迁移源，回填完成后由 purge 迁移清空，
-- 之后停止写入，仅留作空 tombstone。
CREATE TABLE IF NOT EXISTS star_view_counts (
    star_id    TEXT NOT NULL REFERENCES stars(id),
    viewer_id  TEXT NOT NULL REFERENCES users(id),
    view_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (star_id, viewer_id)
);

CREATE TABLE IF NOT EXISTS star_views (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    star_id    TEXT NOT NULL REFERENCES stars(id),
    viewer_id  TEXT NOT NULL REFERENCES users(id),
    viewed_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_star_views_star ON star_views(star_id, viewer_id);

CREATE INDEX IF NOT EXISTS idx_stars_author_state    ON stars(author_id, state);
CREATE INDEX IF NOT EXISTS idx_stars_shared          ON stars(state, shared_at);
CREATE INDEX IF NOT EXISTS idx_requests_owner_status ON star_requests(owner_id, status);
CREATE INDEX IF NOT EXISTS idx_offers_recipient      ON star_offers(recipient_id, status);
CREATE INDEX IF NOT EXISTS idx_items_session         ON star_session_items(session_id, opened);
CREATE INDEX IF NOT EXISTS idx_notifications_user    ON notifications(recipient_id, is_read);
"""

# 已有库的增量迁移：每个名字通过 schema_migrations 只执行一次。
# 早期版本曾经每次启动都重放这里的 SQL，其中两条破坏性清理（unlock_session_stars /
# close_legacy_sessions）会把 waiting/active session 在正常重启时强制结束——
# 这是本轮修复的 P0 问题。它们已退休：第一次初始化版本表时登记为"已处理，
# 不再自动运行"；今后确需清理旧脏数据时走显式维护脚本，不挂在 app startup。
MIGRATIONS = [
    ("stars_note", "ALTER TABLE stars ADD COLUMN note TEXT"),
    ("offers_message", "ALTER TABLE star_offers ADD COLUMN message TEXT"),
    ("stars_first_view_at", "ALTER TABLE stars ADD COLUMN first_view_at TEXT"),
    ("stars_first_view_by", "ALTER TABLE stars ADD COLUMN first_view_by TEXT"),
    ("sessions_quota_cutoff_at",
     "ALTER TABLE star_opening_sessions ADD COLUMN quota_cutoff_at TEXT"),
    # 旧基线数据修复：hidden+SHARED 但 shared_at 为空的星会让纪念日 0 点
    # as-of 额度误判"从未离开私人瓶"（shared_at IS NULL 分支），凭空加大额度。
    # 旧代码里这类星都是经 open 流程进公共池的，shared_at 应回填为 opened_at。
    ("backfill_legacy_shared_at",
     "UPDATE stars SET shared_at = opened_at "
     "WHERE state = 'SHARED' AND visibility = 'hidden' "
     "AND shared_at IS NULL AND opened_at IS NOT NULL"),
    # 一次性数据迁移（幂等写法，版本表保证只跑一次）：
    # 旧版逐行 star_views 收敛为聚合表（只留每人次数，不再留逐次时间）
    ("backfill_star_view_counts",
     "INSERT OR REPLACE INTO star_view_counts (star_id, viewer_id, view_count) "
     "SELECT star_id, viewer_id, COUNT(*) FROM star_views "
     "GROUP BY star_id, viewer_id"),
    # 已被看过的存量星：从旧表最早一条回填首次查看时间/人，
    # 避免"升级后的下一次"被误当成历史第一次
    ("backfill_first_view",
     "UPDATE stars SET "
     "first_view_at = (SELECT MIN(viewed_at) FROM star_views WHERE star_views.star_id = stars.id), "
     "first_view_by = (SELECT viewer_id FROM star_views WHERE star_views.star_id = stars.id "
     "ORDER BY viewed_at, rowid LIMIT 1) "
     "WHERE first_view_at IS NULL "
     "AND EXISTS (SELECT 1 FROM star_views WHERE star_views.star_id = stars.id)"),
    # 回填完成后清空旧逐次时间数据：聚合与 first_view 已各得其所，
    # 逐次查看时间不再保留（表本身留作空 tombstone，不再写入）。
    ("purge_legacy_star_views", "DELETE FROM star_views"),
]

# 退休的迁移名：不再自动执行，只登记进版本表防止任何路径重放。
RETIRED_MIGRATIONS = ("unlock_session_stars", "close_legacy_sessions")
