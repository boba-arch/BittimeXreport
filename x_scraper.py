"""Polls the X API v2 recent-search endpoint for new mentions/keywords."""
import logging
import time
from datetime import datetime, timedelta, timezone

import requests

import config
import db

log = logging.getLogger("x_scraper")

SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"
SINCE_ID_META_KEY = "x_search_since_id"

TWEET_FIELDS = "created_at,author_id,text"
USER_FIELDS = "username"
EXPANSIONS = "author_id"


def _headers() -> dict:
    return {"Authorization": f"Bearer {config.X_BEARER_TOKEN}"}


def fetch_new_tweets() -> list[dict]:
    """Fetch all new tweets matching the tracked query since the last run.

    Handles pagination (next_token) and the since_id watermark so each tweet
    is only ever processed once. Returns a list of plain dicts ready for
    db.insert_tweet.

    On a cold start (no since_id saved yet -- e.g. first ever run, or a fresh
    deploy without a persistent volume), this does NOT pull X's full 7-day
    recent-search history. Instead it uses `start_time` to only look back
    INITIAL_LOOKBACK_MINUTES, so you don't get flooded with days-old tweets.
    """
    query = config.build_search_query()
    since_id = db.get_meta(SINCE_ID_META_KEY)

    collected: list[dict] = []
    newest_id_seen = since_id
    next_token = None

    while True:
        params = {
            "query": query,
            "max_results": 100,
            "tweet.fields": TWEET_FIELDS,
            "user.fields": USER_FIELDS,
            "expansions": EXPANSIONS,
        }
        if since_id:
            params["since_id"] = since_id
        else:
            # Cold start: cap how far back we look instead of defaulting to
            # X's full 7-day recent-search window.
            start_time = datetime.now(timezone.utc) - timedelta(
                minutes=config.INITIAL_LOOKBACK_MINUTES
            )
            params["start_time"] = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        if next_token:
            params["next_token"] = next_token

        resp = requests.get(SEARCH_URL, headers=_headers(), params=params, timeout=30)

        if resp.status_code == 429:
            reset = resp.headers.get("x-rate-limit-reset")
            log.warning("X API rate limited. Reset at epoch %s. Backing off.", reset)
            break
        resp.raise_for_status()
        payload = resp.json()

        users_by_id = {
            u["id"]: u for u in payload.get("includes", {}).get("users", [])
        }

        for t in payload.get("data", []):
            author = users_by_id.get(t.get("author_id"), {})
            username = author.get("username")
            tweet_id = t["id"]
            collected.append(
                {
                    "tweet_id": tweet_id,
                    "author_id": t.get("author_id"),
                    "author_username": username,
                    "text": t.get("text", ""),
                    "matched_query": query,
                    "created_at": t.get("created_at"),
                    "url": f"https://x.com/{username or 'i'}/status/{tweet_id}",
                }
            )
            if newest_id_seen is None or int(tweet_id) > int(newest_id_seen):
                newest_id_seen = tweet_id

        meta = payload.get("meta", {})
        next_token = meta.get("next_token")
        if not next_token:
            break
        time.sleep(1)  # be polite between paginated calls

    if newest_id_seen and newest_id_seen != since_id:
        db.set_meta(SINCE_ID_META_KEY, newest_id_seen)

    return collected


def scrape_and_store() -> int:
    """Fetch new tweets and store any not already in the DB.

    Returns the count of newly inserted tweets.
    """
    tweets = fetch_new_tweets()
    inserted = 0
    for tw in tweets:
        if db.insert_tweet(tw):
            inserted += 1
            log.info(
                "Caught tweet from @%s: %s  (%s)",
                tw.get("author_username") or "unknown",
                tw["text"],
                tw.get("url"),
            )
    if inserted:
        log.info("Stored %d new tweet(s).", inserted)
    else:
        log.info("No new tweets found.")
    return inserted
