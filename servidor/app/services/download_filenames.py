from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def safe_filename_component(value: Any, fallback: str, *, max_length: int = 90) -> str:
    """Return a Windows-safe, readable filename component.

    Spaces are represented with underscores so certificate downloads follow the
    institutional convention ``nombre_completo-nombre_proyecto.pdf``.
    """

    source = str(value or fallback).strip()
    normalized = unicodedata.normalize("NFKD", source)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", ascii_text)
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip(" ._-")
    cleaned = cleaned or fallback
    if cleaned.upper() in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned[:max_length].rstrip(" ._-") or fallback


def certificate_download_filename(person_name: Any, project_name: Any) -> str:
    person = safe_filename_component(person_name, "participante", max_length=80)
    project = safe_filename_component(project_name, "proyecto", max_length=100)
    return f"{person}-{project}.pdf"


def ensure_pdf_filename(filename: str) -> str:
    path = Path(filename)
    return filename if path.suffix.lower() == ".pdf" else f"{filename}.pdf"
