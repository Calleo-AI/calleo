"""
discovery.py — URL discovery and policy for the configured website.

Fetches the site's XML sitemap (location set in site_config.py — note that
some CMSes, e.g. Blackbaud, serve it at /sitemap rather than /sitemap.xml),
normalizes URLs (canonical form, no /page/ duplicates), and filters out
pages that are off-topic for a public knowledge base or disallowed by
robots.txt.

The sitemap is only the *seed* set — frontier.py follows links found on the
pages it fetches. classify_url() is the single gate both paths go through: it
decides whether a URL is an indexable page, a document to extract text from,
or nothing at all.
"""
import re
import sys
from pathlib import Path
from xml.etree import ElementTree

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from site_config import (
    SITE_ROOT,
    SITEMAP_URL,
    ROBOTS_DISALLOWED_PATHS,
    EXCLUDED_URL_PATTERNS,
    USER_AGENT,
    CRAWL_DOCUMENTS,
    DOCUMENT_EXTENSIONS,
)

_SM_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

# robots.txt-disallowed top-level paths; anchored so /app doesn't match /how-to-apply.
_ROBOTS_RES = [
    re.compile(rf"^{re.escape(SITE_ROOT)}{p}(/|$)")
    for p in ROBOTS_DISALLOWED_PATHS
]

# Off-topic / internal / dated content (substring or regex, matched lowercase).
EXCLUDED_PATTERNS = EXCLUDED_URL_PATTERNS
_EXCLUDED_RES = [re.compile(p) for p in EXCLUDED_PATTERNS]


def parse_sitemap(xml_text):
    """Return the list of <loc> URLs from a sitemap urlset document."""
    root = ElementTree.fromstring(xml_text)
    locs = root.findall(".//sm:url/sm:loc", _SM_NS)
    if not locs:
        locs = root.findall(".//url/loc")
    return [loc.text.strip() for loc in locs if loc.text and loc.text.strip()]


def normalize_url(url):
    """Canonicalize a site URL: no fragment, no /page/ prefix, no trailing slash."""
    url = url.strip().split("#")[0]
    url = url.replace(f"{SITE_ROOT}/page/", f"{SITE_ROOT}/", 1)
    return url.rstrip("/")


def is_excluded(url):
    """True if the URL should not be part of the knowledge base."""
    if "?" in url:
        return True
    if not url.startswith(SITE_ROOT):
        return True
    if any(r.search(url) for r in _ROBOTS_RES):
        return True
    low = url.lower()
    return any(r.search(low) for r in _EXCLUDED_RES)


def document_type(url):
    """The DOCUMENT_EXTENSIONS suffix this URL points at, without the dot ('pdf'), else ''."""
    path = url.lower().split("?")[0].rstrip("/")
    for ext in DOCUMENT_EXTENSIONS:
        if path.endswith(ext):
            return ext.lstrip(".")
    return ""


def is_document(url):
    """True if the URL points at a file type we extract text from rather than an HTML page."""
    return bool(document_type(url))


def classify_url(url, allow_documents=None):
    """The single frontier gate: 'page', 'document', or '' (not indexable).

    is_excluded() is checked first and always wins, so a deployer who puts
    r"\\.pdf$" back into EXCLUDED_URL_PATTERNS gets a hard ban regardless of
    CRAWL_DOCUMENTS. Set CRAWL_DOCUMENTS = False for a blanket opt-out.
    """
    if is_excluded(url):
        return ""
    if is_document(url):
        allow = CRAWL_DOCUMENTS if allow_documents is None else allow_documents
        return "document" if allow else ""
    return "page"


def filter_urls(urls):
    """Normalize, drop excluded, dedupe (order-preserving)."""
    seen, out = set(), []
    for u in urls:
        n = normalize_url(u)
        if n in seen or is_excluded(n):
            continue
        seen.add(n)
        out.append(n)
    return out


def classify_urls(urls, allow_documents=None):
    """Normalize + classify + dedupe. Returns [(url, 'page'|'document')]."""
    seen, out = set(), []
    for u in urls:
        n = normalize_url(u)
        if n in seen:
            continue
        seen.add(n)
        kind = classify_url(n, allow_documents=allow_documents)
        if kind:
            out.append((n, kind))
    return out


def filter_documents(urls):
    """Normalize + dedupe + keep only URLs that classify as indexable documents."""
    return [u for u, kind in classify_urls(urls) if kind == "document"]


def get_site_urls(sitemap_url=SITEMAP_URL):
    """Fetch the live sitemap and return the filtered, canonical URL list."""
    resp = httpx.get(
        sitemap_url,
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    resp.raise_for_status()
    urls = filter_urls(parse_sitemap(resp.text))
    if not urls:
        raise RuntimeError(f"Sitemap at {sitemap_url} yielded no usable URLs")
    return urls
