"""Read-only public sources. No inference, paid API, or private site integration."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

HF_AUTHORS = {
    "Qwen", "deepseek-ai", "moonshotai", "MiniMaxAI", "zai-org", "mistralai",
    "google", "meta-llama", "black-forest-labs", "Lightricks", "Wan-AI", "nvidia",
}
GH_ORGS = {
    "QwenLM", "deepseek-ai", "huggingface", "google-deepmind", "anthropics",
    "moonshotai", "MiniMax-AI", "THUDM", "mistralai", "openai", "langchain-ai",
}
GH_RELEASES = {
    "huggingface/transformers", "langchain-ai/langchain", "langchain-ai/langgraph",
    "ollama/ollama", "ggml-org/llama.cpp", "anthropics/claude-code", "openai/codex",
    "Comfy-Org/ComfyUI", "browser-use/browser-use", "pydantic/pydantic-ai",
}
FEEDS = {
    "OpenAI": "https://openai.com/news/rss.xml",
    "Hugging Face": "https://huggingface.co/blog/feed.xml",
    "Google AI": "https://blog.google/technology/ai/rss/",
    "Simon Willison": "https://simonwillison.net/atom/everything/",
}
HOSTS = {
    "api.github.com", "github.com", "huggingface.co", "openai.com", "www.openai.com",
    "blog.google", "simonwillison.net", "www.simonwillison.net",
}
UA = "new-term-radar-free/1.0 (+https://github.com/zhuyansen/new-term-radar)"
AI = re.compile(r"\b(ai|llm|agent|agents|mcp|diffusion|transformer|transformers|inference|"
                r"qwen|deepseek|claude|gpt|llama|gemma|comfyui|machine learning|language model)\b", re.I)
RELEASE = re.compile(r"\b(introducing|introduce|launch|launches|released?|releases|announcing|"
                     r"announce|announces|new|model|models|open.source|available|preview)\b|发布|推出|模型", re.I)
NOISE = re.compile(r"[-_.](gguf|ggml|awq|gptq|fp8|fp16|bf16|int[48]|mlx|exl2|onnx|"
                   r"instruct|chat|it|base|preview|turbo|lora|q\d\w*|\d+(\.\d+)?b|a\d+(\.\d+)?b)(?=[-_.]|$)", re.I)


class ResponseTooLarge(ValueError):
    pass


def stamp(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
        except (ValueError, TypeError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def safe_url(value: str) -> str:
    p = urllib.parse.urlsplit(value)
    if p.scheme != "https" or p.hostname not in HOSTS or p.username or p.password or p.port not in (None, 443):
        raise ValueError("Only approved public HTTPS sources are allowed")
    if p.query or p.fragment:
        # Public item links never retain tracking parameters, credentials, or fragments.
        return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, "", ""))
    return value


def plain(value: str, limit: int = 240) -> str:
    value = re.sub(r"<[^>]*>", " ", value or "")
    value = re.sub(r"https?://\S+", "", value)
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    return " ".join(value.split())[:limit]


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl)
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if result is not None and urllib.parse.urlsplit(newurl).hostname != "api.github.com":
            result.remove_header("Authorization")
        return result


def fetch(url: str, *, github: bool = False, as_json: bool = True):
    safe_url(url)
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json" if github else "application/json, application/xml, text/xml"}
    if github:
        if urllib.parse.urlsplit(url).hostname != "api.github.com":
            raise ValueError("GitHub credentials cannot leave api.github.com")
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    opener = urllib.request.build_opener(PublicRedirect())
    with opener.open(urllib.request.Request(url, headers=headers), timeout=20) as response:
        body = response.read(4 * 1024 * 1024 + 1)
    if len(body) > 4 * 1024 * 1024:
        raise ResponseTooLarge("Public response exceeds size limit")
    return json.loads(body) if as_json else body


def model_name(model_id: str) -> str:
    name = model_id.split("/")[-1]
    for _ in range(4):
        name = NOISE.sub("", name)
    return re.sub(r"[-_]+", " ", name).strip()


def hf_models(author: str | None = None) -> list[dict]:
    params = {"sort": "createdAt" if author else "trendingScore", "direction": -1,
              "limit": 50 if author else 100, "full": "true"}
    if author:
        params["author"] = author
    data = fetch("https://huggingface.co/api/models?" + urllib.parse.urlencode(params))
    out = []
    for rank, model in enumerate(data, 1):
        mid = model.get("id", "")
        if model.get("private") or "/" not in mid or (author and mid.split("/")[0].lower() != author.lower()):
            continue
        base = model_name(mid)
        owner = mid.split("/")[0]
        out.append({"id": "hf:" + owner.lower() + "/" + base.lower(), "title": base,
                    "kind": "model", "url": safe_url("https://huggingface.co/" + urllib.parse.quote(mid, safe="/")),
                    "published": stamp(model.get("createdAt")),
                    "summary": plain(f"{mid} · {model.get('pipeline_tag') or '模型'} · ♥{model.get('likes', 0)}"),
                    "source": "HF 官方发布" if author else "HF 热门榜", "official": bool(author),
                    "reason": f"{author} 新上传的模型" if author else f"进入 HF 热门榜 #{rank}",
                    "rank": rank, "likes": int(model.get("likes") or 0), "hot": not author})
    return out


def repo_row(repo: dict, *, official: bool, reason: str) -> dict | None:
    if repo.get("private") is not False or repo.get("fork") or repo.get("archived"):
        return None
    name = repo["full_name"]
    return {"id": "gh:" + name.lower(), "title": name, "kind": "tool",
            "url": safe_url("https://github.com/" + name), "published": stamp(repo.get("created_at")),
            "summary": plain(repo.get("description") or "暂无项目描述，点击原始链接查看"),
            "source": "GitHub 官方仓库" if official else "GitHub 新项目",
            "official": official, "reason": reason, "stars": int(repo.get("stargazers_count") or 0)}


def gh_org(org: str) -> list[dict]:
    data = fetch(f"https://api.github.com/orgs/{org}/repos?type=public&sort=created&direction=desc&per_page=100", github=True)
    return [row for repo in data if (row := repo_row(repo, official=True, reason=f"{org} 创建新仓库"))]


def gh_releases(repo: str) -> list[dict]:
    metadata = fetch(f"https://api.github.com/repos/{repo}", github=True)
    if metadata.get("private") is not False:
        raise ValueError("Private repositories are not collected")
    try:
        data = fetch(f"https://api.github.com/repos/{repo}/releases?per_page=10", github=True)
    except ResponseTooLarge:
        # Asset-heavy projects can exceed the bounded response size. Read small pages
        # rather than lifting the memory limit or dropping their recent releases.
        data = []
        for page in range(1, 6):
            chunk = fetch(f"https://api.github.com/repos/{repo}/releases?per_page=2&page={page}", github=True)
            data.extend(chunk)
            if len(chunk) < 2:
                break
    out = []
    for release in data:
        if release.get("draft"):
            continue
        tag = release.get("tag_name", "")
        out.append({"id": "release:" + repo.lower() + ":" + tag, "title": f"{repo} · {tag}",
                    "kind": "tool", "url": safe_url(release["html_url"]),
                    "published": stamp(release.get("published_at")), "summary": plain(release.get("name") or tag),
                    "source": "GitHub Releases", "official": True,
                    "reason": "项目发布新版本" + ("（预发布）" if release.get("prerelease") else "")})
    return out


def gh_discovery(days: int, min_stars: int, query: str, now: datetime) -> list[dict]:
    from datetime import timedelta
    since = (now - timedelta(days=days)).date().isoformat()
    q = f"{query} created:>={since} stars:>={min_stars} is:public fork:false archived:false"
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode({"q": q, "sort": "stars", "order": "desc", "per_page": 100})
    data = fetch(url, github=True)
    out = []
    for repo in data.get("items", []):
        context = repo.get("full_name", "") + " " + (repo.get("description") or "") + " " + " ".join(repo.get("topics") or [])
        if AI.search(context) and (row := repo_row(repo, official=False, reason="新 AI 项目达到观察门槛")):
            out.append(row)
    return out


def parse_feed(body: bytes, label: str) -> list[dict]:
    root = ET.fromstring(body)
    atom = "{http://www.w3.org/2005/Atom}"
    if root.tag not in ("rss", atom + "feed"):
        raise ValueError("Response is not an RSS or Atom feed")
    entries = root.findall("./channel/item") or root.findall(atom + "entry")
    out = []
    for entry in entries[:80]:
        title = entry.findtext("title") or entry.findtext(atom + "title") or ""
        link = entry.findtext("link")
        if not link:
            link = next((node.get("href") for node in entry.findall(atom + "link")
                         if node.get("rel", "alternate") == "alternate"), None)
        if not title or not link:
            continue
        try:
            url = safe_url(link)
        except ValueError:
            continue
        # Independent blogs must mention AI; official AI feeds are already scoped.
        if label == "Simon Willison" and not AI.search(title + " " + plain(entry.findtext(atom + "summary") or "")):
            continue
        publication = entry.findtext("pubDate") or entry.findtext(atom + "published") or entry.findtext(atom + "updated")
        is_release = bool(RELEASE.search(title))
        out.append({"id": "feed:" + url, "title": plain(title, 180), "kind": "news", "url": url,
                    "published": stamp(publication), "summary": plain(entry.findtext("description") or entry.findtext(atom + "summary") or ""),
                    "source": label, "official": label != "Simon Willison", "release": is_release,
                    "reason": "发布消息" if is_release else "AI 相关动态"})
    return out


def rss(label: str) -> list[dict]:
    return parse_feed(fetch(FEEDS[label], as_json=False), label)
