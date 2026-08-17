"""Tests for Database/documents.py — PDF/DOCX text extraction.

DOCX files are built in-memory with python-docx rather than checked in as
binaries. The PDF path is exercised against a monkeypatched reader, matching
how the rest of the suite handles third-party libraries.
"""
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Database"))

import pytest

import documents
from documents import doc_type_for, document_title, extract_docx, extract_document, extract_pdf

PDF_URL = "https://www.example-site.org/uploads/family-handbook.pdf"
DOCX_URL = "https://www.example-site.org/uploads/enrolment-form.docx"


# --- Type routing -----------------------------------------------------------

def test_content_type_wins_over_a_misleading_extension():
    assert doc_type_for("https://x/report", "application/pdf") == "pdf"
    assert doc_type_for(PDF_URL, "application/pdf; charset=binary") == "pdf"


def test_extension_is_the_fallback_when_content_type_is_absent():
    assert doc_type_for(PDF_URL, "") == "pdf"
    assert doc_type_for(DOCX_URL, "") == "docx"
    assert doc_type_for("https://x/page.html", "") == ""


def test_unknown_type_is_reported_not_guessed():
    assert extract_document(b"data", "https://x/archive.zip", "application/zip") == (
        "", "unsupported"
    )


# --- PDF --------------------------------------------------------------------

class FakePdfPage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        if self._text is None:
            raise ValueError("malformed page")
        return self._text


class FakePdfReader:
    def __init__(self, *_args, **_kwargs):
        self.pages = FakePdfReader.PAGES


@pytest.fixture
def fake_pdf(monkeypatch):
    """Install FakePdfReader as pypdf.PdfReader for the duration of a test."""
    import pypdf

    monkeypatch.setattr(pypdf, "PdfReader", FakePdfReader)

    def _set(texts):
        FakePdfReader.PAGES = [FakePdfPage(t) for t in texts]
    return _set


def test_pdf_pages_are_joined(fake_pdf):
    fake_pdf(["Tuition for the coming year is $45,000 per student."])
    text, pages = extract_pdf(b"%PDF-")
    assert "45,000" in text
    assert pages == 1


def test_pdf_hyphenation_across_line_breaks_is_repaired(fake_pdf):
    fake_pdf(["The enrol-\nment deadline is the first of September each year."])
    text, _ = extract_pdf(b"%PDF-")
    assert "enrolment deadline" in text


def test_blank_and_scanned_pages_are_dropped(fake_pdf):
    fake_pdf(["", "   ", "Real content that clears the alphanumeric floor."])
    text, pages = extract_pdf(b"%PDF-")
    assert pages == 1
    assert text.startswith("Real content")


def test_a_scanned_pdf_yields_no_text_rather_than_junk(fake_pdf):
    fake_pdf(["", " ", "\n"])
    text, _ = extract_pdf(b"%PDF-")
    assert text == ""


def test_one_malformed_page_does_not_lose_the_document(fake_pdf):
    fake_pdf([None, "The rest of the handbook survives a single bad page."])
    text, pages = extract_pdf(b"%PDF-")
    assert "survives a single bad page" in text
    assert pages == 1


def test_page_markers_appear_only_on_documents_big_enough_to_split(fake_pdf):
    fake_pdf(["Short page one.", "Short page two."])
    small, _ = extract_pdf(b"%PDF-")
    assert "## Page 1" not in small          # would be noise in a single chunk

    fake_pdf(["A" * 1200 + " policy text.", "B" * 1200 + " more policy text."])
    large, _ = extract_pdf(b"%PDF-")
    assert large.startswith("## Page 1")
    assert "## Page 2" in large              # real split points for chunking.py


def test_max_pages_guard_truncates_long_pdfs(fake_pdf):
    fake_pdf([f"Page {i} of a very long appendix document." for i in range(200)])
    _, pages = extract_pdf(b"%PDF-", max_pages=5)
    assert pages == 5


def test_extract_document_routes_pdf_bytes(fake_pdf):
    fake_pdf(["The family handbook covers dress code and attendance."])
    text, strategy = extract_document(b"%PDF-", PDF_URL)
    assert strategy == "pdf"
    assert "dress code" in text


def test_missing_pypdf_raises_a_clear_runtime_error(monkeypatch):
    def boom(module, package):
        raise RuntimeError(f"{package} is required to index documents")
    monkeypatch.setattr(documents, "_require", boom)
    with pytest.raises(RuntimeError, match="pypdf is required"):
        extract_pdf(b"%PDF-")


# --- DOCX -------------------------------------------------------------------

def _docx_bytes(build):
    from docx import Document

    doc = Document()
    build(doc)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def test_docx_heading_styles_become_markdown_headings():
    def build(doc):
        doc.add_heading("Enrolment", level=1)
        doc.add_paragraph("Complete every section of this form.")
        doc.add_heading("Fees", level=2)
        doc.add_paragraph("Tuition is billed in three instalments.")

    text = extract_docx(_docx_bytes(build))
    assert "# Enrolment" in text
    assert "## Fees" in text
    assert "Complete every section" in text


def test_docx_heading_levels_are_capped_at_three():
    # chunking.py splits on #/##/### only; a Heading 5 must not vanish into a
    # level the splitter ignores.
    def build(doc):
        doc.add_heading("Deep section", level=5)

    assert "### Deep section" in extract_docx(_docx_bytes(build))


def test_docx_tables_become_pipe_tables_in_document_order():
    def build(doc):
        doc.add_paragraph("Fee schedule:")
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "Grade"
        table.cell(0, 1).text = "Tuition"
        table.cell(1, 0).text = "Year 7"
        table.cell(1, 1).text = "$45,000"

    text = extract_docx(_docx_bytes(build))
    assert text.index("Fee schedule:") < text.index("| Grade | Tuition |")
    assert "| --- | --- |" in text
    assert "| Year 7 | $45,000 |" in text


def test_extract_document_routes_docx_bytes():
    def build(doc):
        doc.add_paragraph("Please return this form to the front office.")

    text, strategy = extract_document(_docx_bytes(build), DOCX_URL)
    assert strategy == "docx"
    assert "front office" in text


def test_empty_body_short_circuits_before_any_parsing():
    assert extract_document(b"", PDF_URL) == ("", "pdf")


# --- Titles -----------------------------------------------------------------

def test_document_title_is_read_from_the_filename():
    assert document_title(PDF_URL) == "Family Handbook"
    assert document_title("https://x/uploads/2026_fee-schedule.docx") == "2026 Fee Schedule"
    assert document_title("https://x/uploads/FAQ.pdf") == "FAQ"
    assert document_title("") == "Document"
