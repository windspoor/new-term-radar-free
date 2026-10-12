import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from radar import __main__ as cli, aisa, free, free_sources as src, free_state as state

NOW = datetime(2026, 10, 12, 2, 0, tzinfo=timezone.utc)


def item(name="NewModel", published=None, **extra):
    return {"id": "hf:qwen/" + name.lower(), "title": name, "kind": "model",
            "url": "https://huggingface.co/Qwen/" + name,
            "published": published or (NOW - timedelta(hours=1)).isoformat(),
            "summary": "An open model", "source": "HF 官方发布", "official": True,
            "reason": "Qwen 新上传的模型", **extra}


def test_first_scan_backfills_recent_but_not_old_models():
    data = state.fresh()
    assert state.ingest(data, "hf:Qwen", [item(), item("Old", (NOW - timedelta(days=4)).isoformat())], NOW) == 1
    assert len(data["events"]) == 1


def test_model_family_is_deduplicated_across_sizes_and_runs():
    assert src.model_name("Qwen/Qwen3.9-27B-Instruct-GGUF") == "Qwen3.9"
    data = state.fresh()
    assert state.ingest(data, "hf:Qwen", [item(), item()], NOW) == 1
    assert state.ingest(data, "hf:Qwen", [item()], NOW + timedelta(hours=2)) == 0
    assert data["events"][item()["id"]]["first_seen"] == NOW.isoformat(timespec="seconds")


def test_missed_runs_use_last_success_instead_of_a_fixed_24h_window():
    data = state.fresh()
    state.ingest(data, "hf:Qwen", [], NOW)
    later = NOW + timedelta(days=4)
    release = item("Catchup", (NOW + timedelta(days=1)).isoformat())
    assert state.ingest(data, "hf:Qwen", [release], later) == 1


def test_newly_configured_source_does_not_flood_old_releases():
    data = state.fresh()
    state.ingest(data, "hf:Qwen", [], NOW)
    assert state.ingest(data, "release:openai/codex", [item("Old", (NOW - timedelta(days=20)).isoformat())], NOW) == 0


def test_trending_baseline_is_not_presented_as_new_and_reentry_is_deduplicated():
    data = state.fresh()
    a = item(official=False, hot=True)
    assert state.ingest(data, "hf:trending", [a], NOW) == 0
    assert state.ingest(data, "hf:trending", [a, item("Trend", official=False, hot=True)], NOW + timedelta(hours=2)) == 1
    assert state.ingest(data, "hf:trending", [a], NOW + timedelta(hours=4)) == 0


def test_star_growth_is_normalized_to_elapsed_time():
    data = state.fresh()
    row = {**item(), "id": "gh:openai/newtool", "kind": "tool", "url": "https://github.com/openai/newtool", "stars": 20}
    state.ingest(data, "org:openai", [row], NOW)
    state.ingest(data, "org:openai", [{**row, "stars": 32}], NOW + timedelta(hours=2))
    event = data["events"][row["id"]]
    assert event["growth_per_day"] == 144
    assert event["hot_since"] == (NOW + timedelta(hours=2)).isoformat(timespec="seconds")


def test_unknown_fields_from_source_are_not_persisted():
    data = state.fresh()
    state.ingest(data, "hf:Qwen", [item(token="private", private_site="private.example")], NOW)
    assert "private" not in json.dumps(data)


@pytest.mark.parametrize("field", ["gsc", "sitemaps", "api_key", "known_terms", "website", "email"])
def test_public_config_rejects_sensitive_or_legacy_settings(tmp_path, field):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"mode": "free-public", field: "private.example"}), encoding="utf-8")
    with pytest.raises(ValueError):
        free.config(path)


def test_config_rejects_unapproved_accounts_and_queries(tmp_path):
    path = tmp_path / "config.json"
    for config in ({"hf_authors": ["private-user"]}, {"feeds": ["private.example"]},
                   {"discovery": {"queries": ["user:private-user"]}}):
        path.write_text(json.dumps({"mode": "free-public", **config}), encoding="utf-8")
        with pytest.raises(ValueError):
            free.config(path)


@pytest.mark.parametrize("url", ["https://private.example/rss", "http://huggingface.co/api/models",
                                 "https://secret@github.com/x", "https://api.github.com.evil.example/repos/x"])
def test_network_allowlist_rejects_private_or_untrusted_destinations(url):
    with pytest.raises(ValueError):
        src.safe_url(url)


def test_github_token_is_not_sent_to_hugging_face(monkeypatch):
    requests = []
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, n):
            return b"[]"
    class Opener:
        def open(self, request, **kwargs):
            requests.append(request)
            return Response()
    monkeypatch.setenv("GITHUB_TOKEN", "secret-test-token")
    monkeypatch.setattr(src.urllib.request, "build_opener", lambda *args: Opener())
    src.fetch("https://huggingface.co/api/models")
    src.fetch("https://api.github.com/orgs/QwenLM/repos", github=True)
    assert not requests[0].has_header("Authorization")
    assert requests[1].get_header("Authorization") == "Bearer secret-test-token"


def test_private_repositories_and_forks_are_never_candidates():
    repo = {"full_name": "a/secret", "created_at": NOW.isoformat(), "private": True}
    assert src.repo_row(repo, official=True, reason="x") is None
    assert src.repo_row({**repo, "private": False, "fork": True}, official=True, reason="x") is None


def test_rss_and_atom_parsers_handle_dates_and_public_links():
    rss = b'<rss><channel><item><title>Introducing NewModel</title><link>https://openai.com/news/model?tracking=x</link><pubDate>Mon, 12 Oct 2026 01:00:00 GMT</pubDate></item></channel></rss>'
    row = src.parse_feed(rss, "OpenAI")[0]
    assert row["url"] == "https://openai.com/news/model"
    assert row["published"] == "2026-10-12T01:00:00+00:00"
    atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>New AI agent</title><link href="https://simonwillison.net/2026/Oct/12/ai/"/><updated>2026-10-12T01:00:00Z</updated></entry></feed>'
    assert src.parse_feed(atom, "Simon Willison")[0]["release"]


def test_report_escapes_untrusted_markdown_and_uses_shanghai_dates():
    data = state.fresh()
    state.ingest(data, "hf:Qwen", [item(title="[Click](https://evil.example) | <script>x</script>")], NOW)
    text = state.markdown(state.report_data(data, NOW, 60))
    assert "10:00" in text
    assert "<script>" not in text and "https://evil.example" not in text
    assert "\\[Click\\]" in text


def test_state_roundtrip_and_no_silent_reset_of_corrupt_history(tmp_path):
    path = tmp_path / "free-state.json"
    data = state.fresh()
    state.ingest(data, "hf:Qwen", [item()], NOW)
    state.atomic_json(path, data)
    assert state.load_state(path) == data
    path.write_text('{"private_site":"private.example"}', encoding="utf-8")
    with pytest.raises(ValueError):
        state.load_state(path)


def test_partial_failure_preserves_source_watermark_and_never_logs_secrets(monkeypatch, capsys):
    cfg = {"initial_lookback_hours": 48, "retention_days": 30}
    data = state.fresh()
    state.ingest(data, "hf:Qwen", [item()], NOW)
    def broken():
        raise OSError("token=secret private.example")
    monkeypatch.setattr(free, "jobs", lambda *a: {"hf:Qwen": broken, "hf:trending": lambda: []})
    assert free.collect(cfg, data, NOW + timedelta(hours=2)) == 1
    assert data["sources"]["hf:Qwen"]["last_success"] == NOW.isoformat(timespec="seconds")
    assert "secret" not in capsys.readouterr().out
    assert item()["id"] in data["events"]


@pytest.mark.parametrize("alias", ["free", "daily", "sources", "collect"])
def test_all_cli_aliases_run_without_paid_calls_even_if_keys_exist(tmp_path, monkeypatch, alias):
    from radar import goanyapi, notify
    def paid(*args, **kwargs):
        pytest.fail("A paid or private integration was invoked")
    monkeypatch.setenv("AISA_API_KEY", "paid-key-that-must-be-ignored")
    monkeypatch.setenv("GOANYAPI_API_KEY", "paid-key-that-must-be-ignored")
    monkeypatch.setattr(aisa, "call_all", paid)
    monkeypatch.setattr(aisa, "chat", paid)
    monkeypatch.setattr(goanyapi, "call", paid)
    monkeypatch.setattr(notify, "send", paid)
    monkeypatch.setattr(free, "jobs", lambda *args: {"hf:Qwen": lambda: [item()]})
    path = tmp_path / "config.json"
    path.write_text('{"mode":"free-public"}', encoding="utf-8")
    out = tmp_path / "reports/latest.md"
    assert cli.main([alias, "--config", str(path), "--state", str(tmp_path / "state"), "--out", str(out)]) == 0
    assert "付费 API 调用：**0**" in out.read_text(encoding="utf-8")
    assert json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))["paid_api_calls"] == 0


def test_cloud_restore_failure_does_not_restart_history(tmp_path, monkeypatch):
    from scripts import cloud_free
    class API:
        def head(self):
            return "existing-commit"
        def call(self, *args):
            raise urllib.error.HTTPError("https://api.github.com/x", 503, "down", None, None)
    monkeypatch.setattr(cloud_free, "ROOT", tmp_path)
    with pytest.raises(urllib.error.HTTPError):
        cloud_free.restore(API())
    assert not (tmp_path / "state/free-state.json").exists()


def test_cloud_publish_only_uploads_allowlisted_generated_files(tmp_path, monkeypatch):
    from scripts import cloud_free
    data = state.fresh()
    state.ingest(data, "hf:Qwen", [item()], NOW)
    state.atomic_json(tmp_path / "state/free-state.json", data)
    state.atomic_json(tmp_path / "reports/latest.json", state.report_data(data, NOW, 60))
    (tmp_path / "gsc_token.json").write_text("DO NOT UPLOAD")
    calls = []
    class API:
        repo = "public/radar"
        def head(self):
            return None
        def call(self, path, method="GET", body=None):
            calls.append((path, body))
            return {"sha": "s"}
    monkeypatch.setattr(cloud_free, "ROOT", tmp_path)
    cloud_free.publish(API(), daily=True)
    tree = next(body for path, body in calls if path == "/git/trees")
    assert {r["path"] for r in tree["tree"]} == {".radar/free-state.json", "latest.md", "latest.json", "reports/2026-10-12.md", "reports/2026-10-12.json"}
    assert "DO NOT UPLOAD" not in str(calls)


def test_asset_heavy_releases_are_paginated_instead_of_skipped(monkeypatch):
    urls = []
    def fetch(url, **kwargs):
        urls.append(url)
        if "releases" not in url:
            return {"private": False}
        if "per_page=10" in url:
            raise src.ResponseTooLarge("large")
        return [{"tag_name": "v1", "html_url": "https://github.com/openai/codex/releases/tag/v1",
                 "published_at": NOW.isoformat()}]
    monkeypatch.setattr(src, "fetch", fetch)
    assert len(src.gh_releases("openai/codex")) == 1
    assert "per_page=2&page=1" in urls[-1]


def test_repeated_releases_are_merged_only_in_report_not_in_history():
    data = state.fresh()
    first = {**item(), "id": "release:openai/codex:v1", "kind": "tool"}
    second = {**first, "id": "release:openai/codex:v2", "published": NOW.isoformat()}
    state.ingest(data, "release:openai/codex", [first, second], NOW)
    report = state.report_data(data, NOW, 60)
    assert len(data["events"]) == 2
    assert report["total"] == 1 and report["events"][0]["id"].endswith("v2")
    assert "已合并" in state.markdown(report)


def test_multiple_github_searches_do_not_inflate_confidence():
    event = {**item(official=False), "kind": "tool", "sources": ["discover:a", "discover:b", "discover:c"]}
    assert state.rank(event) == state.rank({**event, "sources": ["discover:a"]})


def test_html_error_page_is_not_reported_as_a_healthy_feed():
    with pytest.raises(ValueError):
        src.parse_feed(b"<html><body>blocked</body></html>", "OpenAI")
