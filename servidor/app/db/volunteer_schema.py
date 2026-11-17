from __future__ import annotations

from datetime import date

from app.db.mysql import execute, fetch_one


VOLUNTEER_STATES = [
    ("voluntariado_borrador", "Expediente de voluntariado en preparación"),
    ("voluntariado_pendiente_fichas", "Pendiente de completar fichas individuales del Anexo 12"),
    ("voluntariado_en_evaluacion_inicial", "Anexos 1, 11 y 12 en evaluación"),
    ("voluntariado_observado_inicial", "Expediente inicial observado y pendiente de subsanación"),
    ("voluntariado_aprobado_inicial", "Expediente inicial aprobado por los revisores"),
    ("voluntariado_memorandum_inicial", "Memorándum de aprobación inicial emitido"),
    ("voluntariado_en_ejecucion_interno", "Voluntariado interno en ejecución"),
    ("voluntariado_en_ejecucion_externo", "Voluntariado externo en ejecución"),
    ("voluntariado_en_evaluacion_final", "Anexo 13 en evaluación final"),
    ("voluntariado_en_convalidacion", "Anexo 13 de voluntariado externo en convalidación"),
    ("voluntariado_observado_final", "Informe final observado y pendiente de subsanación"),
    ("voluntariado_aprobado_final", "Informe final de voluntariado aprobado"),
    ("voluntariado_memorandum_final", "Memorándum final emitido"),
    ("voluntariado_en_certificacion", "Voluntariado habilitado para certificación"),
    ("voluntariado_certificado", "Voluntariado finalizado y certificado"),
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


def ensure_volunteer_schema() -> None:
    for name, description in VOLUNTEER_STATES:
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
        VALUES (UUID(),'voluntario','Colaborador voluntario del plan de voluntariado',0,1,1)
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_volunteer_annual_call (
          anio SMALLINT NOT NULL,
          convocatoria_id CHAR(36) NOT NULL,
          permanente TINYINT(1) NOT NULL DEFAULT 1,
          activa TINYINT(1) NOT NULL DEFAULT 1,
          creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (anio),
          UNIQUE KEY uq_volunteer_call (convocatoria_id),
          CONSTRAINT fk_volunteer_call FOREIGN KEY (convocatoria_id) REFERENCES convocatoria(convocatoria_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_volunteer_workflow (
          proyecto_id CHAR(36) NOT NULL,
          estado_flujo VARCHAR(60) NOT NULL DEFAULT 'voluntariado_borrador',
          etapa_actual VARCHAR(45) NOT NULL DEFAULT 'preparacion',
          modalidad ENUM('interno','externo') NULL,
          revision_inicial INT NOT NULL DEFAULT 1,
          revision_final INT NOT NULL DEFAULT 1,
          fecha_limite_subsanacion DATE NULL,
          fecha_presentacion_inicial DATETIME NULL,
          fecha_aprobacion_inicial DATETIME NULL,
          fecha_memorandum_inicial DATETIME NULL,
          fecha_inicio_ejecucion DATE NULL,
          fecha_presentacion_final DATETIME NULL,
          fecha_aprobacion_final DATETIME NULL,
          fecha_memorandum_final DATETIME NULL,
          fecha_certificacion DATETIME NULL,
          creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (proyecto_id),
          CONSTRAINT fk_volunteer_workflow_project FOREIGN KEY (proyecto_id) REFERENCES proyecto(proyecto_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_volunteer_registration (
          registration_id CHAR(36) NOT NULL,
          proyecto_id CHAR(36) NOT NULL,
          persona_id CHAR(36) NOT NULL,
          revision INT NOT NULL DEFAULT 1,
          datos_tutor_json LONGTEXT NULL,
          datos_voluntario_json LONGTEXT NOT NULL,
          seguro_json LONGTEXT NULL,
          discapacidad_json LONGTEXT NULL,
          foto_url VARCHAR(500) NULL,
          firma_url VARCHAR(500) NULL,
          huella_url VARCHAR(500) NULL,
          pdf_url VARCHAR(500) NULL,
          estado VARCHAR(30) NOT NULL DEFAULT 'borrador',
          es_actual TINYINT(1) NOT NULL DEFAULT 1,
          fecha_presentacion DATETIME NULL,
          fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (registration_id),
          UNIQUE KEY uq_volunteer_registration_revision (proyecto_id,persona_id,revision),
          KEY idx_volunteer_registration_current (proyecto_id,persona_id,es_actual),
          CONSTRAINT fk_volunteer_registration_project FOREIGN KEY (proyecto_id) REFERENCES proyecto(proyecto_id) ON DELETE CASCADE,
          CONSTRAINT fk_volunteer_registration_person FOREIGN KEY (persona_id) REFERENCES persona(persona_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_person_volunteer_profile (
          persona_id CHAR(36) NOT NULL,
          pasaporte VARCHAR(30) NULL,
          carne_extranjeria VARCHAR(30) NULL,
          distrito VARCHAR(120) NULL,
          provincia VARCHAR(120) NULL,
          departamento VARCHAR(120) NULL,
          actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (persona_id),
          CONSTRAINT fk_volunteer_profile_person FOREIGN KEY (persona_id)
            REFERENCES persona(persona_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_volunteer_annex12_bundle (
          bundle_id CHAR(36) NOT NULL,
          proyecto_id CHAR(36) NOT NULL,
          revision INT NOT NULL,
          archivo_url VARCHAR(500) NOT NULL,
          manifest_json LONGTEXT NOT NULL,
          cantidad_fichas INT NOT NULL DEFAULT 0,
          cantidad_paginas INT NOT NULL DEFAULT 0,
          sha256 CHAR(64) NOT NULL,
          es_actual TINYINT(1) NOT NULL DEFAULT 1,
          fecha_generacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (bundle_id),
          UNIQUE KEY uq_volunteer_bundle_revision (proyecto_id,revision),
          KEY idx_volunteer_bundle_current (proyecto_id,es_actual),
          CONSTRAINT fk_volunteer_bundle_project FOREIGN KEY (proyecto_id) REFERENCES proyecto(proyecto_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_volunteer_memorandum (
          memorandum_id CHAR(36) NOT NULL,
          proyecto_id CHAR(36) NOT NULL,
          tipo ENUM('aprobacion_inicial','aprobacion_final') NOT NULL,
          numero_memorandum VARCHAR(100) NOT NULL,
          fecha_emision DATE NOT NULL,
          archivo_docx_url VARCHAR(500) NULL,
          archivo_pdf_url VARCHAR(500) NOT NULL,
          responsable_persona_id CHAR(36) NOT NULL,
          vicepresidente_persona_id CHAR(36) NULL,
          directora_persona_id CHAR(36) NULL,
          emitido_por CHAR(36) NOT NULL,
          fecha_registro DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (memorandum_id),
          UNIQUE KEY uq_volunteer_memo_project_type (proyecto_id,tipo),
          UNIQUE KEY uq_volunteer_memo_number (numero_memorandum),
          CONSTRAINT fk_volunteer_memo_project FOREIGN KEY (proyecto_id) REFERENCES proyecto(proyecto_id) ON DELETE CASCADE,
          CONSTRAINT fk_volunteer_memo_responsible FOREIGN KEY (responsable_persona_id) REFERENCES persona(persona_id),
          CONSTRAINT fk_volunteer_memo_vice FOREIGN KEY (vicepresidente_persona_id) REFERENCES persona(persona_id),
          CONSTRAINT fk_volunteer_memo_director FOREIGN KEY (directora_persona_id) REFERENCES persona(persona_id),
          CONSTRAINT fk_volunteer_memo_emitter FOREIGN KEY (emitido_por) REFERENCES persona(persona_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    activity_type = fetch_one(
        "SELECT tipo_proyecto_id FROM tipo_proyecto WHERE LOWER(nombre) LIKE '%voluntariado%' LIMIT 1"
    )
    if not activity_type:
        execute(
            """
            INSERT INTO tipo_proyecto
            (tipo_proyecto_id,nombre,horas_minimas,horas_jornada_minima,descripcion)
            VALUES (UUID(),'Voluntariado RSU',1,1,'Plan anual permanente de voluntariado universitario')
            """
        )
        activity_type = fetch_one(
            "SELECT tipo_proyecto_id FROM tipo_proyecto WHERE LOWER(nombre) LIKE '%voluntariado%' LIMIT 1"
        )
    type_id = str(activity_type["tipo_proyecto_id"])
    for name, annex, stage, required, description in [
        ("Solicitud de participación", "Anexo 1", "presentacion", 1, "Solicitud del responsable"),
        ("Plan de actividades de voluntariado", "Anexo 11", "presentacion", 1, "Plan inicial del voluntariado"),
        ("Ficha de registro de voluntario", "Anexo 12", "presentacion", 1, "Ficha individual consolidada de los colaboradores"),
        ("Informe final de voluntariado", "Anexo 13", "cierre", 1, "Informe final y evidencias"),
        ("Certificado de voluntariado", "Anexo 14", "certificacion", 1, "Certificado individual"),
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

    ensure_annual_volunteer_call(date.today().year)


def ensure_annual_volunteer_call(year: int) -> str:
    existing = fetch_one(
        "SELECT convocatoria_id FROM rsu_volunteer_annual_call WHERE anio=%s AND activa=1 LIMIT 1",
        (int(year),),
    )
    if existing:
        return str(existing["convocatoria_id"])

    type_row = fetch_one(
        "SELECT tipo_proyecto_id FROM tipo_proyecto WHERE LOWER(nombre) LIKE '%voluntariado%' LIMIT 1"
    )
    if not type_row:
        raise RuntimeError("No existe el tipo Voluntariado RSU")

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
    call = fetch_one(
        """SELECT convocatoria_id FROM convocatoria
           WHERE tipo_proyecto_id=%s AND fecha_inicio=%s AND fecha_limite_presentacion=%s LIMIT 1""",
        (type_row["tipo_proyecto_id"], f"{year}-01-01", f"{year}-12-31"),
    )
    if call:
        call_id = str(call["convocatoria_id"])
        execute("UPDATE convocatoria SET estado_id=%s WHERE convocatoria_id=%s", (active_state, call_id))
    else:
        execute(
            """INSERT INTO convocatoria
               (convocatoria_id,tipo_proyecto_id,periodo_academico_id,fecha_inicio,
                fecha_limite_presentacion,monto_maximo_financiamiento,bases_url,estado_id)
               VALUES (UUID(),%s,%s,%s,%s,NULL,NULL,%s)""",
            (type_row["tipo_proyecto_id"], period["periodo_academico_id"], f"{year}-01-01", f"{year}-12-31", active_state),
        )
        call = fetch_one(
            """SELECT convocatoria_id FROM convocatoria
               WHERE tipo_proyecto_id=%s AND fecha_inicio=%s AND fecha_limite_presentacion=%s LIMIT 1""",
            (type_row["tipo_proyecto_id"], f"{year}-01-01", f"{year}-12-31"),
        )
        call_id = str(call["convocatoria_id"])

    execute(
        """INSERT INTO rsu_volunteer_annual_call (anio,convocatoria_id,permanente,activa)
           VALUES (%s,%s,1,1)
           ON DUPLICATE KEY UPDATE convocatoria_id=VALUES(convocatoria_id),permanente=1,activa=1""",
        (int(year), call_id),
    )
    return call_id
