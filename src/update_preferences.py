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
    """Returns (story comments with reaction counts, issues past close window)."""
    window_start = today - dt.timedelta(days=7)
    close_before = today - dt.timedelta(
        days=CFG["retention"]["issues_close_after_days"])
    stories, to_close = [], []
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
            stories.append({"title": m.group(1), "source": m.group(2),
                            "url": m.group(3), "date": created.isoformat(),
                            "up": reactions.get("+1", 0),
                            "down": reactions.get("-1", 0),
                            "heart": reactions.get("heart", 0)})
    return stories, to_close


def new_reaction_deltas(stories: list[dict], ledger: dict) -> list[dict]:
    """Each reaction adjusts the profile exactly once: compare current counts
    to what data/reactions_applied.json already credited and pass only the
    net-new part to the model. Un-reacting is ignored (no negative deltas)."""
    rated = []
    for s in stories:
        prev = ledger.get(s["url"]) or {}
        d_up = max(0, s["up"] - prev.get("up", 0))
        d_down = max(0, s["down"] - prev.get("down", 0))
        d_heart = max(0, s["heart"] - prev.get("heart", 0))
        if d_up or d_down or d_heart:
            rated.append({"title": s["title"], "source": s["source"],
                          "thumbs_up": d_up, "thumbs_down": d_down,
                          "hearts": d_heart})
    return rated


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
            stories, to_close = collect_signals(today)
            ledger = jload(DATA / "reactions_applied.json", {}) or {}
            rated = new_reaction_deltas(stories, ledger)
            log(STAGE, f"{len(stories)} story comments in window, "
                       f"{len(rated)} with new reactions; "
                       f"{len(to_close)} old issues to close")
            if rated:
                prefs = apply_llm_update(prefs, rated)
            # Persist credited counts only after a successful update, so a
            # failed LLM call retries the same signals tomorrow.
            for s in stories:
                if s["up"] or s["down"] or s["heart"]:
                    ledger[s["url"]] = {"up": s["up"], "down": s["down"],
                                        "heart": s["heart"], "date": s["date"]}
            prune = (today - dt.timedelta(days=21)).isoformat()
            ledger = {u: e for u, e in ledger.items()
                      if (e.get("date") or "9999") >= prune}
            jdump(ledger, DATA / "reactions_applied.json")
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
