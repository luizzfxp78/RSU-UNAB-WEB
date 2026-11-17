# RSU UNAB WEB — Actualización v4: etapas, evaluación, personas y firmas

## Cambios

1. **Botón Atrás:** todos los paneles de expediente, evaluación y formularios de operación incluyen navegación de regreso; «Cerrar» sigue disponible.
2. **Estado y avance:** el expediente muestra etapas completadas (✓), etapa actual y pendientes. La etapa que sigue solo se habilita cuando las validaciones y revisiones del servidor lo permiten.
3. **Evaluación contextual:** los criterios de los Anexos 4, 7 y 9 aparecen con los datos enviados por el responsable, y opciones de «Cumple» / «No cumple» y observaciones. Los voluntariados usan el flujo de evaluación de Anexos 11/13 que implementa el servidor.
4. **Personas filtradas:** el buscador permite localizar participantes por nombre o DNI. Cuando corresponde un docente asesor/corresponsable, el selector muestra solo docentes; cuando corresponde colaborador estudiante, solo estudiantes. Se respetan los límites de participantes y las reglas del backend.
5. **Firma en la misma tarjeta:** cada tarjeta de integrante permite dibujar la firma con ratón o pantalla táctil, o adjuntar imagen; la firma del responsable se captura en el mismo formulario. Las firmas se envían mediante los modelos del backend y se guardan en almacenamiento privado. En voluntariado se almacenan primero para asociarlas al Anexo 12.
6. **Pruebas de etapas:** sección de Administración exclusiva de `administrador_rsu`. Permite habilitar el siguiente informe en expedientes cuyo título comience por `[PRUEBA]`, únicamente en servidores locales con activación explícita. Se deja constancia en auditoría y se prohíbe emitir certificados oficiales de expedientes que usaron la simulación.

## Instalación en Windows (si ya instalaste v3)

1. Detén FastAPI (Ctrl+C). **Realiza un respaldo de tu base MySQL y carpeta de proyecto**.
2. Extrae el ZIP de actualización de forma que la carpeta `RSU_UNAB_WEB` del ZIP se superponga a `C:\Users\luisr\Desktop\rsu_web\RSU_UNAB_WEB`. Confirma **reemplazar los archivos** cuando Windows lo pregunte.
3. **No reemplaces ni borres** `servidor\.env`, `servidor\private_storage` o tus datos existentes. El ZIP de actualización no contiene esos datos.
4. Ejecuta `iniciar_windows.bat` desde la carpeta del proyecto y abre `http://127.0.0.1:8000/app/`.
5. Usa Ctrl + F5 para forzar recarga de estilos y scripts.
6. Realiza una prueba de proyecto, actividad y voluntariado con cada perfil; verifica el registro de firmas y cambios de etapa.

## Cómo habilitar «Pruebas de etapas» (únicamente en base de datos aislada)

1. **Nunca uses esta función sobre la base de datos institucional real.** Duplica la base de datos para ensayos y configura esa copia en `.env`.
2. Añade al `servidor\.env` del **entorno local de ensayo**:

   ```ini
   APP_ENV=local
   RSU_ENABLE_TEST_ADVANCE=true
   ```
3. Reinicia FastAPI. Ingresa con una cuenta con rol **`administrador_rsu`**.
4. Crea un proyecto cuyo título comience exactamente por `[PRUEBA]`, por ejemplo `[PRUEBA] Taller de salud`.
5. Abre **Administración → Pruebas de etapas**, selecciona el expediente y usa **Habilitar siguiente**. Escribe un motivo de al menos 10 caracteres.
6. Continúa presentando el siguiente anexo como responsable, sin esperar a las semanas reales del periodo académico.

La simulación **no aprueba oficialmente**, **no genera actas institucionales** y **bloquea la certificación** del expediente usado para pruebas. Cambia `RSU_ENABLE_TEST_ADVANCE=false` para deshabilitarla. El registro de la simulación se conserva para la auditoría.

## Observaciones importantes

- La **subsanación de observaciones** mantiene el plazo de **3 días hábiles** que gestiona el servidor. Esta actualización no amplía ese plazo.
- Una firma dibujada o una imagen de firma **no constituye automáticamente una firma electrónica o digital certificada**; para uso institucional, confirma autenticidad, consentimiento, autorización y requisitos legales con la UNAB. No dibujes ni subas firmas de terceros sin su autorización.
- Los filtros de rol se aplican en la interfaz; el backend continúa siendo el responsable de validar permisos y restricciones.
- Para usar el servicio en producción se requieren auditoría de autorizaciones y pruebas completas con usuarios, documentos y MySQL real.
- Estas pruebas se realizaron mediante simulación de respuestas API en navegador, comprobación de sintaxis JavaScript y compilación Python. **No incluyen una prueba integral conectada al MySQL de la universidad.**

## Archivos principales actualizados

- `servidor/web/main.js`
- `servidor/web/index.html`
- `servidor/web/workflow-v4.css`
- `servidor/app/api/v1/endpoints/test_advance.py` (nuevo)
- `servidor/app/api/v1/endpoints/project_lifecycle.py`
- `servidor/app/api/v1/endpoints/volunteer_workflow.py`
- `servidor/app/api/v1/endpoints/specific_activities.py`
- `servidor/app/api/v1/router.py`
- `servidor/app/api/deps.py`
- `servidor/app/schemas/project_lifecycle.py`
- `servidor/app/core/config.py`
- `servidor/.env.example` (no reemplaza `.env`)

No se modifica la estructura de las tablas ni se requiere reinstalar el esquema SQL.
