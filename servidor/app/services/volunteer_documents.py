from __future__ import annotations

import hashlib
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


PAGE_WIDTH, PAGE_HEIGHT = A4


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "header": ParagraphStyle(
            "header", parent=base["Normal"], fontName="Helvetica", fontSize=8.5,
            leading=10, alignment=TA_CENTER, spaceAfter=6,
        ),
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontName="Helvetica-Bold", fontSize=13,
            leading=16, alignment=TA_CENTER, spaceAfter=8,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=11,
            leading=13, alignment=TA_LEFT, spaceBefore=6, spaceAfter=5,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontName="Helvetica", fontSize=9.2,
            leading=12, alignment=TA_JUSTIFY,
        ),
        "small": ParagraphStyle(
            "small", parent=base["Normal"], fontName="Helvetica", fontSize=8,
            leading=9.5, alignment=TA_LEFT,
        ),
        "center": ParagraphStyle(
            "center", parent=base["Normal"], fontName="Helvetica", fontSize=9,
            leading=11, alignment=TA_CENTER,
        ),
    }


def _doc(path: Path) -> SimpleDocTemplate:
    path.parent.mkdir(parents=True, exist_ok=True)
    return SimpleDocTemplate(
        str(path), pagesize=A4, rightMargin=1.45 * cm, leftMargin=1.45 * cm,
        topMargin=1.15 * cm, bottomMargin=1.2 * cm,
        title=path.stem,
    )


def _header(st: dict[str, ParagraphStyle], annex: str, title: str) -> list[Any]:
    return [
        Paragraph("REGLAMENTO DE ACCIONES DE RESPONSABILIDAD SOCIAL UNIVERSITARIA", st["header"]),
        Spacer(1, 4),
        Paragraph(annex, st["title"]),
        Paragraph(title, st["title"]),
        Spacer(1, 5),
    ]


def _cell(value: Any, style: ParagraphStyle) -> Paragraph:
    text = "" if value is None else str(value)
    return Paragraph(text.replace("\n", "<br/>"), style)


def _grid(rows: list[list[Any]], widths: list[float], *, header_rows: int = 0) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=header_rows)
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.55, colors.HexColor("#4B5563")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header_rows:
        commands.extend([
            ("BACKGROUND", (0, 0), (-1, header_rows - 1), colors.HexColor("#E5E7EB")),
            ("FONTNAME", (0, 0), (-1, header_rows - 1), "Helvetica-Bold"),
            ("ALIGN", (0, 0), (-1, header_rows - 1), "CENTER"),
        ])
    table.setStyle(TableStyle(commands))
    return table


def _image(path: str | Path | None, max_width: float, max_height: float) -> Any:
    if not path:
        return ""
    source = Path(str(path))
    if not source.exists() or source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        return ""
    image = Image(str(source))
    image._restrictSize(max_width, max_height)
    return image


def _signature_table(
    st: dict[str, ParagraphStyle],
    signature_path: str | Path | None,
    name: str,
    role: str,
) -> Table:
    signature = _image(signature_path, 5.2 * cm, 1.5 * cm)
    content: list[Any] = []
    if signature:
        content.extend([signature, Spacer(1, 1)])
    content.append(Paragraph("________________________________", st["center"]))
    content.append(Paragraph(f"<b>{name}</b><br/>{role}", st["center"]))
    table = Table([[content]], colWidths=[8.5 * cm])
    table.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "BOTTOM")]))
    return table


def generate_annex1_pdf(
    output: Path,
    *,
    responsible: dict[str, Any],
    project: dict[str, Any],
    content: dict[str, Any],
    signature_path: str | Path | None = None,
) -> Path:
    st = _styles()
    story = _header(st, "ANEXO 1", "MODELO DE SOLICITUD PARA PARTICIPAR EN LAS ACTIVIDADES DE RSU EN LA UNAB")
    story.extend([
        Paragraph("<b>SOLICITO:</b> Aprobación del plan de actividades de voluntariado de RSU.", st["body"]),
        Spacer(1, 8),
        Paragraph("<b>SEÑOR(A):<br/>DIRECTOR(A) DE RESPONSABILIDAD SOCIAL</b>", st["body"]),
        Spacer(1, 10),
        Paragraph(
            "Yo, <b>{name}</b>, identificado(a) con DNI N.° <b>{dni}</b>, domiciliado(a) en "
            "<b>{address}</b>, en mi condición de <b>{condition}</b>, ante usted con el debido respeto me presento y expongo:".format(
                name=responsible.get("nombre_completo") or "",
                dni=responsible.get("dni") or "",
                address=content.get("domicilio") or responsible.get("domicilio") or "",
                condition=content.get("condicion") or responsible.get("condicion") or "miembro de la comunidad universitaria",
            ),
            st["body"],
        ),
        Spacer(1, 8),
        Paragraph(
            "Que, teniendo el interés de participar en las actividades de Responsabilidad Social Universitaria, presento el "
            "plan de actividades de voluntariado denominado <b>«{title}»</b>, en el marco de la Línea de Acción RSU UNAB: "
            "<b>{line}</b>, para su aprobación según corresponda.".format(
                title=project.get("titulo") or "",
                line=project.get("linea_accion") or "",
            ),
            st["body"],
        ),
        Spacer(1, 8),
        Paragraph("Por lo expuesto, agradezco su atención a la presente.", st["body"]),
        Spacer(1, 28),
        _signature_table(st, signature_path, responsible.get("nombre_completo") or "Responsable", "Responsable del voluntariado"),
    ])
    _doc(output).build(story)
    return output


def generate_annex11_pdf(
    output: Path,
    *,
    responsible: dict[str, Any],
    project: dict[str, Any],
    content: dict[str, Any],
    members: list[dict[str, Any]],
    signature_path: str | Path | None = None,
) -> Path:
    st = _styles()
    story = _header(st, "ANEXO N.° 11", "PLAN DE ACTIVIDADES DE VOLUNTARIADO")
    story.extend([
        Paragraph("1. NOMBRE DEL VOLUNTARIADO", st["subtitle"]),
        _grid([[_cell(project.get("titulo"), st["body"])]], [18.1 * cm]),
        Paragraph("2. LÍNEA DE ACCIÓN DEL RSU", st["subtitle"]),
        _grid([[_cell(project.get("linea_accion"), st["body"])]], [18.1 * cm]),
        Paragraph("3. PLAZO DE EJECUCIÓN", st["subtitle"]),
        _grid([
            [_cell("Fecha de inicio", st["small"]), _cell(project.get("fecha_inicio"), st["body"])],
            [_cell("Fecha de término", st["small"]), _cell(project.get("fecha_fin"), st["body"])],
        ], [5.2 * cm, 12.9 * cm]),
        Paragraph("4. NOMBRE DEL RESPONSABLE DEL VOLUNTARIADO", st["subtitle"]),
        _grid([["Nombres y apellidos", "DNI N.°", "Teléfono", "E-mail"], [
            _cell(responsible.get("nombre_completo"), st["small"]),
            _cell(responsible.get("dni"), st["small"]),
            _cell(responsible.get("telefono"), st["small"]),
            _cell(responsible.get("email"), st["small"]),
        ]], [6.3 * cm, 2.5 * cm, 3.2 * cm, 6.1 * cm], header_rows=1),
        Paragraph("5. MIEMBROS DEL VOLUNTARIADO", st["subtitle"]),
    ])
    member_rows: list[list[Any]] = [["Nombres y apellidos", "Código de estudiante", "Teléfono", "E-mail"]]
    for member in members or [{}]:
        member_rows.append([
            _cell(member.get("nombre_completo"), st["small"]),
            _cell(member.get("codigo_estudiante") or member.get("dni"), st["small"]),
            _cell(member.get("telefono"), st["small"]),
            _cell(member.get("email"), st["small"]),
        ])
    story.extend([
        _grid(member_rows, [6.3 * cm, 3.2 * cm, 3.0 * cm, 5.6 * cm], header_rows=1),
        Paragraph("6. BENEFICIARIOS DE LAS ACTIVIDADES", st["subtitle"]),
        _grid([
            [_cell("Institución/organización/comunidad", st["small"]), _cell(content.get("beneficiario_institucion"), st["body"])],
            [_cell("Facultad UNAB", st["small"]), _cell(content.get("beneficiario_facultad") or "No aplica", st["body"])],
            [_cell("Escuela profesional UNAB", st["small"]), _cell(content.get("beneficiario_escuela") or "No aplica", st["body"])],
        ], [7.2 * cm, 10.9 * cm]),
        Paragraph("7. PROBLEMAS Y/O NECESIDADES", st["subtitle"]),
        _grid([
            [_cell("Problemas", st["small"]), _cell(content.get("problemas"), st["body"])],
            [_cell("Necesidades", st["small"]), _cell(content.get("necesidades"), st["body"])],
        ], [4.2 * cm, 13.9 * cm]),
        Paragraph("8. OBJETIVO DEL VOLUNTARIADO", st["subtitle"]),
        _grid([[_cell(content.get("objetivo"), st["body"])]], [18.1 * cm]),
        Paragraph("9. ACTIVIDADES DE VOLUNTARIADO", st["subtitle"]),
        _grid([[_cell(content.get("actividades"), st["body"])]], [18.1 * cm]),
        Spacer(1, 18),
        _signature_table(st, signature_path, responsible.get("nombre_completo") or "Responsable", "Responsable del voluntariado"),
    ])
    _doc(output).build(story)
    return output


def generate_annex12_pdf(
    output: Path,
    *,
    volunteer: dict[str, Any],
    tutor: dict[str, Any] | None,
    insurance: dict[str, Any] | None,
    disability: dict[str, Any] | None,
    photo_path: str | Path | None,
    volunteer_signature_path: str | Path | None,
    fingerprint_path: str | Path | None,
    responsible: dict[str, Any],
    responsible_signature_path: str | Path | None,
    submitted_at: date | datetime | None = None,
) -> Path:
    st = _styles()
    story = _header(st, "ANEXO 12", "FICHA DE REGISTRO DE VOLUNTARIO")
    photo = _image(photo_path, 3.2 * cm, 4.0 * cm)
    if photo:
        photo_table = Table([["", photo]], colWidths=[14.6 * cm, 3.5 * cm])
        photo_table.setStyle(TableStyle([("ALIGN", (1, 0), (1, 0), "RIGHT"), ("BOX", (1, 0), (1, 0), 0.6, colors.black)]))
        story.append(photo_table)
    story.append(Paragraph("1. DATOS DEL PADRE, MADRE Y/O TUTOR", st["subtitle"]))
    tutor = tutor or {}
    tutor_rows = []
    for label, key in [
        ("DNI N.°", "dni"), ("PASAPORTE", "pasaporte"), ("CARNÉ DE EXTRANJERÍA", "carne_extranjeria"),
        ("APELLIDOS", "apellidos"), ("NOMBRES", "nombres"), ("DIRECCIÓN", "direccion"),
        ("TELÉFONO CELULAR", "telefono"), ("CORREO ELECTRÓNICO", "email"), ("DISTRITO", "distrito"),
        ("PROVINCIA", "provincia"), ("DEPARTAMENTO", "departamento"),
    ]:
        tutor_rows.append([_cell(label, st["small"]), _cell(tutor.get(key) or "No aplica", st["small"])])
    story.extend([_grid(tutor_rows, [5.4 * cm, 12.7 * cm]), Paragraph("2. DATOS PERSONALES DEL VOLUNTARIO", st["subtitle"])])
    volunteer_rows = []
    for label, key in [
        ("DNI N.°", "dni"), ("PASAPORTE", "pasaporte"), ("CARNÉ DE EXTRANJERÍA", "carne_extranjeria"),
        ("APELLIDOS", "apellidos"), ("NOMBRES", "nombres"), ("DIRECCIÓN", "direccion"),
        ("DISTRITO", "distrito"), ("PROVINCIA", "provincia"), ("DEPARTAMENTO", "departamento"),
        ("TELÉFONO CELULAR", "telefono"), ("CORREO ELECTRÓNICO", "email"),
    ]:
        volunteer_rows.append([_cell(label, st["small"]), _cell(volunteer.get(key), st["small"])])
    story.append(_grid(volunteer_rows, [5.4 * cm, 12.7 * cm]))
    insurance = insurance or {}
    disability = disability or {}
    story.extend([
        _grid([
            [_cell("¿Cuenta con seguro médico?", st["small"]), _cell("SÍ" if insurance.get("tiene") else "NO", st["center"])],
            [_cell("Tipo de seguro", st["small"]), _cell(insurance.get("tipo") or "No aplica", st["small"])],
            [_cell("¿Cuenta con algún tipo de discapacidad?", st["small"]), _cell("SÍ" if disability.get("tiene") else "NO", st["center"])],
            [_cell("Tipo de discapacidad", st["small"]), _cell(", ".join(disability.get("tipos") or []) or disability.get("detalle") or "No aplica", st["small"])],
        ], [12.6 * cm, 5.5 * cm]),
        Spacer(1, 10),
    ])
    date_value = submitted_at.date() if isinstance(submitted_at, datetime) else submitted_at or date.today()
    signature = _image(volunteer_signature_path, 4.3 * cm, 1.5 * cm)
    fingerprint = _image(fingerprint_path, 3.0 * cm, 1.8 * cm)
    signature_content: list[Any] = []
    if signature:
        signature_content.append(signature)
    signature_content.append(Paragraph("Firma del solicitante y/o apoderado", st["center"]))
    fingerprint_content: list[Any] = []
    if fingerprint:
        fingerprint_content.append(fingerprint)
    fingerprint_content.append(Paragraph("Huella digital", st["center"]))
    sign_rows = [[
        signature_content,
        fingerprint_content,
        Paragraph(f"Fecha: {date_value.strftime('%d/%m/%Y')}", st["center"]),
    ]]
    sign_table = Table(sign_rows, colWidths=[7.3 * cm, 5.1 * cm, 5.7 * cm], rowHeights=[2.7 * cm])
    sign_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.55, colors.HexColor("#4B5563")),
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    story.extend([
        sign_table,
        Spacer(1, 20),
        _signature_table(st, responsible_signature_path, responsible.get("nombre_completo") or "Responsable", "Responsable del voluntariado"),
    ])
    _doc(output).build(story)
    return output


def merge_annex12_pdfs(output: Path, pdfs: Iterable[Path]) -> tuple[Path, int, str]:
    writer = PdfWriter()
    pages = 0
    for path in pdfs:
        reader = PdfReader(str(path))
        for page in reader.pages:
            writer.add_page(page)
            pages += 1
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        writer.write(stream)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return output, pages, digest


def generate_annex13_pdf(
    output: Path,
    *,
    responsible: dict[str, Any],
    project: dict[str, Any],
    content: dict[str, Any],
    members: list[dict[str, Any]],
    signature_path: str | Path | None = None,
    evidence_paths: list[tuple[str, str | Path]] | None = None,
) -> Path:
    st = _styles()
    story = _header(st, "ANEXO 13", "INFORME FINAL DE VOLUNTARIADO")
    story.extend([
        Paragraph("1. LÍNEA DE ACCIÓN DEL RSU", st["subtitle"]),
        _grid([[_cell(project.get("linea_accion"), st["body"])]], [18.1 * cm]),
        Paragraph("2. PLAZO DE EJECUCIÓN", st["subtitle"]),
        _grid([
            [_cell("Fecha de inicio", st["small"]), _cell(content.get("fecha_inicio") or project.get("fecha_inicio"), st["body"])],
            [_cell("Fecha de término", st["small"]), _cell(content.get("fecha_fin") or project.get("fecha_fin"), st["body"])],
        ], [5.2 * cm, 12.9 * cm]),
        Paragraph("3. NOMBRE DEL RESPONSABLE DEL VOLUNTARIADO", st["subtitle"]),
        _grid([["Nombres y apellidos", "DNI N.°", "Teléfono", "E-mail"], [
            _cell(responsible.get("nombre_completo"), st["small"]), _cell(responsible.get("dni"), st["small"]),
            _cell(responsible.get("telefono"), st["small"]), _cell(responsible.get("email"), st["small"]),
        ]], [6.3 * cm, 2.5 * cm, 3.2 * cm, 6.1 * cm], header_rows=1),
        Paragraph("4. MIEMBROS DEL VOLUNTARIADO", st["subtitle"]),
    ])
    member_rows: list[list[Any]] = [["Nombres y apellidos", "DNI N.°", "Teléfono", "E-mail"]]
    for member in members or [{}]:
        member_rows.append([
            _cell(member.get("nombre_completo"), st["small"]), _cell(member.get("dni"), st["small"]),
            _cell(member.get("telefono"), st["small"]), _cell(member.get("email"), st["small"]),
        ])
    story.extend([
        _grid(member_rows, [6.3 * cm, 2.5 * cm, 3.2 * cm, 6.1 * cm], header_rows=1),
        Paragraph("5. BENEFICIARIOS DE LAS ACTIVIDADES", st["subtitle"]),
        _grid([
            [_cell("Institución/organización/comunidad", st["small"]), _cell(content.get("beneficiario_institucion"), st["small"])],
            [_cell("Facultad UNAB", st["small"]), _cell(content.get("beneficiario_facultad") or "No aplica", st["small"])],
            [_cell("Escuela profesional UNAB", st["small"]), _cell(content.get("beneficiario_escuela") or "No aplica", st["small"])],
        ], [7.2 * cm, 10.9 * cm]),
        Paragraph("6. DESCRIPCIÓN DE LAS ACTIVIDADES REALIZADAS POR CADA SEMANA", st["subtitle"]),
    ])
    week_rows: list[list[Any]] = [["Semana", "Fecha y hora", "Actividad realizada"]]
    weeks = content.get("semanas") or []
    for index in range(1, 9):
        week = next((item for item in weeks if int(item.get("semana") or 0) == index), {})
        moment = " ".join(filter(None, [str(week.get("fecha") or ""), str(week.get("hora") or "")])).strip()
        week_rows.append([_cell(index, st["center"]), _cell(moment, st["small"]), _cell(week.get("descripcion"), st["small"])])
    story.extend([
        _grid(week_rows, [2.0 * cm, 4.2 * cm, 11.9 * cm], header_rows=1),
        Paragraph("7. EVIDENCIAS DE LAS ACTIVIDADES REALIZADAS", st["subtitle"]),
        Paragraph(content.get("evidencias_descripcion") or "Las evidencias se adjuntan al expediente digital.", st["body"]),
        Spacer(1, 18),
        _signature_table(st, signature_path, responsible.get("nombre_completo") or "Responsable", "Responsable del voluntariado"),
    ])
    evidence_paths = evidence_paths or []
    for label, path in evidence_paths:
        image = _image(path, 16.5 * cm, 20.5 * cm)
        if image:
            story.extend([PageBreak(), Paragraph(f"EVIDENCIA: {label}", st["subtitle"]), image])
    _doc(output).build(story)
    return output


def generate_volunteer_memorandum_pdf(
    output: Path,
    *,
    number: str,
    issue_date: date,
    project: dict[str, Any],
    responsible: dict[str, Any],
    director: dict[str, Any] | None,
    vice_president: dict[str, Any] | None,
    final: bool,
) -> Path:
    st = _styles()
    status_text = "aprobación final y habilitación para certificación" if final else "aprobación del plan e inicio de ejecución"
    body = (
        f"Me dirijo a usted para comunicar que el voluntariado «{project.get('titulo') or ''}» ha obtenido la {status_text}. "
        "La evaluación y los documentos que sustentan esta decisión se encuentran registrados en el expediente digital de RSU."
    )
    story = [
        Paragraph("UNIVERSIDAD NACIONAL DE BARRANCA", st["title"]),
        Paragraph("DIRECCIÓN DE RESPONSABILIDAD SOCIAL", st["title"]),
        Spacer(1, 16),
        Paragraph(f"<b>MEMORÁNDUM N.° {number}</b>", st["title"]),
        Spacer(1, 10),
        _grid([
            [_cell("PARA", st["small"]), _cell(f"{responsible.get('nombre_completo') or ''} — Responsable del voluntariado", st["body"])],
            [_cell("C.C.", st["small"]), _cell((vice_president or {}).get("nombre_completo") or "Vicepresidencia Académica", st["body"])],
            [_cell("ASUNTO", st["small"]), _cell(status_text.upper(), st["body"])],
            [_cell("FECHA", st["small"]), _cell(issue_date.strftime("%d/%m/%Y"), st["body"])],
        ], [3.0 * cm, 15.1 * cm]),
        Spacer(1, 18),
        Paragraph(body, st["body"]),
        Spacer(1, 28),
        _signature_table(st, (director or {}).get("firma_url"), (director or {}).get("nombre_completo") or "Dirección de Responsabilidad Social", (director or {}).get("cargo") or "Director(a) de Responsabilidad Social"),
    ]
    _doc(output).build(story)
    return output
