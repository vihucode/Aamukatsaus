"""Stage 7 — publish the episode.

MP3 goes to a GitHub Release (tag ep-YYYY-MM-DD, delete-and-recreate for
idempotency); shownotes + regenerated RSS feed go to docs/ for GitHub
Pages. docs/episodes.json is the persistent episode index the feed is
built from. Old releases beyond retention.releases_keep are deleted.
"""
from __future__ import annotations

import datetime as dt
import email.utils
from xml.sax.saxutils import escape

from src import gh
from src.config import (CFG, DOCS, EPISODE_DATE, OUT, episode_title,
                        human_date, jdump, jload, log, repo_slug, site_base_url)

STAGE = "publish"


def mp3_url(date: str) -> str:
    return (f"https://github.com/{repo_slug()}/releases/download/"
            f"ep-{date}/{date}.mp3")


def _duration_hms(seconds: int) -> str:
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}"


def build_shownotes(sel: dict) -> str:
    def block(title: str, items: list[dict]) -> str:
        if not items:
            return ""
        lines = [f"## {title}", ""]
        for n, item in enumerate(items, 1):
            lines.append(f"{n}. **[{item['title']}]({item['url']})** — {item['source']}")
            if item.get("one_liner"):
                lines.append(f"   {item['one_liner']}")
        return "\n".join(lines) + "\n\n"

    return (f"# {episode_title(sel['date'])}\n\n"
            f"_Generated automatically. Rate stories with emoji reactions in the "
            f"day's GitHub issue to tune future episodes._\n\n"
            + block("Deep dives", sel["deep_dives"])
            + block("Quick hits", sel["quick_hits"])
            + block("Research corner", sel["research"])).rstrip() + "\n"


def build_description(sel: dict) -> str:
    deep = " · ".join(i["title"] for i in sel["deep_dives"])
    extra = []
    if sel["quick_hits"]:
        extra.append(f"{len(sel['quick_hits'])} quick hits")
    if sel["research"]:
        extra.append("the research corner")
    tail = f" Plus {' and '.join(extra)}." if extra else ""
    return f"Deep dives: {deep}.{tail}"


def build_feed(episodes: list[dict]) -> str:
    pc = CFG["podcast"]
    base = site_base_url()
    items = []
    for ep in episodes:
        pub = dt.datetime.combine(dt.date.fromisoformat(ep["date"]),
                                  dt.time(3, 30), tzinfo=dt.timezone.utc)
        items.append(f"""    <item>
      <title>{escape(ep['title'])}</title>
      <description>{escape(ep['description'])}</description>
      <link>{escape(f"{base}/shownotes/{ep['date']}.md")}</link>
      <guid isPermaLink="false">{ep['date']}</guid>
      <pubDate>{email.utils.format_datetime(pub)}</pubDate>
      <enclosure url="{escape(ep['mp3_url'])}" length="{ep['bytes']}" type="audio/mpeg"/>
      <itunes:duration>{_duration_hms(ep['seconds'])}</itunes:duration>
      <itunes:episodeType>full</itunes:episodeType>
    </item>""")
    now = email.utils.format_datetime(dt.datetime.now(dt.timezone.utc))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{escape(pc['title'])}</title>
    <link>{escape(base + '/')}</link>
    <atom:link href="{escape(base + '/feed.xml')}" rel="self" type="application/rss+xml"/>
    <description>{escape(pc['description'])}</description>
    <language>{pc['language']}</language>
    <lastBuildDate>{now}</lastBuildDate>
    <itunes:author>{escape(pc['author'])}</itunes:author>
    <itunes:image href="{escape(base + '/cover.png')}"/>
    <itunes:category text="Technology"/>
    <itunes:explicit>false</itunes:explicit>
    <itunes:block>Yes</itunes:block>
{chr(10).join(items)}
  </channel>
</rss>
"""


def ensure_release(date: str, mp3_path, notes: str) -> None:
    tag = f"ep-{date}"
    slug_path = gh.repo_path
    existing = gh.request("GET", slug_path(f"/releases/tags/{tag}"),
                          ok=(200, 404)).json()
    if existing.get("id"):
        log(STAGE, f"release {tag} exists — deleting for clean re-publish")
        gh.request("DELETE", slug_path(f"/releases/{existing['id']}"), ok=(204, 404))
        gh.request("DELETE", slug_path(f"/git/refs/tags/{tag}"), ok=(204, 404, 422))
    rel = gh.request("POST", slug_path("/releases"), json={
        "tag_name": tag, "name": f"Episode {date}", "body": notes,
        "draft": False, "prerelease": False,
    }).json()
    upload_url = rel["upload_url"].split("{")[0]
    gh.request("POST", f"{upload_url}?name={mp3_path.name}",
               headers=gh.auth_headers({"Content-Type": "audio/mpeg"}),
               data=mp3_path.read_bytes(), ok=(201,), retries=2)
    log(STAGE, f"uploaded {mp3_path.name} to release {tag}")


def prune_releases(keep: int) -> None:
    releases = gh.paginate(gh.repo_path("/releases"))
    episodes = sorted((r for r in releases if r["tag_name"].startswith("ep-")),
                      key=lambda r: r["tag_name"], reverse=True)
    for rel in episodes[keep:]:
        log(STAGE, f"retention: deleting old release {rel['tag_name']}")
        gh.request("DELETE", gh.repo_path(f"/releases/{rel['id']}"), ok=(204, 404))
        gh.request("DELETE", gh.repo_path(f"/git/refs/tags/{rel['tag_name']}"),
                   ok=(204, 404, 422))


def main() -> None:
    sel = jload(OUT / "selected.json")
    meta = jload(OUT / "audio_meta.json")
    if not sel or not meta:
        raise SystemExit(f"[{STAGE}] missing out/selected.json or out/audio_meta.json")
    date = sel["date"]
    mp3_path = OUT / meta["file"]
    keep = CFG["retention"]["releases_keep"]

    notes = build_shownotes(sel)
    (DOCS / "shownotes").mkdir(parents=True, exist_ok=True)
    (DOCS / "shownotes" / f"{date}.md").write_text(notes, encoding="utf-8")

    if gh.have_token(STAGE):
        ensure_release(date, mp3_path, notes)
        prune_releases(keep)
    else:
        log(STAGE, "docs will still be written; release upload skipped")

    episodes = jload(DOCS / "episodes.json", []) or []
    episodes = [e for e in episodes if e["date"] != date]
    episodes.append({
        "date": date,
        "title": episode_title(date),
        "description": build_description(sel),
        "mp3_url": mp3_url(date),
        "bytes": meta["bytes"],
        "seconds": meta["seconds"],
    })
    episodes.sort(key=lambda e: e["date"], reverse=True)
    episodes = episodes[:keep]
    jdump(episodes, DOCS / "episodes.json")
    (DOCS / "feed.xml").write_text(build_feed(episodes), encoding="utf-8")
    log(STAGE, f"feed regenerated with {len(episodes)} episode(s); "
               f"latest: {episode_title(date)} ({human_date(date)})")


if __name__ == "__main__":
    main()
