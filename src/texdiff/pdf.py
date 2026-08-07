"""PDF output backends."""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .models import DiffReport, PdfEngine
from .native_pdf import NativePdfError, write_native_pdf


class PdfError(RuntimeError):
    pass


def _validate_pdf(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < 100:
        raise PdfError(f"PDF engine did not create a valid output file: {path}")
    with path.open("rb") as handle:
        signature = handle.read(5)
    if signature != b"%PDF-":
        raise PdfError(f"Output is not a PDF file: {path}")


def _reportlab(report: DiffReport, pdf_path: Path, *, include_hidden_unchanged: bool, theme_path: str | Path | None = None) -> None:
    try:
        write_native_pdf(report, pdf_path, include_hidden_unchanged=include_hidden_unchanged, theme_path=theme_path)
    except NativePdfError as exc:
        raise PdfError(str(exc)) from exc
    _validate_pdf(pdf_path)


def _weasyprint(html_path: Path, pdf_path: Path) -> None:
    diagnostics = io.StringIO()
    try:
        # WeasyPrint prints native-library diagnostics while importing on Windows.
        # Capture them so auto fallback remains readable.
        with contextlib.redirect_stdout(diagnostics), contextlib.redirect_stderr(diagnostics):
            from weasyprint import HTML
            HTML(filename=str(html_path), base_url=str(html_path.parent)).write_pdf(str(pdf_path))
    except Exception as exc:
        detail = diagnostics.getvalue().strip()
        suffix = f" ({detail.splitlines()[-1]})" if detail else ""
        raise PdfError(f"WeasyPrint PDF export failed: {exc}{suffix}") from exc
    _validate_pdf(pdf_path)


def _playwright(html_path: Path, pdf_path: Path) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PdfError("Playwright is not installed") from exc
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(html_path.as_uri(), wait_until="load", timeout=30000)
                page.pdf(path=str(pdf_path), print_background=True, prefer_css_page_size=True)
            finally:
                browser.close()
    except Exception as exc:
        raise PdfError(
            "Playwright PDF export failed. Install its browser with "
            f"'python -m playwright install chromium': {exc}"
        ) from exc
    _validate_pdf(pdf_path)


def _known_browser_paths(*, platform_name: str | None = None, environ: dict[str, str] | None = None) -> list[Path]:
    paths: list[Path] = []
    platform_name = platform_name or sys.platform
    environ = environ or dict(os.environ)
    if platform_name.startswith("win"):
        roots = [
            environ.get("PROGRAMFILES"),
            environ.get("PROGRAMFILES(X86)"),
            environ.get("LOCALAPPDATA"),
        ]
        relatives = [
            Path("Google/Chrome/Application/chrome.exe"),
            Path("Microsoft/Edge/Application/msedge.exe"),
            Path("Chromium/Application/chrome.exe"),
        ]
        for root in roots:
            if root:
                paths.extend(Path(root) / relative for relative in relatives)
    elif platform_name == "darwin":
        paths.extend(
            [
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            ]
        )
    return paths


def _find_chromium() -> str | None:
    names = (
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
        "chrome",
        "msedge",
        "microsoft-edge",
    )
    for name in names:
        if executable := shutil.which(name):
            return executable
    for path in _known_browser_paths():
        if path.is_file():
            return str(path)
    return None


def _chromium(html_path: Path, pdf_path: Path) -> None:
    executable = _find_chromium()
    if not executable:
        raise PdfError("Chromium, Google Chrome, or Microsoft Edge was not found")
    cmd = [
        executable,
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--print-to-pdf={pdf_path}",
        html_path.as_uri(),
    ]
    kwargs: dict[str, object] = {
        "capture_output": True,
        "text": True,
        "check": False,
        "timeout": 90,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        cmd[2:2] = ["--no-sandbox", "--disable-dev-shm-usage"]
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        raise PdfError(
            "Chromium PDF export failed: "
            f"{result.stderr.strip() or result.stdout.strip() or 'unknown error'}"
        )
    _validate_pdf(pdf_path)


def backend_status() -> dict[str, dict[str, str | bool]]:
    """Return lightweight diagnostics without requiring document inputs."""
    status: dict[str, dict[str, str | bool]] = {}
    try:
        import reportlab

        status["reportlab"] = {
            "available": True,
            "detail": f"ReportLab {getattr(reportlab, 'Version', 'installed')}",
        }
    except ImportError:
        status["reportlab"] = {
            "available": False,
            "detail": 'Install with: pip install -e ".[pdf]"',
        }

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser_path = Path(playwright.chromium.executable_path)
        status["playwright"] = {
            "available": browser_path.is_file(),
            "detail": str(browser_path) if browser_path.is_file() else "Browser binary not installed",
        }
    except Exception as exc:
        status["playwright"] = {"available": False, "detail": str(exc)}

    browser = _find_chromium()
    status["chromium"] = {
        "available": bool(browser),
        "detail": browser or "Chrome/Edge/Chromium not found",
    }

    # Do not import WeasyPrint here: a broken Windows native-library setup may
    # print several lines to the terminal merely during import.
    status["weasyprint"] = {
        "available": "not-probed",
        "detail": "Optional legacy backend; probe only when explicitly selected",
    }
    return status


def write_pdf(
    html_path: Path,
    pdf_path: Path,
    *,
    report: DiffReport | None = None,
    engine: PdfEngine = "auto",
    include_hidden_unchanged: bool = False,
    theme_path: str | Path | None = None,
) -> tuple[Path, str]:
    html_path = html_path.resolve()
    pdf_path = pdf_path.resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if not html_path.is_file():
        raise PdfError(f"HTML report not found: {html_path}")

    failures: list[str] = []
    candidates = [engine] if engine != "auto" else ["reportlab", "playwright", "chromium", "weasyprint"]
    for candidate in candidates:
        try:
            if candidate == "reportlab":
                if report is None:
                    raise PdfError("ReportLab backend requires a DiffReport")
                _reportlab(report, pdf_path, include_hidden_unchanged=include_hidden_unchanged, theme_path=theme_path)
            elif candidate == "weasyprint":
                _weasyprint(html_path, pdf_path)
            elif candidate == "playwright":
                _playwright(html_path, pdf_path)
            elif candidate == "chromium":
                _chromium(html_path, pdf_path)
            else:
                raise PdfError(f"Unknown PDF engine: {candidate}")
            return pdf_path, candidate
        except Exception as exc:
            failures.append(f"{candidate}: {exc}")
            pdf_path.unlink(missing_ok=True)
    raise PdfError("; ".join(failures))
