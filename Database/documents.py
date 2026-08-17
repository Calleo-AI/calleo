"""
documents.py — Text extraction for linked files (PDF, DOCX).

Sites keep their most authoritative content in attachments: handbooks, fee
schedules, policies, forms. Those links used to be dropped on the floor; now
they are fetched and run through the same chunker as HTML pages, so a document
section is retrievable exactly like a page section.

Output is markdown with headings preserved (DOCX heading styles map to #/##/###,
multi-page PDFs get "## Page N" markers) so chunking.py can split on structure.

pypdf / python-docx are imported lazily inside the functions, matching the
trafilatura and markdownify pattern in extraction.py — the serving VM installs
only requirements.txt and must never need them. A missing dependency raises
RuntimeError, which build_chunks turns into a per-document failure instead of
aborting the whole rebuild.
"""
import re
import sys
from pathlib import Path
from urllib.parse import unquote

from extraction import scrub_junk_lines

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from site_config import DOCUMENT_EXTENSIONS

MAX_PDF_PAGES = 60        # pages beyond this are not read (chunk-count guard)
MIN_PAGE_ALNUM = 20       # a PDF page with less than this is blank or scanned

_HYPHEN_BREAK_RE = re.compile(r"(\w)-\n(\w)")
_HEADING_STYLE_RE = re.compile(r"^Heading (\d)$")

_CONTENT_TYPES = {
    "application/pdf": "pdf",
    "application/x-pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}


def doc_type_for(url, content_type=""):
    """'pdf' | 'docx' | '' — the Content-Type wins, the URL extension is the fallback."""
    base = (content_type or "").split(";")[0].strip().lower()
    if base in _CONTENT_TYPES:
        return _CONTENT_TYPES[base]
    path = (url or "").lower().split("?")[0]
    for ext in DOCUMENT_EXTENSIONS:
        if path.endswith(ext):
            return ext.lstrip(".")
    return ""


def _require(module, package):
    try:
        return __import__(module)
    except ImportError as e:
        raise RuntimeError(
            f"{package} is required to index documents "
            f"(pip install -r requirements-crawl.txt), or set CRAWL_DOCUMENTS = False"
        ) from e


def extract_pdf(data, max_pages=MAX_PDF_PAGES):
    """Return (markdown, pages_read) for PDF bytes. Scanned/empty PDFs return ("", n)."""
    import io

    _require("pypdf", "pypdf")
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages[:max_pages]:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""      # one malformed page must not lose the rest of the document
        text = _HYPHEN_BREAK_RE.sub(r"\1\2", text).strip()
        if sum(c.isalnum() for c in text) >= MIN_PAGE_ALNUM:
            pages.append(text)

    if not pages:
        return "", len(reader.pages[:max_pages])

    # Page markers only earn their keep on documents big enough to be split —
    # on a one-pager they would be noise in the only chunk.
    body = "\n\n".join(pages)
    if len(pages) > 1 and len(body) > 2000:
        body = "\n\n".join(f"## Page {i}\n\n{t}" for i, t in enumerate(pages, 1))
    return scrub_junk_lines(body), len(pages)


def _docx_table_markdown(table):
    rows = [[cell.text.strip().replace("|", "\\|") for cell in row.cells] for row in table.rows]
    rows = [r for r in rows if any(r)]
    if not rows:
        return ""
    head, rest = rows[0], rows[1:]
    lines = ["| " + " | ".join(head) + " |",
             "| " + " | ".join("---" for _ in head) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rest]
    return "\n".join(lines)


def extract_docx(data):
    """Return markdown for DOCX bytes: Heading N styles become #/##/###, tables become pipe tables."""
    import io

    _require("docx", "python-docx")
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(io.BytesIO(data))
    parts = []
    # Walk the body in document order so tables stay where their intro text left them.
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(child, doc)
            text = para.text.strip()
            if not text:
                continue
            match = _HEADING_STYLE_RE.match(para.style.name or "")
            if match:
                level = min(int(match.group(1)), 3)
                parts.append(f"{'#' * level} {text}")
            elif (para.style.name or "") == "Title":
                parts.append(f"# {text}")
            else:
                parts.append(text)
        elif tag == "tbl":
            markdown = _docx_table_markdown(Table(child, doc))
            if markdown:
                parts.append(markdown)
    return scrub_junk_lines("\n\n".join(parts))


def extract_document(data, url, content_type=""):
    """Return (markdown, strategy). strategy is 'pdf', 'docx', or 'unsupported'."""
    kind = doc_type_for(url, content_type)
    if not data:
        return "", kind or "unsupported"
    if kind == "pdf":
        text, _ = extract_pdf(data)
        return text, "pdf"
    if kind == "docx":
        return extract_docx(data), "docx"
    return "", "unsupported"


def document_title(url):
    """Human-readable title from the filename: 'family-handbook_2026.pdf' -> 'Family Handbook 2026'."""
    slug = unquote((url or "").split("?")[0].rstrip("/").split("/")[-1])
    slug = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", slug)
    words = [w for w in re.split(r"[-_.+\s]+", slug) if w]
    return " ".join(w if w.isupper() else w.capitalize() for w in words) or "Document"
