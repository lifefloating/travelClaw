from __future__ import annotations

import sqlite3
import threading
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


class PersistentState:
    """Cross-run state at a fixed path (<data_root>/state/state.sqlite).

    Unlike CrawlState (scoped to one run_dir), this persists across orchestrator
    runs so already-completed cities are skipped on rerun. The media_index table
    is written but not yet read for skipping — it reserves the structure for a
    future image-level incremental pass.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS completed_cities (
                city_key TEXT PRIMARY KEY,
                geo_id INTEGER NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                r2_timestamp TEXT NOT NULL DEFAULT '',
                media_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'done',
                completed_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS media_index (
                geo_id INTEGER NOT NULL,
                media_id TEXT NOT NULL,
                phash TEXT NOT NULL DEFAULT '',
                first_seen TEXT NOT NULL,
                PRIMARY KEY (geo_id, media_id)
            )
            """
        )
        self.conn.commit()

    def is_city_done(self, city_key: str) -> bool:
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM completed_cities WHERE city_key = ? AND status = 'done'",
                (city_key,),
            ).fetchone()
        return row is not None

    def mark_city_done(
        self,
        city_key: str,
        *,
        geo_id: int,
        name: str,
        r2_timestamp: str,
        media_count: int,
        completed_at: str,
        status: str = "done",
    ) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO completed_cities
                    (city_key, geo_id, name, r2_timestamp, media_count, status, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (city_key, geo_id, name, r2_timestamp, media_count, status, completed_at),
            )
            self.conn.commit()

    def record_media(self, geo_id: int, media_id: str, phash: str, first_seen: str) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO media_index (geo_id, media_id, phash, first_seen)
                VALUES (?, ?, ?, ?)
                """,
                (geo_id, media_id, phash, first_seen),
            )
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def __enter__(self) -> PersistentState:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

