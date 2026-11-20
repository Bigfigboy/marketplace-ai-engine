"""File storage with per-company isolation.

Layout (outside any web-served directory, created with 0700/0600 permissions):

    STORAGE_DIR/<company_id>/<file_id>.bin   original bytes
    STORAGE_DIR/<company_id>/<file_id>.txt   extracted text

Original filenames are never used in paths. Access rules:
* any file is visible only inside its own company;
* ``knowledge_base`` files are shared within the company (the support agent needs them);
* other purposes (resume, job_description, brief) are private to the uploader and admins;
* only company admins may upload ``knowledge_base`` files;
* deletion is allowed to the uploader and company admins.
Unauthorized access returns 404 so file ids cannot be probed across companies.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import FileValidationError, ForbiddenError, NotFoundError
from app.database.models import DocumentChunk, StoredFile
from app.security.auth import Principal
from app.security.prompt_guard import sanitize_document_text
from app.services.extraction import extract_text, sanitize_filename
from app.services.rag import chunk_text

logger = logging.getLogger("app.files")

FILE_PURPOSES = ("knowledge_base", "resume", "job_description", "brief")


@dataclass
class LoadedFile:
    id: str
    filename: str
    purpose: str
    file_format: str
    text: str


def _company_dir(settings: Settings, company_id: str) -> Path:
    path = Path(settings.storage_dir) / company_id
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _write_private(path: Path, data: bytes) -> None:
    # O_BINARY (Windows only) prevents CRT newline translation that would corrupt stored files.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def save_upload(db: Session, settings: Settings, principal: Principal, *, filename: str | None,
                data: bytes, purpose: str) -> tuple[StoredFile, list[str]]:
    if purpose not in FILE_PURPOSES:
        raise FileValidationError(f"Unknown purpose. Allowed: {', '.join(FILE_PURPOSES)}", code="invalid_purpose")
    if purpose == "knowledge_base" and not principal.is_admin:
        # The knowledge base is shared company-wide and answers customers: only admins may change it.
        raise ForbiddenError("Only company admins can upload knowledge-base documents.",
                             code="knowledge_base_admin_only")
    if len(data) > settings.max_file_size_bytes:
        raise FileValidationError(f"File exceeds the {settings.max_file_size_mb:g} MB limit.",
                                  code="file_too_large", status_code=413)
    safe_name = sanitize_filename(filename)
    doc = extract_text(data, safe_name, max_pages=settings.max_pdf_pages,
                       max_chars=settings.max_extracted_chars,
                       timeout_seconds=settings.extraction_timeout_seconds,
                       memory_mb=settings.extraction_memory_mb)

    record = StoredFile(company_id=principal.company_id, user_id=principal.user_id, purpose=purpose,
                        filename=safe_name, file_format=doc.file_format, size_bytes=len(data),
                        sha256=hashlib.sha256(data).hexdigest(), char_count=len(doc.text),
                        page_count=doc.page_count)
    db.add(record)
    db.flush()

    warnings = list(doc.warnings)
    if purpose == "knowledge_base":
        flagged_total = 0
        for index, text in enumerate(chunk_text(doc.text, size=settings.rag_chunk_chars,
                                                overlap=settings.rag_chunk_overlap)):
            flagged = sanitize_document_text(text).flagged
            flagged_total += flagged
            db.add(DocumentChunk(file_id=record.id, company_id=principal.company_id,
                                 chunk_index=index, text=text, flagged_injection=flagged > 0))
            record.chunk_count = index + 1
        record.injection_flags = flagged_total
        if flagged_total:
            warnings.append("instruction_like_text_detected_will_be_ignored")

    directory = _company_dir(settings, principal.company_id)
    try:
        _write_private(directory / f"{record.id}.bin", data)
        _write_private(directory / f"{record.id}.txt", doc.text.encode("utf-8"))
        db.commit()
    except Exception:
        db.rollback()
        _remove_blobs(settings, principal.company_id, record.id)
        raise
    logger.info("file_uploaded", extra={"file_id": record.id, "format": record.file_format,
                                         "size_bytes": record.size_bytes, "purpose": purpose,
                                         "chunks": record.chunk_count})
    return record, warnings


def _can_read(principal: Principal, record: StoredFile) -> bool:
    if record.company_id != principal.company_id:
        return False
    return record.user_id == principal.user_id or principal.is_admin or record.purpose == "knowledge_base"


def get_file(db: Session, principal: Principal, file_id: str) -> StoredFile:
    record = db.get(StoredFile, file_id) if len(file_id) <= 64 else None
    if record is None or not _can_read(principal, record):
        raise NotFoundError("File not found", code="file_not_found")
    return record


def list_files(db: Session, principal: Principal, purpose: str | None = None) -> list[StoredFile]:
    stmt = select(StoredFile).where(StoredFile.company_id == principal.company_id)
    if not principal.is_admin:
        stmt = stmt.where((StoredFile.user_id == principal.user_id) | (StoredFile.purpose == "knowledge_base"))
    if purpose:
        stmt = stmt.where(StoredFile.purpose == purpose)
    return list(db.scalars(stmt.order_by(StoredFile.created_at.desc())))


def delete_file(db: Session, settings: Settings, principal: Principal, file_id: str) -> None:
    record = get_file(db, principal, file_id)
    if record.user_id != principal.user_id and not principal.is_admin:
        raise NotFoundError("File not found", code="file_not_found")
    db.execute(delete(DocumentChunk).where(DocumentChunk.file_id == record.id))
    db.delete(record)
    db.commit()
    _remove_blobs(settings, record.company_id, record.id)
    logger.info("file_deleted", extra={"file_id": record.id})


def _remove_blobs(settings: Settings, company_id: str, file_id: str) -> None:
    directory = Path(settings.storage_dir) / company_id
    for suffix in (".bin", ".txt"):
        try:
            (directory / f"{file_id}{suffix}").unlink(missing_ok=True)
        except OSError:
            logger.warning("file_blob_delete_failed", extra={"file_id": file_id})


def load_files(db: Session, settings: Settings, principal: Principal, file_ids: list[str]) -> list[LoadedFile]:
    loaded: list[LoadedFile] = []
    for file_id in dict.fromkeys(file_ids):  # de-duplicate, keep order
        record = get_file(db, principal, file_id)
        path = Path(settings.storage_dir) / record.company_id / f"{record.id}.txt"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise NotFoundError("File content is no longer available", code="file_not_found") from exc
        loaded.append(LoadedFile(id=record.id, filename=record.filename, purpose=record.purpose,
                                 file_format=record.file_format, text=text))
    return loaded
