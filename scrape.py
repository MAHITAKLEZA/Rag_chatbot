"""Standalone web scraper: crawl sites and save page text. No database or API keys needed.

    python scrape.py https://www.parkinson.org/resources-support/helpline
    python scrape.py https://diabetes.org https://diatribe.org --max-pages 50 --depth 2
    python scrape.py --file urls.txt --out scraped

Output: one .txt per page in --out/<site>/, plus --out/pages.jsonl (url, title, text).
"""
import argparse
import hashlib
import json
import re
import time
from collections import deque
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; ResearchScraper/1.0)"
SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip", ".mp4", ".mp3", ".css", ".js",
            ".xls", ".xlsx", ".pdf", ".docx", ".ico")
NOISE = ["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "iframe", "svg"]


def host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def robots_for(url: str) -> RobotFileParser:
    p = urlparse(url)
    rp = RobotFileParser()
    try:
        r = requests.get(f"{p.scheme}://{p.netloc}/robots.txt", headers={"User-Agent": UA}, timeout=20)
        if r.status_code in (401, 403):
            rp.disallow_all = True  # site actively blocks us; don't crawl
        elif r.ok:
            rp.parse(r.text.splitlines())
        else:
            rp.parse([])  # no robots.txt -> allow all
    except requests.RequestException:
        rp.parse([])
    return rp


def extract(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""
    for tag in soup(NOISE):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    text = root.get_text("\n", strip=True)
    return title, re.sub(r"\n{3,}", "\n\n", text)


def crawl(start: str, out: Path, depth: int, max_pages: int, delay: float, include: list[str]) -> list[dict]:
    rp = robots_for(start)
    session = requests.Session()
    session.headers["User-Agent"] = UA
    seen, queue, pages = {start}, deque([(start, 0)]), []
    site_dir = out / host(start).replace(":", "_")
    site_dir.mkdir(parents=True, exist_ok=True)

    while queue and len(pages) < max_pages:
        url, d = queue.popleft()
        if not rp.can_fetch(UA, url):
            print(f"[robots] skipped {url}")
            continue
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"[fail] {url}: {e}")
            continue
        if "html" not in r.headers.get("content-type", ""):
            continue
        title, text = extract(r.text)
        if text:
            name = hashlib.md5(url.encode()).hexdigest()[:10]
            (site_dir / f"{name}.txt").write_text(f"{title}\n{url}\n\n{text}", encoding="utf-8")
            pages.append({"url": url, "title": title, "text": text})
            print(f"[ok] {url} ({len(text)} chars)")

        if d < depth:
            for a in BeautifulSoup(r.text, "lxml").find_all("a", href=True):
                link = urldefrag(urljoin(url, a["href"]))[0]
                path = urlparse(link).path.lower()
                if (link in seen or not link.startswith("http") or host(link) != host(start)
                        or path.endswith(SKIP_EXT)
                        or (include and not any(x in path for x in include))):
                    continue
                seen.add(link)
                queue.append((link, d + 1))
        time.sleep(delay)
    return pages


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("urls", nargs="*", help="start URLs")
    ap.add_argument("--file", help="text file with one start URL per line")
    ap.add_argument("--out", default="scraped", help="output folder (default: scraped)")
    ap.add_argument("--depth", type=int, default=2, help="link depth from the start URL (default 2)")
    ap.add_argument("--max-pages", type=int, default=30, help="max pages per site (default 30)")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests (default 1)")
    ap.add_argument("--include", nargs="*", default=[], help="only follow links whose path contains one of these")
    args = ap.parse_args()

    urls = list(args.urls)
    if args.file:
        urls += [u.strip() for u in Path(args.file).read_text().splitlines() if u.strip().startswith("http")]
    if not urls:
        ap.error("give at least one URL or --file")

    out = Path(args.out)
    out.mkdir(exist_ok=True)
    with open(out / "pages.jsonl", "a", encoding="utf-8") as f:
        for url in urls:
            print(f"\n== {url}")
            for page in crawl(url, out, args.depth, args.max_pages, args.delay, args.include):
                f.write(json.dumps(page, ensure_ascii=False) + "\n")
    print(f"\nDone. Saved to {out.resolve()}")


if __name__ == "__main__":
    main()
