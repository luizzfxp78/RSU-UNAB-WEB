from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import re
from typing import Any, Sequence
from xml.etree import ElementTree
from xml.sax.saxutils import escape
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile


# XML 1.0 no permite estos caracteres. Pueden aparecer en observaciones o
# participantes copiados desde sistemas externos y Excel intenta "reparar" el
# libro cuando se incluyen dentro de sheet1.xml.
_INVALID_XML_CHARS = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\uD800-\uDFFF\uFFFE\uFFFF]"
)
_INVALID_SHEET_CHARS = re.compile(r"[\\/*?:\[\]]")
_MAX_EXCEL_CELL_TEXT = 32767


def _column_name(index: int) -> str:
    if index < 1:
        raise ValueError("El índice de columna debe empezar en 1.")
    result = ""
    value = index
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _clean_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = _INVALID_XML_CHARS.sub(" ", text)
    # Excel admite como máximo 32 767 caracteres por celda.
    return text[:_MAX_EXCEL_CELL_TEXT]


def _xml_text(value: Any) -> str:
    return escape(_clean_text(value))


def _safe_sheet_name(value: str) -> str:
    name = _INVALID_SHEET_CHARS.sub("-", _clean_text(value)).strip().strip("'")
    name = name[:31] or "Reporte RSU"
    # Se usa como atributo XML, por eso también deben escaparse las comillas.
    return escape(name, {'"': "&quot;"})


def _inline_cell(reference: str, value: Any, style: int = 4) -> str:
    return (
        f'<c r="{reference}" t="inlineStr" s="{style}">'
        f'<is><t xml:space="preserve">{_xml_text(value)}</t></is></c>'
    )


def _number_cell(reference: str, value: Any, style: int = 6) -> str:
    try:
        number = float(value if value not in (None, "") else 0)
    except (TypeError, ValueError, OverflowError):
        number = 0.0
    if number != number or number in (float("inf"), float("-inf")):
        number = 0.0
    rendered = str(int(number)) if number.is_integer() else format(number, ".15g")
    return f'<c r="{reference}" s="{style}" t="n"><v>{rendered}</v></c>'


def _validate_package(content: bytes) -> None:
    """Comprueba la integridad ZIP y que todas las partes XML sean legibles."""

    required = {
        "[Content_Types].xml",
        "_rels/.rels",
        "xl/workbook.xml",
        "xl/_rels/workbook.xml.rels",
        "xl/worksheets/sheet1.xml",
        "xl/styles.xml",
        "docProps/core.xml",
        "docProps/app.xml",
    }
    try:
        with ZipFile(BytesIO(content), "r") as archive:
            bad_member = archive.testzip()
            if bad_member:
                raise ValueError(f"Parte XLSX dañada: {bad_member}")
            missing = required.difference(archive.namelist())
            if missing:
                raise ValueError(f"Partes XLSX faltantes: {', '.join(sorted(missing))}")
            for member in required:
                if member.endswith(".xml") or member.endswith(".rels"):
                    ElementTree.fromstring(archive.read(member))
    except (BadZipFile, ElementTree.ParseError) as exc:
        raise ValueError("No se pudo construir un archivo XLSX válido.") from exc


def build_table_workbook(
    *,
    title: str,
    subtitle: str,
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    numeric_columns: set[int] | None = None,
    sheet_name: str = "Reporte RSU",
) -> bytes:
    """Genera un XLSX institucional compatible con Microsoft Excel.

    La numeración de ``numeric_columns`` empieza en cero. El libro incluye
    título, subtítulo, encabezado congelado, autofiltro, columnas dimensionadas,
    bordes, filas alternadas y texto multilínea.

    Importante: el orden de los nodos de ``worksheet`` sigue estrictamente el
    esquema SpreadsheetML. En la versión anterior ``autoFilter`` aparecía detrás
    de ``mergeCells``; Microsoft Excel lo consideraba contenido inválido y
    mostraba el cuadro de recuperación del libro.
    """

    if not headers:
        raise ValueError("El reporte debe contener al menos una columna.")

    numeric_columns = numeric_columns or set()
    column_widths = [7, 18, 38, 48, 34, 30, 18, 18, 42]
    max_col = len(headers)
    max_row = max(4, 4 + len(rows))
    end_col = _column_name(max_col)

    sheet_rows: list[str] = []
    sheet_rows.append(
        '<row r="1" ht="28" customHeight="1">'
        + _inline_cell("A1", title, 1)
        + "</row>"
    )
    sheet_rows.append(
        '<row r="2" ht="22" customHeight="1">'
        + _inline_cell("A2", subtitle, 2)
        + "</row>"
    )
    sheet_rows.append('<row r="3" ht="8" customHeight="1"></row>')
    header_cells = [
        _inline_cell(f"{_column_name(index + 1)}4", header, 3)
        for index, header in enumerate(headers)
    ]
    sheet_rows.append('<row r="4" ht="34" customHeight="1">' + "".join(header_cells) + "</row>")

    for row_index, row in enumerate(rows, start=5):
        style_text = 4 if row_index % 2 else 7
        cells: list[str] = []
        max_lines = 1
        for col_index in range(1, max_col + 1):
            value = row[col_index - 1] if col_index - 1 < len(row) else ""
            reference = f"{_column_name(col_index)}{row_index}"
            if (col_index - 1) in numeric_columns:
                cells.append(_number_cell(reference, value, 6))
            else:
                clean_value = _clean_text(value)
                max_lines = max(max_lines, clean_value.count("\n") + 1)
                cell_style = 5 if col_index in {1, 2, 7, 8} else style_text
                cells.append(_inline_cell(reference, clean_value, cell_style))
        row_height = min(120, max(42, 15 * max_lines + 12))
        sheet_rows.append(
            f'<row r="{row_index}" ht="{row_height}" customHeight="1">'
            + "".join(cells)
            + "</row>"
        )

    cols_xml = "".join(
        f'<col min="{i}" max="{i}" width="{column_widths[i - 1] if i <= len(column_widths) else 18}" customWidth="1"/>'
        for i in range(1, max_col + 1)
    )
    safe_sheet = _safe_sheet_name(sheet_name)

    # Orden válido según CT_Worksheet:
    # sheetPr, dimension, sheetViews, sheetFormatPr, cols, sheetData,
    # autoFilter, mergeCells, printOptions, pageMargins, pageSetup.
    worksheet = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>
  <dimension ref="A1:{end_col}{max_row}"/>
  <sheetViews><sheetView workbookViewId="0"><pane ySplit="4" topLeftCell="A5" activePane="bottomLeft" state="frozen"/><selection pane="bottomLeft" activeCell="A5" sqref="A5"/></sheetView></sheetViews>
  <sheetFormatPr defaultRowHeight="18"/>
  <cols>{cols_xml}</cols>
  <sheetData>{''.join(sheet_rows)}</sheetData>
  <autoFilter ref="A4:{end_col}{max_row}"/>
  <mergeCells count="2"><mergeCell ref="A1:{end_col}1"/><mergeCell ref="A2:{end_col}2"/></mergeCells>
  <printOptions headings="0" gridLines="0" horizontalCentered="0" verticalCentered="0"/>
  <pageMargins left="0.25" right="0.25" top="0.5" bottom="0.5" header="0.2" footer="0.2"/>
  <pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0" paperSize="9"/>
</worksheet>'''

    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="4">
    <font><sz val="10"/><name val="Calibri"/><family val="2"/></font>
    <font><b/><sz val="18"/><color rgb="FF123765"/><name val="Calibri"/><family val="2"/></font>
    <font><sz val="10"/><color rgb="FF5B6B7F"/><name val="Calibri"/><family val="2"/></font>
    <font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Calibri"/><family val="2"/></font>
  </fonts>
  <fills count="5">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF1677E8"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFF3F7FC"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFEAF3FF"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border><left style="thin"><color rgb="FFD9E3EF"/></left><right style="thin"><color rgb="FFD9E3EF"/></right><top style="thin"><color rgb="FFD9E3EF"/></top><bottom style="thin"><color rgb="FFD9E3EF"/></bottom><diagonal/></border>
  </borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="8">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment vertical="center"/></xf>
    <xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment vertical="center"/></xf>
    <xf numFmtId="0" fontId="3" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
    <xf numFmtId="0" fontId="0" fillId="3" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''

    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>'''
    workbook = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <workbookPr date1904="0"/>
  <bookViews><workbookView activeTab="0" firstSheet="0"/></bookViews>
  <sheets><sheet name="{safe_sheet}" sheetId="1" state="visible" r:id="rId1"/></sheets>
  <calcPr calcId="191029" fullCalcOnLoad="1"/>
</workbook>'''
    workbook_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    core = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>Reporte institucional RSU</dc:title><dc:creator>UNAB RSU</dc:creator><cp:lastModifiedBy>UNAB RSU</cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>'''
    app = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>UNAB RSU</Application><AppVersion>82.0</AppVersion><HeadingPairs><vt:vector size="2" baseType="variant"><vt:variant><vt:lpstr>Hojas de cálculo</vt:lpstr></vt:variant><vt:variant><vt:i4>1</vt:i4></vt:variant></vt:vector></HeadingPairs><TitlesOfParts><vt:vector size="1" baseType="lpstr"><vt:lpstr>{safe_sheet}</vt:lpstr></vt:vector></TitlesOfParts></Properties>'''

    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED, allowZip64=True) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
        archive.writestr("xl/styles.xml", styles)
        archive.writestr("docProps/core.xml", core)
        archive.writestr("docProps/app.xml", app)

    content = stream.getvalue()
    _validate_package(content)
    return content
