# Bittime X (Twitter) Monitor

Watches X (Twitter) for mentions of `@bittimeexchange` and the keyword
"Bittime", filters out promo/marketing/KOL noise with Claude,
sends the important stuff (complaints, genuine questions, advice, recommendations)
to a Telegram channel, and posts a periodic AI-written summary report.

> This repo tracks **Bittime only**. There's a sibling repo, `bitrue-x-monitor`,
> that tracks `@BitrueOfficial` / "Bitrue" — same code, separate deployment,
> separate Telegram chats, separate database, so the two brands never mix.

## How it works

```
x_scraper.py        -> polls X API v2 /2/tweets/search/recent every N minutes
                        (default 5), stores new tweets in SQLite
ai_classifier.py     -> sends each new tweet's text to Claude, classifies as
                        useful (complaint/question/advice/recommendation) or
                        not (promotional/KOL/other)
telegram_notifier.py -> pushes each "useful" tweet to a Telegram chat
reporter.py           -> every N minutes (default 60), asks Claude to summarize
                        all tweets + reasoning since the last report, saves it
                        to reports/, and sends it to Telegram
main.py               -> ties it together with an in-process scheduler
```

All state lives in a local SQLite file (`data/tweets.db`) so tweets are never
processed or alerted on twice, even across restarts.

## Setup

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure secrets.** Copy `.env.example` to `.env` and fill in:
   - `X_BEARER_TOKEN` — the Bearer Token for your paid X API app (from
     developer.x.com). Needs access to `GET /2/tweets/search/recent`.
   - `ANTHROPIC_API_KEY` — your Anthropic API key.
   - `TELEGRAM_BOT_TOKEN` — token for a bot you created via @BotFather.
   - `TELEGRAM_ALERT_CHAT_ID` — the chat/group/channel ID the bot should post
     "useful" tweet alerts to. Add the bot to that chat first (and make it an
     admin if it's a channel).
   - `TELEGRAM_REPORT_CHAT_ID` — optional; where periodic reports go. Leave
     blank to reuse `TELEGRAM_ALERT_CHAT_ID`.

   ```bash
   cp .env.example .env
   # then edit .env
   ```

3. **Adjust tracking / timing (optional).** In `.env`:
   - `X_TRACK_ACCOUNTS` / `X_TRACK_KEYWORDS` — comma-separated, no `@` needed
     for accounts.
   - `SCRAPE_INTERVAL_MINUTES` — how often to poll X (default 5).
   - `REPORT_INTERVAL_MINUTES` — how often to post the AI summary report
     (default 60, i.e. hourly). Set to whatever cadence you want.
   - `ANTHROPIC_CLASSIFY_MODEL` / `ANTHROPIC_REPORT_MODEL` — swap models if
     you want cheaper/faster or more thorough classification vs. reporting.

4. **Run it**
   ```bash
   python main.py
   ```
   It runs one scrape immediately, then loops forever on the schedule above.
   Stop with Ctrl+C. For continuous operation, run it under `systemd`, `tmux`,
   `screen`, `pm2`, or a small Docker container/cron-friendly host.

   To generate and send one PDF report on demand (without waiting for the
   next scheduled interval, and without starting the scheduler loop):
   ```bash
   python main.py --report-now
   ```
   This picks up whatever's been classified since the last report (same
   logic the scheduled report uses) and sends it right away.

## Getting your Telegram chat ID

1. Create a bot with [@BotFather](https://t.me/BotFather), grab the token.
2. Add the bot to your group/channel (as admin, if it's a channel).
3. Send any message in that chat, then visit:
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
4. Look for `"chat":{"id": ...}` in the response — that's your chat ID
   (group/channel IDs are usually negative numbers).

## Notes on the X API query

The scraper builds a single query like:

```
(@bittimeexchange OR to:bittimeexchange OR "Bittime") -is:retweet -from:bittimeexchange
```

- `@bittimeexchange` catches tweets that mention/tag the account.
- `to:bittimeexchange` catches **replies to Bittime's own tweets**, even when
  the reply text doesn't literally contain "@bittimeexchange" (X still tracks
  who a reply is directed at even if the visible @mention is deleted).
- `"Bittime"` catches any tweet containing that keyword.

- Retweets are excluded (pure reposts add noise, no new text to review).
- Replies and quote tweets ARE included, since complaints/questions often show
  up as replies.
- `since_id` is tracked in the DB so each poll only pulls tweets newer than
  the last one seen — no duplicate work, no missed tweets between polls
  (within X API's recent-search 7-day window).
- Recent-search query length limits depend on your API access tier; if you
  add many more accounts/keywords and hit a 400 error mentioning query length,
  trim the list.

## Classification logic

Each new tweet's text is sent to Claude with a system prompt that sorts it
into: `complaint`, `question`, `advice`, `recommendation`, `promotional`, or
`other`. Only the first four count as "useful" and get pushed to Telegram;
promotional/KOL hype and unrelated "other" mentions are logged in the DB (and
still show up in the report's stats) but don't trigger an alert.

If a classification call fails (network hiccup, rate limit, etc.) the tweet
is safely defaulted to not-useful/other and logged, rather than crashing the
loop or getting stuck forever unclassified.

## Reports

Every `REPORT_INTERVAL_MINUTES`, `reporter.py`:
1. Pulls all classified tweets since the last report.
2. Asks Claude to write a short plain-English summary (volume, key
   complaints, notable questions/advice, noise level).
3. Renders a **PDF** (via reportlab) with an overview stats table, the AI
   summary, and a color-coded tweet-by-tweet detail section (category,
   author, text, why it was flagged, link) — saved to `reports/`.
4. Sends the PDF to `TELEGRAM_REPORT_CHAT_ID` as a Telegram document, with a
   short caption (total tweets / useful count) since Telegram captions are
   capped at 1024 characters.

## Files

| File | Purpose |
|---|---|
| `config.py` | Loads and validates all settings from `.env` |
| `db.py` | SQLite schema + helper functions |
| `x_scraper.py` | X API v2 polling + pagination + since_id watermark |
| `ai_classifier.py` | Claude-based per-tweet classification |
| `telegram_notifier.py` | Telegram Bot API sender |
| `reporter.py` | Periodic AI-summarized report generation |
| `main.py` | Scheduler / entry point |

## Extending

- Multiple Telegram destinations per category: branch in
  `telegram_notifier.send_alert_tweet` on `tweet["ai_category"]`.
- Sentiment scoring, language detection, or auto-reply drafts: add fields to
  the `tweets` table and extend `ai_classifier.classify_tweet`'s prompt/schema.
- Swap SQLite for Postgres/MySQL: only `db.py` needs to change, since every
  other module goes through its functions.
