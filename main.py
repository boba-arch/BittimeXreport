"""Entry point: schedules the three independent stages and runs them on a loop.

Stage 1 - SCRAPE (every SCRAPE_INTERVAL_MINUTES, default 5):
    Poll the X API for anything matching the tracked accounts/keywords and
    store new tweets in SQLite. Nothing else happens here.

Stage 2 - CLASSIFY + ALERT (runs right after every scrape):
    EVERY tweet the scrape just pulled in is sent to Claude for
    classification FIRST. Only a tweet the AI marks "useful" (complaint /
    genuine question / advice / recommendation) is then pushed to Telegram.
    Tweets marked not-useful (promotional/KOL/other) are NOT alerted, but
    stay in the DB with their classification for the report.

Stage 3 - REPORT (every REPORT_INTERVAL_MINUTES, default 60):
    Independent of stage 2's alerting. Pulls EVERY tweet already classified
    (useful and not-useful alike) since the last report, along with its AI
    category and reasoning, renders a PDF, and sends it to Telegram.

Run:
    python main.py                # start the full scheduler loop (default)
    python main.py --scrape-now   # run one scrape+classify+alert cycle immediately, then exit
    python main.py --report-now   # generate + send one PDF report immediately, then exit

Stop with Ctrl+C. Designed to run continuously (e.g. under systemd, tmux, or a
Docker container) since it uses an in-process scheduler loop.
"""
import argparse
import logging
import time

import schedule

import config
import db
import x_scraper
import ai_classifier
import telegram_notifier
import reporter

logging.basicConfig(
    level=logging.DEBUG if config.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("main")


def scrape_job() -> int:
    """Stage 1: poll the X API and store any new matching tweets. Nothing else."""
    try:
        return x_scraper.scrape_and_store()
    except Exception:
        log.exception("Scrape step failed.")
        return 0


def classify_and_alert_job() -> None:
    """Stage 2: classify whatever is unclassified with Claude FIRST, then
    alert on every result the AI marks useful. Nothing reaches Telegram
    without going through classify_tweet() first."""
    try:
        pending = db.get_unclassified_tweets(limit=200)
        for tweet in pending:
            result = ai_classifier.classify_tweet(
                tweet["text"],
                parent_text=tweet["parent_tweet_text"],
                parent_author=tweet["parent_author_username"],
            )
            db.mark_classified(
                tweet["tweet_id"], result["useful"], result["category"], result["reasoning"]
            )
            log.info(
                "Classified @%s [%s | useful=%s]: %s\n    Reasoning: %s",
                tweet["author_username"] or "unknown",
                result["category"],
                result["useful"],
                tweet["text"],
                result["reasoning"],
            )
        if pending:
            log.info("Classified %d tweet(s).", len(pending))
    except Exception:
        log.exception("Classification step failed.")
        return  # don't attempt to alert on a classification pass that blew up

    try:
        to_send = db.get_useful_unsent_tweets(limit=100)
        for tweet in to_send:
            if telegram_notifier.send_alert_tweet(tweet):
                db.mark_sent_to_telegram(tweet["tweet_id"])
        if to_send:
            log.info("Sent %d useful tweet alert(s) to Telegram.", len(to_send))
    except Exception:
        log.exception("Telegram alert step failed.")


def scrape_then_classify_and_alert() -> None:
    """Runs every SCRAPE_INTERVAL_MINUTES: stage 1 followed immediately by stage 2,
    so every scrape result gets classified and, if useful, alerted right away."""
    scrape_job()
    classify_and_alert_job()


def report_job() -> None:
    """Stage 3: independent hourly (configurable) PDF report of everything scraped."""
    try:
        reporter.generate_and_send_report()
    except Exception:
        log.exception("Report generation/send failed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bittime X monitor")
    parser.add_argument(
        "--scrape-now",
        action="store_true",
        help="Run one scrape + classify + alert cycle immediately, then exit "
        "without starting the scheduler.",
    )
    parser.add_argument(
        "--report-now",
        action="store_true",
        help="Generate and send one PDF report immediately for whatever is "
        "queued (everything classified since the last report), then exit "
        "without starting the scheduler.",
    )
    args = parser.parse_args()

    problems = config.validate()
    if problems:
        log.error("Configuration problems found:")
        for p in problems:
            log.error("  - %s", p)
        log.error("Fix your .env file (see .env.example) and re-run.")
        return

    db.init_db()

    if args.scrape_now:
        log.info("Running one on-demand scrape + classify + alert cycle...")
        scrape_then_classify_and_alert()
        return

    if args.report_now:
        log.info("Generating on-demand PDF report...")
        report_job()
        return

    log.info(
        "Tracking accounts=%s keywords=%s | scrape+classify+alert every %dm | report every %dm",
        config.X_TRACK_ACCOUNTS,
        config.X_TRACK_KEYWORDS,
        config.SCRAPE_INTERVAL_MINUTES,
        config.REPORT_INTERVAL_MINUTES,
    )

    # Run once immediately on startup, then on schedule.
    scrape_then_classify_and_alert()

    schedule.every(config.SCRAPE_INTERVAL_MINUTES).minutes.do(scrape_then_classify_and_alert)
    schedule.every(config.REPORT_INTERVAL_MINUTES).minutes.do(report_job)

    log.info("Scheduler started. Press Ctrl+C to stop.")
    while True:
        schedule.run_pending()
        time.sleep(5)


if __name__ == "__main__":
    main()
