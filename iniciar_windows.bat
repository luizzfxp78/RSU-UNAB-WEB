@echo off
setlocal
cd /d "%~dp0servidor"
if not exist ".env" (
  echo.
  echo [RSU UNAB] Falta configurar el acceso a MySQL.
  echo Copia servidor\.env.example como servidor\.env, edita los datos de MySQL y SECRET_KEY.
  echo No incluyas el archivo .env al compartir este proyecto.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Creando entorno virtual...
  py -3 -m venv .venv
  if errorlevel 1 python -m venv .venv
)
call ".venv\Scripts\activate.bat"
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo No se pudieron instalar las dependencias.
  pause
  exit /b 1
)
echo.
echo RSU UNAB WEB: http://127.0.0.1:8000/app/
echo Documentacion FastAPI: http://127.0.0.1:8000/docs
echo.
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
