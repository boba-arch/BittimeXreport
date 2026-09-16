"""Uses Claude to classify tweets as 'useful' (worth a human's attention) or not.

Useful  = complaints, genuine questions, advice, recommendations -- signal that
          Bittime's team would actually want to see and act on.
Not useful = promotional/marketing copy, KOL (influencer) shill posts, spam,
          generic hype with no actionable content.
"""
import json
import logging

import anthropic

import config

log = logging.getLogger("ai_classifier")

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


SYSTEM_PROMPT = """You are a triage assistant for a crypto exchange's social listening \
pipeline. You will be given a tweet that mentions "Bittime" or @bittimeexchange. \
Sometimes the tweet is a reply, in which case you will also be given the \
original tweet it's replying to -- use that as context for what the reply is \
actually about (e.g. a one-word reply like "same" or "+1" only makes sense in \
light of what it's replying to).

Think through the tweet carefully before answering: consider the tone, whether \
it reads as a real personal experience vs. copy-paste marketing, whether it \
contains a specific/actionable detail (an order ID, a specific bug, a specific \
question), and whether an account posting mostly hype/referral-link content \
would plausibly write this. Weigh ambiguous cases explicitly rather than \
defaulting to one category.

Classify the tweet into exactly one category:
- "complaint": user reporting a problem, bug, frozen funds, bad support experience, scam concern, etc.
- "question": a genuine question about the product, account, fees, listings, etc.
- "advice": user giving feedback or suggestions for how the product/service could improve.
- "recommendation": user recommending or endorsing the product to others in a genuine, non-paid-sounding way.
- "promotional": marketing copy, airdrop/referral spam, KOL (influencer) paid-looking hype posts, giveaway bait, generic bullish shilling with no substance.
- "other": anything else (news mentions, unrelated context, jokes, etc.)

A tweet is "useful" (useful=true) only if it is a complaint, genuine question, \
advice, or genuine recommendation that a human on the Bittime team should \
actually read and possibly respond to. Promotional/marketing/KOL hype and "other" \
are NOT useful. Classify only the REPLY itself (not the parent tweet) -- the \
parent is context, not the thing being judged.

Respond with ONLY a single JSON object, no markdown fences, no preamble, in \
exactly this shape:
{"useful": true or false, "category": "one of the categories above", "reasoning": "2-4 sentences walking through what in the tweet's wording, tone, and content drove this call, including why you ruled out the next most plausible category"}
"""


def classify_tweet(
    text: str,
    parent_text: str | None = None,
    parent_author: str | None = None,
) -> dict:
    """Classify a single tweet's text. Returns dict with useful/category/reasoning.

    If the tweet is a reply, pass the tweet it's replying to via parent_text
    (and optionally parent_author) so the model has the conversational
    context -- this matters a lot for short replies like "same issue here"
    that are meaningless on their own.

    Fails safe: on any error, marks the tweet as not useful/other so a pipeline
    hiccup never silently blocks the loop, and logs the issue.
    """
    client = _get_client()

    if parent_text:
        who = f"@{parent_author}" if parent_author else "someone"
        user_content = (
            f'This is a REPLY. The original tweet it\'s replying to (by {who}):\n'
            f'"{parent_text}"\n\n'
            f"The reply to classify:\n{text}"
        )
    else:
        user_content = f"Tweet:\n{text}"

    try:
        response = client.messages.create(
            model=config.ANTHROPIC_CLASSIFY_MODEL,
            max_tokens=config.ANTHROPIC_CLASSIFY_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
        raw = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(raw)
        return {
            "useful": bool(parsed.get("useful", False)),
            "category": str(parsed.get("category", "other")),
            "reasoning": str(parsed.get("reasoning", "")),
        }
    except Exception:
        log.exception("Failed to classify tweet, defaulting to not-useful/other.")
        return {"useful": False, "category": "other", "reasoning": "classification failed"}
