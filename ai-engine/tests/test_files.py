"""File upload: PDF, DOCX, TXT, validation, OCR detection, deletion, storage isolation."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from tests.conftest import (
    FICTIONAL_RESUME_EN_PDF,
    FICTIONAL_RESUME_RU,
    KB_COFFEE_RU,
    make_docx,
    make_image_only_pdf,
    make_pdf,
)


def test_upload_pdf(env):
    r = env.upload(env.a.admin, "resume.pdf", make_pdf(FICTIONAL_RESUME_EN_PDF), purpose="resume")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["file_format"] == "pdf" and body["page_count"] == 1 and body["char_count"] > 50


def test_upload_docx_with_table_and_chunks(env):
    data = make_docx(FICTIONAL_RESUME_RU, table=[["Навык", "Уровень"], ["Excel", "продвинутый"]])
    r = env.upload(env.a.admin, "kb.docx", data, purpose="knowledge_base")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["file_format"] == "docx" and body["chunk_count"] >= 1
    assert body["char_count"] > 100


def test_upload_txt_cp1251(env):
    r = env.upload(env.a.admin, "kb.txt", KB_COFFEE_RU.encode("cp1251"))
    assert r.status_code == 201, r.text
    assert "decoded_as_cp1251" in r.json()["warnings"]


def test_invalid_extension_rejected(env):
    r = env.upload(env.a.admin, "malware.exe", b"MZ\x90\x00binary")
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported_file_type"


def test_extension_content_mismatch_rejected(env):
    r = env.upload(env.a.admin, "fake.pdf", b"This is plain text pretending to be a PDF file.")
    assert r.status_code == 422 and r.json()["error"]["code"] == "file_type_mismatch"
    r = env.upload(env.a.admin, "fake.docx", make_pdf(["hello world text layer"]))
    assert r.json()["error"]["code"] == "file_type_mismatch"
    r = env.upload(env.a.admin, "binary.txt", b"\x00\x01\x02\xff\xfe" * 20)
    assert r.json()["error"]["code"] == "file_type_mismatch"


def test_corrupted_and_empty_files(env):
    r = env.upload(env.a.admin, "broken.pdf", b"%PDF-1.4\n garbage without objects")
    assert r.status_code == 422 and r.json()["error"]["code"] in ("corrupted_file", "ocr_required")
    r = env.upload(env.a.admin, "broken.docx", b"PK\x03\x04 not really a zip")
    assert r.json()["error"]["code"] == "file_type_mismatch"
    r = env.upload(env.a.admin, "empty.txt", b"")
    assert r.json()["error"]["code"] == "empty_file"
    r = env.upload(env.a.admin, "short.txt", b"   \n  ")
    assert r.json()["error"]["code"] == "empty_document"


def test_image_only_pdf_requires_ocr(env):
    r = env.upload(env.a.admin, "scan.pdf", make_image_only_pdf())
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "ocr_required" and "OCR" in err["message"]


def test_file_size_limit(make_env):
    env = make_env(max_file_size_mb=0.01)  # ~10 KB for the test
    r = env.upload(env.a.admin, "big.txt", ("а" * 20_000).encode())
    assert r.status_code == 413 and r.json()["error"]["code"] == "file_too_large"


def test_default_limit_is_10mb(env):
    assert env.settings.max_file_size_bytes == 10 * 1024 * 1024
    big = b"a" * (10 * 1024 * 1024 + 200 * 1024)
    r = env.upload(env.a.admin, "big.txt", big)
    assert r.status_code == 413 and r.json()["error"]["code"] == "file_too_large"


def test_delete_file_removes_blobs_and_chunks(env):
    r = env.upload(env.a.admin, "kb.txt", KB_COFFEE_RU.encode())
    fid = r.json()["id"]
    company_dir = Path(env.settings.storage_dir) / env.a.company_id
    assert (company_dir / f"{fid}.bin").exists()
    # Stored privately, outside any web-served directory, under a random name.
    mode = stat.S_IMODE(os.stat(company_dir / f"{fid}.bin").st_mode)
    assert mode == 0o600
    assert "kb.txt" not in os.listdir(company_dir)
    assert env.client.delete(f"/files/{fid}", headers=env.a.admin).status_code == 204
    assert not (company_dir / f"{fid}.bin").exists() and not (company_dir / f"{fid}.txt").exists()
    assert env.client.get(f"/files/{fid}", headers=env.a.admin).status_code == 404
    from app.database.models import DocumentChunk
    with env.db() as db:
        assert db.query(DocumentChunk).filter_by(file_id=fid).count() == 0


def test_uploads_not_served_by_demo_static(env):
    r = env.upload(env.a.admin, "kb.txt", KB_COFFEE_RU.encode())
    fid = r.json()["id"]
    for path in (f"/demo/{fid}.bin", f"/demo/../data/uploads/{env.a.company_id}/{fid}.bin"):
        r2 = env.client.get(path)
        assert r2.status_code in (401, 404) and "Доставка" not in r2.text


def test_filename_is_sanitized(env):
    r = env.upload(env.a.admin, "../../etc/passwd.txt", KB_COFFEE_RU.encode())
    assert r.status_code == 201 and r.json()["filename"] == "passwd.txt"


def test_knowledge_base_upload_is_admin_only(env):
    r = env.upload(env.a.member, "kb.txt", KB_COFFEE_RU.encode(), purpose="knowledge_base")
    assert r.status_code == 403 and r.json()["error"]["code"] == "knowledge_base_admin_only"
    assert env.upload(env.a.member, "brief.txt", KB_COFFEE_RU.encode(), purpose="brief").status_code == 201
