"""
extraction.py — Deterministic main-content extraction for the configured website.

Chain (escalates when output < MIN_CHARS):
  1. selector_extract  — BeautifulSoup tuned to Blackbaud CMS markup (very
       common for school and non-profit sites): keep div.page-row regions, drop
       narrow promo sidebars (span1-7 cols), strip nav/menus/forms and
       .element-invisible a11y stubs. Non-Blackbaud sites simply produce no
       page-row matches and fall through to trafilatura.
  2. trafilatura       — general-purpose extractor, recall-favoring markdown
  3. fit_markdown      — optional pre-rendered markdown passed in by the caller
       (empty with the httpx fetcher; kept as a hook for alternate fetchers)

All paths return markdown with headings preserved so chunking.py can split
on structure. Junk lines like "List of 6 items." are scrubbed everywhere.
"""
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from site_config import TITLE_SUFFIX_RE

MIN_CHARS = 200

_JUNK_SELECTORS = (
    ".element-invisible, .screen-reader-text, div.content.menu, "
    "div.content.megamenu, div.content.mobilemenu, div.content.search, "
    "div.content.links, nav, form, script, style, noscript"
)

_JUNK_LINE_RE = re.compile(
    r"^[ \t]*(List of \d+ (items|frequently asked questions)\.?|Search|Login|\*[* \t]*)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_BLANKS_RE = re.compile(r"\n{3,}")


def scrub_junk_lines(markdown_text):
    """Remove Blackbaud a11y/nav artifact lines and collapse extra blank lines."""
    out = _JUNK_LINE_RE.sub("", markdown_text or "")
    return _BLANKS_RE.sub("\n\n", out).strip()


def _markdown_from_html(html_fragment, base_url):
    # markdownify, not crawl4ai's generator: pure Python, no playwright stack —
    # keeps the rebuild pipeline runnable on a slim serving VM.
    from markdownify import markdownify as md

    text = md(
        html_fragment,
        heading_style="ATX",      # '#'-style headings so chunking.py can split on them
        bullets="-",
        strip=["a", "img"],       # keep anchor text, drop hrefs/images (RAG noise)
    )
    return (text or "").strip()


def selector_extract(html, url):
    """Extract main content via the verified Blackbaud page-row structure."""
    soup = BeautifulSoup(html, "lxml")
    kept = []
    for row in soup.select("div.page-row"):
        for junk in row.select(_JUNK_SELECTORS):
            junk.decompose()
        # Narrow columns (span1..span7) are cross-page promo rails, not content.
        for col in row.select("div.page-col"):
            spans = [c for c in (col.get("class") or [])
                     if c.startswith("span") and c[4:].isdigit()]
            if spans and int(spans[0][4:]) < 8:
                col.decompose()
        if row.get_text(strip=True):
            kept.append(str(row))
    if not kept:
        return ""
    return scrub_junk_lines(_markdown_from_html("\n".join(kept), url))


_CHROME_SELECTORS = (
    "div.content.menu, div.content.megamenu, div.content.mobilemenu, "
    "#dl-menu, .dl-menuwrapper, .minisitemap, .mm-buttons, .mm-login, "
    ".content.search, .content.links, .background-carousel, "
    ".element-invisible, nav, footer"
)


def chrome_free_soup(html):
    """Soup with site-wide menus/nav/footers removed.

    Used before trafilatura — on content-less pages (photo galleries, landing
    shells) it otherwise extracts the mega-menu link tree and presents nav junk
    as page content — and by images.py, which gets the same header-logo and
    carousel-sprite removal for free.
    """
    soup = BeautifulSoup(html or "", "lxml")
    for junk in soup.select(_CHROME_SELECTORS):
        junk.decompose()
    return soup


def _strip_chrome(html):
    return str(chrome_free_soup(html))


def trafilatura_extract(html, url):
    import trafilatura

    text = trafilatura.extract(
        _strip_chrome(html),
        url=url,
        output_format="markdown",
        include_tables=True,
        include_links=False,
        include_comments=False,
        favor_recall=True,   # missing a policy line is worse than extra noise
        deduplicate=True,    # collapses Blackbaud responsive duplicate blocks
    )
    return scrub_junk_lines(text or "")


def extract_content(html, url, fit_markdown=""):
    """Return (markdown, strategy). May return < MIN_CHARS text — caller skips those."""
    text = selector_extract(html, url)
    if len(text) >= MIN_CHARS:
        return text, "selector"
    text2 = trafilatura_extract(html, url)
    if len(text2) >= MIN_CHARS:
        return text2, "trafilatura"
    candidates = [
        (text, "selector"),
        (text2, "trafilatura"),
        (scrub_junk_lines(fit_markdown), "fit_markdown"),
    ]
    return max(candidates, key=lambda c: len(c[0]))


def extract_title(html, url):
    """Page title for chunk headers: page H1, else <title> minus site suffix, else slug."""
    soup = BeautifulSoup(html or "", "lxml")
    h1 = soup.select_one("h1.page-title") or soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    if soup.title and soup.title.string:
        t = re.sub(TITLE_SUFFIX_RE, "", soup.title.string.strip()).strip()
        if t:
            return t
    slug = url.rstrip("/").split("/")[-1] or "Home"
    return slug.replace("-", " ").title()
