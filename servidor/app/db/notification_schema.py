from __future__ import annotations

from app.db.mysql import execute


def ensure_notification_schema() -> None:
    """Crea el buzón persistente de notificaciones sin alterar tablas existentes."""
    execute(
        """
        CREATE TABLE IF NOT EXISTS rsu_notification (
          notificacion_id CHAR(36) NOT NULL,
          usuario_id CHAR(36) NOT NULL,
          tipo VARCHAR(70) NOT NULL,
          titulo VARCHAR(180) NOT NULL,
          mensaje TEXT NOT NULL,
          modulo VARCHAR(70) NOT NULL DEFAULT 'Sistema',
          prioridad VARCHAR(20) NOT NULL DEFAULT 'informativa',
          entidad_tipo VARCHAR(50) NULL,
          entidad_id CHAR(36) NULL,
          proyecto_id CHAR(36) NULL,
          anexo_numero INT NULL,
          datos_json LONGTEXT NULL,
          clave_evento VARCHAR(220) NULL,
          generada_por VARCHAR(160) NOT NULL DEFAULT 'Sistema RSU',
          canal VARCHAR(30) NOT NULL DEFAULT 'Aplicación',
          leida TINYINT(1) NOT NULL DEFAULT 0,
          importante TINYINT(1) NOT NULL DEFAULT 0,
          archivada TINYINT(1) NOT NULL DEFAULT 0,
          fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          fecha_lectura DATETIME NULL,
          fecha_archivo DATETIME NULL,
          PRIMARY KEY (notificacion_id),
          UNIQUE KEY uq_rsu_notification_event_user (usuario_id, clave_evento),
          KEY idx_rsu_notification_user_date (usuario_id, fecha_creacion),
          KEY idx_rsu_notification_user_read (usuario_id, leida, archivada),
          KEY idx_rsu_notification_project (proyecto_id, anexo_numero),
          CONSTRAINT fk_rsu_notification_user
            FOREIGN KEY (usuario_id) REFERENCES usuario(usuario_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
