"""Regression tests for document extraction on Windows (bug: every PDF/DOCX upload failed
with ``document_too_complex`` on Windows 11).

Root causes:
1. the worker imported the Unix-only ``resource`` module before its error handling, so on
   Windows it crashed with an empty stdout, which the parent misreported as
   ``document_too_complex``;
2. the worker wrote non-ASCII JSON to stdout, whose encoding on Windows pipes is the ANSI
   code page (cp1251/cp1252): Kazakh letters crashed it, Russian text arrived undecodable;
3. any worker crash was reported as a problem with the user's document.

Platform-independent tests reproduce the Windows conditions (no ``resource`` module, ANSI
stdout) on any OS. Tests marked ``windows_only`` exercise the real Job Object memory limit
and run on Windows (locally or in the GitHub Actions ``windows-latest`` job).
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.errors import AppError, FileValidationError
from app.services import extract_worker, extraction
from app.services.extraction import extract_text
from tests.conftest import make_docx, make_pdf

APP_ROOT = Path(extraction.__file__).resolve().parents[2]
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="requires Windows")
unix_only = pytest.mark.skipif(sys.platform == "win32", reason="requires a Unix-like OS")

KAZAKH_TEXT = "Қазақша мәтін: баға 1 000 ₸. Русский текст. English text. (вымышленный документ)"

SIMULATE_WINDOWS = '''\
# Simulates Windows for the worker process: the Unix-only "resource" module is missing.
import sys


class _NoResource:
    def find_spec(self, name, path=None, target=None):
        if name == "resource":
            raise ModuleNotFoundError("No module named 'resource' (simulated Windows)", name="resource")
        return None


sys.modules.pop("resource", None)
sys.meta_path.insert(0, _NoResource())
'''


def small_pdf() -> bytes:
    """About 1.5 KB, one page of ordinary text — the document from the bug report."""
    data = make_pdf(["Fictional one-page document used for upload testing."] * 15)
    assert 1000 < len(data) < 2500
    return data


@pytest.fixture
def simulated_windows(tmp_path, monkeypatch):
    """Worker processes started by the engine behave as on Windows (no `resource`, cp1251 pipes)."""
    sim = tmp_path / "winsim"
    sim.mkdir()
    (sim / "sitecustomize.py").write_text(SIMULATE_WINDOWS, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(sim))
    monkeypatch.setenv("PYTHONIOENCODING", "cp1251")  # the engine must override this for the worker
    return sim


def run_worker(data: bytes, fmt: str, *, env: dict[str, str] | None = None, memory_mb: int = 1024) -> dict:
    proc = subprocess.run([sys.executable, "-m", "app.services.extract_worker", fmt, "200", "500000",  # noqa: S603
                           str(memory_mb)], input=data, capture_output=True, cwd=str(APP_ROOT),
                          env={**os.environ, **(env or {})}, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    out = proc.stdout.decode("ascii")  # must be pure ASCII, whatever the console code page is
    return json.loads(out)


# ── platform-independent reproductions of the Windows conditions ─────────────
def test_small_pdf_and_docx_without_resource_module(simulated_windows):
    pdf = extract_text(small_pdf(), "simple.pdf")
    assert pdf.file_format == "pdf" and pdf.page_count == 1 and "Fictional one-page" in pdf.text
    docx = extract_text(make_docx([KAZAKH_TEXT]), "simple.docx")
    assert docx.text == KAZAKH_TEXT


def test_worker_survives_missing_resource_module(tmp_path):
    (tmp_path / "sitecustomize.py").write_text(SIMULATE_WINDOWS, encoding="utf-8")
    result = run_worker(small_pdf(), "pdf", env={"PYTHONPATH": f"{APP_ROOT}{os.pathsep}{tmp_path}"})
    assert result["ok"] is True and result["memory_limit"] == "unavailable"


@pytest.mark.parametrize("encoding", ["cp1251", "cp1252", "ascii"])
def test_worker_output_is_independent_of_console_code_page(encoding):
    result = run_worker(make_docx([KAZAKH_TEXT]), "docx", env={"PYTHONIOENCODING": encoding})
    assert result["ok"] is True and result["text"] == KAZAKH_TEXT


def test_upload_small_pdf_via_api_in_simulated_windows(env, simulated_windows):
    r = env.upload(env.a.admin, "simple.pdf", small_pdf())
    assert r.status_code == 201, r.text
    assert r.json()["page_count"] == 1 and r.json()["chunk_count"] == 1
    r = env.upload(env.a.member, "резюме.docx", make_docx(["Опыт работы", KAZAKH_TEXT]), purpose="resume")
    assert r.status_code == 201, r.text


def test_worker_environment_is_explicit(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/some/other/path")
    monkeypatch.setenv("PYTHONIOENCODING", "cp1251")
    env = extraction._worker_env()
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(APP_ROOT)
    assert env["PYTHONIOENCODING"] == "utf-8" and env["PYTHONUTF8"] == "1"


def test_worker_crash_is_a_server_error_not_a_document_error(monkeypatch):
    monkeypatch.setattr(extraction, "_worker_command",
                        lambda *a: [sys.executable, "-c", "import sys; sys.stderr.write('ImportError: x'); sys.exit(1)"])
    with pytest.raises(AppError) as exc:
        extract_text(small_pdf(), "simple.pdf")
    assert exc.value.code == "document_processing_failed" and exc.value.status_code == 500
    assert not isinstance(exc.value, FileValidationError)


def test_worker_startup_failure_is_reported_as_server_error(monkeypatch):
    monkeypatch.setattr(extraction, "_worker_command",
                        lambda *a: [sys.executable, "-c", "import json,sys; sys.stdout.write(json.dumps("
                                                          "{'ok': False, 'code': 'worker_error', 'message': 'x'}))"])
    with pytest.raises(AppError) as exc:
        extract_text(small_pdf(), "simple.pdf")
    assert exc.value.code == "document_processing_failed"


def test_worker_killed_by_memory_limit_is_document_too_complex(monkeypatch):
    monkeypatch.setattr(extraction, "_worker_command",
                        lambda *a: [sys.executable, "-c", "import sys; sys.stderr.write('MemoryError'); sys.exit(1)"])
    with pytest.raises(FileValidationError) as exc:
        extract_text(small_pdf(), "simple.pdf")
    assert exc.value.code == "document_too_complex"


def test_timeout_still_enforced(monkeypatch):
    monkeypatch.setattr(extraction, "_worker_command",
                        lambda *a: [sys.executable, "-c", "import time; time.sleep(30)"])
    with pytest.raises(FileValidationError) as exc:
        extract_text(small_pdf(), "simple.pdf", timeout_seconds=1)
    assert exc.value.code == "document_too_complex"


def test_job_object_structure_layout():
    """The ctypes layout must match JOBOBJECT_EXTENDED_LIMIT_INFORMATION on 64-bit Windows."""
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(extract_worker._JobBasicLimitInformation) == 64
        assert ctypes.sizeof(extract_worker._IoCounters) == 48
        assert ctypes.sizeof(extract_worker._JobExtendedLimitInformation) == 144
        assert extract_worker._JobExtendedLimitInformation.ProcessMemoryLimit.offset == 112


def test_memory_limit_disabled_and_unavailable_paths(monkeypatch):
    assert extract_worker.apply_memory_limit(0) == "disabled"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(extract_worker, "_windows_memory_limit", lambda limit: (_ for _ in ()).throw(OSError("x")))
    assert extract_worker.apply_memory_limit(64) == "unavailable"  # never crashes the worker


# ── real OS mechanisms ───────────────────────────────────────────────────────
MEMORY_PROBE = (
    "import sys; from app.services.extract_worker import apply_memory_limit; mode = apply_memory_limit(128)\n"
    "try:\n    block = bytearray(512 * 1024 * 1024)\n    print(mode, 'allocated')\n"
    "except MemoryError:\n    print(mode, 'MemoryError')\n"
)


def _probe() -> str:
    proc = subprocess.run([sys.executable, "-c", MEMORY_PROBE], capture_output=True, cwd=str(APP_ROOT),  # noqa: S603
                          env=extraction._worker_env(), timeout=60, check=False)
    return proc.stdout.decode().strip()


@unix_only
def test_unix_rlimit_blocks_large_allocation():
    assert run_worker(small_pdf(), "pdf")["memory_limit"] == "rlimit"
    assert _probe() == "rlimit MemoryError"


@windows_only
def test_windows_job_object_blocks_large_allocation():
    assert _probe() == "job_object MemoryError"


@windows_only
def test_windows_worker_uses_job_object_and_extracts_small_pdf():
    result = run_worker(small_pdf(), "pdf")
    assert result["ok"] is True and result["memory_limit"] == "job_object" and result["page_count"] == 1


@windows_only
def test_windows_upload_pdf_and_docx(env):
    r = env.upload(env.a.admin, "simple.pdf", small_pdf())
    assert r.status_code == 201, r.text
    r = env.upload(env.a.admin, "kk.docx", make_docx([KAZAKH_TEXT]))
    assert r.status_code == 201, r.text
