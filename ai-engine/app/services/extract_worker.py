"""Isolated document-parsing worker.

Started by ``app.services.extraction`` as ``python -m app.services.extract_worker`` for every
PDF/DOCX upload. It reads the file from stdin, applies its own memory limit, parses the
document and writes a JSON result to stdout. The parent enforces a wall-clock timeout and
kills the worker if it is exceeded, so a crafted document can only waste one short-lived
process instead of the web server.

    argv: <pdf|docx> <max_pages> <max_chars> <memory_mb>
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    file_format, max_pages, max_chars, memory_mb = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    if memory_mb > 0:
        import resource

        limit = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    data = sys.stdin.buffer.read()

    from app.core.errors import FileValidationError
    from app.services.extraction import _extract_docx, _extract_pdf

    try:
        doc = _extract_pdf(data, max_pages, max_chars) if file_format == "pdf" else _extract_docx(data, max_chars)
        result = {"ok": True, "text": doc.text, "page_count": doc.page_count, "warnings": doc.warnings}
    except FileValidationError as exc:
        result = {"ok": False, "code": exc.code, "message": exc.message}
    except MemoryError:
        result = {"ok": False, "code": "document_too_complex", "message": "The document needs too much memory."}
    except Exception:  # noqa: BLE001
        result = {"ok": False, "code": "corrupted_file", "message": "The document cannot be read."}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
