"""Safe file-type detection and text extraction for PDF, DOCX and TXT.

Detection and extraction logic adapted from Resume-AI-Parser (MIT,
(c) 2026 Nabi Bukhsh Baloch, https://github.com/NabiBukhsh-AI/Resume-AI-Parser),
``extraction/detection.py`` and ``extraction/extractors.py``. Changes:
* the ``puremagic`` dependency is replaced by explicit signature + structure checks;
* the extension must agree with the detected content (a renamed file is rejected);
* ZIP-bomb limits for DOCX, a page limit for PDF, and a size limit on extracted text;
* error codes for empty, corrupted, encrypted and image-only (OCR-required) documents.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from app.core.errors import FileValidationError

ALLOWED_EXTENSIONS = {".pdf": "pdf", ".docx": "docx", ".txt": "txt"}

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = (b"PK\x03\x04",)
_MAX_DOCX_UNCOMPRESSED = 100 * 1024 * 1024
_MAX_DOCX_ENTRIES = 2_000
_MIN_TEXT_CHARS = 20

_BLANK_RUN_RE = re.compile(r"\n{3,}")
_TRAILING_SPACE_RE = re.compile(r"[ \t]+\n")
_SPACE_RUN_RE = re.compile(r"[ \t]{3,}")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass
class ExtractedDocument:
    text: str
    file_format: str
    page_count: int | None = None
    warnings: list[str] = field(default_factory=list)


def sanitize_filename(filename: str | None) -> str:
    """Reduce a client filename to a safe display name. Never used to build a path."""
    if not filename:
        return "upload"
    candidate = PurePosixPath(filename.replace("\\", "/")).name
    cleaned = "".join(ch for ch in candidate if ch.isprintable() and ch not in '<>:"|?*')
    cleaned = cleaned.strip(". ")
    return cleaned[:255] or "upload"


def normalize_whitespace(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace(" ", " ").replace("​", "")
    normalized = _CONTROL_RE.sub("", normalized)
    normalized = _TRAILING_SPACE_RE.sub("\n", normalized)
    normalized = _SPACE_RUN_RE.sub("  ", normalized)
    normalized = _BLANK_RUN_RE.sub("\n\n", normalized)
    return normalized.strip()


def _is_docx(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > _MAX_DOCX_ENTRIES:
                raise FileValidationError("DOCX archive has too many entries", code="corrupted_file")
            if sum(i.file_size for i in infos) > _MAX_DOCX_UNCOMPRESSED:
                raise FileValidationError("DOCX archive expands beyond the allowed size", code="corrupted_file")
            names = {i.filename for i in infos}
            return "word/document.xml" in names and "[Content_Types].xml" in names
    except zipfile.BadZipFile:
        return False


def _looks_like_text(data: bytes) -> bool:
    if b"\x00" in data[:8192]:
        return False
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        try:
            data.decode("cp1251")
            return True
        except UnicodeDecodeError:
            return False


def detect_format(data: bytes, filename: str) -> str:
    """Return 'pdf' | 'docx' | 'txt'. Content and extension must agree."""
    suffix = PurePosixPath(filename.lower()).suffix
    expected = ALLOWED_EXTENSIONS.get(suffix)
    if expected is None:
        raise FileValidationError("Unsupported file type. Allowed: PDF, DOCX, TXT.",
                                  code="unsupported_file_type", status_code=415)
    if not data:
        raise FileValidationError("The file is empty.", code="empty_file")

    if data.startswith(_PDF_MAGIC):
        detected = "pdf"
    elif data.startswith(_ZIP_MAGIC):
        if not _is_docx(data):
            raise FileValidationError(
                "The file is a ZIP archive but not a valid DOCX document (.doc/.odt are not supported).",
                code="file_type_mismatch")
        detected = "docx"
    elif _looks_like_text(data):
        detected = "txt"
    else:
        raise FileValidationError("The file content is not a supported document.", code="file_type_mismatch")

    if detected != expected:
        raise FileValidationError(
            f"File extension '{suffix}' does not match its actual content ({detected}).",
            code="file_type_mismatch")
    return detected


def _extract_pdf(data: bytes, max_pages: int) -> ExtractedDocument:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    warnings: list[str] = []
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise FileValidationError("The PDF is password-protected.", code="encrypted_pdf")
            except FileValidationError:
                raise
            except Exception as exc:
                raise FileValidationError("The PDF is password-protected.", code="encrypted_pdf") from exc
        page_count = len(reader.pages)
        if page_count > max_pages:
            raise FileValidationError(f"The PDF has more than {max_pages} pages.", code="too_many_pages")
        pages: list[str] = []
        empty_pages = 0
        for index, page in enumerate(reader.pages, start=1):
            try:
                page_text = page.extract_text() or ""
            except Exception:  # noqa: BLE001 - one bad page should not fail the document
                warnings.append(f"page_{index}_unreadable")
                continue
            if page_text.strip():
                pages.append(page_text)
            else:
                empty_pages += 1
        if empty_pages:
            warnings.append(f"{empty_pages}_pages_without_text_layer")
    except FileValidationError:
        raise
    except PdfReadError as exc:
        raise FileValidationError("The PDF is damaged and cannot be read.", code="corrupted_file") from exc
    except Exception as exc:  # noqa: BLE001
        raise FileValidationError("The PDF cannot be read.", code="corrupted_file") from exc

    text = normalize_whitespace("\n\n".join(pages))
    if len(text) < _MIN_TEXT_CHARS:
        raise FileValidationError(
            "No text could be extracted from this PDF: it appears to contain only images (a scan). "
            "OCR is required - run the document through OCR and upload the searchable PDF, DOCX or TXT.",
            code="ocr_required")
    return ExtractedDocument(text=text, file_format="pdf", page_count=page_count, warnings=warnings)


def _extract_docx(data: bytes) -> ExtractedDocument:
    try:
        import docx
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        document = docx.Document(io.BytesIO(data))
        blocks: list[str] = []
        for child in document.element.body.iterchildren():
            if child.tag == qn("w:p"):
                text = Paragraph(child, document).text.strip()
                if text:
                    blocks.append(text)
            elif child.tag == qn("w:tbl"):
                for row in Table(child, document).rows:
                    deduped: list[str] = []
                    for cell in (c.text.strip() for c in row.cells):
                        if cell and (not deduped or deduped[-1] != cell):
                            deduped.append(cell)
                    if deduped:
                        blocks.append(" | ".join(deduped))
        for section in document.sections:
            for container in (section.header, section.footer):
                for paragraph in container.paragraphs:
                    text = paragraph.text.strip()
                    if text and text not in blocks:
                        blocks.append(text)
    except Exception as exc:  # noqa: BLE001
        raise FileValidationError("The DOCX file is damaged and cannot be read.", code="corrupted_file") from exc
    return ExtractedDocument(text=normalize_whitespace("\n".join(blocks)), file_format="docx")


def _extract_txt(data: bytes) -> ExtractedDocument:
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            decoded = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        warnings = [] if encoding.startswith("utf-8") else [f"decoded_as_{encoding}"]
        return ExtractedDocument(text=normalize_whitespace(decoded), file_format="txt", warnings=warnings)
    raise FileValidationError("The text file encoding is not supported (use UTF-8).", code="corrupted_file")


def extract_text(data: bytes, filename: str, *, max_pages: int = 200,
                 max_chars: int = 500_000) -> ExtractedDocument:
    file_format = detect_format(data, filename)
    if file_format == "pdf":
        doc = _extract_pdf(data, max_pages)
    elif file_format == "docx":
        doc = _extract_docx(data)
    else:
        doc = _extract_txt(data)
    if len(doc.text) < _MIN_TEXT_CHARS:
        raise FileValidationError("The document contains no readable text.", code="empty_document")
    if len(doc.text) > max_chars:
        doc.text = doc.text[:max_chars]
        doc.warnings.append("text_truncated")
    return doc
