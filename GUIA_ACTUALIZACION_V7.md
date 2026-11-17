# RSU UNAB v7 - Semanas 1 a 16 en Anexo 5 + observaciones por inciso

## Cambios

1. El formulario de **Presentar informe de avance (Anexo 5)** incluye **16 recuadros**, identificados de la semana 1 a la semana 16. Los valores se envían en el mismo diccionario que ya acepta FastAPI, sin modificar MySQL.
2. El **Anexo 5 oficial** del reglamento tiene semanas 1 a 9. Para no modificarlo, la generación del PDF mantiene el formato y agrega una **hoja complementaria identificada** para las semanas 10 a 16 cuando estas tienen datos, antes de adjuntar evidencias.
3. El responsable puede visualizar los **incisos específicos observados** (Anexo 4 o 7 para proyectos y Anexo 9 o 10 para actividades), el comentario y el revisor.
4. En el formulario **Corregir propuesta / plan / informe final**, se muestran las observaciones sobre los **campos correspondientes** y enlaces «Ir al campo observado» para encontrar el dato que debe corregirse.
5. Para el **voluntariado**, el evaluador puede seleccionar uno o varios incisos y redactar una observación por inciso. Se guardan como texto estructurado en el campo existente `observaciones`, sin migración SQL.
6. Los plazos, autenticación y autorización para evaluar/subsanar siguen validados por el servidor original.

## Instalación v6 -> v7

1. Detén FastAPI (`Ctrl + C`).
2. Guarda una copia de tu carpeta `servidor/web` y del archivo `servidor/app/services/official_annex_pdf.py`.
3. Extrae el ZIP del parche en la carpeta que contiene `RSU_UNAB_WEB`, conservando la jerarquía de carpetas y aceptando reemplazar los archivos indicados.
4. Conserva `.env`, los documentos privados y tu base de datos MySQL. **No es necesario ejecutar SQL.**
5. Desde `RSU_UNAB_WEB/servidor` reinicia `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload` con tu entorno virtual.
6. Abre `http://127.0.0.1:8000/app/` y fuerza actualización del navegador con `Ctrl + F5`.

## Pruebas sugeridas

- Presenta un Anexo 5 con texto en semanas 1, 9, 10, 16; verifica que las 16 semanas se envían al servidor y las semanas 10-16 figuran en la hoja complementaria al descargar el PDF.
- Como revisor, marca NO en «problemática» y escribe «Identificar población y alcance»; como responsable, abre el expediente observado, entra a «Corregir propuesta» y comprueba que el inciso y el campo «Problemática» aparecen resaltados con esa observación.
- Repite el procedimiento con «Plazo de ejecución» para actividades específicas y con «Problemas» para voluntariado.
- Verifica que no se permita al responsable corregir fuera de la etapa/plazo autorizado por FastAPI.

**Limitación:** se verificó el código y la interfaz con datos simulados. Falta ejecutar pruebas integrales en tu instancia MySQL antes de su despliegue institucional.
