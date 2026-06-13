"""Signal classifier — turns a news headline into structured signal keys.

Deterministic by default (regex rules, no API key, fully testable). If
ANTHROPIC_API_KEY is set and use_llm=True, an LLM pass can adjudicate items the
rules flag as ambiguous (off by default; see classify_llm()).

Each classified item maps to:
  * one or more signal keys understood by scoring.py
  * a matched watchlist person id (or None -> candidate-discovery review)
  * a confidence, gated by source reliability AND signal strength
"""
from __future__ import annotations
import os
import re
import logging
from dataclasses import dataclass, field

log = logging.getLogger("hatring.classify")

# Ordered rules: (signal_key, compiled regex). First strong match sets status.
RULES: list[tuple[str, re.Pattern]] = [
    ("declared", re.compile(r"\b(announces?|launch(es|ed)?|files? (a )?statement of candidacy|enters the race|declares? (a |his |her )?(2028 )?(presidential )?(bid|campaign|run))\b", re.I)),
    ("exploratory", re.compile(r"\b(exploratory committee|testing[- ]the[- ]waters|forms? an exploratory)\b", re.I)),
    ("consideringQuote", re.compile(r"\b(seriously (considering|weighing|thinking)|(consider(ing)?|weighing|mulling|ey(e|es|eing)|exploring|pursuing) a (2028 )?(presidential |white house )?(run|bid|campaign)|thinking about (it|running|a run|a 2028)|will (consider|look at) (it|that))\b", re.I)),
    ("softConsidering", re.compile(r"\b(not?(t)? ruling (it )?out|won'?t rule (it )?out|would(n'?t)? rule (it )?out|never say never|nothing off the table|leaves? (the )?door open|open to (a |the )?(run|bid|idea)|hasn'?t ruled out|doesn'?t rule out|keeping (his|her|their) name|keeps? (his|her|their) options)\b", re.I)),
    ("ruledOut", re.compile(r"\b(not running|won'?t run|rules? out|ruled out|no plans to run|will not (run|seek)|takes? (himself|herself) out|bows? out|not seeking)\b", re.I)),
]
BEHAVIOUR: list[tuple[str, re.Pattern]] = [
    ("earlyState", re.compile(r"\b(Iowa|New Hampshire|South Carolina|Nevada)\b")),
    ("donors", re.compile(r"\b(fundrais(er|ing)|donors?|bundlers?|super ?PAC|leadership PAC|PAC)\b", re.I)),
    ("staffing", re.compile(r"\b(hires?|campaign manager|chief strategist|consultants?|staffs? up|adds? (a |an )?(veteran|operative))\b", re.I)),
    ("mediaBlitz", re.compile(r"\b(Sunday show|podcast|book tour|memoir|media (tour|blitz)|sit-down interview)\b", re.I)),
]

STRENGTH = {"declared": 5, "exploratory": 4, "consideringQuote": 3,
            "ruledOut": 3, "softConsidering": 2}


def _min_conf(a: str, b: str) -> str:
    order = ["Noise", "Low", "Medium", "High", "Very high"]
    return order[min(order.index(a), order.index(b))]


@dataclass
class Classified:
    person_id: str | None
    name_guess: str
    keys: list[str]
    confidence: str
    headline: str
    url: str
    source: str
    date: str
    quote: str = ""
    matched_alias: str | None = None
    discovery: bool = False           # True -> name not on watchlist
    tags: list[str] = field(default_factory=list)


_TITLES = {"Senator", "Sen", "Sen.", "Gov", "Gov.", "Governor", "Rep", "Rep.",
           "Representative", "President", "Secretary", "Mayor", "Former", "The",
           "A", "Congressman", "Congresswoman", "Vice"}


def _guess_name(title: str) -> str:
    """Best-effort proper-name extraction for the discovery review queue."""
    for run in re.findall(r"(?:[A-Z][a-zA-Z.'-]+ )+[A-Z][a-zA-Z.'-]+", title):
        words = [w for w in run.split() if w not in _TITLES]
        if len(words) >= 2:
            return " ".join(words[:3])
    return title.split(" - ")[0][:40]


def _match_person(text: str, watchlist: list[dict]):
    """Return (id, alias) for the first watchlist person whose alias appears."""
    for p in watchlist:
        for alias in (p.get("aliases") or [p["name"]]):
            if re.search(r"\b" + re.escape(alias) + r"\b", text, re.I):
                return p["id"], alias
    return None, None


def classify_item(item, watchlist: list[dict]) -> Classified | None:
    text = f"{item.title}. {item.summary}"
    keys: list[str] = []
    declarative = None
    for key, rx in RULES:
        if rx.search(text):
            declarative = declarative or key      # first (strongest) declarative
            keys.append(key)
    for key, rx in BEHAVIOUR:
        if rx.search(text):
            keys.append(key)
    if not keys:
        return None                                 # no political signal -> drop

    # confidence = min(source ceiling, signal-strength tier)
    sig_conf = {5: "Very high", 4: "High", 3: "High", 2: "Medium"}.get(
        STRENGTH.get(declarative, 0), "Low")
    confidence = _min_conf(item.confidence_ceiling, sig_conf)

    pid, alias = _match_person(text, watchlist)
    return Classified(
        person_id=pid,
        name_guess=alias or _guess_name(item.title),
        keys=sorted(set(keys), key=lambda k: -STRENGTH.get(k, 0)),
        confidence=confidence,
        headline=item.title.strip(),
        url=item.url, source=item.source, date=item.published,
        matched_alias=alias, discovery=(pid is None),
        tags=[item.source] if item.source else [],
    )


def classify_batch(items, watchlist) -> list[Classified]:
    out = [c for c in (classify_item(i, watchlist) for i in items) if c]
    log.info("classify: %d/%d items carried a signal (%d unmatched/discovery)",
             len(out), len(items), sum(1 for c in out if c.discovery))
    return out


# ---- optional LLM adjudication (disabled unless explicitly enabled) --------
def classify_llm(items, watchlist):
    """Hook for an Anthropic-API pass over ambiguous items.

    Intentionally a thin, documented stub: enabling it is a deliberate choice
    that adds a paid dependency. The deterministic path above is the supported,
    tested default. Implement by batching item text to the Messages API with a
    JSON-schema'd tool call returning {person, keys[], confidence, quote}.
    """
    raise NotImplementedError(
        "LLM classification is an opt-in upgrade. Set ANTHROPIC_API_KEY and "
        "implement the Messages-API call here; the rules engine is the default."
    )


def llm_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))
