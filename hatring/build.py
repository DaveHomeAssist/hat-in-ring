"""Render the self-contained dashboard HTML from candidates.json.

The pipeline is the source of truth: it injects the merged dataset as the JS
SEED constant and stamps the build date (which drives the dashboard's recency
maths). Output is a single hostable .html file with no external data deps.
"""
from __future__ import annotations
import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape

log = logging.getLogger("hatring.build")

# fields the dashboard never needs (keep the payload lean & avoid leaking internals)
_DROP = {"history", "fec_ids"}


def _public(records: list[dict]) -> list[dict]:
    out = []
    for r in records:
        out.append({k: v for k, v in r.items() if k not in _DROP})
    return out


def _js_literal(obj) -> str:
    """Serialize to a JS literal that's safe to inject into an inline <script>.

    Jinja autoescape is off for the JS payload, so a value containing "</script>"
    (e.g. a hostile ingested headline) could break out. Escaping "<" plus the JS
    line/paragraph separators closes that — all three round-trip identically through
    the JS string parser. sort_keys keeps the output byte-stable.
    """
    s = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    bs = chr(92)  # literal backslash, built at runtime so the u-escape stays 6 chars
    return (s.replace("<", bs + "u003c")
             .replace(chr(0x2028), bs + "u2028")
             .replace(chr(0x2029), bs + "u2029"))


def render(candidates_path: Path, template_dir: Path, out_path: Path,
           built: date | None = None) -> Path:
    built = built or date.today()
    candidates_path = Path(candidates_path)
    records = json.loads(candidates_path.read_text())
    # The review queue lives next to candidates.json; inline it so the dashboard's
    # review screen has data with no external fetch. Absent file -> empty queue.
    review_path = candidates_path.parent / "review_queue.json"
    review = json.loads(review_path.read_text()) if review_path.exists() else []
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(enabled_extensions=()),  # we inject JS/JSON, not HTML
    )
    tmpl = env.get_template("dashboard.html.j2")
    html = tmpl.render(
        seed_json=_js_literal(_public(records)),
        review_json=_js_literal(review),
        # Anchor with Z so the browser parses the build stamp as UTC; otherwise it is
        # read in the viewer's local TZ and daysSince() can flip the 30/90-day recency
        # bands at date-line offsets, diverging from the Python scoring engine.
        generated_at=json.dumps(built.isoformat() + "T12:00:00Z"),
        generated_at_human=datetime.now().strftime("%b %d %Y %H:%M"),
        as_of=built.strftime("%B %-d, %Y"),
    )
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)  # e.g. public/ for the Pages artifact
    out_path.write_text(html)
    log.info("build: wrote %s (%d records, %d bytes)", out_path, len(records), len(html))
    return out_path
