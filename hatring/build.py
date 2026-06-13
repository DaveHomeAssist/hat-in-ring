"""Render the self-contained dashboard HTML from candidates.json.

The pipeline is the source of truth: it injects the merged dataset as the JS
SEED constant and stamps the build date (which drives the dashboard's recency
maths). Output is a single hostable .html file with no external data deps.
"""
from __future__ import annotations
import json
import logging
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


def render(candidates_path: Path, template_dir: Path, out_path: Path,
           built: date | None = None) -> Path:
    built = built or date.today()
    records = json.loads(Path(candidates_path).read_text())
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(enabled_extensions=()),  # we inject JS/JSON, not HTML
    )
    tmpl = env.get_template("dashboard.html.j2")
    html = tmpl.render(
        seed_json=json.dumps(_public(records), ensure_ascii=False),
        generated_at=json.dumps(built.isoformat() + "T12:00:00"),
        generated_at_human=datetime.now().strftime("%b %d %Y %H:%M"),
        as_of=built.strftime("%B %-d, %Y"),
    )
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)  # e.g. public/ for the Pages artifact
    out_path.write_text(html)
    log.info("build: wrote %s (%d records, %d bytes)", out_path, len(records), len(html))
    return out_path
