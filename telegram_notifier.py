"""Sends messages to Telegram via the Bot API."""
import logging
import html

import requests

import config

log = logging.getLogger("telegram_notifier")

API_BASE = "https://api.telegram.org"
MAX_MESSAGE_LEN = 4000  # Telegram hard limit is 4096; leave headroom


def _send(chat_id: str, text: str) -> bool:
    if not config.TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN is not set; cannot send message.")
        return False
    url = f"{API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    for i in range(0, len(text), MAX_MESSAGE_LEN):
        chunk = text[i : i + MAX_MESSAGE_LEN]
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=15,
        )
        if not resp.ok:
            log.error("Telegram send failed: %s %s", resp.status_code, resp.text)
            return False
    return True


def send_alert_tweet(tweet) -> bool:
    """Send a single 'useful' (AI-classified) tweet alert to the alert channel."""
    category_emoji = {
        "complaint": "🚨",
        "question": "❓",
        "advice": "💡",
        "recommendation": "👍",
    }.get(tweet["ai_category"], "📌")

    author = tweet["author_username"] or "unknown"
    text = html.escape(tweet["text"])
    reasoning = html.escape(tweet["ai_reasoning"] or "")

    message = (
        f"{category_emoji} <b>{tweet['ai_category'].capitalize()}</b> "
        f"from @{html.escape(author)}\n\n"
        f"{text}\n\n"
        f"<i>Why flagged:</i> {reasoning}\n"
        f"{tweet['url']}"
    )
    return _send(config.TELEGRAM_ALERT_CHAT_ID, message)


def send_report(report_text: str) -> bool:
    """Send the periodic report to the report channel as a plain text message."""
    return _send(config.TELEGRAM_REPORT_CHAT_ID, report_text)


def send_document(chat_id: str, file_path: str, caption: str = "") -> bool:
    """Send a file (e.g. the PDF report) to a Telegram chat via sendDocument."""
    if not config.TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN is not set; cannot send document.")
        return False
    url = f"{API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/sendDocument"
    try:
        with open(file_path, "rb") as f:
            resp = requests.post(
                url,
                data={
                    "chat_id": chat_id,
                    "caption": caption[:1024],  # Telegram caption limit
                    "parse_mode": "HTML",
                },
                files={"document": (file_path.split("/")[-1], f, "application/pdf")},
                timeout=60,
            )
        if not resp.ok:
            log.error("Telegram sendDocument failed: %s %s", resp.status_code, resp.text)
            return False
        return True
    except Exception:
        log.exception("Telegram sendDocument raised an exception.")
        return False


def send_report_pdf(file_path: str, caption: str = "") -> bool:
    """Send the periodic report PDF to the report channel."""
    return send_document(config.TELEGRAM_REPORT_CHAT_ID, file_path, caption)
