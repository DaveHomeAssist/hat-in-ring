"""Hat-in-Ring Radar ingest pipeline (CLI).

  fetch (FEC + news)  ->  classify  ->  merge into dataset  ->  rebuild HTML

Idempotent: signal dedup is tracked in data/signals.jsonl, so re-running only
applies genuinely new signals. Designed to run unattended (cron / GitHub
Actions) or by hand.

Usage:
  python -m hatring.pipeline --all
  python -m hatring.pipeline --news --build          # skip FEC
  python -m hatring.pipeline --offline --fixtures tests/fixtures   # no network
  python -m hatring.pipeline --build                 # rebuild HTML only
"""
from __future__ import annotations
import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

import yaml

from . import fec as fecmod
from . import news as newsmod
from . import classify as clf
from .merge import Dataset
from .build import render

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TEMPLATES = ROOT / "templates"

log = logging.getLogger("hatring")


def _load_config() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text())


def _load_dataset(cfg) -> list[dict]:
    cand = DATA / "candidates.json"
    seed = DATA / "seed.json"
    src = cand if cand.exists() else seed
    return json.loads(src.read_text())


def _attach_watchlist_fec_ids(records, cfg):
    """Seed fec_ids onto records from config so FEC matching is deterministic."""
    cfgmap = {c["id"]: c for c in cfg.get("watchlist", [])}
    for r in records:
        c = cfgmap.get(r["id"])
        if c and c.get("fec_ids"):
            r.setdefault("fec_ids", [])
            for fid in c["fec_ids"]:
                if fid not in r["fec_ids"]:
                    r["fec_ids"].append(fid)


def run(args):
    cfg = _load_config()
    today = date.fromisoformat(args.today) if args.today else date.today()
    records = _load_dataset(cfg)
    _attach_watchlist_fec_ids(records, cfg)
    watchlist = cfg.get("watchlist", [{"id": r["id"], "name": r["name"]} for r in records])
    # ensure every dataset record is matchable even if not in config
    have = {w["id"] for w in watchlist}
    for r in records:
        if r["id"] not in have:
            watchlist.append({"id": r["id"], "name": r["name"],
                              "aliases": [r["name"]]})

    ds = Dataset(records, today=today)

    fec_signals, news_items = [], []
    if args.offline:
        fx = Path(args.fixtures)
        if (args.fec or args.all) and (fx / "fec_signals.json").exists():
            raw = json.loads((fx / "fec_signals.json").read_text())
            fec_signals = [fecmod.FecSignal(**r) for r in raw]
        if (args.news or args.all) and (fx / "news_items.json").exists():
            raw = json.loads((fx / "news_items.json").read_text())
            news_items = [newsmod.NewsItem(**r) for r in raw]
    else:
        if args.fec or args.all:
            try:
                fec_signals = fecmod.FecClient().signals(cfg.get("cycle", 2028))
            except Exception as e:                       # never let one source kill the run
                log.error("FEC fetch failed: %s", e)
        if args.news or args.all:
            try:
                news_items = newsmod.fetch_all(
                    watchlist, cfg.get("broad_queries", []),
                    throttle=cfg.get("news_throttle", 1.0))
            except Exception as e:
                log.error("news fetch failed: %s", e)

    classified = clf.classify_batch(news_items, watchlist) if news_items else []

    if fec_signals or classified:
        ds.update(classified, fec_signals, DATA / "signals.jsonl",
                  fec_autocreate=cfg.get("fec_autocreate", False))
        DATA.mkdir(exist_ok=True)
        (DATA / "candidates.json").write_text(json.dumps(ds.records, indent=2, ensure_ascii=False))
        if ds.review:
            (DATA / "review_queue.json").write_text(json.dumps(ds.review, indent=2, ensure_ascii=False))
        log.info("dataset: %d records written", len(ds.records))
    else:
        log.info("no new signals fetched; dataset unchanged")

    if args.build or args.all:
        out = Path(args.out) if args.out else DATA / "dashboard.html"
        render(DATA / "candidates.json" if (DATA / "candidates.json").exists() else DATA / "seed.json",
               TEMPLATES, out, built=today)
        print(f"built dashboard -> {out}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="hatring.pipeline", description=__doc__)
    p.add_argument("--all", action="store_true", help="FEC + news + build")
    p.add_argument("--fec", action="store_true", help="ingest FEC filings")
    p.add_argument("--news", action="store_true", help="ingest news RSS")
    p.add_argument("--build", action="store_true", help="rebuild dashboard HTML")
    p.add_argument("--offline", action="store_true", help="use fixtures, no network")
    p.add_argument("--fixtures", default="tests/fixtures")
    p.add_argument("--out", help="dashboard output path")
    p.add_argument("--today", help="override 'today' (YYYY-MM-DD) for recency maths")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s")
    if not any([a.all, a.fec, a.news, a.build]):
        p.error("nothing to do: pass --all, or some of --fec/--news/--build")
    run(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
