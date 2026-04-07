"""SQLite history DB for deduplication and price tracking."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from scrapers.base import Listing
from utils.logging import get_logger


class HistoryDB:
    def __init__(self, db_path: str = "data/scanner.db") -> None:
        self.log = get_logger("db.history")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                price REAL,
                raw_price TEXT,
                condition TEXT DEFAULT 'new',
                seller TEXT DEFAULT '',
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                last_price REAL,
                notified INTEGER DEFAULT 0,
                UNIQUE(source, url)
            );

            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_id INTEGER NOT NULL,
                price REAL,
                seen_at TEXT NOT NULL,
                FOREIGN KEY (listing_id) REFERENCES listings(id)
            );

            CREATE INDEX IF NOT EXISTS idx_listings_source_url
                ON listings(source, url);
            CREATE INDEX IF NOT EXISTS idx_price_history_listing
                ON price_history(listing_id);
            """
        )
        self.conn.commit()

    def filter_new_or_price_dropped(self, listings: list[Listing]) -> list[Listing]:
        result: list[Listing] = []
        for listing in listings:
            row = self.conn.execute(
                "SELECT id, price, last_price FROM listings WHERE source = ? AND url = ?",
                (listing.source, listing.url),
            ).fetchone()

            if row is None:
                result.append(listing)
            elif listing.price is not None and row["price"] is not None:
                if listing.price < row["price"]:
                    result.append(listing)

        return result

    def save_listings(self, listings: list[Listing]) -> None:
        now = datetime.now(timezone.utc).isoformat()

        for listing in listings:
            row = self.conn.execute(
                "SELECT id, price FROM listings WHERE source = ? AND url = ?",
                (listing.source, listing.url),
            ).fetchone()

            if row is None:
                cursor = self.conn.execute(
                    """
                    INSERT INTO listings (source, url, title, price, raw_price,
                                          condition, seller, first_seen, last_seen, last_price)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        listing.source,
                        listing.url,
                        listing.title,
                        listing.price,
                        listing.raw_price,
                        listing.condition,
                        listing.seller,
                        now,
                        now,
                        listing.price,
                    ),
                )
                listing_id = cursor.lastrowid
            else:
                listing_id = row["id"]
                self.conn.execute(
                    """
                    UPDATE listings
                    SET title = ?, price = ?, raw_price = ?, last_seen = ?,
                        last_price = ?, condition = ?, seller = ?
                    WHERE id = ?
                    """,
                    (
                        listing.title,
                        listing.price,
                        listing.raw_price,
                        now,
                        row["price"],
                        listing.condition,
                        listing.seller,
                        listing_id,
                    ),
                )

            if listing.price is not None:
                self.conn.execute(
                    "INSERT INTO price_history (listing_id, price, seen_at) VALUES (?, ?, ?)",
                    (listing_id, listing.price, now),
                )

        self.conn.commit()

    def get_price_history(self, source: str, url: str) -> list[dict]:
        row = self.conn.execute(
            "SELECT id FROM listings WHERE source = ? AND url = ?",
            (source, url),
        ).fetchone()
        if not row:
            return []

        rows = self.conn.execute(
            "SELECT price, seen_at FROM price_history WHERE listing_id = ? ORDER BY seen_at",
            (row["id"],),
        ).fetchall()
        return [{"price": r["price"], "seen_at": r["seen_at"]} for r in rows]

    def close(self) -> None:
        self.conn.close()
