# Aamukatsaus — Autonomous Daily AI/Tech News Podcast

Every night this repo collects the most important AI/tech news and research,
writes a 20–30 minute spoken briefing, converts it to audio, and publishes it
to a **private podcast RSS feed** your phone auto-downloads before **06:00
Europe/Helsinki**. During the day you rate stories with emoji reactions in the
GitHub mobile app, and the system learns your preferences for the next run.

Runs entirely on **GitHub Actions + GitHub Pages + GitHub Releases** — no
servers. The only paid component is the LLM API (≈ $3–4/month on Claude
Haiku; free-tier Gemini/Groq fallbacks give a $0 build).

## How it works

```
GitHub Actions (cron 01:30 UTC daily)
│
├── 1. update_preferences  ← reads 👍/👎/❤️ reactions from last 7 days' issues
├── 2. fetch               ← RSS/APIs, last 26 h, dedupe vs data/seen.json
├── 3. extract             ← full-text extraction (trafilatura)
├── 4. curate              ← LLM: score & select stories using preferences
├── 5. write_script        ← LLM: 3,400–4,400-word spoken briefing
├── 6. tts                 ← edge-tts per segment → ffmpeg → episode.mp3
├── 7. publish             ← MP3 to a Release; feed.xml + shownotes to docs/
└── 8. feedback            ← GitHub issue with one comment per story
│
GitHub Pages (docs/)  → feed.xml + cover + shownotes
GitHub Releases       → MP3 files (keeps git history small)
```

Each stage writes JSON to `out/`, so stages are individually re-runnable
(`make fetch`, `make curate`, …). Persistent state (`data/preferences.json`,
`data/seen.json`, `docs/`) is committed at the end of a successful run —
a failed run changes nothing.

## Setup (once)

1. **Repo**: must be **public** (free GitHub Pages requires it). Content is
   only news summaries — nothing sensitive.
2. **Secret**: repo → Settings → Secrets and variables → Actions → add
   `ANTHROPIC_API_KEY`. *(Or `GEMINI_API_KEY` / `GROQ_API_KEY` — see
   "LLM providers" below.)*
3. **Pages**: repo → Settings → Pages → "Deploy from a branch" →
   branch `master`, folder `/docs`.
4. **First episode**: Actions → `daily-episode` → *Run workflow* (tick
   `test_short` for a ~5-minute smoke-test episode first if you like).
   After that it runs itself every night at 01:30 UTC.

### Phone setup

1. Feed URL: `https://<user>.github.io/<repo>/feed.xml`
   (this repo: `https://vihucode.github.io/Aamukatsaus/feed.xml`).
2. Subscribe by URL:
   - **Overcast**: + → Add URL
   - **Pocket Casts**: Discover → search bar → paste URL
   - **Apple Podcasts**: Library → ⋯ → Follow a Show by URL
3. Enable **auto-download** and notifications for the show → the episode is
   on your phone by ~05:30.
4. Install the **GitHub mobile app** and enable notifications for this repo →
   the daily *"Episode … — rate stories"* issue is one tap away. Rate with a
   reaction on each story's comment: **👍 more like this · 👎 less ·
   ❤️ much more**.

## The feedback loop

- `feedback.py` posts one comment per story on the daily `episode`-labeled
  issue. Reactions are per-comment, which is what makes per-story rating work
  in the mobile app.
- Next night, `update_preferences.py` collects reaction counts from the last
  7 days' issues and has the LLM adjust `data/preferences.json`:
  **+0.1** per 👍, **−0.1** per 👎, **+0.25** per ❤️ on matching topic weights
  (clamped to 0–2, 1.0 = neutral); it may also boost/mute entities and update
  free-text style notes. Untouched topics decay ~0.02/week back toward 1.0.
  Topics are never deleted. Issues older than 7 days are closed automatically.

## LLM providers & cost

`src/llm.py` exposes one interface with three backends, selected by
`config.yaml` → `llm.provider` (or env `LLM_PROVIDER`):

| provider    | model (default)          | key env             | cost                  |
|-------------|--------------------------|---------------------|-----------------------|
| `anthropic` | `claude-haiku-4-5`       | `ANTHROPIC_API_KEY` | ≈ $0.13/day ≈ $3–4/mo |
| `gemini`    | `gemini-2.5-flash`       | `GEMINI_API_KEY`    | free tier → $0        |
| `groq`      | `llama-3.3-70b-versatile`| `GROQ_API_KEY`      | free tier → $0        |

If the configured provider fails all retries and another key is present, the
run automatically falls back to it. Everything else (Actions on a public
repo, Pages, Releases, edge-tts) is $0.

## Local development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export ANTHROPIC_API_KEY=...       # and optionally GH_TOKEN for publish/feedback
make test-short                    # ~5 min episode end-to-end
make all                           # full-length episode
```

ffmpeg must be installed (`apt install ffmpeg`; preinstalled on Actions
runners). Without a GitHub token, `publish`/`feedback` skip the API work and
still write `docs/` locally. `EPISODE_DATE=YYYY-MM-DD` rebuilds a specific
date. Stage outputs land in `out/` for inspection.

## Scheduling / timezone math

Cron `30 1 * * *` UTC = **04:30** Helsinki in summer (EEST, UTC+3) and
**03:30** in winter (EET, UTC+2). With a < 30 min run and GitHub's typical
cron delay, the episode is in the feed comfortably **before 05:45** year-round
— the schedule needs no DST adjustment.

## Design notes

- **MP3s live in Releases, not git**: ~12 MB/day would bloat history past
  GitHub's limits within a year; release assets are free and podcast apps
  follow the download redirect fine. Old releases are pruned past
  `retention.releases_keep` (60).
- **Idempotent**: re-running a date deletes and recreates that date's release,
  reuses its issue, and upserts its feed entry — never duplicates. Same-day
  re-runs are not blocked by `data/seen.json` (only previous days are).
- **Resilient**: a dead source, failed article extraction, or failed TTS
  segment is logged and skipped; the episode still ships.
- **Private-ish**: `<itunes:block>Yes</itunes:block>` keeps the feed out of
  podcast directories, and `docs/index.html` is `noindex`. (The feed URL is
  still public if someone has it — it's only news summaries.)
- `config.yaml` holds sources, voices, word budgets and retention — edit
  without touching code.

## Repo map

```
.github/workflows/daily.yml   nightly pipeline
src/                          pipeline stages (see architecture above)
prompts/                      curator / scriptwriter / preference-updater prompts
config.yaml                   sources, budgets, voices, retention
data/preferences.json         learned preference profile (committed)
data/seen.json                14-day URL dedupe window (committed)
docs/                         GitHub Pages root: feed.xml, cover.png, shownotes/
out/                          per-run artifacts (gitignored)
```
