"""Central configuration, loaded from environment variables / .env file."""
import os
from dotenv import load_dotenv

load_dotenv()


def _split_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


# --- X (Twitter) API ---
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "")

# --- Anthropic API ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_CLASSIFY_MODEL = os.getenv("ANTHROPIC_CLASSIFY_MODEL", "claude-haiku-4-5-20251001")
ANTHROPIC_REPORT_MODEL = os.getenv("ANTHROPIC_REPORT_MODEL", "claude-sonnet-5")
# Max tokens Claude can use per tweet classification. Higher = more room for
# the model to actually reason about nuance before answering.
ANTHROPIC_CLASSIFY_MAX_TOKENS = int(os.getenv("ANTHROPIC_CLASSIFY_MAX_TOKENS", "1024"))

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALERT_CHAT_ID = os.getenv("TELEGRAM_ALERT_CHAT_ID", "")
TELEGRAM_REPORT_CHAT_ID = os.getenv("TELEGRAM_REPORT_CHAT_ID", "") or TELEGRAM_ALERT_CHAT_ID

# --- Monitoring targets ---
X_TRACK_ACCOUNTS = _split_csv(os.getenv("X_TRACK_ACCOUNTS", "bittimeexchange"))
X_TRACK_KEYWORDS = _split_csv(os.getenv("X_TRACK_KEYWORDS", "Bittime"))

# --- Timing ---
SCRAPE_INTERVAL_MINUTES = int(os.getenv("SCRAPE_INTERVAL_MINUTES", "5"))
REPORT_INTERVAL_MINUTES = int(os.getenv("REPORT_INTERVAL_MINUTES", "60"))
# On the very first poll ever (no watermark saved yet), how far back to look
# instead of pulling X's full 7-day recent-search history.
INITIAL_LOOKBACK_MINUTES = int(os.getenv("INITIAL_LOOKBACK_MINUTES", "10"))

# --- Misc ---
DB_PATH = os.getenv("DB_PATH", "data/tweets.db")
REPORTS_DIR = os.getenv("REPORTS_DIR", "reports")
DEBUG = os.getenv("DEBUG", "0") == "1"


def build_search_query() -> str:
    """Build the X API v2 recent-search query string.

    For each tracked account, matches: mentions (@handle) OR replies to that
    account's tweets (to:handle -- catches replies even when the reply text
    doesn't literally contain "@handle"). Also matches any tracked keyword.
    Excludes retweets and excludes posts authored by the tracked accounts
    themselves, so only what OTHER people say is caught.
    """
    account_terms = []
    for acct in X_TRACK_ACCOUNTS:
        account_terms.append(f"@{acct}")
        account_terms.append(f"to:{acct}")
    keyword_terms = [f'"{kw}"' for kw in X_TRACK_KEYWORDS]
    all_terms = account_terms + keyword_terms
    match_clause = f"({' OR '.join(all_terms)})"
    exclude_self = " ".join(f"-from:{acct}" for acct in X_TRACK_ACCOUNTS)
    return f"{match_clause} -is:retweet {exclude_self}".strip()


def validate() -> list[str]:
    """Return a list of human-readable problems with the current config."""
    problems = []
    if not X_BEARER_TOKEN:
        problems.append("X_BEARER_TOKEN is not set")
    if not ANTHROPIC_API_KEY:
        problems.append("ANTHROPIC_API_KEY is not set")
    if not TELEGRAM_BOT_TOKEN:
        problems.append("TELEGRAM_BOT_TOKEN is not set")
    if not TELEGRAM_ALERT_CHAT_ID:
        problems.append("TELEGRAM_ALERT_CHAT_ID is not set")
    if not X_TRACK_ACCOUNTS and not X_TRACK_KEYWORDS:
        problems.append("No accounts or keywords configured to track")
    return problems
