"""Restore and publish only generated public data to a separate GitHub branch.

Uses the short-lived token of the current Actions job. No artifacts, cache billing,
personal access token, user identity, personal config, or raw log uploads.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from radar import free, free_state

BRANCH = "radar-data"
ROOT = Path(".runtime")


class GitHub:
    def __init__(self):
        self.repo = os.environ.get("GITHUB_REPOSITORY", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repo):
            raise ValueError("Missing repository identity")
        self.token = os.environ.get("GITHUB_TOKEN", "")
        if not self.token:
            raise ValueError("An Actions job token is required")

    def call(self, path: str, method: str = "GET", body: dict | None = None):
        if not path.startswith("/") and path != "":
            raise ValueError("Invalid GitHub path")
        if ".." in path:
            raise ValueError("Invalid GitHub path")
        payload = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request("https://api.github.com/repos/" + self.repo + path,
            data=payload, method=method, headers={"Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json", "Content-Type": "application/json", "User-Agent": "new-term-radar-free"})
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            return json.load(response)

    def head(self) -> str | None:
        try:
            return self.call("/git/ref/heads/" + BRANCH)["object"]["sha"]
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise


def restore(api: GitHub) -> None:
    if api.head() is None:
        print("First cloud run: initializing public history")
        return
    # Transient errors or missing state on an existing branch must never reset history.
    data = api.call("/contents/.radar/free-state.json?ref=" + BRANCH)
    if data.get("encoding") == "none" and data.get("sha"):
        data = api.call("/git/blobs/" + data["sha"])
    if data.get("encoding") != "base64":
        raise ValueError("Unexpected state response")
    decoded = base64.b64decode(data["content"], validate=False)
    if len(decoded) > 2 * 1024 * 1024:
        raise ValueError("State is too large")
    path = ROOT / "state/free-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(decoded)
    free_state.load_state(path)
    print("Restored public history")


def publish(api: GitHub, daily: bool) -> None:
    # Explicit file allowlist: never enumerate the workspace for publication.
    free_state.load_state(ROOT / "state/free-state.json")
    payload = json.loads((ROOT / "reports/latest.json").read_text(encoding="utf-8"))
    if set(payload) != {"generated_at", "timezone", "last_success", "total", "shown", "events", "sources", "paid_api_calls"}:
        raise ValueError("Unexpected report schema")
    if payload["paid_api_calls"] != 0:
        raise ValueError("Paid outputs cannot be published")
    report = free_state.markdown(payload)
    files = {".radar/free-state.json": (ROOT / "state/free-state.json").read_text(encoding="utf-8"),
             "latest.md": report, "latest.json": json.dumps(payload, ensure_ascii=False, indent=2) + "\n"}
    day = datetime.fromisoformat(payload["generated_at"]).astimezone(free_state.SHANGHAI).date().isoformat()
    if daily:
        files["reports/" + day + ".md"] = report
        files["reports/" + day + ".json"] = files["latest.json"]
    head = api.head()
    previous_tree = api.call("/git/commits/" + head)["tree"]["sha"] if head else None
    tree_entries = []
    for path, content in files.items():
        blob = api.call("/git/blobs", "POST", {"content": content, "encoding": "utf-8"})
        tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    if previous_tree:
        cutoff = (datetime.fromisoformat(payload["generated_at"]).astimezone(free_state.SHANGHAI).date() - timedelta(days=30)).isoformat()
        for entry in api.call("/git/trees/" + previous_tree + "?recursive=1")["tree"]:
            if re.fullmatch(r"reports/\d{4}-\d{2}-\d{2}\.(md|json)", entry["path"]) and entry["path"][8:18] < cutoff:
                tree_entries.append({"path": entry["path"], "mode": "100644", "type": "blob", "sha": None})
    tree_body = {"tree": tree_entries}
    if previous_tree:
        tree_body["base_tree"] = previous_tree
    tree = api.call("/git/trees", "POST", tree_body)
    commit = api.call("/git/commits", "POST", {
        "message": f"{'Daily report' if daily else 'Collect public signals'} {day} [skip ci]",
        "tree": tree["sha"], "parents": [head] if head else [],
        "author": {"name": "github-actions[bot]", "email": "41898282+github-actions[bot]@users.noreply.github.com"}})
    if head:
        api.call("/git/refs/heads/" + BRANCH, "PATCH", {"sha": commit["sha"], "force": False})
    else:
        api.call("/git/refs", "POST", {"ref": "refs/heads/" + BRANCH, "sha": commit["sha"]})
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write(report)
    print(f"Published public report: https://github.com/{api.repo}/blob/{BRANCH}/latest.md")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["collect", "daily"])
    args = parser.parse_args()
    try:
        api = GitHub()
        if api.call("").get("private") is not False:
            raise ValueError("Cloud free edition requires a public repository")
        restore(api)
        code = free.main([args.command, "--state", str(ROOT / "state"), "--out", str(ROOT / "reports/latest.md")])
        if code == 2:
            return code
        publish(api, args.command == "daily")
        return code
    except urllib.error.HTTPError as exc:
        print(f"Cloud operation stopped: HTTP {exc.code}; no credential or response body logged")
        return 1
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"Cloud operation stopped: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
