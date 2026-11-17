from __future__ import annotations

from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def _safe(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")


def generate_annexes_pdf(output_path: Path, data: dict[str, Any]) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "BodyRSU",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=13,
        alignment=TA_JUSTIFY,
        spaceAfter=5,
    )
    title = ParagraphStyle(
        "TitleRSU",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=17,
        alignment=TA_CENTER,
        spaceAfter=8,
    )
    section = ParagraphStyle(
        "SectionRSU",
        parent=body,
        fontName="Helvetica-Bold",
        fontSize=10,
        alignment=TA_CENTER,
    )

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=1.4 * cm,
        bottomMargin=1.6 * cm,
    )
    story = []

    story.append(Paragraph("UNIVERSIDAD NACIONAL DE BARRANCA", title))
    story.append(Paragraph("DIRECCIÓN DE RESPONSABILIDAD SOCIAL UNIVERSITARIA", section))
    story.append(Spacer(1, 8))
    story.append(Paragraph("ANEXO 1", title))
    story.append(Paragraph("MODELO DE SOLICITUD PARA PARTICIPAR EN LAS ACTIVIDADES DE RSU EN LA UNAB", section))
    story.append(Spacer(1, 10))
    story.append(Paragraph(f"<b>SOLICITO:</b> Aprobación de {_safe(data.get('solicitud_tipo'))}.", body))
    story.append(Spacer(1, 6))
    story.append(Paragraph("<b>SEÑOR:</b><br/><b>DIRECTOR DE RESPONSABILIDAD SOCIAL</b>", body))
    story.append(Spacer(1, 8))
    solicitud = (
        f"Yo, <b>{_safe(data.get('responsable_nombre'))}</b>, identificado con DNI N.° "
        f"<b>{_safe(data.get('responsable_dni'))}</b>, domiciliado en "
        f"<b>{_safe(data.get('domicilio_solicitante'))}</b>, en mi condición de "
        f"<b>{_safe(data.get('condicion_solicitante'))}</b>, ante usted con el debido respeto me presento y expongo:<br/><br/>"
        f"Que, teniendo interés en participar en las actividades de RSU durante el semestre académico "
        f"<b>{_safe(data.get('periodo_academico'))}</b>, presento el proyecto titulado "
        f"<b>{_safe(data.get('titulo'))}</b>, en el marco de la línea de acción "
        f"<b>{_safe(data.get('linea_accion'))}</b>, para su evaluación y aprobación según corresponda.<br/><br/>"
        "Por lo expuesto, agradezco su atención a la presente."
    )
    story.append(Paragraph(solicitud, body))
    story.append(Spacer(1, 32))
    story.append(Paragraph("____________________________________<br/>Firma<br/>" + _safe(data.get("responsable_nombre")), ParagraphStyle("sig", parent=body, alignment=TA_CENTER)))

    story.append(PageBreak())
    story.append(Paragraph("ANEXO N.° 2", title))
    story.append(Paragraph("FORMATO DE PROYECTOS DE RSU", section))
    story.append(Spacer(1, 8))

    participants = [_safe(data.get("responsable_nombre")) + " (Responsable)"]
    participants.extend(f"{_safe(p.get('nombre_completo'))} - DNI {_safe(p.get('dni'))} (Colaborador)" for p in data.get("colaboradores", []))
    top_data = [
        [Paragraph("<b>TÍTULO DEL PROYECTO</b>", section), Paragraph(_safe(data.get("titulo")), body)],
        [Paragraph("<b>LÍNEA DE ACCIÓN DE RSU</b>", section), Paragraph(_safe(data.get("linea_accion")), body)],
        [Paragraph("<b>RESPONSABLE Y PARTICIPANTES DEL PROYECTO</b>", section), Paragraph("<br/>".join(participants), body)],
    ]
    top = Table(top_data, colWidths=[6.2 * cm, 11.2 * cm])
    top.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.7, colors.black),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#D9D9D9")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(top)

    def add_block(label: str, text: Any) -> None:
        block = Table([
            [Paragraph(f"<b>{label}</b>", section)],
            [Paragraph(_safe(text), body)],
        ], colWidths=[17.4 * cm])
        block.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.7, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9D9D9")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        story.append(block)

    add_block("RESUMEN DEL PROYECTO", data.get("resumen"))
    add_block("PROBLEMÁTICA A ABORDAR", data.get("problematica"))
    add_block("OBJETIVO", data.get("objetivo"))
    add_block("ESTRATEGIAS O METODOLOGÍA DEL PROYECTO", data.get("metodologia"))
    add_block("IMPACTO SOCIAL", data.get("impacto_social"))
    add_block("IMPACTO EN LOS ESTUDIANTES COLABORADORES", data.get("impacto_estudiantes"))
    add_block("MUESTRA, POBLACIÓN Y/O ENTORNO OBJETIVO", data.get("poblacion_objetivo"))

    story.append(PageBreak())
    cronograma_rows = [[Paragraph("<b>Semana</b>", body), Paragraph("<b>Actividad</b>", body)]]
    for item in data.get("cronograma", []):
        cronograma_rows.append([Paragraph(_safe(item.get("semana")), body), Paragraph(_safe(item.get("actividad")), body)])
    cronograma = Table(cronograma_rows, colWidths=[3.4 * cm, 14 * cm], repeatRows=1)
    cronograma.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.7, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9D9D9")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(Paragraph("CRONOGRAMA DE ACTIVIDADES DEL PROYECTO", section))
    story.append(cronograma)
    story.append(Spacer(1, 10))

    if data.get("financiado"):
        budget_rows = [[
            Paragraph("<b>Bien y/o servicio</b>", body),
            Paragraph("<b>Unidad</b>", body),
            Paragraph("<b>Cantidad</b>", body),
            Paragraph("<b>Costo unitario (S/)</b>", body),
            Paragraph("<b>Costo total (S/)</b>", body),
        ]]
        total = 0.0
        for item in data.get("presupuesto", []):
            amount = float(item.get("cantidad", 0)) * float(item.get("costo_unitario", 0))
            total += amount
            budget_rows.append([
                Paragraph(_safe(item.get("bien_servicio")), body),
                Paragraph(_safe(item.get("unidad_medida")), body),
                str(item.get("cantidad", "")),
                f"{float(item.get('costo_unitario', 0)):.2f}",
                f"{amount:.2f}",
            ])
        budget_rows.append([Paragraph("<b>TOTAL</b>", body), "", "", "", f"{total:.2f}"])
        budget = Table(budget_rows, colWidths=[6.3 * cm, 2.6 * cm, 2 * cm, 3.1 * cm, 3.4 * cm], repeatRows=1)
        budget.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.7, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9D9D9")),
            ("SPAN", (0, -1), (3, -1)),
            ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(Paragraph("PRESUPUESTO", section))
        story.append(budget)
    else:
        story.append(Paragraph("Proyecto no financiado: se omite el cuadro presupuestal conforme al Anexo 2.", body))

    story.append(Spacer(1, 28))
    signatures = [[
        Paragraph("____________________________________<br/>" + _safe(data.get("responsable_nombre")) + "<br/><b>RESPONSABLE DEL PROYECTO DE RSU</b>", ParagraphStyle("s1", parent=body, alignment=TA_CENTER)),
        Paragraph("____________________________________<br/>Dirección de Responsabilidad Social<br/><b>RECEPCIÓN / EVALUACIÓN</b>", ParagraphStyle("s2", parent=body, alignment=TA_CENTER)),
    ]]
    story.append(Table(signatures, colWidths=[8.7 * cm, 8.7 * cm]))

    doc.build(story)
    return output_path
