from __future__ import annotations

from datetime import date

from app.db.mysql import execute, fetch_one


ACTIVITY_STATES = [
    ("actividad_presentada", "Anexos 1 y 8 presentados"),
    ("actividad_en_evaluacion", "Plan de actividad en evaluación mediante Anexo 9"),
    ("actividad_observada", "Plan observado y pendiente de subsanación"),
    ("actividad_aprobada", "Plan aprobado por los dos revisores"),
    ("actividad_memorandum_emitido", "Memorándum de aprobación emitido"),
    ("actividad_en_ejecucion", "Actividad específica en ejecución"),
    ("actividad_cierre_presentado", "Anexo 10 presentado para revisión"),
    ("actividad_cierre_observado", "Informe final observado y pendiente de subsanación"),
    ("actividad_final_aprobada", "Informe final aprobado"),
    ("actividad_certificacion", "Actividad lista para certificación"),
    ("actividad_certificada", "Actividad específica finalizada y certificada"),
    ("actividad_no_presentada", "Actividad concluida como no presentada por vencimiento"),
]


def _state_id(entity: str, name: str) -> str:
    row = fetch_one(
        "SELECT catalogo_estado_id FROM catalogo_estado WHERE entidad=%s AND LOWER(nombre)=LOWER(%s) LIMIT 1",
        (entity, name),
    )
    if row:
        return str(row["catalogo_estado_id"])
    execute(
        "INSERT INTO catalogo_estado (catalogo_estado_id,entidad,nombre,descripcion,activo) VALUES (UUID(),%s,%s,%s,1)",
        (entity, name, name.replace("_", " ").title()),
    )
    row = fetch_one(
        "SELECT catalogo_estado_id FROM catalogo_estado WHERE entidad=%s AND LOWER(nombre)=LOWER(%s) LIMIT 1",
        (entity, name),
    )
    if not row:
        raise RuntimeError(f"No se pudo crear el estado {entity}/{name}")
    return str(row["catalogo_estado_id"])


def ensure_specific_activity_schema() -> None:
    for name, description in ACTIVITY_STATES:
        execute(
            """
            INSERT IGNORE INTO catalogo_estado
            (catalogo_estado_id, entidad, nombre, descripcion, activo)
            VALUES (UUID(), 'proyecto', %s, %s, 1)
            """,
            (name, description),
        )

    execute(
        """
        INSERT IGNORE INTO catalogo_rol
        (rol_id,nombre,descripcion,solo_docente,solo_voluntariado,activo)
        VALUES (UUID(),'docente_asesor','Docente asesor de actividad liderada por estudiante',1,0,1)
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_specific_activity_workflow (
          proyecto_id CHAR(36) NOT NULL,
          estado_flujo VARCHAR(50) NOT NULL DEFAULT 'actividad_presentada',
          etapa_actual VARCHAR(40) NOT NULL DEFAULT 'evaluacion_inicial',
          revision_inicial INT NOT NULL DEFAULT 1,
          revision_final INT NOT NULL DEFAULT 1,
          horas_totales INT NOT NULL DEFAULT 16,
          fecha_limite_subsanacion DATE NULL,
          fecha_presentacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          fecha_aprobacion_inicial DATETIME NULL,
          fecha_memorandum_aprobacion DATETIME NULL,
          fecha_inicio_ejecucion DATE NULL,
          fecha_presentacion_cierre DATETIME NULL,
          fecha_aprobacion_final DATETIME NULL,
          fecha_memorandum_final DATETIME NULL,
          fecha_certificacion DATETIME NULL,
          motivo_fin VARCHAR(255) NULL,
          creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (proyecto_id),
          CONSTRAINT fk_specific_activity_project
            FOREIGN KEY (proyecto_id) REFERENCES proyecto(proyecto_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


    hours_column = fetch_one(
        """SELECT 1 AS ok FROM information_schema.columns
           WHERE table_schema=DATABASE() AND table_name='rsu_specific_activity_workflow'
             AND column_name='horas_totales' LIMIT 1"""
    )
    if not hours_column:
        execute(
            "ALTER TABLE rsu_specific_activity_workflow ADD COLUMN horas_totales INT NOT NULL DEFAULT 16 AFTER revision_final"
        )

    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_specific_activity_memorandum (
          memorandum_id CHAR(36) NOT NULL,
          proyecto_id CHAR(36) NOT NULL,
          tipo VARCHAR(20) NOT NULL,
          numero_memorandum VARCHAR(100) NOT NULL,
          fecha_emision DATE NOT NULL,
          archivo_docx_url TEXT NOT NULL,
          archivo_pdf_url TEXT NULL,
          responsable_persona_id CHAR(36) NOT NULL,
          vicepresidente_persona_id CHAR(36) NULL,
          directora_persona_id CHAR(36) NULL,
          emitido_por CHAR(36) NOT NULL,
          fecha_registro DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (memorandum_id),
          UNIQUE KEY uq_specific_activity_memo (proyecto_id,tipo),
          UNIQUE KEY uq_specific_activity_memo_number (numero_memorandum),
          CONSTRAINT fk_specific_activity_memo_project FOREIGN KEY (proyecto_id) REFERENCES proyecto(proyecto_id) ON DELETE CASCADE,
          CONSTRAINT fk_specific_activity_memo_resp FOREIGN KEY (responsable_persona_id) REFERENCES persona(persona_id),
          CONSTRAINT fk_specific_activity_memo_vice FOREIGN KEY (vicepresidente_persona_id) REFERENCES persona(persona_id),
          CONSTRAINT fk_specific_activity_memo_director FOREIGN KEY (directora_persona_id) REFERENCES persona(persona_id),
          CONSTRAINT fk_specific_activity_memo_emitter FOREIGN KEY (emitido_por) REFERENCES persona(persona_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_specific_activity_annual_call (
          anio SMALLINT NOT NULL,
          convocatoria_id CHAR(36) NOT NULL,
          creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (anio),
          UNIQUE KEY uq_specific_activity_call (convocatoria_id),
          CONSTRAINT fk_specific_activity_call FOREIGN KEY (convocatoria_id) REFERENCES convocatoria(convocatoria_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    # Catálogo documental de la modalidad.
    activity_type = fetch_one(
        "SELECT tipo_proyecto_id FROM tipo_proyecto WHERE LOWER(nombre) LIKE '%actividades espec%' LIMIT 1"
    )
    if not activity_type:
        execute(
            """
            INSERT INTO tipo_proyecto
            (tipo_proyecto_id,nombre,horas_minimas,horas_jornada_minima,descripcion)
            VALUES (UUID(),'Actividades específicas y de proyección social',16,NULL,
                    'Actividades específicas y de proyección social de la UNAB')
            """
        )
        activity_type = fetch_one(
            "SELECT tipo_proyecto_id FROM tipo_proyecto WHERE LOWER(nombre) LIKE '%actividades espec%' LIMIT 1"
        )
    type_id = str(activity_type["tipo_proyecto_id"])
    for name, annex, stage, required, description in [
        ("Solicitud de participación", "Anexo 1", "presentacion", 1, "Solicitud para presentar la actividad"),
        ("Plan de actividades específicas", "Anexo 8", "presentacion", 1, "Plan de la actividad específica"),
        ("Evaluación del plan", "Anexo 9", "evaluacion", 1, "Ficha de evaluación del plan"),
        ("Informe final de actividad", "Anexo 10", "cierre", 1, "Informe final de la actividad"),
        ("Certificado", "Anexo 14", "certificacion", 1, "Certificado individual"),
    ]:
        exists = fetch_one(
            """SELECT tipo_documento_id FROM tipo_documento
               WHERE tipo_proyecto_id=%s AND LOWER(COALESCE(anexo_referencia,''))=LOWER(%s) LIMIT 1""",
            (type_id, annex),
        )
        if not exists:
            execute(
                """INSERT INTO tipo_documento
                   (tipo_documento_id,tipo_proyecto_id,nombre,anexo_referencia,etapa,obligatorio,descripcion)
                   VALUES (UUID(),%s,%s,%s,%s,%s,%s)""",
                (type_id, name, annex, stage, required, description),
            )

    ensure_annual_specific_activity_call(date.today().year)


def ensure_annual_specific_activity_call(year: int) -> str:
    existing = fetch_one(
        "SELECT convocatoria_id FROM rsu_specific_activity_annual_call WHERE anio=%s LIMIT 1",
        (int(year),),
    )
    if existing:
        return str(existing["convocatoria_id"])

    type_row = fetch_one(
        "SELECT tipo_proyecto_id FROM tipo_proyecto WHERE LOWER(nombre) LIKE '%actividades espec%' LIMIT 1"
    )
    if not type_row:
        raise RuntimeError("No existe el tipo Actividades específicas")

    period_name = f"{int(year)}-ANUAL"
    period = fetch_one("SELECT periodo_academico_id FROM periodo_academico WHERE nombre=%s LIMIT 1", (period_name,))
    if not period:
        execute(
            """INSERT INTO periodo_academico
               (periodo_academico_id,nombre,fecha_inicio,fecha_fin,activo)
               VALUES (UUID(),%s,%s,%s,0)""",
            (period_name, f"{year}-01-01", f"{year}-12-31"),
        )
        period = fetch_one("SELECT periodo_academico_id FROM periodo_academico WHERE nombre=%s LIMIT 1", (period_name,))

    active_state = _state_id("convocatoria", "activa")
    call_id_row = fetch_one(
        """SELECT convocatoria_id FROM convocatoria
           WHERE tipo_proyecto_id=%s AND fecha_inicio=%s AND fecha_limite_presentacion=%s LIMIT 1""",
        (type_row["tipo_proyecto_id"], f"{year}-01-01", f"{year}-12-31"),
    )
    if call_id_row:
        call_id = str(call_id_row["convocatoria_id"])
        execute("UPDATE convocatoria SET estado_id=%s WHERE convocatoria_id=%s", (active_state, call_id))
    else:
        execute(
            """INSERT INTO convocatoria
               (convocatoria_id,tipo_proyecto_id,periodo_academico_id,fecha_inicio,
                fecha_limite_presentacion,monto_maximo_financiamiento,bases_url,estado_id)
               VALUES (UUID(),%s,%s,%s,%s,NULL,NULL,%s)""",
            (
                type_row["tipo_proyecto_id"],
                period["periodo_academico_id"],
                f"{year}-01-01",
                f"{year}-12-31",
                active_state,
            ),
        )
        call_id_row = fetch_one(
            """SELECT convocatoria_id FROM convocatoria
               WHERE tipo_proyecto_id=%s AND fecha_inicio=%s AND fecha_limite_presentacion=%s LIMIT 1""",
            (type_row["tipo_proyecto_id"], f"{year}-01-01", f"{year}-12-31"),
        )
        call_id = str(call_id_row["convocatoria_id"])

    execute(
        "INSERT IGNORE INTO rsu_specific_activity_annual_call (anio,convocatoria_id) VALUES (%s,%s)",
        (int(year), call_id),
    )
    return call_id
