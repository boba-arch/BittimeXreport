"""Generates a periodic (default hourly, configurable) report of all tweets
seen since the last report, with AI-written summary/reasoning, rendered as a
PDF and sent to Telegram.
"""
import logging
import os
from collections import Counter
from datetime import datetime, timezone
from html import escape

import anthropic
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    HRFlowable,
)

import config
import db
import ai_classifier
import telegram_notifier

log = logging.getLogger("reporter")

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


REPORT_SYSTEM_PROMPT = """You write short, plain-English social-listening reports for \
a crypto exchange's ops team covering Bitrue. You will be given a list of \
tweets from the last reporting window, each with the AI triage category and reasoning \
already applied. Write a concise report (150-300 words) covering:
1. Overall volume and sentiment/tone at a glance.
2. The most important complaints or issues raised, if any (be specific but brief).
3. Notable genuine questions, advice, or recommendations worth the team's attention.
4. A one-line note on the promotional/noise volume (no need to detail it).

Do not invent tweets or details beyond what's given. Plain text only, no markdown \
headers, it will be sent as a Telegram message. Keep it skimmable with short \
paragraphs or a few bullet points using "-".
"""


def _build_tweet_summary_block(tweets) -> str:
    lines = []
    for t in tweets:
        lines.append(
            f"- [{t['ai_category']}] @{t['author_username'] or 'unknown'}: "
            f"\"{t['text']}\" | reasoning: {t['ai_reasoning']}"
        )
    return "\n".join(lines)


def _ai_summarize(tweets) -> str:
    if not tweets:
        return "No new tweets in this window."
    block = _build_tweet_summary_block(tweets)
    client = _get_client()
    try:
        response = client.messages.create(
            model=config.ANTHROPIC_REPORT_MODEL,
            max_tokens=600,
            system=REPORT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": block}],
        )
        return "".join(b.text for b in response.content if b.type == "text").strip()
    except Exception:
        log.exception("Failed to generate AI report summary.")
        return "(AI summary generation failed this cycle; raw stats only below.)"


def _build_styles() -> dict:
    base = getSampleStyleSheet()
    base.add(
        ParagraphStyle(
            name="ReportTitle", parent=base["Title"], fontSize=18, spaceAfter=4
        )
    )
    base.add(
        ParagraphStyle(
            name="Meta", parent=base["Normal"], textColor=colors.HexColor("#555555")
        )
    )
    base.add(
        ParagraphStyle(
            name="SectionHeading",
            parent=base["Heading2"],
            spaceBefore=14,
            spaceAfter=6,
            textColor=colors.HexColor("#1a1a1a"),
        )
    )
    base.add(
        ParagraphStyle(
            name="Body", parent=base["Normal"], leading=15, spaceAfter=8
        )
    )
    base.add(
        ParagraphStyle(
            name="TweetLine", parent=base["Normal"], leading=13, spaceAfter=6,
            leftIndent=10,
        )
    )
    return base


CATEGORY_COLORS = {
    "complaint": "#c0392b",
    "question": "#2980b9",
    "advice": "#8e44ad",
    "recommendation": "#27ae60",
    "promotional": "#7f8c8d",
    "other": "#7f8c8d",
}


def _build_pdf(
    file_path: str,
    now: datetime,
    window_label: str,
    total_count: int,
    useful_count: int | None,
    category_counts: Counter,
    ai_summary: str,
    tweets,
    ai_enabled: bool,
) -> None:
    styles = _build_styles()
    doc = SimpleDocTemplate(
        file_path,
        pagesize=letter,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        title="Bittime X Monitor Report",
    )
    story = []

    story.append(Paragraph("Bittime X (Twitter) Monitor Report", styles["ReportTitle"]))
    story.append(
        Paragraph(
            f"Window: last {window_label} &nbsp;|&nbsp; "
            f"Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}",
            styles["Meta"],
        )
    )
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", color=colors.HexColor("#dddddd")))
    story.append(Spacer(1, 10))

    # --- Stats table ---
    story.append(Paragraph("Overview", styles["SectionHeading"]))
    stats_rows = [["Total tweets", str(total_count)]]
    if ai_enabled:
        stats_rows.append(["Flagged useful", str(useful_count)])
        for cat, count in category_counts.most_common():
            stats_rows.append([cat.capitalize(), str(count)])
    stats_table = Table(stats_rows, colWidths=[3 * inch, 2 * inch])
    stats_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("TEXTCOLOR", (0, 0), (-1, 1), colors.HexColor("#1a1a1a")),
                ("FONTNAME", (0, 0), (-1, 1), "Helvetica-Bold"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#dddddd")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(stats_table)

    # --- AI summary ---
    story.append(Paragraph("AI Summary", styles["SectionHeading"]))
    for para in ai_summary.split("\n"):
        para = para.strip()
        if para:
            story.append(Paragraph(escape(para), styles["Body"]))

    # --- Per-tweet detail ---
    if tweets:
        story.append(Paragraph("Tweet Detail", styles["SectionHeading"]))
        for t in tweets:
            author = escape(t["author_username"] or "unknown")
            text = escape(t["text"] or "")
            url = escape(t["url"] or "")
            if ai_enabled:
                cat = t["ai_category"] or "other"
                color_hex = CATEGORY_COLORS.get(cat, "#7f8c8d")
                reasoning = escape(t["ai_reasoning"] or "")
                line = (
                    f'<font color="{color_hex}"><b>[{cat.upper()}]</b></font> '
                    f"@{author}: {text}<br/>"
                    f'<font color="#666666" size="9">Why: {reasoning} &nbsp;|&nbsp; {url}</font>'
                )
            else:
                line = (
                    f"@{author}: {text}<br/>"
                    f'<font color="#666666" size="9">{url}</font>'
                )
            story.append(Paragraph(line, styles["TweetLine"]))

    doc.build(story)


def classify_pending_tweets() -> int:
    """Run AI classification over every tweet scraped since the last report.

    This is the ONLY place classification happens -- tweets are pushed to
    Telegram raw/unfiltered the moment they're scraped, and the AI only
    analyzes + categorizes them here, right before the report is built.
    No-op (and makes no Anthropic API calls) when AI_ANALYSIS_ENABLED=false.
    Returns how many tweets were classified.
    """
    if not config.AI_ANALYSIS_ENABLED:
        return 0

    pending = db.get_unclassified_tweets(limit=1000)
    for tweet in pending:
        result = ai_classifier.classify_tweet(tweet["text"])
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
        log.info("Classified %d tweet(s) for this report.", len(pending))
    return len(pending)


def generate_report() -> tuple[str, str]:
    """Classify anything pending (if AI_ANALYSIS_ENABLED), build the PDF
    report, save it to disk, and return (pdf_path, short_caption)."""
    ai_enabled = config.AI_ANALYSIS_ENABLED
    now = datetime.now(timezone.utc)
    window_label = f"{config.REPORT_INTERVAL_MINUTES}-minute"

    if ai_enabled:
        classify_pending_tweets()
        tweets = db.get_tweets_for_report(limit=1000)
        category_counts = Counter(t["ai_category"] for t in tweets)
        useful_count = sum(1 for t in tweets if t["ai_useful"])
        ai_summary = _ai_summarize(tweets)
        caption_useful_line = f"Total: {len(tweets)} | Useful: {useful_count}"
    else:
        tweets = db.get_all_unreported_tweets(limit=1000)
        category_counts = Counter()
        useful_count = None
        ai_summary = (
            "AI analysis is currently disabled (AI_ANALYSIS_ENABLED=false on "
            "Railway). This report lists every tweet scraped in this window "
            "with no categorization or reasoning. Set AI_ANALYSIS_ENABLED=true "
            "to re-enable classification and this summary."
        )
        caption_useful_line = f"Total: {len(tweets)} (AI analysis off)"

    total_count = len(tweets)

    os.makedirs(config.REPORTS_DIR, exist_ok=True)
    filename = os.path.join(
        config.REPORTS_DIR, f"report_{now.strftime('%Y%m%d_%H%M%S')}.pdf"
    )
    _build_pdf(
        filename, now, window_label, total_count, useful_count,
        category_counts, ai_summary, tweets, ai_enabled,
    )
    log.info("PDF report saved to %s", filename)

    db.mark_included_in_report([t["tweet_id"] for t in tweets])

    caption = f"\U0001F4CA Bittime X monitor \u2014 last {window_label}\n{caption_useful_line}"
    return filename, caption


def generate_and_send_report() -> None:
    file_path, caption = generate_report()
    ok = telegram_notifier.send_report_pdf(file_path, caption)
    if not ok:
        log.error("Failed to send PDF report to Telegram (it was still saved to disk).")
