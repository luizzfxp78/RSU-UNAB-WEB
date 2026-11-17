# Actualización v5: habilitar Anexo 5 desde evaluación inicial

La actualización coloca el botón **Habilitar Anexo 5** dentro del expediente de Proyectos RSU y en el formulario **Evaluación inicial**, exclusivamente para `administrador_rsu`. Su objetivo es habilitar el envío del informe de avance por el responsable.

## Modo institucional

- Si el expediente REAL ya está en estado `en_ejecucion` o `monitoreo` y la etapa `ejecucion`, el botón utiliza el endpoint existente `POST /api/v1/ciclo-rsu/proyectos/{project_id}/habilitar-anexo-5`.
- El backend registra la apertura anticipada, deja constancia en el historial y avisa al responsable.
- Si el proyecto aún está en evaluación inicial, el botón se muestra **deshabilitado**, con la explicación correspondiente: no es correcto omitir las evaluaciones del Anexo 4, el memorándum, el acto resolutivo y el inicio de la ejecución.
- Una vez habilitado, el responsable verá **Presentar informe de avance (Anexo 5)** dentro de su expediente.

## Modo de ensayo

- Para expedientes cuyo título empieza por `[PRUEBA]`, el botón de la evaluación inicial utiliza el atajo de simulación existente. Solo funciona si el administrador activó `APP_ENV=local` y `RSU_ENABLE_TEST_ADVANCE=true` en una **base de datos de pruebas separada**.
- Se solicita un motivo de al menos 10 caracteres y confirmación explícita.
- La simulación habilita la etapa de ejecución y el Anexo 5, pero no equivale a aprobación ni permite certificados oficiales.

## Instalación en Windows

1. Realiza una copia de seguridad de la carpeta `servidor/web`.
2. Detén FastAPI.
3. Descomprime `RSU_UNAB_WEB_parche_anexo5_v5.zip` en la carpeta que contiene `RSU_UNAB_WEB`, aceptando reemplazar los dos archivos dentro de `servidor/web`.
4. No cambies `.env` ni importes una base de datos SQL.
5. Reinicia Uvicorn, abre `/app/` y presiona Ctrl+F5.

## Verificación manual

1. Ingresa como `administrador_rsu`, abre **Proyectos RSU** y un expediente en evaluación inicial.
2. Abre **Evaluar propuesta**: encontrarás el control de Anexo 5 junto al formulario.
3. En un proyecto real sin aprobar el control debe aparecer deshabilitado; en uno `[PRUEBA]` con el servidor de pruebas habilitado puede simularse el salto.
4. Ingresa como responsable del proyecto después de habilitar: debe aparecer **Presentar informe de avance (Anexo 5)**, y el documento PDF solo estará disponible después de presentarlo.

Esta versión no modifica la estructura de MySQL ni los permisos del servidor original; se apoya en sus endpoints existentes.
