# RSU UNAB v7 · Guía de GitHub y Render (entorno de prueba)

## Preparación
1. No subas `.env`, volcados con personas reales, imágenes de firmas ni archivos de `uploads/` o `private_storage/`.
2. El paquete incluye `database/esquema_sin_datos.sql`, que **no crea usuarios de prueba**. Carga exclusivamente datos ficticios y contraseñas bcrypt.
3. Utiliza una instancia MySQL de pruebas accesible desde Render. No uses `DB_HOST=localhost`.
4. Si tu proveedor exige TLS, suministra su CA y configura `DB_SSL_CA` con la ruta **dentro del servidor**; adaptar provisión del certificado como secreto. Sin CA configurada, este proyecto no fuerza verificación TLS.
5. Ejecuta una copia separada de MySQL y verifica que el servidor pueda hacer las migraciones de esquema de arranque. No otorgues permisos globales.

## GitHub
- Crea un repositorio **privado** y sube el contenido de esta carpeta desde su raíz.
- Ejecuta `git status` y revisa que no incluya archivos `.env`, volcado de la universidad, PDFs reales, firmas ni contraseñas.
- Ejemplo: `git init`, `git add .`, `git commit -m "RSU demo v7"`, `git branch -M main`, `git remote add origin URL_DE_TU_REPO`, `git push -u origin main`.

## Render
1. En Render, configura un Blueprint apuntando al repositorio para leer `render.yaml`, o crea un Web Service manual con Root Directory `servidor`.
2. Runtime Python, plan Free, build `pip install -r requirements.txt` y start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
3. Variables obligatorias: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `SECRET_KEY`. En Render no adjuntes `.env`; ingresa valores en Environment.
4. `APP_ENV=production`, `RSU_ENABLE_TEST_ADVANCE=false`, `DEMO_ALLOW_PLACEHOLDER_PASSWORDS=false`.
5. Tras deploy, visita `https://NOMBRE.onrender.com/app/`. No cambies frontend para usar otro dominio: FastAPI sirve la interfaz con el mismo origen.

## Advertencias que bloquean usar datos reales
- Render Free tiene almacenamiento **efímero**: los PDFs y firmas guardados en `uploads` y `private_storage` desaparecen en reinicios/redeploys. Migrar estos archivos a almacenamiento privado persistente antes de hacer pruebas con archivos importantes.
- `app.main` ejecuta `ensure_*_schema()` al iniciar; el usuario de DB requiere permisos DDL apropiados en la base de pruebas durante la preparación. En operación real, usar migraciones y reducir permisos.
- No se incluyó proveedor MySQL. El alojamiento de FastAPI es gratuito, pero MySQL remoto persistente puede exigir pago o tener límites temporales.
- El test de acceso y autorización por roles debe realizarse de punta a punta antes de publicar información institucional.
- Se debe verificar conversión de DOCX a PDF: LibreOffice no viene preinstalado necesariamente en Render Free.
