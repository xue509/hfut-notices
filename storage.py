"""
合工大通知公告 - SQLite 存储模块

功能：
  - 记录已抓取的通知（URL 去重）
  - 追踪推送状态
  - 查询推送历史
"""

import hashlib
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class NoticeStorage:
    """通知存储管理器（基于 SQLite）"""

    def __init__(self, db_path: str = "notices.db"):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self):
        """初始化数据库表"""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS notices (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url_hash    TEXT UNIQUE NOT NULL,
                title       TEXT NOT NULL,
                date        TEXT NOT NULL,
                link        TEXT NOT NULL,
                source_tab  TEXT DEFAULT '',
                category    TEXT DEFAULT 'other',
                pushed      INTEGER DEFAULT 0,
                pushed_at   TEXT,
                push_method TEXT,
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        # 索引
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_url_hash ON notices(url_hash)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_date ON notices(date)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_category ON notices(category)
        """)
        self.conn.commit()
        logger.debug(f"数据库已初始化: {self.db_path}")

    def is_new(self, notice: dict) -> bool:
        """检查通知是否未见过（基于 URL hash）"""
        url_hash = self._hash_url(notice["link"])
        cursor = self.conn.execute(
            "SELECT id FROM notices WHERE url_hash = ?", (url_hash,)
        )
        return cursor.fetchone() is None

    def is_pushed(self, notice: dict) -> bool:
        """检查通知是否已推送"""
        url_hash = self._hash_url(notice["link"])
        cursor = self.conn.execute(
            "SELECT pushed FROM notices WHERE url_hash = ?", (url_hash,)
        )
        row = cursor.fetchone()
        return row is not None and row[0] == 1

    def save(self, notice: dict):
        """保存一条新通知（尚未推送）"""
        url_hash = self._hash_url(notice["link"])
        try:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO notices
                    (url_hash, title, date, link, source_tab, category)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    url_hash,
                    notice["title"],
                    notice["date"],
                    notice["link"],
                    notice.get("source_tab", ""),
                    notice.get("category", "other"),
                ),
            )
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"保存失败: {e}")

    def save_batch(self, notices: list[dict]):
        """批量保存通知"""
        for notice in notices:
            self.save(notice)
        logger.debug(f"批量保存 {len(notices)} 条记录")

    def mark_pushed(
        self,
        notice: dict,
        push_method: str = "pushplus",
    ):
        """标记通知为已推送"""
        url_hash = self._hash_url(notice["link"])
        self.conn.execute(
            """
            UPDATE notices
            SET pushed = 1,
                pushed_at = ?,
                push_method = ?
            WHERE url_hash = ?
            """,
            (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                push_method,
                url_hash,
            ),
        )
        self.conn.commit()

    def get_unpushed(self, category: Optional[str] = None) -> list[dict]:
        """获取所有未推送的通知，可按分类筛选"""
        if category:
            cursor = self.conn.execute(
                """
                SELECT title, date, link, source_tab, category
                FROM notices
                WHERE pushed = 0 AND category = ?
                ORDER BY date DESC
                """,
                (category,),
            )
        else:
            cursor = self.conn.execute(
                """
                SELECT title, date, link, source_tab, category
                FROM notices
                WHERE pushed = 0
                ORDER BY date DESC
                """
            )

        return [
            {
                "title": row[0],
                "date": row[1],
                "link": row[2],
                "source_tab": row[3],
                "category": row[4],
            }
            for row in cursor.fetchall()
        ]

    def get_recent(
        self,
        days: int = 7,
        category: Optional[str] = None,
    ) -> list[dict]:
        """获取最近N天的通知记录"""
        from datetime import datetime, timedelta

        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        if category:
            cursor = self.conn.execute(
                """
                SELECT title, date, link, source_tab, category, pushed
                FROM notices
                WHERE date >= ?
                ORDER BY date DESC
                """,
                (cutoff,),
            )
        else:
            cursor = self.conn.execute(
                """
                SELECT title, date, link, source_tab, category, pushed
                FROM notices
                WHERE date >= ?
                ORDER BY date DESC
                """,
                (cutoff,),
            )

        return [
            {
                "title": row[0],
                "date": row[1],
                "link": row[2],
                "source_tab": row[3],
                "category": row[4],
                "pushed": bool(row[5]),
            }
            for row in cursor.fetchall()
        ]

    def get_stats(self) -> dict:
        """获取数据库统计"""
        cursor = self.conn.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN category='competition' THEN 1 ELSE 0 END) as competition,
                SUM(CASE WHEN category='holiday' THEN 1 ELSE 0 END) as holiday,
                SUM(CASE WHEN pushed=1 THEN 1 ELSE 0 END) as pushed,
                SUM(CASE WHEN pushed=0 THEN 1 ELSE 0 END) as unpushed
            FROM notices
            """
        )
        row = cursor.fetchone()
        return {
            "total": row[0] or 0,
            "competition": row[1] or 0,
            "holiday": row[2] or 0,
            "pushed": row[3] or 0,
            "unpushed": row[4] or 0,
        }

    @staticmethod
    def _hash_url(url: str) -> str:
        """对 URL 做 SHA256 取前 16 位"""
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]

    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            logger.debug("数据库连接已关闭")


# 命令行测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(message)s")

    db = NoticeStorage(":memory:")  # 内存数据库用于测试
    db.save({"title": "关于2026年清明节放假安排的通知", "date": "2026-07-21",
             "link": "https://example.com/test1", "source_tab": "其他通知",
             "category": "holiday"})
    db.save({"title": "关于举办创新大赛的通知", "date": "2026-07-21",
             "link": "https://example.com/test2", "source_tab": "教学科研",
             "category": "competition"})

    print("未推送:", db.get_unpushed())
    print("统计:", db.get_stats())
    print("test1 是否新通知:", db.is_new({"link": "https://example.com/test1"}))
    print("test3 是否新通知:", db.is_new({"link": "https://example.com/test3"}))
    db.close()
