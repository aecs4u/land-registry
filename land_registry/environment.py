"""Load shared and environment-specific configuration once per process."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT_ALIASES = {
    "dev": "development",
    "development": "development",
    "test": "testing",
    "testing": "testing",
    "stage": "staging",
    "staging": "staging",
    "prod": "production",
    "production": "production",
}

_DERIVED_SETTINGS = {
    "development": {
        "DEBUG": "true",
        "LOG_LEVEL": "DEBUG",
        "LOGFIRE_ENVIRONMENT": "development",
        "LAND_REGISTRY_RELOAD": "true",
        "CADASTRAL_USE_LOCAL_FILES": "true",
    },
    "testing": {
        "DEBUG": "false",
        "LOG_LEVEL": "DEBUG",
        "LOGFIRE_ENVIRONMENT": "testing",
        "LAND_REGISTRY_RELOAD": "false",
        "CADASTRAL_USE_LOCAL_FILES": "false",
    },
    "staging": {
        "DEBUG": "false",
        "LOG_LEVEL": "INFO",
        "LOGFIRE_ENVIRONMENT": "staging",
        "LAND_REGISTRY_RELOAD": "false",
        "CADASTRAL_USE_LOCAL_FILES": "false",
    },
    "production": {
        "DEBUG": "false",
        "LOG_LEVEL": "INFO",
        "LOGFIRE_ENVIRONMENT": "production",
        "LAND_REGISTRY_RELOAD": "false",
        "CADASTRAL_USE_LOCAL_FILES": "false",
    },
}

_loaded_environment: str | None = None


def normalize_environment(value: str | None) -> str:
    """Return the canonical environment name, defaulting to development."""
    selected = (value or "dev").strip().lower()
    try:
        return ENVIRONMENT_ALIASES[selected]
    except KeyError as exc:
        raise ValueError(
            f"Unknown ENVIRONMENT {value!r}; choose dev, test, staging, or production"
        ) from exc


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return {
        key: value
        for key, value in dotenv_values(path, interpolate=False).items()
        if key and value is not None
    }


def _overlay(target: dict[str, str], values: Mapping[str, str]) -> None:
    target.update(values)


def load_environment() -> str:
    """Load profile files while preserving process environment precedence.

    Staging intentionally reads only ``DATABASE_URL`` from ``.env.production``
    so it can use the production app database without loading production secrets
    for other services.
    """
    global _loaded_environment
    if _loaded_environment is not None:
        return _loaded_environment

    process_values = dict(os.environ)
    base_values = _read_env_file(PROJECT_ROOT / ".env")
    selected = normalize_environment(process_values.get("ENVIRONMENT") or base_values.get("ENVIRONMENT"))

    values: dict[str, str] = {}
    _overlay(values, _read_env_file(PROJECT_ROOT / "environments" / "shared.env"))
    _overlay(values, base_values)
    _overlay(values, _read_env_file(PROJECT_ROOT / "environments" / f"{selected}.env"))
    _overlay(values, _read_env_file(PROJECT_ROOT / f".env.{selected}"))

    if selected == "staging" and "DATABASE_URL" not in process_values:
        # Ignore base/staging URLs so staging cannot silently use a dev DB.
        values.pop("DATABASE_URL", None)
        production_database_url = _read_env_file(PROJECT_ROOT / ".env.production").get("DATABASE_URL")
        if production_database_url:
            values["DATABASE_URL"] = production_database_url

    values["ENVIRONMENT"] = selected

    # DEBUG and log levels are environment-derived. Only an explicit process
    # setting may override them; profile files cannot accidentally diverge.
    for key in _DERIVED_SETTINGS[selected]:
        values.pop(key, None)
    for key, value in _DERIVED_SETTINGS[selected].items():
        if key not in process_values:
            os.environ[key] = value

    for key, value in values.items():
        if key not in process_values:
            os.environ[key] = value

    # Normalize shorthand selectors even when they came from the process.
    os.environ["ENVIRONMENT"] = selected

    # The installed shared auth package still reads the historical browser
    # alias. Keep the canonical setting authoritative and expose both names.
    if "CLERK_PUBLISHABLE_KEY" not in os.environ and "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" in os.environ:
        os.environ["CLERK_PUBLISHABLE_KEY"] = os.environ["NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"]
    if "CLERK_PUBLISHABLE_KEY" in os.environ:
        os.environ["NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"] = os.environ["CLERK_PUBLISHABLE_KEY"]

    _loaded_environment = selected
    return selected


CURRENT_ENVIRONMENT = load_environment()
