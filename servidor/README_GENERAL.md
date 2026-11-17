# Servidor RSU UNAB — Guía general V93

Servidor FastAPI del Sistema de Gestión de Responsabilidad Social Universitaria de la Universidad Nacional de Barranca.

## Arquitectura

```text
Cliente PySide6 → FastAPI REST/WebSocket → MySQL `rsu_unab`
```

El servidor centraliza autenticación, autorización por roles, proyectos RSU, actividades específicas, voluntariado, convocatorias, evaluaciones, notificaciones, reportes, auditoría, anexos, memorándums y certificados.

## Requisitos

- Python 3.11 o superior.
- MySQL con la base `rsu_unab`.
- Microsoft Word o LibreOffice para convertir DOCX a PDF. Existe un generador PDF de contingencia para documentos compatibles.

## Instalación

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Copia `.env.example` como `.env` y reemplaza las credenciales y claves de ejemplo.

Variables principales:

```env
APP_HOST=0.0.0.0
APP_PORT=8000
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=usuario_rsu
DB_PASSWORD=CLAVE_MYSQL
DB_NAME=rsu_unab
SECRET_KEY=CLAVE_ALEATORIA_LARGA
PRIVATE_STORAGE_DIR=private_storage
PDF_CONVERTER_ORDER=word,libreoffice
```

No uses en producción el usuario `root`, las contraseñas de ejemplo ni `CORS_ORIGINS=*`.

## Ejecución

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

También puede utilizarse `run_server.bat` en Windows. La documentación interactiva se encuentra en:

```text
http://127.0.0.1:8000/docs
```

## Base de datos

Los scripts SQL incluidos agregan estructuras complementarias para roles, autoridades, notificaciones, ciclo de proyectos, actividades específicas, voluntariado, auditoría y plantillas de anexos. Antes de actualizar una instalación institucional:

1. realiza una copia de seguridad;
2. aplica las migraciones pendientes en orden;
3. inicia FastAPI y revisa el endpoint `/health` y los logs;
4. prueba con cuentas de cada rol.

## Seguridad y codificación

- Código y archivos de configuración en UTF-8.
- Conexión MySQL con `utf8mb4` y Unicode.
- Contraseñas almacenadas como hash bcrypt; nunca en texto plano.
- Toda contraseña nueva o modificada exige mínimo 8 caracteres, una mayúscula, un número y un signo especial.
- JWT para las sesiones y validación de permisos en los endpoints.
- Anexos, firmas, huellas, memorándums y certificados se almacenan en `private_storage`, fuera de las rutas estáticas.
- Las plantillas PDF cargadas por administradores RSU deben pesar menos de 10 MB.

Para revisar hashes heredados:

```powershell
python VERIFICAR_HASHES_USUARIOS_V92.py
```

## Conversión de documentos

En Windows se intenta Microsoft Word antes que LibreOffice. LibreOffice utiliza perfiles temporales aislados para evitar bloqueos de configuración. Herramientas disponibles:

```text
DIAGNOSTICAR_CONVERSOR_PDF.bat
LIMPIAR_LIBREOFFICE_BLOQUEADO.bat
```

Si se configura una ruta manual:

```env
LIBREOFFICE_PATH=C:\Program Files\LibreOffice\program\soffice.com
```

## Directorios principales

```text
app/                       Código FastAPI
app/api/                   Endpoints REST y WebSocket
app/services/              Servicios de negocio y documentos
official_annex_templates/  Plantillas DOCX institucionales
private_storage/           Archivos privados generados
uploads/                   Solo archivos expresamente públicos
```

## Verificación mínima después de actualizar

- Inicio de sesión y política de contraseñas.
- Dashboard por ciclo, facultad y perfil.
- Creación, evaluación y subsanación de expedientes.
- Generación y descarga de anexos, memorándums y certificados.
- Notificaciones y redirecciones por módulo.
- Exportación de reportes Excel.
- Auditoría de registros, ediciones y eliminaciones.
