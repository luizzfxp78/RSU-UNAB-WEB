from __future__ import annotations

"""Generación de anexos usando los DOCX oficiales como plantillas.

El servicio conserva el formato, tablas, encabezados y textos de los anexos
originales. Únicamente reemplaza los espacios de llenado con los datos del
expediente y agrega las imágenes de firma almacenadas en el sistema.
"""

from copy import deepcopy
from datetime import date
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from uuid import uuid4
from typing import Any, Iterable

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_ROW_HEIGHT_RULE
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from PIL import Image, ImageChops, ImageOps


_LIBREOFFICE_CONVERSION_LOCK = threading.RLock()

TEMPLATE_NAMES = {
    1: "Anexo_01_Solicitud.docx",
    2: "Anexo_02_Formato_Proyecto_RSU.docx",
    3: "Anexo_03_Registro_Proyecto_RSU.docx",
    4: "Anexo_04_Ficha_Evaluacion_Proyecto.docx",
    5: "Anexo_05_Informe_Avance.docx",
    6: "Anexo_06_Informe_Final.docx",
    7: "Anexo_07_Evaluacion_Informe_Final.docx",
}


def _template_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "official_annex_templates"


def official_annex_template_path(annex_number: int) -> Path | None:
    """Ruta de la plantilla DOCX oficial usada por el anexo solicitado."""
    template_name = TEMPLATE_NAMES.get(int(annex_number))
    if not template_name:
        return None
    return _template_dir() / template_name


def _annex(project: dict[str, Any], number: int) -> dict[str, Any]:
    return dict((((project.get("anexos") or {}).get(str(number)) or {}).get("contenido") or {}))


def _team(project: dict[str, Any]) -> list[dict[str, Any]]:
    return list(project.get("equipo") or [])


def _responsible(project: dict[str, Any]) -> dict[str, Any]:
    return next((item for item in _team(project) if str(item.get("rol") or "").lower() == "responsable"), {})


def _evaluation(project: dict[str, Any], number: int) -> dict[str, Any]:
    return next(
        (item for item in project.get("evaluaciones") or [] if int(item.get("anexo_numero") or 0) == number),
        {},
    )


def _date(value: Any) -> str:
    if not value:
        return ""
    text = str(value)
    if len(text) >= 10 and text[4:5] == "-":
        return f"{text[8:10]}/{text[5:7]}/{text[0:4]}"
    return text


def _weekly(value: Any) -> dict[int, str]:
    result: dict[int, str] = {}
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            try:
                week = int(item.get("semana") or 0)
            except (TypeError, ValueError):
                continue
            if week:
                result[week] = str(item.get("actividad") or "")
    elif isinstance(value, dict):
        for key, item in value.items():
            raw = str(key).lower().replace("semana_", "").replace("semana ", "")
            try:
                week = int(raw)
            except ValueError:
                continue
            result[week] = str(item.get("actividad") if isinstance(item, dict) else item or "")
    return result


def _clear_paragraph(paragraph) -> None:
    element = paragraph._p
    for child in list(element):
        element.remove(child)


def _set_paragraph(paragraph, text: Any, *, size: float = 9.0, bold: bool = False, center: bool = False) -> None:
    _clear_paragraph(paragraph)
    if center:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run(str(text or ""))
    run.bold = bold
    run.font.size = Pt(size)
    run.font.name = "Arial"


def _set_cell(cell, text: Any, *, size: float = 8.0, bold: bool = False, center: bool = False) -> None:
    cell.text = ""
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    _set_cell_margins(cell)
    paragraph = cell.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER if center else WD_ALIGN_PARAGRAPH.LEFT
    paragraph.paragraph_format.keep_together = False
    paragraph.paragraph_format.keep_with_next = False
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    paragraph.paragraph_format.line_spacing = 1.0
    run = paragraph.add_run(str(text or ""))
    run.bold = bold
    run.font.size = Pt(size)
    run.font.name = "Arial"


def _append_cell_value(cell, value: Any, *, size: float = 8.0, bold: bool = False) -> None:
    """Agrega datos a una celda sin borrar el texto ni el formato oficial."""
    if value in (None, ""):
        return
    paragraph = cell.paragraphs[-1]
    separator = "\n" if cell.text.strip() else ""
    run = paragraph.add_run(separator + str(value))
    run.bold = bold
    run.font.size = Pt(size)
    run.font.name = "Arial"


def _fill_signature_paragraph(
    paragraph,
    *,
    name: str,
    role: str,
    record: dict[str, Any] | None,
    temp_dir: Path,
    key: str,
) -> None:
    """Rellena un espacio de firma existente sin crear tablas ni filas nuevas."""
    _clear_paragraph(paragraph)
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if record:
        prepared = _prepare_signature_image(str(record.get("archivo_url") or ""), temp_dir, key)
        if prepared:
            paragraph.add_run().add_picture(str(prepared), width=Cm(3.8))
            paragraph.add_run("\n")
    line = paragraph.add_run("____________________________\n")
    line.font.size = Pt(8)
    name_run = paragraph.add_run((name or "Nombres y apellidos") + "\n")
    name_run.font.size = Pt(8)
    role_run = paragraph.add_run(role)
    role_run.bold = True
    role_run.font.size = Pt(7.5)


def _set_cell_margins(cell, top: int = 50, start: int = 70, bottom: int = 50, end: int = 70) -> None:
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for margin, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{margin}"))
        if node is None:
            node = OxmlElement(f"w:{margin}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def _allow_row_growth(row) -> None:
    """Elimina alturas exactas y permite que la fila crezca o continúe en otra página."""
    tr_pr = row._tr.get_or_add_trPr()
    for tag in ("w:trHeight", "w:cantSplit"):
        for node in list(tr_pr.findall(qn(tag))):
            tr_pr.remove(node)
    row.height = None
    row.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST


def _make_table_inline(table, *, page_break_before: bool = False) -> None:
    """Convierte una tabla flotante en tabla en línea para evitar superposiciones.

    El Anexo 2 original posiciona el cronograma con ``tblpPr`` en una
    coordenada fija de la página. Cuando las respuestas anteriores crecen,
    esa posición fija hace que ambas tablas se monten. Se conserva la tabla,
    sus filas, columnas y bordes; únicamente se elimina el posicionamiento
    flotante y, cuando corresponde, se inicia en una página nueva.
    """
    tbl_pr = table._tbl.tblPr
    floating = tbl_pr.find(qn("w:tblpPr"))
    if floating is not None:
        tbl_pr.remove(floating)

    if not page_break_before:
        return

    paragraph = OxmlElement("w:p")
    run = OxmlElement("w:r")
    page_break = OxmlElement("w:br")
    page_break.set(qn("w:type"), "page")
    run.append(page_break)
    paragraph.append(run)
    table._tbl.addprevious(paragraph)


def _adaptive_text_size(value: Any, *, default: float = 8.2, minimum: float = 6.8) -> float:
    text = str(value or "")
    length = len(text)
    lines = max(1, text.count("\n") + 1)
    pressure = length + max(0, lines - 2) * 45
    if pressure > 1400:
        return minimum
    if pressure > 950:
        return max(minimum, default - 1.1)
    if pressure > 600:
        return max(minimum, default - 0.7)
    if pressure > 350:
        return max(minimum, default - 0.35)
    return default


def _set_minimum_table_font(table, size: float) -> None:
    for row in table.rows:
        _allow_row_growth(row)
        for cell in row.cells:
            _set_cell_margins(cell)
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.keep_together = False
                paragraph.paragraph_format.keep_with_next = False
                for run in paragraph.runs:
                    current = float(run.font.size.pt) if run.font.size is not None else 0.0
                    if current < size:
                        run.font.size = Pt(size)
                    run.font.name = "Arial"


def _remove_table_borders(table) -> None:
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        node = borders.find(qn(f"w:{edge}"))
        if node is None:
            node = OxmlElement(f"w:{edge}")
            borders.append(node)
        node.set(qn("w:val"), "nil")


def _insert_table_before(document: Document, anchor_paragraph, rows: int, cols: int):
    table = document.add_table(rows=rows, cols=cols)
    table._tbl.getparent().remove(table._tbl)
    anchor_paragraph._p.addprevious(table._tbl)
    _remove_table_borders(table)
    return table


def _clone_row(table, source_index: int = -1):
    copied = deepcopy(table.rows[source_index]._tr)
    table._tbl.append(copied)
    return table.rows[-1]


def _signature_files(project: dict[str, Any], annex_number: int) -> list[dict[str, Any]]:
    records = []
    for item in project.get("_archivos_pdf") or []:
        try:
            number = int(item.get("anexo_numero") or 0)
        except (TypeError, ValueError):
            continue
        path = Path(str(item.get("archivo_url") or ""))
        if number != annex_number or not path.exists():
            continue
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        combined = f"{item.get('proposito') or ''} {item.get('etiqueta') or ''}".lower()
        if "firma" not in combined:
            continue
        records.append(dict(item))
    return records


def _select_signature(
    files: list[dict[str, Any]],
    used: set[str],
    *,
    person_id: str | None = None,
    keywords: Iterable[str] = (),
) -> dict[str, Any] | None:
    normalized_person = str(person_id or "")
    normalized_keywords = [str(item).lower() for item in keywords if str(item).strip()]
    if normalized_person:
        match = next(
            (
                item for item in files
                if str(item.get("workflow_file_id") or "") not in used
                and str(item.get("persona_firmante_id") or "") == normalized_person
            ),
            None,
        )
        if match:
            return match
    if normalized_keywords:
        match = next(
            (
                item for item in files
                if str(item.get("workflow_file_id") or "") not in used
                and any(
                    keyword in f"{item.get('etiqueta') or ''} {item.get('proposito') or ''}".lower()
                    for keyword in normalized_keywords
                )
            ),
            None,
        )
        if match:
            return match
    return next(
        (item for item in files if str(item.get("workflow_file_id") or "") not in used),
        None,
    )


def _prepare_signature_image(path: str | Path, temp_dir: Path, key: str) -> Path | None:
    try:
        image = Image.open(path)
        image = ImageOps.exif_transpose(image).convert("RGBA")
        background = Image.new("RGBA", image.size, (255, 255, 255, 255))
        difference = ImageChops.difference(image, background).convert("L")
        difference = difference.point(lambda pixel: 255 if pixel > 16 else 0)
        bbox = difference.getbbox()
        if bbox:
            image = image.crop(bbox)
        image.thumbnail((1000, 300), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (1000, 300), (255, 255, 255, 0))
        x = max(0, (canvas.width - image.width) // 2)
        y = max(0, (canvas.height - image.height) // 2)
        canvas.alpha_composite(image, (x, y))
        output = temp_dir / f"firma_{key}.png"
        canvas.save(output, "PNG")
        return output
    except Exception:
        return None


def _fill_signature_cell(
    cell,
    *,
    name: str,
    role: str,
    record: dict[str, Any] | None,
    temp_dir: Path,
    key: str,
) -> None:
    cell.text = ""
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.BOTTOM
    paragraph = cell.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if record:
        prepared = _prepare_signature_image(str(record.get("archivo_url") or ""), temp_dir, key)
        if prepared:
            run = paragraph.add_run()
            run.add_picture(str(prepared), width=Cm(4.0))
            paragraph.add_run("\n")
    line = paragraph.add_run("____________________________\n")
    line.font.size = Pt(8)
    name_run = paragraph.add_run((name or "Nombres y apellidos") + "\n")
    name_run.font.size = Pt(8)
    role_run = paragraph.add_run(role)
    role_run.bold = True
    role_run.font.size = Pt(7.5)


def _signature_table(
    document: Document,
    anchor,
    project: dict[str, Any],
    annex_number: int,
    signers: list[dict[str, Any]],
    temp_dir: Path,
    *,
    columns: int | None = None,
):
    if not signers:
        return None
    cols = columns or len(signers)
    rows = (len(signers) + cols - 1) // cols
    table = _insert_table_before(document, anchor, rows, cols)
    files = _signature_files(project, annex_number)
    used: set[str] = set()
    for index in range(rows * cols):
        cell = table.cell(index // cols, index % cols)
        if index >= len(signers):
            _set_cell(cell, "")
            continue
        signer = signers[index]
        record = _select_signature(
            files,
            used,
            person_id=str(signer.get("person_id") or "") or None,
            keywords=signer.get("keywords") or [],
        )
        if record:
            used.add(str(record.get("workflow_file_id") or ""))
        _fill_signature_cell(
            cell,
            name=str(signer.get("name") or ""),
            role=str(signer.get("role") or ""),
            record=record,
            temp_dir=temp_dir,
            key=f"{annex_number}_{index}",
        )
        _set_cell_margins(cell, top=40, bottom=40)
    return table


def _team_signers(project: dict[str, Any]) -> list[dict[str, Any]]:
    signers: list[dict[str, Any]] = []
    for item in _team(project):
        role = str(item.get("rol") or "").lower()
        if role not in {"responsable", "corresponsable"}:
            continue
        signers.append(
            {
                "name": str(item.get("nombre_completo") or ""),
                "role": f"{role.upper()} DEL PROYECTO DE RSU",
                "person_id": item.get("persona_id"),
                "keywords": ["representante" if role == "responsable" else "corresponsable"],
            }
        )
    return signers[:3]


def _fill_annex1(document: Document, project: dict[str, Any], temp_dir: Path) -> None:
    data = _annex(project, 1)
    responsible = _responsible(project)
    responsible_name = str(data.get("responsable") or responsible.get("nombre_completo") or "")
    line = project.get("linea_accion") or data.get("linea_accion") or ""
    for paragraph in document.paragraphs:
        original = paragraph.text.strip()
        if original.startswith("Yo,"):
            _set_paragraph(
                paragraph,
                f"Yo, {responsible_name}, identificado con N° DNI "
                f"{data.get('dni') or responsible.get('dni') or ''}, domiciliado en {data.get('domicilio') or ''}, "
                f"en mi condición de {data.get('condicion') or ''}, ante usted con el debido respeto me presento y expongo:",
                size=10,
            )
        elif original.startswith("Que, teniendo el interés"):
            _set_paragraph(
                paragraph,
                f"Que, teniendo el interés de participar en las actividades de RSU en el semestre académico, "
                f"presento el {data.get('titulo') or project.get('titulo') or ''}, en el marco de la Línea de Acción "
                f"de RSU UNAB: {line}, para su aprobación según corresponda.",
                size=10,
            )

    files = _signature_files(project, 1)
    record = _select_signature(
        files,
        set(),
        person_id=str(responsible.get("persona_id") or "") or None,
        keywords=["representante", "responsable"],
    )
    signature_line = next((p for p in document.paragraphs if "……" in p.text or "...." in p.text), None)
    if signature_line is not None:
        original_line = signature_line.text or "……………………………………………….."
        _clear_paragraph(signature_line)
        signature_line.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if record:
            prepared = _prepare_signature_image(str(record.get("archivo_url") or ""), temp_dir, "1_responsable")
            if prepared:
                signature_line.add_run().add_picture(str(prepared), width=Cm(4.0))
                signature_line.add_run("\n")
        signature_line.add_run(original_line)
    name_paragraph = next((p for p in document.paragraphs if p.text.strip().lower() == "nombres y apellidos"), None)
    if name_paragraph is not None:
        _set_paragraph(name_paragraph, responsible_name, size=9, center=True)


def _fill_annex2(document: Document, project: dict[str, Any], temp_dir: Path) -> None:
    data = _annex(project, 2)
    team = _team(project)
    first = document.tables[0]
    second = document.tables[1]

    # El cronograma del formato original es una tabla flotante con posición
    # absoluta. Se vuelve una tabla en línea y se inicia en una página nueva,
    # de modo que el crecimiento de las respuestas nunca la superponga.
    _make_table_inline(second, page_break_before=True)

    # Las plantillas oficiales suelen contener alturas exactas. Se eliminan
    # únicamente esas restricciones para que el texto extenso fluya sin
    # superponerse, conservando filas, columnas y bordes originales.
    for row in first.rows:
        _allow_row_growth(row)
    for row in second.rows:
        _allow_row_growth(row)

    _set_cell(first.cell(0, 1), project.get("titulo") or data.get("titulo"), size=9)
    _set_cell(first.cell(1, 1), project.get("linea_accion") or data.get("linea_accion"), size=9)

    # Formato oficial solicitado: - Nombre completo - DNI - Cargo.
    participants = "\n".join(
        f"- {str(item.get('nombre_completo') or '').strip()} - "
        f"{str(item.get('dni') or item.get('codigo') or '').strip()} - "
        f"{str(item.get('rol') or '').replace('_', ' ').title()}"
        for item in team
    )
    _set_cell(
        first.cell(2, 1),
        participants,
        size=_adaptive_text_size(participants, default=7.7, minimum=6.7),
    )

    values = {
        4: data.get("resumen") or project.get("resumen"),
        6: data.get("problematica"),
        8: data.get("objetivos") or data.get("objetivo"),
        10: data.get("metodologia"),
        12: data.get("impacto_social"),
        14: data.get("impacto_estudiantes"),
        16: data.get("poblacion_objetivo") or project.get("poblacion_objetivo"),
    }
    for row_index, value in values.items():
        _set_cell(
            first.cell(row_index, 0),
            value,
            size=_adaptive_text_size(value, default=8.2, minimum=6.8),
        )

    schedule = _weekly(data.get("cronograma"))
    for week in range(1, 17):
        activity = schedule.get(week, "")
        cell = second.cell(week - 1, 1)
        _set_cell_margins(cell)
        _append_cell_value(
            cell,
            activity,
            size=_adaptive_text_size(activity, default=7.5, minimum=6.6),
        )

    budgets = list(data.get("presupuesto") or [])
    for index in range(4):
        row_index = 17 + index
        item = budgets[index] if index < len(budgets) else {}
        if index == 3 and len(budgets) > 4:
            extras = budgets[3:]
            item = {
                "bien_servicio": "Otros: " + "; ".join(str(x.get("bien_servicio") or "") for x in extras),
                "unidad_medida": "Varios",
                "cantidad": sum(int(x.get("cantidad") or 0) for x in extras),
                "costo_unitario": 0,
                "_total": sum(float(x.get("cantidad") or 0) * float(x.get("costo_unitario") or 0) for x in extras),
            }
        total = item.get("_total")
        if total is None:
            total = float(item.get("cantidad") or 0) * float(item.get("costo_unitario") or 0)
        values_row = (
            item.get("bien_servicio") or "",
            item.get("unidad_medida") or "",
            item.get("cantidad") or "",
            f"{float(item.get('costo_unitario') or 0):.2f}" if item else "",
            f"{float(total or 0):.2f}" if item else "",
        )
        for col, value in enumerate(values_row, start=1):
            size = _adaptive_text_size(value, default=7.2, minimum=6.5) if col <= 2 else 7.0
            _set_cell(second.cell(row_index, col), value, size=size, center=col >= 3)

    # Firmas: se insertan una sola vez dentro de los espacios existentes de la
    # plantilla. Los tabuladores fijos evitan que las imágenes se desplacen o
    # aparezcan superpuestas al convertir a PDF.
    signers = _team_signers(project)
    signature_files = _signature_files(project, 2)
    used_signatures: set[str] = set()

    top_signature_paragraph = document.paragraphs[6] if len(document.paragraphs) > 6 else None
    if top_signature_paragraph is not None:
        _clear_paragraph(top_signature_paragraph)
        top_signature_paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        fmt = top_signature_paragraph.paragraph_format
        fmt.left_indent = Cm(2.0)
        fmt.space_before = Pt(0)
        fmt.space_after = Pt(0)
        fmt.tab_stops.add_tab_stop(Cm(11.1), WD_TAB_ALIGNMENT.LEFT)
        for index in range(2):
            signer = signers[index] if index < len(signers) else {}
            record = _select_signature(
                signature_files,
                used_signatures,
                person_id=str(signer.get("person_id") or "") or None,
                keywords=signer.get("keywords") or [],
            )
            if record:
                used_signatures.add(str(record.get("workflow_file_id") or ""))
                prepared = _prepare_signature_image(str(record.get("archivo_url") or ""), temp_dir, f"2_top_{index}")
                if prepared:
                    top_signature_paragraph.add_run().add_picture(str(prepared), width=Cm(3.0))
            if index == 0:
                top_signature_paragraph.add_run("\t")
        top_signature_paragraph.add_run(
            "\n…………………….………………………………\t………………….…………………………………."
        )

    top_names = [str(item.get("name") or "") for item in signers[:2]]
    while len(top_names) < 2:
        top_names.append("")
    top_name_paragraph = next(
        (paragraph for paragraph in document.paragraphs if "Nombres y Apellidos" in paragraph.text and "\t" in paragraph.text),
        None,
    )
    if top_name_paragraph is not None:
        _clear_paragraph(top_name_paragraph)
        top_name_paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        top_name_paragraph.paragraph_format.left_indent = Cm(2.0)
        top_name_paragraph.paragraph_format.tab_stops.add_tab_stop(Cm(11.1), WD_TAB_ALIGNMENT.LEFT)
        run = top_name_paragraph.add_run(f"{top_names[0]}\t{top_names[1]}")
        run.font.size = Pt(8)
        run.font.name = "Arial"

    third_signature_paragraph = document.paragraphs[11] if len(document.paragraphs) > 11 else None
    if third_signature_paragraph is not None:
        _clear_paragraph(third_signature_paragraph)
        third_signature_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        third_signer = signers[2] if len(signers) > 2 else {}
        third_record = _select_signature(
            signature_files,
            used_signatures,
            person_id=str(third_signer.get("person_id") or "") or None,
            keywords=third_signer.get("keywords") or [],
        )
        if third_record:
            prepared = _prepare_signature_image(str(third_record.get("archivo_url") or ""), temp_dir, "2_third")
            if prepared:
                third_signature_paragraph.add_run().add_picture(str(prepared), width=Cm(3.0))
                third_signature_paragraph.add_run("\n")
        third_signature_paragraph.add_run("………………………………………………………….")

    third_name_paragraph = next((p for p in document.paragraphs if p.text.strip() == "Nombre y Apellido"), None)
    if third_name_paragraph is not None:
        _set_paragraph(
            third_name_paragraph,
            str(signers[2].get("name") or "") if len(signers) > 2 else "",
            size=8,
            center=True,
        )

    # Mantiene todo el bloque de firmas unido en una página propia. Sin esta
    # regla Word puede dejar las líneas y nombres al pie de una página y mover
    # los cargos a la siguiente.
    if len(document.paragraphs) > 6:
        document.paragraphs[6].paragraph_format.page_break_before = True
    for paragraph_index in range(6, min(13, len(document.paragraphs))):
        document.paragraphs[paragraph_index].paragraph_format.keep_with_next = True
        document.paragraphs[paragraph_index].paragraph_format.keep_together = True
    if len(document.paragraphs) > 13:
        document.paragraphs[13].paragraph_format.keep_together = True
    for paragraph_index in (9, 10, 14, 15):
        if paragraph_index < len(document.paragraphs):
            blank = document.paragraphs[paragraph_index]
            _clear_paragraph(blank)
            blank.paragraph_format.space_before = Pt(0)
            blank.paragraph_format.space_after = Pt(0)
            blank.paragraph_format.line_spacing = 1.0


def _fill_annex3(document: Document, project: dict[str, Any], temp_dir: Path) -> None:
    table = document.tables[0]
    _set_minimum_table_font(table, 8.5)
    _set_cell(table.cell(0, 2), project.get("titulo"), size=10)
    _set_cell(table.cell(1, 2), project.get("linea_accion"), size=10)
    team = _team(project)

    # Distribuye el equipo entre las cuatro filas oficiales sin crear filas ni
    # alterar la tabla. Esto mantiene una letra más grande y uniforme.
    groups: list[list[dict[str, Any]]] = []
    base, extra = divmod(len(team), 4)
    offset = 0
    for slot in range(4):
        count = base + (1 if slot < extra else 0)
        groups.append(team[offset:offset + count])
        offset += count

    running_number = 1
    for slot, members in enumerate(groups):
        row_index = 4 + slot
        _allow_row_growth(table.rows[row_index])
        if not members:
            for col in range(7):
                _set_cell(table.cell(row_index, col), "", size=8.0, center=True)
            continue
        numbers = list(range(running_number, running_number + len(members)))
        running_number += len(members)
        values = [
            "\n".join(str(number) for number in numbers),
            "\n".join(str(item.get("nombre_completo") or "") for item in members),
            "\n".join(str(item.get("codigo") or item.get("dni") or "") for item in members),
            "\n".join(str(item.get("escuela") or "") for item in members),
            "\n".join(str(item.get("correo") or "") for item in members),
            "\n".join(str(item.get("telefono") or "") for item in members),
            "\n".join(str(item.get("rol") or "").replace("_", " ").title() for item in members),
        ]
        size = 8.0 if len(members) == 1 else 7.3
        for col, value in enumerate(values):
            _set_cell(table.cell(row_index, col), value, size=size, center=col in {0, 2, 5, 6})


def _fill_annex4(document: Document, project: dict[str, Any], temp_dir: Path) -> None:
    table = document.tables[0]
    team = _team(project)
    evaluation = _evaluation(project, 4)
    answers = evaluation.get("criterios") or _annex(project, 4).get("criterios") or {}
    details = evaluation.get("observaciones_por_criterio") or _annex(project, 4).get("observaciones_por_criterio") or {}
    _set_cell(table.cell(1, 0), project.get("titulo"), size=8.5)

    participant_groups = [team[:1], team[1:]]
    for row, members in zip((4, 5), participant_groups):
        values = (
            "\n".join(str(item.get("nombre_completo") or "") for item in members),
            "\n".join(str(item.get("dni") or item.get("codigo") or "") for item in members),
            "\n".join(str(item.get("correo") or "") for item in members),
            "\n".join(str(item.get("escuela") or "") for item in members),
        )
        for col, value in enumerate(values):
            _set_cell(table.cell(row, col), value, size=6.5)

    _append_cell_value(table.cell(7, 0), project.get("linea_accion") or "", size=7.2)
    mapping = {
        "resumen": (8, "Resumen"),
        "problematica": (10, "Problemática a abordar"),
        "objetivo": (12, "Objetivo"),
        "metodologia": (14, "Estrategias o metodología"),
        "impacto_social": (16, "Impacto social"),
        "impacto_estudiantes": (18, "Impacto en los estudiantes colaboradores"),
        "poblacion": (20, "Muestra, población y/o entorno objetivo"),
        "cronograma": (22, "Cronograma de actividades"),
        "presupuesto": (24, "Presupuesto"),
    }
    observation_lines: list[str] = []
    for key, (row, label) in mapping.items():
        answer = answers.get(key)
        _set_cell(table.cell(row, 2), "SI" + ("\nX" if answer is True else ""), size=7.2, bold=True, center=True)
        _set_cell(table.cell(row, 3), "NO" + ("\nX" if answer is False else ""), size=7.2, bold=True, center=True)
        detail = str(details.get(key) or "").strip()
        if detail:
            observation_lines.append(f"- {label}: {detail}")

    total_yes = sum(1 for key in mapping if answers.get(key) is True)
    total_no = sum(1 for key in mapping if answers.get(key) is False)
    _set_cell(table.cell(27, 2), f"TOTAL, SI\n{total_yes}", size=7.2, bold=True, center=True)
    _set_cell(table.cell(27, 3), f"TOTAL, NO\n{total_no}", size=7.2, bold=True, center=True)
    result = "APROBADO" if answers and total_no == 0 and not (evaluation.get("observaciones") or details) else "OBSERVADO"
    _set_cell(table.cell(28, 2), result if answers else "", size=7.5, bold=True, center=True)

    general = str(evaluation.get("observaciones") or _annex(project, 4).get("observaciones") or "").strip()
    if general:
        observation_lines.append(general)
    original_observation = table.cell(29, 0).text
    filled_observations = "\n".join(observation_lines)
    _set_cell(
        table.cell(29, 0),
        f"{original_observation}\n{filled_observations}".strip(),
        size=6.5,
    )

    # El Anexo 4 usa las firmas institucionales registradas en el módulo
    # Roles y autoridades. No solicita la firma personal del evaluador.
    authorities = dict(project.get("_autoridades_firma") or {})
    director_rsu = dict(authorities.get("directora_rsu") or {})
    faculty_director = dict(authorities.get("director_facultad") or {})
    authority_signers = [
        {
            "name": str(director_rsu.get("nombre_completo") or ""),
            "role": str(director_rsu.get("cargo_oficial") or "DIRECTORA DE LA DIRECCIÓN DE RESPONSABILIDAD SOCIAL"),
            "record": {"archivo_url": director_rsu.get("firma_url")} if director_rsu.get("firma_url") else None,
        },
        {
            "name": str(faculty_director.get("nombre_completo") or ""),
            "role": str(faculty_director.get("cargo_oficial") or "DIRECTOR(A) DE FACULTAD"),
            "record": {"archivo_url": faculty_director.get("firma_url")} if faculty_director.get("firma_url") else None,
        },
    ]
    if len(document.tables) > 1 and document.tables[1].rows:
        for index, signer in enumerate(authority_signers):
            _fill_signature_cell(
                document.tables[1].cell(0, index),
                name=signer["name"],
                role=signer["role"],
                record=signer["record"],
                temp_dir=temp_dir,
                key=f"4_authority_{index}",
            )
    # La plantilla oficial conserva un tercer espacio. Se deja vacío para no
    # agregar una firma distinta a las dos autoridades solicitadas.
    if document.paragraphs:
        last = document.paragraphs[-1]
        _clear_paragraph(last)


def _fill_annex5(document: Document, project: dict[str, Any], temp_dir: Path) -> None:
    data = _annex(project, 5)
    responsible = _responsible(project)
    table = document.tables[0]
    values = [
        project.get("titulo"),
        project.get("linea_accion"),
        project.get("lugar_ejecucion"),
        responsible.get("nombre_completo"),
        _date(project.get("fecha_inicio")),
        data.get("duracion_horas"),
    ]
    for row, value in enumerate(values):
        _set_cell(table.cell(row, 1), value, size=8)
    activities = _weekly(data.get("actividades_semanales"))
    for week in range(1, 10):
        _set_cell(table.cell(6 + week, 1), activities.get(week, ""), size=7.3)
    _set_cell(table.cell(17, 0), data.get("resumen_labores"), size=7.5)
    _set_cell(table.cell(19, 0), data.get("dificultades"), size=7.5)
    _set_cell(table.cell(21, 0), data.get("oportunidades_logros"), size=7.5)
    _set_cell(table.cell(22, 0), f"Fecha de envío del informe de Avance: {_date(data.get('fecha_envio') or '')}", size=7.2)
    signers = _team_signers(project)
    files = _signature_files(project, 5)
    used: set[str] = set()
    for index in range(2):
        signer = signers[index] if index < len(signers) else {"name": "", "role": ""}
        record = _select_signature(files, used, person_id=signer.get("person_id"), keywords=signer.get("keywords") or [])
        if record:
            used.add(str(record.get("workflow_file_id") or ""))
        _fill_signature_cell(document.tables[1].cell(0, index), name=str(signer.get("name") or ""), role=str(signer.get("role") or ""), record=record, temp_dir=temp_dir, key=f"5_{index}")
    if len(signers) > 2:
        paragraph = document.paragraphs[-1]
        record = _select_signature(files, used, person_id=signers[2].get("person_id"), keywords=signers[2].get("keywords") or [])
        _clear_paragraph(paragraph)
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if record:
            prepared = _prepare_signature_image(record.get("archivo_url"), temp_dir, "5_2")
            if prepared:
                paragraph.add_run().add_picture(str(prepared), width=Cm(4.0))
                paragraph.add_run("\n")
        paragraph.add_run(f"____________________________\n{signers[2].get('name') or ''}\n{signers[2].get('role') or ''}")


def _fill_annex6(document: Document, project: dict[str, Any], temp_dir: Path) -> None:
    """Rellena el nuevo formato oficial del Anexo 6.

    El formato entregado contiene filas independientes para las semanas 1 a 9
    y 12 a 16. Las actividades de las primeras nueve semanas pueden venir
    precargadas desde el Anexo 5 y siguen siendo editables por el responsable.
    """
    data = _annex(project, 6)
    responsible = _responsible(project)
    table = document.tables[0]

    for row in table.rows:
        _allow_row_growth(row)

    header_values = [
        project.get("titulo"),
        data.get("facultad") or responsible.get("facultad"),
        data.get("escuela_profesional") or responsible.get("escuela"),
        responsible.get("nombre_completo"),
        project.get("linea_accion"),
        project.get("lugar_ejecucion"),
        _date(project.get("fecha_inicio")),
        _date(project.get("fecha_fin")),
        data.get("duracion_horas"),
    ]
    for row_index, value in enumerate(header_values):
        _set_cell(
            table.cell(row_index, 1),
            value,
            size=_adaptive_text_size(value, default=8.0, minimum=7.0),
        )

    activities = _weekly(data.get("actividades_semanales"))
    week_rows = {
        1: 10,
        2: 11,
        3: 12,
        4: 13,
        5: 14,
        6: 15,
        7: 16,
        8: 17,
        9: 18,
        12: 19,
        13: 20,
        14: 21,
        15: 22,
        16: 23,
    }
    for week, row_index in week_rows.items():
        activity = activities.get(week, "")
        _set_cell(
            table.cell(row_index, 1),
            activity,
            size=_adaptive_text_size(activity, default=7.5, minimum=6.7),
        )

    # Se mantienen intactos los rótulos oficiales y se rellenan únicamente las
    # filas blancas ubicadas debajo de cada pregunta.
    narrative_rows = {
        25: data.get("resumen_labores"),
        27: data.get("dificultades"),
        29: data.get("resultados_logros"),
        31: data.get("recomendaciones"),
    }
    for row_index, value in narrative_rows.items():
        _set_cell(
            table.cell(row_index, 0),
            value,
            size=_adaptive_text_size(value, default=7.5, minimum=6.7),
        )

    date_value = _date(data.get("fecha_envio") or date.today().isoformat())
    _set_cell(
        table.cell(32, 0),
        "Fecha de envío del informe al correo de la Dirección de Responsabilidad Social:\n"
        f"{date_value}",
        size=7.2,
    )

    signers = _team_signers(project)
    files = _signature_files(project, 6)
    used: set[str] = set()
    for index in range(2):
        signer = signers[index] if index < len(signers) else {"name": "", "role": ""}
        record = _select_signature(
            files,
            used,
            person_id=signer.get("person_id"),
            keywords=signer.get("keywords") or [],
        )
        if record:
            used.add(str(record.get("workflow_file_id") or ""))
        _fill_signature_cell(
            document.tables[1].cell(0, index),
            name=str(signer.get("name") or ""),
            role=str(signer.get("role") or ""),
            record=record,
            temp_dir=temp_dir,
            key=f"6_{index}",
        )
    if len(signers) > 2:
        record = _select_signature(
            files,
            used,
            person_id=signers[2].get("person_id"),
            keywords=signers[2].get("keywords") or [],
        )
        _fill_signature_paragraph(
            document.paragraphs[-1],
            name=str(signers[2].get("name") or ""),
            role=str(signers[2].get("role") or ""),
            record=record,
            temp_dir=temp_dir,
            key="6_2",
        )



def _fill_annex7(document: Document, project: dict[str, Any], temp_dir: Path) -> None:
    evaluation = _evaluation(project, 7)
    annex = _annex(project, 7)
    answers = evaluation.get("criterios") or annex.get("criterios") or {}
    details = evaluation.get("observaciones_por_criterio") or annex.get("observaciones_por_criterio") or {}
    responsible = _responsible(project)
    data6 = _annex(project, 6)
    table = document.tables[0]
    _set_cell(table.cell(1, 0), project.get("titulo"), size=8)
    _set_cell(table.cell(4, 0), data6.get("facultad") or responsible.get("escuela"), size=7.2)
    _set_cell(table.cell(4, 2), data6.get("escuela_profesional") or responsible.get("escuela"), size=7.2)
    _set_cell(table.cell(6, 0), responsible.get("nombre_completo"), size=7)
    _set_cell(table.cell(6, 2), responsible.get("dni"), size=7)
    _set_cell(table.cell(7, 0), responsible.get("correo"), size=7)
    _set_cell(table.cell(7, 2), responsible.get("telefono"), size=7)
    _set_cell(table.cell(9, 0), project.get("linea_accion"), size=7.5)
    _set_cell(table.cell(11, 0), project.get("lugar_ejecucion"), size=7.5)
    _set_cell(table.cell(13, 0), _date(project.get("fecha_inicio")), size=7.5)
    _set_cell(table.cell(15, 0), _date(project.get("fecha_fin")), size=7.5)
    mapping = {
        "resumen_evidencias": (17, "Resumen y evidencia de labores realizadas"),
        "dificultades": (19, "Dificultades encontradas"),
        "logros": (21, "Oportunidades y/o logros alcanzados"),
        "recomendaciones": (23, "Recomendaciones y/o sugerencias"),
        "indicadores": (25, "Indicadores de logro de resultados"),
    }
    observation_lines: list[str] = []
    for key, (row, label) in mapping.items():
        answer = answers.get(key)
        _set_cell(table.cell(row, 2), "SI" + ("\nX" if answer is True else ""), size=7, bold=True, center=True)
        _set_cell(table.cell(row, 3), "NO" + ("\nX" if answer is False else ""), size=7, bold=True, center=True)
        detail = str(details.get(key) or "").strip()
        if detail:
            observation_lines.append(f"- {label}: {detail}")
    total_yes = sum(1 for key in mapping if answers.get(key) is True)
    total_no = sum(1 for key in mapping if answers.get(key) is False)
    _set_cell(table.cell(28, 2), f"TOTAL, SI\n{total_yes}", size=7, bold=True, center=True)
    _set_cell(table.cell(28, 3), f"TOTAL, NO\n{total_no}", size=7, bold=True, center=True)

    result_table = document.tables[1]
    result = "APROBADA" if answers and total_no == 0 and not (evaluation.get("observaciones") or details) else "DESAPROBADA / OBSERVADA"
    _set_cell(result_table.cell(0, 2), result if answers else "", size=7.2, bold=True, center=True)
    _set_cell(result_table.cell(1, 2), f"SÍ: {total_yes} / NO: {total_no}" if answers else "", size=7, center=True)
    general = str(evaluation.get("observaciones") or annex.get("observaciones") or "").strip()
    if general:
        observation_lines.append(general)
    original_observation = result_table.cell(2, 0).text
    filled_observations = "\n".join(observation_lines)
    _set_cell(result_table.cell(2, 0), f"{original_observation}\n{filled_observations}".strip(), size=7)

    authorities = dict(project.get("_autoridades_firma") or {})
    director_rsu = dict(authorities.get("directora_rsu") or {})
    faculty_director = dict(authorities.get("director_facultad") or {})
    director_record = (
        {"archivo_url": director_rsu.get("firma_url")}
        if director_rsu.get("firma_url")
        else None
    )
    faculty_record = (
        {"archivo_url": faculty_director.get("firma_url")}
        if faculty_director.get("firma_url")
        else None
    )
    if len(document.paragraphs) > 3:
        _fill_signature_paragraph(
            document.paragraphs[3],
            name=str(director_rsu.get("nombre_completo") or "Director(a) de la Dirección de RSU"),
            role=str(director_rsu.get("cargo_oficial") or "DIRECTOR(A) DE LA DIRECCIÓN DE RSU"),
            record=director_record,
            temp_dir=temp_dir,
            key="7_director_rsu",
        )
    if len(document.tables) > 2 and document.tables[2].rows:
        _fill_signature_cell(
            document.tables[2].cell(0, 0),
            name=str(faculty_director.get("nombre_completo") or "Director(a) de Facultad"),
            role=str(faculty_director.get("cargo_oficial") or "DIRECTOR(A) DE FACULTAD / JURADO EVALUADOR"),
            record=faculty_record,
            temp_dir=temp_dir,
            key="7_director_facultad",
        )
        _fill_signature_cell(
            document.tables[2].cell(0, 1),
            name="",
            role="",
            record=None,
            temp_dir=temp_dir,
            key="7_espacio_reservado",
        )


FILLERS = {
    1: _fill_annex1,
    2: _fill_annex2,
    3: _fill_annex3,
    4: _fill_annex4,
    5: _fill_annex5,
    6: _fill_annex6,
    7: _fill_annex7,
}


def _stamp_annex2_signatures(pdf_path: Path, project: dict[str, Any]) -> None:
    """Estampa solo las imágenes de firma sobre los espacios oficiales existentes."""
    try:
        from io import BytesIO
        from pypdf import PdfReader, PdfWriter
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas

        reader = PdfReader(str(pdf_path))
        if not reader.pages:
            return
        target_index = max(
            (
                index
                for index, candidate in enumerate(reader.pages)
                if "RESPONSABLE DEL PROYECTO DE RSU" in str(candidate.extract_text() or "").upper()
            ),
            default=len(reader.pages) - 1,
        )
        page = reader.pages[target_index]
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        signers = _team_signers(project)[:3]
        files = _signature_files(project, 2)
        used: set[str] = set()
        positions = [
            (width * 0.29, height * 0.675),
            (width * 0.71, height * 0.675),
            (width * 0.50, height * 0.54),
        ]
        packet = BytesIO()
        overlay_canvas = canvas.Canvas(packet, pagesize=(width, height))
        with tempfile.TemporaryDirectory(prefix="rsu_firmas_anexo2_") as temp_name:
            temp_dir = Path(temp_name)
            for index, signer in enumerate(signers):
                center_x, baseline_y = positions[index]
                record = _select_signature(
                    files, used, person_id=signer.get("person_id"), keywords=signer.get("keywords") or []
                )
                if not record:
                    continue
                used.add(str(record.get("workflow_file_id") or ""))
                prepared = _prepare_signature_image(
                    str(record.get("archivo_url") or ""), temp_dir, f"2_overlay_{index}"
                )
                if prepared is None:
                    continue
                with Image.open(prepared) as image:
                    image_width, image_height = image.size
                draw_width = min(width * 0.18, 125.0)
                draw_height = min(draw_width * image_height / max(1, image_width), 38.0)
                overlay_canvas.drawImage(
                    ImageReader(str(prepared)),
                    center_x - draw_width / 2,
                    baseline_y,
                    width=draw_width,
                    height=draw_height,
                    preserveAspectRatio=True,
                    mask="auto",
                )
        overlay_canvas.save()
        packet.seek(0)
        overlay = PdfReader(packet).pages[0]
        page.merge_page(overlay)
        writer = PdfWriter()
        for existing in reader.pages:
            writer.add_page(existing)
        temp = pdf_path.with_suffix(".signed.pdf")
        with temp.open("wb") as stream:
            writer.write(stream)
        temp.replace(pdf_path)
    except Exception:
        return


def _append_annex5_week_continuation(pdf_path: Path, project: dict[str, Any]) -> None:
    """Conserva el formato reglamentario (semanas 1-9) y adjunta 10-16 si fueron informadas."""
    weeks = _weekly(_annex(project, 5).get("actividades_semanales"))
    additional = [(n, str(weeks.get(n) or "").strip()) for n in range(10, 17)]
    if not any(activity for _, activity in additional):
        return
    from io import BytesIO
    from pypdf import PdfReader, PdfWriter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.utils import simpleSplit
    from xml.sax.saxutils import escape

    stream = BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=48, bottomMargin=42)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('RSUTitle', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=12, leading=16, alignment=TA_CENTER)
    text_style = ParagraphStyle('RSUText', parent=styles['Normal'], fontName='Helvetica', fontSize=9, leading=13)
    content = [Paragraph('ANEXO 5 - CONTINUACION DEL REGISTRO DE ACTIVIDADES SEMANALES', title_style), Spacer(1,12),
        Paragraph('Hoja complementaria del sistema RSU UNAB. El Anexo 5 reglamentario mantiene su formato original de semanas 1 a 9.', text_style), Spacer(1,12),
        Paragraph('Proyecto: '+escape(str(project.get('titulo') or 'No especificado')),text_style),Spacer(1,12)]
    rows = [[Paragraph('Semana',text_style),Paragraph('Actividades realizadas',text_style)]]
    rows.extend([[Paragraph(str(week),text_style),Paragraph(escape(activity) if activity else 'Sin actividad registrada',text_style)] for week,activity in additional])
    table=Table(rows,colWidths=[74,440],repeatRows=1,hAlign='CENTER')
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#DFEAF7')),('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),0.5,colors.HexColor('#B8C7D5')),('LEFTPADDING',(0,0),(-1,-1),9),('RIGHTPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8)]))
    content.append(table)
    doc.build(content)
    stream.seek(0)
    writer=PdfWriter()
    for page in PdfReader(str(pdf_path)).pages: writer.add_page(page)
    for page in PdfReader(stream).pages: writer.add_page(page)
    candidate=pdf_path.with_suffix('.semanas.pdf')
    with candidate.open('wb') as output: writer.write(output)
    candidate.replace(pdf_path)


def _append_annex5_evidence_pages(pdf_path: Path, project: dict[str, Any]) -> None:
    """Adjunta al final del Anexo 5 las fotografías de evidencia presentadas."""
    records: list[dict[str, Any]] = []
    for item in project.get("_archivos_pdf") or []:
        try:
            number = int(item.get("anexo_numero") or 0)
        except (TypeError, ValueError):
            continue
        if number != 5 or "evidencia" not in str(item.get("proposito") or "").lower():
            continue
        path = Path(str(item.get("archivo_url") or ""))
        if path.exists() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".pdf"}:
            records.append(dict(item))
    if not records:
        return
    try:
        from io import BytesIO
        from pypdf import PdfReader, PdfWriter
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas

        writer = PdfWriter()
        for page in PdfReader(str(pdf_path)).pages:
            writer.add_page(page)
        for index, item in enumerate(records, start=1):
            source = Path(str(item.get("archivo_url") or ""))
            if source.suffix.lower() == ".pdf":
                for page in PdfReader(str(source)).pages:
                    writer.add_page(page)
                continue
            with Image.open(source) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
                image_buffer = BytesIO()
                image.save(image_buffer, format="JPEG", quality=92)
                image_buffer.seek(0)
                image_width, image_height = image.size
            page_buffer = BytesIO()
            page_canvas = canvas.Canvas(page_buffer, pagesize=A4)
            page_width, page_height = A4
            page_canvas.setFont("Helvetica-Bold", 13)
            page_canvas.drawCentredString(page_width / 2, page_height - 52, "EVIDENCIAS FOTOGRÁFICAS DEL INFORME DE AVANCE")
            page_canvas.setFont("Helvetica", 9)
            label = str(item.get("etiqueta") or item.get("nombre_archivo") or f"Evidencia {index}")
            page_canvas.drawCentredString(page_width / 2, page_height - 72, f"{index}. {label}"[:110])
            max_width = page_width - 72
            max_height = page_height - 145
            scale = min(max_width / max(1, image_width), max_height / max(1, image_height))
            draw_width = image_width * scale
            draw_height = image_height * scale
            page_canvas.drawImage(
                ImageReader(image_buffer),
                (page_width - draw_width) / 2,
                (page_height - draw_height) / 2 - 10,
                width=draw_width,
                height=draw_height,
                preserveAspectRatio=True,
                mask="auto",
            )
            page_canvas.setFont("Helvetica", 8)
            page_canvas.drawRightString(page_width - 36, 28, f"Evidencia {index} de {len(records)}")
            page_canvas.save()
            page_buffer.seek(0)
            writer.add_page(PdfReader(page_buffer).pages[0])
        temp = pdf_path.with_suffix(".evidencias.pdf")
        with temp.open("wb") as stream:
            writer.write(stream)
        temp.replace(pdf_path)
    except Exception:
        # Las fotografías no deben impedir la descarga del formato principal.
        return


def _postprocess_official_pdf(pdf_path: Path, project: dict[str, Any], annex_number: int) -> None:
    # Las firmas del Anexo 2 ya se insertan dentro del DOCX oficial. Volver a
    # estamparlas sobre el PDF las duplicaba y desordenaba.
    if annex_number == 5:
        _append_annex5_week_continuation(pdf_path, project)
        _append_annex5_evidence_pages(pdf_path, project)


def _trim_trailing_blank_pages(pdf_path: Path) -> None:
    """Elimina páginas finales creadas por saltos residuales de Word/LibreOffice."""
    try:
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(str(pdf_path))
        pages = list(reader.pages)
        if len(pages) <= 1:
            return
        ignored = (
            "reglamento de acciones de responsabilidad social universitaria",
            "dirección de responsabilidad social universitaria",
            "direccion de responsabilidad social universitaria",
            "universidad nacional de barranca",
            "av. toribio de luzuriaga 376",
            "mz j urb la florida barranca",
            "responsabilidad.social@unb.edu.pe",
        )

        def meaningful(page) -> bool:
            text = str(page.extract_text() or "").lower()
            for phrase in ignored:
                text = text.replace(phrase, " ")
            text = re.sub(r"\b\d{1,3}\b", " ", text)
            text = re.sub(r"[^a-záéíóúñü0-9]+", "", text)
            return len(text) >= 12

        keep = len(pages)
        while keep > 1 and not meaningful(pages[keep - 1]):
            keep -= 1
        if keep == len(pages):
            return
        writer = PdfWriter()
        for page in pages[:keep]:
            writer.add_page(page)
        temp = pdf_path.with_suffix(".trimmed.pdf")
        with temp.open("wb") as stream:
            writer.write(stream)
        temp.replace(pdf_path)
    except Exception:
        # El recorte es una optimización visual; nunca debe impedir la descarga.
        return


def _normalize_soffice_candidate(value: Any) -> str | None:
    """Convierte una ruta configurada en el ejecutable CLI real de LibreOffice.

    En Windows se prefiere ``soffice.com`` sobre ``soffice.exe``. El primero
    está diseñado para invocaciones de consola y evita que los errores de
    inicialización abran ventanas modales que bloqueen FastAPI.
    """
    if value in (None, ""):
        return None
    try:
        raw = os.path.expandvars(os.path.expanduser(str(value).strip().strip('"')))
        path = Path(raw)
        if path.is_dir():
            names = (
                ("soffice.com", "soffice.exe", "soffice")
                if os.name == "nt"
                else ("soffice", "libreoffice")
            )
            for name in names:
                candidate = path / name
                if candidate.is_file():
                    return str(candidate.resolve())
            for relative in (
                Path("program") / "soffice.com",
                Path("program") / "soffice.exe",
                Path("program") / "soffice",
                Path("App") / "libreoffice" / "program" / "soffice.com",
                Path("App") / "libreoffice" / "program" / "soffice.exe",
            ):
                candidate = path / relative
                if candidate.is_file():
                    return str(candidate.resolve())
        elif path.is_file():
            # Una configuración antigua puede apuntar a soffice.exe. Cuando
            # exista el wrapper de consola de la misma instalación se usa ese.
            if os.name == "nt" and path.name.lower() == "soffice.exe":
                console = path.with_name("soffice.com")
                if console.is_file():
                    return str(console.resolve())
            return str(path.resolve())
    except (OSError, ValueError):
        return None
    return None


def _registry_soffice_candidates() -> list[str]:
    if os.name != "nt":
        return []
    try:
        import winreg  # type: ignore
    except Exception:
        return []

    candidates: list[str] = []
    locations = [
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\soffice.exe"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\soffice.exe"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\soffice.exe"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\LibreOffice\UNO\InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\LibreOffice\UNO\InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\LibreOffice\UNO\InstallPath"),
    ]
    views = [0]
    for flag_name in ("KEY_WOW64_64KEY", "KEY_WOW64_32KEY"):
        flag = getattr(winreg, flag_name, 0)
        if flag and flag not in views:
            views.append(flag)

    for hive, key_name in locations:
        for view in views:
            try:
                with winreg.OpenKey(hive, key_name, 0, winreg.KEY_READ | view) as key:
                    for value_name in ("", "Path"):
                        try:
                            value, _ = winreg.QueryValueEx(key, value_name)
                        except OSError:
                            continue
                        if value:
                            candidates.append(str(value))
            except OSError:
                continue
    return candidates


def _configured_libreoffice_path() -> str:
    # Pydantic carga este valor desde .env aunque no se encuentre exportado en
    # las variables del proceso.
    try:
        from app.core.config import get_settings

        configured = str(get_settings().libreoffice_path or "").strip()
        if configured:
            return configured
    except Exception:
        pass
    for name in ("RSU_LIBREOFFICE_PATH", "LIBREOFFICE_PATH", "SOFFICE_PATH"):
        configured = str(os.environ.get(name) or "").strip()
        if configured:
            return configured
    return ""


def _find_soffice() -> str | None:
    """Localiza LibreOffice incluso si no fue agregado al PATH de Windows."""
    raw_candidates: list[Any] = []
    configured = _configured_libreoffice_path()
    if configured:
        raw_candidates.append(configured)

    for name in ("soffice.com", "soffice", "soffice.exe", "libreoffice"):
        executable = shutil.which(name)
        if executable:
            raw_candidates.append(executable)

    if os.name == "nt":
        environment_roots = [
            os.environ.get("PROGRAMW6432"),
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
            os.environ.get("USERPROFILE"),
        ]
        for value in environment_roots:
            if not value:
                continue
            base = Path(value)
            raw_candidates.extend(
                [
                    base / "LibreOffice" / "program" / "soffice.com",
                    base / "LibreOffice" / "program" / "soffice.exe",
                    base / "Programs" / "LibreOffice" / "program" / "soffice.com",
                    base / "Programs" / "LibreOffice" / "program" / "soffice.exe",
                    base / "AppData" / "Local" / "Programs" / "LibreOffice" / "program" / "soffice.com",
                    base / "AppData" / "Local" / "Programs" / "LibreOffice" / "program" / "soffice.exe",
                ]
            )
            try:
                raw_candidates.extend(base.glob("LibreOffice*/program/soffice.com"))
                raw_candidates.extend(base.glob("LibreOffice*/program/soffice.exe"))
                raw_candidates.extend(base.glob("Programs/LibreOffice*/program/soffice.com"))
                raw_candidates.extend(base.glob("Programs/LibreOffice*/program/soffice.exe"))
            except OSError:
                pass
        raw_candidates.extend(_registry_soffice_candidates())
    else:
        raw_candidates.extend(("/usr/bin/soffice", "/usr/bin/libreoffice", "/opt/libreoffice/program/soffice"))

    # También admite una copia portable colocada junto al servidor.
    project_root = Path(__file__).resolve().parents[2]
    runtime_roots = [project_root, project_root.parent, Path.cwd(), Path(sys.executable).resolve().parent]
    for base in runtime_roots:
        raw_candidates.extend(
            [
                base / "LibreOfficePortable" / "App" / "libreoffice" / "program" / "soffice.com",
                base / "LibreOfficePortable" / "App" / "libreoffice" / "program" / "soffice.exe",
                base / "LibreOffice" / "program" / "soffice.com",
                base / "LibreOffice" / "program" / "soffice.exe",
                base / "tools" / "LibreOffice" / "program" / "soffice.com",
                base / "tools" / "LibreOffice" / "program" / "soffice.exe",
            ]
        )

    seen: set[str] = set()
    for raw in raw_candidates:
        normalized = _normalize_soffice_candidate(raw)
        if normalized and normalized.lower() not in seen:
            seen.add(normalized.lower())
            return normalized
    return None


def _conversion_detail(value: Any, limit: int = 700) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _windows_hidden_process_options() -> dict[str, Any]:
    """Opciones para que un conversor nunca muestre ventanas en Windows."""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return {"startupinfo": startupinfo, "creationflags": flags}


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    """Finaliza también los hijos soffice.bin de una conversión bloqueada."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=15,
                check=False,
                **_windows_hidden_process_options(),
            )
            return
        except Exception:
            pass
    try:
        process.kill()
    except Exception:
        pass


def _run_libreoffice_conversion(
    docx_paths: list[Path],
    output_dir: Path,
    *,
    timeout: int,
) -> tuple[bool, str]:
    """Ejecuta LibreOffice con perfil, temporales y salida completamente aislados."""
    executable = _find_soffice()
    if not executable:
        return False, (
            "LibreOffice no fue localizado. Instale la aplicación de escritorio o configure "
            "LIBREOFFICE_PATH en el archivo .env con la ruta completa a soffice.com o soffice.exe."
        )
    if not docx_paths:
        return False, "No se recibieron documentos para convertir."

    output_dir.mkdir(parents=True, exist_ok=True)
    source_paths = [Path(item).resolve() for item in docx_paths]
    missing = [str(item) for item in source_paths if not item.is_file()]
    if missing:
        return False, "No se encontraron los documentos de origen: " + ", ".join(missing)

    with _LIBREOFFICE_CONVERSION_LOCK:
        # El perfil nunca se crea dentro de uploads ni junto al DOCX. Se usa el
        # temporal del sistema, que es escribible para la cuenta que ejecuta
        # FastAPI, y se elimina al terminar.
        with tempfile.TemporaryDirectory(prefix="rsu_lo_runtime_") as runtime_name:
            runtime = Path(runtime_name)
            profile_parent = runtime / "profiles"
            profile_parent.mkdir(parents=True, exist_ok=True)
            profile = profile_parent / f"user_{uuid4().hex}"
            staging = runtime / "output"
            staging.mkdir(parents=True, exist_ok=True)
            process_temp = runtime / "temp"
            process_temp.mkdir(parents=True, exist_ok=True)

            # -env debe ir inmediatamente después del ejecutable para que el
            # bootstrap de LibreOffice no intente abrir el perfil real del usuario.
            command = [
                executable,
                f"-env:UserInstallation={profile.resolve().as_uri()}",
                "--headless",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                "--norestore",
                "--nofirststartwizard",
                "--invisible",
                "--convert-to",
                "pdf:writer_pdf_Export",
                "--outdir",
                str(staging.resolve()),
                *[str(item) for item in source_paths],
            ]
            env = os.environ.copy()
            env.update(
                {
                    "SAL_DISABLE_OPENCL": "1",
                    "SAL_USE_VCLPLUGIN": "svp",
                    "SAL_DISABLE_CRASHREPORT": "1",
                    "TMP": str(process_temp.resolve()),
                    "TEMP": str(process_temp.resolve()),
                    "TMPDIR": str(process_temp.resolve()),
                }
            )
            process: subprocess.Popen[str] | None = None
            try:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=str(runtime.resolve()),
                    env=env,
                    **_windows_hidden_process_options(),
                )
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                if process is not None:
                    _terminate_process_tree(process)
                    try:
                        stdout, stderr = process.communicate(timeout=10)
                    except Exception:
                        stdout, stderr = "", ""
                return False, f"LibreOffice excedió el tiempo máximo de {timeout} segundos."
            except OSError as exc:
                if process is not None:
                    _terminate_process_tree(process)
                return False, f"No se pudo iniciar LibreOffice: {_conversion_detail(exc)}"

            return_code = process.returncode if process is not None else -1
            produced_files: list[Path] = []
            for source in source_paths:
                produced = staging / f"{source.stem}.pdf"
                if produced.is_file() and produced.stat().st_size > 0:
                    destination = output_dir / produced.name
                    destination.unlink(missing_ok=True)
                    shutil.move(str(produced), str(destination))
                    produced_files.append(destination)

            if return_code == 0 and len(produced_files) == len(source_paths):
                return True, f"LibreOffice CLI: {executable}"

            detail = _conversion_detail(stderr or stdout or "sin detalle")
            return False, (
                f"LibreOffice terminó con código {return_code} y convirtió "
                f"{len(produced_files)} de {len(source_paths)} documentos. {detail}"
            )


def _convert_many_with_libreoffice(
    docx_paths: list[Path],
    output_dir: Path,
    *,
    timeout: int | None = None,
) -> tuple[bool, str]:
    """Conversión por lote reutilizable por certificados y anexos."""
    if not docx_paths:
        return False, "No se recibieron documentos para convertir."
    max_seconds = timeout or max(120, 25 * len(docx_paths))
    details: list[str] = []
    for attempt in range(1, 3):
        ok, detail = _run_libreoffice_conversion(docx_paths, output_dir, timeout=max_seconds)
        if ok:
            return True, f"{detail} (intento {attempt})"
        details.append(f"Intento {attempt}: {detail}")
        time.sleep(0.4 * attempt)
    return False, " | ".join(details)


def _convert_with_libreoffice(docx_path: Path, output_path: Path) -> tuple[bool, str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="rsu_lo_single_") as temp_name:
        temp_output = Path(temp_name) / "converted"
        ok, detail = _convert_many_with_libreoffice([docx_path], temp_output, timeout=150)
        produced = temp_output / f"{docx_path.stem}.pdf"
        if ok and produced.is_file() and produced.stat().st_size > 0:
            shutil.move(str(produced), str(output_path))
            return True, detail
    return False, detail


def _convert_with_word(docx_path: Path, output_path: Path) -> tuple[bool, str]:
    if os.name != "nt":
        return False, "Microsoft Word solo está disponible como conversor en Windows."

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    errors: list[str] = []
    word = None
    document = None
    pythoncom = None
    com_initialized = False
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
            # msoAutomationSecurityForceDisable. Impide que una plantilla con
            # macros detenga la emisión mediante un cuadro de diálogo.
            word.AutomationSecurity = 3
        except Exception:
            pass
        document = word.Documents.Open(
            str(docx_path.resolve()),
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
            OpenAndRepair=True,
            NoEncodingDialog=True,
        )
        document.ExportAsFixedFormat(str(output_path.resolve()), 17)
        document.Close(False)
        document = None
        word.Quit()
        word = None
        if output_path.exists() and output_path.stat().st_size > 0:
            return True, "Microsoft Word (COM)"
    except Exception as exc:
        errors.append(f"Word COM: {_conversion_detail(exc)}")
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

    try:
        from docx2pdf import convert  # type: ignore

        convert(str(docx_path.resolve()), str(output_path.resolve()))
        if output_path.exists() and output_path.stat().st_size > 0:
            return True, "Microsoft Word (docx2pdf)"
    except Exception as exc:
        errors.append(f"docx2pdf: {_conversion_detail(exc)}")

    detail = "; ".join(errors) or "Word no está instalado o no puede iniciarse."
    return False, f"Microsoft Word no pudo convertir el documento. {detail}"


def _pdf_converter_order() -> tuple[str, ...]:
    """Orden de conversores; en Windows se evita LibreOffice si Word funciona."""
    configured = ""
    try:
        from app.core.config import get_settings

        configured = str(get_settings().pdf_converter_order or "").strip()
    except Exception:
        configured = str(os.environ.get("PDF_CONVERTER_ORDER") or "").strip()
    if configured:
        values = tuple(
            item.strip().lower()
            for item in re.split(r"[,;]", configured)
            if item.strip().lower() in {"word", "libreoffice"}
        )
        if values:
            return values
    return ("word", "libreoffice") if os.name == "nt" else ("libreoffice",)


def _convert_document_to_pdf(docx_path: Path, output_path: Path) -> tuple[bool, str]:
    """Convierte usando el orden seguro definido para la plataforma."""
    details: list[str] = []
    for converter in _pdf_converter_order():
        if converter == "word":
            ok, detail = _convert_with_word(docx_path, output_path)
        else:
            ok, detail = _convert_with_libreoffice(docx_path, output_path)
        if ok:
            return True, detail
        details.append(detail)
    return False, " | ".join(details)


def pdf_converter_diagnostics() -> dict[str, Any]:
    """Información reutilizable por el script de diagnóstico y futuras pantallas."""
    executable = _find_soffice()
    return {
        "platform": sys.platform,
        "configured_path": _configured_libreoffice_path(),
        "libreoffice_executable": executable or "",
        "libreoffice_available": bool(executable),
        "word_conversion_supported": os.name == "nt",
        "converter_order": list(_pdf_converter_order()),
    }


def generate_official_annex_pdf(output_path: Path, project: dict[str, Any], annex_number: int) -> bool:
    """Rellena el DOCX oficial y lo convierte a PDF sin alterar su estructura."""
    template_name = TEMPLATE_NAMES.get(annex_number)
    filler = FILLERS.get(annex_number)
    if not template_name or not filler:
        raise RuntimeError(f"El Anexo {annex_number} no tiene una plantilla o rellenador configurado.")

    template = official_annex_template_path(annex_number)
    if template is None or not template.exists():
        expected = str(template or template_name)
        raise RuntimeError(f"No se encontró la plantilla DOCX oficial: {expected}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"rsu_anexo_{annex_number}_") as temp_name:
        temp_dir = Path(temp_name)
        docx_path = temp_dir / f"Anexo_{annex_number}_rellenado.docx"
        document = Document(template)
        filler(document, project, temp_dir)
        document.save(docx_path)

        converted, conversion_detail = _convert_document_to_pdf(docx_path, output_path)
        if converted:
            _postprocess_official_pdf(output_path, project, annex_number)
            _trim_trailing_blank_pages(output_path)
            return True

    raise RuntimeError(
        "No se pudo convertir la plantilla oficial a PDF. "
        f"{conversion_detail} "
        "Las librerías de Python no reemplazan al programa conversor. En Windows instale "
        "LibreOffice de escritorio o Microsoft Word. Si LibreOffice ya está instalado, agregue "
        "en .env una línea como: LIBREOFFICE_PATH=C:/Program Files/LibreOffice/program/soffice.exe"
    )
