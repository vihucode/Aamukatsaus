"""Stage 8 — create the daily rating issue: one comment per story.

Reactions on those comments (👍 / 👎 / ❤️ in the GitHub mobile app) are
read back by update_preferences at the start of the next nightly run.
Re-running the same date reuses the existing issue and reposts comments,
so a date never gets two issues.
"""
from __future__ import annotations

import time

from src import gh
from src.config import CFG, OUT, jload, log, site_base_url
from src.publish import mp3_url

STAGE = "feedback"
LABEL = "episode"
MARKER = "<!--aamukatsaus-story-->"


def issue_title(date: str) -> str:
    return f"Episode {date} — rate stories"


def issue_body(date: str) -> str:
    base = site_base_url()
    return (
        "Rate today's stories with emoji reactions **on the comments below** — "
        "the nightly run reads them and tunes tomorrow's curation.\n\n"
        "**Legend:** 👍 more like this · 👎 less of this · ❤️ much more of this\n\n"
        f"📄 [Shownotes]({base}/shownotes/{date}.md) · "
        f"🎧 [Episode MP3]({mp3_url(date)})\n"
    )


def ensure_label() -> None:
    gh.request("POST", gh.repo_path("/labels"),
               json={"name": LABEL, "color": "1d76db",
                     "description": "Daily episode rating thread"},
               ok=(201, 422))


def find_existing(date: str) -> list[dict]:
    issues = gh.paginate(gh.repo_path("/issues"),
                         params={"labels": LABEL, "state": "all"}, max_pages=2)
    return [i for i in issues
            if i.get("title") == issue_title(date) and "pull_request" not in i]


def delete_our_comments(number: int) -> None:
    comments = gh.paginate(gh.repo_path(f"/issues/{number}/comments"))
    for c in comments:
        if MARKER in (c.get("body") or ""):
            gh.request("DELETE", gh.repo_path(f"/issues/comments/{c['id']}"),
                       ok=(204, 404))


def story_comment(item: dict) -> str:
    line = f"**{item['title']}** ({item['source']}) — {item['url']}"
    summary = item.get("one_liner") or ""
    return f"{line}\n\n{summary}\n\n{MARKER}"


def main() -> None:
    sel = jload(OUT / "selected.json")
    if not sel:
        raise SystemExit(f"[{STAGE}] out/selected.json missing — run curate first")
    if not gh.have_token(STAGE):
        return
    date = sel["date"]
    ensure_label()

    existing = find_existing(date)
    if existing:
        number = existing[0]["number"]
        log(STAGE, f"reusing issue #{number} for {date}")
        gh.request("PATCH", gh.repo_path(f"/issues/{number}"),
                   json={"state": "open", "body": issue_body(date)})
        delete_our_comments(number)
        for dup in existing[1:]:
            gh.request("PATCH", gh.repo_path(f"/issues/{dup['number']}"),
                       json={"state": "closed", "state_reason": "not_planned"})
    else:
        issue = gh.request("POST", gh.repo_path("/issues"),
                           json={"title": issue_title(date),
                                 "body": issue_body(date),
                                 "labels": [LABEL]}).json()
        number = issue["number"]
        log(STAGE, f"created issue #{number} for {date}")

    stories = sel["deep_dives"] + sel["quick_hits"] + sel["research"]
    for item in stories:
        gh.request("POST", gh.repo_path(f"/issues/{number}/comments"),
                   json={"body": story_comment(item)})
        time.sleep(0.4)  # keep comment order stable, stay far from rate limits
    log(STAGE, f"posted {len(stories)} story comments on issue #{number}")


if __name__ == "__main__":
    main()
