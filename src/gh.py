"""Minimal GitHub REST client used by publish, feedback and preferences.

Uses GH_TOKEN / GITHUB_TOKEN (provided automatically on Actions). Kept as
plain REST so the same code runs on the runner and locally with a PAT.
"""
from __future__ import annotations

import os
import time

import requests

from src.config import log, repo_slug

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
STAGE = "gh"


def token() -> str | None:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or None


def auth_headers(extra: dict | None = None) -> dict:
    h = {
        "Authorization": f"Bearer {token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if extra:
        h.update(extra)
    return h


def request(method: str, path: str, *, ok=(200, 201, 204), retries: int = 3,
            **kwargs) -> requests.Response:
    """path may be absolute (https://...) or relative to the API root.

    Transient failures (network errors, 429/5xx) are retried with backoff;
    anything else outside `ok` raises immediately.
    """
    url = path if path.startswith("http") else f"{API}{path}"
    kwargs.setdefault("timeout", 60)
    kwargs.setdefault("headers", auth_headers())
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.request(method, url, **kwargs)
        except requests.RequestException as e:
            last = e
            if attempt < retries:
                time.sleep(2 ** attempt)
            continue
        if r.status_code in (429, 500, 502, 503, 504) and r.status_code not in ok:
            last = RuntimeError(f"HTTP {r.status_code}")
            if attempt < retries:
                time.sleep(2 ** attempt)
            continue
        if r.status_code not in ok:
            raise RuntimeError(f"{method} {url} -> {r.status_code}: {r.text[:300]}")
        return r
    raise RuntimeError(f"{method} {url} failed after {retries} attempts: {last}")


def get_json(path: str, **kwargs):
    return request("GET", path, **kwargs).json()


def paginate(path: str, params: dict | None = None, max_pages: int = 10) -> list:
    items: list = []
    params = dict(params or {})
    params.setdefault("per_page", 100)
    for page in range(1, max_pages + 1):
        params["page"] = page
        batch = get_json(path, params=params)
        if not batch:
            break
        items.extend(batch)
        if len(batch) < params["per_page"]:
            break
    return items


def repo_path(suffix: str) -> str:
    return f"/repos/{repo_slug()}{suffix}"


def have_token(stage: str) -> bool:
    if token():
        return True
    log(stage, "no GH_TOKEN/GITHUB_TOKEN in environment — skipping GitHub API work "
               "(normal for local runs; the Actions workflow always has one)")
    return False
