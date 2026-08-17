"""
links.py — Pull outbound links out of a fetched page's raw HTML.

The extraction chain (extraction.py) deliberately drops hrefs — they are noise
inside a RAG passage. Link *discovery* is a separate concern, so it reads the
raw HTML instead: every <a href> is resolved against the page URL, normalized
the same way sitemap URLs are, and kept only if it stays on the site. Whether a
surviving URL is an indexable page, a document, or junk is discovery's call.

Pure functions, no network.
"""
import sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from site_config import SITE_ROOT

# Schemes that are never fetchable page content.
_SKIP_SCHEMES = ("mailto:", "tel:", "javascript:", "data:", "sms:", "ftp:", "file:")

_SITE_HOST = (urlsplit(SITE_ROOT).hostname or "").lower()


def is_same_site(url):
    """True if url is on the configured site's host (www./non-www. equivalent)."""
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return False
    return host == _SITE_HOST or host.removeprefix("www.") == _SITE_HOST.removeprefix("www.")


def to_site_root(url):
    """Rewrite a same-site URL onto SITE_ROOT's exact scheme+host.

    Pages link to themselves inconsistently (http:// vs https://, www vs bare),
    and every downstream check — normalize_url's /page/ collapse, is_excluded's
    startswith, dedupe — compares against SITE_ROOT literally. Canonicalizing
    here means one page is one URL no matter how it was linked.
    """
    parts = urlsplit(url)
    tail = parts.path
    if parts.query:
        tail += f"?{parts.query}"
    return f"{SITE_ROOT}{tail}"


def extract_links(html, base_url):
    """Return absolute, deduped, same-site hrefs from the <a> tags in html.

    Order-preserving. Fragment-only anchors ("#main"), empty hrefs and
    non-HTTP schemes are dropped; relative hrefs are resolved against base_url
    (honoring <base href> when the page sets one).
    """
    # normalize_url lives in discovery, which imports nothing from here —
    # imported lazily anyway to keep this module's import graph flat.
    from discovery import normalize_url

    soup = BeautifulSoup(html or "", "lxml")

    base_tag = soup.find("base", href=True)
    if base_tag:
        base_url = urljoin(base_url, base_tag["href"].strip())

    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        if "nofollow" in (a.get("rel") or []):
            continue
        href = a["href"].strip()
        if not href or href.startswith("#"):
            continue
        if href.lower().startswith(_SKIP_SCHEMES):
            continue
        absolute = urljoin(base_url, href)
        if not absolute.lower().startswith(("http://", "https://")):
            continue
        if not is_same_site(absolute):
            continue
        url = normalize_url(to_site_root(absolute))
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out
