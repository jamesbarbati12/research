"""Rule-based first-pass classifier + Claude API fallback for low-confidence items.

classify_item() runs regex/keyword rules against `headline + lede` for every
category in data/tier_table.json (including the new rumor/anticipation/
sentiment/denial categories). Matches below CLASSIFY_CONFIDENCE_THRESHOLD are
flagged needs_llm=True; if ANTHROPIC_API_KEY is set, classify_with_llm_fallback()
re-classifies those with a Claude call constrained to the tier table's
category list. Without a key, low-confidence items stay `unclassified` /
`needs_llm=True` so a later batch job can pick them up.
"""
import json
import re
from functools import lru_cache

from src import config

# SEC filing form types map straight to a category when the regex pass
# doesn't find anything more specific (e.g. an 8-K whose body actually
# describes a merger agreement should win ma_definitive_agreement instead).
FORM_TYPE_FALLBACK = {
    "8-K": ("sec_filing_8k_material", 0.65),
    "SC 13D": ("sec_filing_13d_activist", 0.75),
    "SC 13D/A": ("sec_filing_13d_activist", 0.7),
    "SC 13G": ("sec_filing_13g_passive", 0.75),
    "SC 13G/A": ("sec_filing_13g_passive", 0.7),
    "4": ("insider_form4", 0.75),
}

# Each category maps to a list of (compiled regex, weight). Confidence for a
# category is the sum of weights of every pattern that matches, capped at 1.0.
_RULES_SOURCE = {
    "rumor_speculation": [
        (r"\bsources? (?:say|said|familiar|close to)\b", 0.45),
        (r"\bpeople familiar with the matter\b", 0.45),
        (r"\bin (?:early |advanced )?talks to\b", 0.5),
        (r"\bis reportedly\b|\breportedly\b", 0.35),
        (r"\bconsidering (?:a |an )?(?:sale|acquisition|merger|spinoff|ipo)\b", 0.5),
        (r"\bexploring (?:a |an )?(?:sale|options|strategic alternatives|acquisition)\b", 0.5),
        (r"\bweighing (?:a |an )?(?:sale|bid|offer)\b", 0.45),
        (r"\bcould (?:be )?(?:near|nearing)\b", 0.3),
        (r"\bmay (?:explore|consider|pursue)\b", 0.3),
        (r"\bunconfirmed reports?\b", 0.4),
    ],
    "media_anticipation": [
        (r"\bahead of (?:its |the )?[\w\s]{0,25}?(?:earnings|report|meeting|announcement|launch)\b", 0.45),
        (r"\bwhat to expect (?:from|when|ahead of)?\b", 0.5),
        (r"\bpreview(?:ing|s)?\b", 0.35),
        (r"\bset to report\b|\bdue to report\b", 0.4),
        (r"\binvestors? (?:await|awaiting|watch|watching)\b", 0.35),
        (r"\ball eyes (?:are )?on\b", 0.35),
        (r"\bheading into (?:earnings|the fed|the meeting)\b", 0.4),
        (r"\bhere'?s what to (?:watch|know)\b", 0.4),
    ],
    "social_sentiment_spike": [
        (r"\btrending on (?:twitter|x|reddit|stocktwits)\b", 0.55),
        (r"\bgoing viral\b", 0.4),
        (r"\br/wallstreetbets\b|\bwallstreetbets\b|\bwsb\b", 0.5),
        (r"\bmeme stock\b", 0.45),
        (r"\bretail (?:traders|investors) (?:pile|piling) into\b", 0.45),
        (r"\bsocial media (?:buzz|chatter|sentiment)\b", 0.4),
        (r"\bunusual (?:options|trading) (?:activity|volume)\b", 0.3),
        (r"\bmentions? (?:spike|surge|surging)\b", 0.4),
    ],
    "rumor_denial": [
        (r"\bdenies\b|\bdenied\b", 0.5),
        (r"\bno truth to\b", 0.5),
        (r"\brefutes\b|\brebuts\b", 0.45),
        (r"\bnot in talks\b", 0.45),
        (r"\bdeclined to comment\b", 0.15),
        (r"\bspokesperson (?:denied|denies)\b", 0.5),
        (r"\bdismisses report\b|\bdismissed (?:the )?report\b", 0.45),
    ],
    "ma_definitive_agreement": [
        (r"\bdefinitive (?:merger )?agreement\b", 0.55),
        (r"\bto acquire\b|\bagrees to acquire\b|\bhas agreed to acquire\b", 0.5),
        (r"\bacquisition of\b", 0.4),
        (r"\bmerger agreement\b|\bwill merge with\b", 0.5),
        (r"\ball-cash deal\b|\ball-stock deal\b", 0.35),
        (r"\bcompletes acquisition\b|\bcloses acquisition\b", 0.45),
    ],
    "fda_decision": [
        (r"\bfda approv(?:es|al|ed)\b", 0.6),
        (r"\bcomplete response letter\b|\bcrl\b", 0.55),
        (r"\bfda rejects\b|\bfda declines to approve\b", 0.6),
        (r"\bphase (?:1|2|3|i|ii|iii) (?:results|trial|data)\b", 0.4),
        (r"\btopline (?:results|data)\b", 0.4),
        (r"\bclinical (?:hold|trial) results?\b", 0.3),
    ],
    "guidance_raise": [
        (r"\braises? (?:its )?(?:full-year )?guidance\b", 0.55),
        (r"\bboosts? (?:its )?outlook\b", 0.45),
        (r"\braised? (?:its )?(?:full-year )?forecast\b", 0.45),
    ],
    "guidance_cut": [
        (r"\bcuts? (?:its )?(?:full-year )?guidance\b", 0.55),
        (r"\blowers? (?:its )?outlook\b", 0.45),
        (r"\bwithdraws? (?:its )?guidance\b", 0.55),
        (r"\bcuts? (?:its )?(?:full-year )?forecast\b", 0.45),
    ],
    "earnings_beat": [
        (r"\bbeats? (?:analyst |consensus )?estimates\b", 0.5),
        (r"\btops? (?:analyst |consensus |wall street )?estimates\b", 0.5),
        (r"\breports? (?:q[1-4]|first|second|third|fourth[- ]quarter) (?:results|earnings)\b", 0.2),
    ],
    "earnings_miss": [
        (r"\bmisses? (?:analyst |consensus )?estimates\b", 0.5),
        (r"\bfalls? short of estimates\b", 0.5),
    ],
    "bankruptcy_restructuring": [
        (r"\bfiles? for bankruptcy\b", 0.6),
        (r"\bchapter 11\b|\bchapter 7\b", 0.55),
        (r"\bgoing concern\b", 0.4),
        (r"\brestructuring plan\b", 0.3),
    ],
    "regulatory_legal_action": [
        (r"\bsec investigation\b|\bftc probe\b|\bdoj investigation\b", 0.55),
        (r"\bsubpoena(?:ed)?\b", 0.45),
        (r"\bfined\b|\bfine of\b", 0.4),
        (r"\bsettles? charges\b", 0.4),
        (r"\bclass action lawsuit\b", 0.4),
        (r"\bsues\b|\blawsuit\b", 0.25),
    ],
    "legal_settlement": [
        (r"\bsettlement\b", 0.4),
        (r"\bagrees to pay\b", 0.35),
        (r"\breaches? settlement\b", 0.5),
        (r"\bsettles? lawsuit\b", 0.5),
    ],
    "executive_change": [
        (r"\bresigns? as (?:ceo|cfo|coo|chairman)\b", 0.55),
        (r"\bsteps? down as\b", 0.5),
        (r"\bappoints? new (?:ceo|cfo|coo)\b", 0.5),
        (r"\bnames? new (?:ceo|cfo|coo)\b", 0.5),
        (r"\bceo (?:resignation|departure)\b", 0.45),
        (r"\bto succeed\b.*\bas ceo\b", 0.4),
    ],
    "analyst_upgrade": [
        (r"\bupgrades?\b", 0.4),
        (r"\braises? price target\b", 0.35),
        (r"\binitiates? coverage with (?:a )?buy\b", 0.5),
        (r"\bupgraded to (?:buy|overweight|outperform)\b", 0.55),
    ],
    "analyst_downgrade": [
        (r"\bdowngrades?\b", 0.4),
        (r"\bcuts? price target\b", 0.35),
        (r"\bdowngraded to (?:sell|underweight|underperform)\b", 0.55),
    ],
    "dividend_change": [
        (r"\bdeclares? (?:a |its )?[\w\s]{0,15}?dividend\b", 0.4),
        (r"\braises? (?:its )?[\w\s]{0,15}?dividend\b", 0.5),
        (r"\bcuts? (?:its )?[\w\s]{0,15}?dividend\b", 0.5),
        (r"\bsuspends? (?:its )?[\w\s]{0,15}?dividend\b", 0.55),
        (r"\binitiates? (?:a |its )?[\w\s]{0,15}?dividend\b", 0.45),
    ],
    "buyback_announced": [
        (r"\bshare (?:buyback|repurchase)\b", 0.5),
        (r"\brepurchase program\b", 0.5),
        (r"\bauthorizes? (?:a |an )?(?:new )?buyback\b", 0.5),
        (r"\bexpands? (?:its )?buyback\b", 0.45),
    ],
    "sec_filing_8k_material": [
        (r"\b8-k\b", 0.4),
        (r"\bmaterial definitive agreement\b", 0.4),
        (r"\bcurrent report\b", 0.25),
    ],
    "sec_filing_13d_activist": [
        (r"\bschedule 13d\b|\b13d filing\b", 0.55),
        (r"\bactivist stake\b", 0.5),
        (r"\bnominates? directors?\b", 0.35),
        (r"\bseeks? board seats?\b", 0.4),
    ],
    "sec_filing_13g_passive": [
        (r"\bschedule 13g\b|\b13g filing\b", 0.55),
        (r"\bpassive stake\b", 0.5),
    ],
    "insider_form4": [
        (r"\bform 4\b", 0.5),
        (r"\binsider (?:sells?|buys?|purchase)\b", 0.4),
        (r"\bdirector purchase\b", 0.35),
        (r"\b10b5-1 plan\b", 0.35),
    ],
    "product_launch": [
        (r"\bunveils?\b", 0.35),
        (r"\blaunches? new\b", 0.4),
        (r"\bannounces? (?:the )?launch of\b", 0.45),
        (r"\bintroduces? new\b", 0.35),
    ],
    "partnership_announced": [
        (r"\bpartners? with\b", 0.4),
        (r"\bstrategic partnership\b", 0.45),
        (r"\bjoint venture\b", 0.45),
        (r"\bsigns? (?:an )?agreement with\b", 0.35),
        (r"\bsupply agreement\b", 0.35),
    ],
    "stock_split": [
        (r"\bstock split\b", 0.55),
        (r"\breverse split\b", 0.55),
        (r"\b\d+-for-\d+ split\b", 0.5),
    ],
}


@lru_cache(maxsize=1)
def load_tier_table():
    with open(config.TIER_TABLE_PATH) as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _compiled_rules():
    return {
        category: [(re.compile(pattern, re.IGNORECASE), weight) for pattern, weight in patterns]
        for category, patterns in _RULES_SOURCE.items()
    }


def _regex_scores(text):
    scores = {}
    for category, rules in _compiled_rules().items():
        total = 0.0
        for pattern, weight in rules:
            if pattern.search(text):
                total += weight
        if total > 0:
            scores[category] = min(total, 1.0)
    return scores


def classify_item(headline, lede="", form_type=None):
    """Rule-based first pass. Returns a dict:
    {category, confidence, needs_llm, classified_by, candidates}
    `candidates` is the ranked list of (category, score) for LLM fallback context.
    """
    tier_table = load_tier_table()
    valid_categories = set(tier_table["categories"].keys())

    text = f"{headline}\n{lede}".strip()
    scores = _regex_scores(text)
    scores = {c: s for c, s in scores.items() if c in valid_categories}

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

    if ranked and ranked[0][1] >= config.CLASSIFY_CONFIDENCE_THRESHOLD:
        category, confidence = ranked[0]
        return {
            "category": category,
            "confidence": confidence,
            "needs_llm": False,
            "classified_by": "regex",
            "candidates": ranked[:5],
        }

    # Regex didn't produce a confident match. Try the SEC form-type fallback
    # before giving up to "unclassified".
    if form_type and form_type in FORM_TYPE_FALLBACK:
        category, confidence = FORM_TYPE_FALLBACK[form_type]
        if not ranked or confidence > ranked[0][1]:
            return {
                "category": category,
                "confidence": confidence,
                "needs_llm": confidence < config.CLASSIFY_CONFIDENCE_THRESHOLD,
                "classified_by": "form_type",
                "candidates": ranked[:5],
            }

    best_category, best_confidence = (ranked[0] if ranked else ("unclassified", 0.0))
    return {
        "category": best_category if ranked else "unclassified",
        "confidence": best_confidence,
        "needs_llm": True,
        "classified_by": "regex",
        "candidates": ranked[:5],
    }


def classify_with_llm_fallback(headline, lede="", candidates=None):
    """Re-classify a low-confidence item with Claude, constrained to the tier
    table's category list. Returns the same shape as classify_item(), or None
    if ANTHROPIC_API_KEY isn't configured (caller should leave the item as
    'unclassified'/needs_llm=True for a later batch pass).
    """
    if not config.ANTHROPIC_API_KEY:
        return None

    import anthropic

    tier_table = load_tier_table()
    categories = list(tier_table["categories"].keys())

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    prompt = (
        "Classify this financial news headline into exactly one category from "
        f"this list: {', '.join(categories)}.\n\n"
        f"Headline: {headline}\n"
        f"Lede: {lede}\n\n"
        "Respond with ONLY the category name, nothing else."
    )
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=20,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text.strip().lower().replace(" ", "_")
    category = text if text in categories else "unclassified"
    return {
        "category": category,
        "confidence": 0.9 if category != "unclassified" else 0.0,
        "needs_llm": False,
        "classified_by": "llm",
        "candidates": candidates or [],
    }
