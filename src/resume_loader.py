"""Per-role resume loading from PDF files.

Resumes are stored in the resumes/ directory with the naming convention:
    hj_resume_{normalized_role}.pdf

For example:
    hj_resume_data_engineer.pdf
    hj_resume_software_engineer.pdf
    hj_resume_ai_engineer.pdf

Text is extracted locally using pdfplumber (no external APIs).
Falls back to resume.txt if no per-role PDF is found.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def normalize_role(role: str) -> str:
    """Convert role name to filename-safe format.

    "Data Engineer" → "data_engineer"
    "AI Engineer"   → "ai_engineer"
    "ML Engineer"   → "ml_engineer"
    """
    name = role.lower().strip()
    name = re.sub(r"[^a-z0-9]+", "_", name)
    return name.strip("_")


def _extract_pdf_text(pdf_path: Path) -> str:
    """Extract all text from a PDF file using pdfplumber."""
    import pdfplumber

    text_parts: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n".join(text_parts)


def _find_any_pdf(resumes_dir: Path) -> Path | None:
    """Find any PDF file in the resumes directory as a generic fallback."""
    pdfs = sorted(resumes_dir.glob("*.pdf"))
    return pdfs[0] if pdfs else None


def _load_fallback_resume(fallback_path: Path) -> str | None:
    """Load the legacy resume.txt fallback."""
    if fallback_path.exists():
        text = fallback_path.read_text(encoding="utf-8", errors="ignore").strip()
        if text:
            return text
    return None


def load_resume_for_role(
    resumes_dir: Path,
    role: str,
    fallback_path: Path | None = None,
) -> str | None:
    """Load resume text for a specific role.

    Lookup order:
      1. resumes_dir/hj_resume_{normalized_role}.pdf  (role-specific)
      2. Any PDF in resumes_dir                       (generic fallback)
      3. fallback_path (resume.txt)                   (legacy text fallback)

    Returns None if no resume available for this role.
    """
    normalized = normalize_role(role)
    pdf_name = f"hj_resume_{normalized}.pdf"
    pdf_path = resumes_dir / pdf_name

    # 1. Role-specific PDF
    if pdf_path.exists():
        try:
            text = _extract_pdf_text(pdf_path)
            if text.strip():
                logger.info(f"Loaded resume PDF for '{role}': {pdf_path.name}")
                return text
            logger.warning(f"Resume PDF for '{role}' is empty: {pdf_path.name}")
        except Exception as e:
            logger.error(f"Failed to extract text from {pdf_path.name}: {e}")

    # 2. Any PDF in resumes/ directory
    generic_pdf = _find_any_pdf(resumes_dir)
    if generic_pdf and generic_pdf != pdf_path:
        try:
            text = _extract_pdf_text(generic_pdf)
            if text.strip():
                logger.info(f"No role-specific PDF for '{role}', using: {generic_pdf.name}")
                return text
        except Exception as e:
            logger.error(f"Failed to extract text from {generic_pdf.name}: {e}")

    # 3. Legacy resume.txt
    if fallback_path:
        fallback_text = _load_fallback_resume(fallback_path)
        if fallback_text:
            logger.info(f"No PDF for '{role}', using fallback: {fallback_path.name}")
            return fallback_text

    logger.warning(f"No resume found for '{role}' (expected {pdf_name}). Scoring will be skipped for this role.")
    return None


def load_all_resumes(
    resumes_dir: Path,
    roles: list[str],
    fallback_path: Path | None = None,
) -> dict[str, str]:
    """Load resume text for all roles.

    Returns a dict mapping role → extracted text.
    Roles with no available resume are omitted from the result.
    """
    resumes: dict[str, str] = {}
    for role in roles:
        text = load_resume_for_role(resumes_dir, role, fallback_path)
        if text:
            resumes[role] = text
    return resumes


def get_resume_status(
    resumes_dir: Path,
    roles: list[str],
) -> dict[str, dict]:
    """Check resume availability for each role (used by UI).

    Returns a dict mapping role → {"available": bool, "path": str, "source": str}.
    source is "role_pdf", "generic_pdf", or "none".
    """
    generic_pdf = _find_any_pdf(resumes_dir)
    status: dict[str, dict] = {}
    for role in roles:
        normalized = normalize_role(role)
        pdf_name = f"hj_resume_{normalized}.pdf"
        pdf_path = resumes_dir / pdf_name
        if pdf_path.exists():
            status[role] = {"available": True, "path": str(pdf_path), "source": "role_pdf"}
        elif generic_pdf:
            status[role] = {"available": True, "path": str(generic_pdf), "source": "generic_pdf"}
        else:
            status[role] = {"available": False, "path": str(pdf_path), "source": "none"}
    return status
