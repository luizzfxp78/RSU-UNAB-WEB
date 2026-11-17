from app.db.mysql import execute


def ensure_workflow_schema() -> None:
    """Crea tablas complementarias sin reemplazar el modelo original RSU."""
    execute(
        """
        CREATE TABLE IF NOT EXISTS proyecto_formulario (
          proyecto_id CHAR(36) NOT NULL,
          solicitud_tipo VARCHAR(120) NOT NULL DEFAULT 'Proyecto de RSU',
          domicilio_solicitante VARCHAR(255) NULL,
          condicion_solicitante VARCHAR(100) NULL,
          problematica LONGTEXT NULL,
          objetivo LONGTEXT NULL,
          metodologia LONGTEXT NULL,
          impacto_social LONGTEXT NULL,
          impacto_estudiantes LONGTEXT NULL,
          cronograma_json LONGTEXT NULL,
          presupuesto_json LONGTEXT NULL,
          revision_actual INT NOT NULL DEFAULT 1,
          fecha_actualizacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (proyecto_id),
          CONSTRAINT fk_proyecto_formulario_proyecto
            FOREIGN KEY (proyecto_id) REFERENCES proyecto(proyecto_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS evaluacion_proyecto (
          evaluacion_proyecto_id CHAR(36) NOT NULL,
          proyecto_id CHAR(36) NOT NULL,
          evaluador_persona_id CHAR(36) NOT NULL,
          resultado VARCHAR(30) NOT NULL,
          observaciones LONGTEXT NULL,
          revision INT NOT NULL DEFAULT 1,
          fecha_evaluacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (evaluacion_proyecto_id),
          KEY idx_eval_proyecto (proyecto_id),
          CONSTRAINT fk_eval_proyecto FOREIGN KEY (proyecto_id) REFERENCES proyecto(proyecto_id) ON DELETE CASCADE,
          CONSTRAINT fk_eval_persona FOREIGN KEY (evaluador_persona_id) REFERENCES persona(persona_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
