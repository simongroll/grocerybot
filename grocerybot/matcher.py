"""Turn "2 low fat milk" into (ASIN, qty) using order history.

Pipeline, cheapest first:
  1. learned alias          ("milk" was confirmed as B0xxx before)  -> add
  2. fuzzy match, clear win  (score >= AUTO_ADD_SCORE, big gap)      -> add
  3. LLM picks from fuzzy shortlist (understands "semi skimmed" ~ "low fat")
       confident -> add, unsure -> ask with numbered options
  4. nothing in history      -> caller falls back to an Amazon search
"""

import json
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from . import config, db

WORD_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "a couple of": 2}


@dataclass
class Match:
    phrase: str  # normalised item text, used as alias key
    qty: int | None  # None = user didn't say; caller uses usual_qty
    asin: str | None = None  # set when confident
    options: list[dict] = field(default_factory=list)  # set when we need the user to choose
    reason: str = ""


def parse_quantity(text: str) -> tuple[int | None, str]:
    t = text.strip().lower()
    patterns = [
        r"^(\d+)\s*x?\s+(.+)$",  # "2 milk", "2x milk", "2 x milk"
        r"^(.+?)\s+x\s*(\d+)$",  # "milk x2"
        r"^(.+?)\s*\*\s*(\d+)$",  # "milk *2"
    ]
    for i, p in enumerate(patterns):
        m = re.match(p, t)
        if m:
            qty, rest = (m.group(1), m.group(2)) if i == 0 else (m.group(2), m.group(1))
            return max(1, min(int(qty), 20)), rest.strip()
    for word, n in sorted(WORD_NUMBERS.items(), key=lambda kv: -len(kv[0])):
        if t.startswith(word + " "):
            return n, t[len(word) + 1 :].strip()
    return None, t


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s%.]", " ", text.lower())).strip()


STOPWORDS = {"a", "an", "the", "of", "and", "some", "more", "pack", "by", "amazon", "x"}


def _stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"  # nappies -> nappy
    if len(w) > 4 and w.endswith(("oes", "shes", "ches")):
        return w[:-2]  # tomatoes -> tomato
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]  # leeks -> leek
    return w


def _tokens(text: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z]+", text.lower()) if w not in STOPWORDS and len(w) > 1]


def score(phrase: str, title: str) -> float:
    """How well every word you typed is covered by some word in the title (typo tolerant),
    blended with a whole-string score as a tie-breaker. Whole-string scoring alone lets
    'nappies' match 'apples'."""
    q, t = _tokens(phrase), _tokens(title)
    if not q or not t:
        return fuzz.WRatio(phrase, title.lower())
    per_word = []
    for w in q:
        best = max(fuzz.ratio(w, tw) for tw in t)
        per_word.append(best if best >= 75 else best * 0.5)
    return 0.85 * (sum(per_word) / len(per_word)) + 0.15 * fuzz.WRatio(phrase, title.lower())


def shortlist(phrase: str, products: list[dict], limit: int = 15) -> list[tuple[dict, float]]:
    # light boost for things you buy often, so "milk" prefers the milk you actually buy
    scored = [(p, score(phrase, p["title"]) + min(p["times_ordered"], 10) * 0.3) for p in products]
    return sorted(scored, key=lambda x: -x[1])[:limit]


def match(text: str) -> Match:
    qty, rest = parse_quantity(text)
    phrase = normalise(rest)

    if asin := db.get_alias(phrase):
        return Match(phrase, qty, asin=asin, reason="alias")

    products = db.all_products()
    if not products:
        return Match(phrase, qty, reason="no history")

    cands = shortlist(phrase, products)
    top, top_score = cands[0]
    second_score = cands[1][1] if len(cands) > 1 else 0
    if top_score >= config.AUTO_ADD_SCORE and top_score - second_score >= 8:
        return Match(phrase, qty, asin=top["asin"], reason=f"fuzzy {top_score:.0f}")

    if config.LLM_ENABLED:
        try:
            return _llm_pick(text, phrase, qty, [c for c, _ in cands])
        except Exception as e:  # never let the LLM take the bot down
            print(f"[matcher] LLM failed, falling back to fuzzy: {e!r}")

    good = [c for c, s in cands if s >= 60 and s >= top_score - 15][:4]
    return Match(phrase, qty, options=[{"asin": c["asin"], "title": c["title"]} for c in good], reason="fuzzy ambiguous")


# --- LLM step -------------------------------------------------------------------------

_client = None

PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "asin": {"type": ["string", "null"], "description": "Best ASIN, or null if none fit"},
        "confident": {"type": "boolean"},
        "quantity": {"type": ["integer", "null"], "description": "Only if the user stated one"},
        "alternatives": {"type": "array", "items": {"type": "string"}, "description": "Other plausible ASINs, best first, max 3"},
    },
    "required": ["asin", "confident", "quantity", "alternatives"],
    "additionalProperties": False,
}

SYSTEM = """You match a shopper's short grocery request to a product they have bought before on Amazon Fresh.
You get the request and a candidate list (ASIN, title, how many past orders included it, usual quantity).
- Pick the candidate the shopper most likely means. British/American synonyms count (semi-skimmed ~ low fat / 2%).
- Prefer frequently ordered items when the request is generic ("milk", "bananas").
- confident=true only if one candidate is clearly right; if two differ in a way that matters (size, flavour, brand) and the request does not decide it, confident=false and list them in alternatives.
- asin=null if no candidate is the same kind of product. Never invent an ASIN."""


def _llm_pick(raw: str, phrase: str, qty: int | None, cands: list[dict]) -> Match:
    global _client
    import anthropic

    _client = _client or anthropic.Anthropic()
    lines = "\n".join(
        f"{c['asin']} | {c['title']} | ordered {c['times_ordered']}x | usual qty {c['usual_qty']}" for c in cands
    )
    resp = _client.beta.messages.create(
        model=config.LLM_MODEL,
        max_tokens=2000,
        system=SYSTEM,
        messages=[{"role": "user", "content": f"Request: {raw!r}\n\nCandidates:\n{lines}"}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": PICK_SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("model refused")
    out = json.loads(next(b.text for b in resp.content if b.type == "text"))

    valid = {c["asin"]: c for c in cands}
    qty = qty or out.get("quantity")
    asin = out["asin"] if out["asin"] in valid else None
    if asin and out["confident"]:
        return Match(phrase, qty, asin=asin, reason="llm")
    opts = [a for a in [asin, *out.get("alternatives", [])] if a in valid]
    opts = list(dict.fromkeys(opts))[:4]  # dedupe, keep order
    return Match(phrase, qty, options=[{"asin": a, "title": valid[a]["title"]} for a in opts], reason="llm unsure")
