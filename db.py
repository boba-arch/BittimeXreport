"""SQLite storage for scraped tweets, AI classification, and delivery state."""
import sqlite3
import os
import contextlib
from datetime import datetime, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS tweets (
    tweet_id        TEXT PRIMARY KEY,
    author_id       TEXT,
    author_username TEXT,
    text            TEXT NOT NULL,
    matched_query   TEXT,
    created_at      TEXT,      -- tweet's own created_at from X API (UTC ISO8601)
    fetched_at      TEXT NOT NULL,  -- when our scraper pulled it (UTC ISO8601)
    url             TEXT,

    ai_classified   INTEGER NOT NULL DEFAULT 0,  -- 0/1
    ai_useful       INTEGER,                     -- 0/1, NULL until classified
    ai_category     TEXT,                        -- complaint/question/advice/recommendation/promotional/other
    ai_reasoning    TEXT,

    sent_to_telegram INTEGER NOT NULL DEFAULT 0,  -- 0/1
    included_in_report INTEGER NOT NULL DEFAULT 0 -- 0/1, so reports don't repeat tweets
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with contextlib.closing(_connect()) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_meta(key: str, default: str | None = None) -> str | None:
    with contextlib.closing(_connect()) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_meta(key: str, value: str) -> None:
    with contextlib.closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


def insert_tweet(tweet: dict) -> bool:
    """Insert a tweet if not already present. Returns True if newly inserted."""
    with contextlib.closing(_connect()) as conn:
        try:
            conn.execute(
                """
                INSERT INTO tweets
                    (tweet_id, author_id, author_username, text, matched_query,
                     created_at, fetched_at, url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tweet["tweet_id"],
                    tweet.get("author_id"),
                    tweet.get("author_username"),
                    tweet["text"],
                    tweet.get("matched_query"),
                    tweet.get("created_at"),
                    now_iso(),
                    tweet.get("url"),
                ),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False  # already exists


def get_unclassified_tweets(limit: int = 100) -> list[sqlite3.Row]:
    with contextlib.closing(_connect()) as conn:
        return conn.execute(
            "SELECT * FROM tweets WHERE ai_classified = 0 ORDER BY fetched_at ASC LIMIT ?",
            (limit,),
        ).fetchall()


def mark_classified(tweet_id: str, useful: bool, category: str, reasoning: str) -> None:
    with contextlib.closing(_connect()) as conn:
        conn.execute(
            """
            UPDATE tweets
            SET ai_classified = 1, ai_useful = ?, ai_category = ?, ai_reasoning = ?
            WHERE tweet_id = ?
            """,
            (1 if useful else 0, category, reasoning, tweet_id),
        )
        conn.commit()


def get_unsent_tweets(limit: int = 100) -> list[sqlite3.Row]:
    """Tweets not yet pushed to Telegram, regardless of AI classification --
    alerts now go out raw/immediately, with no AI filtering at push time."""
    with contextlib.closing(_connect()) as conn:
        return conn.execute(
            "SELECT * FROM tweets WHERE sent_to_telegram = 0 ORDER BY fetched_at ASC LIMIT ?",
            (limit,),
        ).fetchall()


def mark_sent_to_telegram(tweet_id: str) -> None:
    with contextlib.closing(_connect()) as conn:
        conn.execute(
            "UPDATE tweets SET sent_to_telegram = 1 WHERE tweet_id = ?", (tweet_id,)
        )
        conn.commit()


def get_tweets_for_report(limit: int = 500) -> list[sqlite3.Row]:
    """All classified tweets not yet folded into a report, oldest first."""
    with contextlib.closing(_connect()) as conn:
        return conn.execute(
            """
            SELECT * FROM tweets
            WHERE ai_classified = 1 AND included_in_report = 0
            ORDER BY fetched_at ASC LIMIT ?
            """,
            (limit,),
        ).fetchall()


def get_all_unreported_tweets(limit: int = 500) -> list[sqlite3.Row]:
    """Every tweet not yet folded into a report, oldest first -- regardless of
    AI classification state. Used when AI_ANALYSIS_ENABLED is false, so the
    report can still list raw tweets even though none were ever classified."""
    with contextlib.closing(_connect()) as conn:
        return conn.execute(
            "SELECT * FROM tweets WHERE included_in_report = 0 ORDER BY fetched_at ASC LIMIT ?",
            (limit,),
        ).fetchall()


def mark_included_in_report(tweet_ids: list[str]) -> None:
    if not tweet_ids:
        return
    with contextlib.closing(_connect()) as conn:
        conn.executemany(
            "UPDATE tweets SET included_in_report = 1 WHERE tweet_id = ?",
            [(tid,) for tid in tweet_ids],
        )
        conn.commit()
