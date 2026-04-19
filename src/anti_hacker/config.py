from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

from .errors import ConfigError


Role = Literal[
    "security-paranoid",
    "pragmatic-engineer",
    "adversarial-critic",
    "code-quality",
    "refactorer",
]


class ProviderConfig(BaseModel):
    name: str
    base_url: str
    api_key_env: str | None = None
    api_key: str = ""  # populated by loader after env resolution
    empty_means_quota: bool = False


class FallbackEntry(BaseModel):
    provider: str
    model: str


class MemberConfig(BaseModel):
    name: str
    model: str
    role: Role
    timeout: int = Field(gt=0, le=600)
    provider: str | None = None               # resolved to providers[0].name if None
    fallbacks: list[FallbackEntry] = []
    # Legacy (deprecated, still accepted for back-compat):
    fallback_provider: str | None = None
    fallback_model: str | None = None


class CartographerConfig(BaseModel):
    model: str
    timeout: int = Field(default=120, gt=0, le=600)
    provider: str | None = None               # defaults to providers[0].name


class LimitsConfig(BaseModel):
    max_files_scan: int = Field(default=50, gt=0, le=500)
    max_additional_file_requests: int = Field(default=3, ge=0, le=20)
    debate_timeout: int = Field(default=180, gt=0, le=1800)
    per_member_timeout_fallback: int = Field(default=90, gt=0, le=600)
    cache_ttl_seconds: int = Field(default=600, ge=0, le=86400)
    max_file_size_bytes: int = Field(default=51200, gt=0, le=5_000_000)


class Config(BaseModel):
    providers: list[ProviderConfig]
    members: list[MemberConfig]
    cartographer: CartographerConfig
    limits: LimitsConfig

    @model_validator(mode="after")
    def _validate(self) -> "Config":
        if not self.providers:
            raise ValueError("at least one provider is required")
        names = [p.name for p in self.providers]
        if len(set(names)) != len(names):
            raise ValueError("provider names must be unique")
        known = set(names)

        if len(self.members) != 5:
            raise ValueError("council must have exactly 5 members")
        mnames = [m.name for m in self.members]
        if len(set(mnames)) != len(mnames):
            raise ValueError("member names must be unique")

        default_provider = self.providers[0].name
        for m in self.members:
            if m.provider is None:
                m.provider = default_provider
            if m.provider not in known:
                raise ValueError(f"unknown provider '{m.provider}' on member '{m.name}'")

            legacy_set = m.fallback_provider is not None or m.fallback_model is not None
            if legacy_set and m.fallbacks:
                raise ValueError(
                    f"member '{m.name}' cannot mix legacy fallback_provider/fallback_model with fallbacks list"
                )
            if legacy_set:
                if m.fallback_provider is None or not m.fallback_model:
                    raise ValueError(
                        f"member '{m.name}' legacy fallback requires both fallback_provider and fallback_model"
                    )
                m.fallbacks = [FallbackEntry(provider=m.fallback_provider, model=m.fallback_model)]
                # Clear legacy fields so nothing downstream reads them.
                m.fallback_provider = None
                m.fallback_model = None

            for idx, entry in enumerate(m.fallbacks):
                if entry.provider not in known:
                    raise ValueError(
                        f"unknown provider '{entry.provider}' in fallbacks[{idx}] on member '{m.name}'"
                    )
                if entry.provider == m.provider:
                    raise ValueError(
                        f"member '{m.name}' fallbacks[{idx}] uses the same provider as primary"
                    )
                if not entry.model:
                    raise ValueError(
                        f"member '{m.name}' fallbacks[{idx}] has empty model"
                    )

        if self.cartographer.provider is None:
            self.cartographer.provider = default_provider
        elif self.cartographer.provider not in known:
            raise ValueError(f"unknown provider '{self.cartographer.provider}' in cartographer")
        return self


def _back_compat_providers(data: dict) -> list[dict]:
    """If [[providers]] is missing but legacy [openrouter] is present, synthesize one."""
    if "providers" in data and data["providers"]:
        return data["providers"]
    legacy = data.get("openrouter") or {}
    base_url = legacy.get("base_url", "https://openrouter.ai/api/v1")
    return [{"name": "openrouter", "base_url": base_url, "api_key_env": "OPENROUTER_API_KEY"}]


def load_config(toml_path: Path) -> Config:
    load_dotenv()

    if not toml_path.exists():
        raise ConfigError(f"Council config not found: {toml_path}")

    try:
        with toml_path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {toml_path}: {exc}") from exc

    # Back-compat: synthesize providers if needed
    providers_data = _back_compat_providers(data)
    # Resolve api keys from environment
    resolved: list[dict] = []
    for p in providers_data:
        env_name = p.get("api_key_env")
        if env_name is None:
            resolved.append({**p, "api_key": ""})
            continue
        key = os.getenv(env_name)
        if not key:
            raise ConfigError(f"{env_name} is not set in environment or .env")
        resolved.append({**p, "api_key": key})
    data = {**data, "providers": resolved}
    data.pop("openrouter", None)  # drop legacy block after synthesis

    try:
        return Config(**data)
    except Exception as exc:
        msg = str(exc)
        raise ConfigError(msg) from exc


def provider_by_name(cfg: Config, name: str) -> ProviderConfig:
    for p in cfg.providers:
        if p.name == name:
            return p
    raise ConfigError(f"provider not found: {name}")
