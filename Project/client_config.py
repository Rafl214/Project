from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from Prompts import SYSTEM_PROMPT


PROJECT_DIR = Path(__file__).resolve().parent
ROOT_DIR = PROJECT_DIR.parent
DEFAULT_CLIENT_CONFIGS_DIR = PROJECT_DIR / "client_configs"


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"Переменная окружения {name} должна быть целым числом.") from exc


def _coerce_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    raise ValueError(f"Поле {field_name} должно быть логическим значением.")


def _resolve_path(raw_path: str, *, base_dir: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _load_text_file(path: Path) -> str:
    if not path.exists():
        raise ValueError(f"Файл {path} не найден.")
    return path.read_text(encoding="utf-8").strip()


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def normalize_client_id(raw_client_id: str | None) -> str | None:
    if not raw_client_id:
        return None

    cleaned = raw_client_id.strip().lower()
    if not cleaned:
        return None

    cleaned = re.sub(r"[^a-z0-9._-]+", "-", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-.")
    return cleaned or None


def get_client_configs_dir() -> Path:
    raw_dir = os.getenv("CLIENT_CONFIGS_DIR", "").strip()
    if not raw_dir:
        return DEFAULT_CLIENT_CONFIGS_DIR
    return _resolve_path(raw_dir, base_dir=ROOT_DIR)


def list_registered_clients() -> list[str]:
    configs_dir = get_client_configs_dir()
    if not configs_dir.exists():
        return []
    return sorted(path.stem for path in configs_dir.glob("*.json") if path.is_file())


@dataclass(frozen=True)
class ClientSettings:
    requested_client_id: str | None
    effective_client_id: str
    display_name: str
    config_source: str
    model_name: str
    polza_base_url: str
    polza_api_key: str
    system_prompt: str
    request_intro_text: str
    reasoning_enabled: bool
    reasoning_effort: str
    max_content_length_mb: int

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "requested_client_id": self.requested_client_id,
            "effective_client_id": self.effective_client_id,
            "display_name": self.display_name,
            "config_source": self.config_source,
            "model_name": self.model_name,
            "polza_base_url": self.polza_base_url,
            "reasoning_enabled": self.reasoning_enabled,
            "reasoning_effort": self.reasoning_effort,
            "max_content_length_mb": self.max_content_length_mb,
            "has_api_key": bool(self.polza_api_key),
        }


def _build_base_config() -> dict[str, Any]:
    return {
        "display_name": "Базовый клиент",
        "model_name": os.getenv("MODEL_NAME", "openai/gpt-5.5"),
        "polza_base_url": os.getenv("POLZA_BASE_URL", "https://routerai.ru/api/v1"),
        "polza_api_key": os.getenv("POLZA_API_KEY", "").strip(),
        "system_prompt": SYSTEM_PROMPT.strip(),
        "request_intro_text": os.getenv("DEFAULT_REQUEST_INTRO_TEXT", "Проанализируй эти документы:").strip(),
        "reasoning_enabled": _env_bool("DEFAULT_REASONING_ENABLED", True),
        "reasoning_effort": os.getenv("DEFAULT_REASONING_EFFORT", "low").strip() or "low",
        "max_content_length_mb": _env_int("MAX_CONTENT_LENGTH_MB", 30),
    }


def _load_client_override(client_id: str) -> tuple[dict[str, Any] | None, Path | None]:
    config_path = get_client_configs_dir() / f"{client_id}.json"
    if not config_path.exists():
        return None, None

    try:
        raw_data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Конфиг клиента {client_id} содержит невалидный JSON.") from exc

    if not isinstance(raw_data, dict):
        raise ValueError(f"Конфиг клиента {client_id} должен быть JSON-объектом.")

    return raw_data, config_path


def resolve_client_settings(raw_client_id: str | None) -> ClientSettings:
    normalized_client_id = normalize_client_id(raw_client_id)
    base_config = _build_base_config()
    override_data: dict[str, Any] | None = None
    config_path: Path | None = None

    if normalized_client_id:
        override_data, config_path = _load_client_override(normalized_client_id)

    config = dict(base_config)
    display_name = base_config["display_name"]
    config_source = "default"
    effective_client_id = "default"

    if override_data is not None and config_path is not None:
        allowed_keys = {
            "display_name",
            "model_name",
            "polza_base_url",
            "polza_api_key",
            "system_prompt",
            "system_prompt_file",
            "request_intro_text",
            "reasoning_enabled",
            "reasoning_effort",
            "max_content_length_mb",
        }
        unknown_keys = sorted(set(override_data) - allowed_keys)
        if unknown_keys:
            unknown = ", ".join(unknown_keys)
            raise ValueError(f"В конфиге клиента {normalized_client_id} есть неизвестные поля: {unknown}.")

        for key in ("display_name", "model_name", "polza_base_url", "polza_api_key", "system_prompt", "request_intro_text", "reasoning_effort"):
            value = override_data.get(key)
            if value is None:
                continue
            if not isinstance(value, str):
                raise ValueError(f"Поле {key} в конфиге клиента {normalized_client_id} должно быть строкой.")
            config[key] = value.strip()

        if "reasoning_enabled" in override_data:
            config["reasoning_enabled"] = _coerce_bool(override_data["reasoning_enabled"], "reasoning_enabled")

        if "max_content_length_mb" in override_data:
            try:
                config["max_content_length_mb"] = int(override_data["max_content_length_mb"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Поле max_content_length_mb в конфиге клиента {normalized_client_id} должно быть целым числом."
                ) from exc

        prompt_file = override_data.get("system_prompt_file")
        if prompt_file is not None:
            if not isinstance(prompt_file, str) or not prompt_file.strip():
                raise ValueError(
                    f"Поле system_prompt_file в конфиге клиента {normalized_client_id} должно быть непустой строкой."
                )
            config["system_prompt"] = _load_text_file(_resolve_path(prompt_file.strip(), base_dir=config_path.parent))

        display_name = config.get("display_name") or normalized_client_id
        config_source = _display_path(config_path)
        effective_client_id = normalized_client_id

    if not config["polza_base_url"]:
        raise ValueError("POLZA_BASE_URL не должен быть пустым.")
    if not config["model_name"]:
        raise ValueError("MODEL_NAME не должен быть пустым.")
    if not config["system_prompt"]:
        raise ValueError("Системный промпт не должен быть пустым.")
    if not config["request_intro_text"]:
        raise ValueError("Текст пользовательского промпта не должен быть пустым.")
    if config["max_content_length_mb"] <= 0:
        raise ValueError("MAX_CONTENT_LENGTH_MB должен быть больше нуля.")

    return ClientSettings(
        requested_client_id=normalized_client_id,
        effective_client_id=effective_client_id,
        display_name=display_name,
        config_source=config_source,
        model_name=config["model_name"],
        polza_base_url=config["polza_base_url"],
        polza_api_key=config["polza_api_key"],
        system_prompt=config["system_prompt"],
        request_intro_text=config["request_intro_text"],
        reasoning_enabled=config["reasoning_enabled"],
        reasoning_effort=config["reasoning_effort"],
        max_content_length_mb=config["max_content_length_mb"],
    )
