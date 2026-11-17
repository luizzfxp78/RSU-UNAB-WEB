from __future__ import annotations

from app.db.mysql import execute


def ensure_governance_schema() -> None:
    """Crea las estructuras de auditoría y plantillas de referencia de anexos."""
    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_audit_log (
          audit_id CHAR(36) NOT NULL,
          accion VARCHAR(24) NOT NULL,
          modulo VARCHAR(90) NOT NULL,
          entidad VARCHAR(120) NULL,
          recurso_id VARCHAR(120) NULL,
          metodo_http VARCHAR(10) NOT NULL,
          ruta VARCHAR(500) NOT NULL,
          detalle VARCHAR(500) NULL,
          estado_http SMALLINT NOT NULL,
          usuario_id CHAR(36) NULL,
          persona_id CHAR(36) NULL,
          usuario_responsable VARCHAR(180) NOT NULL DEFAULT 'Sistema',
          username VARCHAR(80) NULL,
          rol_sistema VARCHAR(80) NULL,
          direccion_ip VARCHAR(64) NULL,
          user_agent VARCHAR(300) NULL,
          fecha_hora DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
          PRIMARY KEY (audit_id),
          KEY idx_audit_datetime (fecha_hora),
          KEY idx_audit_action_date (accion, fecha_hora),
          KEY idx_audit_module_date (modulo, fecha_hora),
          KEY idx_audit_user_date (usuario_id, fecha_hora)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_annex_reference_template (
          template_id CHAR(36) NOT NULL,
          modulo VARCHAR(60) NOT NULL,
          codigo_anexo VARCHAR(40) NOT NULL,
          nombre VARCHAR(180) NOT NULL,
          descripcion VARCHAR(500) NULL,
          version INT NOT NULL DEFAULT 1,
          nombre_archivo_original VARCHAR(255) NOT NULL,
          ruta_archivo VARCHAR(500) NOT NULL,
          tamano_bytes BIGINT NOT NULL,
          sha256 CHAR(64) NOT NULL,
          mime_type VARCHAR(80) NOT NULL DEFAULT 'application/pdf',
          usuario_carga_id CHAR(36) NOT NULL,
          usuario_carga_nombre VARCHAR(180) NOT NULL,
          es_actual TINYINT(1) NOT NULL DEFAULT 1,
          activo TINYINT(1) NOT NULL DEFAULT 1,
          fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          fecha_actualizacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (template_id),
          UNIQUE KEY uq_annex_template_version (modulo, codigo_anexo, version),
          KEY idx_annex_template_current (modulo, codigo_anexo, es_actual, activo),
          KEY idx_annex_template_date (fecha_actualizacion)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
