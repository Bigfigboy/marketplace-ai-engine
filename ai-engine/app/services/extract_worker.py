"""Isolated document-parsing worker (Linux, macOS and Windows).

Started by ``app.services.extraction`` as ``python -m app.services.extract_worker`` for every
PDF/DOCX upload. It applies its own memory limit, reads the file from stdin, parses it and
writes ONE JSON object to stdout. The parent enforces a wall-clock timeout and kills the
worker if it is exceeded, so a crafted document can only waste one short-lived process
instead of the web server.

    argv: <pdf|docx> <max_pages> <max_chars> <memory_mb>

Portability rules (the Windows 11 bug fixed here was a violation of the first two):
* the memory limit is platform-specific: ``RLIMIT_AS`` via ``resource`` on Unix, a Job
  Object with ``ProcessMemoryLimit`` on Windows (``resource`` does not exist there);
* stdout carries ASCII-only JSON written as bytes, so the console/pipe code page
  (cp1251/cp1252 on Windows) can never corrupt or crash the output;
* every failure, including import errors, still produces a JSON result.
"""

from __future__ import annotations

import ctypes
import json
import sys

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _JobBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _JobExtendedLimitInformation(ctypes.Structure):
    """JOBOBJECT_EXTENDED_LIMIT_INFORMATION (144 bytes on 64-bit Windows)."""

    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _windows_memory_limit(limit_bytes: int) -> str:
    """Put the current process in a Job Object that caps its committed memory."""
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return "unavailable"
    info = _JobExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_PROCESS_MEMORY
    info.ProcessMemoryLimit = limit_bytes
    if not kernel32.SetInformationJobObject(job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                            ctypes.byref(info), ctypes.sizeof(info)):
        return "unavailable"
    # Nested jobs are supported since Windows 8, so this works even if the server itself
    # already runs inside a job (e.g. some terminals, services or CI runners).
    if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
        return "unavailable"
    return "job_object"  # the handle is intentionally kept open for the process lifetime


def apply_memory_limit(memory_mb: int) -> str:
    """Limit this process's memory. Returns the mechanism used, or "unavailable"/"disabled"."""
    if memory_mb <= 0:
        return "disabled"
    limit = memory_mb * 1024 * 1024
    try:
        if sys.platform == "win32":
            return _windows_memory_limit(limit)
        import resource  # Unix only

        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        return "rlimit"
    except (ImportError, OSError, ValueError, AttributeError):
        return "unavailable"


def _emit(result: dict) -> None:
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=True).encode("ascii"))
    sys.stdout.buffer.flush()


def main() -> int:
    try:
        file_format, max_pages, max_chars, memory_mb = (
            sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]))
        limit_mode = apply_memory_limit(memory_mb)  # before any parsing allocation
        data = sys.stdin.buffer.read()

        from app.core.errors import FileValidationError
        from app.services.extraction import _extract_docx, _extract_pdf
    except MemoryError:
        _emit({"ok": False, "code": "document_too_complex", "message": "The document needs too much memory."})
        return 0
    except BaseException as exc:  # noqa: BLE001 - always answer with JSON
        _emit({"ok": False, "code": "worker_error", "message": f"Worker start-up failed: {type(exc).__name__}"})
        return 0

    try:
        doc = _extract_pdf(data, max_pages, max_chars) if file_format == "pdf" else _extract_docx(data, max_chars)
        result = {"ok": True, "text": doc.text, "page_count": doc.page_count, "warnings": doc.warnings}
    except FileValidationError as exc:
        result = {"ok": False, "code": exc.code, "message": exc.message}
    except MemoryError:
        result = {"ok": False, "code": "document_too_complex", "message": "The document needs too much memory."}
    except Exception:  # noqa: BLE001
        result = {"ok": False, "code": "corrupted_file", "message": "The document cannot be read."}
    result["memory_limit"] = limit_mode
    _emit(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
