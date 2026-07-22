"""Shared configuration, paths and small helpers for every pipeline stage.

Stages communicate exclusively through files in out/ so each one can be
re-run individually (see Makefile). State that persists across days lives
in data/ and docs/ and is committed by the workflow at the end of a run.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
DATA = ROOT / "data"
DOCS = ROOT / "docs"
PROMPTS = ROOT / "prompts"

with open(ROOT / "config.yaml", "r", encoding="utf-8") as f:
    CFG: dict = yaml.safe_load(f)

TEST_SHORT = os.environ.get("TEST_SHORT", "").strip().lower() in ("1", "true", "yes")
if TEST_SHORT:
    # ~5 minute episode for pipeline testing: fewer stories, smaller budgets.
    CFG["target_minutes"] = 5
    CFG["episode"] = {"deep_dives": 1, "quick_hits": 3, "research_items": 1}

TZ = ZoneInfo(CFG["timezone"])

# Episode date = calendar date in the local timezone at run time. The nightly
# run fires at 01:30 UTC = 03:30/04:30 Helsinki, so this is "this morning".
# Override with EPISODE_DATE=YYYY-MM-DD to rebuild a specific date.
EPISODE_DATE: str = os.environ.get("EPISODE_DATE") or dt.datetime.now(TZ).date().isoformat()


def human_date(date_iso: str | None = None) -> str:
    """'2026-07-23' -> '23 July 2026' (for episode titles and the script)."""
    d = dt.date.fromisoformat(date_iso or EPISODE_DATE)
    return f"{d.day} {d.strftime('%B %Y')}"


def episode_title(date_iso: str | None = None) -> str:
    return f"AI Briefing — {human_date(date_iso)}"


def word_target() -> tuple[int, int, int]:
    """(low, target, high) word counts for the full script."""
    wpm = CFG["words_per_minute"]
    target = CFG["target_minutes"] * wpm
    if TEST_SHORT:
        return int(target * 0.7), target, int(target * 1.4)
    # Spec: 3,400–4,400 target band at 25 min / 155 wpm.
    return 3400, target, 4400


def repo_slug() -> str:
    """'owner/Repo' — from Actions env, else parsed from the git remote."""
    slug = os.environ.get("GITHUB_REPOSITORY")
    if slug:
        return slug
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, cwd=ROOT, check=True,
        ).stdout.strip()
    except Exception:
        return "OWNER/REPO"
    url = url.removesuffix(".git").rstrip("/")
    parts = url.replace(":", "/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else "OWNER/REPO"


def site_base_url() -> str:
    override = (CFG.get("podcast") or {}).get("site_url") or ""
    if override:
        return override.rstrip("/")
    owner, _, repo = repo_slug().partition("/")
    return f"https://{owner}.github.io/{repo}"


def log(stage: str, msg: str) -> None:
    print(f"[{stage}] {msg}", flush=True)


def die(stage: str, msg: str) -> None:
    print(f"[{stage}] FATAL: {msg}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def jload(path: Path, default=None):
    if not Path(path).exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def jdump(obj, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def read_prompt(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")
