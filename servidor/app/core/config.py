from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "RSU UNAB API"
    app_env: str = "local"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    api_prefix: str = "/api/v1"
    cors_origins: str = "*"

    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "rsu_unab"
    db_ssl_ca: str = ""  # Optional absolute CA path for TLS-verified remote MySQL

    secret_key: str = "configura-una-clave-aleatoria-en-el-env"
    access_token_expire_minutes: int = 480
    demo_allow_placeholder_passwords: bool = False
    rsu_enable_test_advance: bool = False  # SOLO instalaciones locales de prueba, nunca producción

    upload_dir: Path = Path("uploads")
    private_storage_dir: Path = Path("private_storage")
    # Ruta opcional al ejecutable soffice.exe. Puede configurarse en .env.
    libreoffice_path: str = ""
    # Orden opcional: word,libreoffice o libreoffice,word.
    pdf_converter_order: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origin_list(self) -> List[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    (settings.upload_dir / "documents").mkdir(parents=True, exist_ok=True)
    settings.private_storage_dir.mkdir(parents=True, exist_ok=True)
    return settings
