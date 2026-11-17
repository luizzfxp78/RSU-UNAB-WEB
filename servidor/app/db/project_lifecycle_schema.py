from __future__ import annotations

from app.db.mysql import execute, fetch_one


PROJECT_STATES = [
    ("presentado", "Anexos 1 y 2 presentados"),
    ("registro_anexo3", "Anexo 3 pendiente de presentación por el responsable"),
    ("en_evaluacion", "Proyecto en evaluación mediante Anexo 4"),
    ("con_observaciones", "Proyecto observado y pendiente de subsanación"),
    ("aprobado", "Proyecto aprobado por la Dirección de RSU"),
    ("pendiente_acta", "Estado legado pendiente de acto resolutivo"),
    ("memorandum_emitido", "Memorándum de aprobación emitido y entregado"),
    ("listo_ejecucion", "Memorándum emitido; proyecto listo para iniciar"),
    ("en_ejecucion", "Proyecto en ejecución"),
    ("monitoreo", "Proyecto en etapa de monitoreo"),
    ("cierre", "Proyecto en etapa de cierre"),
    ("observacion_final", "Informe final observado y pendiente de subsanación"),
    ("finalizado", "Proyecto finalizado y certificado"),
    ("no_presentado", "Proyecto no presentado por vencimiento de subsanación"),
    ("desaprobado", "Proyecto o informe final desaprobado"),
]

STAGE_STATES = [
    ("pendiente", "Etapa pendiente"),
    ("en_revision", "Etapa en revisión"),
    ("con_observaciones", "Etapa observada"),
    ("completado", "Etapa completada"),
    ("vencido", "Etapa vencida"),
]

CERTIFICATE_STATES = [
    ("pendiente", "Certificado pendiente"),
    ("emitido", "Certificado emitido"),
]


def ensure_project_lifecycle_schema() -> None:
    """Extiende de manera compatible el modelo original con el ciclo completo RSU."""
    for name, description in PROJECT_STATES:
        execute(
            """
            INSERT IGNORE INTO catalogo_estado
            (catalogo_estado_id, entidad, nombre, descripcion, activo)
            VALUES (UUID(), 'proyecto', %s, %s, 1)
            """,
            (name, description),
        )
    for name, description in STAGE_STATES:
        execute(
            """
            INSERT IGNORE INTO catalogo_estado
            (catalogo_estado_id, entidad, nombre, descripcion, activo)
            VALUES (UUID(), 'etapa', %s, %s, 1)
            """,
            (name, description),
        )
    for role_name, description, solo_docente in [
        ("responsable", "Representante del proyecto", 0),
        ("corresponsable", "Corresponsable del proyecto", 1),
        ("colaborador", "Colaborador del proyecto", 0),
    ]:
        execute(
            """
            INSERT IGNORE INTO catalogo_rol
            (rol_id, nombre, descripcion, solo_docente, solo_voluntariado, activo)
            VALUES (UUID(), %s, %s, %s, 0, 1)
            """,
            (role_name, description, solo_docente),
        )

    for name, description in CERTIFICATE_STATES:
        execute(
            """
            INSERT IGNORE INTO catalogo_estado
            (catalogo_estado_id, entidad, nombre, descripcion, activo)
            VALUES (UUID(), 'certificado', %s, %s, 1)
            """,
            (name, description),
        )

    execute(
        """
        CREATE TABLE IF NOT EXISTS calendario_laboral (
          fecha DATE NOT NULL,
          es_laborable TINYINT(1) NOT NULL DEFAULT 1,
          motivo VARCHAR(255) NULL,
          PRIMARY KEY (fecha)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_project_workflow (
          proyecto_id CHAR(36) NOT NULL,
          etapa_actual VARCHAR(40) NOT NULL DEFAULT 'presentacion',
          estado_flujo VARCHAR(40) NOT NULL DEFAULT 'presentado',
          fecha_limite_actual DATE NULL,
          fecha_primera_presentacion DATETIME NULL,
          fecha_aprobacion_direccion DATETIME NULL,
          fecha_acta_resolutiva DATETIME NULL,
          fecha_inicio_ejecucion DATE NULL,
          fecha_semana_9 DATE NULL,
          fecha_semana_16 DATE NULL,
          fecha_finalizacion DATETIME NULL,
          eliminado_logico TINYINT(1) NOT NULL DEFAULT 0,
          motivo_cierre VARCHAR(255) NULL,
          creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (proyecto_id),
          CONSTRAINT fk_rsu_workflow_proyecto
            FOREIGN KEY (proyecto_id) REFERENCES proyecto(proyecto_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    # Aperturas anticipadas de los informes durante la ejecución. Las fechas
    # reglamentarias se conservan y el administrador RSU puede habilitar cada
    # formulario antes de la semana programada sin alterar el cronograma.
    for column_name, definition in [
        ("anexo5_habilitado_manual", "TINYINT(1) NOT NULL DEFAULT 0"),
        ("anexo6_habilitado_manual", "TINYINT(1) NOT NULL DEFAULT 0"),
        ("fecha_habilitacion_anexo5", "DATETIME NULL"),
        ("fecha_habilitacion_anexo6", "DATETIME NULL"),
        ("anexo5_habilitado_por", "CHAR(36) NULL"),
        ("anexo6_habilitado_por", "CHAR(36) NULL"),
    ]:
        exists = fetch_one(
            """SELECT 1 AS ok FROM information_schema.columns
            WHERE table_schema=DATABASE() AND table_name='rsu_project_workflow' AND column_name=%s""",
            (column_name,),
        )
        if not exists:
            execute(f"ALTER TABLE rsu_project_workflow ADD COLUMN {column_name} {definition}")

    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_annex_submission (
          submission_id CHAR(36) NOT NULL,
          proyecto_id CHAR(36) NOT NULL,
          anexo_numero INT NOT NULL,
          revision INT NOT NULL DEFAULT 1,
          presentado_por CHAR(36) NOT NULL,
          contenido_json LONGTEXT NOT NULL,
          estado VARCHAR(30) NOT NULL DEFAULT 'presentado',
          es_actual TINYINT(1) NOT NULL DEFAULT 1,
          fecha_presentacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          fecha_revision DATETIME NULL,
          PRIMARY KEY (submission_id),
          UNIQUE KEY uq_annex_revision (proyecto_id, anexo_numero, revision),
          KEY idx_annex_current (proyecto_id, anexo_numero, es_actual),
          CONSTRAINT fk_annex_project FOREIGN KEY (proyecto_id) REFERENCES proyecto(proyecto_id) ON DELETE CASCADE,
          CONSTRAINT fk_annex_person FOREIGN KEY (presentado_por) REFERENCES persona(persona_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_annex_evaluation (
          evaluation_id CHAR(36) NOT NULL,
          proyecto_id CHAR(36) NOT NULL,
          anexo_numero INT NOT NULL,
          revision INT NOT NULL DEFAULT 1,
          evaluador_persona_id CHAR(36) NOT NULL,
          criterios_json LONGTEXT NOT NULL,
          total_si INT NOT NULL DEFAULT 0,
          total_no INT NOT NULL DEFAULT 0,
          resultado VARCHAR(30) NOT NULL,
          observaciones LONGTEXT NULL,
          fecha_limite_subsanacion DATE NULL,
          fecha_evaluacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (evaluation_id),
          KEY idx_annex_eval_project (proyecto_id, anexo_numero, fecha_evaluacion),
          CONSTRAINT fk_annex_eval_project FOREIGN KEY (proyecto_id) REFERENCES proyecto(proyecto_id) ON DELETE CASCADE,
          CONSTRAINT fk_annex_eval_person FOREIGN KEY (evaluador_persona_id) REFERENCES persona(persona_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_workflow_file (
          workflow_file_id CHAR(36) NOT NULL,
          proyecto_id CHAR(36) NOT NULL,
          anexo_numero INT NULL,
          proposito VARCHAR(60) NOT NULL,
          etiqueta VARCHAR(120) NULL,
          persona_firmante_id CHAR(36) NULL,
          archivo_url VARCHAR(500) NOT NULL,
          nombre_archivo VARCHAR(255) NOT NULL,
          mime_type VARCHAR(100) NULL,
          subido_por CHAR(36) NOT NULL,
          fecha_subida DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (workflow_file_id),
          KEY idx_workflow_file_project (proyecto_id, anexo_numero),
          CONSTRAINT fk_workflow_file_project FOREIGN KEY (proyecto_id) REFERENCES proyecto(proyecto_id) ON DELETE CASCADE,
          CONSTRAINT fk_workflow_file_signer FOREIGN KEY (persona_firmante_id) REFERENCES persona(persona_id),
          CONSTRAINT fk_workflow_file_uploader FOREIGN KEY (subido_por) REFERENCES persona(persona_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_resolution_act (
          resolution_id CHAR(36) NOT NULL,
          proyecto_id CHAR(36) NOT NULL,
          numero_acta VARCHAR(80) NOT NULL,
          fecha_acta DATE NOT NULL,
          vicepresidente_nombre VARCHAR(180) NOT NULL,
          observaciones TEXT NULL,
          workflow_file_id CHAR(36) NULL,
          registrado_por CHAR(36) NOT NULL,
          fecha_registro DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (resolution_id),
          UNIQUE KEY uq_resolution_project (proyecto_id),
          CONSTRAINT fk_resolution_project FOREIGN KEY (proyecto_id) REFERENCES proyecto(proyecto_id) ON DELETE CASCADE,
          CONSTRAINT fk_resolution_file FOREIGN KEY (workflow_file_id) REFERENCES rsu_workflow_file(workflow_file_id),
          CONSTRAINT fk_resolution_person FOREIGN KEY (registrado_por) REFERENCES persona(persona_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_project_incident (
          incident_id CHAR(36) NOT NULL,
          proyecto_id CHAR(36) NOT NULL,
          reportado_por CHAR(36) NOT NULL,
          tipo VARCHAR(40) NOT NULL,
          descripcion LONGTEXT NOT NULL,
          miembro_afectado_id CHAR(36) NULL,
          reemplazo_propuesto_id CHAR(36) NULL,
          estado VARCHAR(20) NOT NULL DEFAULT 'pendiente',
          respuesta_admin LONGTEXT NULL,
          verificado_por CHAR(36) NULL,
          fecha_reporte DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          fecha_verificacion DATETIME NULL,
          PRIMARY KEY (incident_id),
          KEY idx_incident_project (proyecto_id, estado),
          CONSTRAINT fk_incident_project FOREIGN KEY (proyecto_id) REFERENCES proyecto(proyecto_id) ON DELETE CASCADE,
          CONSTRAINT fk_incident_reporter FOREIGN KEY (reportado_por) REFERENCES persona(persona_id),
          CONSTRAINT fk_incident_member FOREIGN KEY (miembro_afectado_id) REFERENCES persona(persona_id),
          CONSTRAINT fk_incident_replacement FOREIGN KEY (reemplazo_propuesto_id) REFERENCES persona(persona_id),
          CONSTRAINT fk_incident_verifier FOREIGN KEY (verificado_por) REFERENCES persona(persona_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_workflow_history (
          history_id CHAR(36) NOT NULL,
          proyecto_id CHAR(36) NOT NULL,
          etapa_anterior VARCHAR(40) NULL,
          etapa_nueva VARCHAR(40) NOT NULL,
          estado_anterior VARCHAR(40) NULL,
          estado_nuevo VARCHAR(40) NOT NULL,
          cambiado_por CHAR(36) NULL,
          comentario VARCHAR(500) NULL,
          fecha_cambio DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (history_id),
          KEY idx_workflow_history_project (proyecto_id, fecha_cambio),
          CONSTRAINT fk_workflow_history_project FOREIGN KEY (proyecto_id) REFERENCES proyecto(proyecto_id) ON DELETE CASCADE,
          CONSTRAINT fk_workflow_history_person FOREIGN KEY (cambiado_por) REFERENCES persona(persona_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    # Roles funcionales del sistema y perfiles de autoridades.
    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_system_role (
          system_role_id CHAR(36) NOT NULL,
          codigo VARCHAR(60) NOT NULL,
          nombre VARCHAR(120) NOT NULL,
          descripcion VARCHAR(255) NULL,
          activo TINYINT(1) NOT NULL DEFAULT 1,
          PRIMARY KEY (system_role_id),
          UNIQUE KEY uq_rsu_system_role_code (codigo)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    for code, name, description in [
        ("administrador_rsu", "Administrador de RSU", "Gestiona el flujo y participa como revisor institucional."),
        ("administrativo_normal", "Administrativo general", "Personal administrativo de otras áreas, sin acceso a la administración de RSU."),
        ("director_rsu", "Director(a) de RSU", "Autoridad que firma el memorándum de aprobación."),
        ("director_facultad", "Director(a) de facultad", "Autoridad académica de la facultad y segundo revisor del proyecto."),
        ("vicepresidente_academico", "Vicepresidente académico", "Autoridad receptora de los memorándums de aprobación."),
    ]:
        execute(
            """INSERT IGNORE INTO rsu_system_role
            (system_role_id,codigo,nombre,descripcion,activo) VALUES (UUID(),%s,%s,%s,1)""",
            (code, name, description),
        )
    execute(
        """UPDATE rsu_system_role
        SET nombre='Director(a) de facultad',
            descripcion='Autoridad académica de la facultad y segundo revisor del proyecto.',
            activo=1
        WHERE codigo='director_facultad'"""
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_user_system_role (
          user_system_role_id CHAR(36) NOT NULL,
          usuario_id CHAR(36) NOT NULL,
          system_role_id CHAR(36) NOT NULL,
          facultad VARCHAR(150) NULL,
          activo TINYINT(1) NOT NULL DEFAULT 1,
          fecha_asignacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (user_system_role_id),
          UNIQUE KEY uq_user_system_role (usuario_id,system_role_id,facultad),
          KEY idx_user_system_role_active (usuario_id,activo),
          CONSTRAINT fk_user_system_role_user FOREIGN KEY (usuario_id) REFERENCES usuario(usuario_id) ON DELETE CASCADE,
          CONSTRAINT fk_user_system_role_role FOREIGN KEY (system_role_id) REFERENCES rsu_system_role(system_role_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_authority_profile (
          persona_id CHAR(36) NOT NULL,
          cargo_oficial VARCHAR(150) NOT NULL,
          facultad VARCHAR(150) NULL,
          oficina VARCHAR(180) NULL,
          resolucion_designacion VARCHAR(100) NULL,
          fecha_inicio DATE NULL,
          fecha_fin DATE NULL,
          firma_url VARCHAR(500) NULL,
          datos_adicionales_json LONGTEXT NULL,
          activo TINYINT(1) NOT NULL DEFAULT 1,
          actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (persona_id),
          CONSTRAINT fk_authority_profile_person FOREIGN KEY (persona_id) REFERENCES persona(persona_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_person_affiliation (
          persona_id CHAR(36) NOT NULL,
          tipo_vinculo VARCHAR(30) NOT NULL,
          departamento_academico_id CHAR(36) NULL,
          codigo_institucional VARCHAR(30) NULL,
          anio_egreso SMALLINT NULL,
          grado_academico VARCHAR(120) NULL,
          institucion_origen VARCHAR(180) NULL,
          PRIMARY KEY (persona_id),
          KEY idx_affiliation_type (tipo_vinculo),
          CONSTRAINT fk_affiliation_person FOREIGN KEY (persona_id) REFERENCES persona(persona_id) ON DELETE CASCADE,
          CONSTRAINT fk_affiliation_department FOREIGN KEY (departamento_academico_id) REFERENCES departamento_academico(departamento_academico_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_project_review_assignment (
          assignment_id CHAR(36) NOT NULL,
          proyecto_id CHAR(36) NOT NULL,
          anexo_numero INT NOT NULL DEFAULT 4,
          revision INT NOT NULL DEFAULT 1,
          rol_revisor VARCHAR(40) NOT NULL,
          revisor_persona_id CHAR(36) NULL,
          facultad VARCHAR(150) NULL,
          estado VARCHAR(30) NOT NULL DEFAULT 'pendiente',
          evaluation_id CHAR(36) NULL,
          fecha_asignacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          fecha_revision DATETIME NULL,
          activo TINYINT(1) NOT NULL DEFAULT 1,
          PRIMARY KEY (assignment_id),
          UNIQUE KEY uq_project_review_role (proyecto_id,anexo_numero,revision,rol_revisor),
          KEY idx_review_assignee (revisor_persona_id,estado,activo),
          CONSTRAINT fk_review_assignment_project FOREIGN KEY (proyecto_id) REFERENCES proyecto(proyecto_id) ON DELETE CASCADE,
          CONSTRAINT fk_review_assignment_person FOREIGN KEY (revisor_persona_id) REFERENCES persona(persona_id),
          CONSTRAINT fk_review_assignment_eval FOREIGN KEY (evaluation_id) REFERENCES rsu_annex_evaluation(evaluation_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    # V49: la misma tabla administra la revisión inicial (Anexo 4) y la
    # evaluación final (Anexo 7). Los registros antiguos se consideran Anexo 4.
    review_annex_column = fetch_one(
        """SELECT 1 AS ok FROM information_schema.columns
        WHERE table_schema=DATABASE() AND table_name='rsu_project_review_assignment'
          AND column_name='anexo_numero'"""
    )
    if not review_annex_column:
        execute("ALTER TABLE rsu_project_review_assignment ADD COLUMN anexo_numero INT NOT NULL DEFAULT 4 AFTER proyecto_id")
    old_review_index = fetch_one(
        """SELECT 1 AS ok FROM information_schema.statistics
        WHERE table_schema=DATABASE() AND table_name='rsu_project_review_assignment'
          AND index_name='uq_project_review_role' AND seq_in_index=4"""
    )
    if not old_review_index:
        existing_review_index = fetch_one(
            """SELECT 1 AS ok FROM information_schema.statistics
            WHERE table_schema=DATABASE() AND table_name='rsu_project_review_assignment'
              AND index_name='uq_project_review_role'
              LIMIT 1"""
        )
        if existing_review_index:
            # MySQL puede estar usando el índice único antiguo como índice de
            # soporte de la FK `fk_review_assignment_project`. Antes de
            # eliminarlo se crea un índice independiente sobre `proyecto_id`;
            # de lo contrario MySQL devuelve el error 1553.
            supporting_project_index = fetch_one(
                """SELECT 1 AS ok FROM information_schema.statistics
                WHERE table_schema=DATABASE()
                  AND table_name='rsu_project_review_assignment'
                  AND index_name='idx_review_project_fk'
                LIMIT 1"""
            )
            if not supporting_project_index:
                execute(
                    "ALTER TABLE rsu_project_review_assignment "
                    "ADD KEY idx_review_project_fk (proyecto_id)"
                )
            execute(
                "ALTER TABLE rsu_project_review_assignment "
                "DROP INDEX uq_project_review_role"
            )
        execute(
            "ALTER TABLE rsu_project_review_assignment "
            "ADD UNIQUE KEY uq_project_review_role "
            "(proyecto_id,anexo_numero,revision,rol_revisor)"
        )

    # Compatibilidad con versiones anteriores: el cargo "jefe_unidad_facultad"
    # fue retirado porque la universidad realiza la segunda revisión mediante
    # el Director de Facultad correspondiente. Se migran asignaciones antiguas
    # y se desactiva el rol obsoleto sin perder el historial.
    execute(
        """
        INSERT IGNORE INTO rsu_user_system_role
        (user_system_role_id,usuario_id,system_role_id,facultad,activo,fecha_asignacion)
        SELECT UUID(), old_usr.usuario_id, director_role.system_role_id,
               old_usr.facultad, old_usr.activo, old_usr.fecha_asignacion
        FROM rsu_user_system_role old_usr
        INNER JOIN rsu_system_role old_role
            ON old_role.system_role_id=old_usr.system_role_id
           AND old_role.codigo='jefe_unidad_facultad'
        INNER JOIN rsu_system_role director_role
            ON director_role.codigo='director_facultad'
        WHERE NOT EXISTS (
            SELECT 1
            FROM rsu_user_system_role existing
            WHERE existing.system_role_id=director_role.system_role_id
              AND existing.activo=1
              AND COALESCE(LOWER(TRIM(existing.facultad)),'')=
                  COALESCE(LOWER(TRIM(old_usr.facultad)),'')
        )
        """
    )
    execute(
        """
        UPDATE rsu_project_review_assignment pra
        INNER JOIN rsu_user_system_role usr
            ON usr.activo=1
           AND COALESCE(LOWER(TRIM(usr.facultad)),'')=
               COALESCE(LOWER(TRIM(pra.facultad)),'')
        INNER JOIN rsu_system_role sr
            ON sr.system_role_id=usr.system_role_id
           AND sr.codigo='director_facultad'
        INNER JOIN usuario u ON u.usuario_id=usr.usuario_id AND u.activo=1
        SET pra.rol_revisor='director_facultad',
            pra.revisor_persona_id=u.persona_id
        WHERE pra.rol_revisor='jefe_unidad_facultad'
          AND pra.activo=1
          AND pra.estado='pendiente'
        """
    )
    execute(
        """
        DELETE obsolete_assignment
        FROM rsu_project_review_assignment obsolete_assignment
        INNER JOIN rsu_project_review_assignment current_assignment
            ON current_assignment.proyecto_id=obsolete_assignment.proyecto_id
           AND current_assignment.revision=obsolete_assignment.revision
           AND current_assignment.rol_revisor='director_facultad'
        WHERE obsolete_assignment.rol_revisor='jefe_unidad_facultad'
        """
    )
    execute(
        "UPDATE rsu_project_review_assignment SET rol_revisor='director_facultad' "
        "WHERE rol_revisor='jefe_unidad_facultad'"
    )
    execute(
        """
        UPDATE rsu_user_system_role usr
        INNER JOIN rsu_system_role sr ON sr.system_role_id=usr.system_role_id
        SET usr.activo=0
        WHERE sr.codigo='jefe_unidad_facultad'
        """
    )
    execute("UPDATE rsu_system_role SET activo=0 WHERE codigo='jefe_unidad_facultad'")

    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_project_memorandum (
          memorandum_id CHAR(36) NOT NULL,
          proyecto_id CHAR(36) NOT NULL,
          numero_memorandum VARCHAR(100) NOT NULL,
          fecha_emision DATE NOT NULL,
          tipo_proyecto VARCHAR(120) NOT NULL,
          titulo_proyecto VARCHAR(255) NOT NULL,
          responsable_persona_id CHAR(36) NOT NULL,
          vicepresidente_persona_id CHAR(36) NULL,
          directora_persona_id CHAR(36) NULL,
          archivo_docx_url VARCHAR(500) NOT NULL,
          archivo_pdf_url VARCHAR(500) NULL,
          emitido_por CHAR(36) NOT NULL,
          fecha_entrega_responsable DATETIME NULL,
          fecha_entrega_vicepresidencia DATETIME NULL,
          fecha_registro DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (memorandum_id),
          UNIQUE KEY uq_memorandum_project (proyecto_id),
          UNIQUE KEY uq_memorandum_number (numero_memorandum),
          CONSTRAINT fk_memorandum_project FOREIGN KEY (proyecto_id) REFERENCES proyecto(proyecto_id) ON DELETE CASCADE,
          CONSTRAINT fk_memorandum_responsible FOREIGN KEY (responsable_persona_id) REFERENCES persona(persona_id),
          CONSTRAINT fk_memorandum_vice FOREIGN KEY (vicepresidente_persona_id) REFERENCES persona(persona_id),
          CONSTRAINT fk_memorandum_director FOREIGN KEY (directora_persona_id) REFERENCES persona(persona_id),
          CONSTRAINT fk_memorandum_emitter FOREIGN KEY (emitido_por) REFERENCES persona(persona_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_certificate_memorandum (
          certificate_memorandum_id CHAR(36) NOT NULL,
          proyecto_id CHAR(36) NOT NULL,
          numero_memorandum VARCHAR(100) NOT NULL,
          fecha_emision DATE NOT NULL,
          titulo_proyecto VARCHAR(255) NOT NULL,
          cantidad_certificados INT NOT NULL DEFAULT 0,
          vicepresidente_persona_id CHAR(36) NULL,
          directora_persona_id CHAR(36) NULL,
          archivo_docx_url VARCHAR(500) NOT NULL,
          archivo_pdf_url VARCHAR(500) NULL,
          emitido_por CHAR(36) NOT NULL,
          fecha_entrega_vicepresidencia DATETIME NULL,
          fecha_registro DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (certificate_memorandum_id),
          UNIQUE KEY uq_certificate_memorandum_project (proyecto_id),
          UNIQUE KEY uq_certificate_memorandum_number (numero_memorandum),
          CONSTRAINT fk_cert_memo_project FOREIGN KEY (proyecto_id) REFERENCES proyecto(proyecto_id) ON DELETE CASCADE,
          CONSTRAINT fk_cert_memo_vice FOREIGN KEY (vicepresidente_persona_id) REFERENCES persona(persona_id),
          CONSTRAINT fk_cert_memo_director FOREIGN KEY (directora_persona_id) REFERENCES persona(persona_id),
          CONSTRAINT fk_cert_memo_emitter FOREIGN KEY (emitido_por) REFERENCES persona(persona_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    # Las rutas de Windows pueden superar con facilidad 500 o 1024 caracteres
    # cuando el proyecto está dentro de carpetas de Descargas muy profundas.
    # TEXT evita que la emisión de certificados o memorándums falle por longitud.
    for table_name, column_name, nullable in [
        ("certificado", "archivo_url", "NULL"),
        ("rsu_workflow_file", "archivo_url", "NOT NULL"),
        ("rsu_project_memorandum", "archivo_docx_url", "NOT NULL"),
        ("rsu_project_memorandum", "archivo_pdf_url", "NULL"),
        ("rsu_certificate_memorandum", "archivo_docx_url", "NOT NULL"),
        ("rsu_certificate_memorandum", "archivo_pdf_url", "NULL"),
    ]:
        column = fetch_one(
            """SELECT data_type FROM information_schema.columns
               WHERE table_schema=DATABASE() AND table_name=%s AND column_name=%s""",
            (table_name, column_name),
        )
        if column and str(column.get("data_type") or "").lower() != "text":
            execute(f"ALTER TABLE {table_name} MODIFY COLUMN {column_name} TEXT {nullable}")

    # Columnas complementarias para identificar la función del evaluador.
    for column_name, definition in [
        ("rol_revisor", "VARCHAR(40) NULL"),
        ("facultad_revisor", "VARCHAR(150) NULL"),
    ]:
        exists = fetch_one(
            """SELECT 1 AS ok FROM information_schema.columns
            WHERE table_schema=DATABASE() AND table_name='rsu_annex_evaluation' AND column_name=%s""",
            (column_name,),
        )
        if not exists:
            execute(f"ALTER TABLE rsu_annex_evaluation ADD COLUMN {column_name} {definition}")

    # Los administrativos sin un rol explícito quedan como administrativos
    # generales. Los administradores RSU existentes conservan su asignación.
    execute(
        """
        INSERT IGNORE INTO rsu_user_system_role
        (user_system_role_id,usuario_id,system_role_id,facultad,activo)
        SELECT UUID(),u.usuario_id,sr.system_role_id,NULL,1
        FROM usuario u
        INNER JOIN administrativo a ON a.persona_id=u.persona_id
        INNER JOIN rsu_system_role sr ON sr.codigo='administrativo_normal'
        WHERE u.activo=1
          AND NOT EXISTS (
              SELECT 1
              FROM rsu_user_system_role existing
              WHERE existing.usuario_id=u.usuario_id AND existing.activo=1
          )
        """
    )

