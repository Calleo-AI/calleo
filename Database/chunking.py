"""
chunking.py — Heading-aware chunking with contextual headers.

Strategy (see docs/superpowers/specs/2026-06-10-db-rebuild-pipeline-design.md):
  * pages <= WHOLE_PAGE_MAX chars are indexed as a single chunk
  * larger pages split at #/##/### headings; sections < MIN_CHUNK chars merge
    into a neighbor; sections <= SECTION_MAX stay whole; only oversize
    sections are sub-split (sentence-aware, no overlap)
  * every chunk is prefixed with "Document: <title> / Source: <url> /
    Section: <breadcrumb>" so retrieval never loses page context
"""
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

HEADERS_TO_SPLIT_ON = [("#", "h1"), ("##", "h2"), ("###", "h3")]
WHOLE_PAGE_MAX = 2000   # chars (~500 tokens) — well under gemini-embedding-001's 2048-token cap
SECTION_MAX = 1600      # a heading-section under this stays one chunk (keeps tables intact)
MIN_CHUNK = 300         # sections smaller than this merge into a neighbor
MIN_KEEP_ALNUM = 50     # chunks with less alphanumeric content than this are dropped

_md_splitter = MarkdownHeaderTextSplitter(HEADERS_TO_SPLIT_ON, strip_headers=False)
_child_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=0,
    separators=["\n\n", "\n", ". ", "? ", "! ", " ", ""],
)


def build_header(title, url, breadcrumb=None):
    lines = [f"Document: {title}", f"Source: {url}"]
    if breadcrumb:
        lines.append(f"Section: {breadcrumb}")
    return "\n".join(lines) + "\n\n"


def _breadcrumb(section_metadata):
    return " > ".join(v for _, v in sorted(section_metadata.items()))


def chunk_page(markdown_text, title, url):
    """Return [{"text": header+body, "metadata": {...}}] ready for ChromaDB."""
    markdown_text = (markdown_text or "").strip()
    if not markdown_text:
        return []

    if len(markdown_text) <= WHOLE_PAGE_MAX:
        return [{
            "text": build_header(title, url) + markdown_text,
            "metadata": {"source": url, "title": title},
        }]

    sections = _md_splitter.split_text(markdown_text)

    # Merge-forward: a small section (often a bare heading stub) is glued onto
    # the FOLLOWING section content by appending into the stub, keeping the
    # stub's heading metadata — so "## Important Dates" travels with its list.
    merged = []
    for sec in sections:
        if merged and len(merged[-1].page_content) < MIN_CHUNK:
            merged[-1].page_content += "\n\n" + sec.page_content
        else:
            merged.append(sec)
    if len(merged) > 1 and len(merged[-1].page_content) < MIN_CHUNK:
        last = merged.pop()
        merged[-1].page_content += "\n\n" + last.page_content

    chunks = []
    for sec in merged:
        body = sec.page_content.strip()
        if sum(c.isalnum() for c in body) < MIN_KEEP_ALNUM:
            continue
        crumb = _breadcrumb(sec.metadata)
        header = build_header(title, url, crumb or None)
        meta = {"source": url, "title": title}
        if crumb:
            meta["section"] = crumb
        if len(body) <= SECTION_MAX:
            chunks.append({"text": header + body, "metadata": dict(meta)})
        else:
            pieces = _child_splitter.split_text(body)
            if len(pieces) > 1 and len(pieces[-1]) < MIN_CHUNK:
                last = pieces.pop()
                pieces[-1] += "\n\n" + last
            for piece in pieces:
                if sum(c.isalnum() for c in piece) < MIN_KEEP_ALNUM:
                    continue
                chunks.append({"text": header + piece, "metadata": dict(meta)})
    return chunks
