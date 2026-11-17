# Actualización v6 — Cronograma semanal y Anexo 5

## Cambios

- Formulario de **Nuevo proyecto RSU**: 16 recuadros identificados «Semana 1» ... «Semana 16», en el campo «Cronograma» del Anexo 2.
- Formulario para **Corregir anexos 1 y 2**: recupera las actividades previamente guardadas en sus semanas correspondientes.
- Formulario **Presentar informe de avance (Anexo 5)**: ocho recuadros «Semana 1» ... «Semana 8», para registrar actividades efectivamente realizadas.
- Los recuadros convierten los datos a los formatos originales del servidor: `[{"semana":1,"actividad":"..."}]` para cronograma y `{"1":"..."}` para el Anexo 5; el usuario no tiene que escribir JSON.
- Se requiere completar al menos una semana por formulario. Los demás recuadros pueden quedar vacíos; las semanas vacías no se envían al servidor.
- El documento oficial vigente del Anexo 5 incluye una fila para la semana 9. Esta actualización **no altera esa plantilla** y esa fila queda vacía si no se registró información para la semana 9.
- No cambia MySQL, no modifica los endpoints existentes y no altera las reglas de aprobación y revisión.

## Instalación en Windows

1. Haz copia de seguridad de la carpeta `servidor/web`.
2. Detén Uvicorn con Ctrl+C.
3. Descomprime el archivo del parche sobre la carpeta que **contiene** `RSU_UNAB_WEB`, aceptando reemplazar `servidor/web/main.js`, `servidor/web/workflow-v4.css` y `servidor/web/index.html`.
4. Ejecuta de nuevo `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload` desde `RSU_UNAB_WEB/servidor` (con su entorno virtual activado).
5. Abre `http://127.0.0.1:8000/app/` y presiona Ctrl+F5.

## Pruebas recomendadas

1. Inicia sesión como responsable, crea un proyecto RSU y comprueba que existen 16 campos y que se guardan las semanas indicadas.
2. Abre «Corregir anexos 1 y 2» en un expediente con observaciones: las semanas previamente registradas deben aparecer precargadas.
3. Desde un proyecto habilitado para avance, presenta el Anexo 5 y confirma que aparecen exactamente 8 recuadros y no un editor JSON.
4. Verifica la información resultante en el expediente y en la descarga del Anexo 2 y Anexo 5.

**Las verificaciones automáticas de la web emplearon datos simulados. Es necesario probar con MySQL real antes del uso institucional.**
