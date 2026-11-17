from __future__ import annotations

from datetime import date
from pathlib import Path
import re
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib import colors

from app.services.official_annex_pdf import _convert_document_to_pdf


MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def _clear_paragraph(paragraph) -> None:
    for child in list(paragraph._p):
        paragraph._p.remove(child)


def _set_paragraph(paragraph, text: str, *, bold: bool | None = None, center: bool | None = None) -> None:
    _clear_paragraph(paragraph)
    if center is not None:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER if center else WD_ALIGN_PARAGRAPH.JUSTIFY
    run = paragraph.add_run(text)
    if bold is not None:
        run.bold = bold
    run.font.name = "Arial"
    run.font.size = Pt(10)


def _set_cell(cell, text: str, *, bold_first_line: bool = False) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    lines = str(text or "").splitlines() or [""]
    for index, line in enumerate(lines):
        run = paragraph.add_run(line)
        run.font.name = "Arial"
        run.font.size = Pt(10)
        if bold_first_line and index == 0:
            run.bold = True
        if index < len(lines) - 1:
            run.add_break()


def _format_date(value: date) -> str:
    return f"Barranca {value.day:02d} de {MONTHS[value.month - 1]} del {value.year}."


def _normalize_project_type(value: Any) -> str:
    raw = str(value or "Proyecto de Responsabilidad Social Universitaria").strip()
    lowered = raw.lower()
    if "actividad" in lowered:
        return "Actividad Específica y de Proyección Social"
    if "volunt" in lowered:
        return "Plan de Actividades de Voluntariado de RSU"
    return "Proyecto de Responsabilidad Social Universitaria"


def _fallback_memorandum_pdf(
    output: Path,
    *,
    number: str,
    issue_date: date,
    recipient_name: str,
    recipient_title: str,
    sender_name: str,
    sender_title: str,
    subject: str,
    body: str,
    follow_up: str,
    signature_path: str | None,
    cc_lines: list[str],
) -> Path:
    """Genera un PDF de contingencia cuando Word/LibreOffice no está disponible."""
    output.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("memo-title", parent=styles["Heading2"], alignment=TA_CENTER, fontName="Helvetica-Bold", fontSize=12, leading=15)
    body_style = ParagraphStyle("memo-body", parent=styles["BodyText"], alignment=TA_JUSTIFY, fontName="Helvetica", fontSize=10, leading=15)
    center = ParagraphStyle("memo-center", parent=body_style, alignment=TA_CENTER)
    doc = SimpleDocTemplate(str(output), pagesize=A4, leftMargin=2.2*cm, rightMargin=2.2*cm, topMargin=1.7*cm, bottomMargin=1.7*cm)
    info = [
        [Paragraph("<b>A</b>", body_style), Paragraph(recipient_name + "<br/>" + recipient_title, body_style)],
        [Paragraph("<b>DE</b>", body_style), Paragraph(sender_name + "<br/>" + sender_title, body_style)],
        [Paragraph("<b>ASUNTO</b>", body_style), Paragraph(subject, body_style)],
        [Paragraph("<b>FECHA</b>", body_style), Paragraph(_format_date(issue_date), body_style)],
    ]
    table = Table(info, colWidths=[2.2*cm, 13.2*cm])
    table.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP"), ("LINEBELOW", (0,-1), (-1,-1), 0.5, colors.HexColor("#777777")), ("LEFTPADDING", (0,0), (-1,-1), 4), ("RIGHTPADDING", (0,0), (-1,-1), 4), ("TOPPADDING", (0,0), (-1,-1), 4), ("BOTTOMPADDING", (0,0), (-1,-1), 4)]))
    story = [
        Paragraph("UNIVERSIDAD NACIONAL DE BARRANCA", title_style),
        Paragraph("DIRECCIÓN DE RESPONSABILIDAD SOCIAL", center),
        Spacer(1, 8), Paragraph(number, title_style), Spacer(1, 10), table, Spacer(1, 16),
        Paragraph(body, body_style), Spacer(1, 10), Paragraph(follow_up, body_style), Spacer(1, 22),
    ]
    sig = Path(str(signature_path or ""))
    if sig.exists() and sig.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        image = Image(str(sig)); image._restrictSize(4.2*cm, 1.8*cm); story.extend([image, Spacer(1, 2)])
    story.extend([Paragraph("____________________________________________", center), Paragraph(f"<b>{sender_name.upper()}</b><br/>{sender_title}", center), Spacer(1, 14)])
    if cc_lines:
        story.append(Paragraph("<b>C.c.</b><br/>" + "<br/>".join(cc_lines), body_style))
    doc.build(story)
    return output


def generate_memorandum(
    *,
    template_path: Path,
    output_dir: Path,
    number: str,
    issue_date: date,
    project: dict[str, Any],
    responsible: dict[str, Any],
    vice_president: dict[str, Any] | None,
    director: dict[str, Any],
    director_signature_path: str | None,
) -> tuple[Path, Path | None]:
    """Rellena una copia del memorándum oficial sin rediseñar su estructura."""
    output_dir.mkdir(parents=True, exist_ok=True)
    document = Document(template_path)
    project_type = _normalize_project_type(project.get("tipo_proyecto"))
    title = str(project.get("titulo") or "")
    faculty = str(responsible.get("facultad") or responsible.get("escuela") or "la facultad correspondiente")
    period = str(project.get("periodo_academico") or "")
    responsible_name = str(responsible.get("nombre_completo") or "Responsable del proyecto")
    director_name = str(director.get("nombre_completo") or "Director(a) de la Dirección de Responsabilidad Social")
    director_title = str(director.get("cargo_oficial") or director.get("cargo") or "Directora de la Dirección de Responsabilidad Social")

    paragraphs = document.paragraphs
    if len(paragraphs) >= 9:
        _set_paragraph(paragraphs[3], number, bold=True, center=True)
        # Se conserva el texto del memorándum original y se reemplazan
        # exclusivamente las dos líneas punteadas por el tipo y el título.
        original_body = paragraphs[4].text
        body = re.sub(r"[.…]{5,}", project_type, original_body, count=1)
        body = re.sub(r"[.…]{5,}", title, body, count=1)
        if project_type not in body:
            body = body.replace("luego de la revisión de", f"luego de la revisión de {project_type}", 1)
        if title not in body:
            body = body.replace("denominado,", f"denominado, “{title}”", 1)
        _set_paragraph(paragraphs[4], body, center=False)
        follow_up = (
            f"Asimismo, hacer de su conocimiento que el {project_type.lower()} estará sujeto al monitoreo y seguimiento "
            f"del responsable de la Oficina de Responsabilidad Social, Proyección Social y Extensión Universitaria de {faculty}."
        )
        _set_paragraph(paragraphs[5], follow_up, center=False)
        signature_paragraph = paragraphs[7]
        _clear_paragraph(signature_paragraph)
        signature_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        signature_path = Path(str(director_signature_path or ""))
        if signature_path.exists() and signature_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            signature_paragraph.add_run().add_picture(str(signature_path), width=Cm(3.5))
            signature_paragraph.add_run("\n")
        name_run = signature_paragraph.add_run(director_name.upper())
        name_run.bold = True
        name_run.font.name = "Arial"
        name_run.font.size = Pt(10)
        _set_paragraph(paragraphs[8], f"{director_title}\nUNIVERSIDAD NACIONAL DE BARRANCA", center=True)
        if len(paragraphs) > 10:
            cc = (
                "C.c.\n"
                f"Vicepresidencia Académica: {str((vice_president or {}).get('nombre_completo') or 'Autoridad registrada')}\n"
                f"Responsable de la Oficina de Responsabilidad Social, Proyección Social y Extensión Universitaria de {faculty}.\n"
                "Archivo"
            )
            _set_paragraph(paragraphs[10], cc)

    if document.tables:
        table = document.tables[0]
        _set_cell(table.cell(0, 2), f"{responsible_name}\nResponsable de {project_type}", bold_first_line=True)
        _set_cell(table.cell(1, 2), f"{director_name}\n{director_title}", bold_first_line=True)
        _set_cell(table.cell(2, 2), f"REMITO VALIDACIÓN Y APROBACIÓN DE {project_type.upper()} - {period}", bold_first_line=True)
        _set_cell(table.cell(3, 2), _format_date(issue_date), bold_first_line=True)

    safe_number = number.replace("/", "-").replace(" ", "_")
    docx_path = output_dir / f"{safe_number}.docx"
    pdf_path = output_dir / f"{safe_number}.pdf"
    document.save(docx_path)

    converted, _detail = _convert_document_to_pdf(docx_path, pdf_path)
    if not converted or not pdf_path.exists() or pdf_path.stat().st_size == 0:
        body_text = (
            f"Es grato dirigirme a usted para comunicar que, luego de la revisión del {project_type.lower()} "
            f"denominado «{title}», este se encuentra aprobado y puede iniciar su ejecución."
        )
        follow_text = (
            f"El {project_type.lower()} estará sujeto al monitoreo y seguimiento institucional de {faculty}."
        )
        _fallback_memorandum_pdf(
            pdf_path, number=number, issue_date=issue_date, recipient_name=responsible_name,
            recipient_title=f"Responsable de {project_type}", sender_name=director_name, sender_title=director_title,
            subject=f"REMITO VALIDACIÓN Y APROBACIÓN DE {project_type.upper()} - {period}",
            body=body_text, follow_up=follow_text, signature_path=director_signature_path,
            cc_lines=[f"Vicepresidencia Académica: {str((vice_president or {}).get('nombre_completo') or 'Autoridad registrada')}", "Archivo"],
        )
    return docx_path, pdf_path if pdf_path.exists() and pdf_path.stat().st_size > 0 else None


def generate_certificate_memorandum(
    *,
    template_path: Path,
    output_dir: Path,
    number: str,
    issue_date: date,
    project: dict[str, Any],
    responsible: dict[str, Any],
    vice_president: dict[str, Any],
    director: dict[str, Any],
    director_signature_path: str | None,
    certificate_count: int = 0,
    purpose: str = "certificate_emission",
) -> tuple[Path, Path | None]:
    """Genera el segundo memorándum institucional del proyecto.

    Se utiliza la misma plantilla institucional, modificando únicamente los
    campos de destinatario, asunto, cuerpo, copias y firma. La plantilla base no
    se rediseña ni se alteran sus tablas.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    document = Document(template_path)
    title = str(project.get("titulo") or "")
    project_type = _normalize_project_type(project.get("tipo_proyecto"))
    period = str(project.get("periodo_academico") or "")
    responsible_name = str(responsible.get("nombre_completo") or "Responsable del proyecto")
    vice_name = str(vice_president.get("nombre_completo") or "Vicepresidente académico")
    vice_title = str(vice_president.get("cargo_oficial") or "Vicepresidente Académico")
    director_name = str(director.get("nombre_completo") or "Director(a) de la Dirección de Responsabilidad Social")
    director_title = str(director.get("cargo_oficial") or director.get("cargo") or "Directora de la Dirección de Responsabilidad Social")
    amount = max(0, int(certificate_count or 0))
    final_approval = str(purpose or "").strip().lower() == "final_approval"
    if final_approval:
        subject = f"REMITO APROBACIÓN DEL INFORME FINAL DE {project_type.upper()} - {period}"
        body = (
            "Es grato dirigirme a usted, a fin de expresarle mi cordial saludo y, a la vez, por medio del presente, "
            f"hacer de conocimiento que el informe final del {project_type.lower()} denominado “{title}” fue "
            "evaluado mediante el Anexo 7 y aprobado por los revisores correspondientes."
        )
        follow_up = (
            "En consecuencia, el expediente pasa a la etapa de certificación. Se remite el presente memorándum al "
            "responsable del proyecto y a la Vicepresidencia Académica para conocimiento y fines institucionales."
        )
    else:
        amount = max(1, amount)
        certificate_word = "certificado" if amount == 1 else "certificados"
        subject = f"REMITO EMISIÓN DE CERTIFICADOS DE {project_type.upper()} - {period}"
        body = (
            "Es grato dirigirme a usted, a fin de expresarle mi cordial saludo y, a la vez, por medio del presente, "
            f"hacer de conocimiento que, culminada la evaluación y el cierre del {project_type.lower()} denominado "
            f"“{title}”, se ha dispuesto la emisión de {amount} {certificate_word} para el responsable y los "
            "participantes activos del proyecto, con sus respectivos datos y horas acreditadas."
        )
        follow_up = (
            "Los certificados individuales han sido incorporados al expediente digital y puestos a disposición de "
            "cada participante mediante el Sistema RSU. Se remite el presente memorándum para conocimiento de la "
            "Vicepresidencia Académica y fines institucionales correspondientes."
        )

    paragraphs = document.paragraphs
    if len(paragraphs) >= 9:
        _set_paragraph(paragraphs[3], number, bold=True, center=True)
        _set_paragraph(paragraphs[4], body, center=False)
        _set_paragraph(paragraphs[5], follow_up, center=False)
        signature_paragraph = paragraphs[7]
        _clear_paragraph(signature_paragraph)
        signature_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        signature_path = Path(str(director_signature_path or ""))
        if signature_path.exists() and signature_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            signature_paragraph.add_run().add_picture(str(signature_path), width=Cm(3.5))
            signature_paragraph.add_run("\n")
        name_run = signature_paragraph.add_run(director_name.upper())
        name_run.bold = True
        name_run.font.name = "Arial"
        name_run.font.size = Pt(10)
        _set_paragraph(paragraphs[8], f"{director_title}\nUNIVERSIDAD NACIONAL DE BARRANCA", center=True)
        if len(paragraphs) > 10:
            cc = (
                "C.c.\n"
                f"Responsable del proyecto: {responsible_name}\n"
                "Archivo"
            )
            _set_paragraph(paragraphs[10], cc)

    if document.tables:
        table = document.tables[0]
        _set_cell(table.cell(0, 2), f"{vice_name}\n{vice_title}", bold_first_line=True)
        _set_cell(table.cell(1, 2), f"{director_name}\n{director_title}", bold_first_line=True)
        _set_cell(
            table.cell(2, 2),
            subject,
            bold_first_line=True,
        )
        _set_cell(table.cell(3, 2), _format_date(issue_date), bold_first_line=True)

    safe_number = number.replace("/", "-").replace(" ", "_")
    docx_path = output_dir / f"{safe_number}.docx"
    pdf_path = output_dir / f"{safe_number}.pdf"
    document.save(docx_path)
    converted, _detail = _convert_document_to_pdf(docx_path, pdf_path)
    if not converted or not pdf_path.exists() or pdf_path.stat().st_size == 0:
        _fallback_memorandum_pdf(
            pdf_path, number=number, issue_date=issue_date, recipient_name=vice_name, recipient_title=vice_title,
            sender_name=director_name, sender_title=director_title, subject=subject, body=body, follow_up=follow_up,
            signature_path=director_signature_path, cc_lines=[f"Responsable del proyecto: {responsible_name}", "Archivo"],
        )
    return docx_path, pdf_path if pdf_path.exists() and pdf_path.stat().st_size > 0 else None
