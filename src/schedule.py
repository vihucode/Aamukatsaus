"""Which nights an episode is built — see `schedule:` in config.yaml.

Kept separate from guard.py so the same decision is available to anything
that needs it: the in-workflow guard, the external nightly scheduler
(which checks before pushing a trigger at all), and `make schedule`.

An explicit workflow_dispatch always bypasses this — asking for an episode
by hand means you want one, whatever the calendar says.
"""
from __future__ import annotations

import datetime as dt
import sys

from src.config import CFG, EPISODE_DATE

# Accepts "mon", "monday", "Mon" ... index matches date.weekday().
_DAY_INDEX = {}
for _i, (_short, _long) in enumerate([
        ("mon", "monday"), ("tue", "tuesday"), ("wed", "wednesday"),
        ("thu", "thursday"), ("fri", "friday"), ("sat", "saturday"),
        ("sun", "sunday")]):
    _DAY_INDEX[_short] = _i
    _DAY_INDEX[_long] = _i

_ALIASES = {
    "all": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "daily": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "weekdays": ["mon", "tue", "wed", "thu", "fri"],
    "weekends": ["sat", "sun"],
}


def _configured_days() -> set[int]:
    """Weekday indices an episode is wanted on. Unknown names are ignored."""
    raw = (CFG.get("schedule") or {}).get("days")
    if raw is None:
        return set(range(7))  # no schedule block configured -> every night
    if isinstance(raw, str):
        raw = [raw]
    names: list[str] = []
    for entry in raw:
        key = str(entry).strip().lower()
        names.extend(_ALIASES.get(key, [key]))
    return {_DAY_INDEX[n] for n in names if n in _DAY_INDEX}


_SHORT_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def enabled_days_label() -> str:
    wanted = _configured_days()
    return ", ".join(n for i, n in enumerate(_SHORT_NAMES) if i in wanted) or "none"


def should_build(date_iso: str | None = None) -> tuple[bool, str]:
    """(build?, human-readable reason) for the given episode date."""
    sched = CFG.get("schedule") or {}
    date_iso = date_iso or EPISODE_DATE
    day = dt.date.fromisoformat(date_iso)
    weekday = day.strftime("%A")

    if sched.get("paused"):
        return False, "schedule is paused (schedule.paused: true in config.yaml)"
    if date_iso in {str(d) for d in (sched.get("skip_dates") or [])}:
        return False, f"{date_iso} is listed in schedule.skip_dates"
    wanted = _configured_days()
    if not wanted:
        return False, "schedule.days is empty — no night is enabled"
    if day.weekday() not in wanted:
        return False, (f"{weekday} is not an episode night "
                       f"(enabled: {enabled_days_label()})")
    return True, f"{weekday} is an episode night"


def main() -> None:
    """`python -m src.schedule` — prints the decision, exit 0 build / 1 skip."""
    build, reason = should_build()
    print(f"{'BUILD' if build else 'SKIP'} {EPISODE_DATE}: {reason}")
    sys.exit(0 if build else 1)


if __name__ == "__main__":
    main()
