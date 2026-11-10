"""Playwright (headless Chromium) scraper for JS-rendered sites. Same output layout as scrape.py.

    python scrape_js.py https://www.endloneliness.com --max-pages 30 --depth 2
Respects robots.txt. Run with a Python that has playwright installed.
"""
import argparse
import hashlib
import json
import time
from collections import deque
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

from playwright.sync_api import sync_playwright

SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip", ".mp4", ".mp3", ".css", ".js",
            ".xls", ".xlsx", ".pdf", ".docx", ".ico")


def host(u):
    return urlparse(u).netloc.lower().removeprefix("www.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--out", default="scraped")
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--max-pages", type=int, default=30)
    ap.add_argument("--delay", type=float, default=1.0)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(exist_ok=True)

    with sync_playwright() as p, open(out / "pages.jsonl", "a", encoding="utf-8") as jf:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent="Mozilla/5.0 (compatible; ResearchScraper/1.0)")
        page = ctx.new_page()
        for start in a.urls:
            print(f"\n== {start}")
            pr = urlparse(start)
            rp = RobotFileParser(f"{pr.scheme}://{pr.netloc}/robots.txt")
            try:
                rp.read()
            except Exception:
                pass
            site_dir = out / host(start)
            site_dir.mkdir(parents=True, exist_ok=True)
            seen, queue, n = {start}, deque([(start, 0)]), 0
            while queue and n < a.max_pages:
                url, d = queue.popleft()
                if not rp.can_fetch("*", url):
                    print(f"[robots] skipped {url}")
                    continue
                try:
                    resp = page.goto(url, wait_until="networkidle", timeout=45000)
                    if resp and resp.status >= 400:
                        print(f"[fail] {url}: HTTP {resp.status}")
                        continue
                    title = page.title()
                    text = page.evaluate("""() => {
                        for (const s of document.querySelectorAll('script,style,noscript,nav,footer,header,aside,form,iframe'))
                            s.remove();
                        const r = document.querySelector('main') || document.querySelector('article') || document.body;
                        return r.innerText;
                    }""")
                    links = page.evaluate("[...document.querySelectorAll('a[href]')].map(a => a.href)") if d < a.depth else []
                except Exception as e:
                    print(f"[fail] {url}: {e}")
                    continue
                text = text.strip()
                if text:
                    name = hashlib.md5(url.encode()).hexdigest()[:10]
                    (site_dir / f"{name}.txt").write_text(f"{title}\n{url}\n\n{text}", encoding="utf-8")
                    jf.write(json.dumps({"url": url, "title": title, "text": text}, ensure_ascii=False) + "\n")
                    n += 1
                    print(f"[ok] {url} ({len(text)} chars)")
                for l in links:
                    l = urldefrag(urljoin(url, l))[0]
                    if (l in seen or not l.startswith("http") or host(l) != host(start)
                            or urlparse(l).path.lower().endswith(SKIP_EXT)):
                        continue
                    seen.add(l)
                    queue.append((l, d + 1))
                time.sleep(a.delay)
        browser.close()


if __name__ == "__main__":
    main()
