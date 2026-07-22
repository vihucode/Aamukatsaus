"""Stage 3 — full-text extraction for news candidates (trafilatura).

Research items keep their abstract. Failures fall back to the RSS summary
with full_text=false; extraction never kills the run.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import requests
import trafilatura

from src.config import CFG, OUT, jdump, jload, log

STAGE = "extract"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TIMEOUT = 15
ATTEMPTS = 3  # 1 try + 2 retries
MIN_TEXT = 400  # chars; below this the "extraction" was probably a cookie wall


def _download(url: str) -> str | None:
    for attempt in range(ATTEMPTS):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT,
                             allow_redirects=True)
            if r.status_code == 200 and r.text:
                return r.text
        except requests.RequestException:
            pass
    return None


def _extract_one(item: dict) -> dict:
    max_chars = CFG["llm"]["max_article_chars"]
    if item["kind"] == "research":
        item["text"] = item["summary"][:max_chars]
        item["full_text"] = False
        return item
    html = _download(item["url"])
    text = None
    if html:
        try:
            text = trafilatura.extract(html, include_comments=False,
                                       include_tables=False, url=item["url"])
        except Exception:  # noqa: BLE001
            text = None
    if text and len(text) >= MIN_TEXT:
        item["text"] = text[:max_chars]
        item["full_text"] = True
    else:
        item["text"] = item["summary"][:max_chars]
        item["full_text"] = False
    return item


def main() -> None:
    data = jload(OUT / "candidates.json")
    if not data:
        raise SystemExit(f"[{STAGE}] out/candidates.json missing — run fetch first")
    items = data["items"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        items = list(pool.map(_extract_one, items))
    ok = sum(1 for i in items if i.get("full_text"))
    news = sum(1 for i in items if i["kind"] == "news")
    log(STAGE, f"full text for {ok}/{news} news items "
               f"(the rest fall back to their RSS summary)")
    data["items"] = items
    jdump(data, OUT / "articles.json")


if __name__ == "__main__":
    main()
