from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.services.official_annex_pdf import (
    _convert_document_to_pdf,
    _convert_many_with_libreoffice,
    _convert_with_word,
)


def _safe(value: Any) -> str:
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _normalize_project_type(value: Any) -> str:
    lowered = str(value or "").strip().lower()
    if "actividad" in lowered:
        return "Plan de Actividades Específicas y de Proyección Social"
    if "volunt" in lowered:
        return "Plan de Actividades de Voluntariado de RSU"
    return "Proyecto de Responsabilidad Social Universitaria"


def _clear_paragraph(paragraph) -> None:
    for child in list(paragraph._p):
        paragraph._p.remove(child)


def _set_paragraph(paragraph, text: str, *, bold: bool = False, center: bool | None = None, size: float = 11) -> None:
    _clear_paragraph(paragraph)
    if center is not None:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER if center else WD_ALIGN_PARAGRAPH.JUSTIFY
    run = paragraph.add_run(str(text or ""))
    run.bold = bold
    run.font.name = "Arial"
    run.font.size = Pt(size)


def _official_template() -> Path:
    return Path(__file__).resolve().parents[2] / "official_annex_templates" / "Anexo_14_Modelo_Certificado.docx"


def _prepare_official_certificate_docx(docx_path: Path, data: dict[str, Any]) -> bool:
    template = _official_template()
    if not template.exists():
        return False

    document = Document(template)
    paragraphs = document.paragraphs
    if len(paragraphs) < 10:
        return False

    recipient = str(data.get("nombre_completo") or "")
    identification = str(data.get("identificacion") or "")
    recipient_text = recipient if not identification else f"{recipient}\n{identification}"
    _set_paragraph(paragraphs[4], recipient_text, bold=True, center=True, size=13)

    project_type = _normalize_project_type(data.get("tipo_proyecto"))
    body = (
        f"por su participación en el {project_type}, ejecutando actividades de Responsabilidad Social tituladas "
        f"“{str(data.get('titulo') or '')}”, enmarcadas en la línea de acción de RSU "
        f"“{str(data.get('linea_accion') or '')}”, acreditando el cumplimiento de un total de "
        f"{str(data.get('horas') or '')} horas como ACTIVIDADES DE RESPONSABILIDAD SOCIAL del "
        f"PROGRAMA ACADÉMICO DE {str(data.get('programa_academico') or 'UNAB')}."
    )
    _set_paragraph(paragraphs[5], body, center=False, size=11)
    _set_paragraph(paragraphs[7], f"Fecha: {str(data.get('fecha_emision') or '')}", center=True, size=10)

    signature_paragraph = paragraphs[9]
    _clear_paragraph(signature_paragraph)
    signature_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    signature_path = Path(str(data.get("firma_director_path") or ""))
    if signature_path.exists() and signature_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        signature_paragraph.add_run().add_picture(str(signature_path), width=Cm(4.0))
        signature_paragraph.add_run("\n")
    line = signature_paragraph.add_run("____________________________________________\n")
    line.font.name = "Arial"
    line.font.size = Pt(10)
    name = str(data.get("nombre_director") or "").strip()
    if name:
        name_run = signature_paragraph.add_run(f"{name.upper()}\n")
        name_run.bold = True
        name_run.font.name = "Arial"
        name_run.font.size = Pt(10)
    role_run = signature_paragraph.add_run("Director(a) de la Dirección de Responsabilidad Social")
    role_run.font.name = "Arial"
    role_run.font.size = Pt(10)

    docx_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(docx_path)
    return docx_path.exists() and docx_path.stat().st_size > 0


def _generate_official_certificate_pdf(output: Path, data: dict[str, Any]) -> bool:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rsu_certificado_") as temp_name:
        temp_dir = Path(temp_name)
        docx_path = temp_dir / "Anexo_14_Certificado_rellenado.docx"
        if not _prepare_official_certificate_docx(docx_path, data):
            return False
        converted, _detail = _convert_document_to_pdf(docx_path, output)
        return bool(converted and output.exists() and output.stat().st_size > 0)


def _batch_convert_with_word(docx_paths: list[Path], output_dir: Path) -> bool:
    """Convierte el lote con una sola instancia de Word en Windows."""
    if os.name != "nt" or not docx_paths:
        return False
    output_dir.mkdir(parents=True, exist_ok=True)
    word = None
    document = None
    pythoncom = None
    com_initialized = False
    converted = 0
    try:
        import pythoncom as _pythoncom  # type: ignore
        import win32com.client  # type: ignore

        pythoncom = _pythoncom
        pythoncom.CoInitialize()
        com_initialized = True
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        try:
            word.AutomationSecurity = 3
        except Exception:
            pass
        for source in docx_paths:
            destination = output_dir / f"{source.stem}.pdf"
            destination.unlink(missing_ok=True)
            document = word.Documents.Open(
                str(source.resolve()),
                ConfirmConversions=False,
                ReadOnly=True,
                AddToRecentFiles=False,
                OpenAndRepair=True,
                NoEncodingDialog=True,
            )
            document.ExportAsFixedFormat(str(destination.resolve()), 17)
            document.Close(False)
            document = None
            if destination.exists() and destination.stat().st_size > 0:
                converted += 1
        word.Quit()
        word = None
    except Exception:
        return False
    finally:
        try:
            if document is not None:
                document.Close(False)
        except Exception:
            pass
        try:
            if word is not None:
                word.Quit()
        except Exception:
            pass
        if com_initialized and pythoncom is not None:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
    return converted == len(docx_paths)


def _batch_convert_with_libreoffice(
    docx_paths: list[Path],
    output_dir: Path,
) -> bool:
    """Convierte el lote con el runner aislado y sin ventanas."""
    ok, _detail = _convert_many_with_libreoffice(
        docx_paths,
        output_dir,
        timeout=max(120, 25 * len(docx_paths)),
    )
    return ok


def generate_certificate_pdfs_batch(
    items: list[tuple[Path, dict[str, Any]]],
) -> list[Path]:
    """Genera certificados oficiales por lote y usa fallback por archivo.

    Arrancar LibreOffice una vez por participante era la principal causa de
    espera y de timeouts. Esta función prepara todos los DOCX y los convierte
    en una sola ejecución. Los documentos que no se conviertan se completan
    individualmente con Word o con el generador PDF alternativo.
    """
    if not items:
        return []

    generated: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="rsu_certificados_lote_") as temp_name:
        temp_dir = Path(temp_name)
        prepared: list[tuple[Path, Path, dict[str, Any]]] = []
        for index, (output, data) in enumerate(items, start=1):
            output.parent.mkdir(parents=True, exist_ok=True)
            docx_path = temp_dir / f"certificado_{index:04d}.docx"
            if _prepare_official_certificate_docx(docx_path, data):
                prepared.append((docx_path, output, data))
            else:
                _generate_fallback_certificate_pdf(output, data)
                generated.append(output)

        prepared_paths = [docx_path for docx_path, _output, _data in prepared]
        # En Windows Word se intenta primero. De esta forma una instalación de
        # LibreOffice con el perfil de usuario dañado no bloquea la emisión ni
        # muestra la ventana fatal observada por el usuario.
        if not _batch_convert_with_word(prepared_paths, temp_dir):
            missing = [item for item in prepared_paths if not (temp_dir / f"{item.stem}.pdf").exists()]
            if missing:
                _batch_convert_with_libreoffice(missing, temp_dir)

        for docx_path, output, data in prepared:
            produced = temp_dir / f"{docx_path.stem}.pdf"
            if produced.exists() and produced.stat().st_size > 0:
                shutil.move(str(produced), str(output))
            else:
                converted, _detail = _convert_with_word(docx_path, output)
                if not converted:
                    _generate_fallback_certificate_pdf(output, data)
            if output.exists() and output.stat().st_size > 0:
                generated.append(output)

    return generated



def _signature_block(path: str | Path | None, center_style: ParagraphStyle) -> Table:
    flowables: list[Any] = []
    if path:
        signature_path = Path(path)
        if signature_path.exists() and signature_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            image = Image(str(signature_path))
            image._restrictSize(5.0 * cm, 1.9 * cm)
            flowables.extend([image, Spacer(1, 2)])
    flowables.append(
        Paragraph(
            "____________________________________________<br/>"
            "<b>Director(a) de la Dirección de Responsabilidad Social</b>",
            center_style,
        )
    )
    table = Table([[flowables]], colWidths=[13 * cm])
    table.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
            ]
        )
    )
    return table


def _generate_fallback_certificate_pdf(output: Path, data: dict[str, Any]) -> Path:
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "cert-title",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=28,
        leading=34,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#0B3A75"),
    )
    body = ParagraphStyle(
        "cert-body",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=13,
        leading=21,
        alignment=TA_JUSTIFY,
        textColor=colors.HexColor("#152444"),
    )
    center = ParagraphStyle("cert-center", parent=body, alignment=TA_CENTER)
    recipient = ParagraphStyle(
        "recipient",
        parent=center,
        fontName="Helvetica-Bold",
        fontSize=19,
        leading=25,
        textColor=colors.HexColor("#146C94"),
    )
    doc = SimpleDocTemplate(
        str(output),
        pagesize=landscape(A4),
        rightMargin=2.1 * cm,
        leftMargin=2.1 * cm,
        topMargin=1.6 * cm,
        bottomMargin=1.6 * cm,
    )
    project_type = _normalize_project_type(data.get("tipo_proyecto"))
    story = [
        Paragraph("UNIVERSIDAD NACIONAL DE BARRANCA", center),
        Paragraph("DIRECCIÓN DE RESPONSABILIDAD SOCIAL UNIVERSITARIA", center),
        Spacer(1, 18),
        Paragraph("CERTIFICADO", title),
        Spacer(1, 18),
        Paragraph("La Dirección de Responsabilidad Social otorga el presente certificado a:", center),
        Spacer(1, 12),
        Paragraph(
            f"<b>{_safe(data.get('nombre_completo'))}</b><br/>{_safe(data.get('identificacion'))}",
            recipient,
        ),
        Spacer(1, 18),
        Paragraph(
            f"por su participación en el <b>{_safe(project_type)}</b> titulado "
            f"<b>{_safe(data.get('titulo'))}</b>, enmarcado en la línea de acción "
            f"<b>{_safe(data.get('linea_accion'))}</b>, acreditando el cumplimiento de un total de "
            f"<b>{_safe(data.get('horas'))} horas</b> como actividades de Responsabilidad Social del programa académico "
            f"<b>{_safe(data.get('programa_academico'))}</b>.",
            body,
        ),
        Spacer(1, 18),
        Paragraph("Se otorga la presente para los fines que el interesado estime pertinentes.", center),
        Spacer(1, 16),
        Paragraph(f"Barranca, {_safe(data.get('fecha_emision'))}", center),
        Spacer(1, 14),
        _signature_block(data.get("firma_director_path"), center),
    ]
    doc.build(story)
    return output


def generate_certificate_pdf(output: Path, data: dict[str, Any]) -> Path:
    """Rellena el Anexo 14 oficial y lo convierte a PDF.

    Si el conversor externo no está disponible, se conserva el generador PDF
    alternativo para que la emisión no quede bloqueada.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    if _generate_official_certificate_pdf(output, data):
        return output
    return _generate_fallback_certificate_pdf(output, data)
