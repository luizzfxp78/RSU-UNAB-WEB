# SISTEMA RSU UNAB — Versión web

**Migración de la interfaz de escritorio (PySide6) a una aplicación web con FastAPI y MySQL.**

Este paquete reutiliza **el servidor FastAPI y las entidades MySQL del proyecto entregado**, incorpora una interfaz web adaptable para navegador y mantiene los endpoints y servicios de negocio que gestionan convocatorias, proyectos RSU, actividades específicas, voluntariado, evaluaciones y certificaciones. La interfaz ya no requiere instalar PySide6 ni desplegar un ejecutable en cada computadora.

## Actualización de interfaz v2.1 (17 de noviembre de 2026)

La interfaz web utiliza ahora la paleta y la estructura visual del tablero
PySide6 original (`dashboard.qss`): navegación lateral blanca, selección azul,
tarjetas de resumen, tablas y formularios uniformes. Se agregaron nombres
legibles en lugar de identificadores MySQL en vistas, fichas y selectores; los
IDs se conservan exclusivamente para las llamadas a la API.

Para actualizar una instalación existente basta reemplazar los archivos de
`servidor/web` incluidos en `RSU_UNAB_WEB_actualizacion_interfaz_v2.zip`.
**No se debe modificar el archivo `.env` ni la base de datos.** Tras reiniciar
FastAPI, fuerza la recarga del navegador con `Ctrl + F5`.

No se verificó esta actualización contra una base MySQL real; la validación
realizada corresponde a sintaxis JS, pruebas de interfaz con respuestas API
simuladas y revisión estática. Las referencias no autorizadas o no disponibles
no se muestran como UUID, sino como "Referencia no disponible".

## 1. Estructura

```
RSU_UNAB_WEB/
  servidor/
    app/                         # Backend FastAPI original, adaptado para web
    web/index.html                # Inicio de sesión y aplicación web
    web/main.js                   # Navegación, API, formularios y flujos
    web/styles.css                # Diseño adaptable
    web/assets/                   # Imágenes institucionales
    official_annex_templates/    # Plantillas DOCX ya existentes
    uploads/                     # Carpeta local, inicialmente vacía
    private_storage/             # Carpeta privada, inicialmente vacía
    .env.example                 # Configuración SIN credenciales reales
    requirements.txt
  database/
    esquema_sin_datos.sql         # Sólo definición SQL, SIN registros personales
  iniciar_windows.bat
  iniciar_linux.sh
  README.md
```

## 2. Requisitos

- Windows 10/11 o Linux; Python 3.11 o posterior.
- MySQL 8 (se recomienda usar la misma base **rsu_unab** del servidor original).
- LibreOffice (recomendado) o Microsoft Word en Windows para convertir anexos DOCX a PDF.
- Navegador moderno: Chrome, Firefox o Edge.
- Conectividad de red entre el navegador y el equipo donde se ejecuta FastAPI.

**No hacen falta Node.js, npm, React ni PySide6.** El frontend es HTML, CSS y JavaScript estándar servido por FastAPI.

## 3. Instalación en Windows

1. Descomprime el ZIP en una carpeta estable (por ejemplo `C:\RSU_UNAB_WEB`).
2. Entra a `servidor` y **copia `.env.example` como `.env`**. Nunca incluyas este archivo en repositorios o entregas públicas.
3. Configura `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD` y `DB_NAME` con tus datos MySQL. Usa el usuario real que tenga permisos sobre la base `rsu_unab`.
4. Genera una clave de aplicación con `python -c "import secrets; print(secrets.token_urlsafe(48))"` y escríbela en `SECRET_KEY`.
5. Conserva la base de datos existente. Haz primero un respaldo; **no importes `esquema_sin_datos.sql` sobre la base existente porque contiene sentencias de estructura y puede reemplazar tablas**.
6. Ejecuta `iniciar_windows.bat` desde el directorio principal. El script crea `.venv`, instala dependencias y ejecuta FastAPI.
7. Abre **http://127.0.0.1:8000/app/** en el navegador del servidor o **http://IP_DEL_SERVIDOR:8000/app/** desde otra computadora de la misma red (si el cortafuegos lo permite).
8. Inicia sesión con una cuenta existente y habilitada de la base MySQL. No se incluyen usuarios ni contraseñas de prueba en el paquete.

Para Linux, utiliza `chmod +x iniciar_linux.sh && ./iniciar_linux.sh`. El archivo `.env` debe configurarse antes de iniciar.

**Alternativa manual:**

```powershell
cd servidor
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

La documentación interactiva de los endpoints seguirá disponible en **http://127.0.0.1:8000/docs**.

## 4. Módulos de la nueva interfaz

| Módulo web | Implementación y operaciones |
|---|---|
| Inicio | Resumen de expedientes, indicadores, plazos y accesos rápidos |
| Convocatorias | Consultar, registrar, modificar, activar y cerrar convocatorias |
| Proyectos RSU | Crear propuesta, consultar expediente, anexos 1–7, evaluación, aprobación, ejecución, informes y certificación |
| Actividades específicas | Crear plan, revisión Anexo 9, ejecución, Anexo 10, cierre y certificación |
| Voluntariado | Crear plan, registro Anexo 12, evaluaciones, ejecución, informe Anexo 13 y certificación |
| Evaluaciones | Indicadores y estado de expedientes; revisión desde la ficha de cada expediente |
| Reportes | Panel institucional y descarga de Excel |
| Horas RSU | Indicador global de horas-persona; acceso a expedientes para el detalle |
| Notificaciones | Bandejas, lectura, marcación y archivo de notificaciones |
| Gestión de personal | Consulta, búsqueda, registro y edición de personas (administración) |
| Roles y autoridades | Roles y asignaciones institucionales (administración) |
| Configuración | Catálogos académicos, líneas, modalidades y límites (administración) |
| Plantillas y anexos | Repositorio, subida y descarga de formatos institucionales |
| Auditoría | Historial de operaciones (administración) |
| Herramientas avanzadas | Formularios generados desde los esquemas OpenAPI de la API existente |

Los formularios se construyen con los **esquemas Pydantic/OpenAPI** del backend: muestran campos obligatorios, listas, fechas, selección de catálogos, personas, archivos para firmas y evidencias, y formularios de evaluación. La validación final permanece en FastAPI.

**Importante:** cada acción continúa sujeta a los permisos y estados configurados en el servidor. La interfaz no sustituye las decisiones de aprobación del personal autorizado ni los requisitos institucionales.

## 5. Base normativa utilizada

Reglamento RSU de la UNAB, Resolución de Comisión Organizadora N.º 494-2026-UNAB (16/04/2026):

- Art. 14: modalidades y mínimos (proyectos RSU 32 horas; actividades específicas y voluntariado 16 horas).
- Art. 21: líneas de acción.
- Arts. 22–24: proyectos RSU, equipos, convocatoria, evaluación, aprobación, seguimiento, cierre y certificación.
- Arts. 25–28: actividades específicas y de proyección social.
- Arts. 29–30: voluntariado.
- Anexos 1 al 14: formularios, fichas de evaluación, informes y certificados.

Se preserva la lógica reglamentaria existente del servidor (etapas, anexos, registro de observaciones y resolución). El frontend muestra las etapas, pero la **verificación de plazos, roles y cantidades debe mantenerse y probarse en el backend**.

## 6. Cambios respecto del servidor entregado

- FastAPI ahora sirve `/app/` desde `servidor/web/` y redirige `/` a la plataforma.
- Se eliminó la publicación indiscriminada de `/uploads` como carpeta estática pública.
- Las rutas antiguas de `documentos` se limitaron a cuentas administrativas, mientras que los módulos de anexos del flujo institucional conservan sus endpoints autenticados originales.
- Los endpoints de consulta general de personas se restringieron a administradores; la búsqueda para seleccionar participantes usa `/flujo-rsu/personas-seleccion`.
- La validación básica del número de horas a certificar exige 32 en proyectos RSU y 16 en voluntariado. **Debe verificarse también la acreditación real de cada participante en los servicios de negocio.**
- Se excluyeron datos personales, firmas, documentos privados y credenciales `.env` del ZIP de entrega.

## 7. Seguridad y despliegue

- Este proyecto está preparado para ejecutarse localmente o en una red interna. **Para exposición pública, primero configura HTTPS, firewall, proxy inverso, política de copias de seguridad, límites de carga, permisos de almacenamiento y controles de autorización por objeto.**
- No uses `SECRET_KEY` de ejemplo, `DB_PASSWORD` de ejemplo ni el modo de contraseñas placeholder.
- Mantén `private_storage` fuera de cualquier carpeta estática del servidor. Las firmas institucionales son información sensible.
- Los tokens de la web se guardan en `sessionStorage` y se borran al cerrar sesión. Antes de producción conviene evaluar sesión mediante cookie HttpOnly, CSRF y una auditoría de seguridad integral.
- Los archivos entregados del backend mantienen gran parte de la lógica heredada: no debe asumirse que todas las rutas históricas tienen las mismas verificaciones que los flujos nuevos.

## 8. Estado de las pruebas y limitaciones

**Comprobado en esta entrega:** compilación sintáctica del código Python y JavaScript; inspección del esquema OpenAPI (124 rutas, 69 esquemas), y prueba de respuesta HTTP para el inicio web y archivos estáticos mediante cliente de prueba sin conectar la base de datos.

**No comprobado todavía:** sesión real, transacciones MySQL, permisos de cada rol, envío de correos Gmail/SMTP, funcionamiento interactivo completo en navegador conectado a MySQL, conversión de PDF con la instalación local del usuario, ni generación efectiva de todos los anexos con datos reales. No se creó un sistema de envío de correos Gmail porque no existe una integración comprobada en el servidor suministrado.

**Aspectos de interfaz con soporte inicial, no equivalencia funcional total:** visualización de horas individuales acreditadas, formularios altamente especializados del voluntariado (seguros, tutor, discapacidad y huella) y manejo visual avanzado de versiones documentales. Los endpoints y modelos permanecen accesibles, incluso desde herramientas avanzadas, pero requieren pruebas de aceptación para confirmar paridad completa.

### Lista mínima de pruebas de aceptación

- [ ] Director/administrador: gestión de personal y roles visibles y operativas.
- [ ] Convocatoria creada, activada y visible en el selector del responsable.
- [ ] Docente y estudiante: registrar proyecto RSU, actividad específica y voluntariado con convocatorias válidas.
- [ ] Evaluador asignado: revisar ficha y registrar observaciones; responsable subsana.
- [ ] Aprobar propuesta y generar el documento oficial según el flujo.
- [ ] Presentar informes, validar cierre y acreditar las horas reales.
- [ ] Emitir y descargar los certificados sin duplicidades.
- [ ] Descargar anexos PDF, firmas y evidencias sólo con permisos apropiados.
- [ ] Generar reportes Excel por periodo y facultad.
- [ ] Verificar fechas hábiles, límites de participantes, horas mínimas y auditoría.

## 9. Sobre la base de datos suministrada

El ZIP del usuario incluía registros reales o de trabajo, correos y potencialmente información personal. Por seguridad **NO se redistribuye ese contenido**. El archivo `database/esquema_sin_datos.sql` es sólo una referencia estructural, no crea cuentas con las que iniciar sesión. Para la migración se recomienda continuar utilizando **la misma base MySQL ya existente**, con una copia de seguridad previa y los permisos apropiados.

## 10. Próximas mejoras recomendadas

1. Diseñar formularios guiados completos, con validación de cada requisito reglamentario, para los Anexos 1–14.
2. Fortalecer autorizaciones por expediente, facultad, persona y etapa en todos los endpoints históricos.
3. Desarrollar la consulta individual de horas **acreditadas**, separadas de horas programadas y realizadas.
4. Implementar pruebas automáticas FastAPI + MySQL de los flujos completos para cada perfil.
5. Incorporar HTTPS, protección anti-CSRF en caso de migrar a cookies y un servicio de correo institucional si se aprueba su uso.
