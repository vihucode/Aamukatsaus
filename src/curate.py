"""Stage 4 — LLM call #1: score and select today's stories.

Sends compact previews of every candidate plus the preference profile;
receives strict JSON naming deep dives, quick hits, research picks and
notable skips. Output: out/selected.json with full text attached.
"""
from __future__ import annotations

import json
import re

from src.config import CFG, DATA, EPISODE_DATE, OUT, jdump, jload, log, read_prompt
from src.llm import complete_json

STAGE = "curate"


def one_liner(item: dict) -> str:
    text = item.get("summary") or item.get("text") or ""
    text = re.sub(r"\s+", " ", text).strip()
    m = re.match(r"(.{40,240}?[.!?])(\s|$)", text)
    line = m.group(1) if m else text[:240]
    return line.strip()


def _preview(item: dict) -> str:
    chars = (900 if item["kind"] == "research"
             else CFG["llm"]["curation_preview_chars"])
    header = (f"#{item['id']} [{item['kind']}] {item['title']} "
              f"(source: {item['source']}, published: {item['published'][:16]}"
              + (f", HN points: {item['points']}" if item.get("points") else "")
              + f", full_text: {str(bool(item.get('full_text'))).lower()})")
    body = re.sub(r"\s+", " ", item.get("text") or "")[:chars]
    return f"{header}\n{body}"


def _valid_picks(raw, items_by_id: dict, kind: str, limit: int, taken: set) -> list[dict]:
    picks = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        try:
            sid = int(entry.get("id"))
        except (TypeError, ValueError):
            continue
        item = items_by_id.get(sid)
        if item is None or sid in taken or item["kind"] != kind:
            continue
        taken.add(sid)
        picks.append({**item,
                      "angle": str(entry.get("angle") or "")[:600],
                      "reason": str(entry.get("reason") or "")[:400],
                      "one_liner": one_liner(item)})
        if len(picks) >= limit:
            break
    return picks


def main() -> None:
    data = jload(OUT / "articles.json")
    if not data:
        raise SystemExit(f"[{STAGE}] out/articles.json missing — run extract first")
    items = data["items"]
    items_by_id = {i["id"]: i for i in items}
    prefs = jload(DATA / "preferences.json", {}) or {}
    ep = CFG["episode"]

    user = (
        f"TODAY: {EPISODE_DATE}\n\n"
        f"REQUIRED COUNTS: deep_dives={ep['deep_dives']}, "
        f"quick_hits={ep['quick_hits']}, research={ep['research_items']}\n\n"
        f"LISTENER PREFERENCE PROFILE:\n{json.dumps(prefs, ensure_ascii=False, indent=1)}\n\n"
        f"CANDIDATES ({len(items)}):\n\n"
        + "\n\n".join(_preview(i) for i in items)
    )
    result = complete_json(read_prompt("curator.md"), user,
                           max_tokens=3000, temperature=0.2)

    taken: set = set()
    deep = _valid_picks(result.get("deep_dives"), items_by_id, "news",
                        ep["deep_dives"], taken)
    quick = _valid_picks(result.get("quick_hits"), items_by_id, "news",
                         ep["quick_hits"], taken)
    research = _valid_picks(result.get("research"), items_by_id, "research",
                            ep["research_items"], taken)
    skipped = []
    for entry in result.get("skipped_notable") or []:
        item = items_by_id.get(entry.get("id")) if isinstance(entry, dict) else None
        if item:
            skipped.append({"id": item["id"], "title": item["title"],
                            "why": str(entry.get("why") or "")[:300]})

    if not deep or (len(deep) + len(quick) + len(research)) < 3:
        raise SystemExit(f"[{STAGE}] curation too thin: {len(deep)} deep dives, "
                         f"{len(quick)} quick hits, {len(research)} research")
    if len(deep) < ep["deep_dives"] or len(quick) < ep["quick_hits"]:
        log(STAGE, f"NOTE: model returned fewer picks than configured "
                   f"(dd {len(deep)}/{ep['deep_dives']}, qh {len(quick)}/{ep['quick_hits']}) "
                   f"— continuing with what we have")

    jdump({"date": EPISODE_DATE, "deep_dives": deep, "quick_hits": quick,
           "research": research, "skipped_notable": skipped},
          OUT / "selected.json")
    log(STAGE, f"selected {len(deep)} deep dives, {len(quick)} quick hits, "
               f"{len(research)} research; noted {len(skipped)} skips")


if __name__ == "__main__":
    main()
