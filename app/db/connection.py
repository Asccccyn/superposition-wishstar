"""SQLite 连接封装。

每个事务使用独立连接；写事务用 BEGIN IMMEDIATE 抢写锁，
配合仓储层的条件 UPDATE（WHERE state = ...）实现并发保护（架构文档 §48）。

init() 通过 schema_migrations 版本表保证每个迁移只执行一次：
正常重启不会重放任何 SQL，退休的破坏性清理（见 schema.RETIRED_MIGRATIONS）
只在第一次建立版本表时登记为"已处理"，永不自动运行。
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..common import now_iso
from ..repositories import migration_repo
from .schema import RETIRED_MIGRATIONS


class Database:
    def __init__(self, path, migrations=None):
        self.path = str(path)
        self.migrations = migrations or []

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def transaction(self, immediate: bool = False):
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def init(self, ddl: str, seed=None) -> None:
        """建库：建目录 -> WAL -> 建表 -> 一次性增量迁移 -> 种子数据。

        迁移按 name 在 schema_migrations 里登记，只执行一次；已有库上
        "列已存在"的 OperationalError 视为该迁移早已完成，同样登记后跳过。
        任何情况下都不执行 RETIRED_MIGRATIONS 里的破坏性清理。
        """
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = self.connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(ddl)
            self._apply_migrations(conn)
            if seed:
                seed(conn)
        finally:
            conn.close()

    def _apply_migrations(self, conn) -> None:
        for name in RETIRED_MIGRATIONS:
            if not migration_repo.is_applied(conn, name):
                migration_repo.mark_applied(conn, name, now_iso())
        for name, sql in self.migrations:
            if migration_repo.is_applied(conn, name):
                continue
            try:
                conn.execute(sql)
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
            migration_repo.mark_applied(conn, name, now_iso())
