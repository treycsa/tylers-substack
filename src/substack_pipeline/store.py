"""SQLite log: which repos were scored when, what label they got, and the adopted ledger."""
from __future__ import annotations

import csv
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS scored (
    full_name TEXT NOT NULL,
    scored_on TEXT NOT NULL,
    total REAL NOT NULL,
    label TEXT,
    latest_release TEXT,
    PRIMARY KEY (full_name, scored_on)
);
CREATE TABLE IF NOT EXISTS posts_seen (
    url TEXT PRIMARY KEY,
    seen_on TEXT NOT NULL
);
"""


class Store:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(SCHEMA)
        self.adopted_csv = db_path.parent / "adopted.csv"

    # --- dedupe -----------------------------------------------------------
    def recently_scored(self, full_name: str, window_days: int) -> tuple[bool, str | None]:
        cutoff = (date.today() - timedelta(days=window_days)).isoformat()
        row = self.conn.execute(
            "SELECT latest_release FROM scored WHERE full_name=? AND scored_on>=? ORDER BY scored_on DESC LIMIT 1",
            (full_name, cutoff),
        ).fetchone()
        return (row is not None, row[0] if row else None)

    def mark_post_seen(self, url: str) -> bool:
        """Returns True if the post was new."""
        try:
            self.conn.execute("INSERT INTO posts_seen VALUES (?, ?)", (url, date.today().isoformat()))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    # --- results ----------------------------------------------------------
    def record(self, full_name: str, total: float, label: str | None, latest_release: str | None,
               scored_on: str | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO scored VALUES (?, ?, ?, ?, ?)",
            (full_name, scored_on or date.today().isoformat(), total, label, latest_release),
        )
        self.conn.commit()

    def record_adopted(self, issue_date: str, full_name: str, label: str, total: float, verdict: str) -> None:
        new = not self.adopted_csv.exists()
        with open(self.adopted_csv, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["date", "repo", "label", "score", "verdict"])
            w.writerow([issue_date, full_name, label, total, verdict])

    def adopted_this_week(self, as_of: date | None = None) -> tuple[int, int]:
        """(adopted, reviewed) over the trailing 7 days, computed from adopted.csv, never hand-typed."""
        as_of = as_of or date.today()
        start = as_of - timedelta(days=6)
        if not self.adopted_csv.exists():
            return (0, 0)
        adopted = reviewed = 0
        with open(self.adopted_csv) as f:
            for row in csv.DictReader(f):
                d = datetime.fromisoformat(row["date"]).date()
                if start <= d <= as_of:
                    reviewed += 1
                    if row["label"] == "ADOPTED":
                        adopted += 1
        return (adopted, reviewed)

    def leaderboard(self, days: int = 30, limit: int = 10) -> list[tuple[str, float, str | None]]:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        return self.conn.execute(
            "SELECT full_name, MAX(total), label FROM scored WHERE scored_on>=? GROUP BY full_name "
            "ORDER BY 2 DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
