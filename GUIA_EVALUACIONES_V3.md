# RSU UNAB · Sistema de evaluación y seguimiento por etapas (web v3)

## Alcance implementado

Esta actualización conserva FastAPI + MySQL y el esquema original; no ejecuta migraciones SQL ni modifica tablas. La nueva interfaz web incluye:

- Bandeja **Evaluaciones** que consolida los expedientes en revisión o subsanación de proyectos, actividades específicas y voluntariados.
- Evaluación estructurada con fichas reglamentarias (Anexos 4, 7 y 9; revisión del informe final de actividades Anexo 10). Voluntariado utiliza el resultado aprobado/observado del flujo existente.
- Validación de observaciones: cada criterio marcado NO en los proyectos requiere una explicación concreta. Las observaciones quedan persistidas por los endpoints originales; el envío dispara las notificaciones que ya tenía FastAPI.
- Subsanación en tres días hábiles calculados por el backend mediante el calendario laboral. La página presenta la fecha límite recibida del servidor y el historial de comentarios.
- Botones y documentos habilitados de acuerdo con la etapa y rol: no se muestran operaciones ajenas al estado actual y los PDFs figuran bloqueados hasta ser presentados o emitidos.
- Los documentos que se habilitan según el proceso incluyen Anexos 1–7 para proyectos, 1/8–10 para actividades, 1/11–13 para voluntariados, memorándums y certificados.
- Refuerzo del endpoint PDF de las fichas de evaluación 4 y 7: antes de una evaluación solo se autoriza una vista preliminar al revisor asignado.
- En el flujo de voluntariado, las correcciones iniciales y finales se rechazan en el servidor cuando vence el plazo de subsanación (HTTP 409). Si se corrige el plan inicial, la fecha límite se conserva también mientras se completan nuevamente las fichas del Anexo 12. Una nueva evaluación o excepción debe resolverse institucionalmente.

## Recorridos de trabajo

**Proyectos de RSU:** el responsable registra la propuesta (Anexos 1 y 2), presenta el equipo en el Anexo 3 y entonces se asignan evaluaciones. Los revisores registrados (RSU y, cuando corresponde, Dirección de Facultad) evalúan con el Anexo 4. Si hay observaciones, se informa el vencimiento y se habilita la corrección de la propuesta. Cuando las aprobaciones exigidas están completas se emite el memorándum, se gestiona el acto resolutivo, comienza la ejecución y se activan los informes de las semanas 9 y 16 (Anexos 5 y 6). El Anexo 7 registra la evaluación final antes de certificación.

**Actividades específicas:** la creación del plan (Anexo 8) lo somete a evaluación (Anexo 9); ante observaciones se corrige dentro del plazo. Con aprobaciones y memorándum se inicia ejecución, se presenta Anexo 10 y se evalúa el cierre. Después corresponde certificar.

**Voluntariado:** se crea el plan (Anexo 11), los colaboradores completan sus fichas (Anexo 12) y se remite para evaluación. Un resultado observado devuelve el expediente al responsable, quien corrige y vuelve a presentar. Aprobada la fase inicial, se inicia la ejecución y posteriormente se entrega y evalúa el Anexo 13. La aprobación final habilita la certificación.

**Importante:** las aprobaciones no se concentran automáticamente en un administrador cuando el flujo original requiere dos revisores institucionales. La aplicación respeta las asignaciones vigentes; no se han eliminado las autorizaciones ni las restricciones del Reglamento de RSU.

## Actualización en Windows sin perder datos

1. **Haz copia de seguridad** de la carpeta actual `RSU_UNAB_WEB` y de la base MySQL antes de instalar.
2. Detén FastAPI (`Ctrl+C` en la ventana de ejecución).
3. Descomprime `RSU_UNAB_WEB_actualizacion_evaluaciones_v3.zip` en la carpeta que contiene `RSU_UNAB_WEB` (por ejemplo, `C:\Users\luisr\Desktop\rsu_web`). Acepta reemplazar archivos.
4. **No sustituyas el archivo `.env`** ni la base MySQL. El ZIP no los contiene.
5. Ejecuta `RSU_UNAB_WEB\iniciar_windows.bat`.
6. Abre `http://127.0.0.1:8000/app/` y presiona `Ctrl+F5` para borrar los recursos web en caché.
7. Ingresa con una cuenta autorizada (administrador RSU o revisor de facultad) y selecciona **Evaluaciones**.

## Pruebas recomendadas con MySQL

Realiza las pruebas con registros de prueba, no con expedientes oficiales:

1. Crear una convocatoria vigente y una propuesta de cada modalidad.
2. Completar los anexos previos que requiere cada flujo (en particular Anexo 3 del proyecto y Anexo 12 de voluntariado).
3. Revisar la propuesta con la cuenta RSU o el revisor asignado; registrar una observación y comprobar estado, notificación e historial.
4. Acceder con la cuenta responsable, visualizar la fecha límite y corregir. Comprobar que se reasigne la revisión.
5. Aprobar el expediente con todos los revisores necesarios y confirmar que se habiliten memorándum y las acciones de ejecución.
6. Presentar informes y realizar la revisión final; comprobar disponibilidad progresiva de anexos y certificados.
7. Confirmar que usuarios sin permiso reciben HTTP 403 al llamar directamente a endpoints de evaluación; el front-end no sustituye el control del servidor.
8. Comprobar vencimiento con fechas de prueba y `calendario_laboral`; un plazo vencido de voluntariado debe impedir la corrección mediante HTTP 409.

## Verificaciones realizadas

- Comprobación de sintaxis de JavaScript mediante `node --check`.
- Compilación sintáctica de todos los archivos Python del servidor.
- Pruebas de navegación en Chromium/Playwright con datos simulados para evaluaciones de los tres tipos de expediente, observaciones, estado de subsanación y disponibilidad de PDFs.

No se ha ejecutado una prueba de integración contra la instancia de MySQL del usuario. En este entorno tampoco se comprobó la importación completa de FastAPI por faltar dependencias locales (por ejemplo `bcrypt`).
