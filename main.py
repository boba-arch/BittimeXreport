"""Entry point: schedules the two independent stages and runs them on a loop.

Stage 1 - SCRAPE + PUSH (every SCRAPE_INTERVAL_MINUTES, default 5):
    Poll the X API for anything matching the tracked accounts/keywords,
    store new tweets in SQLite, and push every one of them straight to
    Telegram -- unfiltered, no AI involved at this point.

Stage 2 - REPORT (every REPORT_INTERVAL_MINUTES, default 60):
    Independent of stage 1. First runs AI classification (analyze +
    categorize: complaint / question / advice / recommendation / promotional
    / other) over every tweet scraped since the last report, THEN renders a
    PDF listing all of them with their category + reasoning, and sends it to
    Telegram. This is the only point the AI ever looks at a tweet.

Run:
    python main.py                # start the full scheduler loop (default)
    python main.py --scrape-now   # run one scrape+push cycle immediately, then exit
    python main.py --report-now   # classify pending + generate/send one PDF report, then exit

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
import telegram_notifier
import reporter

logging.basicConfig(
    level=logging.DEBUG if config.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("main")


def scrape_and_push_job() -> None:
    """Stage 1: poll the X API, store new tweets, push every one to Telegram
    immediately with no AI filtering."""
    try:
        x_scraper.scrape_and_store()
    except Exception:
        log.exception("Scrape step failed.")
        return

    try:
        to_send = db.get_unsent_tweets(limit=200)
        for tweet in to_send:
            if telegram_notifier.send_new_tweet(tweet):
                db.mark_sent_to_telegram(tweet["tweet_id"])
        if to_send:
            log.info("Pushed %d new tweet(s) to Telegram.", len(to_send))
    except Exception:
        log.exception("Telegram push step failed.")


def report_job() -> None:
    """Stage 2: classify everything pending, then build + send the PDF report."""
    try:
        reporter.generate_and_send_report()
    except Exception:
        log.exception("Report generation/send failed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bittime X monitor")
    parser.add_argument(
        "--scrape-now",
        action="store_true",
        help="Run one scrape + push cycle immediately, then exit without "
        "starting the scheduler.",
    )
    parser.add_argument(
        "--report-now",
        action="store_true",
        help="Classify everything scraped since the last report, generate "
        "and send one PDF report immediately, then exit without starting "
        "the scheduler.",
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
        log.info("Running one on-demand scrape + push cycle...")
        scrape_and_push_job()
        return

    if args.report_now:
        log.info("Generating on-demand PDF report...")
        report_job()
        return

    log.info(
        "Tracking accounts=%s keywords=%s | scrape+push every %dm | report every %dm",
        config.X_TRACK_ACCOUNTS,
        config.X_TRACK_KEYWORDS,
        config.SCRAPE_INTERVAL_MINUTES,
        config.REPORT_INTERVAL_MINUTES,
    )

    # Run once immediately on startup, then on schedule.
    scrape_and_push_job()

    schedule.every(config.SCRAPE_INTERVAL_MINUTES).minutes.do(scrape_and_push_job)
    schedule.every(config.REPORT_INTERVAL_MINUTES).minutes.do(report_job)

    log.info("Scheduler started. Press Ctrl+C to stop.")
    while True:
        schedule.run_pending()
        time.sleep(5)


if __name__ == "__main__":
    main()
