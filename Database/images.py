"""
images.py — Index images as TEXT records.

The knowledge base is embedded with a text-only model, so an image can only be
retrieved through words written about it. This module harvests those words —
alt text, <figcaption>, the nearest preceding heading, the title attribute, and
the cleaned-up filename — and turns each image into an ordinary chunk carrying
the same contextual header as every other chunk.

The image URL goes into chunk metadata AND into the chunk body, because the
retrieval path hands raw document text to the model: an image URL that lives
only in metadata can never be cited in an answer.

Two invariants worth keeping in mind:
  * metadata["source"] is the HOST PAGE, not the image. That makes a page's
    images part of the page as far as create_db's swap and update_db's refresh
    are concerned — re-crawling a page atomically replaces its images — and it
    means an answer's source link points at a readable page, not a bare .jpg.
  * An image with no descriptive text is skipped, not indexed with an empty
    body. A filename alone does not earn an embedding.
"""
import hashlib
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from chunking import build_header
from extraction import chrome_free_soup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from site_config import (
    EXCLUDED_IMAGE_PATTERNS,
    IMAGE_MAX_PER_PAGE,
    IMAGE_MIN_ALT_CHARS,
)

_EXCLUDED_IMAGE_RES = [re.compile(p) for p in EXCLUDED_IMAGE_PATTERNS]

# Alt values that are placeholders rather than descriptions.
_ALT_STOPWORDS = {"", "image", "photo", "picture", "img", "graphic", "banner",
                  "thumbnail", "logo", "icon", "spacer"}

_MIN_DIMENSION = 100          # declared width/height below this is decoration
_HASHY_RE = re.compile(r"^[0-9a-f]{8,}$")
_DIMENSION_RE = re.compile(r"^\d+x\d+$")
_FILENAME_NOISE = {"thumb", "thumbnail", "small", "medium", "large", "crop",
                   "resized", "final", "copy", "img", "image", "photo"}


def _first_srcset_url(value):
    for candidate in (value or "").split(","):
        url = candidate.strip().split(" ")[0].strip()
        if url:
            return url
    return ""


def _image_src(tag):
    """The best src for an <img>, covering the common lazy-loading attributes."""
    for attr in ("src", "data-src", "data-original", "data-lazy-src"):
        value = (tag.get(attr) or "").strip()
        if value and not value.lower().startswith("data:"):
            return value
    for attr in ("srcset", "data-srcset"):
        value = _first_srcset_url(tag.get(attr))
        if value and not value.lower().startswith("data:"):
            return value
    return ""


def _too_small(tag):
    """True if the tag declares pixel dimensions below the decoration threshold."""
    for attr in ("width", "height"):
        raw = (tag.get(attr) or "").strip().rstrip("px")
        if raw.isdigit() and int(raw) < _MIN_DIMENSION:
            return True
    return False


def _caption_for(tag):
    """Text of the <figcaption> or .caption belonging to this image, if any."""
    figure = tag.find_parent("figure")
    if figure:
        caption = figure.find("figcaption")
        if caption:
            return caption.get_text(" ", strip=True)
    for sibling in list(tag.next_siblings)[:3]:
        classes = getattr(sibling, "get", lambda *_: None)("class") or []
        if any("caption" in c.lower() for c in classes):
            return sibling.get_text(" ", strip=True)
    return ""


def _heading_for(tag):
    """The nearest heading above this image — the section it visually belongs to."""
    for previous in tag.find_all_previous(["h1", "h2", "h3", "h4"]):
        text = previous.get_text(" ", strip=True)
        if text:
            return text
    return ""


def _filename_words(image_url):
    """'campus-tour_2019_1920x1080.jpg' -> 'Campus Tour'."""
    slug = unquote(urlsplit(image_url).path.rstrip("/").split("/")[-1])
    slug = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", slug)
    words = []
    for word in re.split(r"[-_.+\s]+", slug):
        low = word.lower()
        if not word or low.isdigit() or low in _FILENAME_NOISE:
            continue
        if _HASHY_RE.match(low) or _DIMENSION_RE.match(low):
            continue
        words.append(word if word.isupper() else word.capitalize())
    return " ".join(words)


def _clean_alt(value):
    text = re.sub(r"\s+", " ", (value or "")).strip()
    return "" if text.lower().strip(" .") in _ALT_STOPWORDS else text


def harvest(html, page_url, page_title, max_images=None):
    """Return image records for the indexable <img> tags on a page.

    Each record is {image_url, page_url, page_title, alt, caption, heading,
    title_attr, filename}. Site chrome is removed first (chrome_free_soup), so
    header logos and mega-menu sprites never reach the deny patterns.
    """
    cap = IMAGE_MAX_PER_PAGE if max_images is None else max_images
    soup = chrome_free_soup(html)

    records, seen = [], set()
    for tag in soup.find_all("img"):
        if len(records) >= cap:
            break
        src = _image_src(tag)
        if not src:
            continue
        image_url = urljoin(page_url, src)
        if not image_url.lower().startswith(("http://", "https://")):
            continue
        low = image_url.lower()
        if any(r.search(low) for r in _EXCLUDED_IMAGE_RES):
            continue
        if _too_small(tag):
            continue
        if image_url in seen:
            continue

        alt = _clean_alt(tag.get("alt"))
        title_attr = _clean_alt(tag.get("title"))
        caption = _caption_for(tag)
        heading = _heading_for(tag)
        # An image earns its embedding on words a human wrote *about it* — alt,
        # caption, title. The heading and filename are context that rides along;
        # neither can carry an image over the bar on its own.
        described = " ".join(x for x in (alt, caption, title_attr) if x).strip()
        if len(described) < IMAGE_MIN_ALT_CHARS:
            continue

        seen.add(image_url)
        records.append({
            "image_url": image_url,
            "page_url": page_url,
            "page_title": page_title,
            "alt": alt,
            "caption": caption,
            "heading": heading,
            "title_attr": title_attr,
            "filename": _filename_words(image_url),
        })
    return records


def record_text(record):
    """The chunk body for one image record."""
    described = record["alt"] or record["title_attr"] or record["caption"]
    lines = [f'Image on the page "{record["page_title"]}": {described}']
    if record["caption"] and record["caption"] != described:
        lines.append(f"Caption: {record['caption']}")
    if record["heading"]:
        lines.append(f"Nearby heading: {record['heading']}")
    if record["filename"]:
        lines.append(f"Filename: {record['filename']}")
    lines.append(f"Image URL: {record['image_url']}")
    lines.append(f"Appears on: {record['page_url']}")
    return "\n".join(lines)


def record_id(record):
    """Content-addressed id: a reshuffled gallery must not churn every image's id."""
    digest = hashlib.sha1(record["image_url"].encode("utf-8")).hexdigest()[:10]
    return f"{record['page_url']}_image_{digest}"


def build_chunks(records):
    """Render image records as ChromaDB chunks. Returns (chunks, per_page_counts).

    Deduped on the image URL plus its *description* — deliberately not on the
    rendered body, which names the host page and so would differ every time.
    A stock photo reused across 50 pages with the same alt text is embedded
    once; the same photo described differently on two pages is real signal and
    is kept twice.
    """
    chunks, counts, seen = [], {}, set()
    for record in records:
        key = (record["image_url"], record["alt"], record["caption"],
               record["title_attr"], record["heading"])
        if key in seen:
            continue
        seen.add(key)
        metadata = {
            "source": record["page_url"],
            "title": record["page_title"],
            "extractor": "image",
            "kind": "image",
            "image_url": record["image_url"],
        }
        if record["alt"]:
            metadata["image_alt"] = record["alt"]
        if record["heading"]:
            metadata["section"] = record["heading"]
        chunks.append({
            "id": record_id(record),
            "text": build_header(record["page_title"], record["page_url"],
                                 record["heading"] or None) + record_text(record),
            "metadata": metadata,
        })
        counts[record["page_url"]] = counts.get(record["page_url"], 0) + 1
    return chunks, counts
