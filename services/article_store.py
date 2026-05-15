from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _article_id_from_url(url: str) -> str:
    return hashlib.sha1((url or "").strip().encode("utf-8")).hexdigest()[:16]


class ArticleStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS articles (
                    article_id TEXT PRIMARY KEY,
                    url TEXT NOT NULL UNIQUE,
                    original_url TEXT,
                    is_aggregator INTEGER NOT NULL DEFAULT 0,
                    company TEXT,
                    title TEXT,
                    source TEXT,
                    published_at TEXT,
                    fetched_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(conn, "articles", "original_url", "TEXT")
            self._ensure_column(conn, "articles", "is_aggregator", "INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS article_pipeline (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    article_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(article_id) REFERENCES articles(article_id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_article_pipeline_article_id ON article_pipeline(article_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_article_pipeline_stage ON article_pipeline(stage)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_system_instruction_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stage TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    system_instruction_version TEXT NOT NULL,
                    context_url TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_instruction_stage ON llm_system_instruction_audit(stage)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_instruction_created_at ON llm_system_instruction_audit(created_at)")

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def upsert_articles(self, articles: Iterable[Any]) -> None:
        now = _utc_now_iso()
        rows = []
        for article in articles:
            url = str(getattr(article, "url", "")).strip()
            if not url:
                continue
            rows.append(
                (
                    _article_id_from_url(url),
                    url,
                    str(getattr(article, "original_url", "") or url),
                    int(bool(getattr(article, "is_aggregator", False))),
                    str(getattr(article, "company", "")),
                    str(getattr(article, "title", "")),
                    str(getattr(article, "source", "")),
                    str(getattr(article, "published_at", "")),
                    now,
                )
            )
        if not rows:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO articles(article_id, url, original_url, is_aggregator, company, title, source, published_at, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    original_url=excluded.original_url,
                    is_aggregator=excluded.is_aggregator,
                    company=excluded.company,
                    title=excluded.title,
                    source=excluded.source,
                    published_at=excluded.published_at,
                    fetched_at=excluded.fetched_at
                """,
                rows,
            )

    def record_by_url(self, url: str, stage: str, payload: dict[str, Any]) -> None:
        url = (url or "").strip()
        if not url:
            return
        article_id = _article_id_from_url(url)
        now = _utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO articles(article_id, url, company, title, source, published_at, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (article_id, url, "", "", "", "", now),
            )
            conn.execute(
                """
                INSERT INTO article_pipeline(article_id, stage, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (article_id, stage, json.dumps(payload, ensure_ascii=False), now),
            )


    def record_validation_metrics(
        self,
        *,
        scope: str,
        stage: str,
        metrics: dict[str, Any],
    ) -> None:
        self.record_by_url(
            url=f"internal://validation/{scope}",
            stage=stage,
            payload={"metrics": metrics},
        )
    def record_stage(
        self,
        *,
        url: str,
        stage: str,
        provider: str,
        prompt: str | None = None,
        parsed_json: dict[str, Any] | list[Any] | None = None,
        model: str | None = None,
        system_instruction_version: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"provider": provider}
        if prompt:
            payload["prompt"] = prompt[:12000]
        if parsed_json is not None:
            payload["parsed_json"] = parsed_json
        self.record_by_url(url=url, stage=stage, payload=payload)
        if model and system_instruction_version:
            now = _utc_now_iso()
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO llm_system_instruction_audit(
                        stage, provider, model, system_instruction_version, context_url, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (stage, provider, model, system_instruction_version, url, now),
                )
