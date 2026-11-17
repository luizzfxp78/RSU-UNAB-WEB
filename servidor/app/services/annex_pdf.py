from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import re
import threading
from typing import Any, Iterable
from uuid import uuid4

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image as PdfImage,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.services.official_annex_pdf import (
    TEMPLATE_NAMES,
    generate_official_annex_pdf,
    official_annex_template_path,
)

PAGE_WIDTH, PAGE_HEIGHT = A4


_ANNEX_CACHE_VERSION = "v49-annex5-evidence-final-review-1"
_ANNEX_CACHE_LOCKS: dict[str, threading.Lock] = {}
_ANNEX_CACHE_LOCKS_GUARD = threading.Lock()


def _cache_lock(output_path: Path) -> threading.Lock:
    key = str(output_path.resolve())
    with _ANNEX_CACHE_LOCKS_GUARD:
        lock = _ANNEX_CACHE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _ANNEX_CACHE_LOCKS[key] = lock
        return lock


def _cleanup_old_server_versions(output_path: Path, keep: int = 6) -> None:
    """Conserva pocas versiones y nunca falla si Windows mantiene una abierta."""
    match = re.match(r"^(anexo_\d+)_", output_path.stem)
    if not match:
        return
    prefix = match.group(1)
    candidates: list[tuple[int, Path]] = []
    for item in output_path.parent.glob(f"{prefix}_*.pdf"):
        if item == output_path:
            continue
        try:
            candidates.append((item.stat().st_mtime_ns, item))
        except OSError:
            continue
    candidates.sort(reverse=True)
    for _, item in candidates[max(0, keep - 1):]:
        try:
            item.unlink(missing_ok=True)
            item.with_suffix(item.suffix + ".sha256").unlink(missing_ok=True)
        except OSError:
            # FileResponse o un antivirus puede mantener el PDF abierto.
            pass


def _file_signature(path_value: Any) -> dict[str, Any] | None:
    if not path_value:
        return None
    try:
        path = Path(str(path_value))
        stat = path.stat()
    except (OSError, ValueError):
        return {"path": str(path_value), "missing": True}
    return {
        "path": str(path.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def annex_pdf_fingerprint(project: dict[str, Any], annex_number: int) -> str:
    """Huella estable con solo los datos que intervienen en cada anexo.

    Una evaluación ya no invalida innecesariamente los PDF de los Anexos 1,
    2 y 3. Estos solo se regeneran cuando cambia su contenido, equipo o firma.
    El Anexo 4 sí incorpora sus evaluaciones y autoridades firmantes.
    """
    annex_number = int(annex_number)
    template = official_annex_template_path(annex_number)
    annexes = project.get("anexos") or {}
    related_annexes = {
        1: {1},
        2: {2},
        3: {3},
        4: {1, 2, 3, 4},
        5: {5},
        6: {6},
        7: {6, 7},
    }.get(annex_number, {annex_number})
    selected_annexes = {
        str(number): annexes.get(str(number))
        for number in sorted(related_annexes)
        if str(number) in annexes
    }
    relevant_project = {
        key: project.get(key)
        for key in (
            "titulo",
            "linea_accion",
            "resumen",
            "poblacion_objetivo",
            "lugar_ejecucion",
            "fecha_inicio",
            "fecha_fin",
            "equipo",
        )
    }
    relevant_project["anexos"] = selected_annexes
    if annex_number in {4, 7}:
        relevant_project["evaluaciones"] = [
            item for item in project.get("evaluaciones") or []
            if int(item.get("anexo_numero") or 0) == annex_number
        ]
    if annex_number in {4, 7}:
        relevant_project["_autoridades_firma"] = project.get("_autoridades_firma")

    file_rows: list[dict[str, Any]] = []
    for item in project.get("_archivos_pdf") or []:
        if not isinstance(item, dict):
            continue
        item_annex = item.get("anexo_numero")
        try:
            item_annex_number = int(item_annex) if item_annex is not None else None
        except (TypeError, ValueError):
            item_annex_number = None
        if item_annex_number not in related_annexes:
            continue
        row = {
            key: item.get(key)
            for key in (
                "workflow_file_id",
                "anexo_numero",
                "proposito",
                "etiqueta",
                "persona_firmante_id",
                "nombre_archivo",
                "mime_type",
                "fecha_subida",
            )
        }
        row["archivo"] = _file_signature(item.get("archivo_url"))
        file_rows.append(row)
    payload = {
        "cache_version": _ANNEX_CACHE_VERSION,
        "annex_number": annex_number,
        "template_name": TEMPLATE_NAMES.get(annex_number),
        "template": _file_signature(template),
        "project": relevant_project,
        "files": file_rows,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def ensure_annex_pdf_cached(
    output_path: Path,
    project: dict[str, Any],
    annex_number: int,
    annex4_criteria: dict[str, str] | None = None,
    annex7_criteria: dict[str, str] | None = None,
) -> tuple[Path, str, bool]:
    """Devuelve el PDF vigente y solo lo regenera cuando cambian sus datos.

    Retorna ``(ruta, huella, regenerado)``. La generación se protege con un
    bloqueo por archivo para evitar abrir varias instancias de LibreOffice
    cuando llegan solicitudes simultáneas.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = annex_pdf_fingerprint(project, annex_number)
    fingerprint_path = output_path.with_suffix(output_path.suffix + ".sha256")

    def is_current() -> bool:
        if not output_path.exists() or output_path.stat().st_size <= 0:
            return False
        try:
            stored = fingerprint_path.read_text(encoding="utf-8").strip()
        except OSError:
            return False
        return stored == fingerprint

    if is_current():
        return output_path, fingerprint, False

    with _cache_lock(output_path):
        if is_current():
            return output_path, fingerprint, False
        temporary = output_path.with_name(
            f".{output_path.stem}.{uuid4().hex}.tmp{output_path.suffix}"
        )
        temporary_fingerprint = fingerprint_path.with_name(
            f".{fingerprint_path.name}.{uuid4().hex}.tmp"
        )
        try:
            generate_annex_pdf(
                temporary,
                project,
                annex_number,
                annex4_criteria,
                annex7_criteria,
            )
            if not temporary.exists() or temporary.stat().st_size <= 0:
                raise RuntimeError("El generador no produjo un PDF válido.")
            os.replace(temporary, output_path)
            temporary_fingerprint.write_text(fingerprint, encoding="utf-8")
            os.replace(temporary_fingerprint, fingerprint_path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            try:
                temporary_fingerprint.unlink(missing_ok=True)
            except OSError:
                pass
        _cleanup_old_server_versions(output_path)
        return output_path, fingerprint, True


def _safe(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _date(value: Any) -> str:
    if not value:
        return ""
    text = str(value)
    if len(text) >= 10 and text[4:5] == "-":
        return f"{text[8:10]}/{text[5:7]}/{text[0:4]}"
    return text


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "AnnexTitle", parent=base["Heading1"], fontName="Helvetica-Bold",
            fontSize=12, leading=14, alignment=TA_CENTER, spaceAfter=8,
        ),
        "subtitle": ParagraphStyle(
            "AnnexSubtitle", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=10, leading=12, alignment=TA_CENTER, spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "AnnexBody", parent=base["BodyText"], fontName="Helvetica",
            fontSize=8.3, leading=10.5, alignment=TA_LEFT,
        ),
        "small": ParagraphStyle(
            "AnnexSmall", parent=base["BodyText"], fontName="Helvetica",
            fontSize=7.2, leading=9,
        ),
        "label": ParagraphStyle(
            "AnnexLabel", parent=base["BodyText"], fontName="Helvetica-Bold",
            fontSize=8.2, leading=10,
        ),
        "center": ParagraphStyle(
            "AnnexCenter", parent=base["BodyText"], fontName="Helvetica",
            fontSize=8.2, leading=10, alignment=TA_CENTER,
        ),
    }


def _p(text: Any, style) -> Paragraph:
    # ReportLab interpreta un subconjunto de HTML. Protegemos caracteres
    # ingresados por el usuario sin eliminar las etiquetas simples que usa la
    # plantilla (b, i, u y br).
    value = str(text or "").replace("\n", "<br/>")
    value = re.sub(r"&(?!#\d+;|#x[0-9A-Fa-f]+;|[A-Za-z][A-Za-z0-9]+;)", "&amp;", value)
    value = re.sub(r"<(?!/?(?:b|i|u|br)(?:\s*/?)?>)", "&lt;", value)
    return Paragraph(value, style)


def _header_footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica-Bold", 7)
    canvas.drawString(1.6 * cm, PAGE_HEIGHT - 1.1 * cm, "UNIVERSIDAD NACIONAL DE BARRANCA")
    canvas.setFont("Helvetica", 7)
    canvas.drawCentredString(PAGE_WIDTH / 2, PAGE_HEIGHT - 1.1 * cm, "DIRECCION DE RESPONSABILIDAD SOCIAL UNIVERSITARIA")
    canvas.drawRightString(PAGE_WIDTH - 1.6 * cm, PAGE_HEIGHT - 1.1 * cm, str(doc.page))
    canvas.setStrokeColor(colors.HexColor("#333333"))
    canvas.line(1.6 * cm, 1.35 * cm, PAGE_WIDTH - 1.6 * cm, 1.35 * cm)
    canvas.setFont("Helvetica", 6.8)
    canvas.drawString(1.6 * cm, 0.95 * cm, "Av. Toribio de Luzuriaga 376, Mz - J Urb. La Florida - Barranca")
    canvas.drawRightString(PAGE_WIDTH - 1.6 * cm, 0.95 * cm, "responsabilidad.social@unb.edu.pe")
    canvas.restoreState()


def _table(data: list[list[Any]], widths: list[float] | None = None, *, header_rows: int = 0, paddings: int = 5) -> Table:
    table = Table(data, colWidths=widths, repeatRows=header_rows)
    commands: list[tuple] = [
        ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#666666")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), paddings),
        ("RIGHTPADDING", (0, 0), (-1, -1), paddings),
        ("TOPPADDING", (0, 0), (-1, -1), paddings),
        ("BOTTOMPADDING", (0, 0), (-1, -1), paddings),
    ]
    if header_rows:
        commands.extend([
            ("BACKGROUND", (0, 0), (-1, header_rows - 1), colors.HexColor("#E7E7E7")),
            ("FONTNAME", (0, 0), (-1, header_rows - 1), "Helvetica-Bold"),
            ("ALIGN", (0, 0), (-1, header_rows - 1), "CENTER"),
        ])
    table.setStyle(TableStyle(commands))
    return table


def _label_value(label: str, value: Any, styles, *, label_width: float = 6.0 * cm) -> Table:
    return _table([[ _p(label, styles["label"]), _p(value or " ", styles["body"]) ]], [label_width, PAGE_WIDTH - 3.2 * cm - label_width])


def _section(title: str, instruction: str, value: Any, styles) -> Table:
    body = [
        [_p(title, styles["label"])],
        [_p(instruction, styles["small"])],
        [_p(value or " ", styles["body"])],
    ]
    table = _table(body, [PAGE_WIDTH - 3.2 * cm], paddings=6)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 1), colors.HexColor("#E3E3E3")),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("MINROWHEIGHT", (0, 2), (-1, 2), 1.1 * cm),
    ]))
    return table


def _signature_table(names: Iterable[tuple[str, str]], styles) -> Table:
    cells = []
    for name, role in names:
        cells.append(_p(f"____________________________<br/>{_safe(name) or 'Nombres y apellidos'}<br/><b>{_safe(role)}</b>", styles["center"]))
    if not cells:
        return _table([[""]], [PAGE_WIDTH - 3.2 * cm])
    return Table([cells], colWidths=[(PAGE_WIDTH - 3.2 * cm) / len(cells)] * len(cells), style=[("VALIGN", (0, 0), (-1, -1), "TOP")])




def _workflow_signature_files(project: dict[str, Any], annex_number: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
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
        if "firma" not in str(item.get("proposito") or "").lower() and "firma" not in str(item.get("etiqueta") or "").lower():
            continue
        result.append(dict(item))
    return result


def _signature_image(path: str | Path, max_width: float = 4.0 * cm, max_height: float = 1.45 * cm):
    try:
        image = PdfImage(str(path))
        width = float(image.imageWidth or 1)
        height = float(image.imageHeight or 1)
        scale = min(max_width / width, max_height / height, 1.0)
        image.drawWidth = max(1.0, width * scale)
        image.drawHeight = max(1.0, height * scale)
        return image
    except Exception:
        return None


def _signed_signature_table(
    project: dict[str, Any],
    annex_number: int,
    signers: Iterable[dict[str, Any]],
    styles,
) -> Table:
    available = _workflow_signature_files(project, annex_number)
    used: set[str] = set()
    cells: list[Any] = []
    for index, signer in enumerate(signers):
        person_id = str(signer.get("person_id") or "")
        keywords = [str(value).lower() for value in signer.get("keywords") or [] if str(value).strip()]
        chosen: dict[str, Any] | None = None
        if person_id:
            chosen = next(
                (
                    item for item in available
                    if str(item.get("workflow_file_id") or "") not in used
                    and str(item.get("persona_firmante_id") or "") == person_id
                ),
                None,
            )
        if chosen is None and keywords:
            chosen = next(
                (
                    item for item in available
                    if str(item.get("workflow_file_id") or "") not in used
                    and any(
                        word in f"{item.get('etiqueta') or ''} {item.get('proposito') or ''}".lower()
                        for word in keywords
                    )
                ),
                None,
            )
        if chosen is None:
            chosen = next(
                (item for item in available if str(item.get("workflow_file_id") or "") not in used),
                None,
            )
        if chosen:
            used.add(str(chosen.get("workflow_file_id") or ""))
        flowables: list[Any] = []
        if chosen:
            image = _signature_image(str(chosen.get("archivo_url") or ""))
            if image is not None:
                flowables.extend([image, Spacer(1, 2)])
        name = str(signer.get("name") or "Nombres y apellidos")
        role = str(signer.get("role") or "")
        flowables.append(_p(f"____________________________<br/>{_safe(name)}<br/><b>{_safe(role)}</b>", styles["center"]))
        cells.append(flowables)
    if not cells:
        return _table([[""]], [PAGE_WIDTH - 3.2 * cm])
    return Table(
        [cells],
        colWidths=[(PAGE_WIDTH - 3.2 * cm) / len(cells)] * len(cells),
        style=[("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("ALIGN", (0, 0), (-1, -1), "CENTER")],
    )

def _project_annex(project: dict[str, Any], number: int) -> dict[str, Any]:
    return dict((((project.get("anexos") or {}).get(str(number)) or {}).get("contenido") or {}))


def _latest_evaluation(project: dict[str, Any], annex_number: int) -> dict[str, Any]:
    return next((item for item in project.get("evaluaciones") or [] if int(item.get("anexo_numero") or 0) == annex_number), {})


def _team(project: dict[str, Any]) -> list[dict[str, Any]]:
    return list(project.get("equipo") or [])


def _responsible(project: dict[str, Any]) -> dict[str, Any]:
    return next((x for x in _team(project) if str(x.get("rol") or "").lower() == "responsable"), {})


def _weekly_activities(value: Any) -> dict[int, str]:
    """Normaliza cronogramas guardados como lista o diccionario."""
    result: dict[int, str] = {}
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            try:
                week = int(item.get("semana") or item.get("week") or 0)
            except (TypeError, ValueError):
                continue
            if week:
                result[week] = str(item.get("actividad") or item.get("detalle") or "")
    elif isinstance(value, dict):
        for raw_key, raw_value in value.items():
            key = str(raw_key).lower().replace("semana_", "").replace("semana ", "")
            try:
                week = int(key)
            except (TypeError, ValueError):
                continue
            if isinstance(raw_value, dict):
                raw_value = raw_value.get("actividad") or raw_value.get("detalle") or ""
            result[week] = str(raw_value or "")
    return result


def _annex1_story(project: dict[str, Any], styles) -> list[Any]:
    data = _project_annex(project, 1)
    responsible = _responsible(project)
    line = project.get("linea_accion") or data.get("linea_accion") or ""
    story: list[Any] = [
        _p("ANEXO N. 1", styles["title"]),
        _p("MODELO DE SOLICITUD PARA PARTICIPAR EN LAS ACTIVIDADES DE RSU EN LA UNAB", styles["subtitle"]),
        Spacer(1, 8),
        _p("<b>SOLICITO:</b> Aprobacion de proyecto de RSU o Plan de actividades especificas y de proyeccion social o plan de actividades de voluntariado de RSU.", styles["body"]),
        Spacer(1, 12),
        _p("<b>SEÑOR:</b><br/><b>DIRECTOR DE RESPONSABILIDAD SOCIAL</b>", styles["body"]),
        Spacer(1, 14),
        _p(
            f"Yo, <b>{_safe(data.get('responsable') or responsible.get('nombre_completo'))}</b>, identificado con N. DNI "
            f"<b>{_safe(data.get('dni') or responsible.get('dni'))}</b>, domiciliado en "
            f"<b>{_safe(data.get('domicilio'))}</b>, en mi condicion de "
            f"<b>{_safe(data.get('condicion'))}</b>, ante usted con el debido respeto me presento y expongo:",
            styles["body"],
        ),
        Spacer(1, 10),
        _p(
            f"Que, teniendo el interes de participar en las actividades de RSU en el semestre academico, presento el "
            f"<b>{_safe(data.get('titulo') or project.get('titulo'))}</b>, en el marco de la Linea de Accion de RSU UNAB: "
            f"<b>{_safe(line)}</b>, para su aprobacion segun corresponda.", styles["body"],
        ),
        Spacer(1, 12),
        _p("Por lo expuesto, agradezco su atencion a la presente.", styles["body"]),
        Spacer(1, 60),
        _signed_signature_table(
            project,
            1,
            [{
                "name": data.get("responsable") or responsible.get("nombre_completo") or "",
                "role": "FIRMA - NOMBRES Y APELLIDOS",
                "person_id": responsible.get("persona_id"),
                "keywords": ["representante"],
            }],
            styles,
        ),
    ]
    return story


def _annex2_story(project: dict[str, Any], styles) -> list[Any]:
    data = _project_annex(project, 2)
    team = _team(project)
    participants = ", ".join(str(x.get("nombre_completo") or "") for x in team)
    story: list[Any] = [
        _p("ANEXO N. 2", styles["title"]),
        _p("FORMATO DE PROYECTOS DE RSU", styles["subtitle"]),
        _label_value("TITULO DEL PROYECTO", project.get("titulo") or data.get("titulo"), styles),
        _label_value("LINEA DE ACCION DE RSU", project.get("linea_accion") or data.get("linea_accion"), styles),
        _label_value("RESPONSABLE Y PARTICIPANTES DEL PROYECTO", participants, styles),
        _section("RESUMEN DEL PROYECTO", "Resumir en que consistira el desarrollo del proyecto, considerando los objetivos, metodologia o estrategia (instrumentos, muestra o poblacion) y resultados esperados.", data.get("resumen") or project.get("resumen"), styles),
        _section("PROBLEMATICA A ABORDAR", "Breve descripcion de la problematica en relacion a la linea de accion a abordar.", data.get("problematica"), styles),
        _section("OBJETIVO", "Listar y describir los objetivos que se esperan alcanzar al termino del desarrollo del proyecto.", data.get("objetivos") or data.get("objetivo"), styles),
        _section("ESTRATEGIAS O METODOLOGIA DEL PROYECTO", "Describir brevemente las estrategias o metodologia que empleara el equipo para conseguir el logro de los objetivos planteados.", data.get("metodologia"), styles),
        _section("IMPACTO SOCIAL", "Explicar de que manera los resultados y productos presentados en el proyecto responden a las demandas reales de la comunidad. Dar cuenta de pertinencia social, coherencia, claridad y factibilidad de la iniciativa.", data.get("impacto_social"), styles),
        PageBreak(),
        _section("IMPACTO EN LOS ESTUDIANTES COLABORADORES", "Describir de que manera el proyecto de RSU contribuye a la formacion de los estudiantes y colaboradores.", data.get("impacto_estudiantes"), styles),
        _section("MUESTRA, POBLACION Y/O ENTORNO OBJETIVO", "Describir la poblacion y muestra o entorno en el que se desarrollara el proyecto: cantidad, ubicacion y/o caracteristicas.", data.get("poblacion_objetivo") or project.get("poblacion_objetivo"), styles),
    ]
    schedule_by_week = {int(item.get("semana") or 0): item.get("actividad") for item in data.get("cronograma") or [] if isinstance(item, dict)}
    rows = [[_p("CRONOGRAMA DE ACTIVIDADES DEL PROYECTO", styles["label"]), _p("ACTIVIDAD", styles["label"]), _p("SEMANA", styles["label"]), _p("ACTIVIDAD", styles["label"])]]
    for row in range(1, 9):
        rows.append([
            _p(f"Semana {row}", styles["body"]), _p(schedule_by_week.get(row) or " ", styles["body"]),
            _p(f"Semana {row + 8}", styles["body"]), _p(schedule_by_week.get(row + 8) or " ", styles["body"]),
        ])
    schedule = _table(rows, [2.3 * cm, 6.2 * cm, 2.3 * cm, 6.2 * cm], header_rows=1, paddings=4)
    story.extend([schedule, Spacer(1, 8)])
    budget_rows = [[_p("Bien y/o servicio", styles["label"]), _p("Unidad de medida", styles["label"]), _p("Cantidad", styles["label"]), _p("Costo unitario (S/)", styles["label"]), _p("Costo total (S/)", styles["label"])]]
    for item in data.get("presupuesto") or []:
        total = float(item.get("cantidad") or 0) * float(item.get("costo_unitario") or 0)
        budget_rows.append([
            _p(item.get("bien_servicio"), styles["body"]), _p(item.get("unidad_medida"), styles["body"]),
            _p(item.get("cantidad"), styles["center"]), _p(f"{float(item.get('costo_unitario') or 0):.2f}", styles["center"]),
            _p(f"{total:.2f}", styles["center"]),
        ])
    if len(budget_rows) == 1:
        budget_rows.append([_p("Proyecto no financiado", styles["body"]), "", "", "", ""])
    story.extend([
        _p("PRESUPUESTO", styles["subtitle"]),
        _p("Indicar los bienes y/o servicios que se requieran para el desarrollo del proyecto. El presupuesto debera ajustarse al monto de financiamiento establecido en las disposiciones especificas de cada convocatoria.", styles["small"]),
        _table(budget_rows, [5.5 * cm, 3.2 * cm, 2.1 * cm, 3.0 * cm, 3.0 * cm], header_rows=1, paddings=4),
        Spacer(1, 36),
    ])
    signers: list[dict[str, Any]] = []
    selected_people: set[str] = set()
    for role in ("responsable", "corresponsable", "corresponsable"):
        match = next(
            (
                item for item in team
                if str(item.get("rol") or "").lower() == role
                and str(item.get("persona_id") or "") not in selected_people
            ),
            None,
        )
        if match:
            selected_people.add(str(match.get("persona_id") or ""))
            signers.append({
                "name": str(match.get("nombre_completo") or ""),
                "role": f"{role.upper()} DEL PROYECTO DE RSU",
                "person_id": match.get("persona_id"),
                "keywords": [role, "representante" if role == "responsable" else role],
            })
    story.append(_signed_signature_table(project, 2, signers, styles))
    return story


def _annex3_story(project: dict[str, Any], styles) -> list[Any]:
    rows = [[
        _p("APELLIDOS Y NOMBRES", styles["label"]), _p("DNI / CODIGO", styles["label"]),
        _p("ESCUELA PROFESIONAL", styles["label"]), _p("CORREO ELECTRONICO", styles["label"]),
        _p("N. CELULAR", styles["label"]), _p("PARTICIPACION", styles["label"]),
    ]]
    for item in _team(project):
        rows.append([
            _p(item.get("nombre_completo"), styles["body"]), _p(item.get("codigo") or item.get("dni"), styles["body"]),
            _p(item.get("escuela"), styles["body"]), _p(item.get("correo"), styles["body"]),
            _p(item.get("telefono"), styles["body"]), _p(item.get("rol"), styles["body"]),
        ])
    return [
        _p("ANEXO N. 3", styles["title"]), _p("REGISTRO DEL PROYECTO DE RESPONSABILIDAD SOCIAL UNIVERSITARIA", styles["subtitle"]),
        _label_value("TITULO", project.get("titulo"), styles),
        _label_value("LINEA DE ACCION DE RSU", project.get("linea_accion"), styles),
        Spacer(1, 8),
        _p("DATOS DEL EQUIPO DEL PROYECTO DE RSU", styles["subtitle"]),
        _table(rows, [4.2 * cm, 2.2 * cm, 3.2 * cm, 3.5 * cm, 2.0 * cm, 2.5 * cm], header_rows=1, paddings=3),
        Spacer(1, 8), _p("* DNI en caso de docentes.", styles["small"]),
        Spacer(1, 24),
        _signed_signature_table(
            project,
            3,
            [{"name": "Dirección de RSU", "role": "VISTO BUENO / REGISTRO DEL ANEXO 3", "keywords": ["registro", "administrador"]}],
            styles,
        ),
    ]


def _evaluation_story(project: dict[str, Any], annex_number: int, styles, criteria: dict[str, str]) -> list[Any]:
    evaluation = _latest_evaluation(project, annex_number)
    answers = evaluation.get("criterios") or _project_annex(project, annex_number).get("criterios") or {}
    per_criterion = evaluation.get("observaciones_por_criterio") or _project_annex(project, annex_number).get("observaciones_por_criterio") or {}
    responsible = _responsible(project)
    if annex_number == 4:
        title = "FICHA DE EVALUACION DEL PROYECTO DE RSU"
        intro_rows = [
            [ _p("1. TITULO DEL PROYECTO", styles["label"]), _p(project.get("titulo"), styles["body"]) ],
            [ _p("2. RESPONSABLE", styles["label"]), _p(f"{responsible.get('nombre_completo','')} - DNI {responsible.get('dni','')}", styles["body"]) ],
            [ _p("LINEA DE ACCION DE RSU", styles["label"]), _p(project.get("linea_accion"), styles["body"]) ],
        ]
    else:
        title = "FICHA DE EVALUACION DE INFORME FINAL DEL PROYECTO DE RSU"
        intro_rows = [
            [ _p("1. NOMBRE DEL PROYECTO", styles["label"]), _p(project.get("titulo"), styles["body"]) ],
            [ _p("2. PROCEDENCIA DEL PROYECTO", styles["label"]), _p(f"Facultad/Escuela: {responsible.get('escuela','')}", styles["body"]) ],
            [ _p("3. RESPONSABLE DEL PROYECTO", styles["label"]), _p(f"{responsible.get('nombre_completo','')} - DNI {responsible.get('dni','')} - {responsible.get('correo','')}", styles["body"]) ],
            [ _p("4. LINEA DE ACCION DE RSU", styles["label"]), _p(project.get("linea_accion"), styles["body"]) ],
            [ _p("5. LUGAR DONDE REALIZO EL PROYECTO", styles["label"]), _p(project.get("lugar_ejecucion"), styles["body"]) ],
            [ _p("6-7. FECHAS", styles["label"]), _p(f"Inicio: {_date(project.get('fecha_inicio'))} / Termino: {_date(project.get('fecha_fin'))}", styles["body"]) ],
        ]
    rows = [[_p("CRITERIO DE EVALUACION", styles["label"]), _p("SI", styles["label"]), _p("NO", styles["label"]), _p("OBSERVACION", styles["label"])]]
    for key, description in criteria.items():
        answer = answers.get(key)
        rows.append([
            _p(description, styles["body"]), _p("X" if answer is True else "", styles["center"]),
            _p("X" if answer is False else "", styles["center"]), _p(per_criterion.get(key) or "", styles["small"]),
        ])
    total_yes = sum(1 for key in criteria if answers.get(key) is True)
    total_no = sum(1 for key in criteria if answers.get(key) is False)
    story: list[Any] = [
        _p(f"ANEXO N. {annex_number}", styles["title"]), _p(title, styles["subtitle"]),
        _table(intro_rows, [6.2 * cm, 10.6 * cm], paddings=4), Spacer(1, 8),
        _p("CRITERIOS DE EVALUACION", styles["subtitle"]),
        _table(rows, [10.5 * cm, 1.2 * cm, 1.2 * cm, 4.0 * cm], header_rows=1, paddings=4),
        Spacer(1, 8),
        _table([[ _p("TOTAL SI", styles["label"]), _p(total_yes, styles["center"]), _p("TOTAL NO", styles["label"]), _p(total_no, styles["center"]) ]], [4.0*cm, 2.0*cm, 4.0*cm, 2.0*cm]),
        Spacer(1, 8),
        _section("OBSERVACIONES", "De haber observaciones, el responsable debe absolverlas en un plazo maximo de tres (03) dias habiles; de lo contrario, el documento se tendra por no presentado.", evaluation.get("observaciones") or _project_annex(project, annex_number).get("observaciones"), styles),
        Spacer(1, 30),
    ]
    evaluator = evaluation.get("evaluador") or "Evaluador RSU"
    if annex_number == 4:
        signers = [
            {"name": evaluator, "role": "EVALUADOR RESPONSABLE", "keywords": ["director/evaluador", "evaluador responsable"]},
            {"name": "Jurado evaluador 1", "role": "JURADO EVALUADOR", "keywords": ["jurado evaluador 1"]},
            {"name": "Jurado evaluador 2", "role": "JURADO EVALUADOR", "keywords": ["jurado evaluador 2"]},
        ]
    else:
        signers = [
            {"name": evaluator, "role": "DIRECTOR(A) / JURADO EVALUADOR", "keywords": ["director/evaluador", "director"]},
            {"name": "Jurado evaluador 1", "role": "JURADO EVALUADOR", "keywords": ["jurado evaluador 1"]},
            {"name": "Jurado evaluador 2", "role": "JURADO EVALUADOR", "keywords": ["jurado evaluador 2"]},
            {"name": "Jurado evaluador 3", "role": "JURADO EVALUADOR", "keywords": ["jurado evaluador 3"]},
        ]
    story.append(_signed_signature_table(project, annex_number, signers, styles))
    return story


def _annex5_story(project: dict[str, Any], styles) -> list[Any]:
    data = _project_annex(project, 5)
    responsible = _responsible(project)
    rows = [
        ("NOMBRE DEL PROYECTO DE RSU", project.get("titulo")),
        ("LINEA DE ACCION DE RSU", project.get("linea_accion")),
        ("LUGAR DONDE REALIZA EL PROYECTO DE RSU", project.get("lugar_ejecucion")),
        ("RESPONSABLE DEL PROYECTO", responsible.get("nombre_completo")),
        ("FECHA DE INICIO", _date(project.get("fecha_inicio"))),
        ("DURACION EN HORAS", data.get("duracion_horas")),
    ]
    story = [_p("ANEXO N. 5", styles["title"]), _p("INFORME DE AVANCE DE PROYECTO DE RSU", styles["subtitle"])]
    for label, value in rows:
        story.append(_label_value(label, value, styles))
    activities = _weekly_activities(data.get("actividades_semanales"))
    activity_rows = [[_p("DESCRIPCION DE LAS ACTIVIDADES REALIZADAS POR CADA SEMANA", styles["label"]), _p("DETALLE", styles["label"])]]
    for week in range(1, 10):
        activity_rows.append([_p(f"SEMANA N. {week}", styles["body"]), _p(_safe(activities.get(week) or ""), styles["body"])])
    story.extend([
        _table(activity_rows, [5.3 * cm, 11.5 * cm], header_rows=1, paddings=4),
        _section("1. RESUMEN Y EVIDENCIA DE LAS LABORES", "Resuma y evidencie las labores realizadas hasta el presente informe.", data.get("resumen_labores"), styles),
        _section("2. DIFICULTADES ENCONTRADAS", "Mencione las dificultades encontradas hasta el presente informe.", data.get("dificultades"), styles),
        _section("3. OPORTUNIDADES Y/O LOGROS", "Mencione las oportunidades y/o logros alcanzados hasta el presente informe.", data.get("oportunidades_logros"), styles),
        Spacer(1, 20), _signed_signature_table(
            project,
            5,
            [{"name": responsible.get("nombre_completo") or "", "role": "RESPONSABLE DEL PROYECTO DE RSU", "person_id": responsible.get("persona_id"), "keywords": ["representante"]}],
            styles,
        ),
    ])
    return story


def _annex6_story(project: dict[str, Any], styles) -> list[Any]:
    data = _project_annex(project, 6)
    responsible = _responsible(project)
    rows = [
        ("NOMBRE DEL PROYECTO", project.get("titulo")), ("FACULTAD", data.get("facultad")),
        ("ESCUELA PROFESIONAL", data.get("escuela_profesional")),
        ("RESPONSABLE DEL INFORME DE PROYECTO DE RSU", responsible.get("nombre_completo")),
        ("LINEA DE ACCION DE RSU", project.get("linea_accion")),
        ("LUGAR DONDE REALIZO EL PROYECTO", project.get("lugar_ejecucion")),
        ("FECHA DE INICIO", _date(project.get("fecha_inicio"))), ("FECHA DE TERMINO", _date(project.get("fecha_fin"))),
        ("DURACION (HORAS TOTALES)", data.get("duracion_horas")),
    ]
    story = [_p("ANEXO N. 6", styles["title"]), _p("INFORME FINAL DE PROYECTO DE RSU", styles["subtitle"])]
    for label, value in rows:
        story.append(_label_value(label, value, styles))
    activities = _weekly_activities(data.get("actividades_semanales"))
    activity_rows = [[_p("DESCRIPCION DE LAS ACTIVIDADES REALIZADAS POR CADA SEMANA", styles["label"]), _p("DETALLE", styles["label"])]]
    for week in range(1, 17):
        activity_rows.append([_p(f"SEMANA N. {week}", styles["body"]), _p(_safe(activities.get(week) or ""), styles["body"])])
    story.extend([
        _table(activity_rows, [5.3 * cm, 11.5 * cm], header_rows=1, paddings=3),
        _section("1. RESUMEN Y EVIDENCIA DE LAS LABORES", "Resuma y evidencie las labores realizadas.", data.get("resumen_labores"), styles),
        _section("2. DIFICULTADES ENCONTRADAS", "Mencione las dificultades encontradas.", data.get("dificultades"), styles),
        _section("3. RESULTADOS Y/O LOGROS OBTENIDOS", "Mencione los resultados y/o logros obtenidos.", data.get("resultados_logros"), styles),
        _section("4. RECOMENDACIONES Y/O SUGERENCIAS", "Registre recomendaciones y/o sugerencias.", data.get("recomendaciones"), styles),
        Spacer(1, 20), _signed_signature_table(
            project,
            6,
            [{"name": responsible.get("nombre_completo") or "", "role": "RESPONSABLE DEL PROYECTO DE RSU", "person_id": responsible.get("persona_id"), "keywords": ["representante"]}],
            styles,
        ),
    ])
    return story


def generate_annex_pdf(
    output_path: Path,
    project: dict[str, Any],
    annex_number: int,
    annex4_criteria: dict[str, str] | None = None,
    annex7_criteria: dict[str, str] | None = None,
) -> Path:
    """Genera el anexo rellenando exclusivamente la plantilla DOCX oficial.

    No se reconstruye el documento con tablas alternativas. Para conservar
    exactamente el diseño original, el servidor requiere LibreOffice o
    Microsoft Word para convertir la copia rellenada a PDF.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        generated = generate_official_annex_pdf(output_path, project, annex_number)
    except Exception as exc:
        # Conserva el diagnóstico real (ruta no encontrada, fallo de Word,
        # permisos, timeout, etc.) para que la interfaz muestre una solución útil.
        raise RuntimeError(str(exc)) from exc
    if not generated:
        raise RuntimeError(
            "El conversor no produjo un PDF. Configure LIBREOFFICE_PATH en .env "
            "o verifique la instalación de Microsoft Word."
        )
    return output_path
