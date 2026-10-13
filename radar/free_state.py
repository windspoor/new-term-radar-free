"""Bounded public event history, deterministic ranking, and Chinese reports."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .free_sources import safe_url, stamp, plain

SHANGHAI = timezone(timedelta(hours=8))
EVENT_FIELDS = {"id", "title", "kind", "url", "published", "summary", "source", "official",
                "reason", "rank", "likes", "hot", "release", "stars", "first_seen", "last_seen",
                "sources", "reasons", "score", "growth_per_day", "hot_since"}


def fresh() -> dict:
    return {"version": 1, "events": {}, "seen": {}, "stars": {}, "sources": {},
            "last_attempt": None, "last_success": None}


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    if len(payload.encode()) > 2 * 1024 * 1024:
        raise ValueError("Public state exceeds the 2 MB safety limit")
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(payload, encoding="utf-8")
    temp.replace(path)


def load_state(path: Path) -> dict:
    if not path.exists():
        return fresh()
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("State is too large")
    data = json.loads(path.read_text(encoding="utf-8"))
    if set(data) != set(fresh()) or data["version"] != 1:
        raise ValueError("Unsupported public state; private or legacy state is never restored")
    for name in ("events", "seen", "stars", "sources"):
        if not isinstance(data[name], dict):
            raise ValueError("Invalid public state")
    for event in data["events"].values():
        if set(event) - EVENT_FIELDS or event.get("kind") not in {"model", "tool", "news"}:
            raise ValueError("Unexpected fields in public event")
        safe_url(event["url"])
    return data


def rank(event: dict) -> int:
    score = 55 if event.get("official") else 25
    score += 15 if event["kind"] == "model" else 10 if event["kind"] == "tool" else 0
    score += 10 if event.get("release") else 0
    # Three topic searches on the same platform are one signal, not three endorsements.
    families = {"github" if s.startswith(("org:", "release:", "discover:"))
                else "hf" if s.startswith("hf:") else s for s in event.get("sources", [])}
    score += min(15, max(0, len(families) - 1) * 8)
    score += 10 if event.get("hot_since") else 0
    return min(100, score)


def ingest(data: dict, source: str, rows: list[dict], now: datetime,
           bootstrap_hours: int = 48, retention_days: int = 30) -> int:
    """First scan backfills only recent official items; subsequent scans catch missed runs."""
    iso = now.isoformat(timespec="seconds")
    previous = data["sources"].get(source, {})
    first = not previous.get("last_success")
    cutoff = now - timedelta(hours=bootstrap_hours) if first else max(
        now - timedelta(days=retention_days),
        datetime.fromisoformat(previous["last_success"]) - timedelta(hours=24))
    added = 0
    for raw in rows:
        row = {k: v for k, v in raw.items() if k in EVENT_FIELDS}
        if not row.get("id") or not row.get("url") or row.get("kind") not in {"model", "tool", "news"}:
            continue
        row["url"] = safe_url(row["url"])
        row["title"] = plain(row.get("title", ""), 180)
        row["summary"] = plain(row.get("summary", ""), 240)
        published = stamp(row.get("published"))
        row["published"] = published
        seen_key = source + "|" + row["id"]
        was_seen = seen_key in data["seen"]
        data["seen"][seen_key] = iso
        current = data["events"].get(row["id"])
        is_discovery = source.startswith("discover:")
        recent = published and cutoff <= datetime.fromisoformat(published) <= now + timedelta(hours=1)
        eligible = (bool(recent) or is_discovery or (not first and not published)) and not was_seen
        old_stars = data["stars"].get(row["id"])
        hot = row.get("hot", False)
        growth = None
        if "stars" in row:
            stars = row["stars"]
            if old_stars:
                hours = (now - datetime.fromisoformat(old_stars["at"])).total_seconds() / 3600
                gain = stars - old_stars["value"]
                if hours >= 1:
                    growth = round(gain * 24 / hours, 1)
                    hot |= gain >= 10 and growth >= 50
            elif published:
                age = max((now - datetime.fromisoformat(published)).total_seconds() / 86400, 1)
                hot |= stars >= 50 and stars / age >= 30
            data["stars"][row["id"]] = {"value": stars, "at": iso}
        # Trending arrivals are evidence of attention, not a claim of a new publication.
        if not row.get("official") and row.get("hot"):
            eligible = not was_seen and not first
        if current is None and not eligible:
            continue
        if current is None:
            current = {**row, "first_seen": iso, "last_seen": iso, "sources": [], "reasons": []}
            data["events"][row["id"]] = current
            added += 1
        else:
            for field in ("stars", "likes", "rank"):
                if field in row:
                    current[field] = row[field]
            if row.get("summary"):
                current["summary"] = row["summary"]
            current["official"] |= row.get("official", False)
            current["last_seen"] = iso
        if source not in current["sources"]:
            current["sources"].append(source)
        reason = row.get("reason", "")
        if reason and reason not in current["reasons"]:
            current["reasons"].append(reason)
        if hot and not current.get("hot_since"):
            current["hot_since"] = iso
        if growth is not None:
            current["growth_per_day"] = growth
        current["score"] = rank(current)
    data["sources"][source] = {"status": "ok", "last_success": iso, "checked": len(rows),
                               "new": added, "bootstrap": first}
    return added


def prune(data: dict, now: datetime, retention_days: int) -> None:
    cutoff = (now - timedelta(days=retention_days)).isoformat()
    data["events"] = dict(sorted(
        ((k, v) for k, v in data["events"].items() if v["last_seen"] >= cutoff),
        key=lambda item: item[1]["first_seen"], reverse=True)[:1500])
    seen_cutoff = (now - timedelta(days=90)).isoformat()
    data["seen"] = dict(sorted(((k, v) for k, v in data["seen"].items() if v >= seen_cutoff),
                               key=lambda item: item[1], reverse=True)[:10000])
    data["stars"] = {k: v for k, v in data["stars"].items() if v["at"] >= cutoff}


def local(value: str | None) -> str:
    return datetime.fromisoformat(value).astimezone(SHANGHAI).strftime("%m-%d %H:%M") if value else "未知"


def escape(value: str) -> str:
    # Public source titles are untrusted Markdown; retain readable text, no raw HTML.
    value = plain(value, 240).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()#!|])", r"\\\1", value)


def introduction(row: dict) -> str:
    text = plain(row.get("summary", ""))
    # Old snapshots may still contain model statistics or just a release tag.
    if row.get("kind") == "model" and " · ♥" in text:
        from .free_sources import model_summary
        parts = text.split(" · ")
        text = model_summary(parts[1] if len(parts) > 1 and parts[1] != "模型" else None, parts[0])
    if row["id"].startswith("release:") and text == row["id"].split(":", 2)[-1]:
        text = ""
    if not text or text.startswith("暂无项目描述"):
        return "公开来源暂未提供简介。"
    if len(text) > 180:
        shortened = text[:180]
        if " " in shortened and not re.search(r"[\u4e00-\u9fff]", shortened):
            shortened = shortened.rsplit(" ", 1)[0]
        text = shortened.rstrip("，、；,. ") + "…"
    return text


def report_data(data: dict, now: datetime, limit: int) -> dict:
    cutoff = now - timedelta(hours=24)
    rows = [v for v in data["events"].values()
            if datetime.fromisoformat(v["first_seen"]) >= cutoff or
            (v.get("hot_since") and datetime.fromisoformat(v["hot_since"]) >= cutoff)]
    # Preserve all tags in history, but show only the latest release per repository.
    releases = {}
    other = []
    for row in rows:
        if not row["id"].startswith("release:"):
            other.append(row)
            continue
        repo = row["id"].split(":", 2)[1]
        releases.setdefault(repo, []).append(row)
    for entries in releases.values():
        latest = max(entries, key=lambda r: r.get("published") or r["first_seen"])
        latest = {**latest, "reasons": list(latest["reasons"])}
        if len(entries) > 1:
            latest["reasons"].append(f"另有 {len(entries) - 1} 次版本更新已合并")
        other.append(latest)
    rows = other
    rows.sort(key=lambda r: (-r["score"],
                             -datetime.fromisoformat(r.get("published") or r["first_seen"]).timestamp(),
                             -r.get("stars", 0), r["id"]))
    return {"generated_at": now.isoformat(timespec="seconds"), "timezone": "Asia/Shanghai",
            "last_success": data["last_success"], "total": len(rows), "shown": min(limit, len(rows)),
            "events": rows[:limit], "sources": data["sources"], "paid_api_calls": 0}


def markdown(payload: dict) -> str:
    now = datetime.fromisoformat(payload["generated_at"]).astimezone(SHANGHAI)
    lines = [f"# 新模型与新工具日报 · {now:%Y-%m-%d}", "",
             f"生成时间：{now:%Y-%m-%d %H:%M}（北京时间） · 最近成功采集：{local(payload['last_success'])}", "",
             f"过去 24 小时发现或开始升温 **{payload['total']}** 条，展示 {payload['shown']} 条。付费 API 调用：**0**。", "",
             "首次运行会补录近 48 小时的公开发布与近两周达到观察门槛的项目；首次发现时间不等于发布时间。", "",
             "规则分只用于安排阅读顺序，热度来自平台信号；它们均不代表搜索量或 SEO 收益。", ""]
    groups = [("新模型", lambda r: r["kind"] == "model"),
              ("新工具与版本更新", lambda r: r["kind"] == "tool"),
              ("发布消息与相关动态", lambda r: r["kind"] == "news"),
              ("开始升温", lambda r: bool(r.get("hot_since")))]
    for heading, select in groups:
        rows = [r for r in payload["events"] if select(r)]
        lines.extend([f"## {heading}", ""])
        if not rows:
            lines.extend(["本次没有新的记录。", ""])
            continue
        lines.extend(["| 名称 / 原始链接 | 项目简介 / 主要用途 | 发布时间 | 首次发现 | 为什么值得看 | 规则分 |",
                      "|---|---|---|---|---|---|"])
        for row in rows:
            reasons = "；".join(row["reasons"][:3])
            if "stars" in row:
                reasons += f"；★{row['stars']}"
            if row.get("growth_per_day", 0) > 0:
                reasons += f"；当前约 +{row['growth_per_day']:g} 星/天"
            link = safe_url(row["url"]).replace("(", "%28").replace(")", "%29")
            lines.append(f"| [{escape(row['title'])}]({link}) | {escape(introduction(row))} | {local(row['published'])} | "
                         f"{local(row['first_seen'])} | {escape(reasons)} | {row['score']} |")
        lines.append("")
    lines.extend(["## 采集状态", "", "| 来源 | 状态 | 最近成功 | 检查 / 新增 |", "|---|---|---|---|"])
    for label, source in sorted(payload["sources"].items()):
        state = "正常" if source["status"] == "ok" else "失败：" + source.get("error", "来源暂不可用")
        lines.append(f"| {escape(label)} | {escape(state)} | {local(source.get('last_success'))} | "
                     f"{source.get('checked', 0)} / {source.get('new', 0)} |")
    lines.extend(["", "数据仅来自公开发布、公开仓库和公开模型。没有接入私人网站、Search Console、广告账号或邮件地址。", ""])
    return "\n".join(lines)
