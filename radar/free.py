"""The free edition's CLI. Its network paths only read approved public sources."""
from __future__ import annotations

import argparse
import json
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from . import free_sources as src, free_state as state

FIELDS = {"mode", "hf_authors", "github_orgs", "github_releases", "feeds", "discovery",
          "retention_days", "report_limit", "initial_lookback_hours"}
QUERIES = {"topic:artificial-intelligence", "topic:llm", "topic:ai-agents"}


def config(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) - FIELDS or raw.get("mode") != "free-public":
        raise ValueError("Only a free-public config is accepted; private sites, credentials and legacy config are forbidden")
    for key, allowed in (("hf_authors", src.HF_AUTHORS), ("github_orgs", src.GH_ORGS),
                         ("github_releases", src.GH_RELEASES), ("feeds", set(src.FEEDS))):
        values = raw.get(key, [])
        if not isinstance(values, list) or any(not isinstance(v, str) or v not in allowed for v in values):
            raise ValueError(f"{key} must contain approved public sources only")
        raw[key] = list(dict.fromkeys(values))
    for key, default, low, high in (("retention_days", 30, 7, 90), ("report_limit", 60, 5, 200),
                                   ("initial_lookback_hours", 48, 1, 72)):
        value = raw.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"Invalid bounded setting: {key}")
        raw[key] = value
    discovery = raw.get("discovery", {})
    if not isinstance(discovery, dict) or set(discovery) - {"queries", "window_days", "min_stars"}:
        raise ValueError("Unexpected discovery setting")
    queries = discovery.get("queries", [])
    if not isinstance(queries, list) or any(q not in QUERIES for q in queries):
        raise ValueError("Only public AI topic searches are allowed")
    for key, default, low, high in (("window_days", 14, 1, 30), ("min_stars", 20, 5, 300)):
        value = discovery.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"Invalid discovery setting: {key}")
        discovery[key] = value
    discovery["queries"] = list(dict.fromkeys(queries))
    raw["discovery"] = discovery
    return raw


def jobs(cfg: dict, now: datetime) -> dict:
    from functools import partial
    tasks = {"hf:" + a: partial(src.hf_models, a) for a in cfg["hf_authors"]}
    tasks["hf:trending"] = src.hf_models
    tasks.update({"org:" + a: partial(src.gh_org, a) for a in cfg["github_orgs"]})
    tasks.update({"release:" + r: partial(src.gh_releases, r) for r in cfg["github_releases"]})
    tasks.update({"feed:" + label: partial(src.rss, label) for label in cfg["feeds"]})
    for q in cfg["discovery"]["queries"]:
        tasks["discover:" + q] = partial(src.gh_discovery, cfg["discovery"]["window_days"],
                                        cfg["discovery"]["min_stars"], q, now)
    return tasks


def collect(cfg: dict, data: dict, now: datetime) -> int:
    tasks = jobs(cfg, now)
    results = {}
    # Only network reads run concurrently. State mutations are applied in stable order.
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {pool.submit(fn): name for name, fn in tasks.items()}
        for future in as_completed(pending):
            name = pending[future]
            try:
                results[name] = (future.result(), None)
            except Exception as exc:
                # Never put request URLs, headers, token values, or raw exception bodies in public logs.
                error = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
                results[name] = (None, error)
    successes = 0
    for name in sorted(results):
        rows, error = results[name]
        if error:
            previous = data["sources"].get(name, {})
            data["sources"][name] = {**previous, "status": "error", "error": error, "new": 0}
            print(f"{name}: {error}")
        else:
            added = state.ingest(data, name, rows, now, cfg["initial_lookback_hours"], cfg["retention_days"])
            successes += 1
            print(f"{name}: checked={len(rows)} new={added}")
    data["last_attempt"] = now.isoformat(timespec="seconds")
    if successes:
        data["last_success"] = data["last_attempt"]
    state.prune(data, now, cfg["retention_days"])
    return successes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="免费公开版新词雷达；不读取私人站点，不调用付费服务")
    parser.add_argument("command", nargs="?", choices=["collect", "daily"], default="daily")
    parser.add_argument("--config", default="config.public.json")
    parser.add_argument("--state", default="state/free")
    parser.add_argument("--out", default="reports/latest.md")
    parser.add_argument("--report-only", action="store_true", help="只从历史记录生成报告，不采集")
    args = parser.parse_args(argv)
    try:
        cfg = config(Path(args.config))
        path = Path(args.state) / "free-state.json"
        data = state.load_state(path)
        now = datetime.now(timezone.utc)
        successes = 1 if args.report_only else collect(cfg, data, now)
        state.atomic_json(path, data)
        payload = state.report_data(data, now, cfg["report_limit"])
        text = state.markdown(payload)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        state.atomic_json(out.with_suffix(".json"), payload)
        if args.command == "daily":
            day = now.astimezone(state.SHANGHAI).date().isoformat()
            out.with_name(day + ".md").write_text(text, encoding="utf-8")
            state.atomic_json(out.with_name(day + ".json"), payload)
        print(f"Free report: {payload['total']} leads; paid API calls=0")
        return 0 if successes else 1
    except (ValueError, OSError, KeyError, TypeError) as exc:
        # Config values and paths may be private. Only a generic category goes to stdout.
        print(f"Free radar stopped: {type(exc).__name__}; check public config/state locally")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
