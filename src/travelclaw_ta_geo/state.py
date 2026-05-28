from __future__ import annotations

import sqlite3
from pathlib import Path


class CrawlState:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS completed_geos (
                seed_key TEXT PRIMARY KEY,
                geo_id INTEGER NOT NULL,
                source_url TEXT NOT NULL,
                completed_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def is_done(self, seed_key: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM completed_geos WHERE seed_key = ?", (seed_key,)).fetchone()
        return row is not None

    def mark_done(self, seed_key: str, geo_id: int, source_url: str, completed_at: str) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO completed_geos(seed_key, geo_id, source_url, completed_at)
            VALUES (?, ?, ?, ?)
            """,
            (seed_key, geo_id, source_url, completed_at),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> CrawlState:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

