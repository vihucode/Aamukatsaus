"""Pre-flight for automatic runs. Turns the whole run into a no-op when
either (a) tonight is not an episode night per `schedule:` in config.yaml,
or (b) today's episode already shipped.

(b) exists because several triggers can fire for the same date — the
nightly push plus GitHub's own crons — and only the first should spend
LLM/TTS budget. (a) is how the owner chooses which nights the podcast
rolls at all.

Manual workflow_dispatch runs never reach this guard (the workflow gates
it on the event type), so a deliberate rebuild always goes through.
"""
from __future__ import annotations

import os

from src import gh
from src.config import EPISODE_DATE, log
from src.schedule import should_build

STAGE = "guard"


def main() -> None:
    skip, reason = False, ""

    build_tonight, why = should_build()
    if not build_tonight:
        skip, reason = True, why

    if not skip and gh.have_token(STAGE):
        rel = gh.request("GET", gh.repo_path(f"/releases/tags/ep-{EPISODE_DATE}"),
                         ok=(200, 404)).json()
        has_asset = any(a.get("name") == f"{EPISODE_DATE}.mp3"
                        for a in rel.get("assets") or [])
        published_today = (rel.get("published_at") or "") >= f"{EPISODE_DATE}T00:00:00"
        if bool(rel.get("id")) and has_asset and published_today:
            skip, reason = True, "episode is already published"

    log(STAGE, f"skipping {EPISODE_DATE}: {reason}" if skip
        else f"building {EPISODE_DATE}: {why}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"skip={'true' if skip else 'false'}\n")


if __name__ == "__main__":
    main()
