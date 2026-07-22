"""Stage 2 — collect candidate stories from all configured sources.

Pulls items published in the last `fetch.window_hours`, dedupes against
data/seen.json (previous days only, so same-day re-runs are idempotent),
and writes out/candidates.json. Any single source failing is logged and
skipped — the episode must survive dead feeds.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import requests

from src.config import CFG, DATA, EPISODE_DATE, OUT, jdump, jload, log

STAGE = "fetch"
UA = "Mozilla/5.0 (X11; Linux x86_64) AamukatsausBot/1.0"
TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "ref", "cmpid", "mc_cid", "mc_eid")


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not any(k.lower().startswith(p) or k.lower() == p.rstrip("_")
                        for p in TRACKING_PARAMS)]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path,
                       urlencode(query), ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()[:24]


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _within_window(published: dt.datetime | None, cutoff: dt.datetime) -> bool:
    return published is None or published >= cutoff


def _entry_time(entry) -> dt.datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        st = getattr(entry, attr, None) or entry.get(attr)
        if st:
            return dt.datetime(*st[:6], tzinfo=dt.timezone.utc)
    return None


def _get(url: str) -> bytes:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
    r.raise_for_status()
    return r.content


def _from_rss(source: dict, cutoff: dt.datetime) -> list[dict]:
    feed = feedparser.parse(_get(source["url"]))
    items = []
    for e in feed.entries:
        link = e.get("link")
        if not link:
            continue
        when = _entry_time(e)
        if not _within_window(when, cutoff):
            continue
        items.append({
            "title": strip_html(e.get("title", ""))[:300],
            "url": link,
            "source": source["name"],
            "published": (when or dt.datetime.now(dt.timezone.utc)).isoformat(),
            "kind": "news",
            "summary": strip_html(e.get("summary", ""))[:1200],
        })
    return items


def _from_arxiv(source: dict, cutoff: dt.datetime) -> list[dict]:
    # arXiv announces once per weekday; entries are "new today" even when the
    # per-entry timestamp is coarse. Keep genuinely new papers, drop updates.
    feed = feedparser.parse(_get(source["url"]))
    items = []
    for e in feed.entries:
        summary = strip_html(e.get("summary", ""))
        m = re.match(r"arXiv:\S+\s+Announce Type:\s*(\S+)\s*Abstract:\s*(.*)",
                     summary, re.DOTALL)
        announce, abstract = (m.group(1), m.group(2)) if m else ("new", summary)
        if announce not in ("new", "cross"):
            continue
        when = _entry_time(e)
        if not _within_window(when, cutoff):
            continue
        items.append({
            "title": strip_html(e.get("title", ""))[:300],
            "url": e.get("link", ""),
            "source": source["name"],
            "published": (when or dt.datetime.now(dt.timezone.utc)).isoformat(),
            "kind": "research",
            "summary": abstract.strip()[:1500],
        })
    return items


def _from_hn(source: dict, cutoff: dt.datetime) -> list[dict]:
    data = requests.get(source["url"], headers={"User-Agent": UA}, timeout=25).json()
    min_points = CFG["fetch"]["hn_min_points"]
    keyword_re = re.compile(
        r"\b(?:" + "|".join(re.escape(k.lower()) for k in CFG["fetch"]["ai_keywords"])
        + r")\b")
    items = []
    for hit in data.get("hits", []):
        title = hit.get("title") or ""
        when = dt.datetime.fromisoformat(hit["created_at"].replace("Z", "+00:00"))
        if not _within_window(when, cutoff):
            continue
        points = hit.get("points") or 0
        if points < min_points and not keyword_re.search(title.lower()):
            continue
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        items.append({
            "title": title[:300],
            "url": url,
            "source": source["name"],
            "published": when.isoformat(),
            "kind": "news",
            "summary": strip_html(hit.get("story_text") or "")[:1200],
            "points": points,
        })
    return items


_FETCHERS = {"rss": _from_rss, "arxiv": _from_arxiv, "hn": _from_hn}


def main() -> None:
    cfg = CFG["fetch"]
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=cfg["window_hours"])
    cap = cfg["per_source_cap"]

    collected: list[dict] = []
    for source in CFG["sources"]:
        try:
            items = _FETCHERS[source["kind"]](source, cutoff)[:cap]
            collected.extend(items)
            log(STAGE, f"{source['name']}: {len(items)} items")
        except Exception as e:  # noqa: BLE001 — one dead source must not kill the run
            log(STAGE, f"WARNING: source '{source['name']}' failed and was skipped: {e}")
        time.sleep(0.2)

    # Dedupe: within this run, then against previous days in seen.json.
    seen: dict[str, str] = jload(DATA / "seen.json", {}) or {}
    today = EPISODE_DATE
    fresh, in_run = [], set()
    for item in collected:
        h = url_hash(item["url"])
        if h in in_run:
            continue
        if seen.get(h) and seen[h] < today:
            continue
        in_run.add(h)
        item["hash"] = h
        fresh.append(item)

    news = sorted((i for i in fresh if i["kind"] == "news"),
                  key=lambda i: i["published"], reverse=True)[:cfg["max_news_candidates"]]
    research = sorted((i for i in fresh if i["kind"] == "research"),
                      key=lambda i: i["published"], reverse=True)[:cfg["max_research_candidates"]]
    candidates = news + research
    for n, item in enumerate(candidates, start=1):
        item["id"] = n

    # Mark candidates as seen (dated today) and prune the rolling window.
    prune_before = (dt.date.fromisoformat(today)
                    - dt.timedelta(days=CFG["retention"]["seen_days"])).isoformat()
    seen = {h: d for h, d in seen.items() if d >= prune_before}
    for item in candidates:
        seen.setdefault(item["hash"], today)
    jdump(seen, DATA / "seen.json")

    jdump({"date": today, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "items": candidates}, OUT / "candidates.json")
    log(STAGE, f"total: {len(collected)} fetched -> {len(candidates)} candidates "
               f"({len(news)} news, {len(research)} research)")
    if not candidates:
        raise SystemExit(f"[{STAGE}] no candidates at all — every source failed?")


if __name__ == "__main__":
    main()
