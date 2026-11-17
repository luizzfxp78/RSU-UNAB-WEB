from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path
import tempfile
from typing import Any

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_ROW_HEIGHT_RULE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from app.services.official_annex_pdf import _convert_document_to_pdf


TEMPLATES = {
    1: "Actividad_Anexo_01.docx",
    8: "Actividad_Anexo_08.docx",
    9: "Actividad_Anexo_09.docx",
    10: "Actividad_Anexo_10.docx",
}


def template_path(number: int) -> Path:
    return Path(__file__).resolve().parents[2] / "official_annex_templates" / TEMPLATES[int(number)]


def _clear(paragraph) -> None:
    for child in list(paragraph._p):
        paragraph._p.remove(child)


def _set_paragraph(paragraph, text: Any, *, size: float = 9.5, bold: bool = False, center: bool = False) -> None:
    _clear(paragraph)
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER if center else WD_ALIGN_PARAGRAPH.JUSTIFY
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    run = paragraph.add_run(str(text or ""))
    run.font.name = "Arial"
    run.font.size = Pt(size)
    run.bold = bold


def _set_cell(cell, text: Any, *, size: float = 8.5, bold: bool = False, center: bool = False) -> None:
    cell.text = ""
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER if center else WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.keep_together = False
    p.paragraph_format.keep_with_next = False
    run = p.add_run(str(text or ""))
    run.font.name = "Arial"
    run.font.size = Pt(size)
    run.bold = bold


def _append_cell(cell, text: Any, *, size: float = 8.5, bold: bool = False) -> None:
    p = cell.paragraphs[-1]
    if cell.text.strip():
        p.add_run("\n")
    run = p.add_run(str(text or ""))
    run.font.name = "Arial"
    run.font.size = Pt(size)
    run.bold = bold


def _allow_growth(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    for tag in ("w:trHeight", "w:cantSplit"):
        for node in list(tr_pr.findall(qn(tag))):
            tr_pr.remove(node)
    row.height = None
    row.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST


def _clone_row_before(table, source_index: int, before_index: int):
    copied = deepcopy(table.rows[source_index]._tr)
    table.rows[before_index]._tr.addprevious(copied)
    return table.rows[before_index]


def _date_text(value: Any) -> str:
    if not value:
        return ""
    text = str(value)
    if len(text) >= 10 and text[4] == "-":
        return f"{text[8:10]}/{text[5:7]}/{text[:4]}"
    return text


def _team(activity: dict[str, Any]) -> list[dict[str, Any]]:
    return list(activity.get("equipo") or [])


def _responsible(activity: dict[str, Any]) -> dict[str, Any]:
    return next((p for p in _team(activity) if str(p.get("rol") or "").lower() == "responsable"), {})


def _signature_path(member: dict[str, Any]) -> Path | None:
    raw = str(member.get("firma_url") or member.get("firma_path") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.exists() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        return path
    return None


def _fill_signature_paragraph(paragraph, member: dict[str, Any], role_label: str) -> None:
    _clear(paragraph)
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    signature = _signature_path(member)
    if signature:
        paragraph.add_run().add_picture(str(signature), width=Cm(3.3))
        paragraph.add_run("\n")
    line = paragraph.add_run("_____________________________\n")
    line.font.size = Pt(8)
    name = paragraph.add_run(str(member.get("nombre_completo") or "Nombres y Apellidos") + "\n")
    name.font.name = "Arial"
    name.font.size = Pt(8)
    role = paragraph.add_run(role_label)
    role.font.name = "Arial"
    role.font.size = Pt(7.5)
    role.bold = True


def _fill_annex_1(document: Document, activity: dict[str, Any]) -> None:
    responsible = _responsible(activity)
    content = dict(activity.get("anexo_1") or {})
    name = str(responsible.get("nombre_completo") or "")
    dni = str(responsible.get("dni") or "")
    address = str(content.get("domicilio") or responsible.get("domicilio") or "")
    condition = str(content.get("condicion") or responsible.get("condicion") or activity.get("tipo_liderazgo") or "")
    line = str(activity.get("linea_accion") or "")
    title = str(activity.get("titulo") or "")
    if len(document.paragraphs) > 14:
        _set_paragraph(
            document.paragraphs[12],
            f"Yo, {name}, identificado(a) con N° DNI {dni}, domiciliado(a) en {address}, en mi condición de {condition}, ante usted con el debido respeto me presento y expongo:",
            size=10,
        )
        _set_paragraph(
            document.paragraphs[14],
            f"Que, teniendo el interés de participar en las actividades de RSU, presento el Plan de Actividades Específicas y de Proyección Social denominado «{title}», en el marco de la Línea de Acción de RSU UNAB: {line}, para su aprobación según corresponda.",
            size=10,
        )
    # El último bloque no vacío de la plantilla corresponde a la firma.
    candidates = [p for p in document.paragraphs if "Firma" in p.text or "Nombres y apellidos" in p.text]
    if candidates:
        _fill_signature_paragraph(candidates[-1], responsible, "RESPONSABLE DE LA ACTIVIDAD ESPECÍFICA")


def _fill_people_table(table, activity: dict[str, Any]) -> None:
    responsible = _responsible(activity)
    _set_cell(table.cell(6, 0), responsible.get("nombre_completo"), size=8.5)
    _set_cell(table.cell(6, 1), responsible.get("dni"), size=8.5, center=True)
    _set_cell(table.cell(6, 2), responsible.get("email") or responsible.get("email_institucional"), size=8.0)
    _set_cell(table.cell(6, 3), responsible.get("facultad") or responsible.get("escuela"), size=8.0)

    others = [item for item in _team(activity) if str(item.get("rol") or "").lower() != "responsable"]
    # Se conserva la fila original y se agregan copias antes del bloque de plazo.
    while len(table.rows) > 24:
        table._tbl.remove(table.rows[10]._tr)
    for idx in range(max(1, len(others))):
        if idx > 0:
            _clone_row_before(table, 9, 9 + idx)
        row = table.rows[9 + idx]
        _allow_growth(row)
        member = others[idx] if idx < len(others) else {}
        role = str(member.get("rol_label") or member.get("rol") or "").replace("_", " ").title()
        display_name = str(member.get("nombre_completo") or "")
        if role and display_name:
            display_name = f"{display_name} — {role}"
        _set_cell(row.cells[0], display_name, size=7.7)
        _set_cell(row.cells[1], member.get("dni") or member.get("codigo"), size=7.7, center=True)
        _set_cell(row.cells[2], member.get("email") or member.get("email_institucional"), size=7.3)
        _set_cell(row.cells[3], member.get("facultad") or member.get("escuela"), size=7.3)


def _fill_plan_table(document: Document, activity: dict[str, Any], *, final: bool) -> None:
    table = document.tables[0]
    _fill_people_table(table, activity)
    # La cantidad de colaboradores puede insertar filas; localizar los rótulos dinámicamente.
    def find_row(prefix: str) -> int:
        for i, row in enumerate(table.rows):
            if any(str(cell.text or "").strip().upper().startswith(prefix.upper()) for cell in row.cells):
                return i
        raise ValueError(f"No se encontró la fila {prefix}")

    title_row = find_row("TÍTULO DEL PLAN") + 1
    line_row = find_row("LINEA DE ACCIÓN RSU") + 1
    deadline_row = find_row("PLAZO DE EJECUCIÓN")
    location_title = find_row("LUGAR DE EJECUCIÓN")
    content_title = find_row("DESCRIPCIÓN DE LAS ACTIVIDADES") if final else find_row("CONTENIDO DE LA PROPUESTA")

    _set_cell(table.cell(title_row, 0), activity.get("titulo"), size=9.0, bold=True)
    _set_cell(table.cell(line_row, 0), activity.get("linea_accion"), size=8.8)
    _set_cell(table.cell(deadline_row + 1, 1), _date_text(activity.get("fecha_inicio")), size=8.5)
    _set_cell(table.cell(deadline_row + 2, 1), _date_text(activity.get("fecha_fin")), size=8.5)
    _set_cell(table.cell(deadline_row + 3, 1), activity.get("horas_totales"), size=8.5)

    content = dict(activity.get("anexo_10" if final else "anexo_8") or {})
    _set_cell(table.cell(location_title + 1, 0), content.get("lugar_entorno") or activity.get("lugar_ejecucion"), size=8.3)
    keys = (
        ["resumen", "problematica", "objetivo", "metodologia", "impacto_social", "evidencias", "financiamiento"]
        if final
        else ["resumen", "problematica", "objetivo", "metodologia", "impacto_social", "medio_difusion", "financiamiento"]
    )
    # Las filas de contenido comienzan después del título. En cada una se mantiene
    # el texto guía de la primera celda y se rellena exclusivamente la segunda.
    for offset, key in enumerate(keys, start=1):
        row_index = content_title + offset
        if row_index >= len(table.rows):
            break
        _allow_growth(table.rows[row_index])
        target = table.rows[row_index].cells[-1]
        _set_cell(target, content.get(key), size=8.1)
        if final and key == "evidencias":
            for evidence in activity.get("evidencias") or []:
                path = Path(str(evidence.get("archivo_url") or evidence.get("path") or ""))
                if path.exists() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                    p = target.add_paragraph()
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    p.add_run().add_picture(str(path), width=Cm(5.5))
                    caption = str(evidence.get("etiqueta") or evidence.get("nombre_archivo") or path.name)
                    p.add_run("\n" + caption).font.size = Pt(7)

    # Firmas del responsable y del equipo institucional. Se utiliza el
    # bloque de firmas existente y se incorporan las imágenes sin alterar las
    # tablas del Anexo 8.
    signature_members = [_responsible(activity)] + [
        item for item in _team(activity)
        if str(item.get("rol") or "").lower() in {"corresponsable", "docente_asesor"}
    ]
    signature_paragraphs = list(document.paragraphs[4:11]) if len(document.paragraphs) > 10 else []
    if signature_paragraphs:
        # Se reemplaza el bloque textual por una tabla sin bordes, manteniendo
        # el espacio final de la plantilla y distribuyendo dos firmas por fila.
        anchor = signature_paragraphs[0]
        signature_table = document.add_table(rows=max(1, (len(signature_members) + 1) // 2), cols=2)
        signature_table._tbl.getparent().remove(signature_table._tbl)
        anchor._p.addprevious(signature_table._tbl)
        for row_index, row in enumerate(signature_table.rows):
            _allow_growth(row)
            for col_index, cell in enumerate(row.cells):
                member_index = row_index * 2 + col_index
                member = signature_members[member_index] if member_index < len(signature_members) else {}
                role_value = str(member.get("rol") or "responsable").lower()
                role_label = (
                    "RESPONSABLE DE LA INICIATIVA DE RSU" if role_value == "responsable"
                    else "DOCENTE ASESOR DE LA INICIATIVA DE RSU" if role_value == "docente_asesor"
                    else f"CORRESPONSABLE N° {member_index} DE LA INICIATIVA DE RSU"
                )
                cell.text = ""
                _fill_signature_paragraph(cell.paragraphs[0], member, role_label)
        for paragraph in signature_paragraphs:
            _clear(paragraph)


def _fill_annex_9(document: Document, activity: dict[str, Any]) -> None:
    evaluation = dict(activity.get("evaluacion") or {})
    responsible = _responsible(activity)
    t0, t1, t2 = document.tables
    _set_cell(t0.cell(1, 0), activity.get("titulo"), size=8.8, bold=True)
    _set_cell(t0.cell(3, 0), activity.get("linea_accion"), size=8.5)
    _set_cell(t0.cell(6, 0), responsible.get("nombre_completo"), size=8.0)
    _set_cell(t0.cell(6, 1), responsible.get("dni"), size=8.0, center=True)
    _set_cell(t0.cell(6, 2), responsible.get("email") or responsible.get("email_institucional"), size=7.5)
    _set_cell(t0.cell(6, 3), responsible.get("facultad") or responsible.get("escuela"), size=7.5)

    criteria = list(evaluation.get("criterios") or [])
    answer_by_key = {str(item.get("clave") or item.get("key") or ""): bool(item.get("cumple")) for item in criteria}
    ordered = [
        "plazo_ejecucion", "lugar_ejecucion", "resumen", "problematica", "objetivo",
        "metodologia", "impacto_social", "medio_difusion", "financiamiento",
    ]
    positions = [
        (t0, 8, 4, 5), (t0, 10, 4, 5), (t0, 12, 4, 5),
        (t0, 14, 4, 5), (t0, 16, 4, 5), (t0, 18, 4, 5),
        (t0, 20, 4, 5), (t1, 1, 1, 2), (t1, 3, 1, 2),
    ]
    total_yes = 0
    for key, (table, row, yes_col, no_col) in zip(ordered, positions):
        yes = bool(answer_by_key.get(key))
        total_yes += int(yes)
        _set_cell(table.cell(row, yes_col), "X" if yes else "", size=10, bold=True, center=True)
        _set_cell(table.cell(row, no_col), "" if yes else "X", size=10, bold=True, center=True)
    total_no = len(ordered) - total_yes
    _set_cell(t1.cell(6, 1), total_yes, size=9, bold=True, center=True)
    _set_cell(t1.cell(6, 2), total_no, size=9, bold=True, center=True)
    result = "APROBADO" if total_no == 0 else "OBSERVADO"
    observations = str(evaluation.get("observaciones") or "")
    _set_cell(t1.cell(8, 0), f"{result}\n{observations}", size=8.5, bold=True, center=True)

    reviewers = list(evaluation.get("revisores") or [])
    for col in range(2):
        reviewer = reviewers[col] if col < len(reviewers) else {}
        cell = t2.cell(0, col)
        cell.text = ""
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        sig = _signature_path(reviewer)
        if sig:
            p.add_run().add_picture(str(sig), width=Cm(3.0))
            p.add_run("\n")
        p.add_run("____________________________\n")
        p.add_run(str(reviewer.get("nombre_completo") or "Comité evaluador RSU") + "\n")
        p.add_run(str(reviewer.get("cargo") or reviewer.get("rol_label") or "Comité evaluador RSU"))


def generate_activity_docx(number: int, output: Path, activity: dict[str, Any]) -> Path:
    template = template_path(number)
    if not template.exists():
        raise FileNotFoundError(f"No se encontró la plantilla oficial del Anexo {number}")
    document = Document(template)
    if int(number) == 1:
        _fill_annex_1(document, activity)
    elif int(number) == 8:
        _fill_plan_table(document, activity, final=False)
    elif int(number) == 9:
        _fill_annex_9(document, activity)
    elif int(number) == 10:
        _fill_plan_table(document, activity, final=True)
    else:
        raise ValueError(f"Anexo de actividad no soportado: {number}")
    output.parent.mkdir(parents=True, exist_ok=True)
    document.save(output)
    return output


def generate_activity_pdf(number: int, output: Path, activity: dict[str, Any]) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"rsu_act_anexo_{number}_") as temp_name:
        docx_path = Path(temp_name) / f"anexo_{int(number)}.docx"
        generate_activity_docx(number, docx_path, activity)
        converted, detail = _convert_document_to_pdf(docx_path, output)
        if not converted or not output.exists() or output.stat().st_size <= 0:
            raise RuntimeError(detail or f"No se pudo convertir el Anexo {number} a PDF")
    return output
