"""Step 1: discover URLs from a source (sitemap first, link crawl as fallback)."""
import time
from collections import deque
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .config import REQUEST_DELAY, USER_AGENT

SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".zip", ".mp4", ".mp3", ".css", ".js", ".xls", ".xlsx")
DOC_EXT = (".pdf", ".docx")


def _get(url: str) -> requests.Response | None:
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        return r if r.ok else None
    except requests.RequestException:
        return None


def _host(netloc: str) -> str:
    return netloc.lower().removeprefix("www.")


def _allowed(url: str, base: str, rules: dict) -> bool:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or _host(p.netloc) != _host(urlparse(base).netloc):
        return False
    path = p.path.lower()
    if path.endswith(SKIP_EXT):
        return False
    if any(x in path for x in rules.get("exclude", [])):
        return False
    include = rules.get("include", [])
    return not include or any(x in path for x in include)


def _from_sitemap(url: str, depth: int = 0) -> list[str]:
    r = _get(url)
    if r is None or depth > 3:
        return []
    soup = BeautifulSoup(r.content, "xml")
    locs = [loc.text.strip() for loc in soup.find_all("loc")]
    if soup.find("sitemapindex"):
        out: list[str] = []
        for sm in locs:
            out += _from_sitemap(sm, depth + 1)
        return out
    return locs


def _crawl(base: str, rules: dict) -> list[str]:
    max_depth, max_pages = rules.get("max_depth", 2), rules.get("max_pages", 100)
    seen, queue, found = {base}, deque([(base, 0)]), []
    while queue and len(found) < max_pages:
        url, depth = queue.popleft()
        found.append(url)
        if depth >= max_depth or url.lower().endswith(DOC_EXT):
            continue
        time.sleep(REQUEST_DELAY)
        r = _get(url)
        if r is None or "html" not in r.headers.get("content-type", ""):
            continue
        for a in BeautifulSoup(r.text, "lxml").find_all("a", href=True):
            link = urldefrag(urljoin(url, a["href"]))[0]
            if link not in seen and _allowed(link, base, rules):
                seen.add(link)
                queue.append((link, depth + 1))
    return found


def discover(base_url: str, source_type: str, rules: dict) -> list[str]:
    max_pages = rules.get("max_pages", 100)
    if source_type in ("pdf", "docx"):
        return [base_url]
    if source_type == "sitemap" or base_url.endswith(".xml"):
        urls = _from_sitemap(base_url)
    else:
        urls = _from_sitemap(urljoin(base_url, "/sitemap.xml")) or _crawl(base_url, rules)
    urls = [u for u in dict.fromkeys(urls) if _allowed(u, base_url, rules)]
    return urls[:max_pages]
