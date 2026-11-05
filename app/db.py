"""SQLite 持久层：连接、建表与用户/作文的 CRUD。

为什么用手写 sqlite3 而不是 ORM：
1. 规格限定「依赖只用 fastapi / uvicorn / openai / python-dotenv」，ORM 属于额外依赖。
2. 数据规模小、表结构固定，标准库足够，还能让代码对每一条 SQL 都透明可控。
3. 每请求新建连接：FastAPI 的 async 端点与线程池端点会并发执行，连接不跨请求共享
   才能避免「同一连接被多个线程复用」的 SQLite 报错，也省掉全局锁的复杂度。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .config import settings


def _now() -> str:
    """统一时间格式：本地时间、精确到秒，入库后直接展示，省去前端再格式化。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    """每次调用都开新连接，事务结束自动 commit，出异常回滚，finally 里确保 close。

    用 contextmanager 包一层，避免 `with sqlite3.connect(...)` 只提交不关闭的坑。
    """
    parent = Path(settings.db_path).parent
    parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    # 打开外键不是必须（这里没有级联），但开着更符合「数据关系明确」的预期
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def init_db() -> None:
    """建表（幂等）。列定义与 SPEC 第 4 节逐字一致。"""
    with _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                nickname TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS essays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                topic TEXT NOT NULL,
                genre TEXT NOT NULL,
                content TEXT NOT NULL,
                outline TEXT DEFAULT '',
                critique TEXT DEFAULT '',
                score_json TEXT DEFAULT '',
                word_count INTEGER DEFAULT 0,
                favorite INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_essays_user ON essays(user_id, created_at DESC);
            """
        )


# ---------------------------------------------------------------------------
# 用户
# ---------------------------------------------------------------------------

def create_user(username: str, password_hash: str, salt: str, nickname: str | None) -> dict[str, Any]:
    """插入新用户并返回完整行；重名冲突由上层在插入前显式检查，这里直接抛 IntegrityError。"""
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO users(username, password_hash, salt, nickname, created_at) "
            "VALUES(?, ?, ?, ?, ?)",
            (username, password_hash, salt, nickname or "", _now()),
        )
        user_id = cur.lastrowid
    return get_user_by_id(user_id) or {}


def get_user_by_username(username: str) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return _row_to_dict(row)


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# 作文
# ---------------------------------------------------------------------------

def _score_total(score_json: str) -> float | None:
    """从 score_json 里抠出总分，用于历史列表直接展示分数列。"""
    if not score_json:
        return None
    try:
        data = json.loads(score_json)
        total = data.get("total")
        return float(total) if total is not None else None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def create_essay(
    user_id: int,
    title: str,
    topic: str,
    genre: str,
    content: str,
    outline: str,
    critique: str,
    score_json: str,
    word_count: int,
) -> int:
    """落库一篇作文，返回自增 id（SSE 的 saved 事件要用）。"""
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO essays(user_id, title, topic, genre, content, outline, critique, "
            "score_json, word_count, favorite, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (user_id, title, topic, genre, content, outline, critique, score_json, word_count, _now()),
        )
        return int(cur.lastrowid)


def list_essays(user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    """返回当前用户的作文摘要（不含正文，避免列表接口拖大）。"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, title, topic, genre, word_count, score_json, favorite, created_at "
            "FROM essays WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["score"] = _score_total(d.pop("score_json", ""))
        d["favorite"] = bool(d.get("favorite"))
        items.append(d)
    return items


def get_essay(essay_id: int) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM essays WHERE id = ?", (essay_id,)).fetchone()
    return _row_to_dict(row)


def set_favorite(essay_id: int, user_id: int, favorite: bool) -> bool:
    """只允许本人改自己作文的收藏状态；受影响行数为 0 说明越权或不存在。"""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE essays SET favorite = ? WHERE id = ? AND user_id = ?",
            (1 if favorite else 0, essay_id, user_id),
        )
        return cur.rowcount > 0


def delete_essay(essay_id: int, user_id: int) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM essays WHERE id = ? AND user_id = ?",
            (essay_id, user_id),
        )
        return cur.rowcount > 0


__all__ = [
    "init_db",
    "create_user",
    "get_user_by_username",
    "get_user_by_id",
    "create_essay",
    "list_essays",
    "get_essay",
    "set_favorite",
    "delete_essay",
]
