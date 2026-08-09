from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

from .pdf_preparation import (
    PDFPreparationError,
    normalize_account_key,
)
from .profile_loader import PROJECT_ROOT


DEFAULT_SETTINGS_PATH = (
    PROJECT_ROOT
    / "config"
    / "preparation.local.json"
)

SETTINGS_KEY = "preparation_accounts"


def _settings_path(
    settings_path: Path | None,
) -> Path:
    return (
        Path(settings_path)
        if settings_path is not None
        else DEFAULT_SETTINGS_PATH
    ).resolve()


def _account_key_text(
    account_key: str | Path,
) -> str:
    return normalize_account_key(account_key).as_posix()


def _sorted_unique_account_keys(
    account_keys: Iterable[str | Path],
) -> tuple[str, ...]:
    normalized: set[str] = set()

    for account_key in account_keys:
        normalized.add(_account_key_text(account_key))

    return tuple(
        sorted(
            normalized,
            key=str.casefold,
        )
    )


def load_preparation_account_keys(
    *,
    settings_path: Path | None = None,
) -> tuple[str, ...]:
    path = _settings_path(settings_path)

    if not path.exists():
        return ()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()

    if not isinstance(data, dict):
        return ()

    values = data.get(SETTINGS_KEY, [])

    if not isinstance(values, list):
        return ()

    normalized: set[str] = set()

    for value in values:
        if not isinstance(value, str):
            continue

        try:
            normalized.add(_account_key_text(value))
        except PDFPreparationError:
            continue

    return tuple(
        sorted(
            normalized,
            key=str.casefold,
        )
    )


def save_preparation_account_keys(
    account_keys: Iterable[str | Path],
    *,
    settings_path: Path | None = None,
) -> tuple[str, ...]:
    path = _settings_path(settings_path)
    normalized = _sorted_unique_account_keys(account_keys)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_name(
        f".{path.name}.tmp"
    )

    payload = {
        SETTINGS_KEY: list(normalized),
    }

    try:
        temporary.write_text(
            json.dumps(
                payload,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(
            temporary,
            path,
        )
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    return normalized


def remember_preparation_account_key(
    account_key: str | Path,
    *,
    settings_path: Path | None = None,
) -> tuple[str, ...]:
    normalized_key = _account_key_text(account_key)
    existing = load_preparation_account_keys(
        settings_path=settings_path,
    )

    return save_preparation_account_keys(
        (*existing, normalized_key),
        settings_path=settings_path,
    )
