"""Pre-flight for scheduled runs: skip the pipeline when today's episode
already shipped.

The workflow carries two cron entries because GitHub's scheduler skips or
delays firings on quiet repos. Whichever fires first builds the episode;
the other lands here, sees the published release, and turns the whole run
into a no-op so no duplicate LLM/TTS cost is incurred. Manual dispatches
never run this guard (the workflow gates it to schedule events) so a
forced rebuild always goes through.
"""
from __future__ import annotations

import os

from src import gh
from src.config import EPISODE_DATE, log

STAGE = "guard"


def main() -> None:
    skip = False
    if gh.have_token(STAGE):
        rel = gh.request("GET", gh.repo_path(f"/releases/tags/ep-{EPISODE_DATE}"),
                         ok=(200, 404)).json()
        has_asset = any(a.get("name") == f"{EPISODE_DATE}.mp3"
                        for a in rel.get("assets") or [])
        published_today = (rel.get("published_at") or "") >= f"{EPISODE_DATE}T00:00:00"
        skip = bool(rel.get("id")) and has_asset and published_today
    log(STAGE, f"episode {EPISODE_DATE} already published — skipping this run"
        if skip else f"no published episode for {EPISODE_DATE} yet — proceeding")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"skip={'true' if skip else 'false'}\n")


if __name__ == "__main__":
    main()
