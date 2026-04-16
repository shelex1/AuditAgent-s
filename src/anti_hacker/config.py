from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator

from .errors import ConfigError


Role = Literal[
    "security-paranoid",
    "pragmatic-engineer",
    "adversarial-critic",
    "code-quality",
    "refactorer",
]


class MemberConfig(BaseModel):
    name: str
    model: str
    role: Role
    timeout: int = Field(gt=0, le=600)


class CartographerConfig(BaseModel):
    model: str
    timeout: int = Field(default=120, gt=0, le=600)


class LimitsConfig(BaseModel):
    max_files_scan: int = Field(default=50, gt=0, le=500)
    max_additional_file_requests: int = Field(default=3, ge=0, le=20)
    debate_timeout: int = Field(default=180, gt=0, le=1800)
    per_member_timeout_fallback: int = Field(default=90, gt=0, le=600)
    cache_ttl_seconds: int = Field(default=600, ge=0, le=86400)
    max_file_size_bytes: int = Field(default=51200, gt=0, le=5_000_000)


class OpenRouterConfig(BaseModel):
    base_url: str = "https://openrouter.ai/api/v1"


class Config(BaseModel):
    api_key: str
    members: list[MemberConfig]
    cartographer: CartographerConfig
    limits: LimitsConfig
    openrouter: OpenRouterConfig

    @model_validator(mode="after")
    def _validate_members(self) -> "Config":
        if len(self.members) != 5:
            raise ValueError("council must have exactly 5 members")
        names = [m.name for m in self.members]
        if len(set(names)) != len(names):
            raise ValueError("member names must be unique")
        return self


def load_config(toml_path: Path) -> Config:
    load_dotenv()
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ConfigError("OPENROUTER_API_KEY is not set in environment or .env")

    if not toml_path.exists():
        raise ConfigError(f"Council config not found: {toml_path}")

    try:
        with toml_path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {toml_path}: {exc}") from exc

    try:
        return Config(api_key=api_key, **data)
    except Exception as exc:
        # Pydantic ValidationError may wrap ValueError messages; extract and re-raise
        msg = str(exc)
        raise ConfigError(msg) from exc
