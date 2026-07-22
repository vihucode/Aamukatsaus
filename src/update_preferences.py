"""Stage 1 — LLM call #3: fold reaction signals into the preference profile.

Reads 👍 / 👎 / ❤️ reactions from the last 7 days' episode issues, asks the
model for an updated preferences.json (clamped and merged so topics are
never deleted), applies a slow decay toward neutral for untouched topics,
and closes rating issues older than the configured window.

This stage is deliberately non-fatal: any failure logs a warning and the
episode pipeline continues with the existing profile.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import traceback

from src import gh
from src.config import CFG, DATA, EPISODE_DATE, jdump, jload, log, read_prompt
from src.llm import complete_json

STAGE = "prefs"
LABEL = "episode"
STORY_RE = re.compile(r"\*\*(.+?)\*\*\s*\((.+?)\)\s*—\s*(\S+)")
DECAY_PER_DAY = 0.003  # ≈ 0.02/week toward 1.0 for topics reactions didn't touch


def collect_signals(today: dt.date) -> tuple[list[dict], list[dict]]:
    """Returns (rated stories, open issues past the close-after window)."""
    window_start = today - dt.timedelta(days=7)
    close_before = today - dt.timedelta(
        days=CFG["retention"]["issues_close_after_days"])
    rated, to_close = [], []
    issues = gh.paginate(gh.repo_path("/issues"),
                         params={"labels": LABEL, "state": "all"}, max_pages=3)
    for issue in issues:
        if "pull_request" in issue:
            continue
        created = dt.date.fromisoformat(issue["created_at"][:10])
        if issue["state"] == "open" and created < close_before:
            to_close.append(issue)
        if created < window_start:
            continue
        comments = gh.paginate(gh.repo_path(f"/issues/{issue['number']}/comments"))
        for c in comments:
            m = STORY_RE.search(c.get("body") or "")
            if not m:
                continue
            reactions = c.get("reactions") or {}
            up, down, heart = (reactions.get("+1", 0), reactions.get("-1", 0),
                               reactions.get("heart", 0))
            if up or down or heart:
                rated.append({"title": m.group(1), "source": m.group(2),
                              "thumbs_up": up, "thumbs_down": down,
                              "hearts": heart})
    return rated, to_close


def apply_llm_update(prefs: dict, rated: list[dict]) -> dict:
    user = (f"CURRENT PREFERENCES:\n{json.dumps(prefs, ensure_ascii=False, indent=1)}\n\n"
            f"REACTION SIGNALS FROM THE LAST 7 DAYS:\n"
            f"{json.dumps(rated, ensure_ascii=False, indent=1)}")
    out = complete_json(read_prompt("preference_updater.md"), user,
                        max_tokens=2000, temperature=0.2)
    new = dict(prefs)
    topics = dict(prefs.get("topics") or {})
    for k, v in (out.get("topics") or {}).items():
        try:
            topics[str(k)[:60]] = round(min(2.0, max(0.0, float(v))), 2)
        except (TypeError, ValueError):
            continue
    new["topics"] = topics  # old topics stay: we only overlay, never delete
    for key in ("boosted_entities", "muted_entities"):
        val = out.get(key)
        if isinstance(val, list):
            new[key] = [str(x)[:60] for x in val][:20]
    if isinstance(out.get("style_notes"), str):
        new["style_notes"] = out["style_notes"][:600]
    return new


def apply_decay(prefs: dict, before_topics: dict) -> dict:
    topics = dict(prefs.get("topics") or {})
    for k, v in topics.items():
        if before_topics.get(k) != v:
            continue  # adjusted (or new) today — no decay
        if v > 1.0:
            topics[k] = round(max(1.0, v - DECAY_PER_DAY), 3)
        elif v < 1.0:
            topics[k] = round(min(1.0, v + DECAY_PER_DAY), 3)
    prefs["topics"] = topics
    return prefs


def close_old_issues(to_close: list[dict]) -> None:
    for issue in to_close:
        log(STAGE, f"closing rating issue #{issue['number']} ({issue['title']})")
        gh.request("PATCH", gh.repo_path(f"/issues/{issue['number']}"),
                   json={"state": "closed", "state_reason": "completed"},
                   ok=(200, 404))


def main() -> None:
    try:
        prefs = jload(DATA / "preferences.json", {}) or {}
        before_topics = dict(prefs.get("topics") or {})
        today = dt.date.fromisoformat(EPISODE_DATE)
        if gh.have_token(STAGE):
            rated, to_close = collect_signals(today)
            log(STAGE, f"{len(rated)} rated stories in window; "
                       f"{len(to_close)} old issues to close")
            if rated:
                prefs = apply_llm_update(prefs, rated)
            close_old_issues(to_close)
        prefs = apply_decay(prefs, before_topics)
        prefs["updated"] = EPISODE_DATE
        jdump(prefs, DATA / "preferences.json")
        log(STAGE, f"preferences updated ({len(prefs.get('topics', {}))} topics)")
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 — a broken prefs update must not kill the episode
        log(STAGE, "WARNING: preference update failed; keeping existing profile")
        traceback.print_exc()


if __name__ == "__main__":
    main()
