from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

from .pdf_preparation import (
    PDFPreparationError,
    normalize_account_key,
)
from .pdf_redaction import normalize_redaction_terms
from .profile_loader import PROJECT_ROOT


DEFAULT_REDACTION_TERMS_PATH = (
    PROJECT_ROOT
    / "config"
    / "redaction_terms.local.json"
)

ACCOUNTS_KEY = "accounts"


def _settings_path(
    settings_path: Path | None,
) -> Path:
    return (
        Path(settings_path)
        if settings_path is not None
        else DEFAULT_REDACTION_TERMS_PATH
    ).resolve()


def _account_key_text(
    account_key: str | Path,
) -> str:
    return normalize_account_key(account_key).as_posix()


def _normalized_terms(
    terms: Iterable[str],
) -> tuple[str, ...]:
    return normalize_redaction_terms(tuple(terms))


def load_redaction_term_accounts(
    *,
    settings_path: Path | None = None,
) -> dict[str, tuple[str, ...]]:
    path = _settings_path(settings_path)

    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(data, dict):
        return {}

    accounts = data.get(ACCOUNTS_KEY, {})

    if not isinstance(accounts, dict):
        return {}

    normalized: dict[str, tuple[str, ...]] = {}

    for account_key, terms in accounts.items():
        if not isinstance(account_key, str):
            continue

        if not isinstance(terms, list):
            continue

        if any(not isinstance(term, str) for term in terms):
            continue

        try:
            normalized_key = _account_key_text(account_key)
            normalized_terms = _normalized_terms(terms)
        except Exception:
            continue

        normalized[normalized_key] = normalized_terms

    return {
        account_key: normalized[account_key]
        for account_key in sorted(
            normalized,
            key=str.casefold,
        )
    }


def load_redaction_terms_for_account(
    account_key: str | Path,
    *,
    settings_path: Path | None = None,
) -> tuple[str, ...]:
    normalized_key = _account_key_text(account_key)
    accounts = load_redaction_term_accounts(
        settings_path=settings_path,
    )

    return accounts.get(normalized_key, ())


def save_redaction_terms_for_account(
    account_key: str | Path,
    terms: Iterable[str],
    *,
    settings_path: Path | None = None,
) -> tuple[str, ...]:
    path = _settings_path(settings_path)
    normalized_key = _account_key_text(account_key)
    normalized_terms = _normalized_terms(terms)
    accounts = load_redaction_term_accounts(
        settings_path=settings_path,
    )

    accounts[normalized_key] = normalized_terms

    payload = {
        ACCOUNTS_KEY: {
            key: list(accounts[key])
            for key in sorted(
                accounts,
                key=str.casefold,
            )
        }
    }

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_name(
        f".{path.name}.tmp"
    )

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

    return normalized_terms
