"""Stage 5 — LLM calls #2a/#2b: write the spoken briefing.

Chunk 1: cold open + deep dives. Chunk 2: quick hits + research corner +
outro. Segments come back inside <segment id="..."> tags for clean TTS
chunking. A single repair call expands or trims when the word count lands
outside the configured band.
"""
from __future__ import annotations

import datetime as dt
import re

from src.config import (CFG, DATA, EPISODE_DATE, OUT, TEST_SHORT, human_date,
                        jdump, jload, log, read_prompt, word_target)
from src.llm import complete

STAGE = "script"
SEG_RE = re.compile(r"<segment\s+id=[\"']([^\"']+)[\"']\s*>(.*?)</segment>",
                    re.DOTALL | re.IGNORECASE)

# Ask-budgets are deliberately inflated ~15–20% above the spec bands
# (dd 500–650, qh 150–220, rc ~250): models consistently undershoot, and
# asking high lands the actual output on target. FLOORS are the real
# minimums the repair pass enforces; EXPAND_TO is what a repair asks for.
if TEST_SHORT:
    BUDGETS = {"intro": "80–110", "dd": "340–440", "qh": "110–150",
               "rc": "130–180", "outro": "35–50"}
    FLOORS = {"intro": 50, "dd": 240, "qh": 75, "rc": 95, "outro": 15}
else:
    BUDGETS = {"intro": "150–190", "dd": "620–750", "qh": "200–260",
               "rc": "260–320", "outro": "50–70"}
    FLOORS = {"intro": 110, "dd": 480, "qh": 150, "rc": 210, "outro": 30}
EXPAND_TO = {"dd": 700, "qh": 240, "rc": 300}


def _seg_kind(sid: str) -> str:
    return sid.rstrip("0123456789")


def sanitize(text: str) -> str:
    """Defense-in-depth: nothing that reads badly aloud survives to TTS."""
    text = re.sub(r"</?segment[^>]*>", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)      # md links -> label
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.M)  # headings
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.M)       # bullets
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _weekday_date() -> str:
    d = dt.date.fromisoformat(EPISODE_DATE)
    return f"{d.strftime('%A')}, {human_date()}"


def _material(item: dict, chars: int) -> str:
    return (f"TITLE: {item['title']}\nSOURCE: {item['source']} "
            f"(published {item['published'][:16]})\n"
            + (f"ANGLE TO EMPHASIZE: {item['angle']}\n" if item.get("angle") else "")
            + f"TEXT:\n{(item.get('text') or item.get('summary') or '')[:chars]}")


def _parse(text: str) -> dict[str, str]:
    return {sid.strip(): sanitize(body) for sid, body in SEG_RE.findall(text)}


def _call_chunk(system: str, user: str, expected: list[str]) -> dict[str, str]:
    for attempt in (1, 2):
        segs = _parse(complete(system, user, max_tokens=8000, temperature=0.7))
        got = [sid for sid in expected if segs.get(sid)]
        if len(got) >= max(1, len(expected) // 2 + 1):
            missing = [sid for sid in expected if not segs.get(sid)]
            if missing:
                log(STAGE, f"WARNING: model omitted segments {missing}")
            return segs
        log(STAGE, f"chunk attempt {attempt} unparseable "
                   f"({len(got)}/{len(expected)} segments); "
                   + ("retrying" if attempt == 1 else "giving up"))
    return segs


def _style_notes(prefs: dict) -> str:
    return (prefs.get("style_notes") or "").strip() or "(none)"


def main() -> None:
    sel = jload(OUT / "selected.json")
    if not sel:
        raise SystemExit(f"[{STAGE}] out/selected.json missing — run curate first")
    prefs = jload(DATA / "preferences.json", {}) or {}
    system = read_prompt("scriptwriter.md")
    max_chars = CFG["llm"]["max_article_chars"]
    deep, quick, research = sel["deep_dives"], sel["quick_hits"], sel["research"]

    headlines = [i["title"] for i in (deep + quick)][:3]
    dd_ids = [f"dd{n}" for n in range(1, len(deep) + 1)]
    qh_ids = [f"qh{n}" for n in range(1, len(quick) + 1)]
    rc_ids = [f"rc{n}" for n in range(1, len(research) + 1)]

    user1 = (
        f"DATE: {_weekday_date()}\n"
        f"LISTENER STYLE NOTES: {_style_notes(prefs)}\n\n"
        f"WRITE THIS PART OF THE EPISODE — segments, in order:\n"
        f'- <segment id="intro">: {BUDGETS["intro"]} words. Cold open: greet very briefly, '
        f"say the show name (Aamukatsaus) and the date, then tease today's top three "
        f"headlines listed below, then hand off to the first deep dive.\n"
        + "".join(f'- <segment id="{sid}">: {BUDGETS["dd"]} words. Deep dive on story {sid.upper()} '
                  f"below. Open with a clear verbal transition.\n" for sid in dd_ids)
        + f"\nTOP HEADLINES FOR THE COLD OPEN:\n"
        + "".join(f"{n}. {h}\n" for n, h in enumerate(headlines, 1))
        + "\nSTORY MATERIAL:\n\n"
        + "\n\n".join(f"=== {sid.upper()} ===\n{_material(item, max_chars)}"
                      for sid, item in zip(dd_ids, deep))
    )
    segs = _call_chunk(system, user1, ["intro"] + dd_ids)

    user2 = (
        f"DATE: {_weekday_date()}\n"
        f"LISTENER STYLE NOTES: {_style_notes(prefs)}\n\n"
        f"The episode's deep dives are already written. WRITE THE REST — segments, in order:\n"
        + "".join(f'- <segment id="{sid}">: {BUDGETS["qh"]} words. Quick hit on story '
                  f"{sid.upper()} below.\n" for sid in qh_ids)
        + (f"The first quick hit should open the round with a short transition like "
           f"\"Time for the quick hits.\"\n" if qh_ids else "")
        + "".join(f'- <segment id="{sid}">: {BUDGETS["rc"]} words. Research corner item '
                  f"{sid.upper()} below: explain the paper plainly — what it is, why it "
                  f"matters, one caveat. The first research segment opens with a transition "
                  f"into the research corner.\n" for sid in rc_ids)
        + f'- <segment id="outro">: {BUDGETS["outro"]} words. Exactly two sentences: sign '
        f"off, and remind the listener to rate today's stories with emoji reactions in "
        f"the day's GitHub issue.\n"
        + "\nSTORY MATERIAL:\n\n"
        + "\n\n".join(f"=== {sid.upper()} ===\n{_material(item, 2000)}"
                      for sid, item in zip(qh_ids, quick))
        + ("\n\n" if research else "")
        + "\n\n".join(f"=== {sid.upper()} ===\n{_material(item, 2000)}"
                      for sid, item in zip(rc_ids, research))
    )
    segs.update(_call_chunk(system, user2, qh_ids + rc_ids + ["outro"]))

    order = ["intro"] + dd_ids + qh_ids + rc_ids + ["outro"]
    segments = [{"id": sid, "text": segs[sid], "words": len(segs[sid].split())}
                for sid in order if segs.get(sid)]
    total = sum(s["words"] for s in segments)
    low, target, high = word_target()
    log(STAGE, f"draft: {total} words (target {target}, band {low}–{high})")

    material = {sid: item for sid, item in
                list(zip(dd_ids, deep)) + list(zip(qh_ids, quick))
                + list(zip(rc_ids, research))}

    def _apply(repaired: dict[str, str], ids: list[str]) -> int:
        for s in segments:
            if s["id"] in ids and repaired.get(s["id"]):
                s["text"] = repaired[s["id"]]
                s["words"] = len(s["text"].split())
        return sum(s["words"] for s in segments)

    if not TEST_SHORT:
        # Under-length repair (spec: below 3,300): up to two rounds, expanding
        # whichever story segments are furthest below their floor — the model
        # tends to undershoot deep dives most.
        for round_no in (1, 2):
            if total >= low - 100:
                break
            under = sorted(
                (s for s in segments
                 if s["id"] in material and s["words"] < FLOORS[_seg_kind(s["id"])]),
                key=lambda s: FLOORS[_seg_kind(s["id"])] - s["words"], reverse=True)[:8]
            if not under:
                break
            fix_ids = [s["id"] for s in under]
            repair_user = (
                f"DATE: {_weekday_date()}\n"
                f"The episode script totals {total} words but must reach at least {low}. "
                f"The segments below are under their word budgets. Rewrite ONLY these "
                f"segments, each expanded to its stated target — add analysis, context "
                f"and concrete detail from the material, never filler. Keep each "
                f"segment's opening transition. Return each inside its "
                f"<segment id=\"...\"> tag:\n\n"
                + "\n\n".join(
                    f"=== {s['id'].upper()} — currently {s['words']} words, expand to "
                    f"about {EXPAND_TO[_seg_kind(s['id'])]} words ===\n{s['text']}\n\n"
                    f"=== MATERIAL FOR {s['id'].upper()} ===\n"
                    f"{_material(material[s['id']], 3000)}"
                    for s in under)
            )
            total = _apply(_parse(complete(system, repair_user, max_tokens=8000,
                                           temperature=0.6)), fix_ids)
            log(STAGE, f"after expand round {round_no}: {total} words")

        # Over-length repair (spec: above 4,600): condense the deep dives.
        if total > high + 200:
            fix_ids = [sid for sid in dd_ids if segs.get(sid)]
            repair_user = (
                f"DATE: {_weekday_date()}\n"
                f"The episode script is {total} words; it must land between {low} and "
                f"{high}. Condense each deep dive below to 430–480 words, keeping the "
                f"transition and all key facts. Return each inside its "
                f"<segment id=\"...\"> tag:\n\n"
                + "\n\n".join(f"=== CURRENT {sid.upper()} ===\n{segs[sid]}"
                              for sid in fix_ids)
            )
            total = _apply(_parse(complete(system, repair_user, max_tokens=8000,
                                           temperature=0.6)), fix_ids)
            log(STAGE, f"after condense: {total} words")

    est_min = total / CFG["words_per_minute"]
    log(STAGE, f"final script: {total} words ≈ {est_min:.1f} min, "
               f"{len(segments)} segments")
    jdump(segments, OUT / "segments.json")
    script = "\n\n".join(f'<segment id="{s["id"]}">\n{s["text"]}\n</segment>'
                         for s in segments)
    (OUT / "script.txt").write_text(script + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
