"""Stage 6 — synthesize the script with edge-tts and assemble the MP3.

Each segment is synthesized separately (retries + fallback voice); one
failing segment is dropped rather than killing the run. Segments are
joined with 400 ms gaps, loudness-normalized and encoded to mono MP3,
then ID3-tagged with the episode title and cover art.
"""
from __future__ import annotations

import asyncio
import subprocess
import time

from mutagen.id3 import APIC, ID3, TALB, TDRC, TIT2, TPE1
from mutagen.mp3 import MP3

from src.config import (CFG, DOCS, EPISODE_DATE, OUT, TEST_SHORT,
                        episode_title, jdump, jload, log)

STAGE = "tts"
AUDIO_DIR = OUT / "audio"
MIN_SEGMENT_BYTES = 1024


def _synth(text: str, voice: str, path) -> bool:
    import edge_tts

    async def run():
        rate = CFG["tts"].get("rate") or "+0%"
        await edge_tts.Communicate(text, voice, rate=rate).save(str(path))

    try:
        asyncio.run(run())
        return path.exists() and path.stat().st_size >= MIN_SEGMENT_BYTES
    except Exception as e:  # noqa: BLE001
        log(STAGE, f"  synth error ({voice}): {e}")
        return False


def synth_segment(seg: dict, path) -> bool:
    plan = [(CFG["tts"]["voice"], 3), (CFG["tts"].get("fallback_voice"), 2)]
    for voice, tries in plan:
        if not voice:
            continue
        for attempt in range(1, tries + 1):
            if _synth(seg["text"], voice, path):
                return True
            time.sleep(2 * attempt)
        log(STAGE, f"  voice {voice} exhausted for segment {seg['id']}")
    return False


def _run_ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[:800]}")


def assemble(files: list, out_path) -> None:
    gap_s = CFG["tts"]["segment_gap_ms"] / 1000
    silence = AUDIO_DIR / "silence.mp3"
    # Match edge-tts output params (24 kHz mono) so concat is seamless.
    _run_ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                 "-t", f"{gap_s}", "-c:a", "libmp3lame", "-b:a", "48k",
                 "-ar", "24000", "-ac", "1", str(silence)])
    listfile = AUDIO_DIR / "concat.txt"
    lines = []
    for n, f in enumerate(files):
        if n:
            lines.append(f"file '{silence.name}'")
        lines.append(f"file '{f.name}'")
    listfile.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listfile),
                 "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                 "-c:a", "libmp3lame", "-b:a", CFG["tts"]["bitrate"],
                 "-ac", "1", "-ar", "44100", str(out_path)])


def tag(path) -> None:
    audio = MP3(str(path))
    if audio.tags is None:
        audio.add_tags()
    tags: ID3 = audio.tags
    tags.add(TIT2(encoding=3, text=episode_title()))
    tags.add(TPE1(encoding=3, text=CFG["podcast"]["author"]))
    tags.add(TALB(encoding=3, text=CFG["podcast"]["title"]))
    tags.add(TDRC(encoding=3, text=EPISODE_DATE))
    cover = DOCS / "cover.png"
    if cover.exists():
        tags.add(APIC(encoding=3, mime="image/png", type=3, desc="Cover",
                      data=cover.read_bytes()))
    audio.save()


def main() -> None:
    segments = jload(OUT / "segments.json")
    if not segments:
        raise SystemExit(f"[{STAGE}] out/segments.json missing — run write_script first")
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    files, skipped = [], []
    for n, seg in enumerate(segments, start=1):
        path = AUDIO_DIR / f"{n:02d}_{seg['id']}.mp3"
        log(STAGE, f"synthesizing {seg['id']} ({seg['words']} words)")
        if synth_segment(seg, path):
            files.append(path)
        else:
            skipped.append(seg["id"])
        time.sleep(0.3)

    if skipped:
        log(STAGE, f"WARNING: skipped segments after all retries: {skipped}")
    if not files or len(skipped) > len(segments) * 0.3:
        raise SystemExit(f"[{STAGE}] too many TTS failures "
                         f"({len(skipped)}/{len(segments)}) — aborting")

    out_path = OUT / f"{EPISODE_DATE}.mp3"
    assemble(files, out_path)
    try:
        tag(out_path)
    except Exception as e:  # noqa: BLE001 — tags are cosmetic
        log(STAGE, f"WARNING: ID3 tagging failed: {e}")

    seconds = int(MP3(str(out_path)).info.length)
    size = out_path.stat().st_size
    log(STAGE, f"episode: {seconds // 60}m{seconds % 60:02d}s, {size / 1e6:.1f} MB")
    if not TEST_SHORT:
        if seconds < 8 * 60:
            raise SystemExit(f"[{STAGE}] episode only {seconds // 60} min — "
                             f"something upstream went badly wrong")
        if not (20 * 60 <= seconds <= 30 * 60):
            log(STAGE, f"WARNING: duration {seconds / 60:.1f} min is outside the "
                       f"20–30 min band")
    jdump({"file": out_path.name, "seconds": seconds, "bytes": size},
          OUT / "audio_meta.json")


if __name__ == "__main__":
    main()
