"""Backend configuration, read exclusively from the adjacent .env file."""

import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import URL


ROOT = Path(__file__).resolve().parent


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    VOXVAULT_PORT: int = Field(ge=1, le=65535)
    VOXVAULT_KEY_DIR_HOST: Path
    VOXVAULT_PUBLIC_URL: str
    POSTGRES_HOST: str
    POSTGRES_PORT: int = Field(ge=1, le=65535)
    POSTGRES_DB: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str = Field(repr=False)
    DATABASE_INIT_RETRIES: int = Field(gt=0)
    DATABASE_INIT_RETRY_DELAY_SECONDS: float = Field(ge=0, allow_inf_nan=False)
    WHISPERX_MODEL: str
    WHISPERX_DEVICE: Literal["cpu"]
    WHISPERX_COMPUTE_TYPE: str
    WHISPERX_BATCH_SIZE: int = Field(gt=0)
    WHISPERX_CPU_THREADS: int = Field(gt=0)
    WHISPERX_LANGUAGE_MIN_CONFIDENCE: float = Field(ge=0, le=1)
    WHISPERX_LANGUAGE_SAMPLE_SECONDS: float = Field(gt=0, le=30)
    VOXVAULT_MODEL_IDLE_SECONDS: float = Field(ge=0, allow_inf_nan=False)
    VOXVAULT_PROCESSING_DIR: Path
    HUGGINGFACE_TOKEN: str = Field(repr=False)

    @field_validator("VOXVAULT_KEY_DIR_HOST")
    @classmethod
    def absolute_key_directory(cls, value):
        if not value.is_absolute() or value == Path("/"):
            raise ValueError("Use an absolute key directory other than /")
        return value

    @property
    def private_key_path(self):
        return self.VOXVAULT_KEY_DIR_HOST / "server-privatekey.key"

    @property
    def database_url(self):
        return URL.create(
            "postgresql+psycopg", username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD, host=self.POSTGRES_HOST,
            port=self.POSTGRES_PORT, database=self.POSTGRES_DB,
        )


def load_settings(path=ROOT / ".env"):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Backend configuration is missing: {path}")
    return Settings.model_validate(dotenv_values(path, interpolate=False))


settings = load_settings()
# Library caches are internal paths, shared by the models volume in Compose.
os.environ["HF_HOME"] = str(ROOT / "models" / "huggingface")
os.environ["TORCH_HOME"] = str(ROOT / "models" / "torch")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=settings.VOXVAULT_PORT, workers=1)
